"""Tests der Mechanismen gegen verschenkten Solarstrom.

Deckt ab: Quantisierung der Wallbox-Stellgröße, Lückenfüller an fein
modulierbare Lasten, adaptives Totband, aktive Nutzung der Solarprognose und
die Kennzahl „verschenkte Energie“. Alles ohne Hardware und ohne Datenbank.
"""
from datetime import datetime, timedelta

import pytest

from app.core.loop import ControlLoop
from app.core.regulation import (
    ChargeContext,
    RegulationConfig,
    VolatilityTracker,
    WallboxController,
    WallboxSettings,
    WaterHeaterController,
    WaterHeaterDecision,
    WaterHeaterSettings,
    compute_budget,
    protected_battery_discharge,
    reserved_battery_charge,
    wasted_power,
)
from app.drivers.base import WallboxData, WallboxState, WaterHeaterData


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def ctx(**kw) -> ChargeContext:
    return ChargeContext(now=datetime(2026, 7, 3, 12, 0), **kw)


def charging(power: float, soc=None) -> WallboxData:
    return WallboxData(state=WallboxState.CHARGING, power=power, soc=soc)


def connected(power=0.0, soc=None) -> WallboxData:
    return WallboxData(state=WallboxState.CONNECTED, power=power, soc=soc)


def running_wallbox(clock, **overrides) -> WallboxController:
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16,
                        phases_mode="fixed1", start_delay_s=60, stop_delay_s=180, **overrides),
        clock=clock,
    )
    wb.decide(3000, connected(), ctx())
    clock.advance(61)
    assert wb.decide(3000, connected(), ctx()).enable
    return wb


def running_heater(clock, **overrides) -> tuple[WaterHeaterController, WaterHeaterData]:
    wh = WaterHeaterController(
        WaterHeaterSettings(mode="pv_only", min_power_w=100, max_power_w=3000,
                            start_delay_s=30, stop_delay_s=60, **overrides),
        clock=clock,
    )
    data = WaterHeaterData(power=0, temperature_c=40)
    wh.decide(1000, data, ctx())
    clock.advance(31)
    assert wh.decide(1000, data, ctx()).power_w > 0
    return wh, data


# ------------------------------------------------------------ Quantisierung

def test_current_is_rounded_down_to_whole_amps():
    """Aufrunden würde mehr anfordern, als PV liefert → Netzbezug."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(2290, charging(2000), ctx())  # 2290 W / 230 V = 9,96 A
    assert d.current_a == 9
    assert d.power_w == pytest.approx(9 * 230)


def test_decision_reports_quantized_power_not_wish():
    """Der Loop verteilt anhand von power_w weiter – der Wert muss die
    tatsächlich abgenommene Leistung sein, nicht den Wunsch."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(2290, charging(2000), ctx())
    assert d.power_w < 2290  # der Rest steht dem Lückenfüller zur Verfügung
    assert 2290 - d.power_w == pytest.approx(2290 - 2070)


def test_nearest_rounding_is_opt_in():
    clock = FakeClock()
    wb = running_wallbox(clock, current_rounding="nearest")
    d = wb.decide(2290, charging(2000), ctx())
    assert d.current_a == 10


def test_quantization_never_falls_below_minimum():
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(1400, charging(1380), ctx())  # 6,08 A → 6 A, nicht 5 A
    assert d.current_a == 6


# ------------------------------------------------------------ Lückenfüller

