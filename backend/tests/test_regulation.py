"""Unit-Tests der Regelungslogik – komplett ohne Hardware (injizierte Uhr)."""
from datetime import datetime

import pytest

from app.core.regulation import (
    ChargeContext,
    RegulationConfig,
    WallboxController,
    WallboxSettings,
    WaterHeaterController,
    WaterHeaterSettings,
    compute_budget,
)
from app.core.schedules import window_active
from app.drivers.base import WallboxData, WallboxState, WaterHeaterData


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_wallbox(clock, **overrides) -> WallboxController:
    settings = WallboxSettings(**{"mode": "pv_only", "min_current": 6, "max_current": 16,
                                  "phases_mode": "fixed1", "start_delay_s": 60,
                                  "stop_delay_s": 180, **overrides})
    return WallboxController(settings, clock=clock)


def connected(power=0.0, soc=None) -> WallboxData:
    return WallboxData(state=WallboxState.CONNECTED, power=power, soc=soc)


def charging(power: float, soc=None) -> WallboxData:
    return WallboxData(state=WallboxState.CHARGING, power=power, soc=soc)


def ctx(**kw) -> ChargeContext:
    return ChargeContext(now=datetime(2026, 7, 3, 12, 0), **kw)


# ------------------------------------------------------------ Budget

def test_budget_outside_deadband():
    cfg = RegulationConfig(grid_target_w=0, deadband_w=100)
    # 500 W Einspeisung, 2000 W laufende steuerbare Last → 2500 W Budget
    assert compute_budget(-500, 2000, cfg) == 2500


def test_budget_inside_deadband_keeps_allocation():
    cfg = RegulationConfig(grid_target_w=0, deadband_w=100)
    # ±50 W um den Sollwert → aktuelle Verteilung beibehalten
    assert compute_budget(50, 2000, cfg) == 2000
    assert compute_budget(-50, 2000, cfg) == 2000


def test_budget_grid_import_reduces():
    cfg = RegulationConfig(grid_target_w=0, deadband_w=100)
    assert compute_budget(800, 3000, cfg) == 2200


# ------------------------------------------------------------ Start-Hysterese

def test_no_start_below_threshold():
    clock = FakeClock()
    wb = make_wallbox(clock)
    d = wb.decide(800, connected(), ctx())
    assert not d.enable


def test_start_requires_sustained_surplus():
    clock = FakeClock()
    wb = make_wallbox(clock)
    assert not wb.decide(2000, connected(), ctx()).enable  # Timer startet erst
    clock.advance(30)
    assert not wb.decide(2000, connected(), ctx()).enable  # 30 s < 60 s
    clock.advance(31)
    d = wb.decide(2000, connected(), ctx())
    assert d.enable
    # 2000 W / 230 V ≈ 8,7 A → auf ganze Ampere abgerundet: 8 A.
    # Abgerundet wird bewusst: Aufrunden würde Netzbezug erzeugen. Der Rest
    # (≈ 160 W) geht nicht verloren, sondern an eine fein modulierbare Last
    # (siehe test_gap_filler_*).
    assert d.current_a == 8
    assert d.power_w == pytest.approx(8 * 230)


def test_start_timer_resets_on_dip():
    clock = FakeClock()
    wb = make_wallbox(clock)
    wb.decide(2000, connected(), ctx())
    clock.advance(50)
    wb.decide(500, connected(), ctx())  # Einbruch → Timer-Reset
    clock.advance(15)
    assert not wb.decide(2000, connected(), ctx()).enable


# ------------------------------------------------------------ Stop-Hysterese & Min-Strom

def _started_controller(clock) -> WallboxController:
    wb = make_wallbox(clock)
    wb.decide(3000, connected(), ctx())
    clock.advance(61)
    assert wb.decide(3000, connected(), ctx()).enable
    return wb


def test_min_current_held_during_stop_delay():
    clock = FakeClock()
    wb = _started_controller(clock)
    # Budget fällt unter Minimum (6 A × 230 V = 1380 W) → Min-Strom halten
    d = wb.decide(400, charging(1380), ctx())
    assert d.enable
    assert d.current_a == 6


def test_stop_after_sustained_deficit():
    clock = FakeClock()
    wb = _started_controller(clock)
    wb.decide(400, charging(1380), ctx())
    clock.advance(181)
    d = wb.decide(400, charging(1380), ctx())
    assert not d.enable


def test_current_capped_at_max():
    clock = FakeClock()
    wb = _started_controller(clock)
    d = wb.decide(10000, charging(3600), ctx())
    assert d.current_a == pytest.approx(16, abs=0.01)


# ------------------------------------------------------------ Modi

def test_fast_mode_ignores_budget():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="fast")
    d = wb.decide(0, connected(), ctx())
    assert d.enable and d.current_a == 16


