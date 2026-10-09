"""2.16: Leitfragen aus Teil B als Integrationstests, Eingriffe mit Ende,
Timing/Race, Fehlerinjektion in den Treibern, Migration.

Kein Test spricht mit echter Hardware – Simulationstreiber und Fälschungen.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.core.battery_policy import BatteryIntent, DischargeGuard, GuardMode
from app.core.loop import ControlLoop
from app.core.regulation import (
    RegulationConfig, WallboxController, WallboxSettings, config_from_settings, migrate_regulation,
)
from app.db import Base, async_session, engine
from app.drivers import registry
from app.drivers.base import (
    BatteryMode, WallboxData, WallboxState, WaterHeaterData,
)
from app.drivers.simulation import SimulationWorld
from app.drivers.sungrow.battery import CMD_STOP, SungrowBattery
from app.drivers.tesla.vehicle import REACHABLE_TRUST_S, TeslaVehicle, _phases
from app.models import Device, Event, Setting

from .test_battery_control import FakeSungrowConn
from .test_price_window_integration import FakeTariff


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture(autouse=True)
def night(monkeypatch):
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: 0.0)
    monkeypatch.setattr(SimulationWorld, "house_load", lambda self: 400.0)


async def site(price_ct=6.0, car_mode="pv_price", water_mode="pv_price", reg=None,
               bat_soc=60.0, controllable=True):
    registry.discover()
    SimulationWorld._instance = None
    w = SimulationWorld.instance()
    w.bat_soc = bat_soc
    w.wh_temp = 40.0
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await session.execute(delete(Device))
        rows = [
            Device(name="PV", category="inverter", driver_id="sim_inverter", config={"kwp": 9.8}, settings={}),
            Device(name="Zähler", category="meter", driver_id="sim_meter", config={}, settings={}),
            Device(name="Tesla", category="wallbox", driver_id="sim_wallbox",
                   config={"max_current": 16}, settings={"mode": car_mode, "min_current": 6,
                                                         "max_current": 16, "phases_mode": "fixed3"}),
            Device(name="ELWA", category="water_heater", driver_id="sim_water_heater",
                   config={"rated_power": 3000},
                   settings={"mode": water_mode, "max_power_w": 3000, "target_temp_c": 58}),
            Device(name="Speicher", category="battery", driver_id="sim_battery",
                   config={"capacity_kwh": 9.6, "max_power": 5000, "allow_active_control": controllable},
                   settings={}),
        ]
        session.add_all(rows)
        if reg:
            session.add(Setting(key="regulation", value=reg))
        await session.commit()
    loop = ControlLoop()
    loop.tariff = FakeTariff(price_ct)
    await loop._reload()
    for dev in loop.devices.values():
        await dev.driver.connect()
    SimulationWorld.instance().wb_phases = 3
    SimulationWorld.instance().bat_soc = bat_soc
    return loop


def dev(loop, category):
    return next(d for d in loop.devices.values() if d.category == category)


# ------------------------------------------------------------ Leitfragen (Teil B)

async def test_car_and_water_both_cheap_share_grid_and_battery_stays_out():
    """Auto und Warmwasser auf 'PV + Preis", Preis günstig: beide laufen,
    Reihenfolge Auto → Warmwasser, Netzanschluss-Grenze hält, Speicher
    entlädt nicht in die Lasten."""
    loop = await site()
    await loop._step()

    car, elwa, bat = dev(loop, "wallbox"), dev(loop, "water_heater"), dev(loop, "battery")
    assert car.decision["enable"] and car.decision["current_a"] == 16.0
    assert elwa.decision["power_w"] == 3000
    assert loop.battery_locked
    assert (await bat.driver.read_data()).mode in (BatteryMode.HOLD, BatteryMode.FORCE_DISCHARGE)
    assert loop.grid_budget_left_w >= 0


async def test_tight_grid_limit_car_first_water_gets_the_rest():
    loop = await site(reg={"grid_charge_max_w": 9000})
    loop.priority = [dev(loop, "wallbox").id, dev(loop, "water_heater").id]
    await loop._step()

    car, elwa = dev(loop, "wallbox"), dev(loop, "water_heater")
    house = 400.0
    car_w = car.decision["current_a"] * 3 * 230 if car.decision["enable"] else 0.0
    assert car.decision["enable"]
    assert car_w + elwa.decision["power_w"] <= 9000 - house + 1
    assert elwa.decision["power_w"] <= 9000 - house - car_w + 1


async def test_battery_locked_water_off_car_fast():
    """Leitfrage: 'Batterie komplett sperren" + Warmwasser aus + Auto volle
    Last – nichts Unerwartetes: Auto 16 A, Heizstab 0, Speicher ruht."""
    loop = await site(price_ct=35.0, car_mode="pv_only", water_mode="off")
    car = dev(loop, "wallbox")
    car.controller.set_override("fast", hours=12)
    loop.start_battery_manual(BatteryMode.HOLD, 1000, 60)
    for _ in range(2):
        await loop._step()

    assert car.decision["enable"] and car.decision["current_a"] == 16.0
    assert dev(loop, "water_heater").decision["power_w"] == 0
    assert loop.battery_plan.intent == BatteryIntent.HOLD
    assert (await dev(loop, "battery").driver.read_data()).mode is BatteryMode.HOLD


async def test_fast_charging_uses_battery_by_default_but_can_be_protected():
    """Diagnose 06.10. 12:00: 'Sofort voll", Speicher lieferte 0,9 kW dazu.
    Bei vollem Netzpreis ist das kein Fehler (der Speicher deckt teuren
    Strom). Wer das nicht will, schaltet die Experten-Option ein."""
    loop = await site(price_ct=33.0, car_mode="pv_only", water_mode="pv_only")
    dev(loop, "wallbox").controller.set_override("fast", hours=12)
    await loop._step()
    assert loop.battery_plan.intent == BatteryIntent.AUTO

    loop.cfg.battery_protect_other_loads = True
    await loop._step()
    assert loop.battery_plan.source == "other_loads"
    assert (await dev(loop, "battery").driver.read_data()).mode is not BatteryMode.AUTO


async def test_own_program_heater_expert_option_keeps_battery_for_the_house(monkeypatch):
    loop = await site(price_ct=35.0, car_mode="pv_only", water_mode="pv_only",
                      reg={"battery_protect_other_loads": True})
    elwa = dev(loop, "water_heater")

    async def own_program():
        return WaterHeaterData(power=2990.0, temperature_c=45.0, device_mode="Warmwasser-Sicherstellung")
    monkeypatch.setattr(elwa.driver, "read_data", own_program)
    SimulationWorld.instance().wh_power = 2990.0        # der Zähler sieht den Heizstab
    monkeypatch.setattr(SimulationWorld, "tick", lambda self: None)
    await loop._step()

    assert loop.battery_plan.source == "foreign_loads"
    mode = (await dev(loop, "battery").driver.read_data()).mode
    # Speicher versorgt nur das Haus (400 W) – den Heizstab das Netz
    assert mode in (BatteryMode.FORCE_DISCHARGE, BatteryMode.HOLD)
    if mode is BatteryMode.FORCE_DISCHARGE:
        assert SimulationWorld.instance().bat_force_w == pytest.approx(400, abs=60)
    # Eigenbetrieb ist kein Überschuss
    assert loop._redirectable_power(elwa) == 0.0


async def test_grid_charge_needs_switch_and_bad_forecast():
    class Forecast:
        def __init__(self, kwh):
            self.kwh = kwh

        def is_transient_dip(self, pv):
            return False

        def expected_surplus_kwh(self, house):
            return self.kwh

    reg = {"battery_grid_charge_enabled": True, "battery_grid_charge_soc": 80}
    loop = await site(car_mode="pv_only", water_mode="pv_only", reg=reg, bat_soc=30.0)
    loop.forecast = Forecast(20.0)         # Sonne bringt morgen 20 kWh
    await loop._step()
    assert loop.battery_plan.intent == BatteryIntent.AUTO and "Sonne" in loop.battery_plan.reason

    loop.forecast = Forecast(0.5)          # trüb
    await loop._step()
    assert loop.battery_plan.intent == BatteryIntent.CHARGE
    assert (await dev(loop, "battery").driver.read_data()).mode is BatteryMode.FORCE_CHARGE


async def test_battery_mode_change_is_logged_once():
    """08.10.: 1,5 h Netzladen ohne einen Protokolleintrag. Jetzt steht jeder
    Wechsel der Automatik genau einmal drin."""
    loop = await site()
    for _ in range(3):
        await loop._step()
    async with async_session() as s:
        rows = (await s.scalars(select(Event).where(Event.category == "battery"))).all()
    assert len(rows) == 1 and "Entladung gesperrt" in rows[0].message


# ------------------------------------------------------------ Eingriffe enden

def wb_controller(clock=None):
    ctrl = WallboxController(WallboxSettings(mode="pv_only", phases_mode="fixed3"), clock=clock or FakeClock())
    now = [datetime(2026, 10, 8, 2, 43, tzinfo=timezone.utc)]
    ctrl.utcnow = lambda: now[0]
    return ctrl, now


def test_stop_override_ends_after_timeout():
    """08.10.: 'Aus" um 02:43 hielt bis zum Nachmittag – 3,5 kWh Sonne
    gingen ins Netz. Jetzt endet es spätestens nach 4 h."""
    ctrl, now = wb_controller()
    ctrl.set_override("stop", hours=4)
    connected = WallboxData(state=WallboxState.CONNECTED, vehicle_reachable=True)
    ctrl.decide(0, connected, _ctx())
    assert ctrl.override == "stop"
    now[0] += timedelta(hours=4, seconds=1)
    ctrl.decide(0, connected, _ctx())
    assert ctrl.override is None
    assert ctrl.pop_override_end() == ("stop", "Zeit abgelaufen")


def test_stop_override_ends_on_unplug_but_not_while_asleep():
    ctrl, now = wb_controller()
    ctrl.set_override("stop", hours=4)
    now[0] += timedelta(minutes=10)
    ctrl.decide(0, WallboxData(state=WallboxState.IDLE, vehicle_reachable=False), _ctx())
    assert ctrl.override == "stop"          # schläft – nicht 'abgesteckt"
    ctrl.decide(0, WallboxData(state=WallboxState.IDLE, vehicle_reachable=True), _ctx())
    assert ctrl.override is None
    assert ctrl.pop_override_end() == ("stop", "abgesteckt")


def test_fast_override_ends_when_full_but_not_right_after_pressing():
    ctrl, now = wb_controller()
    ctrl.set_override("fast", hours=12)
    full = WallboxData(state=WallboxState.COMPLETE, vehicle_reachable=True)
    ctrl.decide(0, full, _ctx())            # Messwert stammt noch von vorher
    assert ctrl.override == "fast"
    now[0] += timedelta(minutes=3)
    ctrl.decide(0, full, _ctx())
    assert ctrl.override is None
    assert ctrl.pop_override_end() == ("fast", "Ladeziel erreicht")


def test_override_state_shows_remaining_time():
    ctrl, now = wb_controller()
    ctrl.set_override("fast", hours=12)
    now[0] += timedelta(hours=1)
    state = ctrl.override_state()
    assert state["kind"] == "fast" and state["remaining_s"] == 11 * 3600


async def test_api_sets_override_with_end(monkeypatch):
    from app.api.devices import device_action
    from app.core import runtime
    from app.schemas import DeviceActionRequest

    loop = await site(car_mode="pv_only")
    monkeypatch.setattr(runtime, "get_loop", lambda: loop)
    car = dev(loop, "wallbox")

    class U:
        id = None
        role = "admin"

    await device_action(car.id, DeviceActionRequest(action="stop"), user=U())
    until = car.controller.override_until_utc
    assert until is not None
    assert abs((until - datetime.now(timezone.utc)).total_seconds() - 4 * 3600) < 60
    await device_action(car.id, DeviceActionRequest(action="auto"), user=U())
    assert car.controller.override is None


async def test_override_during_running_step_is_applied_next_step(monkeypatch):
    """Race: Befehl trifft ein, während ein Takt läuft. Er darf nicht verloren
    gehen und nicht doppelt wirken."""
    loop = await site(price_ct=6.0, car_mode="pv_price")
    car = dev(loop, "wallbox")

    async def press():
        await asyncio.sleep(0)
        car.controller.set_override("stop", hours=4)
        loop.kick()

    await asyncio.gather(loop._step(), press())
    await loop._step()
    assert car.decision["enable"] is False
    assert loop._wake.is_set()


async def test_duplicate_commands_reach_the_device_once():
    calls = []

    class Drv:
        resend_interval_s = 0

        async def stop_charging(self):
            calls.append("stop")

    loop = ControlLoop()

    class D:
        name = "Tesla"
        driver = Drv()
        sent, sent_at, send_fail_count, send_backoff = {}, {}, {}, {}
        command_error = None
        last_error = None

    d = D()
    for _ in range(3):
        await loop._send(d, "enable", False, lambda: d.driver.stop_charging())
    assert calls == ["stop"]


async def test_failing_command_is_backed_off_not_hammered():
    calls = []

    async def fail():
        calls.append(1)
        raise RuntimeError("Proxy nicht erreichbar")

    loop = ControlLoop()

    class D:
        name = "Tesla"
        driver = object()
        sent, sent_at, send_fail_count, send_backoff = {}, {}, {}, {}
        command_error = None
        last_error = None

    d = D()
    for _ in range(5):
        await loop._send(d, "enable", True, fail)
    assert len(calls) == 1
    assert d.command_error.startswith("enable:")


# ------------------------------------------------------------ Phasen, Pause

def _ctx(**kw):
    from app.core.regulation import ChargeContext
    return ChargeContext(now=datetime(2026, 10, 7, 14, 0), house_limit_a=63, **kw)


def test_downgrade_to_one_phase_needs_confirmation():
    """07.10.: Ein einzelnes '1 Phase" ließ den Regler mit 1,6 kW Mindest-
    leistung rechnen; das Auto zog 4,2 kW – Flattern."""
    clock = FakeClock()
    ctrl = WallboxController(WallboxSettings(phases_mode="fixed3"), clock=clock)
    one = WallboxData(state=WallboxState.CHARGING, power=1380, current_set=6, phases_active=1)
    ctrl.observe(one)
    assert ctrl._min_power() == pytest.approx(6 * 3 * 230, rel=0.15)
    for _ in range(3):
        clock.advance(30)
        ctrl.observe(one)
    assert ctrl._phases_seen == 1


def test_upgrade_to_three_phases_is_immediate():
    ctrl = WallboxController(WallboxSettings(phases_mode="fixed1"), clock=FakeClock())
    ctrl.observe(WallboxData(state=WallboxState.CHARGING, power=4140, current_set=6, phases_active=3, voltage=230))
    assert ctrl._phases_seen == 3
    assert ctrl._min_power() == pytest.approx(4140)


def test_tesla_phase_report_is_checked_against_power():
    assert _phases({"charger_phases": 1}, amps=6, volts=230, reported_kw=4) == 3
    assert _phases({"charger_phases": 2}, amps=6, volts=230, reported_kw=1) == 1
    assert _phases({"charger_phases": 2}, amps=0, volts=None, reported_kw=0) == 3


def test_min_pause_after_stop_limits_ble_traffic():
    clock = FakeClock()
    ctrl = WallboxController(WallboxSettings(phases_mode="fixed1", start_delay_s=0, stop_delay_s=0,
                                             min_pause_s=300), clock=clock)
    charging = WallboxData(state=WallboxState.CHARGING, power=1380, current_set=6)
    assert ctrl.decide(2000, WallboxData(state=WallboxState.CONNECTED), _ctx()).enable
    ctrl.decide(2000, charging, _ctx())
    clock.advance(1)
    assert not ctrl.decide(0, charging, _ctx()).enable          # Überschuss weg → Stopp
    clock.advance(60)
    d = ctrl.decide(5000, WallboxData(state=WallboxState.CONNECTED), _ctx())
    assert not d.enable and "Pause" in d.reason
    clock.advance(300)
    assert ctrl.decide(5000, WallboxData(state=WallboxState.CONNECTED), _ctx()).enable


def test_unreachable_vehicle_keeps_last_decision_without_commands():
    clock = FakeClock()
    ctrl = WallboxController(WallboxSettings(phases_mode="fixed1", start_delay_s=0), clock=clock)
    ctrl.decide(3000, WallboxData(state=WallboxState.CONNECTED), _ctx())
    d = ctrl.decide(0, WallboxData(state=WallboxState.CHARGING, power=2000, vehicle_reachable=False), _ctx())
    assert d.enable and "nicht erreichbar" in d.reason


# ------------------------------------------------------------ Fehlerinjektion

class FailingEmsConn(FakeSungrowConn):
    async def write_register(self, address, value, *, verify=False, expect=None):
        if address == 13049 and value == 2:
            raise TimeoutError("Modbus-Timeout")
        return await super().write_register(address, value, verify=verify, expect=expect)


async def test_sungrow_rolls_back_to_self_consumption_on_failed_write():
    drv = SungrowBattery({"host": "192.0.2.1", "allow_active_control": True})
    drv.conn = FailingEmsConn()
    with pytest.raises(TimeoutError):
        await drv.set_mode(BatteryMode.HOLD)
    # Befehl 'stopp" ging raus, EMS-Zwangsmodus scheiterte → zurück auf 0
    assert (13050, 0) in drv.conn.writes
    assert drv.conn.holding[13049] == 0


async def test_sungrow_reserve_is_clamped_to_what_the_inverter_accepts():
    drv = SungrowBattery({"host": "192.0.2.1", "allow_active_control": True})
    drv.conn = FakeSungrowConn(max_soc=1000, min_soc=50, cmd=CMD_STOP)
    await drv.set_reserve_soc(55)
    assert (13059, 500) in drv.conn.writes


async def test_rejected_reserve_is_reported_and_held_in_software():
    from app.drivers.validation import DeviceRejected

    class RejectingConn(FakeSungrowConn):
        async def write_register(self, address, value, *, verify=False, expect=None):
            if address == 13058:
                raise DeviceRejected("Schreiben Register 13058 abgelehnt – Gerät meldet einen internen Fehler.")
            return await super().write_register(address, value, verify=verify, expect=expect)

    loop = await site(car_mode="pv_only", water_mode="pv_only", reg={"battery_reserve_soc": 55})
    bat = dev(loop, "battery")
    bat.driver = SungrowBattery({"host": "192.0.2.1", "allow_active_control": True})
    bat.driver.conn = RejectingConn()
    bat.online = True
    await loop._apply_battery(bat)
    assert bat.inverter_reserve is None
    assert loop._inverter_holds_reserve([bat]) is False
    async with async_session() as s:
        rows = (await s.scalars(select(Event).where(Event.category == "battery"))).all()
    assert any("hält sie selbst" in r.message for r in rows)


class TimeoutClient:
    def __init__(self):
        self.ok = True

    async def vehicle_data(self, endpoint):
        if self.ok:
            return {"charge_state": {"charging_state": "Charging", "charger_actual_current": 6,
                                     "charger_voltage": 230, "charger_power": 4, "charger_phases": 2,
                                     "charge_amps": 6, "battery_level": 60}}
        raise TimeoutError("BLE")

    async def close(self):
        pass


async def test_tesla_last_known_state_instead_of_gone(monkeypatch):
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://192.0.2.33"})
    drv.client = TimeoutClient()
    t = [1000.0]
    monkeypatch.setattr("app.drivers.tesla.vehicle.time.monotonic", lambda: t[0])
    first = await drv.read_data()
    assert first.vehicle_reachable and first.phases_active == 3
    drv.client.ok = False
    t[0] += 30
    short = await drv.read_data()
    assert short.vehicle_reachable is True and short.power == first.power   # kurzer Aussetzer
    t[0] += REACHABLE_TRUST_S + 30
    long = await drv.read_data()
    assert long.vehicle_reachable is False
    assert long.state == WallboxState.CHARGING          # letzter Stand, nicht 'weg"
    assert long.power == 0.0
    assert drv.likely_plugged_in and drv.presence_known


async def test_proxy_offline_goes_offline_with_backoff_and_presence():
    class Offline:
        async def read_data(self):
            raise ConnectionError("Tesla-Endpunkt http://192.0.2.33:8080 nicht erreichbar – Proxy?")

        async def connect(self):
            pass

    loop = await site(car_mode="pv_only", water_mode="pv_only")
    car = dev(loop, "wallbox")
    car.driver.read_data = Offline().read_data
    car.online = True
    for _ in range(4):
        await loop._read_device(car)
    assert car.online is False
    assert car.read_retry_at > time.monotonic()
    assert loop._presence(car) == "proxy_offline"


# ------------------------------------------------------------ Entladesperre

def test_guard_enters_protection_before_the_load_starts():
    g = DischargeGuard()
    step = g.step(0.0, battery_w=0.0, grid_w=0.0, pv_w=0.0, house_w=200.0, expected_load_w=11000.0)
    assert step.mode == GuardMode.HOLD


def test_guard_supplies_house_only():
    g = DischargeGuard()
    step = g.step(0.0, battery_w=-3500.0, grid_w=0.0, pv_w=0.0, house_w=600.0)
    assert step.mode == GuardMode.HOUSE and step.power_w == 600.0


def test_guard_releases_after_sustained_export():
    g = DischargeGuard()
    g.step(0.0, battery_w=-500.0, grid_w=0.0, pv_w=0.0, house_w=200.0)
    assert g.mode == GuardMode.HOLD
    assert g.step(30.0, battery_w=0.0, grid_w=-1500.0, pv_w=4000.0, house_w=500.0).mode == GuardMode.HOLD
    assert g.step(95.0, battery_w=0.0, grid_w=-1500.0, pv_w=4000.0, house_w=500.0).mode == GuardMode.AUTO


def test_guard_never_discharges_below_reserve():
    g = DischargeGuard()
    step = g.step(0.0, battery_w=-800.0, grid_w=0.0, pv_w=0.0, house_w=800.0, soc=19.0, reserve_soc=20.0)
    assert step.mode == GuardMode.HOLD


# ------------------------------------------------------------ Migration

USER_REGULATION = {  # aus der 7-Tage-Diagnose
    "interval_s": 20.0, "grid_target_w": 0.0, "deadband_w": 100.0, "smoothing_samples": 3,
    "house_limit_a": 35.0, "battery_priority": True, "battery_reserve_soc": 55.0, "ev_lock_soc": 30.0,
    "battery_drain_limit_w": 250.0, "adaptive_deadband": True, "deadband_min_w": 40.0,
    "deadband_max_w": 400.0, "gap_fill_min_w": 25.0, "use_forecast": True,
    "price_window_battery": "charge", "battery_release_soc": 100.0, "battery_release_hysteresis": 5.0,
    "battery_grid_charge_enabled": False, "grid_charge_max_w": 10000.0,
    "battery_manual_power_w": 7000.0, "battery_manual_max_min": 240.0, "vehicle_wake_enabled": True,
}


def test_user_regulation_migrates_without_losing_intent():
    out = migrate_regulation(USER_REGULATION)
    assert out["interval_s"] == 10.0
    assert out["battery_reserve_soc"] == 55.0          # die Reserve bleibt
    assert out["battery_priority_soc"] == 100.0        # Batterie zuerst – wie vorher
    assert out["battery_grid_charge_enabled"] is False
    assert out["battery_manual_power_w"] == 7000.0
    for legacy in ("battery_priority", "price_window_battery", "battery_release_soc", "deadband_w", "ev_lock_soc"):
        assert legacy not in out
    assert migrate_regulation(out) == out              # idempotent
    cfg = config_from_settings(USER_REGULATION)
    assert cfg.interval_s == 10.0 and cfg.battery_priority_soc == 100.0


def test_priority_off_maps_to_loads_first_above_reserve():
    out = migrate_regulation({"battery_priority": False, "battery_reserve_soc": 25, "ev_lock_soc": 80})
    assert out["battery_priority_soc"] == 25
    assert out["battery_ev_support_soc"] == 80


async def test_migrate_settings_moves_everything_and_keeps_a_backup():
    from app.startup import SCHEMA_VERSION, migrate_settings

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        s.add_all([
            Device(id=3, name="Tesla", category="wallbox", driver_id="sim_wallbox", config={},
                   settings={"mode": "pv_only", "price_limit_mode": "global", "price_limit_ct": 25}),
            Device(id=4, name="Speicher", category="battery", driver_id="sim_battery", config={},
                   settings={"reserve_soc": 20, "manage_reserve": False}),
            Device(id=5, name="ELWA", category="water_heater", driver_id="sim_water_heater", config={},
                   settings={"mode": "pv_only", "price_limit_ct": 15}),
            Setting(key="regulation", value=USER_REGULATION),
            Setting(key="priority", value={"order": [3, 4, 5]}),
        ])
        await s.commit()
    await migrate_settings()
    await migrate_settings()                    # zweimal: nichts doppelt
    async with async_session() as s:
        devices = {d.id: d for d in (await s.scalars(select(Device))).all()}
        settings = {r.key: r.value for r in (await s.scalars(select(Setting))).all()}
    assert settings["priority"]["order"] == [3, 5]
    assert settings["regulation"]["battery_priority_soc"] == 100.0
    assert "price_limit_ct" not in devices[3].settings and devices[3].settings["mode"] == "pv_only"
    assert "price_limit_ct" not in devices[5].settings
    assert devices[4].settings == {"manage_reserve": True}
    assert settings["schema"]["version"] == SCHEMA_VERSION
    assert settings["schema"]["backup_v1"]["regulation"]["battery_release_soc"] == 100.0


def test_config_has_no_deadband_or_smoothing_settings_anymore():
    cfg = config_from_settings({"deadband_w": 999, "smoothing_samples": 9})
    assert cfg.deadband_w == RegulationConfig().deadband_w
    assert cfg.smoothing_samples == RegulationConfig().smoothing_samples


async def test_manual_discharge_lock_via_api(monkeypatch):
    """Batterie-Modi aus Teil B: 'Entladung sperren" und 'komplett sperren"
    als manuelle Befehle mit Dauer."""
    from app.api.battery import BatteryCommand, battery_manual
    from app.core import runtime

    loop = await site(price_ct=35.0, car_mode="pv_only", water_mode="pv_only")
    monkeypatch.setattr(runtime, "get_loop", lambda: loop)
    await loop._step()                      # Geräte online

    class U:
        id = None
        role = "admin"

    await battery_manual(BatteryCommand(action="no_discharge", minutes=30), U())
    await loop._step()
    assert loop.battery_plan.intent == BatteryIntent.NO_DISCHARGE and loop.battery_plan.source == "manual"
    await battery_manual(BatteryCommand(action="hold", minutes=30), U())
    await loop._step()
    assert (await dev(loop, "battery").driver.read_data()).mode is BatteryMode.HOLD
    await battery_manual(BatteryCommand(action="auto"), U())
    await loop._step()
    assert loop.battery_plan.intent == BatteryIntent.AUTO


async def test_lock_ignores_manual_power_above_device_limit(monkeypatch):
    """Feld 08.10.: Vorgabe 'Leistung von Hand" 7000 W bei 5-kW-Speicher.
    'Entladung sperren" scheiterte mit 'Leistung muss zwischen 100 W und
    5000 W liegen" – Sperren braucht gar keine Leistung. Laden ohne
    Leistungsangabe nimmt die gedeckelte Vorgabe."""
    from app.api.battery import BatteryCommand, battery_manual
    from app.core import runtime

    loop = await site(price_ct=35.0, car_mode="pv_only", water_mode="pv_only")
    monkeypatch.setattr(runtime, "get_loop", lambda: loop)
    await loop._step()
    loop.cfg.battery_manual_power_w = 99999.0

    class U:
        id = None
        role = "admin"

    for action in ("no_discharge", "hold"):
        await battery_manual(BatteryCommand(action=action, minutes=30), U())
    await battery_manual(BatteryCommand(action="charge", minutes=30), U())
    assert loop.battery_manual is not None
    assert loop.battery_manual.power_w <= loop._battery_max_power(
        [d for d in loop.devices.values() if d.category == "battery"])


# ------------------------------------------------------------ Zeitplan Warmwasser

from app.core.regulation import ChargeContext  # noqa: E402

def _heater(mode="schedule"):
    from app.core.regulation import WaterHeaterController, WaterHeaterSettings
    t = [0.0]
    ctrl = WaterHeaterController(WaterHeaterSettings(mode=mode, max_power_w=3000, min_power_w=100,
                                                     target_temp_c=60, start_delay_s=0),
                                 clock=lambda: t[0])
    return ctrl, t


def test_schedule_window_heats_full_power():
    from app.drivers.base import WaterHeaterData
    ctrl, _ = _heater()
    d = ctrl.decide(0.0, WaterHeaterData(power=0.0, temperature_c=40.0), ChargeContext(schedule_active=True))
    assert d.power_w == 3000 and "Zeitfenster" in d.reason


def test_schedule_uses_pv_outside_window():
    """Vorher: außerhalb des Fensters immer aus, auch bei 3 kW Überschuss."""
    from app.drivers.base import WaterHeaterData
    ctrl, t = _heater()
    data = WaterHeaterData(power=0.0, temperature_c=40.0)
    d = None
    for _ in range(5):
        d = ctrl.decide(2000.0, data, ChargeContext(schedule_active=False))
        t[0] += 10
    assert d.power_w > 0 and "Überschuss" in d.reason


def test_schedule_window_stops_at_target_and_respects_grid_limit():
    from app.drivers.base import WaterHeaterData
    ctrl, _ = _heater()
    hot = ctrl.decide(0.0, WaterHeaterData(power=0.0, temperature_c=61.0), ChargeContext(schedule_active=True))
    assert hot.power_w == 0
    tight = ctrl.decide(0.0, WaterHeaterData(power=0.0, temperature_c=40.0),
                        ChargeContext(schedule_active=True, grid_budget_w=1200.0))
    assert tight.power_w == 1200.0


async def test_schedule_api_validates_input():
    from fastapi import HTTPException
    from app.api.settings_api import create_schedule
    from app.schemas import ScheduleCreate

    loop = await site()
    heater_id = dev(loop, "water_heater").id
    async with async_session() as db:
        for bad in ({"start_time": "25:00", "end_time": "06:00"},
                    {"start_time": "06:00", "end_time": "06:00"},
                    {"start_time": "06:00", "end_time": "07:00", "days_mask": 0}):
            with pytest.raises(HTTPException):
                await create_schedule(ScheduleCreate(device_id=heater_id, **bad), db, None)
        ok = await create_schedule(ScheduleCreate(device_id=heater_id, start_time="6:00", end_time="07:30"), db, None)
        assert ok.start_time == "06:00"


def test_schedule_uses_local_time_not_container_utc(monkeypatch):
    """Container läuft in UTC. Ein Fenster 20–22 Uhr muss um 20 Uhr
    Ortszeit (18 UTC) greifen, nicht um 22 Uhr."""
    from datetime import timezone
    from app.core import clock
    from app.core.schedules import any_window_active

    monkeypatch.delenv("TZ", raising=False)
    real = clock.datetime

    class Fixed(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 8, 18, 0, tzinfo=timezone.utc).astimezone(tz) if tz else real(2026, 10, 8, 18, 0)

    monkeypatch.setattr(clock, "datetime", Fixed)
    win = [{"days_mask": 127, "start_time": "20:00", "end_time": "22:00", "enabled": True}]
    assert any_window_active(win)            # 18:00 UTC = 20:00 Berlin
    assert clock.local_now().hour == 20
    assert not any_window_active([{**win[0], "start_time": "06:00", "end_time": "08:00"}])