def test_gap_filler_absorbs_leftover():
    """Der klassische Fall: 200 W Rest, für die Wallbox zu wenig, für den
    modulierenden Heizstab genau richtig."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    decision = WaterHeaterDecision(power_w=1000, reason="PV-Überschuss 1000 W")
    improved = wh.absorb(decision, 200, data)
    assert improved.power_w == 1200
    assert "Lückenfüller" in improved.reason


def test_gap_filler_respects_maximum():
    clock = FakeClock()
    wh, data = running_heater(clock)
    decision = WaterHeaterDecision(power_w=2900, reason="x")
    improved = wh.absorb(decision, 500, data)
    assert improved.power_w == 3000  # max_power_w, nicht 3400


def test_gap_filler_ignores_relay_heaters():
    """Ein Relais kann nur ganz an oder ganz aus – es taugt nicht als
    Lückenfüller und darf keinen Rest zugeteilt bekommen."""
    clock = FakeClock()
    wh = WaterHeaterController(
        WaterHeaterSettings(mode="pv_only", modulating=False, max_power_w=3000), clock=clock
    )
    wh.on = True
    data = WaterHeaterData(power=3000, temperature_c=40)
    assert wh.fine_modulating is False
    assert wh.absorb(WaterHeaterDecision(3000, "x"), 200, data).power_w == 3000


def test_gap_filler_stops_at_target_temperature():
    clock = FakeClock()
    wh, _ = running_heater(clock, target_temp_c=60)
    hot = WaterHeaterData(power=1000, temperature_c=61)
    assert wh.absorb(WaterHeaterDecision(1000, "x"), 500, hot).power_w == 1000


def test_gap_filler_only_increases():
    """Absenken bleibt der regulären Entscheidung vorbehalten – sonst würde
    der Lückenfüller die Hysterese unterlaufen."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    assert wh.absorb(WaterHeaterDecision(1500, "x"), -400, data).power_w == 1500


def test_gap_filler_inactive_when_heater_is_off():
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only"), clock=clock)
    data = WaterHeaterData(power=0, temperature_c=40)
    assert wh.headroom(WaterHeaterDecision(0, "aus"), data) == 0


# ------------------------------------------------------------ Adaptives Totband

def test_deadband_tightens_when_weather_is_stable():
    cfg = RegulationConfig(deadband_w=100, adaptive_deadband=True)
    v = VolatilityTracker()
    for _ in range(10):
        v.add(5000.0)  # völlig konstante Erzeugung
    assert v.deadband(cfg) < cfg.deadband_w
    assert v.describe() == "stabil"


def test_deadband_widens_on_cloud_swings():
    cfg = RegulationConfig(deadband_w=100, adaptive_deadband=True)
    v = VolatilityTracker()
    for i in range(10):
        v.add(5000.0 if i % 2 else 1500.0)  # harte Sprünge
    assert v.deadband(cfg) > cfg.deadband_w
    assert v.describe() == "starker Wolkenwechsel"


def test_deadband_stays_within_limits():
    cfg = RegulationConfig(deadband_w=100, deadband_min_w=40, deadband_max_w=250)
    v = VolatilityTracker()
    for i in range(10):
        v.add(20000.0 if i % 2 else 0.0)
    assert v.deadband(cfg) == 250


def test_adaptive_deadband_can_be_switched_off():
    cfg = RegulationConfig(deadband_w=100, adaptive_deadband=False)
    v = VolatilityTracker()
    for i in range(10):
        v.add(9000.0 if i % 2 else 0.0)
    assert v.deadband(cfg) == 100


def test_budget_uses_supplied_deadband():
    cfg = RegulationConfig(grid_target_w=0, deadband_w=100)
    # 60 W Abweichung: im Standard-Totband (100) ignoriert …
    assert compute_budget(60, 2000, cfg) == 2000
    # … mit engerem adaptivem Totband (40) wird nachgeregelt.
    assert compute_budget(60, 2000, cfg, deadband_w=40) == 1940


# ------------------------------------------------------------ Verschenkte Energie

def test_waste_is_export_above_target():
    cfg = RegulationConfig(grid_target_w=0)
    assert wasted_power(-500, cfg) == 500   # 500 W Einspeisung
    assert wasted_power(300, cfg) == 0      # Netzbezug ist kein Verschenken
    assert wasted_power(0, cfg) == 0


def test_waste_respects_intentional_export_target():
    """Bei gewollter Mindest-Einspeisung zählt nur, was darüber hinausgeht."""
    cfg = RegulationConfig(grid_target_w=-300)
    assert wasted_power(-500, cfg) == 200
    assert wasted_power(-300, cfg) == 0
    assert wasted_power(-100, cfg) == 0


# ------------------------------------------------------------ Prognose

