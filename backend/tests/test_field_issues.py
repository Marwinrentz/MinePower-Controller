"""Fälle aus dem Diagnosebericht vom 03.10.

* ELWA heizt im eigenen Programm (Warmwasser-Sicherstellung) → kein
  Warnungs-Dauerfeuer, kein Scheinüberschuss.
* Tesla startet beim Anstecken selbst → Stopp sofort, Hinweis statt Warnung.
* Langsames Fahrzeug (BLE) hält den Regeltakt nicht mehr auf.
* Angesteckt eingeschlafenes Auto wird bei Überschuss geweckt.
* Verschenkte Energie wird je Episode mit Grund protokolliert.
* Warmwasser als Wärmepuffer: mit Überschuss über die Zieltemperatur.
* Erkannte Phasenzahl überlebt einen Neustart.
"""
import asyncio
import time

import pytest
from sqlalchemy import delete, select

import app.core.loop as loop_module
from app.core.loop import ControlLoop, ManagedDevice
from app.core.regulation import (
    ChargeContext,
    WallboxController,
    WallboxSettings,
    WaterHeaterController,
    WaterHeaterSettings,
)
from app.db import Base, async_session, engine
from app.drivers import registry
from app.drivers.base import (
    DeviceCategory,
    DriverMeta,
    WallboxData,
    WallboxDriver,
    WallboxState,
    WaterHeaterData,
    WaterHeaterDriver,
)
from app.drivers.simulation import SimulationWorld
from app.models import Device, Event


# ------------------------------------------------------------ Testtreiber


@registry.register
class OwnProgramHeater(WaterHeaterDriver):
    """Heizstab, dessen Eigenprogramm von außen schaltbar ist (wie my-PV
    Status 4 'Boost")."""

    meta = DriverMeta(id="test_own_program_heater", name="Test: Heizstab",
                      category=DeviceCategory.WATER_HEATER, capabilities={"modulation"})
    state: dict = {}

    async def read_data(self) -> WaterHeaterData:
        st = OwnProgramHeater.state
        return WaterHeaterData(power=st["power"], temperature_c=st["temp"],
                               device_mode=st["mode"], is_on=st["power"] > 0)

    async def set_power(self, watts: float) -> None:
        OwnProgramHeater.state["set"].append(watts)


@registry.register
class SlowVehicle(WallboxDriver):
    """Fahrzeug über einen trägen Funkweg: Lesen dauert `delay` Sekunden."""

    meta = DriverMeta(id="test_slow_vehicle", name="Test: langsames Fahrzeug",
                      category=DeviceCategory.WALLBOX, capabilities={"wake"})
    read_timeout_s = 30.0
    delay = 0.0
    reachable = True
    plugged = True
    wakes: list = []
    commands: list = []

    @property
    def likely_plugged_in(self) -> bool:
        return SlowVehicle.plugged

    async def read_data(self) -> WallboxData:
        await asyncio.sleep(SlowVehicle.delay)
        return WallboxData(state=WallboxState.CONNECTED if SlowVehicle.reachable else WallboxState.IDLE,
                           power=0.0, vehicle_reachable=SlowVehicle.reachable)

    async def set_current(self, amps: float) -> None:
        SlowVehicle.commands.append(("current", amps))

    async def start_charging(self) -> None:
        SlowVehicle.commands.append(("start",))

    async def stop_charging(self) -> None:
        SlowVehicle.commands.append(("stop",))

    async def wake_up(self) -> None:
        SlowVehicle.wakes.append(time.monotonic())


class FakeTariff:
    cheap_limit_ct = 24.0

    def current_price_ct(self):
        return 30.0

    def is_cheap_hour(self) -> bool:
        return False


async def make_loop(rows: list[Device]) -> ControlLoop:
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await session.execute(delete(Device))
        session.add_all([
            Device(name="PV", category="inverter", driver_id="sim_inverter", config={"kwp": 9.8}, settings={}),
            Device(name="Zähler", category="meter", driver_id="sim_meter", config={}, settings={}),
            *rows,
        ])
        await session.commit()
    loop = ControlLoop()
    loop.tariff = FakeTariff()
    await loop._reload()
    for dev in loop.devices.values():
        await dev.driver.connect()
    return loop


async def events(contains: str = "") -> list[Event]:
    async with async_session() as session:
        rows = (await session.scalars(select(Event).order_by(Event.id))).all()
    return [e for e in rows if contains in e.message]


def of(loop: ControlLoop, category: str) -> ManagedDevice:
    return next(d for d in loop.devices.values() if d.category == category)


@pytest.fixture
def no_sun(monkeypatch):
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: 0.0)
    monkeypatch.setattr(SimulationWorld, "house_load", lambda self: 400.0)


# ------------------------------------------------------------ ELWA-Eigenprogramm


