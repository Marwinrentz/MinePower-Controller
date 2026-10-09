"""Tests der Mechanismen gegen verschenkten Solarstrom.

Deckt ab: Quantisierung der Wallbox-Stellgröße, Lückenfüller an fein
modulierbare Lasten, adaptives Totband, aktive Nutzung der Solarprognose und
die Kennzahl 'verschenkte Energie'. Alles ohne Hardware und ohne Datenbank.
"""
from datetime import datetime

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
    battery_drain_alarm,
    compute_budget,
    protected_battery_discharge,
    releasable_battery_charge,
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
# Seit 2.16 ein Konzept: Entladung zählt nie als Überschuss (außer per
# Experten-Einstellung `battery_ev_support_soc`), Ladeleistung wird ab
# 'Batterie zuerst bis …" an die Lasten freigegeben.

def test_discharge_is_not_surplus():
    """Was aus dem Speicher kommt, ist kein Solarüberschuss."""
    cfg = RegulationConfig()
    assert protected_battery_discharge(80, -2000, cfg) == 2000


def test_charging_battery_is_not_protected():
    """Nur Entladung wird geschützt – Ladung darf die Kette weiterreichen."""
    cfg = RegulationConfig()
    assert protected_battery_discharge(80, +2000, cfg) == 0


def test_idle_battery_ignores_measurement_noise():
    cfg = RegulationConfig()
    assert protected_battery_discharge(80, -30, cfg) == 0


def test_expert_setting_lets_battery_support_loads_above_threshold():
    """Nur mit der Experten-Einstellung: Ab dem eingestellten Ladestand darf
    der Speicher Auto/Warmwasser mitversorgen."""
    cfg = RegulationConfig(battery_ev_support_soc=80)
    assert protected_battery_discharge(85, -2000, cfg) == 0
    assert protected_battery_discharge(79, -2000, cfg) == 2000


def test_battery_support_off_protects_at_any_soc():
    """Standard (0 = nie): Der Speicher versorgt die Lasten nie – auch nicht
    bei exakt 100,0 % (zweimal ist der Schutz früher genau am Grenzwert gekippt)."""
    cfg = RegulationConfig()
    for soc in (0.0, 20.0, 55.0, 99.9, 100.0, None):
        assert protected_battery_discharge(soc, -2000, cfg) == 2000


def test_controller_does_not_subtract_twice():
    """Hat der Loop die Entladung schon abgezogen, darf der Regler es nicht
    noch einmal tun – sonst fehlt die Leistung doppelt im Budget."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    d = wb.decide(2500, charging(2500),
                  ctx(battery_soc=50, battery_power=-2000, battery_handled_globally=True))
    assert d.current_a == 10  # 2500 W / 230 V, abgerundet – kein zweiter Abzug


def test_battery_protection_defeats_the_forecast_hold():
    """Eine Ladung 'über den Wolkendurchzug retten" wäre hier falsch: Der
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


# ------------------------------------------------- Batterie zuerst bis …

def test_battery_first_keeps_its_charge_at_any_soc():
    """Standard 'Batterie zuerst bis 100 %": Die Ladeleistung gehört dem
    Speicher – sie wird nicht freigegeben (und auch nicht doppelt abgezogen)."""
    cfg = RegulationConfig()
    for soc in (0.0, 20.0, 55.0, 99.9, 100.0, None):
        assert releasable_battery_charge(soc, 4000, cfg) == 0


def test_above_priority_soc_loads_may_take_the_charge():
    cfg = RegulationConfig(battery_priority_soc=80)
    assert releasable_battery_charge(85, 4000, cfg) == 4000
    assert releasable_battery_charge(45, 4000, cfg) == 0


def test_priority_has_hysteresis():
    """Freigegeben bei 80 %, zurückgenommen erst unter 77 % – sonst pendelt
    die Reihenfolge an der Grenze im Minutentakt."""
    cfg = RegulationConfig(battery_priority_soc=80)
    assert cfg.battery_first(79.0, was_first=True)
    assert not cfg.battery_first(80.0, was_first=True)
    assert not cfg.battery_first(78.0, was_first=False)
    assert cfg.battery_first(76.9, was_first=False)


def test_never_below_reserve_even_when_loads_go_first():
    cfg = RegulationConfig(battery_priority_soc=0, battery_reserve_soc=20)
    assert releasable_battery_charge(15, 4000, cfg) == 0
    assert releasable_battery_charge(55, 4000, cfg) == 4000


