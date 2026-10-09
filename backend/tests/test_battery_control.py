"""Aktive Batteriesteuerung: Sungrow-Registerkarte, manueller Modus,
Sollmodus-Abgleich, Fail-safe und Netzanschluss-Budget.

Hintergrund (Diagnosebericht 30.09.): Die Batterie lud nicht. Ursachen waren
eine fehlende Schreibfreigabe, eine um eins verschobene Registerkarte für
Max-/Min-SoC und ein Batteriemodus, der nie zurückgelesen wurde. Die Tests
hier halten jede dieser Stellen fest.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.api.battery import BatteryCommand, battery_manual
from app.core.loop import BATTERY_MIN_FORCE_W
from app.drivers.base import BatteryMode
from app.drivers.modbus_util import ModbusConnection
from app.drivers.simulation import SimulationWorld
from app.drivers.sungrow.battery import (
    CMD_CHARGE,
    CMD_DISCHARGE,
    CMD_STOP,
    EMS_FORCED,
    EMS_SELF_CONSUMPTION,
    SungrowBattery,
)
from app.drivers.validation import CommandNotApplied, DeviceRejected

from .test_price_window_integration import battery_of, build_loop, wallbox_of


# ------------------------------------------------------------ Sungrow-Treiber


class FakeSungrowConn:
    """Holding-/Input-Register als dict (Adressen 0-basiert wie beim Zugriff)."""

    def __init__(self, *, max_soc=1000, min_soc=150, ems=0, cmd=CMD_STOP, power=0, soc=556):
        self.holding = {13049: ems, 13050: cmd, 13051: power, 13057: max_soc, 13058: min_soc}
        self.input = {13000: 0x0002, 13021: 1200, 13022: soc, 13023: 990, 13024: 215}
        self.writes: list[tuple[int, int]] = []
        self.write_log = []

    async def read_input(self, address, count=1):
        return [self.input.get(address + i, 0) for i in range(count)]

    async def read_holding(self, address, count=1):
        return [self.holding.get(address + i, 0) for i in range(count)]

    async def write_register(self, address, value, *, verify=False, expect=None):
        self.writes.append((address + 1, value))  # 1-basiert wie in der Doku
        self.holding[address] = value
        return value


def sungrow(control=True, **regs) -> tuple[SungrowBattery, FakeSungrowConn]:
    drv = SungrowBattery({"host": "192.0.2.1", "allow_active_control": control, "max_power_w": 4000})
    conn = FakeSungrowConn(**regs)
    drv.conn = conn
    return drv, conn


async def test_force_charge_writes_power_and_command_before_ems_mode():
    """Erst Leistung und Befehl, dann Zwangsmodus – sonst führt der WR kurz
    den alten Befehl aus."""
    drv, conn = sungrow(cmd=CMD_DISCHARGE)
    await drv.set_mode(BatteryMode.FORCE_CHARGE, 2500)
    assert conn.writes == [(13052, 2500), (13051, CMD_CHARGE), (13050, EMS_FORCED)]


async def test_auto_resets_ems_first_then_stops_command():
    drv, conn = sungrow(ems=EMS_FORCED, cmd=CMD_CHARGE)
    await drv.set_mode(BatteryMode.AUTO)
    assert conn.writes == [(13050, EMS_SELF_CONSUMPTION), (13051, CMD_STOP)]


async def test_force_power_clamped_to_configured_maximum():
    drv, conn = sungrow()
    await drv.set_mode(BatteryMode.FORCE_DISCHARGE, 9000)
    assert (13052, 4000) in conn.writes


async def test_reserve_is_written_to_min_soc_register_13059():
    """Der eigentliche Registerfehler: Die Reserve landete in 13058 (Max-SoC)
    – 'Reserve 20 %" begrenzte damit die LADUNG auf 20 %."""
    drv, conn = sungrow()
    await drv.set_reserve_soc(20)
    assert conn.writes == [(13059, 200)]
    assert conn.holding[13057] == 1000  # Max-SoC unberührt


async def test_reserve_refused_when_register_map_looks_wrong():
    drv, conn = sungrow(max_soc=200)  # 'Max-SoC 20 %" – unplausibel
    with pytest.raises(DeviceRejected):
        await drv.set_reserve_soc(20)
    assert conn.writes == []


async def test_read_data_reports_real_mode_and_soc_limits():
    drv, _ = sungrow(ems=EMS_FORCED, cmd=CMD_CHARGE, power=3000, max_soc=950, min_soc=100)
    data = await drv.read_data()
    assert data.mode is BatteryMode.FORCE_CHARGE
    assert data.mode_known is True
    assert data.max_soc == 95.0 and data.min_soc == 10.0
    assert data.soc == pytest.approx(55.6)