async def test_device_program_is_reported_once_not_as_disobedience(no_sun):
    OwnProgramHeater.state = {"power": 0.0, "temp": 45.0, "mode": None, "set": []}
    loop = await make_loop([
        Device(name="AC ELWA 2", category="water_heater", driver_id="test_own_program_heater",
               config={}, settings={"mode": "pv_only", "max_power_w": 3000}),
    ])
    await loop._step()
    heater = of(loop, "water_heater")

    # 06:00 Ortszeit: Das Gerät startet seine Warmwasser-Sicherstellung.
    OwnProgramHeater.state.update(power=3000.0, mode="Eigenes Programm des Geräts")
    heater.sent_at = {k: 0.0 for k in heater.sent_at}   # Karenzzeit längst vorbei
    for _ in range(8):
        await loop._step()

    assert await events("folgt der Regelung nicht") == []
    assert len(await events("heizt per Geräteprogramm")) == 1
    assert heater.uncontrolled is False

    OwnProgramHeater.state.update(power=0.0, mode=None)
    await loop._step()
    ended = await events("Geräteprogramm beendet")
    assert len(ended) == 1


async def test_device_program_power_is_not_counted_as_surplus(no_sun):
    """Vorher zählten die 3 kW des selbst heizenden Stabs als verteilbarer
    Überschuss – genug, um ein Auto zu starten."""
    OwnProgramHeater.state = {"power": 3000.0, "temp": 45.0,
                              "mode": "Eigenes Programm des Geräts", "set": []}
    loop = await make_loop([
        Device(name="AC ELWA 2", category="water_heater", driver_id="test_own_program_heater",
               config={}, settings={"mode": "pv_only", "max_power_w": 3000}),
    ])
    heater = of(loop, "water_heater")
    await loop._read_all([heater])
    assert loop._redirectable_power(heater) == 0.0


# ------------------------------------------------------------ Selbststart Auto


async def test_vehicle_self_start_is_stopped_immediately_without_warning(no_sun):
    w = SimulationWorld.instance()
    w.car_connected, w.wb_enabled, w.car_soc, w.car_charge_limit = True, False, 40.0, 80.0
    loop = await make_loop([
        Device(name="Tesla", category="wallbox", driver_id="sim_wallbox",
               config={"max_current": 16}, settings={"mode": "pv_only", "phases_mode": "fixed1"}),
    ])
    await loop._step()                 # kein Überschuss → 'aus" ist gesendet
    car = of(loop, "wallbox")
    assert car.sent.get("enable") is False

    # Auto wird angesteckt und lädt von selbst mit 3 Phasen los.
    w.wb_enabled, w.wb_phases, w.wb_current = True, 3, 6.0
    w.wb_power = 6 * 3 * 230.0
    await loop._step()

    assert w.wb_enabled is False, "Stopp muss sofort erneut gesendet werden"
    assert len(await events("startet selbst")) == 1
    assert await events("folgt der Regelung nicht") == []


# ------------------------------------------------------------ Langsames Fahrzeug


async def test_slow_vehicle_read_does_not_block_the_tick(monkeypatch, no_sun):
    monkeypatch.setattr(loop_module, "READ_BUDGET_S", 0.2)
    SlowVehicle.delay, SlowVehicle.reachable = 2.0, True
    loop = await make_loop([
        Device(name="Tesla", category="wallbox", driver_id="test_slow_vehicle",
               config={}, settings={"mode": "pv_only"}),
    ])
    try:
        started = time.monotonic()
        await loop._step()
        assert time.monotonic() - started < 1.5
        meter = of(loop, "meter")
        assert meter.online, "schnelle Geräte werden trotzdem gelesen"
        car = of(loop, "wallbox")
        first = loop._read_tasks[car.id]
        await loop._step()
        assert loop._read_tasks[car.id] is first, "kein zweiter Lesezugriff, solange einer läuft"
    finally:
        for task in loop._read_tasks.values():
            task.cancel()


# ------------------------------------------------------------ Wecken


async def test_sleeping_plugged_vehicle_is_woken_on_sustained_surplus(no_sun):
    SlowVehicle.delay, SlowVehicle.reachable, SlowVehicle.plugged = 0.0, False, True
    SlowVehicle.wakes = []
    loop = await make_loop([
        Device(name="Tesla", category="wallbox", driver_id="test_slow_vehicle",
               config={}, settings={"mode": "pv_only", "phases_mode": "fixed3"}),
    ])
    await loop._step()
    car = of(loop, "wallbox")
    car.last_avail_w = 6000.0

    loop._maybe_wake(car)                       # Überschuss gerade erst da
    assert SlowVehicle.wakes == []
    car.wake_surplus_since = time.monotonic() - 400
    loop._maybe_wake(car)
    await car.wake_task
    assert len(SlowVehicle.wakes) == 1
    assert len(await events("geweckt")) == 1

    loop._maybe_wake(car)                       # nicht gleich noch einmal
    assert len(SlowVehicle.wakes) == 1