def test_discharging_battery_releases_nothing():
    cfg = RegulationConfig(battery_priority_soc=0)
    assert releasable_battery_charge(45, -2000, cfg) == 0


def _pool(budget: float, soc, bat_power: float, cfg: RegulationConfig) -> float:
    """Die Topf-Rechnung des Control-Loops, isoliert nachgebaut."""
    guard = protected_battery_discharge(soc, bat_power, cfg)
    release = releasable_battery_charge(soc, bat_power, cfg)
    return max(0.0, budget - guard + release)


def test_charging_battery_is_not_subtracted_from_the_pool():
    """Der teuerste Fehler einer früheren Fassung, als Rechenprobe.

    6 kW Sonne, 0,7 kW Haus, der Speicher nimmt maximal 2 kW auf. Am Netzpunkt
    bleiben damit 3,3 kW übrig – die Ladeleistung des Speichers ist dort schon
    abgezogen. Wer sie ein zweites Mal abzieht, kommt auf 1,3 kW – das Auto
    startet nie, und 3,3 kW gehen ins Netz."""
    cfg = RegulationConfig()
    budget = 6000 - 700 - 2000          # = 3300 W, so misst es der Zähler
    assert _pool(budget, 95.0, +2000, cfg) == 3300


def test_pool_excludes_the_discharge():
    cfg = RegulationConfig()
    assert _pool(9000, 95.0, -4000, cfg) == 5000


def test_released_charge_is_added_to_the_pool():
    """Über 'Batterie zuerst bis …": Was der Speicher lädt, darf die Kette ihm
    abnehmen – dafür wird es dem Topf zugeschlagen."""
    cfg = RegulationConfig(battery_priority_soc=50, battery_reserve_soc=20)
    assert _pool(3300, 95.0, +2000, cfg) == 5300


# ------------------------------------ Abregeln statt abschalten
# Der Kern des im Diagnosebericht sichtbaren Taktens: Jede Entladung über 50 W
# löste einen Nothalt aus. Abregeln hätte genügt – ein Nothalt kostet danach
# die volle Startschwelle und eine Minute Startverzögerung.

def test_small_drain_does_not_trigger_a_hard_stop():
    """Der gemeldete Fall: 102 W aus dem Speicher, während der Überschuss nur
    5 W unter dem Mindeststrom lag. Die Ladung wurde beendet und setzte danach
    acht Minuten aus – in denen 1,6 kW ins Netz gingen."""
    cfg = RegulationConfig()
    assert not battery_drain_alarm(102.0, cfg)


def test_substantial_drain_still_triggers():
    """Gegenprobe: Zieht die Last den Speicher wirklich leer, wird gestoppt."""
    cfg = RegulationConfig()
    assert battery_drain_alarm(1380.0, cfg)


def test_drain_limit_is_configurable():
    """Wer gar keine Entladung dulden will, stellt die Schwelle auf 0."""
    cfg = RegulationConfig(battery_drain_limit_w=0)
    assert battery_drain_alarm(60.0, cfg)


def test_small_drain_is_still_subtracted_from_the_budget():
    """Wichtig: Geduldet heißt nicht ignoriert. Die Entladung wird weiterhin
    vollständig vom Budget abgezogen – die Last regelt also ab, sie läuft nur
    nicht in den Nothalt."""
    cfg = RegulationConfig()
    assert protected_battery_discharge(95.0, -102.0, cfg) == 102.0
    assert _pool(1500, 95.0, -102.0, cfg) == 1398


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
    wb.decide(2500, measured, ctx())          # Sollstrom das erste Mal gesehen
    clock.advance(46)                          # eingeschwungen
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
    clock.advance(46)
    wb.decide(3000, ramping, ctx())
    assert wb.watts_per_amp() == pytest.approx(230.0)  # Nennwert bleibt


# --------------------------------------------- Nur eingeschwungen messen
# Aus dem Diagnosebericht: Die Ladung brach alle paar Minuten ab. Ursache war
# eine Kettenreaktion – während des Hochrampens gemessen, Faktor zu klein
# gelernt, daraufhin zu viele Ampere angefordert, Speicher deckt die Lücke,
# Batterie-Schutz beendet die Ladung.

