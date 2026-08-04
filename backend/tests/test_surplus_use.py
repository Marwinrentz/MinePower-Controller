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

    fine = [(dev, decision)]
    pool, filled = _loop()._fill_gaps(fine, 180.0)

    assert filled == 180.0          # kompletter Rest verwertet
    assert pool == 0.0              # nichts bleibt fürs Netz übrig
    assert fine[0][1].power_w == 980.0


def test_loop_skips_gap_filling_below_threshold():
    """Unter der Bagatellgrenze lohnt kein Nachstellen (Gerätelebensdauer)."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    fine = [(FakeDevice("Warmwasser", wh, data), WaterHeaterDecision(800, "x"))]
    pool, filled = _loop(gap_fill_min_w=25)._fill_gaps(fine, 10.0)
    assert filled == 0.0
    assert pool == 10.0


def test_loop_gap_filling_stops_at_device_maximum():
    clock = FakeClock()
    wh, data = running_heater(clock)  # max 3000 W
    fine = [(FakeDevice("Warmwasser", wh, data), WaterHeaterDecision(2800, "x"))]
    pool, filled = _loop()._fill_gaps(fine, 900.0)
    assert filled == 200.0
    assert pool == 700.0  # Rest bleibt liegen – dafür gibt es keinen Abnehmer


def test_loop_gap_filler_never_taps_the_battery():
    """Früher durfte der Lückenfüller zusätzlich die Batterie-Ladeleistung
    verwenden, solange die Last in der Kette vor der Batterie stand. Diese
    Sonderregel gibt es nicht mehr: Was der Speicher lädt, gehört dem
    Speicher und wurde vorher global abgezogen – im Topf taucht es gar
    nicht erst auf."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    fine = [(FakeDevice("Warmwasser", wh, data), WaterHeaterDecision(500, "x"))]
    pool, filled = _loop()._fill_gaps(fine, 0.0)
    assert filled == 0.0
    assert pool == 0.0


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
    """Nur mit abgeschaltetem Batterie-Vorrang: Ab dem eingestellten
    Ladestand gibt der Nutzer die Batterie frei."""
    cfg = RegulationConfig(battery_priority=False, ev_lock_soc=80)
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
    """Nur mit abgeschaltetem Batterie-Vorrang: Ein fast voller Speicher
    soll das Auto nicht ausbremsen."""
    cfg = RegulationConfig(battery_priority=False, battery_reserve_soc=80)
    assert reserved_battery_charge(85, 4000, cfg) == 0


def test_discharging_battery_is_not_a_charge_claim():
    cfg = RegulationConfig(battery_reserve_soc=80)
    assert reserved_battery_charge(45, -2000, cfg) == 0


def test_reserve_threshold_applies_only_without_priority():
    """Ohne Batterie-Vorrang gilt wieder die alte Schwelle: Oberhalb von
    20 % darf die Ladeleistung an die Lasten weitergereicht werden."""
    cfg = RegulationConfig(battery_priority=False)
    assert reserved_battery_charge(55, 4000, cfg) == 0


def test_lock_at_100_also_holds_at_exactly_full():
    """Der gemeldete Fall: Ein voller Speicher meldet über längere Zeit exakt
    100,0 %. Genau dann darf die Sperre nicht aufgehen – sonst zieht das Auto
    den Speicher leer, während die Einstellung „niemals" verspricht."""
    cfg = RegulationConfig(ev_lock_soc=100)
    assert protected_battery_discharge(100.0, -2000, cfg) == 2000
    assert protected_battery_discharge(99.9, -2000, cfg) == 2000


def test_partial_release_still_works_at_its_threshold():
    """Gegenprobe ohne Batterie-Vorrang: Unter 100 bleibt die Freigabe ab
    dem Schwellwert erhalten."""
    cfg = RegulationConfig(battery_priority=False, ev_lock_soc=90)
    assert protected_battery_discharge(90.0, -2000, cfg) == 0
    assert protected_battery_discharge(89.9, -2000, cfg) == 2000


def test_missing_soc_is_treated_conservatively():
    """Ohne SoC-Wert wird geschützt statt freigegeben."""
    cfg = RegulationConfig(ev_lock_soc=50)
    assert protected_battery_discharge(None, -2000, cfg) == 2000