async def test_writes_refused_without_active_control():
    drv, conn = sungrow(control=False)
    with pytest.raises(DeviceRejected):
        await drv.set_mode(BatteryMode.FORCE_CHARGE, 1000)
    assert conn.writes == []


# ------------------------------------------------------------ Schreibjournal


class _Result:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):  # noqa: N802 – pymodbus-API
        return self._error


class _Client:
    connected = True

    def __init__(self, readback):
        self.readback = readback

    async def connect(self):
        return True

    async def write_register(self, address, value, **_):
        return _Result()

    async def read_holding_registers(self, address, count, **_):
        return _Result([self.readback] * count)

    async def read_input_registers(self, address, count, **_):
        return _Result([0] * count)


async def test_write_journal_records_success_and_silent_rejection():
    conn = ModbusConnection(host="192.0.2.1", name="Test")
    conn.client = _Client(readback=2)
    await conn.write_register(13049, 2, verify=True)
    conn.client = _Client(readback=0)  # Gerät verwirft still
    with pytest.raises(CommandNotApplied):
        await conn.write_register(13049, 2, verify=True)

    ok, rejected = list(conn.write_log)
    assert ok["register"] == 13050 and ok["ok"] is True and ok["readback"] == 2
    assert rejected["ok"] is False and rejected["readback"] == 0 and "nicht übernommen" in rejected["error"]


# ------------------------------------------------------------ Loop: manuell


def _reset_world(**kw):
    w = SimulationWorld.instance()
    w.bat_mode = BatteryMode.AUTO
    w.bat_force_w = 0.0
    for k, v in kw.items():
        setattr(w, k, v)
    return w


async def test_manual_charge_is_applied_by_loop_with_power():
    _reset_world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    loop.start_battery_manual(BatteryMode.FORCE_CHARGE, 2000, 30, target_soc=90)

    await loop._step()

    w = SimulationWorld.instance()
    assert w.bat_mode is BatteryMode.FORCE_CHARGE
    assert w.bat_force_w == 2000
    assert loop.snapshot["battery_manual"]["mode"] == "force_charge"
    assert loop.battery_locked is True  # lädt → entlädt nicht
    # Der Snapshot zeigt schon im selben Takt den Modus NACH dem Befehl –
    # sonst meldete die Oberfläche kurz eine Abweichung, die keine ist.
    assert battery_of(loop).data.mode is BatteryMode.FORCE_CHARGE