async def test_vehicle_not_woken_without_surplus_or_when_unplugged(no_sun):
    SlowVehicle.delay, SlowVehicle.reachable, SlowVehicle.plugged = 0.0, False, False
    SlowVehicle.wakes = []
    loop = await make_loop([
        Device(name="Tesla", category="wallbox", driver_id="test_slow_vehicle",
               config={}, settings={"mode": "pv_only"}),
    ])
    await loop._step()
    car = of(loop, "wallbox")
    car.last_avail_w = 6000.0
    car.wake_surplus_since = time.monotonic() - 400
    loop._maybe_wake(car)
    assert car.wake_task is None, "zuletzt nicht angesteckt → nicht wecken"


# ------------------------------------------------------------ Verschenkt


async def test_waste_episode_is_logged_with_reason(no_sun):
    loop = await make_loop([])
    # 180 Takte à 10 s mit 4 kW Einspeisung = 0,5 h × 4 kW = 2 kWh. Die
    # Leistung zählt je Takt höchstens über die Taktlänge, nie über eine Lücke.
    for i in range(180):
        loop._last_publish_at = time.monotonic() - 10
        reason = f"Batterie lädt mit {1000 + i} W – sie hat Vorrang; Tesla: nicht erreichbar"
        await loop._track_waste(4000.0, reason)
    loop._waste_started = time.monotonic() - 3700     # Episode lang genug → schreiben
    await loop._track_waste(0.0, None)
    rows = await events("kWh ins Netz")
    assert len(rows) == 1
    # Wechselnde Zahlen im Grund werden zu EINEM Grund zusammengefasst.
    assert rows[0].message.count("sie hat Vorrang") == 1
    assert "2.0 kWh" in rows[0].message
    assert rows[0].category == "waste"
    # Batterie lädt am Limit, Auto nicht da: unvermeidbar
    assert rows[0].data["avoidable"] is False


async def test_small_waste_is_not_logged(no_sun):
    loop = await make_loop([])
    loop._last_publish_at = time.monotonic() - 10
    await loop._track_waste(200.0, "Rest")
    await loop._flush_waste()
    assert await events("kWh ins Netz") == []


# ------------------------------------------------------------ Wärmepuffer


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_surplus_heats_above_target_up_to_buffer_limit():
    clock = Clock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", target_temp_c=60, surplus_temp_c=70,
                                                   start_delay_s=30), clock=clock)
    data = WaterHeaterData(power=0, temperature_c=62)
    wh.decide(2000.0, data, ChargeContext())
    clock.t += 31
    d = wh.decide(2000.0, data, ChargeContext())
    assert d.power_w == 2000.0
    assert "Wärmepuffer" in d.reason
    # Oben angekommen: aus
    assert wh.decide(2000.0, WaterHeaterData(power=2000, temperature_c=70), ChargeContext()).power_w == 0


def test_buffer_never_heats_from_grid_or_schedule():
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_price", target_temp_c=60, surplus_temp_c=70))
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=62), ChargeContext(price_ct=5.0, cheap_hour=True))
    assert d.power_w == 0, "über der Zieltemperatur nur Überschuss, nie Netz"


def test_without_buffer_target_temperature_stops_as_before():
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", target_temp_c=60))
    d = wh.decide(3000.0, WaterHeaterData(power=0, temperature_c=61), ChargeContext())
    assert d.power_w == 0 and "Zieltemperatur" in d.reason


# ------------------------------------------------------------ Phasen


def test_detected_phases_set_minimum_power():
    """fixed1 eingestellt, Fahrzeug lädt dreiphasig: Die Mindestleistung ist
    6 A × 3 × 230 V, nicht 1,4 kW – sonst startet die Regelung bei 1,4 kW
    Überschuss und das Auto zieht 4,1 kW."""
    wb = WallboxController(WallboxSettings(phases_mode="fixed1", min_current=6))
    wb.observe(WallboxData(state=WallboxState.CHARGING, power=4140, current_set=6, phases_active=3))
    assert wb._min_power() == pytest.approx(6 * 3 * 230, rel=0.05)


async def test_detected_phases_survive_restart(no_sun):
    loop = await make_loop([
        Device(name="Tesla", category="wallbox", driver_id="sim_wallbox",
               config={"max_current": 16}, settings={"mode": "pv_only", "phases_mode": "fixed1"}),
    ])
    car = of(loop, "wallbox")
    car.controller._phases_seen = 3
    loop._phases_saved[car.id] = 3
    await loop.save_runtime()

    restarted = ControlLoop()
    await restarted._reload()
    assert of(restarted, "wallbox").controller._phases_seen == 3


def test_start_delay_is_reported_as_countdown_not_as_missing_surplus():
    """Reicht der Überschuss, läuft aber noch die Startverzögerung, darf die
    Begründung nicht 'warte auf Überschuss (4067 W)' heißen – das liest sich
    wie ein Widerspruch. Sie nennt stattdessen den Start."""
    from app.core.regulation import DelayTimer, waiting_reason

    now = [1000.0]
    timer = DelayTimer(60.0, clock=lambda: now[0])
    assert waiting_reason(timer, 800.0).startswith("warte auf Überschuss")
    timer.check(True)
    now[0] += 20.0
    assert not timer.check(True)
    reason = waiting_reason(timer, 4067.0)
    assert reason == "Überschuss reicht (4067 W) – Start in 40 s"
