"""Regelungslogik: Überschussverteilung, Hysterese, Lademodi, Phasenumschaltung.

Bewusst als reine Controller-Klassen ohne I/O implementiert (injizierbare
Uhr `clock`), damit die komplette Logik ohne Hardware unit-testbar ist.

Budget-Modell (wie bei etablierten Überschussreglern):
    budget = Σ(aktuelle Leistung aller steuerbaren Lasten) − P_grid − grid_target
P_grid ist geglättet, + = Bezug. Ein Export von 500 W bei 4 kW laufender
Ladung ergibt also budget = 4500 W → so viel PV-Überschuss steht den
steuerbaren Lasten insgesamt zur Verfügung.

Batterie in der Prioritätskette:
  * Lasten, die VOR der Batterie stehen, dürfen deren aktuelle Ladeleistung
    zusätzlich verwenden (der Hybrid-WR reduziert die Batterieladung
    automatisch, weil er selbst auf 0 W am Netzpunkt regelt).
  * Steht die Batterie vorne, bleibt ihre Ladeleistung unantastbar.
  * `ev_lock_soc`: unterhalb dieses SoC wird eine Batterie-Entladung nicht
    fürs Auto verwendet (Budget wird um die Entladeleistung reduziert).

Drei Mechanismen sorgen dafür, dass möglichst wenig Überschuss ins Netz geht:

1. **Ehrliche Quantisierung.** Eine Wallbox kann nur ganze Ampere. Der
   Regler rundet deshalb ab (nie auf – das würde Netzbezug erzeugen) und
   meldet im `WallboxDecision.power_w`, wie viel Leistung wirklich abgenommen
   wird. Nur so weiß der Control-Loop, wie viel Überschuss übrig bleibt.
2. **Lückenfüller** (:meth:`WaterHeaterController.absorb`). Der Rest, der für
   die nächste grob gestufte Last zu klein ist, wandert an eine fein
   modulierbare Last – unabhängig von deren Platz in der Prioritätskette.
3. **Adaptives Totband** (:class:`VolatilityTracker`). Bei ruhigem Wetter
   wird enger geregelt (weniger Verschenken), bei Wolkenwechsel weiter
   (weniger Flattern und weniger Start/Stopp-Zyklen).

Die Prognose (forecast.solar) fließt aktiv ein: Bei einem kurzen
Wolkendurchzug mit absehbarer Erholung wird eine laufende Last gehalten statt
abgeschaltet, und die Zielladung beginnt früher, wenn die erwartete Sonne bis
zur Deadline nicht reicht.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..drivers.base import WallboxData, WallboxState, WaterHeaterData

VOLTAGE = 230.0


class Smoother:
    """Gleitender Mittelwert über die letzten n Messwerte."""

    def __init__(self, samples: int = 3) -> None:
        self.values: deque[float] = deque(maxlen=max(1, samples))

    def add(self, value: float) -> float:
        self.values.append(value)
        return self.value

    @property
    def value(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0


class DelayTimer:
    """Bedingungs-Timer: `check(cond)` liefert erst True, wenn `cond`
    ununterbrochen für `delay_s` Sekunden erfüllt war."""

    def __init__(self, delay_s: float, clock=time.monotonic) -> None:
        self.delay_s = delay_s
        self.clock = clock
        self._since: float | None = None

    def check(self, condition: bool) -> bool:
        if not condition:
            self._since = None
            return False
        if self._since is None:
            self._since = self.clock()
        return (self.clock() - self._since) >= self.delay_s

    def reset(self) -> None:
        self._since = None

    def elapsed(self) -> float:
        """Wie lange die Bedingung schon erfüllt ist (0, wenn nicht)."""
        return 0.0 if self._since is None else self.clock() - self._since


#: Änderung der PV-Leistung je Tick, ab der wir von „starkem Wolkenwechsel“
#: sprechen. Bei einer 10-kWp-Anlage sind 400 W/Tick ein deutlicher Sprung.
VOLATILITY_REFERENCE_W = 400.0


class VolatilityTracker:
    """Wie unruhig ist die Erzeugung gerade?

    Der Index (0 = ruhig, 1 = starker Wolkenwechsel) steuert das Totband.
    Der Kompromiss dahinter, damit er nachvollziehbar bleibt:

    * **Ruhiges Wetter** → enges Totband. Jede kleine Abweichung wird
      ausgeregelt, es geht kaum Überschuss verloren. Das ständige Nachregeln
      kostet hier nichts, weil sich ohnehin wenig ändert.
    * **Wolkenwechsel** → weites Totband. Sonst jagt der Regler jedem Sprung
      hinterher, und die Lasten takten sich mit Start/Stopp-Zyklen kaputt.
      Der dabei kurzzeitig verschenkte Überschuss ist kleiner als der Verlust
      durch Dauer-Neustarts.
    """

    def __init__(self, samples: int = 20) -> None:
        self.samples: deque[float] = deque(maxlen=max(2, samples))
        self._last: float | None = None
        self.index = 0.0

    def add(self, pv_power: float) -> float:
        if self._last is not None:
            self.samples.append(abs(pv_power - self._last))
        self._last = pv_power
        if self.samples:
            mean_change = sum(self.samples) / len(self.samples)
            self.index = min(1.0, mean_change / VOLATILITY_REFERENCE_W)
        return self.index

    def deadband(self, cfg: "RegulationConfig") -> float:
        """Wirksames Totband für den aktuellen Zustand."""
        if not cfg.adaptive_deadband:
            return cfg.deadband_w
        factor = 0.5 + 2.5 * self.index  # 0,5× bei Ruhe … 3× bei starkem Wechsel
        return max(cfg.deadband_min_w, min(cfg.deadband_max_w, cfg.deadband_w * factor))

    def describe(self) -> str:
        if self.index < 0.15:
            return "stabil"
        if self.index < 0.45:
            return "leicht wechselhaft"
        return "starker Wolkenwechsel"


@dataclass
class RegulationConfig:
    interval_s: float = 3.0
    grid_target_w: float = 0.0     # Sollwert am Netzpunkt (0 = Nulleinspeisung)
    deadband_w: float = 100.0      # Totband: kleine Abweichungen nicht ausregeln
    smoothing_samples: int = 3
    house_limit_a: float = 35.0    # Hausanschluss-Schutz (je Phase)
    battery_reserve_soc: float = 20.0
    #: Ab diesem Batterie-SoC darf sich die Entladung als Überschuss verwenden
    #: lassen. Darunter ist sie geschützt: Was aus dem Speicher kommt, gilt
    #: nicht als Solarüberschuss und füttert weder Auto noch Warmwasser.
    #: 100 = Batterie wird nie für steuerbare Lasten angezapft.
    ev_lock_soc: float = 30.0
    # --- Verschenkte Energie minimieren ---
    adaptive_deadband: bool = True  # Totband an die Wetterlage anpassen
    deadband_min_w: float = 40.0
    deadband_max_w: float = 400.0
    gap_fill_min_w: float = 25.0   # ab diesem Rest lohnt der Lückenfüller
    use_forecast: bool = True      # Prognose aktiv in Entscheidungen einbeziehen


@dataclass
class ChargeContext:
    """Umgebungsdaten für Modus-Entscheidungen."""

    now: datetime = field(default_factory=datetime.now)
    price_ct: float | None = None            # aktueller Strompreis (ct/kWh)
    cheap_hour: bool = False                 # Tarif-Service: aktuelle Stunde günstig?
    schedule_active: bool = False            # Zeitplan-Fenster aktiv?
    battery_soc: float | None = None
    battery_power: float = 0.0               # + laden / − entladen
    house_current_a: float = 0.0             # geschätzter Hausstrom je Phase (ohne Wallboxen)
    other_wallbox_current_a: float = 0.0     # Summe Sollströme anderer Wallboxen
    house_limit_a: float = 63.0              # Hausanschluss-Limit je Phase
    # --- Solarprognose (forecast.solar) ---
    #: True, wenn die Erzeugung gerade deutlich unter der Prognose liegt, die
    #: Prognose für die nächste halbe Stunde aber wieder hoch ist → Wolke zieht
    #: durch, Erholung absehbar.
    solar_recovery_expected: bool = False
    #: Erwarteter, für steuerbare Lasten nutzbarer Solarertrag bis zur
    #: Zielladungs-Deadline in kWh (None = keine Prognose konfiguriert).
    forecast_surplus_kwh: float | None = None
    #: True, wenn der Control-Loop die Batterie-Entladung bereits global aus
    #: dem Budget herausgerechnet hat. Dann darf der Regler das nicht noch
    #: einmal tun – sonst wird sie doppelt abgezogen.
    battery_handled_globally: bool = False
    #: True, wenn die Batterie gerade geschont wird (Entladung zählt nicht als
    #: Überschuss). In dem Zustand darf keine Halte-Logik greifen: Eine Ladung
    #: „über den Wolkendurchzug zu retten“ hieße hier, den Speicher leerzuziehen.
    battery_protected: bool = False


@dataclass
class WallboxSettings:
    mode: str = "pv_only"          # pv_only|min_pv|fast|schedule|target|price
    min_current: float = 6.0
    max_current: float = 16.0
    phases_mode: str = "fixed1"    # fixed1|fixed3|auto
    start_threshold_w: float = 1400.0
    stop_threshold_w: float = 200.0   # Budget-Unterschreitung unter Minimum
    start_delay_s: float = 60.0
    stop_delay_s: float = 180.0
    phase_switch_delay_s: float = 120.0
    phase_switch_lock_s: float = 600.0
    min_pv_current: float = 6.0    # Garantie-Strom im Modus min_pv
    target_soc: float = 80.0
    target_time: str = "07:00"     # Deadline für Zielladung
    price_limit_ct: float = 15.0
    use_battery_for_ev: bool = False  # Batterie-Entladung fürs Auto zulassen
    # --- Stellgrößen-Quantisierung ---
    #: Kleinste Stromstufe der Box in A. Fast alle können nur ganze Ampere.
    current_step: float = 1.0
    #: "down" = abrunden (nie Netzbezug erzeugen; der Rest geht an eine fein
    #: modulierbare Last, siehe Lückenfüller). "nearest" = kaufmännisch runden.
    current_rounding: str = "down"
    # --- Prognose ---
    use_forecast: bool = True
    #: Wie lange eine Ladung bei absehbarer Erholung höchstens gehalten wird.
    forecast_hold_max_s: float = 900.0

    @classmethod
    def from_dict(cls, d: dict) -> "WallboxSettings":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d and d[f] is not None}
        return cls(**known)


@dataclass
class WallboxDecision:
    enable: bool = False
    current_a: float = 0.0
    phases: int | None = None      # None = nicht ändern
    reason: str = ""
    #: Leistung, die diese Entscheidung tatsächlich abnimmt (quantisiert).
    #: Der Control-Loop rechnet damit weiter – sonst würde der durch das
    #: Abrunden frei gewordene Rest in der Budgetrechnung verschwinden.
    power_w: float = 0.0


class WallboxController:
    """Regelt eine Wallbox (oder ein direkt gesteuertes Fahrzeug)."""

    def __init__(self, settings: WallboxSettings, clock=time.monotonic) -> None:
        self.s = settings
        self.clock = clock
        self.start_timer = DelayTimer(settings.start_delay_s, clock)
        self.stop_timer = DelayTimer(settings.stop_delay_s, clock)
        self.phase_up_timer = DelayTimer(settings.phase_switch_delay_s, clock)
        self.phase_down_timer = DelayTimer(settings.phase_switch_delay_s, clock)
        self.charging = False
        self.phases = 1 if settings.phases_mode != "fixed3" else 3
        self._last_phase_switch = -1e9
        self._forecast_hold_since: float | None = None
        self.override: str | None = None   # None|"fast"|"stop" (manueller Override)

    # ------------------------------------------------------------ Helpers

    def _min_power(self) -> float:
        return self.s.min_current * self.phases * VOLTAGE

    def _max_power(self) -> float:
        return self.s.max_current * self.phases * VOLTAGE

    def _amps_for(self, watts: float) -> float:
        return watts / (self.phases * VOLTAGE)

    def _quantize(self, amps: float) -> float:
        """Auf die kleinste Stromstufe der Box bringen.

        Standard ist Abrunden: Aufrunden würde mehr anfordern, als PV liefert,
        und damit Netzbezug erzeugen. Der abgeschnittene Rest ist nicht
        verloren – der Control-Loop reicht ihn an eine fein modulierbare Last
        weiter (Lückenfüller)."""
        step = self.s.current_step
        if step <= 0:
            return amps
        stepped = math.floor(amps / step + 1e-9) * step if self.s.current_rounding == "down" \
            else round(amps / step) * step
        return max(self.s.min_current, min(stepped, self.s.max_current))

    def _decide_power(self, amps: float) -> float:
        return amps * self.phases * VOLTAGE

    def _effective_mode(self, ctx: ChargeContext) -> str:
        if self.override == "stop":
            return "off"
        if self.override == "fast":
            return "fast"
        return self.s.mode

    def _forecast_active(self, ctx: ChargeContext) -> bool:
        """Darf die Prognose gerade eine Ladung halten?

        Nur, solange die Erholung wirklich absehbar ist *und* das Halten nicht
        ausufert. Ohne diese Obergrenze würde eine dauerhaft falsche Prognose
        stundenlang Netzstrom ziehen."""
        # Wird die Batterie gerade geschont, ist Halten die falsche Antwort:
        # Der fehlende Überschuss käme nicht aus der Sonne, sondern aus dem
        # Speicher. Lieber sauber abschalten und später neu starten.
        if ctx.battery_protected:
            self._forecast_hold_since = None
            return False
        if not (self.s.use_forecast and ctx.solar_recovery_expected):
            self._forecast_hold_since = None
            return False
        now = self.clock()
        if self._forecast_hold_since is None:
            self._forecast_hold_since = now
        if (now - self._forecast_hold_since) > self.s.forecast_hold_max_s:
            return False
        return True

    def _target_needs_grid(self, data: WallboxData, ctx: ChargeContext) -> bool:
        """Zielladung: Reicht die Restzeit noch, um mit maximaler Leistung
        fertig zu werden? Wenn nicht → Netzladung erzwingen.

        Mit Prognose zusätzlich vorausschauend: Wenn absehbar ist, dass die
        Sonne bis zur Deadline nicht genug liefert, wird die Netzladung schon
        früher angesetzt, statt bis kurz vor knapp zu warten."""
        if data.soc is None:
            return False  # ohne SoC keine Deadline-Rechnung → PV-only-Verhalten
        hh, mm = (int(x) for x in self.s.target_time.split(":"))
        deadline = ctx.now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if deadline <= ctx.now:
            deadline += timedelta(days=1)
        missing_pct = max(0.0, self.s.target_soc - data.soc)
        if missing_pct <= 0:
            return False
        capacity = (data.extra or {}).get("capacity_kwh") or 60.0
        needed_kwh = missing_pct / 100.0 * float(capacity)
        max_kw = self._max_power() / 1000.0
        needed_h = needed_kwh / max(0.1, max_kw)
        remaining_h = (deadline - ctx.now).total_seconds() / 3600.0
        return remaining_h <= needed_h * self._deadline_margin(needed_kwh, ctx)

    def _deadline_margin(self, needed_kwh: float, ctx: ChargeContext) -> float:
        """Sicherheitsreserve für die Zielladung – prognoseabhängig.

        Ohne Prognose bleibt es bei 15 % Reserve: erst starten, wenn die Zeit
        knapp wird. Mit Prognose wird die Reserve gespreizt:

        * **Sonne deckt den Bedarf** → 15 % wie bisher. Es lohnt sich zu warten,
          jede gewartete Stunde bringt Solarstrom statt Netzstrom.
        * **Sonne reicht nicht** → bis zu 3-fache Reserve, die Netzladung
          beginnt also deutlich früher. Sonst hinge am Ende die gesamte
          Fehlmenge in einem knappen Zeitfenster, und jede Störung (Fahrzeug
          abgesteckt, Hausanschluss-Limit, Wolken) ließe das Ziel platzen.
        """
        base = 1.15
        if not (self.s.use_forecast and ctx.forecast_surplus_kwh is not None) or needed_kwh <= 0:
            return base
        coverage = min(1.0, max(0.0, ctx.forecast_surplus_kwh / needed_kwh))
        return base + (1.0 - coverage) * 1.85  # 1,15 … 3,0

    # ------------------------------------------------------------ Hauptlogik

    def decide(self, budget_w: float, data: WallboxData, ctx: ChargeContext) -> WallboxDecision:
        s = self.s
        mode = self._effective_mode(ctx)

        if data.state in (WallboxState.IDLE, WallboxState.ERROR, WallboxState.COMPLETE):
            self.charging = False
            self.start_timer.reset()
            self.stop_timer.reset()
            return WallboxDecision(enable=False, current_a=0, reason=f"state={data.state.value}")

        if mode == "off":
            self.charging = False
            return WallboxDecision(enable=False, current_a=0, reason="manuell gestoppt")

        # Batterie-Sperre: Entladung der Hausbatterie nicht fürs Auto verwenden.
        # (Die Verteilung der Batterie-LADEleistung über die Prioritätskette
        # übernimmt der Control-Loop über getrennte Budget-Pools.)
        # Hat der Control-Loop die Entladung schon global abgezogen, wäre ein
        # zweiter Abzug hier doppelt gemoppelt.
        if (
            not ctx.battery_handled_globally
            and ctx.battery_soc is not None
            and ctx.battery_power < -50
            and not s.use_battery_for_ev
        ):
            budget_w += ctx.battery_power  # battery_power ist negativ → Budget sinkt

        if mode == "fast" or (mode == "schedule" and ctx.schedule_active):
            self.charging = True
            return self._finish(WallboxDecision(True, s.max_current, self._phase_target(1e9), "Volllast"), ctx)

        if mode == "schedule" and not ctx.schedule_active:
            self.charging = False
            return WallboxDecision(enable=False, current_a=0, reason="außerhalb Zeitfenster")

        if mode == "price":
            if ctx.cheap_hour or (ctx.price_ct is not None and ctx.price_ct <= s.price_limit_ct):
                self.charging = True
                return self._finish(
                    WallboxDecision(True, s.max_current, self._phase_target(1e9), "günstiger Tarif"), ctx
                )
            mode = "pv_only"  # sonst Überschussladen

        if mode == "target":
            if self._target_needs_grid(data, ctx):
                self.charging = True
                return self._finish(
                    WallboxDecision(True, s.max_current, self._phase_target(1e9), "Zielladung: Deadline"), ctx
                )
            mode = "pv_only"

        guaranteed_w = 0.0
        if mode == "min_pv":
            guaranteed_w = s.min_pv_current * self.phases * VOLTAGE

        # --- PV-Überschussregelung mit Hysterese ---
        surplus_for_start = budget_w
        if not self.charging:
            if self.start_timer.check(surplus_for_start >= max(s.start_threshold_w, self._min_power())) or guaranteed_w > 0:
                self.charging = True
                self.stop_timer.reset()
            else:
                return WallboxDecision(enable=False, current_a=0, reason=f"warte auf Überschuss ({budget_w:.0f} W)")

        desired_w = max(guaranteed_w, budget_w)  # ungekappt – Basis der Phasenwahl
        target_w = max(guaranteed_w, min(budget_w, self._max_power()))

        # Phasenumschaltung prüfen (nur im Auto-Modus); None = keine Änderung
        phases = self._phase_target(desired_w)
        if phases is not None:
            return self._finish(
                WallboxDecision(True, s.min_current, phases, f"Phasenumschaltung → {phases}p"), ctx
            )

        reason = f"PV-Überschuss {budget_w:.0f} W"
        if target_w < self._min_power():
            if guaranteed_w > 0:
                target_w = guaranteed_w
            elif self._forecast_active(ctx):
                # Wolke zieht durch, Erholung ist absehbar: Ladung halten statt
                # abschalten. Ein Neustart kostet die Stop-/Start-Hysterese und
                # damit mehr Überschuss, als das Halten an Netzstrom zieht.
                target_w = self._min_power()
                self.stop_timer.reset()
                reason = "Wolkendurchzug – Ladung wird gehalten (Prognose: Erholung)"
            elif self.stop_timer.check(budget_w < self._min_power() - self.s.stop_threshold_w):
                self.charging = False
                self.start_timer.reset()
                return WallboxDecision(enable=False, current_a=0, reason="Überschuss zu gering → Stopp")
            else:
                target_w = self._min_power()  # Mindeststrom halten bis Stop-Timer greift
        else:
            self.stop_timer.reset()
            self._forecast_hold_since = None

        amps = max(s.min_current, self._amps_for(target_w))
        return self._finish(WallboxDecision(True, amps, None, reason), ctx)

    def _phase_target(self, target_w: float) -> int | None:
        """1↔3-Phasen-Logik mit Umschaltverzögerung und Sperrzeit."""
        s = self.s
        if s.phases_mode == "fixed1":
            self.phases = 1
            return None
        if s.phases_mode == "fixed3":
            self.phases = 3
            return None
        now = self.clock()
        locked = (now - self._last_phase_switch) < s.phase_switch_lock_s
        three_min = s.min_current * 3 * VOLTAGE
        one_max = s.max_current * 1 * VOLTAGE
        if self.phases == 1 and not locked and self.phase_up_timer.check(target_w > one_max * 1.05 and target_w >= three_min):
            self.phases = 3
            self._last_phase_switch = now
            self.phase_down_timer.reset()
            return 3
        if self.phases == 3 and not locked and self.phase_down_timer.check(target_w < three_min * 0.95):
            self.phases = 1
            self._last_phase_switch = now
            self.phase_up_timer.reset()
            return 1
        return None

    def _finish(self, d: WallboxDecision, ctx: ChargeContext) -> WallboxDecision:
        """Hausanschluss-Schutz anwenden, auf Gerätestufen quantisieren und die
        tatsächlich abgenommene Leistung ausweisen."""
        # Hausanschluss-Schutz: Gesamtstrom je Phase begrenzen.
        # Annahme: Wallbox belastet alle aktiven Phasen gleich
        available = max(0.0, ctx.house_limit_a - ctx.house_current_a - ctx.other_wallbox_current_a)
        if d.enable and d.current_a > available:
            d.current_a = max(0.0, available)
            d.reason += " (Hausanschluss-Limit)"
            if d.current_a < self.s.min_current:
                d.enable = False
                d.current_a = 0.0
        if d.enable:
            d.current_a = self._quantize(d.current_a)
            d.power_w = self._decide_power(d.current_a)
        else:
            d.power_w = 0.0
        return d

    # Rückwärtskompatibler Alias (frühere Versionen nutzten _limit)
    _limit = _finish


@dataclass
class WaterHeaterSettings:
    mode: str = "pv_only"          # pv_only|schedule|off
    min_power_w: float = 100.0     # kleinste sinnvolle Modulationsstufe
    max_power_w: float = 3000.0
    start_threshold_w: float = 200.0
    start_delay_s: float = 30.0
    stop_delay_s: float = 60.0
    target_temp_c: float = 60.0
    boost_temp_c: float = 65.0
    modulating: bool = True        # False = Relais (nur EIN/AUS mit Nennleistung)
    use_forecast: bool = True
    forecast_hold_max_s: float = 900.0

    @classmethod
    def from_dict(cls, d: dict) -> "WaterHeaterSettings":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d and d[f] is not None}
        return cls(**known)


@dataclass
class WaterHeaterDecision:
    power_w: float = 0.0
    reason: str = ""


class WaterHeaterController:
    def __init__(self, settings: WaterHeaterSettings, clock=time.monotonic) -> None:
        self.s = settings
        self.clock = clock
        self.start_timer = DelayTimer(settings.start_delay_s, clock)
        self.stop_timer = DelayTimer(settings.stop_delay_s, clock)
        self.on = False
        self.boost = False  # manueller Override "Warmwasser-Boost"
        self._forecast_hold_since: float | None = None

    def _forecast_active(self, ctx: ChargeContext) -> bool:
        if not (self.s.use_forecast and ctx.solar_recovery_expected):
            self._forecast_hold_since = None
            return False
        now = self.clock()
        if self._forecast_hold_since is None:
            self._forecast_hold_since = now
        return (now - self._forecast_hold_since) <= self.s.forecast_hold_max_s

    def decide(self, budget_w: float, data: WaterHeaterData, ctx: ChargeContext) -> WaterHeaterDecision:
        s = self.s
        temp = data.temperature_c

        if self.boost:
            if temp is not None and temp >= s.boost_temp_c:
                self.boost = False
            else:
                return WaterHeaterDecision(s.max_power_w, "Boost aktiv")

        if s.mode == "off":
            self.on = False
            return WaterHeaterDecision(0.0, "deaktiviert")

        if temp is not None and temp >= s.target_temp_c:
            self.on = False
            return WaterHeaterDecision(0.0, f"Zieltemperatur {s.target_temp_c:.0f} °C erreicht")

        if s.mode == "schedule":
            if ctx.schedule_active:
                return WaterHeaterDecision(s.max_power_w, "Zeitfenster aktiv")
            return WaterHeaterDecision(0.0, "außerhalb Zeitfenster")

        # PV-Überschuss
        min_w = s.min_power_w if s.modulating else s.max_power_w
        if not self.on:
            if self.start_timer.check(budget_w >= max(s.start_threshold_w, min_w)):
                self.on = True
                self.stop_timer.reset()
            else:
                return WaterHeaterDecision(0.0, f"warte auf Überschuss ({budget_w:.0f} W)")

        if budget_w < min_w:
            if self._forecast_active(ctx):
                return WaterHeaterDecision(min_w, "Wolkendurchzug – Heizung wird gehalten (Prognose: Erholung)")
            if self.stop_timer.check(True):
                self.on = False
                self.start_timer.reset()
                return WaterHeaterDecision(0.0, "Überschuss zu gering → Stopp")
            return WaterHeaterDecision(min_w, "Stopp-Verzögerung")
        self.stop_timer.reset()
        self._forecast_hold_since = None

        power = min(budget_w, s.max_power_w) if s.modulating else s.max_power_w
        return WaterHeaterDecision(power, f"PV-Überschuss {budget_w:.0f} W")

    # ------------------------------------------------------------ Lückenfüller

    @property
    def fine_modulating(self) -> bool:
        """Kann diese Last beliebig kleine Restüberschüsse aufnehmen?

        Nur stufenlose Geräte (my-PV & Co.). Ein Relais-Heizstab kann nur
        seine volle Nennleistung – der taugt nicht als Lückenfüller."""
        return self.s.modulating

    def headroom(self, decision: WaterHeaterDecision, data: WaterHeaterData) -> float:
        """Wie viel zusätzliche Leistung könnte dieses Gerät jetzt aufnehmen?"""
        if not self.fine_modulating or not self.on or self.boost:
            return 0.0
        if self.s.mode != "pv_only":
            return 0.0
        temp = data.temperature_c
        if temp is not None and temp >= self.s.target_temp_c:
            return 0.0
        return max(0.0, self.s.max_power_w - decision.power_w)

    def absorb(
        self, decision: WaterHeaterDecision, extra_w: float, data: WaterHeaterData
    ) -> WaterHeaterDecision:
        """Rest-Überschuss zusätzlich aufnehmen (Lückenfüller).

        Wird vom Control-Loop gerufen, wenn nach der Prioritätsverteilung
        Überschuss übrig bleibt, der für die nächste grob gestufte Last (etwa
        den Mindeststrom einer Wallbox) zu klein ist. Er wandert dann an diese
        fein modulierbare Last – unabhängig von deren Platz in der Kette –
        statt ins Netz zu gehen.

        Erhöht ausschließlich: Ein Absenken über diesen Weg würde die
        Hysterese der normalen Entscheidung unterlaufen."""
        room = self.headroom(decision, data)
        add = min(room, max(0.0, extra_w))
        if add <= 0:
            return decision
        return WaterHeaterDecision(
            power_w=decision.power_w + add,
            reason=f"{decision.reason} + {add:.0f} W Restüberschuss (Lückenfüller)",
        )


def compute_budget(
    grid_power_smoothed: float,
    controllable_power: float,
    cfg: RegulationConfig,
    deadband_w: float | None = None,
) -> float:
    """Verfügbares Budget für alle steuerbaren Lasten.

    Innerhalb des Deadbands um den Netz-Sollwert wird die aktuelle
    Verteilung beibehalten (kein Nachregeln → kein Flattern).
    `deadband_w` überschreibt das konfigurierte Totband – der Control-Loop
    reicht hier das adaptive Totband herein (siehe :class:`VolatilityTracker`)."""
    band = cfg.deadband_w if deadband_w is None else deadband_w
    error = grid_power_smoothed - cfg.grid_target_w
    if abs(error) <= band:
        return controllable_power
    return controllable_power - error


#: Unterhalb dieser Leistung gilt die Batterie als in Ruhe (Messrauschen).
BATTERY_IDLE_W = 50.0


def protected_battery_discharge(
    battery_soc: float | None, battery_power: float, cfg: RegulationConfig
) -> float:
    """Wie viel Batterie-Entladeleistung darf **nicht** als Überschuss gelten?

    Der Kern des Problems: `compute_budget` sieht nur den Netzpunkt. Entlädt
    sich die Hausbatterie, um Auto und Warmwasser zu versorgen, steht am Netz
    trotzdem 0 W – das Budget wirkt also unverändert groß, obwohl die Sonne
    längst nicht mehr reicht. Ohne diese Korrektur füttert der Speicher die
    steuerbaren Lasten, bis er leer ist.

    Was hier abgezogen wird, ist genau die Entladeleistung. Oberhalb von
    `ev_lock_soc` gibt der Nutzer die Batterie bewusst frei – bei 100 %
    (Standard-Empfehlung für „nie anzapfen“) bleibt sie immer geschützt.

    Wichtig: Der Hausverbrauch bleibt davon unberührt. Geschützt wird nur
    gegen die *steuerbaren* Lasten; die Batterie darf weiterhin ganz normal
    den Haushalt versorgen – dafür ist sie da.
    """
    if battery_power >= -BATTERY_IDLE_W:
        return 0.0
    # 100 % heißt „niemals freigeben". Ohne diesen Sonderfall würde die
    # Einstellung am Grenzwert das Gegenteil bewirken: Ein voller Speicher
    # meldet über längere Zeit exakt 100,0 % – und genau dann wäre der Schutz
    # aus, während das Auto ihn leerzieht.
    if cfg.ev_lock_soc >= 100:
        return -battery_power
    if battery_soc is not None and battery_soc >= cfg.ev_lock_soc:
        return 0.0  # Nutzer erlaubt die Entladung ab diesem Ladestand
    return -battery_power


def reserved_battery_charge(
    battery_soc: float | None, battery_power: float, cfg: RegulationConfig
) -> float:
    """Ladeleistung, die dem Speicher vorbehalten bleibt.

    Gegenstück zum bisherigen Modell: Bislang durften Lasten, die in der Kette
    **vor** der Batterie stehen, deren Ladeleistung mitnutzen – es gab aber
    keinen Weg, der Batterie umgekehrt Vorrang zu geben. Ein Auto konnte damit
    auf 16 A stehen bleiben, während der Speicher nur noch den Rest bekam.

    Unterhalb von `battery_reserve_soc` hat der Speicher jetzt absoluten
    Vorrang: Seine aktuelle Ladeleistung wird aus dem Überschuss-Topf
    herausgerechnet. Darüber gilt wieder die Prioritätskette – ein fast voller
    Speicher soll das Auto nicht ausbremsen.

    Standard ist 20 %, das Verhalten ändert sich also nur, wenn der Nutzer den
    Wert bewusst hochsetzt.
    """
    if battery_power <= BATTERY_IDLE_W:
        return 0.0  # lädt nicht
    if battery_soc is None or battery_soc >= cfg.battery_reserve_soc:
        return 0.0
    return battery_power


def wasted_power(grid_power_smoothed: float, cfg: RegulationConfig) -> float:
    """Verschenkter Solarstrom in W: Einspeisung über dem Netz-Sollwert.

    Das ist Überschuss, der am Netzpunkt vorbeigeflossen ist, ohne dass ihn
    eine steuerbare Last aufgenommen hat. Bei `grid_target_w = 0` (Standard)
    ist das schlicht die aktuelle Einspeiseleistung. Ist bewusst eine
    Mindest-Einspeisung eingestellt (negativer Sollwert), zählt nur, was
    darüber hinausgeht."""
    return max(0.0, cfg.grid_target_w - grid_power_smoothed)
