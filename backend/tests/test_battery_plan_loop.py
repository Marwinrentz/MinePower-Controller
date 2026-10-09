"""Batterie-Plan im Control-Loop: Absichten der Lasten erkennen und den Plan
in Gerätebefehle umsetzen (ersetzt die Tests des alten Preisfensters).

Die Fälschungen sind echte BatteryDriver-Unterklassen; 'kann nicht" heißt
'überschreibt die Methode nicht" – genau wie in der Wirklichkeit.
"""
import pytest

from app.core.battery_policy import BatteryIntent, BatteryPlan, DischargeGuard
from app.core.loop import ControlLoop
from app.core.regulation import (
    WallboxController, WallboxSettings, WaterHeaterController, WaterHeaterSettings,
)
from app.drivers.base import (
    BatteryData, BatteryDriver, BatteryMode, WallboxData, WallboxState, WaterHeaterData,
)


class _RecordingBattery(BatteryDriver):
    meta = None

    def __init__(self, *, controllable=True, fail=False):
        super().__init__({"allow_active_control": controllable})
        self.mode_calls: list[tuple[BatteryMode, float | None]] = []
        self.reserve_calls: list[int] = []
        self.fail = fail

    async def read_data(self) -> BatteryData:
        return BatteryData(soc=50.0, power=-500.0)


class FullBattery(_RecordingBattery):
    async def set_mode(self, mode: BatteryMode, power_w=None) -> None:
        if self.fail:
            raise RuntimeError("Register 13050 abgelehnt")
        self.mode_calls.append((mode, power_w))

    async def set_reserve_soc(self, soc: int) -> None:
        self.reserve_calls.append(soc)


class ReserveOnlyBattery(_RecordingBattery):
    async def set_reserve_soc(self, soc: int) -> None:
        self.reserve_calls.append(soc)


class MonitoringBattery(_RecordingBattery):
    pass


class FakeDevice:
    def __init__(self, name, driver=None, settings=None, category="battery"):
        self.id = id(self)
        self.name = name
        self.category = category
        self.driver = driver
        self.settings = settings or {}
        self.online = True
        self.enabled = True
        self.sent = {}
        self.sent_at = {}
        self.send_fail_count = {}
        self.send_backoff = {}
        self.controller = None
        self.command_error = None
        self.last_error = None
        self.data = None
        self.schedules = []
        self.discharge_guard = DischargeGuard()
        self.battery_mismatch_reported = False
        self.battery_target = None
        self.inverter_reserve = None


@pytest.fixture
def loop():
    return ControlLoop()


def wallbox(mode="pv_price", override=None, state=WallboxState.CONNECTED, reachable=True, power=0.0):
    dev = FakeDevice("Tesla", category="wallbox")
    dev.controller = WallboxController(WallboxSettings(mode=mode, phases_mode="fixed3"))
    dev.controller.override = override
    dev.data = WallboxData(state=state, vehicle_reachable=reachable, power=power)
    return dev


def heater(mode="pv_price", temp=45.0, boost=False, device_mode=None, power=0.0):
    dev = FakeDevice("ELWA", category="water_heater")
    dev.controller = WaterHeaterController(WaterHeaterSettings(mode=mode, target_temp_c=58))
    if boost:
        dev.controller.start_boost()
    dev.data = WaterHeaterData(temperature_c=temp, device_mode=device_mode, power=power)
    return dev


# ------------------------------------------------- Absichten der Lasten

def test_price_mode_car_wants_grid_only_when_it_can_charge(loop):
    assert loop._grid_intents([wallbox()], [], cheap=True)
    assert not loop._grid_intents([wallbox()], [], cheap=False)
    # Volles, abgestecktes oder schlafendes Auto 'öffnete" bis 2.15 trotzdem
    # ein Fenster – und der Speicher wurde dafür aus dem Netz geladen.
    assert not loop._grid_intents([wallbox(state=WallboxState.COMPLETE)], [], cheap=True)
    assert not loop._grid_intents([wallbox(state=WallboxState.IDLE)], [], cheap=True)
    assert not loop._grid_intents([wallbox(reachable=False)], [], cheap=True)


def test_stop_override_beats_price(loop):
    assert not loop._grid_intents([wallbox(override="stop")], [], cheap=True)


def test_fast_is_a_grid_load(loop):
    intents = loop._grid_intents([wallbox(mode="pv_only", override="fast")], [], cheap=False)
    (name, watts, cheap_load), = intents.values()
    assert "Sofort" in name and watts >= 11000
    assert cheap_load is False      # kein Preisfenster → Speicher darf helfen (Standard)


def test_pv_only_never_wants_grid(loop):
    assert not loop._grid_intents([wallbox(mode="pv_only")], [heater(mode="pv_only")], cheap=True)


def test_heater_intents(loop):
    assert loop._grid_intents([], [heater()], cheap=True)
    assert not loop._grid_intents([], [heater(temp=60)], cheap=True)       # Ziel erreicht
    assert loop._grid_intents([], [heater(mode="pv_only", boost=True)], cheap=False)
    assert not loop._grid_intents([], [heater(mode="off", boost=True)], cheap=True)


def test_device_program_is_foreign_not_grid_intent(loop):
    elwa = heater(mode="pv_only", device_mode="Warmwasser-Sicherstellung", power=2990)
    assert not loop._grid_intents([], [elwa], cheap=True)
    assert loop._foreign_loads([], [elwa]) == ["ELWA (Geräteprogramm)"]


