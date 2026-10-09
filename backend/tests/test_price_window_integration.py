"""Integrationstest: echter Control-Loop, echte Simulationstreiber.

Die Unit-Tests prüfen die Bausteine einzeln. Hier läuft ein vollständiger
`_step()` mit den Treibern aus `app.drivers.simulation`, damit auch die
Verdrahtung stimmt: Reihenfolge (erst sperren, dann laden), Weitergabe von
`battery_drain_unblockable` an die Regler und die Rückkehr in den
Normalbetrieb, sobald der Preis wieder steigt.

Nur der Tarif ist gefälscht — Tibber/aWATTar sind externe Dienste, und ein
Test, der vom Börsenpreis des Tages abhängt, prüft nicht die Software.
"""
import pytest
from sqlalchemy import delete

from app.core.loop import ControlLoop, ManagedDevice
from app.db import Base, async_session, engine
from app.drivers import registry
from app.drivers.base import BatteryMode, DeviceCategory, DriverMeta, InverterData, InverterDriver
from app.models import Device, Setting


@registry.register
class FakeHybridInverter(InverterDriver):
    """Steht für Fronius/Huawei/SMA/SunSpec: liefert Batteriewerte eingebettet
    im Wechselrichter (`battery_monitor`), ohne eigenen `BatteryDriver` –
    genau der Fall, für den es kein `set_mode`/`set_reserve_soc` gibt."""

    meta = DriverMeta(
        id="test_hybrid_inverter", name="Test: Hybrid-Wechselrichter",
        category=DeviceCategory.INVERTER, capabilities={"hybrid", "battery_monitor"},
    )

    async def read_data(self) -> InverterData:
        return InverterData(pv_power=3000.0, battery_soc=40.0, battery_power=-800.0)


class FakeTariff:
    """Preis frei setzbar; sonst wie services.tariff.TariffService."""

    def __init__(self, price_ct: float, cheap_limit_ct: float = 15.0):
        self.price_ct = price_ct
        self.cheap_limit_ct = cheap_limit_ct

    def current_price_ct(self):
        return self.price_ct

    def is_cheap_hour(self) -> bool:
        return self.price_ct is not None and self.price_ct <= self.cheap_limit_ct


@pytest.fixture(autouse=True)
def night(monkeypatch):
    """Keine Sonne, feste Hauslast – sonst hinge das Ergebnis von der Uhrzeit ab."""
    from app.drivers.simulation import SimulationWorld
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: 0.0)
    monkeypatch.setattr(SimulationWorld, "house_load", lambda self: 400.0)


async def build_loop(
    price_ct: float, *, battery_controllable: bool, wallbox_mode: str = "price",
    inline_battery: bool = False,
    battery_release_soc: float = 0.0,
    battery_soc: float | None = None,
    # Standardmäßig AUS, wie im echten Betrieb (siehe startup.py) – wer das
    # aktive Netzladen der Batterie testen will, muss es hier wie ein echter
    # Nutzer explizit einschalten.
    battery_grid_charge_enabled: bool = False,
):
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await session.execute(delete(Device))
        rows = [
            Device(name="PV", category="inverter",
                   driver_id="test_hybrid_inverter" if inline_battery else "sim_inverter",
                   config={"kwp": 9.8}, settings={}),
            Device(name="Zähler", category="meter", driver_id="sim_meter", config={}, settings={}),
            Device(name="Wallbox", category="wallbox", driver_id="sim_wallbox",
                   config={"max_current": 16, "car_capacity_kwh": 62},
                   settings={"mode": wallbox_mode, "min_current": 6, "max_current": 16,
                             "phases_mode": "fixed3", "price_limit_ct": 15}),
        ]
        # Fronius/Huawei/SMA/SunSpec: kein eigenes Batteriegerät, die Werte
        # kommen über den Wechselrichter (siehe FakeHybridInverter oben).
        if not inline_battery:
            rows.append(Device(
                name="Speicher", category="battery", driver_id="sim_battery",
                config={"capacity_kwh": 9.6, "max_power": 5000,
                       "allow_active_control": battery_controllable},
                settings={"reserve_soc": 20},
            ))
        if battery_release_soc or battery_grid_charge_enabled:
            rows.append(Setting(key="regulation", value={
                "battery_release_soc": battery_release_soc,
                "battery_grid_charge_enabled": battery_grid_charge_enabled,
            }))
        session.add_all(rows)
        await session.commit()

    loop = ControlLoop()
    loop.tariff = FakeTariff(price_ct)
    await loop._reload()
    for dev in loop.devices.values():
        await dev.driver.connect()
    if battery_soc is not None:
        from app.drivers.simulation import SimulationWorld
        SimulationWorld.instance().bat_soc = battery_soc
    return loop


def battery_of(loop: ControlLoop) -> ManagedDevice:
    return next(d for d in loop.devices.values() if d.category == "battery")


def wallbox_of(loop: ControlLoop) -> ManagedDevice:
    return next(d for d in loop.devices.values() if d.category == "wallbox")


