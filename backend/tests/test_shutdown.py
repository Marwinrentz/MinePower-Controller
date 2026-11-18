"""Beenden des Control-Loops: sicherer Zustand vor allem anderen.

Docker gibt 30 s bis zum harten Abbruch. Hängt die Datenbank (Unraid stoppt
sie oft zuerst), darf das Wegschreiben der Messwerte den Fail-safe nicht
aufhalten – sonst bleibt der Speicher gesperrt oder im Zwangsladen.
"""
import asyncio
import time

from app.core.loop import WallboxController
from app.drivers.base import BatteryMode
from app.drivers.simulation import SimulationWorld
from app.services import measurements

from .test_price_window_integration import build_loop, wallbox_of


def _world(**kw):
    w = SimulationWorld.instance()
    w.bat_mode = BatteryMode.AUTO
    w.bat_force_w = 0.0
    for k, v in kw.items():
        setattr(w, k, v)
    return w


async def test_battery_released_before_measurements_with_hanging_db(monkeypatch):
    _world(bat_soc=40.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=True, wallbox_mode="pv_only")
    loop.start_battery_manual(BatteryMode.FORCE_CHARGE, 2000, 30)
    await loop._step()
    assert SimulationWorld.instance().bat_mode is BatteryMode.FORCE_CHARGE

    released_at: dict[str, float] = {}
    orig = loop._release_battery

    async def spy(dev, reason):
        await orig(dev, reason)
        released_at["t"] = time.monotonic()

    loop._release_battery = spy

    async def hang(rows):  # Datenbank antwortet nicht
        await asyncio.sleep(3600)

    monkeypatch.setattr(measurements, "_write", hang)
    monkeypatch.setattr("app.core.loop.SHUTDOWN_FLUSH_TIMEOUT_S", 0.5)
    loop.buffer.add("site", "pv_power", 1.0)

    t0 = time.monotonic()
    await loop.stop()

    assert SimulationWorld.instance().bat_mode is BatteryMode.AUTO
    assert released_at["t"] - t0 < 2.0, "Fail-safe kam erst nach dem Messwert-Flush"
    assert time.monotonic() - t0 < 5.0


async def test_pv_charging_wallbox_is_stopped_on_exit():
    w = _world(bat_soc=40.0, car_connected=True)
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    wb = wallbox_of(loop)
    wb.online = True
    await wb.driver.start_charging()
    wb.sent["enable"] = True
    assert w.wb_enabled is True

    await loop.stop()

    assert w.wb_enabled is False


async def test_fast_charging_wallbox_keeps_running_on_exit():
    w = _world(bat_soc=40.0, car_connected=True)
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    wb = wallbox_of(loop)
    wb.online = True
    assert isinstance(wb.controller, WallboxController)
    wb.controller.override = "fast"
    await wb.driver.start_charging()
    wb.sent["enable"] = True

    await loop.stop()

    assert w.wb_enabled is True, "'Sofort laden' hat der Nutzer verlangt – läuft weiter"


async def test_wallbox_not_started_by_minepower_is_left_alone():
    w = _world(bat_soc=40.0, car_connected=True)
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    wb = wallbox_of(loop)
    wb.online = True
    await wb.driver.start_charging()  # von Hand / Hersteller-App
    wb.sent.pop("enable", None)

    await loop.stop()

    assert w.wb_enabled is True


async def test_stale_meter_value_triggers_safety_stop():
    """Hängender Zähler: Der letzte Wert gilt nur GRID_MAX_AGE_S lang."""
    from app.core import loop as L

    _world(bat_soc=40.0, car_connected=True)
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    await loop._step()
    meter = next(d for d in loop.devices.values() if d.category == "meter")
    assert loop.snapshot.get("safety") in (None, "")

    async def hang():
        await asyncio.sleep(3600)

    meter.driver.read_data = hang
    meter.data_at = time.monotonic() - L.GRID_MAX_AGE_S - 5
    await loop._step()
    assert "Netzmessung" in (loop.snapshot.get("safety") or "")
    assert any("veraltet" in w for w in loop.warnings)
    loop._cancel_read(meter.id)
