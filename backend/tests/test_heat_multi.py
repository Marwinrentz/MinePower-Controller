"""3.4: mehrere Wärmelasten gleichzeitig (Wärmepumpe + Heizstab) mit
Priorität, über den echten Regelkreis mit Simulationsgeräten."""
import pytest
from sqlalchemy import delete

from app.core.loop import ControlLoop
from app.db import Base, async_session, engine
from app.drivers import registry
from app.drivers.simulation import SimulationWorld
from app.models import Device

from .test_price_window_integration import FakeTariff


@pytest.fixture(autouse=True)
def sunny(monkeypatch):
    monkeypatch.setattr(SimulationWorld, "house_load", lambda self: 400.0)


async def site(pv_w: float, monkeypatch, hp_settings=None):
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: pv_w)
    registry.discover()
    SimulationWorld._instance = None
    w = SimulationWorld.instance()
    w.wh_temp = 40.0
    w.hp_temp = 45.0
    w.bat_soc = 100.0
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await session.execute(delete(Device))
        session.add_all([
            Device(name="PV", category="inverter", driver_id="sim_inverter", config={"kwp": 9.8}, settings={}),
            Device(name="Zähler", category="meter", driver_id="sim_meter", config={}, settings={}),
            Device(name="Wärmepumpe", category="water_heater", driver_id="sim_heat_pump",
                   config={"rated_power": 1800},
                   settings={"mode": "pv_only", "start_delay_s": 0, "stop_delay_s": 0, **(hp_settings or {})}),
            Device(name="Heizstab", category="water_heater", driver_id="sim_water_heater",
                   config={"rated_power": 3000},
                   settings={"mode": "pv_only", "max_power_w": 3000, "target_temp_c": 60,
                             "start_delay_s": 0, "stop_delay_s": 0, "start_threshold_w": 100}),
        ])
        await session.commit()
    loop = ControlLoop()
    loop.tariff = FakeTariff(30.0)
    await loop._reload()
    for d in loop.devices.values():
        await d.driver.connect()
    return loop


def by_name(loop, name):
    return next(d for d in loop.devices.values() if d.name == name)


async def test_heat_pump_first_then_heater_gets_the_rest(monkeypatch):
    loop = await site(5000.0, monkeypatch)
    hp, rod = by_name(loop, "Wärmepumpe"), by_name(loop, "Heizstab")
    loop.priority = [hp.id, rod.id]
    for _ in range(3):
        await loop._step()
    assert hp.decision["power_w"] == 1800
    assert 0 < rod.decision["power_w"] <= 5000 - 400 - 1800 + 50
    # Beide gleichzeitig, Wärmepumpe mit Art und Verdichterschutz im Snapshot
    snap = {d["name"]: d for d in loop.snapshot["devices"]}
    assert snap["Wärmepumpe"]["kind"] == "heat_pump" and snap["Wärmepumpe"]["self_regulating"] is True
    assert snap["Wärmepumpe"]["min_on_s"] == 600
    assert snap["Heizstab"]["kind"] == "water"


async def test_priority_reversed_heater_takes_all(monkeypatch):
    loop = await site(3500.0, monkeypatch)
    hp, rod = by_name(loop, "Wärmepumpe"), by_name(loop, "Heizstab")
    loop.priority = [rod.id, hp.id]
    for _ in range(3):
        await loop._step()
    assert rod.decision["power_w"] >= 2900
    assert hp.decision["power_w"] == 0


async def test_heat_pump_own_operation_is_not_disobedience(monkeypatch):
    """Nachts heizt die Wärmepumpe im eigenen Normalbetrieb – das ist keine
    Regelverletzung und keine Warnung wert."""
    loop = await site(0.0, monkeypatch)
    SimulationWorld.instance().hp_temp = 30.0  # kalt: läuft von selbst
    hp = by_name(loop, "Wärmepumpe")
    for _ in range(8):
        await loop._step()
    assert hp.data.power > 0
    assert hp.decision["power_w"] == 0
    assert not hp.uncontrolled