async def test_manual_charge_ends_at_target_soc_and_returns_to_auto():
    _reset_world(bat_soc=91.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    loop.start_battery_manual(BatteryMode.FORCE_CHARGE, 2000, 30, target_soc=90)

    await loop._step()

    assert loop.battery_manual is None
    assert "Ziel-SoC" in loop.battery_manual_last["reason"]
    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO


async def test_manual_command_ends_after_its_time():
    _reset_world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    m = loop.start_battery_manual(BatteryMode.FORCE_DISCHARGE, 1500, 10, target_soc=20)
    await loop._step()
    assert SimulationWorld.instance().bat_mode is BatteryMode.FORCE_DISCHARGE

    m.until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await loop._step()

    assert loop.battery_manual is None
    assert "Zeit abgelaufen" in loop.battery_manual_last["reason"]
    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO


async def test_stop_resets_battery_from_manual_mode():
    """Fail-safe: Wer MinePower mitten im Zwangsladen beendet, darf keinen
    hängenden Wechselrichter hinterlassen."""
    _reset_world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    loop.start_battery_manual(BatteryMode.FORCE_CHARGE, 2000, 30)
    await loop._step()
    assert SimulationWorld.instance().bat_mode is BatteryMode.FORCE_CHARGE

    await loop.stop()

    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO


async def test_foreign_forced_mode_at_startup_is_reset():
    """Nach einem harten Absturz stand der WR noch im Zwangsladen."""
    _reset_world(bat_soc=40.0, bat_mode=BatteryMode.FORCE_CHARGE)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")

    await loop._step()

    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO


async def test_mode_mismatch_is_detected_and_command_resent():
    """Hersteller-App oder eigenes EMS stellt den Modus um – der Loop hält
    den Sollzustand, statt sich auf seinen Dedup zu verlassen."""
    _reset_world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    await loop._step()
    bat = battery_of(loop)
    assert bat.battery_target[0] is BatteryMode.AUTO

    SimulationWorld.instance().bat_mode = BatteryMode.FORCE_DISCHARGE  # fremder Eingriff
    bat.sent_at["battery_mode"] = time.monotonic() - 3600  # Karenzzeit vorbei
    for _ in range(3):
        await loop._step()

    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO


async def test_manual_overrides_price_automatic():
    """Manueller Befehl schlägt das Preisfenster (hier: Entladen trotz
    günstigem Preis und Batterie unter Ziel)."""
    _reset_world(bat_soc=40.0)
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    loop.start_battery_manual(BatteryMode.FORCE_DISCHARGE, 1000, 30, target_soc=20)

    await loop._step()

    assert SimulationWorld.instance().bat_mode is BatteryMode.FORCE_DISCHARGE
    assert loop.battery_grid_charging is False
    assert loop.grid_price_window is False


async def test_kick_applies_command_without_waiting_for_the_interval():
    """Klick → Gerät: bei 20-s-Takt früher bis zu 20 s, jetzt ein Bruchteil."""
    _reset_world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    loop.cfg.interval_s = 20.0
    loop._reload_requested = False
    loop.running = True
    task = asyncio.create_task(loop._run())
    try:
        await asyncio.sleep(0.3)  # erster Takt läuft, danach schläft der Loop 20 s
        started = time.monotonic()
        loop.start_battery_manual(BatteryMode.FORCE_CHARGE, 1500, 30)
        while SimulationWorld.instance().bat_mode is not BatteryMode.FORCE_CHARGE:
            assert time.monotonic() - started < 3.0, "Befehl kam nicht zeitnah an"
            await asyncio.sleep(0.02)
        latency = time.monotonic() - started
    finally:
        loop.running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert latency < 2.0


# ------------------------------------------------------------ API-Validierung


class _User:
    id = None
    role = "admin"


async def test_api_refuses_discharge_below_reserve():
    from fastapi import HTTPException

    from app.core import runtime

    _reset_world(bat_soc=60.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    await loop._step()
    runtime.loop = loop
    with pytest.raises(HTTPException) as exc:
        await battery_manual(BatteryCommand(action="discharge", target_soc=5), _User())
    assert exc.value.status_code == 422
    assert loop.battery_manual is None


async def test_api_explains_missing_write_permission():
    from fastapi import HTTPException

    from app.core import runtime

    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    await loop._step()
    runtime.loop = loop
    with pytest.raises(HTTPException) as exc:
        await battery_manual(BatteryCommand(action="charge"), _User())
    assert exc.value.status_code == 409
    assert "Aktive Batteriesteuerung erlauben" in exc.value.detail


async def test_api_charge_sets_state_and_returns_immediately():
    from app.core import runtime

    _reset_world(bat_soc=50.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    await loop._step()
    runtime.loop = loop
    started = time.monotonic()
    result = await battery_manual(BatteryCommand(action="charge", power_w=2500, minutes=45), _User())
    assert time.monotonic() - started < 0.5  # kein Modbus im Request
    assert result["manual"]["power_w"] == 2500
    assert loop.battery_manual.target_soc == 100


# ------------------------------------------------------------ Netzanschluss


@pytest.fixture
def fixed_site(monkeypatch):
    """Deterministische Anlage: keine Sonne, 500 W Hauslast."""
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: 0.0)
    monkeypatch.setattr(SimulationWorld, "house_load", lambda self: 500.0)


async def test_battery_first_consumes_grid_budget_before_wallbox(fixed_site):
    _reset_world(bat_soc=40.0, car_connected=True)
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    loop.cfg.grid_charge_max_w = 3500  # schwache Zuleitung

    await loop._step()

    bat = battery_of(loop)
    # 3500 W Grenze − ~500 W Haus → Batterie bekommt den Rest, nicht mehr
    assert bat.battery_target[0] is BatteryMode.FORCE_CHARGE
    assert bat.battery_target[1] <= 3100
    wb = wallbox_of(loop)
    assert wb.decision["enable"] is False


async def test_battery_behind_wallbox_in_chain_lets_wallbox_charge_first(fixed_site):
    """'sofern das der Priorisierung entspricht": Steht die Batterie in der
    Kette hinter dem Auto, bekommt das Auto den günstigen Netzstrom zuerst –
    auch wenn der Speicher sein Ziel noch nicht erreicht hat."""
    _reset_world(bat_soc=40.0, car_connected=True, car_soc=40.0)
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_price",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    wb, bat = wallbox_of(loop), battery_of(loop)
    loop.priority = [wb.id, bat.id]

    await loop._step()

    assert wb.decision["enable"] is True
    assert "Netzladen" in wb.decision["reason"]


async def test_grid_limit_below_minimum_only_holds_battery(fixed_site):
    _reset_world(bat_soc=40.0)
    loop = await build_loop(
        price_ct=6.0, battery_controllable=True, wallbox_mode="pv_only",
        battery_release_soc=80.0, battery_soc=40.0, battery_grid_charge_enabled=True,
    )
    loop.cfg.grid_charge_max_w = 500 + BATTERY_MIN_FORCE_W / 2  # praktisch nichts frei

    await loop._step()

    assert loop.battery_grid_charging is False
    assert "Netzanschluss ausgelastet" in loop.battery_plan.reason
