"""Warmwasser-Boost: einstellbare Abschaltbedingung, sicheres Ende ohne
Temperaturfühler, Verhalten gegenüber Batterie-Vorrang und Netzanschluss,
Neustart."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.regulation import (
    BOOST_TEMP_MAX_C,
    ChargeContext,
    WaterHeaterController,
    WaterHeaterSettings,
)
from app.drivers.base import WaterHeaterData

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def heater(**kw) -> WaterHeaterController:
    s = WaterHeaterSettings.from_dict({"mode": "pv_only", "max_power_w": 3000, "min_power_w": 100, **kw})
    wh = WaterHeaterController(s)
    wh.start_boost(now=T0)
    return wh


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# ------------------------------------------------------------ Abschaltmodi


def test_time_mode_ends_after_duration_even_below_target_temp():
    wh = heater(boost_end_mode="time", boost_duration_min=30, boost_temp_c=65)
    assert wh.boost_end_reason(40.0, now=at(29)) is None
    assert "Zeit abgelaufen" in wh.boost_end_reason(40.0, now=at(30))


def test_time_mode_ignores_target_temperature():
    wh = heater(boost_end_mode="time", boost_duration_min=30, boost_temp_c=65)
    assert wh.boost_end_reason(70.0, now=at(5)) is None


@pytest.mark.parametrize("mode", ["time", "temp", "both"])
def test_safety_temperature_ends_boost_in_every_mode(mode):
    """Auch 'nur nach Zeit" darf den Speicher nicht über 80 °C treiben."""
    wh = heater(boost_end_mode=mode, boost_duration_min=120, boost_temp_c=75)
    assert "Sicherheitstemperatur" in wh.boost_end_reason(BOOST_TEMP_MAX_C, now=at(5))


def test_temp_mode_ends_at_target_temperature():
    wh = heater(boost_end_mode="temp", boost_temp_c=65)
    assert wh.boost_end_reason(64.9, now=at(90)) is None
    assert "Temperatur" in wh.boost_end_reason(65.0, now=at(90))


def test_temp_mode_has_hard_safety_cap():
    wh = heater(boost_end_mode="temp", boost_temp_c=65, boost_max_min=120)
    assert wh.boost_end_reason(50.0, now=at(119)) is None
    assert "Sicherheitsgrenze" in wh.boost_end_reason(50.0, now=at(120))


def test_temp_mode_without_valid_sensor_falls_back_to_duration():
    """Nie endlos heizen: Ohne gültigen Messwert gilt die normale Dauer."""
    wh = heater(boost_end_mode="temp", boost_duration_min=45, boost_temp_c=65, boost_max_min=240)
    assert wh.boost_end_reason(None, now=at(44)) is None
    reason = wh.boost_end_reason(None, now=at(45))
    assert reason and "kein gültiger Temperaturwert" in reason


@pytest.mark.parametrize("bogus", [float("nan"), -40.0, 250.0])
def test_implausible_sensor_values_count_as_missing(bogus):
    wh = heater(boost_end_mode="temp", boost_duration_min=45, boost_temp_c=65)
    # Ein Fantasiewert von 250 °C darf den Boost nicht als 'erreicht" beenden
    # – und einer von −40 °C ihn nicht endlos laufen lassen.
    assert wh.boost_end_reason(bogus, now=at(10)) is None
    assert wh.boost_end_reason(bogus, now=at(45)) is not None


@pytest.mark.parametrize("temp,minute,expected", [
    (65.0, 10, "Temperatur"),     # Temperatur zuerst
    (50.0, 60, "Zeit"),           # Zeit zuerst
])
def test_both_mode_whichever_comes_first(temp, minute, expected):
    wh = heater(boost_end_mode="both", boost_duration_min=60, boost_temp_c=65)
    assert expected in wh.boost_end_reason(temp, now=at(minute))


def test_settings_are_clamped_to_safe_limits():
    s = WaterHeaterSettings.from_dict({"boost_temp_c": 95, "boost_duration_min": 1, "boost_end_mode": "x"})
    assert s.boost_temp_c == BOOST_TEMP_MAX_C
    assert s.boost_duration_min >= 5
    assert s.boost_end_mode == "both"