def test_self_starting_car_is_foreign(loop):
    car = wallbox(mode="pv_only", state=WallboxState.CHARGING, power=4200)
    car.sent["enable"] = False
    assert loop._foreign_loads([car], []) == ["Tesla (Selbststart)"]


# ------------------------------------------------- Plan umsetzen

async def run(loop, devs, plan, **kw):
    soc = kw.pop("soc", 50.0)
    params = dict(bat_power_s=-500.0, grid_power=0.0, pv_power=0.0, house_power=400.0,
                  expected_grid_w=0.0)
    params.update(kw)
    battery = BatteryData(soc=soc, power=params["bat_power_s"])
    return await loop._execute_battery_plan(devs, plan, battery, **params)


async def test_auto_plan_sends_auto(loop):
    drv = FullBattery()
    locked, share = await run(loop, [FakeDevice("Speicher", drv)], BatteryPlan(BatteryIntent.AUTO, "x"))
    assert drv.mode_calls == [(BatteryMode.AUTO, None)]
    assert locked is False and share == 0.0


async def test_grid_charge_sends_force_charge_with_budget(loop):
    drv = FullBattery()
    plan = BatteryPlan(BatteryIntent.CHARGE, "günstig", "grid_charge", power_w=4600)
    locked, share = await run(loop, [FakeDevice("Speicher", drv)], plan)
    assert drv.mode_calls == [(BatteryMode.FORCE_CHARGE, 4600)]
    assert locked is True and share == 4600


async def test_grid_charge_without_budget_only_holds(loop):
    drv = FullBattery()
    plan = BatteryPlan(BatteryIntent.CHARGE, "günstig", "grid_charge", power_w=100)
    await run(loop, [FakeDevice("Speicher", drv)], plan)
    assert drv.mode_calls == [(BatteryMode.HOLD, None)]


async def test_no_discharge_holds_when_house_need_is_small(loop):
    drv = FullBattery()
    plan = BatteryPlan(BatteryIntent.NO_DISCHARGE, "Auto lädt mit Netzstrom", "grid_loads")
    locked, _ = await run(loop, [FakeDevice("Speicher", drv)], plan, house_power=200.0)
    assert drv.mode_calls == [(BatteryMode.HOLD, None)]
    assert locked is True


async def test_no_discharge_keeps_supplying_the_house(loop):
    """06:00, ELWA im Geräteprogramm: Das Haus (600 W) darf weiter aus dem
    Speicher kommen, die 3 kW des Heizstabs nicht."""
    drv = FullBattery()
    plan = BatteryPlan(BatteryIntent.NO_DISCHARGE, "ELWA im Eigenbetrieb", "foreign_loads")
    await run(loop, [FakeDevice("Speicher", drv)], plan, house_power=600.0, bat_power_s=-3600.0)
    assert drv.mode_calls == [(BatteryMode.FORCE_DISCHARGE, 600)]


async def test_failed_write_is_not_protected(loop):
    """Bis 2.15 galt der Speicher als gesperrt, sobald der Treiber set_mode
    kannte – auch wenn das Schreiben scheiterte. Dann lud das Auto mit
    Netzstrom, und der Speicher lief leer."""
    drv = FullBattery(fail=True)
    dev = FakeDevice("Speicher", drv)
    plan = BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads")
    locked, _ = await run(loop, [dev], plan)
    assert locked is False
    assert dev.command_error and dev.command_error.startswith("battery_mode:")


async def test_reserve_only_battery_blocks_via_reserve(loop):
    drv = ReserveOnlyBattery()
    plan = BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads")
    dev = FakeDevice("Speicher", drv)
    locked, _ = await run(loop, [dev], plan, soc=47.3)
    assert drv.reserve_calls == [48]
    assert locked is True
    loop.cfg.battery_reserve_soc = 25
    await run(loop, [dev], BatteryPlan(BatteryIntent.AUTO, "x"))
    assert drv.reserve_calls[-1] == 25


async def test_monitoring_battery_is_never_protected(loop):
    drv = MonitoringBattery(controllable=True)
    locked, _ = await run(loop, [FakeDevice("Speicher", drv)],
                          BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads"))
    assert locked is False


async def test_uncontrollable_battery_is_never_protected(loop):
    drv = FullBattery(controllable=False)
    locked, _ = await run(loop, [FakeDevice("Speicher", drv)],
                          BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads"))
    assert locked is False and drv.mode_calls == []


async def test_offline_battery_is_never_protected(loop):
    drv = FullBattery()
    dev = FakeDevice("Speicher", drv)
    dev.online = False
    locked, _ = await run(loop, [dev], BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads"))
    assert locked is False


async def test_no_battery_at_all_is_safe_inline_battery_is_not(loop):
    plan = BatteryPlan(BatteryIntent.NO_DISCHARGE, "x", "grid_loads")
    assert await loop._execute_battery_plan([], plan, None, bat_power_s=0, grid_power=0,
                                            pv_power=0, house_power=0, expected_grid_w=0) == (True, 0.0)
    inline = BatteryData(soc=40, power=-800)
    assert (await loop._execute_battery_plan([], plan, inline, bat_power_s=-800, grid_power=0,
                                             pv_power=0, house_power=0, expected_grid_w=0))[0] is False


async def test_every_battery_must_be_protected(loop):
    ok, bad = FullBattery(), FullBattery(fail=True)
    locked, _ = await run(loop, [FakeDevice("A", ok), FakeDevice("B", bad)],
                          BatteryPlan(BatteryIntent.HOLD, "manuell", "manual"))
    assert locked is False
