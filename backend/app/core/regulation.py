"""Regelungslogik: Überschussverteilung, Hysterese, Lademodi, Phasenumschaltung.

Bewusst als reine Controller-Klassen ohne I/O implementiert (injizierbare
Uhr `clock`), damit die komplette Logik ohne Hardware unit-testbar ist.

Budget-Modell (wie bei etablierten Überschussreglern):
    budget = Σ(aktuelle Leistung aller steuerbaren Lasten) − P_grid − grid_target
P_grid ist geglättet, + = Bezug. Ein Export von 500 W bei 4 kW laufender
Ladung ergibt also budget = 4500 W → so viel PV-Überschuss steht den
steuerbaren Lasten insgesamt zur Verfügung.

Batterie – ein Konzept statt dreier Schalter (seit 2.16):
    Die Netzleistung ist eine *Bilanz*: Was der Speicher gerade lädt, ist am
    Netzpunkt bereits verbraucht und taucht in `budget` nicht mehr auf. Was er
    entlädt, steckt umgekehrt voll darin, obwohl es nicht aus der Sonne kommt.

      pool = budget − Entladeleistung + freigegebene Ladeleistung

    * **Entladen** wird immer abgezogen (Ausnahme nur per Experten-Einstellung
      `battery_ev_support_soc`). Sonst würden Auto und Warmwasser aus dem
      Speicher gefüttert, während der Netzpunkt ausgeglichen aussieht.
    * **Laden** wird nie doppelt abgezogen. Unterhalb von
      `battery_priority_soc` ('Batterie zuerst bis …") behält der Speicher
      seine Ladeleistung; darüber dürfen die Lasten sie ihm abnehmen
      (siehe :func:`releasable_battery_charge`).

    Was der Speicher selbst tut (Automatik, Entladung gesperrt, Netzladen),
    entscheidet :mod:`app.core.battery_policy` – eine Tabelle, eine Funktion.

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
from datetime import datetime, timedelta, timezone

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

    @property
    def running(self) -> bool:
        """Bedingung erfüllt, Verzögerung aber noch nicht abgelaufen."""
        return self._since is not None


def waiting_reason(timer: DelayTimer, budget_w: float) -> str:
    """Begründung, solange eine Last noch nicht startet.

    Läuft die Startverzögerung schon, reicht der Überschuss – dann wäre
    'warte auf Überschuss (4067 W)' ein Widerspruch, der aussieht wie ein
    Fehler. Stattdessen steht da, wann es losgeht."""
    if timer.running:
        remaining = max(0.0, timer.delay_s - timer.elapsed())
        return f"Überschuss reicht ({budget_w:.0f} W) – Start in {remaining:.0f} s"
    return f"warte auf Überschuss ({budget_w:.0f} W)"


#: Änderung der PV-Leistung je Tick, ab der wir von 'starkem Wolkenwechsel'
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
    #: Regeltakt. 10 s vertragen alle Treiber (Tesla liest im Hintergrund mit
    #: eigenem Budget, der WiNet-S liest Steuerregister nur alle 15 s).
    interval_s: float = 10.0
    grid_target_w: float = 0.0     # Sollwert am Netzpunkt (0 = Nulleinspeisung)
    # --- Intern, keine Nutzereinstellung mehr (passt sich selbst an) -------
    #: Totband und Glättung: Der Regler bestimmt sie aus der Wetterlage
    #: (VolatilityTracker). Eine Einstellung dafür hat nur Fehlgriffe erzeugt.
    deadband_w: float = 100.0
    smoothing_samples: int = 3
    adaptive_deadband: bool = True
    deadband_min_w: float = 40.0
    deadband_max_w: float = 400.0
    gap_fill_min_w: float = 25.0   # ab diesem Rest lohnt der Lückenfüller
    # --- Hausanschluss ------------------------------------------------------
    house_limit_a: float = 35.0    # Hausanschluss-Schutz (je Phase)
    #: Phasen des Hausanschlusses (1 oder 3)
    house_phases: int = 3
    #: Batteriekapazität (kWh), falls das Gerät sie nicht meldet. 0 = unbekannt.
    battery_capacity_kwh: float = 0.0
    #: Höchster Netzbezug (W), solange über den PV-Überschuss hinaus aus dem
    #: Netz geladen wird. 0 = aus dem Hausanschluss abgeleitet.
    grid_charge_max_w: float = 0.0
    # --- Batterie: EIN Konzept ---------------------------------------------
    #: Die einzige Reserve: So weit entlädt der Speicher nie. MinePower
    #: schreibt sie als Min-SoC in den Wechselrichter (soweit der Wert dort
    #: erlaubt ist) und hält den Rest selbst (Entladesperre).
    battery_reserve_soc: float = 20.0
    #: 'Batterie zuerst bis …": Bis zu diesem Ladestand bekommt der Speicher
    #: Sonnenstrom (und günstigen Netzstrom) vor Auto und Warmwasser. 100 =
    #: immer zuerst, 0 = immer zuletzt.
    battery_priority_soc: float = 100.0
    battery_priority_hysteresis: float = 3.0
    #: Experte: Ab diesem Ladestand darf der Speicher Auto/Warmwasser im
    #: Überschussbetrieb mitversorgen. 0 = nie (Standard).
    battery_ev_support_soc: float = 0.0
    #: Ab welcher **bestätigten** Entladeleistung laufende Überschuss-Lasten
    #: hart abgeschaltet werden (statt nur abzuregeln).
    battery_drain_limit_w: float = 250.0
    #: Netzladen der Batterie: nur mit diesem Schalter, sonst nie.
    battery_grid_charge_enabled: bool = False
    #: Bis zu welchem Ladestand bei günstigem Preis aus dem Netz geladen wird.
    battery_grid_charge_soc: float = 50.0
    #: Nur, wenn die Prognose sagt, dass die Sonne es nicht schafft.
    battery_grid_charge_forecast: bool = True
    #: Experte: Speicher auch nicht für 'Sofort laden", Boost, Zeitplan und
    #: Geräteprogramme (ELWA) hergeben. Standard aus – das kostet mehr, als es
    #: spart (siehe battery_policy, Zeile 4). Preis-Lasten sind immer gesperrt.
    battery_protect_other_loads: bool = False
    # --- Manuelle Eingriffe: jeder endet ------------------------------------
    battery_manual_power_w: float = 3000.0
    battery_manual_max_min: float = 240.0
    #: 'Auto aus" endet beim Abstecken, spätestens nach dieser Zeit.
    override_stop_h: float = 4.0
    #: 'Sofort voll" endet bei Ladeende/Abstecken, spätestens nach dieser Zeit.
    override_fast_h: float = 12.0
    # --- Sonstiges ---------------------------------------------------------
    use_forecast: bool = True      # Prognose aktiv in Entscheidungen einbeziehen
    #: Ein angesteckt eingeschlafenes Fahrzeug wecken, wenn sonst Überschuss
    #: ins Netz ginge (höchstens alle 30 min, siehe ControlLoop._maybe_wake).
    vehicle_wake_enabled: bool = True

    def grid_limit_w(self) -> float:
        if self.grid_charge_max_w and self.grid_charge_max_w > 0:
            return float(self.grid_charge_max_w)
        return float(self.house_limit_a) * 230.0 * (1 if self.house_phases == 1 else 3)

    def battery_first(self, soc: float | None, was_first: bool = True) -> bool:
        """Hat der Speicher gerade Vorrang? Mit Hysterese, damit die
        Reihenfolge an der Grenze nicht im Minutentakt wechselt."""
        if self.battery_priority_soc >= 100:
            return True
        if self.battery_priority_soc <= 0 or soc is None:
            return self.battery_priority_soc >= 100
        if was_first:
            return soc < self.battery_priority_soc
        return soc < self.battery_priority_soc - self.battery_priority_hysteresis


#: Einstellungen, die es seit 2.16 nicht mehr gibt (siehe migrate_regulation)
LEGACY_REGULATION_KEYS = (
    "battery_priority", "battery_release_soc", "battery_release_hysteresis",
    "price_window_battery", "ev_lock_soc", "deadband_w", "smoothing_samples",
    "adaptive_deadband", "deadband_min_w", "deadband_max_w", "gap_fill_min_w",
)


def migrate_regulation(reg: dict) -> dict:
    """Alte Regelungs-Einstellungen auf das 2.16-Modell abbilden – ohne dass
    eine Absicht verloren geht. Idempotent.

    * `battery_priority` + `battery_release_soc` → 'Batterie zuerst bis X %":
      Vorrang an, Freigabe 0 → 100 % (immer zuerst); Vorrang an, Freigabe
      80 → 80 %; Vorrang aus → bis zur Reserve.
    * `ev_lock_soc` (nur ohne Vorrang wirksam) → `battery_ev_support_soc`.
    * Netzladen: Ziel war die Freigabe-Schwelle → `battery_grid_charge_soc`.
    * `price_window_battery` ('vollladen") entfällt: Der Speicher lädt nur
      noch mit eigenem Schalter aus dem Netz. Genau diese Nebenwirkung lud
      am 08.10. 1,5 h mit 5 kW zu 33 ct.
    * Totband/Glättung regeln sich selbst; Takt höchstens 10 s.
    """
    out = dict(reg or {})

    def num(key: str, default: float) -> float:
        try:
            value = out.get(key)
            return float(default if value is None else value)
        except (TypeError, ValueError):
            return float(default)

    priority = bool(out.get("battery_priority", True))
    release = num("battery_release_soc", 0.0)
    if "battery_priority_soc" not in out:
        if priority:
            out["battery_priority_soc"] = release if 0 < release < 100 else 100.0
        else:
            out["battery_priority_soc"] = num("battery_reserve_soc", 20.0)
    if "battery_ev_support_soc" not in out:
        out["battery_ev_support_soc"] = 0.0 if priority else min(100.0, num("ev_lock_soc", 30.0))
    if "battery_grid_charge_soc" not in out:
        out["battery_grid_charge_soc"] = release if release > 0 else 50.0
    out.setdefault("battery_grid_charge_forecast", True)
    out.setdefault("battery_protect_other_loads", False)
    out.pop("battery_protect_foreign_loads", None)
    out.setdefault("override_stop_h", 4.0)
    out.setdefault("override_fast_h", 12.0)
    if num("interval_s", 10.0) > 10.0:
        out["interval_s"] = 10.0
    for key in LEGACY_REGULATION_KEYS:
        out.pop(key, None)
    return out


def config_from_settings(reg: dict, default_interval_s: float = 10.0) -> RegulationConfig:
    """RegulationConfig aus dem Einstellungs-Abschnitt 'regulation"."""
    r = migrate_regulation(reg)

    def num(key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
        try:
            value = r.get(key)
            v = float(default if value is None or value == "" else value)
        except (TypeError, ValueError):
            v = float(default)
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    return RegulationConfig(
        interval_s=num("interval_s", default_interval_s, 2.0, 60.0),
        grid_target_w=num("grid_target_w", 0.0),
        house_limit_a=num("house_limit_a", 35.0, 6.0, 250.0),
        house_phases=1 if int(num("house_phases", 3, 1, 3)) == 1 else 3,
        battery_capacity_kwh=num("battery_capacity_kwh", 0.0, 0.0, 1000.0),
        grid_charge_max_w=num("grid_charge_max_w", 0.0, 0.0),
        battery_reserve_soc=num("battery_reserve_soc", 20.0, 0.0, 95.0),
        battery_priority_soc=num("battery_priority_soc", 100.0, 0.0, 100.0),
        battery_ev_support_soc=num("battery_ev_support_soc", 0.0, 0.0, 100.0),
        battery_drain_limit_w=num("battery_drain_limit_w", 250.0, 50.0),
        battery_grid_charge_enabled=bool(r.get("battery_grid_charge_enabled", False)),
        battery_grid_charge_soc=num("battery_grid_charge_soc", 50.0, 10.0, 100.0),
        battery_grid_charge_forecast=bool(r.get("battery_grid_charge_forecast", True)),
        battery_protect_other_loads=bool(r.get("battery_protect_other_loads", False)),
        battery_manual_power_w=num("battery_manual_power_w", 3000.0, 100.0),
        battery_manual_max_min=num("battery_manual_max_min", 240.0, 5.0, 720.0),
        override_stop_h=num("override_stop_h", 4.0, 0.5, 48.0),
        override_fast_h=num("override_fast_h", 12.0, 0.5, 48.0),
        use_forecast=bool(r.get("use_forecast", True)),
        vehicle_wake_enabled=bool(r.get("vehicle_wake_enabled", True)),
    )


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
    #: 'über den Wolkendurchzug zu retten' hieße hier, den Speicher leerzuziehen.
    battery_protected: bool = False
    #: True, wenn der Hausspeicher während eines Netzladefensters gesperrt
    #: werden konnte (Modus HOLD/Zwangsladen oder Reserve auf 100 %). Nur dann
    #: ist garantiert, dass Netzladen wirklich aus dem Netz kommt und nicht
    #: aus dem Speicher. Siehe `grid_charge_blocked` in loop.py.
    battery_locked: bool = False
    #: True, wenn ein Hausspeicher vorhanden ist, gerade entlädt und sich
    #: NICHT sperren lässt (reines Monitoring). Dann würde Netzladen den
    #: Speicher leerziehen – genau das Gegenteil der Absicht.
    battery_drain_unblockable: bool = False
    #: Die eine Preisgrenze aus 'Tarif" (ct/kWh) – nur für Begründungstexte;
    #: die Entscheidung 'günstig?" steckt bereits in `cheap_hour`.
    price_limit_ct: float | None = None
    #: Wie viel Leistung dieses Gerät beim Netzladen höchstens ziehen darf,
    #: ohne dass der Netzbezug des Hauses über `grid_charge_max_w` steigt (W).
    #: Der Control-Loop verteilt das Budget entlang der Prioritätskette:
    #: Wer vorn steht, bekommt den günstigen Netzstrom zuerst. None = keine
    #: Begrenzung (Einzeltests ohne Loop).
    grid_budget_w: float | None = None


def battery_blocks_grid_charge(ctx: ChargeContext, allow_drain: bool) -> str | None:
    """Würde Netzladen gerade den Hausspeicher leersaugen?

    Gibt den Grund im Klartext zurück, oder None wenn unbedenklich.

    Ein Hybrid-Wechselrichter kennt den Unterschied zwischen 'Auto" und
    'Haushalt" nicht. Er sieht am Netzpunkt eine Last und deckt sie aus der
    Batterie, solange Ladestand da ist. Wer absichtlich Netzstrom zieht, muss
    den Speicher deshalb vorher gegen Entladen sichern (battery_policy) – geht
    das nicht, bleibt es beim Überschussladen.

    Der Ladestand des Speichers blockiert Netzladen nicht mehr: Bekommt der
    Speicher Vorrang, regelt das die Verteilung des Netzbudgets, nicht ein
    Verbot für die Lasten (vorher wartete das Auto bis 100 %, obwohl der
    Hausanschluss beides gleichzeitig hergab)."""
    if allow_drain:
        return None
    if ctx.battery_drain_unblockable:
        return "Hausspeicher entlädt und lässt sich nicht sperren"
    return None


def price_reason(ctx: ChargeContext, limit_ct: float | None = None) -> str:
    """Begründung fürs Dashboard, wenn ein Preisfenster genutzt wird – eine
    Formulierung für Wallbox und Warmwasser."""
    price = ctx.price_ct
    limit = limit_ct if limit_ct is not None else ctx.price_limit_ct
    if price is None:
        return "günstiger Tarif"
    if limit is None:
        return f"Netzladen: {price:.1f} ct – günstig laut Tarif"
    return f"Netzladen: {price:.1f} ct unter Grenze {limit:.1f} ct"


def price_window_open(ctx: ChargeContext, limit_ct: float | None = None) -> bool:
    """Ist der Strompreis gerade günstig genug?

    **Eine Grenze für alles** (seit 2.16). Vorher hatte jedes Gerät eine
    eigene Grenze, dazu kam die des Tarifs – im Diagnosebericht standen 20,
    25 und 15 ct nebeneinander, und niemand konnte sagen, welche gilt. Jetzt
    entscheidet allein der Tarif (`cheap_hour`, inklusive 'bei negativem
    Preis immer"). `limit_ct` bleibt nur für Tests erhalten."""
    if limit_ct is None:
        return bool(ctx.cheap_hour)
    return ctx.price_ct is not None and ctx.price_ct <= limit_ct


@dataclass
class WallboxSettings:
    #: pv_only | min_pv | pv_price | schedule | target
    #:
    #: 'fast" ist hier bewusst nicht mehr aufgefuehrt: Volllast ist ein
    #: *Eingriff* ('jetzt sofort"), keine Betriebsart. Als Dauermodus stand
    #: er in Konkurrenz zur Taste 'Jetzt laden" – zwei Bedienelemente mit
    #: derselben Wirkung, von denen eines den Speicher leersaugte. Der Wert
    #: wird weiterhin akzeptiert (Bestandsanlagen), aber nicht mehr angeboten.
    #:
    #: 'price" hiess frueher 'nur bei guenstigem Preis"; es verhaelt sich
    #: identisch zu 'pv_price" und bleibt als Synonym bestehen.
    mode: str = "pv_only"
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
    #: Nach einem Stopp frühestens nach dieser Zeit wieder im Überschuss
    #: starten. Jeder Start/Stopp kostet über den BLE-Proxy mehrere Befehle;
    #: am 07.10. flatterte das Auto im 10-Minuten-Takt.
    min_pause_s: float = 300.0
    #: Netzladen im Modus 'price" auch dann erlauben, wenn der Hausspeicher
    #: sich nicht sperren lässt (reines Monitoring) und gerade entlädt.
    #:
    #: Standard False, und das ist die teure Voreinstellung mit Absicht: Wer
    #: 'preisoptimiert" wählt, will billigen Netzstrom – nicht den eigenen,
    #: teuer geladenen Speicher ins Auto umfüllen. Ohne diese Sperre finanziert
    #: die günstige Stunde einen Speicher-Rundlauf mit zweifachem
    #: Wirkungsgradverlust. Wer trotzdem laden will (etwa weil der
    #: Wechselrichter selbst schon auf 'nicht entladen" steht), schaltet es
    #: hier frei.
    price_allow_battery_drain: bool = False
    # --- Stellgrößen-Quantisierung ---
    #: Kleinste Stromstufe der Box in A. Fast alle können nur ganze Ampere.
    current_step: float = 1.0
    #: "down" = abrunden (nie Netzbezug erzeugen; der Rest geht an eine fein
    #: modulierbare Last, siehe Lückenfüller). "nearest" = kaufmännisch runden.
    current_rounding: str = "down"
    #: Um wie viel Ampere der Sollstrom je Takt höchstens steigen darf.
    #: Nach unten wird nicht begrenzt – Schutz vor Batteriebezug geht vor.
    ramp_up_step_a: float = 3.0
    #: Wie lange derselbe Sollstrom anliegen muss, bevor aus Leistung und
    #: Strom ein Umrechnungsfaktor gelernt wird (siehe observe). Ein Fahrzeug
    #: folgt einem neuen Sollwert erst nach etlichen Sekunden; wer währenddessen
    #: misst, lernt einen zu kleinen Faktor.
    measure_settle_s: float = 45.0
    #: Nach wie vielen Takten mit gesendetem Ladestrom, aber ohne gemessene
    #: Leistung, das Fahrzeug als nicht ladebereit gilt (siehe _responding).
    idle_confirm_ticks: int = 6
    #: Wie lange danach nicht erneut versucht wird.
    idle_retry_s: float = 600.0
    # --- Prognose ---
    use_forecast: bool = True
    #: Wie lange eine Ladung bei absehbarer Erholung höchstens gehalten wird.
    forecast_hold_max_s: float = 900.0

    @classmethod
    def from_dict(cls, d: dict) -> "WallboxSettings":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d and d[f] is not None}
        return cls(**known)


#: Plausible Spanne für 'Leistung je Ampere': einphasig ≈ 230 W, dreiphasig
#: ≈ 690 W. Alles außerhalb stammt aus einer Messung während des Hochrampens
#: oder ist schlicht falsch – daraus wird nicht gelernt.
#:
#: Die Grenzen sind **asymmetrisch**, weil die beiden Irrtümer verschieden
#: teuer sind:
#:
#: * Zu **niedrig** geschätzt ist der gefährliche Fall: Der Regler rechnet
#:   dann zu viele Ampere aus demselben Budget, das Fahrzeug zieht mehr als
#:   die Sonne hergibt, und die Differenz kommt aus dem Hausspeicher. Genau
#:   das löst anschließend den Batterie-Schutz aus und beendet die Ladung.
#: * Zu **hoch** geschätzt kostet nur Ertrag: Es wird etwas zu wenig
#:   angefordert, und der Rest geht an den Lückenfüller.
#:
#: Nach unten gilt deshalb die Netzspannung nach EN 50160 (230 V ±10 %) als
#: harte Schranke – weniger kann eine echte Messung nicht ergeben. Nach oben
#: bleibt etwas mehr Luft.
WATTS_PER_AMP_TOLERANCE = (0.9, 1.15)


#: Phasen-Erkennung: so lange und so oft muss dieselbe Phasenzahl bei
#: echter Leistung gemeldet werden, bevor der Regler sie übernimmt.
PHASE_CONFIRM_S = 60.0
PHASE_CONFIRM_READINGS = 3
PHASE_MIN_POWER_W = 1000.0
#: So lange nach dem Setzen wird ein Eingriff nicht wegen des Zustands
#: (abgesteckt/voll) aufgehoben – der Messwert stammt noch von vorher.
OVERRIDE_GRACE_S = 120.0


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
        #: Warum das Netzladen gerade ausgesetzt wird (Speicher hat Vorrang).
        #: Wird in die Begründung der Entscheidung gehoben, damit im Dashboard
        #: steht, warum trotz 'Zielladung" oder günstigem Preis nicht aus dem
        #: Netz geladen wird. Ohne das sieht der Nutzer nur 'PV-Überschuss
        #: 0 W" und hält die Betriebsart für kaputt.
        self.grid_charge_blocked_reason: str | None = None
        self._last_phase_switch = -1e9
        self._forecast_hold_since: float | None = None
        #: Manueller Eingriff: None | "fast" | "stop". Endet immer – nach Zeit,
        #: beim Abstecken oder ('fast") am Ladeende. Siehe set_override.
        self.override: str | None = None
        self.override_since_utc: datetime | None = None
        self.override_until_utc: datetime | None = None
        #: (Art, Grund) des zuletzt beendeten Eingriffs – vom Loop einmal
        #: abgeholt und protokolliert.
        self._override_end: tuple[str, str] | None = None
        self.utcnow = lambda: datetime.now(timezone.utc)
        #: Phasen-Erkennung: Kandidat und seit wann er stabil gemeldet wird.
        self._phase_candidate: int | None = None
        self._phase_candidate_since = 0.0
        self._phase_candidate_votes = 0
        #: Wann zuletzt aus laufender Ladung gestoppt wurde (Mindestpause).
        self._stopped_at = -1e9
        #: Gemessene Leistung je Ampere (siehe observe). None = noch nichts
        #: gemessen, dann gilt der Nennwert aus Phasenzahl × 230 V.
        self._watts_per_amp: float | None = None
        #: Vom Gerät gemeldete Phasenzahl. Maßgeblich für den Nennwert, denn
        #: ein Fahrzeug kann dreiphasig laden, obwohl '1-phasig" eingestellt
        #: ist – dann wäre der konfigurierte Wert der falsche Bezug.
        self._phases_seen: int | None = None
        #: Zuletzt entschiedener Sollstrom – Basis für die Anstiegsbegrenzung.
        self._last_current_a = 0.0
        #: Takte in Folge, in denen Strom kommandiert wurde, aber keine
        #: Leistung floss (siehe _responding).
        self._silent_ticks = 0
        #: Bis wann nicht erneut versucht wird, nachdem das Fahrzeug als
        #: nicht ladebereit erkannt wurde.
        self._idle_until: float = -1e9
        #: Zuletzt vom Gerät gemeldeter Sollstrom und seit wann er unverändert
        #: anliegt – die Einschwingsperre für :meth:`observe`.
        self._setpoint_seen: float | None = None
        self._setpoint_stable_since: float = 0.0

    # ------------------------------------------------------------ Helpers

    def observe(self, data: WallboxData) -> None:
        """Umrechnungsfaktor Ampere → Watt bestimmen.

        Der Faktor ist keine Konstante: Die Netzspannung liegt real bei
        230–245 V, und ein Fahrzeug lädt je nach Kabel und Standort ein- oder
        dreiphasig. Ein starr gerechneter Sollstrom liegt deshalb systematisch
        daneben – zu hoch heißt Bezug aus dem Speicher, zu niedrig heißt
        Einspeisung.

        **Gemessen schlägt geschätzt.** Meldet das Gerät Spannung und
        Phasenzahl, ergibt sich der Faktor exakt aus beiden. Nur wenn diese
        Angaben fehlen, wird er aus Leistung und Sollstrom geschätzt – und
        dann eng um den Nennwert begrenzt.

        Die Begrenzung ist nicht theoretisch: Tesla meldet ``charger_power``
        als **ganzzahligen kW-Wert**. Bei 2 kW und 5 A Sollstrom ergibt die
        Division 400 W/A statt 230. Da der Faktor auch in die Mindestleistung
        eingeht, stieg damit die Startschwelle von 1,4 kW auf über 2,4 kW –
        das Fahrzeug lud erst bei doppeltem Überschuss los.

        **Nur im eingeschwungenen Zustand messen.** Ein Fahrzeug folgt einem
        neuen Sollstrom erst nach etlichen Sekunden. Wer währenddessen misst,
        teilt eine noch kleine Leistung durch den schon großen Sollstrom und
        lernt einen systematisch **zu kleinen** Faktor – und ein zu kleiner
        Faktor ist genau der teure Irrtum: Der Regler fordert daraufhin zu
        viele Ampere an, das Fahrzeug zieht mehr als die Sonne hergibt, der
        Hausspeicher deckt die Lücke, und der Batterie-Schutz beendet die
        Ladung. Danach beginnt dasselbe Spiel von vorn.

        Deshalb wird erst gelernt, wenn derselbe Sollstrom `measure_settle_s`
        lang unverändert anliegt. Die Plausibilitätsgrenzen allein reichten
        nicht: Ein Ramp-Messwert liegt oft noch innerhalb der Toleranz.
        """
        self._observe_phases(data)
        self._track_setpoint(data)
        # 1. Exakter Weg: Spannung × Phasen direkt vom Gerät. Das ist eine
        #    Messung, keine Schätzung – sie braucht keine Einschwingzeit.
        if data.voltage and data.phases_active:
            self._watts_per_amp = data.voltage * data.phases_active
            return
        # 2. Schätzung nur als Rückfall, und nur aus eindeutigen Messungen.
        if data.state != WallboxState.CHARGING:
            return
        amps = data.current_set
        if not amps or amps < 1.0 or data.power < 200.0:
            return
        if (self.clock() - self._setpoint_stable_since) < self.s.measure_settle_s:
            return  # Fahrzeug rampt noch – die Messung wäre zu niedrig
        measured = data.power / amps
        nominal = self._nominal_watts_per_amp()
        if not (nominal * WATTS_PER_AMP_TOLERANCE[0] <= measured <= nominal * WATTS_PER_AMP_TOLERANCE[1]):
            return  # zu grob aufgelöst oder unplausibel – nicht übernehmen
        self._watts_per_amp = (
            measured if self._watts_per_amp is None
            else 0.7 * self._watts_per_amp + 0.3 * measured
        )

    def _observe_phases(self, data: WallboxData) -> None:
        """Phasenzahl aus Messungen lernen – aber nur bestätigt.

        Am 07.10. meldete das Fahrzeug beim Ladebeginn einmal '1 Phase". Der
        Regler glaubte das sofort, rechnete mit 1,6 kW Mindestleistung und
        startete das Auto bei 1,6 kW Überschuss – es zog dreiphasig 4,2 kW,
        die Differenz kam aus Netz und Speicher, und nach der Stopp-
        verzögerung begann das Spiel von vorn (Flattern 14:25–14:56 UTC).

        Jetzt zählt nur, was über PHASE_CONFIRM_S stabil und bei echter
        Leistung gemeldet wird. Leistung und Strom schlagen dabei die
        gemeldete Phasenzahl: P / (U·I) ≈ 3 heißt dreiphasig, egal was das
        Fahrzeug behauptet."""
        if data.state != WallboxState.CHARGING or data.power < PHASE_MIN_POWER_W:
            return
        candidate = data.phases_active if data.phases_active in (1, 3) else None
        amps = float(data.current_set or 0.0)
        if amps >= 1.0:
            ratio = data.power / ((data.voltage or VOLTAGE) * amps)
            measured = 3 if ratio > 2.0 else 1
            # Nur widersprechen, wenn die Messung eindeutig ist (Sollstrom
            # stabil, keine Rampe): sonst bleibt die Meldung des Geräts.
            stable = (self.clock() - self._setpoint_stable_since) >= self.s.measure_settle_s
            if candidate is None or (stable and measured != candidate):
                candidate = measured
        if candidate is None:
            return
        now = self.clock()
        # Asymmetrisch: MEHR Phasen sofort übernehmen – zu wenige anzunehmen
        # ist der teure Irrtum (zu frühe Starts, Unterdeckung). WENIGER Phasen
        # erst nach Bestätigung (Fall 07.10.).
        current = self._phases_seen or self.phases
        if candidate > current:
            self._phases_seen = candidate
            self._phase_candidate = candidate
            self._phase_candidate_votes = PHASE_CONFIRM_READINGS
            return
        if candidate != self._phase_candidate:
            self._phase_candidate = candidate
            self._phase_candidate_since = now
            self._phase_candidate_votes = 1
            return
        self._phase_candidate_votes += 1
        if (self._phase_candidate_votes >= PHASE_CONFIRM_READINGS
                and now - self._phase_candidate_since >= PHASE_CONFIRM_S):
            self._phases_seen = candidate

    def set_override(self, kind: str | None, hours: float | None = None) -> None:
        """Manuellen Eingriff setzen ('fast"/'stop") oder aufheben (None).

        Jeder Eingriff endet: nach `hours`, beim Abstecken, und 'fast" auch am
        Ladeende. Ein 'Aus" um 02:43 hielt am 08.10. bis in den Nachmittag –
        3,5 kWh Sonnenstrom gingen ins Netz, weil niemand es zurückgenommen
        hatte."""
        now = self.utcnow()
        if kind is None:
            self.override = None
            self.override_since_utc = None
            self.override_until_utc = None
            return
        self.override = kind
        self.override_since_utc = now
        self.override_until_utc = now + timedelta(hours=hours) if hours else None
        self._override_end = None

    def _check_override_end(self, data: WallboxData) -> None:
        if self.override is None:
            return
        now = self.utcnow()
        reason = None
        if self.override_until_utc is not None and now >= self.override_until_utc:
            reason = "Zeit abgelaufen"
        # Zustand nur werten, wenn das Fahrzeug wirklich antwortet: Ein
        # schlafendes Auto ist nicht 'abgesteckt". Kurz nach dem Setzen gilt
        # der alte Messwert noch – nicht sofort wieder aufheben.
        settled = self.override_since_utc is None or (now - self.override_since_utc).total_seconds() >= OVERRIDE_GRACE_S
        if reason is None and settled and data.vehicle_reachable is not False:
            if data.state == WallboxState.IDLE:
                reason = "abgesteckt"
            elif self.override == "fast" and data.state == WallboxState.COMPLETE:
                reason = "Ladeziel erreicht"
        if reason is not None:
            self._override_end = (self.override, reason)
            self.set_override(None)

    def pop_override_end(self) -> tuple[str, str] | None:
        end, self._override_end = self._override_end, None
        return end

    def override_state(self) -> dict | None:
        if self.override is None:
            return None
        remaining = None
        if self.override_until_utc is not None:
            remaining = max(0, int((self.override_until_utc - self.utcnow()).total_seconds()))
        return {
            "kind": self.override,
            "since": self.override_since_utc.isoformat() if self.override_since_utc else None,
            "until": self.override_until_utc.isoformat() if self.override_until_utc else None,
            "remaining_s": remaining,
            "ends_on": "unplug" if self.override == "stop" else "complete",
        }

    def _track_setpoint(self, data: WallboxData) -> None:
        """Seit wann liegt derselbe Sollstrom unverändert an?

        Maßgeblich ist der vom Gerät *gemeldete* Sollstrom, nicht der zuletzt
        gesendete: Zwischen 'Befehl abgesetzt' und 'Fahrzeug hat übernommen'
        liegt bei einer Funkanbindung noch einmal Zeit."""
        amps = float(data.current_set or 0.0)
        if self._setpoint_seen is None or abs(amps - self._setpoint_seen) >= 0.5:
            self._setpoint_seen = amps
            self._setpoint_stable_since = self.clock()

    def _nominal_watts_per_amp(self) -> float:
        """Nennwert als Bezug für die Plausibilitätsgrenzen. Die vom Gerät
        gemeldete Phasenzahl schlägt die konfigurierte – sie ist die
        Wirklichkeit."""
        return (self._phases_seen or self.phases) * VOLTAGE

    def watts_per_amp(self) -> float:
        """Aktueller Umrechnungsfaktor A → W (gemessen, sonst Nennwert)."""
        nominal = self._nominal_watts_per_amp()
        if not self._watts_per_amp:
            return nominal
        # Harte Schranke: Der Faktor geht über _min_power in die Startschwelle
        # ein. Ein Ausreißer darf sie nicht verdoppeln.
        return max(nominal * WATTS_PER_AMP_TOLERANCE[0],
                   min(self._watts_per_amp, nominal * WATTS_PER_AMP_TOLERANCE[1]))

    def _min_power(self) -> float:
        return self.s.min_current * self.watts_per_amp()

    def _max_power(self) -> float:
        return self.s.max_current * self.watts_per_amp()

    def _amps_for(self, watts: float) -> float:
        return watts / self.watts_per_amp()

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
        return amps * self.watts_per_amp()

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

    def _stop(self, reason: str) -> WallboxDecision:
        """Einheitlicher Abbruch: Ladezustand zurücksetzen und Grund melden.

        An einer Stelle gebündelt, damit kein Rückgabepfad den zuletzt
        gesendeten Sollstrom stehen lässt – sonst würde die
        Anstiegsbegrenzung beim Wiederanlauf von einem veralteten Wert
        ausgehen."""
        if self.charging:
            self._stopped_at = self.clock()
        self.charging = False
        self._last_current_a = 0.0
        return WallboxDecision(enable=False, current_a=0, reason=reason)

    def _responding(self, data: WallboxData) -> bool:
        """Nimmt das Fahrzeug den gesendeten Ladestrom überhaupt an?

        Der gemeldete Zustand allein ist keine verlässliche Auskunft: Ein
        Fahrzeug, das per Funk nicht erreichbar ist oder gar nicht am Kabel
        hängt, meldet je nach Anbindung trotzdem 'verbunden". Die
        Prioritätskette reserviert dann Leistung, die nie abgerufen wird –
        und alles dahinter bekommt zu wenig.

        Deshalb wird nicht geglaubt, sondern gemessen: Fließt über mehrere
        Takte kein Strom, obwohl welcher kommandiert ist, gilt das Fahrzeug
        als nicht ladebereit. Danach eine Weile Ruhe, damit nicht im
        Sekundentakt weitergeprobt wird."""
        if not self.charging or self._last_current_a <= 0 or data.vehicle_reachable is False:
            self._silent_ticks = 0
            return True
        if data.power > 100.0:
            self._silent_ticks = 0
            return True
        self._silent_ticks += 1
        if self._silent_ticks < self.s.idle_confirm_ticks:
            return True
        self._idle_until = self.clock() + self.s.idle_retry_s
        self._silent_ticks = 0
        return False

    def _grid_charge_reason(
        self, mode: str, data: WallboxData, ctx: ChargeContext
    ) -> str | None:
        """Will dieser Modus gerade über den PV-Überschuss hinaus ins Netz
        greifen? Gibt die Begründung für die Anzeige zurück, oder None.

        Die Trennung ist wichtig: 'Welcher Modus will Netzstrom" ist eine
        Frage der Betriebsart, 'darf er" eine Frage des Speicherzustands.
        Vorher steckten beide in denselben if-Zweigen und die zweite fiel
        meistens unter den Tisch.
        """
        if mode == "fast":
            return "Volllast"
        if mode == "schedule" and ctx.schedule_active:
            return "Zeitfenster aktiv"
        if mode in ("price", "pv_price") and price_window_open(ctx):
            return price_reason(ctx)
        if mode == "target" and self._target_needs_grid(data, ctx):
            return "Zielladung: Deadline"
        return None

    def decide(self, budget_w: float, data: WallboxData, ctx: ChargeContext) -> WallboxDecision:
        s = self.s
        mode = self._effective_mode(ctx)
        # Zuerst messen – die Entscheidung unten rechnet dann schon mit dem
        # realen Watt-je-Ampere dieser Installation.
        self.observe(data)
        self._check_override_end(data)
        mode = self._effective_mode(ctx)

        # Fahrzeug antwortet gerade nicht (BLE): nichts umwerfen. Kein Befehl
        # (der Loop sendet dann nicht), keine Timer – sonst stoppte jeder
        # Funkaussetzer eine laufende Ladung und startete sie danach neu.
        if data.vehicle_reachable is False:
            return WallboxDecision(
                enable=self.charging, current_a=self._last_current_a if self.charging else 0.0,
                reason="Fahrzeug nicht erreichbar – letzter Stand bleibt", power_w=max(0.0, data.power),
            )

        if data.state in (WallboxState.IDLE, WallboxState.ERROR, WallboxState.COMPLETE):
            self.start_timer.reset()
            self.stop_timer.reset()
            return self._stop(f"state={data.state.value}")

        # Ein manueller Eingriff hebt die Sperre sofort auf – der Nutzer weiß
        # besser als wir, ob das Kabel steckt.
        if self.override is not None:
            self._idle_until = -1e9
        if not self._responding(data):
            self.start_timer.reset()
            return self._stop("Fahrzeug nimmt keine Leistung auf – nicht angesteckt?")
        if self.clock() < self._idle_until:
            return self._stop("Fahrzeug nicht ladebereit – nächster Versuch später")

        if mode == "off":
            return self._stop("manuell gestoppt")

        # Batterie-Sperre: Entladung der Hausbatterie nicht fürs Auto verwenden.
        # (Die Verteilung der Batterie-LADEleistung über die Prioritätskette
        # übernimmt der Control-Loop über getrennte Budget-Pools.)
        # Hat der Control-Loop die Entladung schon global abgezogen, wäre ein
        # zweiter Abzug hier doppelt gemoppelt.
        if (
            not ctx.battery_handled_globally
            and ctx.battery_soc is not None
            and ctx.battery_power < -50
        ):
            budget_w += ctx.battery_power  # battery_power ist negativ → Budget sinkt

        # --- Netzladen: ein Weg, ein Schutz -----------------------------
        #
        # Alle Zweige, die über den PV-Überschuss hinaus aus dem Netz ziehen,
        # laufen ab hier durch dieselbe Prüfung. Vorher hatte jeder seinen
        # eigenen (oder gar keinen), und `fast`, `target` und `schedule`
        # hatten keinen – siehe battery_blocks_grid_charge().
        #
        # Der manuelle Eingriff 'Jetzt laden" ist bewusst ausgenommen: Wer
        # ihn drückt, will jetzt laden und weiß, was er tut. Eine Automatik,
        # die den ausdrücklichen Wunsch des Nutzers überstimmt, ist keine
        # Hilfe, sondern ein Ärgernis.
        grid_reason = self._grid_charge_reason(mode, data, ctx)
        if grid_reason is not None:
            if self.override == "fast":
                self.charging = True
                return self._finish(
                    WallboxDecision(True, s.max_current, self._phase_target(1e9), grid_reason), ctx
                )
            blocked = battery_blocks_grid_charge(ctx, s.price_allow_battery_drain)
            grid_amps = s.max_current
            grid_target_w = 1e9
            if blocked is None and ctx.grid_budget_w is not None:
                # Netzanschluss-Budget: Was Geräte weiter vorn in der Kette
                # (oder die Batterie) schon beziehen, steht hier nicht mehr
                # zur Verfügung. Reicht der Rest nicht für den Mindeststrom,
                # bleibt es beim Überschussladen.
                grid_target_w = max(0.0, ctx.grid_budget_w)
                grid_amps = min(s.max_current, self._amps_for(grid_target_w))
                if grid_amps < s.min_current:
                    blocked = f"Netzanschluss-Limit: nur {grid_target_w:.0f} W frei"
            if blocked is not None:
                # Kein harter Stopp: Was die Sonne hergibt, wird weiter
                # geladen. Nur der Griff ins Netz entfällt.
                self.grid_charge_blocked_reason = blocked
                mode = "pv_only"
            else:
                self.charging = True
                self.grid_charge_blocked_reason = None
                return self._finish(
                    WallboxDecision(True, grid_amps, self._phase_target(grid_target_w), grid_reason), ctx
                )
        else:
            self.grid_charge_blocked_reason = None

        if mode == "schedule" and not ctx.schedule_active:
            return self._stop("außerhalb Zeitfenster")

        # 'price"/'pv_price"/'target" ohne Netzbedarf fallen auf reines
        # Überschussladen zurück. Die Sonne zu verschenken, nur weil der
        # Börsenpreis gestiegen ist, wäre die falsche Antwort.
        if mode in ("price", "pv_price", "target", "schedule"):
            mode = "pv_only"

        guaranteed_w = 0.0
        if mode == "min_pv":
            guaranteed_w = s.min_pv_current * self.watts_per_amp()

        # --- PV-Überschussregelung mit Hysterese ---
        surplus_for_start = budget_w
        if not self.charging:
            pause_left = s.min_pause_s - (self.clock() - self._stopped_at)
            if pause_left > 0 and guaranteed_w <= 0:
                self.start_timer.reset()
                wait = (f"{pause_left:.0f} s" if pause_left < 90
                        else f"{math.ceil(pause_left / 60):.0f} min")
                return self._stop(f"Pause nach Stopp – frühestens in {wait} wieder")
            # Startschwelle nie unter der echten Mindestleistung: Mit 1400 W
            # eingestellt, aber dreiphasig 4,1 kW Mindestlast, startete das
            # Auto in eine sichere Unterdeckung.
            threshold = max(s.start_threshold_w, self._min_power() * 1.05)
            if self.start_timer.check(surplus_for_start >= threshold) or guaranteed_w > 0:
                self.charging = True
                self.stop_timer.reset()
            else:
                # Die Sperre gehört in die Begründung: Sonst steht im
                # Dashboard 'warte auf Überschuss", obwohl der Preis stimmt
                # und der Nutzer nicht erkennen kann, dass der Speicher
                # Vorrang hat.
                if self.grid_charge_blocked_reason:
                    return self._stop(self.grid_charge_blocked_reason)
                return self._stop(waiting_reason(self.start_timer, budget_w))

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
            elif ctx.battery_protected:
                # Der fehlende Überschuss käme aus dem Speicher, nicht aus der
                # Sonne. Sofort abregeln statt die Stopp-Verzögerung
                # abzuwarten – jede Sekunde Halten entlädt die Batterie.
                self.start_timer.reset()
                return self._stop("Batterie hat Vorrang → Stopp")
            elif self.stop_timer.check(budget_w < self._min_power() - self.s.stop_threshold_w):
                self.start_timer.reset()
                return self._stop("Überschuss zu gering → Stopp")
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
        # Sanft hochfahren: Ein Fahrzeug folgt einem neuen Sollstrom erst nach
        # etlichen Sekunden. Springt der Regler in einem Takt von 6 auf 16 A,
        # sieht er die Wirkung erst Takte später am Netzpunkt – bis dahin hat
        # er längst mehr angefordert, als die Sonne hergibt, und der Speicher
        # deckt die Lücke. Nach unten wird bewusst nicht begrenzt.
        if d.enable and self._last_current_a >= self.s.min_current:
            d.current_a = min(d.current_a, self._last_current_a + self.s.ramp_up_step_a)
        if d.enable:
            d.current_a = self._quantize(d.current_a)
            d.power_w = self._decide_power(d.current_a)
        else:
            d.power_w = 0.0
        self._last_current_a = d.current_a if d.enable else 0.0
        return d

    # Rückwärtskompatibler Alias (frühere Versionen nutzten _limit)
    _limit = _finish


@dataclass
class WaterHeaterSettings:
    mode: str = "pv_only"          # pv_only|schedule|price|off
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
    #: Mit Solarüberschuss über die Zieltemperatur hinaus heizen, bis zu
    #: dieser Temperatur (°C). None/0 = aus. Der Speicher wird damit zum
    #: Wärmepuffer: Überschuss, der sonst ins Netz ginge, landet im Wasser.
    #: Nie aus dem Netz, nie per Zeitplan – nur echter Überschuss.
    surplus_temp_c: float | None = None
    #: Netzheizen erlauben, obwohl der Hausspeicher entlädt und sich nicht
    #: sperren lässt. Siehe `WallboxSettings.price_allow_battery_drain`.
    price_allow_battery_drain: bool = False
    # --- Boost: wann endet er? ------------------------------------------
    #: "time" = nach `boost_duration_min`, "temp" = bei `boost_temp_c`,
    #: "both" = was zuerst eintritt.
    boost_end_mode: str = "both"
    boost_duration_min: float = 60.0
    #: Harte Obergrenze für jeden Boost, auch im Temperaturmodus. Ein
    #: Temperaturfühler, der klemmt oder einen Fantasiewert meldet, darf den
    #: Stab nicht die ganze Nacht heizen lassen.
    boost_max_min: float = 180.0

    @classmethod
    def from_dict(cls, d: dict) -> "WaterHeaterSettings":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d and d[f] is not None}
        s = cls(**known)
        s.normalize()
        return s

    def normalize(self) -> None:
        """Werte in sichere Grenzen bringen – auch für Bestandsdaten, die
        vor der Validierung gespeichert wurden."""
        if self.boost_end_mode not in BOOST_END_MODES:
            self.boost_end_mode = "both"
        self.boost_temp_c = min(BOOST_TEMP_MAX_C, max(BOOST_TEMP_MIN_C, float(self.boost_temp_c)))
        self.boost_max_min = min(BOOST_MAX_MIN, max(5.0, float(self.boost_max_min)))
        self.boost_duration_min = min(self.boost_max_min, max(5.0, float(self.boost_duration_min)))
        if self.surplus_temp_c:
            self.surplus_temp_c = min(BOOST_TEMP_MAX_C, max(BOOST_TEMP_MIN_C, float(self.surplus_temp_c)))