# --------------------------------------------- Batterie hat IMMER Vorrang
# Dritter Anlauf beim selben Thema, diesmal ohne Schwellwert. Zweimal ist der
# Schutz vorher genau am Grenzwert gekippt (100 gemeint, 100 gemessen; dann
# 99,9 eingestellt, 100,0 gemessen). Eine Einstellung, die man exakt treffen
# muss, damit sie das Gegenteil nicht tut, ist die falsche Einstellung.

def test_battery_priority_protects_discharge_at_any_soc():
    """Standard: Der Speicher hat Vorrang, egal wie voll er ist."""
    cfg = RegulationConfig()
    for soc in (0.0, 20.0, 55.0, 99.9, 100.0, None):
        assert protected_battery_discharge(soc, -2000, cfg) == 2000


def test_battery_priority_reserves_charge_at_any_soc():
    """Auch die Ladeleistung gehört dem Speicher – nicht erst unter 20 %."""
    cfg = RegulationConfig()
    for soc in (0.0, 20.0, 55.0, 99.9, 100.0, None):
        assert reserved_battery_charge(soc, 4000, cfg) == 4000


def test_almost_full_lock_no_longer_backfires():
    """Der zuletzt gemeldete Fall: ev_lock_soc = 99,9 bei einem Speicher, der
    exakt 100,0 % meldet. Mit der Schwellenlogik war der Schutz genau dann
    aus. Der Vorrang kennt diesen Grenzfall nicht mehr."""
    cfg = RegulationConfig(ev_lock_soc=99.9)
    assert protected_battery_discharge(100.0, -3000, cfg) == 3000


def test_pool_excludes_everything_the_battery_uses():
    """Rechenprobe zum gemeldeten Fall: 9 kW Überschuss, davon lädt der
    Speicher 4 kW. Für Auto und Warmwasser bleiben 5 kW – nicht 9."""
    cfg = RegulationConfig()
    guard = protected_battery_discharge(95.0, 4000, cfg)
    claim = reserved_battery_charge(95.0, 4000, cfg)
    assert max(0.0, 9000 - guard - claim) == 5000


def test_wallbox_stops_at_once_when_battery_is_protected():
    """Ohne diesen Zweig hielte die Stopp-Verzögerung das Auto noch drei
    Minuten auf Mindeststrom – bezahlt aus dem Speicher."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(200, charging(1380),
                  ctx(battery_protected=True, battery_handled_globally=True))
    assert not d.enable
    assert "Batterie" in d.reason


def test_heater_stops_at_once_when_battery_is_protected():
    """Gleiches Spiel beim Warmwasser: Die Stopp-Verzögerung darf den
    Heizstab nicht aus dem Speicher weiterlaufen lassen."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    d = wh.decide(50, data, ctx(battery_protected=True))
    assert d.power_w == 0
    assert "Batterie" in d.reason


def test_heater_forecast_hold_cannot_override_battery_protection():
    """Auch die Prognose darf den Heizstab nicht am Speicher halten."""
    clock = FakeClock()
    wh, data = running_heater(clock)
    d = wh.decide(50, data, ctx(solar_recovery_expected=True, battery_protected=True))
    assert d.power_w == 0


# ------------------------------------------- Ampere-Regelung am Netzpunkt
# Ziel des Nutzers: weder Einspeisung noch Batteriebezug. Dafür muss der
# Sollstrom zur Wirklichkeit passen – 230 V je Phase ist nur ein Nennwert.

def test_learned_watts_per_amp_prevents_overdraw():
    """Mit starren 230 V fordert der Regler bei 2400 W Budget 10 A an. Das
    Fahrzeug zieht davon real 2500 W – die fehlenden 100 W kommen aus dem
    Speicher. Mit dem gemessenen Faktor bleibt es bei 9 A."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    measured = WallboxData(state=WallboxState.CHARGING, power=2500, current_set=10)
    wb.decide(2500, measured, ctx())
    assert wb.watts_per_amp() == pytest.approx(250.0)

    d = wb.decide(2400, measured, ctx())
    assert d.current_a == 9
    assert d.power_w <= 2400


def test_implausible_measurement_is_not_learned():
    """Beim Hochrampen passen Sollstrom und Messung kurz nicht zusammen.
    Daraus zu lernen würde die Regelung dauerhaft verziehen."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    ramping = WallboxData(state=WallboxState.CHARGING, power=300, current_set=16)
    wb.decide(3000, ramping, ctx())
    assert wb.watts_per_amp() == pytest.approx(230.0)  # Nennwert bleibt