def test_min_pv_guarantees_minimum():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="min_pv", min_pv_current=6)
    d = wb.decide(0, connected(), ctx())
    assert d.enable
    assert d.current_a == 6


def test_price_mode_charges_when_cheap():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="price")
    assert wb.decide(0, connected(), ctx(price_ct=10, cheap_hour=True)).enable
    # Preis steigt über die Schwelle → Wechsel in Überschussregelung.
    # Erst greift die Stop-Verzögerung (Min-Strom-Haltung gegen Flattern) …
    d = wb.decide(0, connected(), ctx(price_ct=30))
    assert d.enable and d.current_a == 6
    # … nach Ablauf der Hysterese wird gestoppt.
    clock.advance(181)
    assert not wb.decide(0, connected(), ctx(price_ct=30)).enable


def test_price_mode_never_starts_when_expensive():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="price")
    assert not wb.decide(0, connected(), ctx(price_ct=30, cheap_hour=False)).enable


def test_target_mode_forces_grid_when_deadline_close():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="target", target_soc=80, target_time="13:00", max_current=16)
    data = charging(0, soc=20)
    data.extra["capacity_kwh"] = 60
    # 60 % von 60 kWh = 36 kWh fehlen; 16 A × 230 V = 3,68 kW → ~9,8 h nötig,
    # Deadline in 1 h → Netzladung erzwingen
    d = wb.decide(0, data, ctx())
    assert d.enable and d.current_a == 16


def test_manual_stop_override():
    clock = FakeClock()
    wb = make_wallbox(clock, mode="fast")
    wb.override = "stop"
    assert not wb.decide(5000, connected(), ctx()).enable


def test_battery_discharge_not_used_for_ev():
    clock = FakeClock()
    wb = _started_controller(clock)
    # Batterie entlädt mit 2000 W bei niedrigem SoC → Budget sinkt effektiv
    d = wb.decide(2500, charging(2500), ctx(battery_soc=25, battery_power=-2000))
    # 2500 − 2000 = 500 W < Min-Leistung → Min-Strom (Stop-Timer läuft)
    assert d.current_a == 6


# ------------------------------------------------------------ Hausanschluss-Schutz

def test_house_limit_caps_current():
    clock = FakeClock()
    wb = _started_controller(clock)
    d = wb.decide(3680, charging(3680), ctx(house_limit_a=20, house_current_a=5, other_wallbox_current_a=8))
    assert d.current_a <= 7.0


# ------------------------------------------------------------ Phasenumschaltung

def test_phase_switch_up_after_delay():
    clock = FakeClock()
    wb = make_wallbox(clock, phases_mode="auto", phase_switch_delay_s=120, phase_switch_lock_s=0)
    wb.decide(3000, connected(), ctx())
    clock.advance(61)
    assert wb.decide(3000, connected(), ctx()).enable
    assert wb.phases == 1
    # dauerhaft > 1-phasiges Maximum (16 A × 230 V = 3680 W) und ≥ 3-phasiges Minimum;
    # der Umschalt-Timer (120 s) startet beim ersten Tick mit erfüllter Bedingung
    for _ in range(4):
        clock.advance(50)
        d = wb.decide(6000, charging(3600), ctx())
    assert wb.phases == 3
    assert d.phases == 3


# ------------------------------------------------------------ Warmwasser

def test_heater_modulates_to_budget():
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", min_power_w=100, max_power_w=3000,
                                                   start_delay_s=30, stop_delay_s=60), clock=clock)
    data = WaterHeaterData(power=0, temperature_c=40)
    wh.decide(1500, data, ctx())
    clock.advance(31)
    d = wh.decide(1500, data, ctx())
    assert d.power_w == 1500


def test_heater_stops_at_target_temperature():
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", target_temp_c=60), clock=clock)
    d = wh.decide(3000, WaterHeaterData(power=1000, temperature_c=61), ctx())
    assert d.power_w == 0


def test_heater_boost_overrides():
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", max_power_w=3000, boost_temp_c=65), clock=clock)
    wh.boost = True
    d = wh.decide(0, WaterHeaterData(power=0, temperature_c=40), ctx())
    assert d.power_w == 3000


# ------------------------------------------------------------ Zeitfenster

def test_window_same_day():
    now = datetime(2026, 7, 3, 23, 0)  # Freitag
    assert window_active(127, "22:00", "23:30", now)
    assert not window_active(127, "10:00", "12:00", now)


def test_window_over_midnight():
    friday_night = datetime(2026, 7, 3, 23, 30)
    saturday_early = datetime(2026, 7, 4, 5, 0)
    friday_bit = 1 << 4  # Freitag
    assert window_active(friday_bit, "22:00", "06:00", friday_night)
    assert window_active(friday_bit, "22:00", "06:00", saturday_early)  # zählt zum Starttag Freitag
    assert not window_active(friday_bit, "22:00", "06:00", datetime(2026, 7, 4, 23, 30))