def test_boost_end_reason_is_reported_once():
    wh = heater(boost_end_mode="time", boost_duration_min=30)
    wh.stop_boost("Zeit abgelaufen (30 min)")
    assert wh.pop_boost_end() == "Zeit abgelaufen (30 min)"
    assert wh.pop_boost_end() is None


def test_boost_state_reports_remaining_time():
    wh = heater(boost_end_mode="time", boost_duration_min=30)
    state = wh.boost_state(now=at(10))
    assert state["remaining_s"] == 20 * 60
    assert state["end_mode"] == "time"


# ------------------------------------------------------------ Vorrang/Konflikte


def test_boost_is_not_blocked_by_battery_priority():
    """Boost = ausdrücklicher Wunsch: Ein Speicher, der gerade noch Vorrang
    hat, blockiert ihn nicht (er wird stattdessen gegen Entladen gesichert)."""
    wh = heater(boost_end_mode="time", boost_duration_min=60)
    wh.boost_until_utc = datetime.now(timezone.utc) + timedelta(minutes=30)
    ctx = ChargeContext(battery_soc=30.0)
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=45), ctx)
    assert d.power_w == 3000
    assert "Boost" in d.reason


def test_boost_respects_grid_connection_budget():
    wh = heater(boost_end_mode="time", boost_duration_min=60)
    wh.boost_until_utc = datetime.now(timezone.utc) + timedelta(minutes=30)
    d = wh.decide(0.0, WaterHeaterData(power=0, temperature_c=45), ChargeContext(grid_budget_w=1200))
    assert d.power_w == 1200
    assert "Netzanschluss-Limit" in d.reason


def test_off_mode_ends_boost_with_reason():
    wh = heater()
    wh.s.mode = "off"
    wh.decide(0.0, WaterHeaterData(power=0, temperature_c=45), ChargeContext())
    assert wh.boost is False
    assert wh.pop_boost_end() == "Gerät deaktiviert"


# ------------------------------------------------------------ Loop: Neustart


async def test_boost_survives_restart_until_its_end_time():
    from app.core.loop import ControlLoop

    from .test_price_window_integration import build_loop

    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    # Heizstab zur Laufzeit ergänzen, wie ihn der Nutzer anlegen würde
    from app.db import async_session
    from app.models import Device

    async with async_session() as session:
        session.add(Device(name="Heizstab", category="water_heater", driver_id="sim_water_heater",
                           config={"rated_power": 3000},
                           settings={"mode": "pv_only", "boost_end_mode": "time", "boost_duration_min": 60}))
        await session.commit()
    await loop._reload()
    wh = next(d for d in loop.devices.values() if d.category == "water_heater")
    wh.controller.boost = True
    until = wh.controller.boost_until_utc
    await loop.save_runtime()

    restarted = ControlLoop()
    await restarted._reload()
    wh2 = next(d for d in restarted.devices.values() if d.category == "water_heater")
    assert wh2.controller.boost is True
    assert wh2.controller.boost_until_utc == until


async def test_expired_boost_is_dropped_on_restart():
    from app.core.loop import ControlLoop

    loop = ControlLoop()
    await loop._reload()
    wh = next((d for d in loop.devices.values() if d.category == "water_heater"), None)
    if wh is None:
        pytest.skip("kein Heizstab in der Test-DB")
    restarted = ControlLoop()
    restarted._runtime_restored = True
    await restarted._reload()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    restarted._restore_runtime({"boost": {str(wh.id): {"started": past, "until": past}}})
    assert restarted.devices[wh.id].controller.boost is False


async def test_settings_change_keeps_running_boost():
    """Vorher beendete jede Geräteänderung einen laufenden Boost still."""
    from app.core.loop import ControlLoop, ManagedDevice
    from app.models import Device

    row = Device(id=99, name="Heizstab", category="water_heater", driver_id="sim_water_heater",
                 config={"rated_power": 3000}, settings={"mode": "pv_only"}, enabled=True)
    old = ManagedDevice(row)
    old.controller.boost = True
    row.settings = {"mode": "pv_only", "target_temp_c": 55}
    new = ManagedDevice(row)
    ControlLoop._carry_over(old, new)
    assert new.controller.boost is True
    assert new.controller.boost_until_utc == old.controller.boost_until_utc