def test_forecast_holds_charge_through_cloud():
    """Wolkendurchzug: Ladung wird gehalten statt abgeschaltet."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    clock.advance(181)  # weit über der Stopp-Verzögerung
    d = wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    assert d.enable
    assert d.current_a == 6
    assert "Wolkendurchzug" in d.reason


def test_forecast_hold_is_time_limited():
    """Eine dauerhaft falsche Prognose darf nicht stundenlang Netzstrom ziehen."""
    clock = FakeClock()
    wb = running_wallbox(clock, forecast_hold_max_s=300)
    wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    clock.advance(400)  # Halte-Fenster abgelaufen
    wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    clock.advance(181)  # danach greift die normale Stopp-Hysterese
    assert not wb.decide(300, charging(1380), ctx(solar_recovery_expected=True)).enable


def test_forecast_hold_can_be_disabled():
    clock = FakeClock()
    wb = running_wallbox(clock, use_forecast=False)
    wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    clock.advance(181)
    assert not wb.decide(300, charging(1380), ctx(solar_recovery_expected=True)).enable


def test_heater_holds_through_cloud():
    clock = FakeClock()
    wh, data = running_heater(clock)
    wh.decide(50, data, ctx(solar_recovery_expected=True))
    clock.advance(61)
    d = wh.decide(50, data, ctx(solar_recovery_expected=True))
    assert d.power_w == 100  # Mindeststufe gehalten
    assert "Wolkendurchzug" in d.reason


def _target_controller(clock) -> tuple[WallboxController, WallboxData]:
    """1-phasige 3,7-kW-Ladung, 30 kWh fehlen, Deadline 23:00 (jetzt 12:00).

    Reine Zeitrechnung: 30 kWh / 3,68 kW = 8,15 h nötig, 11 h übrig → warten.
    """
    wb = WallboxController(
        WallboxSettings(mode="target", target_soc=80, target_time="23:00",
                        min_current=6, max_current=16, phases_mode="fixed1"),
        clock=clock,
    )
    data = charging(0, soc=30)
    data.extra["capacity_kwh"] = 60  # 50 % von 60 kWh = 30 kWh fehlen
    return wb, data


def test_target_mode_waits_when_forecast_is_good():
    """Die Sonne deckt den Bedarf → jede gewartete Stunde bringt Solarstrom."""
    clock = FakeClock()
    wb, data = _target_controller(clock)
    assert not wb.decide(0, data, ctx(forecast_surplus_kwh=35)).enable


def test_target_mode_starts_early_when_forecast_is_poor():
    """Prognose: kaum Sonne → Netzladung beginnt vorausschauend früher,
    statt bis kurz vor der Deadline zu warten."""
    clock = FakeClock()
    wb, data = _target_controller(clock)
    d = wb.decide(0, data, ctx(forecast_surplus_kwh=1))
    assert d.enable
    assert "Zielladung" in d.reason


def test_target_mode_without_forecast_behaves_as_before():
    clock = FakeClock()
    wb, data = _target_controller(clock)
    assert not wb.decide(0, data, ctx(forecast_surplus_kwh=None)).enable


def test_forecast_never_overrides_manual_stop():
    """Sicherheitsrelevant: Kein Prognose-Automatismus hebelt einen
    manuellen Stopp aus."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    wb.override = "stop"
    assert not wb.decide(5000, charging(3000), ctx(solar_recovery_expected=True)).enable


def test_forecast_does_not_start_a_stopped_load():
    """Die Prognose hält nur eine laufende Last – sie startet nie eine."""
    clock = FakeClock()
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16, phases_mode="fixed1"),
        clock=clock,
    )
    clock.advance(300)
    assert not wb.decide(200, connected(), ctx(solar_recovery_expected=True)).enable


# ------------------------------------------------------------ Loop-Verteilung
# Der Lückenfüller im Control-Loop – ohne Datenbank und ohne Geräte.

class FakeDevice:
    """Minimaler Ersatz für ManagedDevice (nur was _fill_gaps anfasst)."""

    def __init__(self, name, controller, data, online=True, enabled=True):
        self.name = name
        self.controller = controller
        self.data = data
        self.online = online
        self.enabled = enabled


def _loop(**cfg_overrides) -> ControlLoop:
    loop = ControlLoop()
    loop.cfg = RegulationConfig(**cfg_overrides)
    return loop