async def test_cheap_hour_protects_battery():
    """Auto lädt günstig aus dem Netz → Speicher darf nicht ins Auto entladen.
    Er ruht oder liefert nur die Hauslast – nie AUTO (dann deckte er das Auto)."""
    loop = await build_loop(price_ct=6.0, battery_controllable=True)
    bat = battery_of(loop)

    await loop._step()

    assert loop.grid_price_window is True
    assert loop.battery_locked is True
    data = await bat.driver.read_data()
    assert data.mode in (BatteryMode.HOLD, BatteryMode.FORCE_DISCHARGE)
    assert loop.battery_plan.source == "grid_loads"


async def test_cheap_window_never_grid_charges_battery_without_switch():
    """Diagnose 08.10.: Speicher lud 1,5 h mit 5 kW aus dem Netz (33 ct),
    obwohl 'Netzladen" aus war – 'vollladen" im Preisfenster. Das gibt es
    nicht mehr: Ohne Schalter nie FORCE_CHARGE."""
    loop = await build_loop(price_ct=6.0, battery_controllable=True, battery_soc=30.0)
    bat = battery_of(loop)
    for _ in range(3):
        await loop._step()
    assert (await bat.driver.read_data()).mode is not BatteryMode.FORCE_CHARGE
    assert loop.battery_grid_charging is False


async def test_expensive_hour_leaves_battery_on_auto():
    loop = await build_loop(price_ct=32.0, battery_controllable=True)
    bat = battery_of(loop)

    await loop._step()

    assert loop.grid_price_window is False
    data = await bat.driver.read_data()
    assert data.mode is BatteryMode.AUTO


async def test_window_closes_and_battery_returns_to_auto():
    """Der Rückweg ist die Hälfte des Mechanismus: Ein Speicher, der nach der
    günstigen Stunde gesperrt bleibt, wäre schlimmer als gar keine Sperre."""
    loop = await build_loop(price_ct=6.0, battery_controllable=True)
    bat = battery_of(loop)
    await loop._step()
    assert (await bat.driver.read_data()).mode in (BatteryMode.HOLD, BatteryMode.FORCE_DISCHARGE)

    loop.tariff.price_ct = 30.0
    await loop._step()

    assert loop.grid_price_window is False
    assert (await bat.driver.read_data()).mode is BatteryMode.AUTO


async def test_pv_only_wallbox_never_opens_window():
    """Regressionsschutz für 'an der Überschussregelung nichts kaputtmachen":
    Ein billiger Nachttarif darf den Speicher nicht sperren, wenn kein Gerät
    preisoptimiert lädt."""
    loop = await build_loop(price_ct=3.0, battery_controllable=True, wallbox_mode="pv_only")
    bat = battery_of(loop)

    await loop._step()

    assert loop.grid_price_window is False
    assert (await bat.driver.read_data()).mode is BatteryMode.AUTO


async def test_uncontrollable_battery_reports_unlocked_window():
    """Reines Monitoring: Das Fenster ist offen, der Speicher aber nicht
    gesperrt – der Wallbox-Regler bekommt das über den Kontext mit."""
    loop = await build_loop(price_ct=6.0, battery_controllable=False)

    await loop._step()

    assert loop.grid_price_window is True
    assert loop.battery_locked is False


async def test_snapshot_exposes_window_state():
    loop = await build_loop(price_ct=6.0, battery_controllable=True)

    await loop._step()

    assert loop.snapshot["grid_price_window"] is True
    assert loop.snapshot["battery_locked"] is True


async def test_stop_releases_locked_battery():
    """MinePower mitten in der günstigen Stunde beenden darf keinen
    dauerhaft gesperrten Wechselrichter hinterlassen."""
    loop = await build_loop(price_ct=6.0, battery_controllable=True)
    bat = battery_of(loop)
    await loop._step()
    assert (await bat.driver.read_data()).mode in (BatteryMode.HOLD, BatteryMode.FORCE_DISCHARGE)

    await loop.stop()

    assert (await bat.driver.read_data()).mode is BatteryMode.AUTO


async def test_hybrid_inline_battery_blocks_grid_charge_without_control():
    """Der Fall, der der eigentliche Anlass für diese Ergänzung ist:
    Fronius/Huawei/SMA/SunSpec melden ihre Batterie nur über den
    Wechselrichter, es gibt kein eigenes Batteriegerät und damit keine
    Möglichkeit, es zu sperren. Der Speicher entlädt gerade (−800 W aus dem
    Fake-Treiber) – das Netzladen muss aussetzen, nicht heimlich aus dem
    Speicher laufen.

    Geprüft wird die vom Regler tatsächlich getroffene und an den Treiber
    gesendete Entscheidung (`wb.decision`), nicht der simulierte Ladezustand:
    Der Simulationstreiber bremst seine Physik auf mindestens 0,25 s Realzeit
    zwischen zwei Takten (siehe `SimulationWorld.tick`) – ein `_step()` in
    einem Test läuft schneller, `wb_power` bliebe deshalb auch bei einer
    korrekt gesendeten Freigabe noch bei 0 W. Das wäre ein Artefakt der
    Simulation, keine Aussage über die Regelungslogik."""
    loop = await build_loop(price_ct=6.0, battery_controllable=False, inline_battery=True)

    await loop._step()

    assert loop.grid_price_window is True
    assert loop.battery_locked is False
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is False
    assert "lässt sich nicht sperren" in wb.decision["reason"]