def test_ramp_measurement_is_ignored_even_inside_tolerance():
    """Der gemeldete Fall: Das Fahrzeug steht auf 10 A, zieht aber erst
    2100 W statt 2300 W. 210 W/A liegt noch in der Plausibilitätsspanne – aus
    einer Rampenmessung darf trotzdem nicht gelernt werden."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    ramping = WallboxData(state=WallboxState.CHARGING, power=2100, current_set=10)
    for _ in range(5):                 # fünf Takte, aber ohne Zeit dazwischen
        wb.decide(2300, ramping, ctx())
    assert wb.watts_per_amp() == pytest.approx(230.0)


def test_settled_measurement_is_learned():
    """Gegenprobe: Liegt derselbe Sollstrom lange genug an, wird gelernt."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    settled = WallboxData(state=WallboxState.CHARGING, power=2100, current_set=10)
    wb.decide(2300, settled, ctx())
    clock.advance(46)
    wb.decide(2300, settled, ctx())
    assert wb.watts_per_amp() == pytest.approx(210.0)


def test_setpoint_change_restarts_the_settling_time():
    """Ein neuer Sollstrom heißt: von vorn einschwingen."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    wb.decide(2300, WallboxData(state=WallboxState.CHARGING, power=2100, current_set=10), ctx())
    clock.advance(40)
    # Sollstrom wechselt auf 12 A – die Uhr beginnt neu
    ramping = WallboxData(state=WallboxState.CHARGING, power=2100, current_set=12)
    wb.decide(2760, ramping, ctx())
    clock.advance(20)                  # 60 s seit dem ersten, aber erst 20 s seit dem Wechsel
    wb.decide(2760, ramping, ctx())
    assert wb.watts_per_amp() == pytest.approx(230.0)


def test_underestimated_factor_cannot_cause_overdraw():
    """Die Schranke nach unten ist die wichtige: Ein zu kleiner Faktor lässt
    den Regler zu viele Ampere anfordern. 230 V ±10 % (EN 50160) ist das
    Äußerste, was eine echte Messung ergeben kann."""
    clock = FakeClock()
    wb = running_wallbox(clock)
    absurd = WallboxData(state=WallboxState.CHARGING, power=1000, current_set=10)
    wb.decide(2300, absurd, ctx())
    clock.advance(46)
    wb.decide(2300, absurd, ctx())
    assert wb.watts_per_amp() >= 230 * 0.9


def test_current_rises_gradually():
    """Ein Sprung von 6 auf 16 A überfordert die Regelung: Das Fahrzeug folgt
    erst Sekunden später, bis dahin deckt der Speicher die Lücke."""
    clock = FakeClock()
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16,
                        phases_mode="fixed1", start_delay_s=0, ramp_up_step_a=3),
        clock=clock,
    )
    # Startschwelle liegt 5 % über der Mindestleistung (1380 W → 1449 W)
    assert wb.decide(1500, connected(), ctx()).current_a == 6
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
    'verbunden". Bisher reservierte die Prioritätskette dafür Leistung, die
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


# ------------------------------- Der Regler muss die Hardware-Grenzen kennen
# Aus dem Diagnosebericht: Ein Tesla nimmt erst ab 5 A an, viele Wallboxen erst
# ab 6 A. Kommandiert der Regler weniger, klemmt der Treiber nach oben – das
# Fahrzeug zieht dann mehr, als zugeteilt war, und die Differenz kommt aus
# Speicher oder Netz. Der Regler muss deshalb mit dem echten Mindeststrom
# rechnen, nicht mit dem gewünschten.

class _StubRow:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.name = kw.get("name", "Auto")
        self.category = "wallbox"
        self.driver_id = kw.get("driver_id", "simulation_wallbox")
        self.config = kw.get("config", {})
        self.settings = kw.get("settings", {})
        self.enabled = True


def _managed(settings: dict, driver_floor: float, driver_ceiling: float = 0.0):
    from app.core.loop import ManagedDevice

    dev = ManagedDevice.__new__(ManagedDevice)
    dev.name = "Auto"
    dev.settings = settings
    dev.driver = type("D", (), {"min_current": driver_floor, "max_current": driver_ceiling})()
    return dev._wallbox_settings()


def test_driver_floor_raises_the_configured_minimum():
    """5 A eingestellt, aber die Box kann erst 6 A: Es gilt 6 A."""
    s = _managed({"min_current": 5, "max_current": 16}, driver_floor=6.0)
    assert s.min_current == 6.0


def test_configured_minimum_is_kept_when_it_is_higher():
    """Gegenprobe: Ein bewusst höherer Mindeststrom bleibt stehen."""
    s = _managed({"min_current": 8, "max_current": 16}, driver_floor=5.0)
    assert s.min_current == 8.0


def test_driver_ceiling_caps_the_configured_maximum():
    """Was das Gerät nicht kann, darf nicht ins Budget eingeplant werden."""
    s = _managed({"min_current": 6, "max_current": 32}, driver_floor=5.0, driver_ceiling=16.0)
    assert s.max_current == 16.0


def test_guaranteed_current_follows_the_raised_minimum():
    """Der Garantie-Strom im Modus min_pv darf nicht unter der Hardware
    liegen – sonst zieht das Fahrzeug im Zweifel mehr als garantiert."""
    s = _managed({"min_current": 5, "min_pv_current": 5, "max_current": 16}, driver_floor=6.0)
    assert s.min_pv_current == 6.0


def test_minimum_charge_power_follows_the_hardware_floor():
    """Die Startschwelle rechnet mit dem echten Mindeststrom: 6 A × 230 V =
    1380 W. Mit 5 A gerechnet hätte der Regler schon bei 1150 W gestartet und
    die fehlenden 230 W aus dem Speicher gezogen."""
    clock = FakeClock()
    s = _managed({"min_current": 5, "max_current": 16}, driver_floor=6.0)
    wb = WallboxController(s, clock=clock)
    assert wb._min_power() == pytest.approx(1380.0)


def test_three_phase_vehicle_needs_three_phase_worth_of_surplus():
    """'Ein Tesla kann nicht mit 1 kW laden."

    Lädt das Fahrzeug dreiphasig, sind 6 A Mindeststrom nicht 1,38 kW, sondern
    3 × 230 V × 6 A = 4,14 kW. Der Regler muss deshalb auch 4,14 kW Überschuss
    abwarten – startet er früher, zieht das Fahrzeug die Differenz aus Speicher
    oder Netz, und zwar sofort und in voller Höhe."""
    clock = FakeClock()
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16,
                        phases_mode="fixed1", start_delay_s=0, start_threshold_w=1400),
        clock=clock,
    )
    dreiphasig = WallboxData(state=WallboxState.CHARGING, power=4140,
                             current_set=6, phases_active=3, voltage=230.0)
    wb.observe(dreiphasig)
    assert wb.watts_per_amp() == pytest.approx(690.0)
    assert wb._min_power() == pytest.approx(4140.0)

    # 2 kW Überschuss reichen dafür nicht – trotz 'start_threshold_w = 1400".
    assert not wb.decide(2000, connected(), ctx()).enable
    # Start erst 5 % über der echten Mindestleistung (4140 W → 4347 W)
    assert not wb.decide(4200, connected(), ctx()).enable
    assert wb.decide(4400, connected(), ctx()).enable


def test_three_phase_budget_yields_three_phase_amps():
    """Gegenprobe zur Ampere-Genauigkeit: 5 kW Überschuss sind dreiphasig
    7 A, nicht 21 A. Mit dem einphasigen Faktor gerechnet hätte das Fahrzeug
    das Dreifache gezogen."""
    clock = FakeClock()
    wb = WallboxController(
        WallboxSettings(mode="pv_only", min_current=6, max_current=16,
                        phases_mode="fixed1", start_delay_s=0, ramp_up_step_a=16),
        clock=clock,
    )
    laufend = WallboxData(state=WallboxState.CHARGING, power=4140,
                          current_set=6, phases_active=3, voltage=230.0)
    wb.decide(5000, laufend, ctx())
    d = wb.decide(5000, laufend, ctx())
    assert d.current_a == 7
    assert d.power_w == pytest.approx(7 * 690)


# ---------------------------------------------- 'Aus" muss aus bedeuten
# Gemeldet: Der Heizstab lief nach dem Abschalten manchmal weiter. Zwei
# unabhängige Ursachen, beide hier festgehalten.

def test_off_beats_a_running_boost():
    """Boost wurde vor dem Modus geprüft und lief mit voller Leistung weiter,
    obwohl der Nutzer gerade abgeschaltet hatte."""
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="off", max_power_w=3000), clock=clock)
    wh.boost = True
    d = wh.decide(3000, WaterHeaterData(power=3000, temperature_c=40), ctx())
    assert d.power_w == 0.0
    assert wh.boost is False        # und bleibt aus, nicht nur diesen Takt


def test_boost_still_works_when_not_switched_off():
    """Gegenprobe: Im PV-Modus heizt der Boost weiterhin durch."""
    clock = FakeClock()
    wh = WaterHeaterController(WaterHeaterSettings(mode="pv_only", max_power_w=3000), clock=clock)
    wh.boost = True
    assert wh.decide(0, WaterHeaterData(power=0, temperature_c=40), ctx()).power_w == 3000


# ------------------------- Gegenprüfung: folgt das Gerät dem Befehl?
# Aus dem Diagnosebericht: Das Auto lud um 18 Uhr mit 5,3 kW bei 435 W Sonne –
# 4,2 kW aus dem Speicher, 855 W aus dem Netz. MinePower hatte 'aus" längst
# gesendet und wiederholte es wegen des Sende-Dedups nie wieder.

class _ObeyDev:
    def __init__(self, power):
        self.name = "Auto"
        self.data = WallboxData(state=WallboxState.CHARGING, power=power)
        self.sent = {"enable": False, "current": 6}
        self.send_backoff = {"enable": (False, 1e9)}
        self.ignored_ticks = 0
        self.uncontrolled = False


async def _run_obey(dev, measured, allowed, ticks):
    loop = ControlLoop()
    for _ in range(ticks):
        await loop._obeyed(dev, measured, allowed, "aus")


async def test_load_that_ignores_off_is_retried():
    """Nach mehreren widersprechenden Takten wird der Dedup verworfen –
    nur so kommt 'aus" ein zweites Mal auf dem Gerät an."""
    dev = _ObeyDev(5349)
    await _run_obey(dev, 5349, 300.0, 4)
    assert dev.sent == {}            # Dedup weg → Befehl geht erneut raus
    assert dev.uncontrolled is True