def test_loop_hands_leftover_to_modulating_load():
    """Kernanforderung: Was für die nächste grob gestufte Last zu klein ist,
    geht an die fein modulierbare – nicht ins Netz."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    dev = FakeDevice("Warmwasser", wh, data)
    decision = WaterHeaterDecision(power_w=800, reason="PV-Überschuss 800 W")

    fine = [(dev, decision, False)]
    pool_pv, pool_bat, filled = _loop()._fill_gaps(fine, pool_pv=180.0, pool_bat=0.0)

    assert filled == 180.0          # kompletter Rest verwertet
    assert pool_pv == 0.0           # nichts bleibt fürs Netz übrig
    assert fine[0][1].power_w == 980.0


def test_loop_skips_gap_filling_below_threshold():
    """Unter der Bagatellgrenze lohnt kein Nachstellen (Gerätelebensdauer)."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    fine = [(FakeDevice("Warmwasser", wh, data), WaterHeaterDecision(800, "x"), False)]
    pool_pv, _, filled = _loop(gap_fill_min_w=25)._fill_gaps(fine, pool_pv=10.0, pool_bat=0.0)
    assert filled == 0.0
    assert pool_pv == 10.0


def test_loop_gap_filling_stops_at_device_maximum():
    clock = FakeClock()
    wh, data = running_heater(clock)  # max 3000 W
    fine = [(FakeDevice("Warmwasser", wh, data), WaterHeaterDecision(2800, "x"), False)]
    pool_pv, _, filled = _loop()._fill_gaps(fine, pool_pv=900.0, pool_bat=0.0)
    assert filled == 200.0
    assert pool_pv == 700.0  # Rest bleibt liegen – dafür gibt es keinen Abnehmer


def test_loop_battery_pool_only_for_loads_above_battery():
    """Die Batterie-Ladeleistung steht nur Lasten VOR der Batterie zu –
    auch der Lückenfüller darf diese Regel nicht aushebeln."""
    clock = FakeClock()
    wh_below, data_below = running_heater(clock)
    below = [(FakeDevice("Warmwasser", wh_below, data_below), WaterHeaterDecision(500, "x"), False)]
    _, pool_bat, filled_below = _loop()._fill_gaps(below, pool_pv=0.0, pool_bat=1000.0)
    assert filled_below == 0.0      # unterhalb der Batterie: kein Zugriff
    assert pool_bat == 1000.0

    clock2 = FakeClock()
    wh_above, data_above = running_heater(clock2)
    above = [(FakeDevice("Warmwasser", wh_above, data_above), WaterHeaterDecision(500, "x"), True)]
    _, pool_bat_after, filled_above = _loop()._fill_gaps(above, pool_pv=0.0, pool_bat=1000.0)
    assert filled_above == 1000.0   # oberhalb: darf die Ladeleistung mitnutzen
    assert pool_bat_after == 0.0


def test_waste_reason_names_the_blocker():
    reason = ControlLoop._waste_reason(
        800.0, ["Wallbox: warte auf Überschuss (800 W)"], [FakeDevice("Wallbox", None, None)]
    )
    assert "warte auf Überschuss" in reason


def test_waste_reason_when_nothing_is_configured():
    assert "Keine steuerbare Last" in ControlLoop._waste_reason(800.0, [], [])


def test_waste_reason_when_everything_runs_at_maximum():
    reason = ControlLoop._waste_reason(800.0, [], [FakeDevice("Warmwasser", None, None)])
    assert "Maximum" in reason


def test_no_waste_reason_for_negligible_amounts():
    assert ControlLoop._waste_reason(10.0, [], []) is None


# ------------------------------------------------------------ Batterie schonen
# Praxisfall aus einem Diagnosebericht: Das Auto lud, aber die Hausbatterie
# entlud sich dabei. Ursache war, dass `ev_lock_soc` zwar konfigurierbar war,
# aber von keiner Entscheidung gelesen wurde – und dass der Schutz nur in der
# Wallbox griff, nicht im gemeinsamen Überschuss-Topf.

def test_discharge_is_not_surplus():
    """Was aus dem Speicher kommt, ist kein Solarüberschuss."""
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(80, -2000, cfg) == 2000


def test_charging_battery_is_not_protected():
    """Nur Entladung wird geschützt – Ladung darf die Kette weiterreichen."""
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(80, +2000, cfg) == 0