#: Erlaubte Abschaltmodi des Boosts
BOOST_END_MODES = ("time", "temp", "both")
#: Sichere Grenzen für die Boost-Zieltemperatur. 80 °C liegen unter der
#: Sicherheitsabschaltung üblicher Warmwasserspeicher (≈ 90–95 °C) und über
#: der Legionellen-Schwelle (60 °C); höher braucht es im Haushalt nicht.
BOOST_TEMP_MIN_C = 30.0
BOOST_TEMP_MAX_C = 80.0
#: Längster Boost überhaupt (Minuten).
BOOST_MAX_MIN = 360.0
#: Plausibler Messbereich des Speicherfühlers. Werte außerhalb gelten als
#: Sensorfehler – dann entscheidet das Zeitlimit.
_TEMP_VALID_RANGE = (-10.0, 110.0)


def _valid_temp(temp: float | None) -> float | None:
    if temp is None:
        return None
    try:
        t = float(temp)
    except (TypeError, ValueError):
        return None
    if t != t or not (_TEMP_VALID_RANGE[0] <= t <= _TEMP_VALID_RANGE[1]):
        return None
    return t


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
        self._forecast_hold_since: float | None = None
        #: Warum das Netzheizen gerade nicht greift (Speicher-Vorrang,
        #: Netzanschluss-Limit) – für die Begründung im Überschusszweig.
        self.grid_blocked_reason: str | None = None
        # Manueller Override 'Warmwasser-Boost". Start- und Endzeit werden
        # gemerkt, damit er garantiert endet und die Oberfläche die Restzeit
        # zeigen kann. Wanduhr (UTC) statt monotoner Uhr, weil der Zustand
        # einen Neustart überlebt (siehe ControlLoop._restore_runtime).
        self._boost = False
        self.boost_started_utc: datetime | None = None
        self.boost_until_utc: datetime | None = None
        #: Grund des zuletzt beendeten Boosts – vom Loop einmal abgeholt und
        #: ins Protokoll geschrieben (siehe pop_boost_end).
        self._boost_end: str | None = None

    # ------------------------------------------------------------ Boost

    @property
    def boost(self) -> bool:
        return self._boost

    @boost.setter
    def boost(self, on: bool) -> None:
        if on:
            self.start_boost()
        else:
            self.stop_boost("manuell gestoppt")

    def start_boost(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._boost = True
        self._boost_end = None
        self.boost_started_utc = now
        # Endzeit beim Start festlegen. Im reinen Temperaturmodus gilt die
        # Sicherheitsgrenze – ein Boost ohne Ende gibt es nicht.
        minutes = self.s.boost_max_min if self.s.boost_end_mode == "temp" else self.s.boost_duration_min
        self.boost_until_utc = now + timedelta(minutes=minutes)

    def stop_boost(self, reason: str) -> None:
        if self._boost:
            self._boost_end = reason
        self._boost = False
        self.boost_started_utc = None
        self.boost_until_utc = None

    def pop_boost_end(self) -> str | None:
        """Grund des gerade beendeten Boosts – genau einmal."""
        reason, self._boost_end = self._boost_end, None
        return reason

    def boost_end_reason(self, temp: float | None, now: datetime | None = None) -> str | None:
        """Muss der laufende Boost jetzt enden? Gibt den Grund zurück.

        Regeln (in dieser Reihenfolge):
          1. Temperatur erreicht – nur in den Modi 'temp" und 'both" und nur
             mit gültigem Messwert.
          2. Endzeit erreicht (Modus 'time"/'both": Dauer; Modus 'temp":
             Sicherheitsgrenze).
          3. Kein gültiger Messwert im Modus 'temp": Dann gilt statt der
             Sicherheitsgrenze die normale Dauer – ohne Fühler weiß niemand,
             wann das Wasser warm ist, und die Zeit ist die einzige sichere
             Grenze.
        """
        if not self._boost:
            return None
        now = now or datetime.now(timezone.utc)
        s = self.s
        t = _valid_temp(temp)
        # Harte Sicherheitsgrenze in JEDEM Modus – auch 'nur nach Zeit":
        # Ein Boost darf den Speicher nie über die sichere Temperatur treiben,
        # egal wie viel Zeit noch übrig ist.
        if t is not None and t >= BOOST_TEMP_MAX_C:
            return f"Sicherheitstemperatur {BOOST_TEMP_MAX_C:.0f} °C erreicht"
        if s.boost_end_mode in ("temp", "both") and t is not None and t >= s.boost_temp_c:
            return f"Temperatur {s.boost_temp_c:.0f} °C erreicht"
        if self.boost_until_utc is not None and now >= self.boost_until_utc:
            if s.boost_end_mode == "temp":
                return f"Sicherheitsgrenze {s.boost_max_min:.0f} min erreicht"
            return f"Zeit abgelaufen ({s.boost_duration_min:.0f} min)"
        if s.boost_end_mode == "temp" and t is None and self.boost_started_utc is not None:
            if now >= self.boost_started_utc + timedelta(minutes=s.boost_duration_min):
                return (f"kein gültiger Temperaturwert – nach {s.boost_duration_min:.0f} min "
                        f"beendet (Zeitlimit als Rückfall)")
        return None

    def boost_state(self, now: datetime | None = None) -> dict | None:
        """Für Snapshot und Oberfläche: Restzeit, Ziel, Modus."""
        if not self._boost:
            return None
        now = now or datetime.now(timezone.utc)
        remaining = (
            max(0, int((self.boost_until_utc - now).total_seconds()))
            if self.boost_until_utc else None
        )
        return {
            "started": self.boost_started_utc.isoformat() if self.boost_started_utc else None,
            "until": self.boost_until_utc.isoformat() if self.boost_until_utc else None,
            "remaining_s": remaining,
            "end_mode": self.s.boost_end_mode,
            "target_temp_c": self.s.boost_temp_c,
        }

    def _forecast_active(self, ctx: ChargeContext) -> bool:
        # Wie bei der Wallbox: Wird die Batterie geschont, hieße 'Heizung über
        # den Wolkendurchzug halten' nichts anderes als den Speicher
        # leerzuziehen. Dann lieber abschalten und später neu starten.
        if ctx.battery_protected:
            self._forecast_hold_since = None
            return False
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

        # 'Aus" schlägt alles – auch einen noch laufenden Boost.
        #
        # Vorher wurde der Boost zuerst geprüft und lief mit voller Leistung
        # weiter, obwohl der Nutzer das Gerät gerade abgeschaltet hatte. Aus
        # dessen Sicht heizte der Stab 'manchmal trotzdem noch", und die
        # einzige Abhilfe war, den Boost separat zu finden und zu beenden.
        # Ein Ausschalter, den man zweimal betätigen muss, ist keiner.
        if s.mode == "off":
            self.on = False
            self.stop_boost("Gerät deaktiviert")
            return WaterHeaterDecision(0.0, "deaktiviert")

        # Boost: manueller Eingriff, schlägt Preis, Überschuss und Kette.
        # Begrenzt nur durch Netzanschluss (grid_budget_w) und Nennleistung.
        # Er sperrt den Hausspeicher NICHT – ein Hybrid-Wechselrichter kann
        # ihn wie jede Hauslast aus der Batterie decken (siehe docs).
        if self._boost:
            ended = self.boost_end_reason(temp)
            if ended is not None:
                self.stop_boost(ended)
            else:
                power = s.max_power_w
                reason = "Boost aktiv"
                if ctx.grid_budget_w is not None and ctx.grid_budget_w < power:
                    power = max(0.0, ctx.grid_budget_w)
                    reason = f"Boost aktiv (Netzanschluss-Limit: {power:.0f} W)"
                    if power < s.min_power_w:
                        power = 0.0
                return WaterHeaterDecision(power, reason)

        # Zieltemperatur erreicht – außer der Speicher darf als Wärmepuffer
        # weiter Überschuss aufnehmen ('Mit Überschuss heizen bis").
        #
        # Im Diagnosebericht gingen am 02.10. mittags 4,8 kW ins Netz: Batterie
        # voll, Auto nicht da, Wasser auf 60 °C. Ein Speicher, der bis 70 °C
        # weiterheizen darf, nimmt in dieser Lage noch einige kWh auf.
        surplus_only = False
        if temp is not None and temp >= s.target_temp_c:
            cap = self.surplus_cap()
            if cap is None or temp >= cap or s.mode not in ("pv_only", "price", "pv_price", "schedule"):
                self.on = False
                if cap is not None and temp >= cap:
                    return WaterHeaterDecision(0.0, f"Wärmepuffer voll ({cap:.0f} °C)")
                return WaterHeaterDecision(0.0, f"Zieltemperatur {s.target_temp_c:.0f} °C erreicht")
            surplus_only = True

        # Zeitplan = Sonne + feste Zeitfenster: Im Fenster heizt der Stab mit
        # voller Leistung (bis zur Zieltemperatur, begrenzt durch den
        # Netzanschluss), außerhalb nutzt er Überschuss wie 'Nur Sonne".
        # Vorher blieb er außerhalb der Fenster aus – mittags ging die Sonne
        # ins Netz, obwohl das Wasser kalt war.
        if s.mode == "schedule" and ctx.schedule_active and not surplus_only:
            power = s.max_power_w
            reason = "Zeitfenster aktiv"
            if ctx.grid_budget_w is not None and ctx.grid_budget_w < power:
                power = max(0.0, ctx.grid_budget_w)
                reason = f"Zeitfenster aktiv (Netzanschluss-Limit: {power:.0f} W)"
                if power < s.min_power_w:
                    power = 0.0
            self.on = power > 0
            self.start_timer.reset()
            self.stop_timer.reset()
            return WaterHeaterDecision(power, reason)

        if s.mode in ("price", "pv_price") and not surplus_only:
            if price_window_open(ctx):
                # Dieselbe Prüfung wie bei der Wallbox (battery_blocks_grid_charge):
                # Netzheizen nur, wenn der Speicher dabei nicht entlädt.
                blocked = battery_blocks_grid_charge(ctx, s.price_allow_battery_drain)
                power = s.max_power_w
                if blocked is None and ctx.grid_budget_w is not None:
                    power = min(power, max(0.0, ctx.grid_budget_w))
                    min_w = s.min_power_w if s.modulating else s.max_power_w
                    if power < min_w:
                        blocked = f"Netzanschluss-Limit: nur {ctx.grid_budget_w:.0f} W frei"
                if blocked is not None:
                    # Wie bei der Wallbox kein Dauerstopp: Der PV-Überschuss
                    # darunter darf weiter genutzt werden – über die normale
                    # Start-Hysterese, damit ein Stab, der eben noch aus dem
                    # Netz heizte, nicht über die Stopp-Verzögerung weiter
                    # Netz- oder Speicherstrom zieht.
                    if self.grid_blocked_reason is None:
                        self.on = False
                    self.grid_blocked_reason = blocked
                else:
                    self.grid_blocked_reason = None
                    self.on = True
                    self.stop_timer.reset()
                    return WaterHeaterDecision(power, price_reason(ctx))
            else:
                self.grid_blocked_reason = None
            # Preis über der Grenze: Netzheizen endet, Überschussbetrieb läuft
            # weiter. Die Zieltemperatur-Prüfung oben hat bereits gegriffen.

        # PV-Überschuss
        min_w = s.min_power_w if s.modulating else s.max_power_w
        if not self.on:
            if self.start_timer.check(budget_w >= max(s.start_threshold_w, min_w)):
                self.on = True
                self.stop_timer.reset()
            else:
                # Der Grund, warum nicht aus dem Netz geheizt wird, ist hier
                # die eigentliche Antwort – 'warte auf Überschuss" allein
                # ließe den Preismodus kaputt aussehen.
                if self.grid_blocked_reason:
                    return WaterHeaterDecision(0.0, self.grid_blocked_reason)
                return WaterHeaterDecision(0.0, waiting_reason(self.start_timer, budget_w))

        if budget_w < min_w:
            if ctx.battery_protected:
                # Ohne diesen Zweig würde die Stopp-Verzögerung den Heizstab
                # noch minutenlang auf Mindestleistung halten – bezahlt aus
                # dem Speicher, weil am Netzpunkt nichts übrig ist.
                self.on = False
                self.start_timer.reset()
                return WaterHeaterDecision(0.0, "Batterie hat Vorrang → Stopp")
            # Den Wärmepuffer nie über eine Wolke 'retten": Das Wasser hat
            # seine Zieltemperatur schon, jedes gehaltene Watt käme aus Netz
            # oder Speicher.
            if not surplus_only and self._forecast_active(ctx):
                return WaterHeaterDecision(min_w, "Wolkendurchzug – Heizung wird gehalten (Prognose: Erholung)")
            if self.stop_timer.check(True):
                self.on = False
                self.start_timer.reset()
                return WaterHeaterDecision(0.0, "Überschuss zu gering → Stopp")
            return WaterHeaterDecision(min_w, "Stopp-Verzögerung")
        self.stop_timer.reset()
        self._forecast_hold_since = None

        power = min(budget_w, s.max_power_w) if s.modulating else s.max_power_w
        if surplus_only:
            return WaterHeaterDecision(
                power, f"Wärmepuffer: Überschuss {budget_w:.0f} W, heizt bis {self.surplus_cap():.0f} °C"
            )
        return WaterHeaterDecision(power, f"PV-Überschuss {budget_w:.0f} W")

    def surplus_cap(self) -> float | None:
        """Obergrenze für Überschuss-Heizen über die Zieltemperatur hinaus,
        oder None, wenn nicht eingestellt."""
        cap = self.s.surplus_temp_c
        if not cap or cap <= self.s.target_temp_c:
            return None
        return min(float(cap), BOOST_TEMP_MAX_C)

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
        # 'pv_price"/'price" zählt mit: Außerhalb des günstigen Fensters läuft
        # das Gerät als reine Überschusslast und darf den Rest genauso
        # aufnehmen wie im Modus 'pv_only". Innerhalb des Fensters heizt es
        # ohnehin schon mit voller Leistung – dann ist `max_power_w -
        # power_w` gleich null und der Lückenfüller greift von selbst nicht.
        if self.s.mode not in ("pv_only", "price", "pv_price", "schedule"):
            return 0.0
        temp = data.temperature_c
        limit = self.surplus_cap() or self.s.target_temp_c
        if temp is not None and temp >= limit:
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


def battery_drain_alarm(battery_guard_w: float, cfg: RegulationConfig) -> bool:
    """Ist die Entladung so groß, dass laufende Lasten hart abschalten müssen?

    Zwei getrennte Fragen, die vorher eine einzige waren:

    * *Wie viel Budget haben die Lasten?* → immer um die volle Entladung
      gekürzt (:func:`protected_battery_discharge`). Der Sollwert bleibt
      dadurch ehrlich, die Lasten regeln ab.
    * *Muss sofort abgeschaltet werden?* → nur bei nennenswerter Entladung.

    Das Zusammenwerfen beider Fragen war der Grund für das Takten: Jede
    Entladung über 50 W löste einen Nothalt aus, obwohl Abregeln genügt hätte.
    Ein Nothalt kostet anschließend die volle Startschwelle plus Startzeit –
    er ist um ein Vielfaches teurer als die paar Wattstunden, die er spart.
    """
    return battery_guard_w > cfg.battery_drain_limit_w


#: Wie lange der Speicher ununterbrochen über `battery_drain_limit_w` entladen
#: muss, bevor laufende Lasten hart abgeschaltet werden.
#:
#: Der Schutz greift sofort, sobald die Entladung erkannt ist – aber 'erkannt'
#: braucht mehr als einen Messwert. Ohne diese Bestätigungszeit beendete jede
#: kurze Lastspitze im Haus (Wasserkocher, Wärmepumpen-Anlauf) und jeder
#: Regel-Überschwinger die Ladung sofort, ohne Stopp-Verzögerung. Danach
#: brauchte es wieder die volle Startschwelle für 60 s – der Regler taktete
#: sich im Minutenrhythmus selbst aus. Im Diagnosebericht waren das drei
#: Ladevorgänge in 17 Minuten bei 30 % verschenktem Solarstrom.
#:
#: Die Rechnung dahinter: 30 s Mindeststrom kosten den Speicher rund 12 Wh.
#: Ein vermiedener Fehlstopp bringt ein Vielfaches davon zurück, weil die
#: Ladung sonst minutenlang aussetzt, während die Sonne weiter scheint.
#: Erst *ansteigend* verzögert, *fallend* sofort – Schutz aufheben darf man
#: ohne Zögern, Schutz auslösen nicht.
BATTERY_PROTECT_CONFIRM_S = 30.0


def protected_battery_discharge(
    battery_soc: float | None, battery_power: float, cfg: RegulationConfig
) -> float:
    """Wie viel Batterie-Entladeleistung darf **nicht** als Überschuss gelten?

    `compute_budget` sieht nur den Netzpunkt. Entlädt sich die Hausbatterie,
    um Auto und Warmwasser zu versorgen, steht am Netz trotzdem 0 W – das
    Budget wirkt unverändert groß, obwohl die Sonne längst nicht mehr reicht.
    Abgezogen wird deshalb die volle Entladeleistung.

    Einzige Ausnahme ist die Experten-Einstellung `battery_ev_support_soc`:
    Ab diesem Ladestand darf der Speicher die Lasten mitversorgen. Der
    Hausverbrauch bleibt davon ohnehin unberührt."""
    if battery_power >= -BATTERY_IDLE_W:
        return 0.0
    discharge = -battery_power
    support = cfg.battery_ev_support_soc
    if support and 0 < support < 100 and battery_soc is not None and battery_soc >= support:
        return 0.0
    return discharge


def releasable_battery_charge(
    battery_soc: float | None, battery_power: float, cfg: RegulationConfig,
    battery_first: bool | None = None,
) -> float:
    """Ladeleistung des Speichers, die steuerbare Lasten ihm abnehmen dürfen.

    Die Ladeleistung ist am Netzpunkt **schon verbraucht** – sie steht gar
    nicht im Topf. Hat der Speicher Vorrang ('Batterie zuerst bis …" noch
    nicht erreicht), bleibt das so: null. Darüber dürfen die Lasten sie ihm
    abnehmen; dann wird sie dem Topf zugeschlagen, und der Speicher steckt
    zurück. Unter der Reserve nie."""
    if battery_power <= BATTERY_IDLE_W:
        return 0.0  # lädt nicht
    first = cfg.battery_first(battery_soc) if battery_first is None else battery_first
    if first:
        return 0.0
    if battery_soc is None or battery_soc < cfg.battery_reserve_soc:
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