async def test_single_deviating_tick_is_tolerated():
    """Ein Fahrzeug antwortet über Funk verzögert; der Tesla-Treiber liefert
    bis zu 20 s alte Werte. Ein einzelner Takt ist noch kein Ungehorsam."""
    dev = _ObeyDev(5349)
    await _run_obey(dev, 5349, 300.0, 1)
    assert dev.sent != {}
    assert dev.uncontrolled is False


async def test_obedient_load_is_left_alone():
    """Gegenprobe: Wer folgt, wird nicht angefasst."""
    dev = _ObeyDev(0)
    await _run_obey(dev, 0.0, 300.0, 10)
    assert dev.sent != {}
    assert dev.ignored_ticks == 0
    assert dev.uncontrolled is False


async def test_recovery_is_noticed():
    """Folgt das Gerät wieder, wird der Zustand zurückgesetzt."""
    dev = _ObeyDev(5349)
    await _run_obey(dev, 5349, 300.0, 4)
    assert dev.uncontrolled is True
    await _run_obey(dev, 0.0, 300.0, 1)
    assert dev.uncontrolled is False


def test_obey_check_waits_for_settle_time(monkeypatch):
    """Direkt nach einem Stellbefehl wird nicht ueber Gehorsam geurteilt.

    Im Diagnosebericht stand zweimal 'Tesla folgt der Regelung nicht: aus,
    gemessen 11136 W" -- exakt 16 A auf drei Phasen, also der Messwert VOR
    dem Stoppbefehl, abgelesen unmittelbar danach. Das Fahrzeug hatte nichts
    falsch gemacht; der Regelkreis hat zu frueh hingesehen.
    """
    import asyncio
    import time as _time
    from app.core.loop import ControlLoop

    class Dev:
        name = "Tesla"
        driver = type("D", (), {"read_timeout_s": 20.0})()
        ignored_ticks = 0
        uncontrolled = False
        sent_at: dict = {}

    loop = ControlLoop()
    dev = Dev()
    dev.sent_at = {"enable": _time.monotonic()}  # Befehl gerade eben gesendet

    # Mehr Takte als OBEY_CONFIRM_TICKS, trotzdem keine Meldung.
    for _ in range(10):
        asyncio.run(loop._obeyed(dev, measured_w=11136.0, allowed_w=0.0, what="aus"))

    assert dev.ignored_ticks == 0
    assert not dev.uncontrolled


def test_obey_check_fires_after_settle_time():
    """Nach der Karenzzeit greift die Gegenpruefung wie bisher."""
    import asyncio
    from app.core.loop import ControlLoop, OBEY_CONFIRM_TICKS

    class Dev:
        name = "Tesla"
        driver = type("D", (), {"read_timeout_s": 0.0})()
        ignored_ticks = 0
        uncontrolled = False
        sent_at: dict = {}
        sent: dict = {}
        send_backoff: dict = {}

    loop = ControlLoop()
    dev = Dev()
    dev.sent_at = {"enable": 0.0}  # lange her

    for _ in range(OBEY_CONFIRM_TICKS):
        asyncio.run(loop._obeyed(dev, measured_w=5000.0, allowed_w=0.0, what="aus"))

    assert dev.uncontrolled