def test_idle_battery_ignores_measurement_noise():
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(80, -30, cfg) == 0


def test_full_battery_may_be_used_when_user_allows_it():
    """Ab dem eingestellten Ladestand gibt der Nutzer die Batterie frei."""
    cfg = RegulationConfig(ev_lock_soc=80)
    assert protected_battery_discharge(85, -2000, cfg) == 0
    assert protected_battery_discharge(79, -2000, cfg) == 2000


def test_lock_at_100_never_releases_the_battery():
    """Die Einstellung, die der Nutzer erwartet: niemals aus dem Speicher laden."""
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(99, -3000, cfg) == 3000


def test_controller_does_not_subtract_twice():
    """Hat der Loop die Entladung schon abgezogen, darf der Regler es nicht
    noch einmal tun – sonst fehlt die Leistung doppelt im Budget."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(2500, charging(2500),
                  ctx(battery_soc=50, battery_power=-2000, battery_handled_globally=True))
    assert d.current_a == 10  # 2500 W / 230 V, abgerundet – kein zweiter Abzug


def test_battery_protection_defeats_the_forecast_hold():
    """Eine Ladung „über den Wolkendurchzug retten" wäre hier falsch: Der
    fehlende Überschuss käme aus dem Speicher, nicht aus der Sonne."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    context = dict(solar_recovery_expected=True, battery_protected=True,
                   battery_handled_globally=True)
    wb.decide(300, charging(1380), ctx(**context))
    clock.advance(181)
    assert not wb.decide(300, charging(1380), ctx(**context)).enable


def test_forecast_hold_still_works_without_battery_drain():
    """Gegenprobe: Ohne Batterie-Schutz greift die Halte-Logik weiterhin."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    wb.decide(300, charging(1380), ctx(solar_recovery_expected=True))
    clock.advance(181)
    assert wb.decide(300, charging(1380), ctx(solar_recovery_expected=True)).enable


# ------------------------------------------------- Batterie hat Vorrang
# Zweiter Praxisfall: Das Auto stand auf 16 A, während der Speicher nur den
# Rest bekam. Ursache: Es gab bislang nur den Weg, der Batterie Ladeleistung
# wegzunehmen – aber keinen, ihr Vorrang zu geben.

def test_charging_battery_is_reserved_below_reserve_soc():
    cfg = RegulationConfig(battery_reserve_soc=80)
    assert reserved_battery_charge(45, 4000, cfg) == 4000


def test_full_enough_battery_does_not_block_the_car():
    """Ein fast voller Speicher soll das Auto nicht ausbremsen."""
    cfg = RegulationConfig(battery_reserve_soc=80)
    assert reserved_battery_charge(85, 4000, cfg) == 0


def test_discharging_battery_is_not_a_charge_claim():
    cfg = RegulationConfig(battery_reserve_soc=80)
    assert reserved_battery_charge(45, -2000, cfg) == 0


def test_default_reserve_keeps_previous_behaviour():
    """Standard 20 %: Das Verhalten ändert sich nur, wenn der Nutzer den
    Wert bewusst hochsetzt."""
    cfg = RegulationConfig()
    assert reserved_battery_charge(55, 4000, cfg) == 0


def test_lock_at_100_also_holds_at_exactly_full():
    """Der gemeldete Fall: Ein voller Speicher meldet über längere Zeit exakt
    100,0 %. Genau dann darf die Sperre nicht aufgehen – sonst zieht das Auto
    den Speicher leer, während die Einstellung „niemals" verspricht."""
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(100.0, -2000, cfg) == 2000
    assert protected_battery_discharge(99.9, -2000, cfg) == 2000


def test_partial_release_still_works_at_its_threshold():
    """Gegenprobe: Unter 100 bleibt die Freigabe ab dem Schwellwert erhalten."""
    cfg = RegulationConfig(ev_lock_soc=90)
    assert protected_battery_discharge(90.0, -2000, cfg) == 0
    assert protected_battery_discharge(89.9, -2000, cfg) == 2000


def test_missing_soc_is_treated_conservatively():
    """Ohne SoC-Wert wird geschützt statt freigegeben."""
    cfg = RegulationConfig(ev_lock_soc=50)
    assert protected_battery_discharge(None, -2000, cfg) == 2000