async def test_hybrid_inline_battery_blocks_grid_charge_even_when_idle():
    """Steht die Batterie gerade still, entlädt sie trotzdem, sobald das Auto
    anspringt – der Wechselrichter sieht nur eine Last. Bis 2.15 wurde
    deshalb erst gestartet und dann (nach dem ersten Entladen) gestoppt:
    Flattern und Energie aus dem Speicher. Jetzt vorbeugend: kein Netzladen,
    solange ein nicht sperrbarer Speicher Ladung hat."""
    loop = await build_loop(price_ct=6.0, battery_controllable=False, inline_battery=True)
    inverter = next(d for d in loop.devices.values() if d.category == "inverter")
    inverter.driver.read_data = lambda: _idle_inverter_data()

    await loop._step()

    assert loop.grid_price_window is True
    assert loop.battery_locked is False
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is False
    assert "nicht sperren" in wb.decision["reason"]


async def test_hybrid_inline_battery_allows_grid_charge_when_empty():
    loop = await build_loop(price_ct=6.0, battery_controllable=False, inline_battery=True)
    inverter = next(d for d in loop.devices.values() if d.category == "inverter")
    inverter.driver.read_data = lambda: _empty_inverter_data()

    await loop._step()

    wb = wallbox_of(loop)
    assert wb.decision["enable"] is True
    assert wb.decision["current_a"] == 16.0
    assert "6.0 ct" in wb.decision["reason"]


async def _empty_inverter_data():
    from app.drivers.base import InverterData
    return InverterData(pv_power=0.0, battery_soc=5.0, battery_power=0.0)


async def _idle_inverter_data():
    from app.drivers.base import InverterData
    return InverterData(pv_power=3000.0, battery_soc=40.0, battery_power=0.0)


# ------------------------------------------------- Batterie laedt selbst aus dem Netz
#
# Der eigentliche Punkt der Ergaenzung: Bei guenstigem Preis laedt die
# Batterie AKTIV aus dem Netz, bis sie ihre eigene Grenze erreicht -- auch
# wenn kein anderes Geraet ueberhaupt im Preismodus steht. Erst danach wird
# der guenstige Strom an Auto/Warmwasser weitergereicht (siehe
# battery_blocks_grid_charge in core/regulation.py, das hier ueber die
# Blockade von Auto in pv_only-Rueckfall mitgeprueft wird).


async def test_battery_charges_from_grid_below_target_without_any_device_in_price_mode():
    """Netzladen an + günstig + unter dem Ziel → der Speicher lädt aktiv, auch
    ohne Gerät im Preismodus (Prognose fehlt hier → lädt)."""
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_only",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    bat = battery_of(loop)

    await loop._step()

    assert loop.battery_locked is True
    assert loop.battery_grid_charging is True
    data = await bat.driver.read_data()
    assert data.mode is BatteryMode.FORCE_CHARGE


async def test_battery_and_car_share_cheap_grid_power():
    """Seit 2.16 wartet das Auto nicht mehr, bis der Speicher voll ist: Beide
    teilen sich das Netzbudget, der Speicher (Vorrang) zuerst. 35 A × 3 ×
    230 V ≈ 24 kW reichen für 5 kW Speicher + 11 kW Auto."""
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )

    await loop._step()

    assert loop.battery_grid_charging is True
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is True
    assert wb.decision["current_a"] == 16.0


async def test_car_charges_and_battery_is_protected_above_target():
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=85.0, battery_grid_charge_enabled=True,
    )
    bat = battery_of(loop)

    await loop._step()

    assert loop.battery_grid_charging is False  # Ziel erreicht
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is True
    data = await bat.driver.read_data()
    assert data.mode in (BatteryMode.HOLD, BatteryMode.FORCE_DISCHARGE)


async def test_uncontrollable_battery_blocks_grid_charging_of_car():
    """Ohne Schreibzugriff kein Schutz: Das Auto lädt dann nicht aus dem Netz
    (der Speicher würde es füllen) – ehrlich statt vorgetäuscht."""
    loop = await build_loop(
        price_ct=6.0, battery_controllable=False, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    for _ in range(3):
        await loop._step()

    assert loop.battery_grid_charging is False
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is False
    assert "nicht sperren" in wb.decision["reason"]


async def test_expensive_price_does_not_trigger_battery_grid_charge():
    loop = await build_loop(
        price_ct=32.0, battery_controllable=True, wallbox_mode="pv_only",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )

    await loop._step()

    assert loop.grid_price_window is False
    assert loop.battery_grid_charging is False


async def test_battery_stays_on_auto_without_switch_and_without_other_demand():
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_only",
        battery_release_soc=80.0, battery_soc=40.0,
        battery_grid_charge_enabled=False,
    )
    bat = battery_of(loop)

    await loop._step()

    assert loop.grid_price_window is False
    assert loop.battery_grid_charging is False
    data = await bat.driver.read_data()
    assert data.mode is BatteryMode.AUTO