def test_current_rises_gradually():
    """Ein Sprung von 6 auf 16 A überfordert die Regelung: Das Fahrzeug folgt
    erst Sekunden später, bis dahin deckt der Speicher die Lücke."""
    clock = FakeClock()
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16,
                        phases_mode="fixed1", start_delay_s=0, ramp_up_step_a=3),
        clock=clock,
    )
    assert wb.decide(1400, connected(), ctx()).current_a == 6
    assert wb.decide(10000, charging(1380), ctx()).current_a == 9
    assert wb.decide(10000, charging(2070), ctx()).current_a == 12
    assert wb.decide(10000, charging(2760), ctx()).current_a == 15


def test_current_may_drop_immediately():
    """Nach unten wird nicht begrenzt – sonst zöge das Fahrzeug bei einer
    Wolke minutenlang weiter aus dem Speicher."""
    clock = FakeClock()
    wb = running_wallbox(clock)  # steht bei 13 A
    assert wb.decide(1380, charging(3000), ctx()).current_a == 6


def test_measured_voltage_beats_estimation():
    """Meldet das Gerät Spannung und Phasenzahl, wird gerechnet statt
    geschätzt – das ist exakt und braucht keine Einschwingzeit."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    wb.decide(3000, WallboxData(state=WallboxState.CHARGING, power=2400,
                                current_set=10, phases_active=1, voltage=241.0), ctx())
    assert wb.watts_per_amp() == pytest.approx(241.0)


def test_coarse_power_reading_cannot_inflate_the_start_threshold():
    """Der gemeldete Fall: Tesla liefert charger_power als ganzzahligen
    kW-Wert. 2 kW geteilt durch 5 A Sollstrom ergibt 400 W/A statt 230.

    Da der Faktor über die Mindestleistung in die Startschwelle eingeht, stieg
    diese damit von 1,4 kW auf 6 A × 400 W = 2,4 kW – das Fahrzeug lud erst
    bei fast doppeltem Überschuss los."""
    clock = FakeClock()
    wb = running_wallbox(clock)  # 6 A Mindeststrom, einphasig
    grob = WallboxData(state=WallboxState.CHARGING, power=2000, current_set=5)
    for _ in range(5):
        wb.decide(2000, grob, ctx())
    assert wb.watts_per_amp() <= 230 * 1.35      # nicht 400
    assert wb._min_power() < 1500                 # nicht 2400


def test_vehicle_that_draws_nothing_is_recognised_as_absent():
    """Der gemeldete Fall: Das Auto steht nicht am Kabel, meldet aber
    „verbunden". Bisher reservierte die Prioritätskette dafür Leistung, die
    nie abgerufen wurde – das Warmwasser bekam nur den Rest.

    Der Zustand wird deshalb nicht geglaubt, sondern gemessen."""
    clock = FakeClock()
    wb = running_wallbox(clock, idle_confirm_ticks=3)
    for _ in range(3):
        d = wb.decide(3000, connected(power=0), ctx())
    assert not d.enable
    assert "keine Leistung" in d.reason
    # Danach wird eine Weile nicht erneut probiert.
    assert not wb.decide(5000, connected(power=0), ctx()).enable


def test_absent_vehicle_detection_does_not_fire_while_charging():
    """Gegenprobe: Fließt Strom, bleibt alles wie gehabt."""
    clock = FakeClock()
    wb = running_wallbox(clock, idle_confirm_ticks=3)
    for _ in range(6):
        d = wb.decide(3000, charging(2500), ctx())
    assert d.enable


def test_manual_override_clears_the_absence_lock():
    """Der Nutzer sieht das Kabel, die Software nicht. Ein manueller Eingriff
    hebt die Sperre deshalb sofort auf."""
    clock = FakeClock()
    wb = running_wallbox(clock, idle_confirm_ticks=2)
    for _ in range(2):
        wb.decide(3000, connected(power=0), ctx())
    assert not wb.decide(3000, connected(power=0), ctx()).enable
    wb.override = "fast"
    assert wb.decide(3000, connected(power=0), ctx()).enable
