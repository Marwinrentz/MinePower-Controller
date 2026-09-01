"""Regelungslogik: Überschussverteilung, Hysterese, Lademodi, Phasenumschaltung.

Bewusst als reine Controller-Klassen ohne I/O implementiert (injizierbare
Uhr `clock`), damit die komplette Logik ohne Hardware unit-testbar ist.

Budget-Modell (wie bei etablierten Überschussreglern):
    budget = Σ(aktuelle Leistung aller steuerbaren Lasten) − P_grid − grid_target
P_grid ist geglättet, + = Bezug. Ein Export von 500 W bei 4 kW laufender
Ladung ergibt also budget = 4500 W → so viel PV-Überschuss steht den
steuerbaren Lasten insgesamt zur Verfügung.

Batterie (Standard: `battery_priority = True`):
    Entscheidend ist, was in `budget` schon steckt. Die Netzleistung ist eine
    *Bilanz*: Was der Speicher gerade lädt, ist am Netzpunkt bereits
    verbraucht und taucht in `budget` gar nicht mehr auf. Was er entlädt,
    steckt umgekehrt voll darin, obwohl es nicht aus der Sonne kommt.
    Deshalb ist die Korrektur einseitig:

      pool = budget − Entladeleistung + freigegebene Ladeleistung

    * **Entladen** wird abgezogen. Sonst würden Auto und Warmwasser aus dem
      Speicher gefüttert, während der Netzpunkt ausgeglichen aussieht.
    * **Laden** wird *nicht* abgezogen. Es steht schon nicht mehr im Budget;
      ein zweiter Abzug würde dieselbe Leistung doppelt wegrechnen. Der
      Speicher behält seine Ladeleistung damit trotzdem – niemand nimmt sie
      ihm weg, sie war nie Teil des verteilbaren Topfes.

    Genau hier lag ein Fehler mit sichtbaren Folgen: Mit dem doppelten Abzug
    schrumpfte der Topf bei jedem Takt weiter, in dem der Speicher lud. Bei
    6 kW Sonne, 0,7 kW Haus und einem Speicher, der nur 2 kW aufnehmen kann,
    bekam das Auto am Ende **nichts** – die restlichen 3,3 kW gingen ins Netz.
    Siehe :func:`releasable_battery_charge`.

    Der Hausverbrauch bleibt unberührt: Geschützt wird nur gegen die
    *steuerbaren* Lasten, den Haushalt darf der Speicher weiter versorgen.

    Nur wenn `battery_priority` bewusst abgeschaltet wird, greifen wieder die
    alten SoC-Schwellen (`ev_lock_soc`, `battery_reserve_soc`) – oberhalb der
    Reserve dürfen die Lasten dem Speicher seine Ladeleistung dann abnehmen.

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
    #: Hausspeicher hat immer Vorrang: Weder Lade- noch Entladeleistung zählt
    #: als Überschuss für steuerbare Lasten. Das ist die einzige Einstellung,
    #: die man dafür braucht – und der Standard.
    #:
    #: Vorher regelten zwei SoC-Schwellen dasselbe Thema, und beide hatten
    #: dieselbe Falle: Sie beschrieben, ab wann die Batterie *freigegeben*
    #: wird. Wer „nie anzapfen“ wollte, musste den Grenzwert exakt treffen –
    #: 99,9 statt 100 kehrte die Wirkung bei vollem Speicher um.
    battery_priority: bool = True
    battery_reserve_soc: float = 20.0
    #: Nur wirksam, wenn `battery_priority` abgeschaltet ist: Ab diesem
    #: Ladestand darf die Entladung als Überschuss verwendet werden.
    ev_lock_soc: float = 30.0
    #: Ab welcher **bestätigten** Entladeleistung laufende Lasten hart
    #: abgeschaltet werden (statt nur abzuregeln).
    #:
    #: Die Entladung wird immer vollständig vom Budget abgezogen – der
    #: Sollwert ist also in jedem Fall ehrlich. Diese Schwelle entscheidet nur
    #: über das Nothalt-Verhalten, und dafür war „jede Entladung über 50 W"
    #: viel zu scharf: Ein Hybrid-Wechselrichter deckt jede kleine Lücke aus
    #: dem Speicher, bevor er sie ans Netz weitergibt. Im Diagnosebericht
    #: beendete eine Entladung von 102 W eine laufende Ladung – bei einem
    #: Überschuss, der nur 5 W unter dem Mindeststrom lag. Die Ladung setzte
    #: danach acht Minuten aus, in denen 1,6 kW ins Netz gingen.
    #:
    #: 250 W entspricht der Größenordnung von `stop_threshold_w`: So viel
    #: Unterdeckung wird auf Mindeststrom ausgesessen, mehr nicht.
    battery_drain_limit_w: float = 250.0
    # --- Verschenkte Energie minimieren ---
    adaptive_deadband: bool = True  # Totband an die Wetterlage anpassen
    deadband_min_w: float = 40.0
    deadband_max_w: float = 400.0
    gap_fill_min_w: float = 25.0   # ab diesem Rest lohnt der Lückenfüller
    use_forecast: bool = True      # Prognose aktiv in Entscheidungen einbeziehen
    #: Was mit dem Hausspeicher geschieht, solange ein Gerät im Modus „price"
    #: aus dem Netz lädt: "hold" = sperren (weder laden noch entladen),
    #: "charge" = aus dem günstigen Netz vollladen, aber nicht entladen.
    #: Beides erfüllt die eigentliche Bedingung „nicht beziehen"; einzeln je
    #: Speicher über dessen `price_window_mode` übersteuerbar.
    price_window_battery: str = "hold"


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
    #: True, wenn der Hausspeicher während eines Netzladefensters gesperrt
    #: werden konnte (Modus HOLD/Zwangsladen oder Reserve auf 100 %). Nur dann
    #: ist garantiert, dass Netzladen wirklich aus dem Netz kommt und nicht
    #: aus dem Speicher. Siehe `grid_charge_blocked` in loop.py.
    battery_locked: bool = False
    #: True, wenn ein Hausspeicher vorhanden ist, gerade entlädt und sich
    #: NICHT sperren lässt (reines Monitoring). Dann würde Netzladen den
    #: Speicher leerziehen – genau das Gegenteil der Absicht.
    battery_drain_unblockable: bool = False


def price_window_open(ctx: ChargeContext, limit_ct: float) -> bool:
    """Ist der Strompreis gerade unter der eingestellten Grenze?

    Eine Funktion statt zweier Kopien in Wallbox- und Warmwasser-Regler: Beide
    Geräte müssen dieselbe Stunde als günstig bewerten, sonst lädt das Auto
    aus dem Netz, während der Heizstab dieselbe Stunde für zu teuer hält –
    und der Speicher wird gesperrt für ein Fenster, das nur die Hälfte der
    Geräte nutzt.

    `cheap_hour` kommt vom Tarif-Dienst (Tibber/aWATTar kennen ihre eigene
    Günstig-Schwelle), der Vergleich mit `limit_ct` ist die geräteeigene
    Grenze. Eine der beiden reicht.
    """
    if ctx.cheap_hour:
        return True
    return ctx.price_ct is not None and ctx.price_ct <= limit_ct


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
    #: Netzladen im Modus „price" auch dann erlauben, wenn der Hausspeicher
    #: sich nicht sperren lässt (reines Monitoring) und gerade entlädt.
    #:
    #: Standard False, und das ist die teure Voreinstellung mit Absicht: Wer
    #: „preisoptimiert" wählt, will billigen Netzstrom – nicht den eigenen,
    #: teuer geladenen Speicher ins Auto umfüllen. Ohne diese Sperre finanziert
    #: die günstige Stunde einen Speicher-Rundlauf mit zweifachem
    #: Wirkungsgradverlust. Wer trotzdem laden will (etwa weil der
    #: Wechselrichter selbst schon auf „nicht entladen" steht), schaltet es
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


#: Plausible Spanne für „Leistung je Ampere“: einphasig ≈ 230 W, dreiphasig
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
        #: Gemessene Leistung je Ampere (siehe observe). None = noch nichts
        #: gemessen, dann gilt der Nennwert aus Phasenzahl × 230 V.
        self._watts_per_amp: float | None = None
        #: Vom Gerät gemeldete Phasenzahl. Maßgeblich für den Nennwert, denn
        #: ein Fahrzeug kann dreiphasig laden, obwohl „1-phasig" eingestellt
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
        if data.phases_active in (1, 3):
            self._phases_seen = data.phases_active
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

    def _track_setpoint(self, data: WallboxData) -> None:
        """Seit wann liegt derselbe Sollstrom unverändert an?

        Maßgeblich ist der vom Gerät *gemeldete* Sollstrom, nicht der zuletzt
        gesendete: Zwischen „Befehl abgesetzt“ und „Fahrzeug hat übernommen“
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
        self.charging = False
        self._last_current_a = 0.0
        return WallboxDecision(enable=False, current_a=0, reason=reason)

    def _responding(self, data: WallboxData) -> bool:
        """Nimmt das Fahrzeug den gesendeten Ladestrom überhaupt an?

        Der gemeldete Zustand allein ist keine verlässliche Auskunft: Ein
        Fahrzeug, das per Funk nicht erreichbar ist oder gar nicht am Kabel
        hängt, meldet je nach Anbindung trotzdem „verbunden". Die
        Prioritätskette reserviert dann Leistung, die nie abgerufen wird –
        und alles dahinter bekommt zu wenig.

        Deshalb wird nicht geglaubt, sondern gemessen: Fließt über mehrere
        Takte kein Strom, obwohl welcher kommandiert ist, gilt das Fahrzeug
        als nicht ladebereit. Danach eine Weile Ruhe, damit nicht im
        Sekundentakt weitergeprobt wird."""
        if not self.charging or self._last_current_a <= 0:
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

    def decide(self, budget_w: float, data: WallboxData, ctx: ChargeContext) -> WallboxDecision:
        s = self.s
        mode = self._effective_mode(ctx)
        # Zuerst messen – die Entscheidung unten rechnet dann schon mit dem
        # realen Watt-je-Ampere dieser Installation.
        self.observe(data)

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
            and not s.use_battery_for_ev
        ):
            budget_w += ctx.battery_power  # battery_power ist negativ → Budget sinkt

        if mode == "fast" or (mode == "schedule" and ctx.schedule_active):
            self.charging = True
            return self._finish(WallboxDecision(True, s.max_current, self._phase_target(1e9), "Volllast"), ctx)

        if mode == "schedule" and not ctx.schedule_active:
            return self._stop("außerhalb Zeitfenster")

        if mode == "price":
            if price_window_open(ctx, s.price_limit_ct):
                # Netzladen heißt Netz – nicht Speicher. Zieht der Hausspeicher
                # gerade mit und lässt er sich nicht sperren, ist das kein
                # billiger Netzstrom, sondern ein Speicher-Rundlauf: einmal
                # geladen, einmal entladen, zweimal Wirkungsgrad verloren, und
                # abends steht das Haus ohne Reserve da.
                if ctx.battery_drain_unblockable and not s.price_allow_battery_drain:
                    self.charging = False
                    return self._stop(
                        "günstiger Tarif, aber Hausspeicher entlädt und lässt sich nicht sperren"
                    )
                self.charging = True
                return self._finish(
                    WallboxDecision(True, s.max_current, self._phase_target(1e9), "günstiger Tarif"), ctx
                )
            # Preis wieder über der Grenze: Netzladen endet hier. Weiter geht es
            # als reines Überschussladen – die Sonne zu verschenken, nur weil
            # der Börsenpreis gestiegen ist, wäre die falsche Antwort.
            mode = "pv_only"

        if mode == "target":
            if self._target_needs_grid(data, ctx):
                self.charging = True
                return self._finish(
                    WallboxDecision(True, s.max_current, self._phase_target(1e9), "Zielladung: Deadline"), ctx
                )
            mode = "pv_only"

        guaranteed_w = 0.0
        if mode == "min_pv":
            guaranteed_w = s.min_pv_current * self.watts_per_amp()

        # --- PV-Überschussregelung mit Hysterese ---
        surplus_for_start = budget_w
        if not self.charging:
            if self.start_timer.check(surplus_for_start >= max(s.start_threshold_w, self._min_power())) or guaranteed_w > 0:
                self.charging = True
                self.stop_timer.reset()
            else:
                return self._stop(f"warte auf Überschuss ({budget_w:.0f} W)")

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
    #: Preisgrenze für den Modus „price" (ct/kWh) – wie bei der Wallbox.
    price_limit_ct: float = 15.0
    #: Netzheizen erlauben, obwohl der Hausspeicher entlädt und sich nicht
    #: sperren lässt. Siehe `WallboxSettings.price_allow_battery_drain`.
    price_allow_battery_drain: bool = False

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
        # Wie bei der Wallbox: Wird die Batterie geschont, hieße „Heizung über
        # den Wolkendurchzug halten“ nichts anderes als den Speicher
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

        # „Aus" schlägt alles – auch einen noch laufenden Boost.
        #
        # Vorher wurde der Boost zuerst geprüft und lief mit voller Leistung
        # weiter, obwohl der Nutzer das Gerät gerade abgeschaltet hatte. Aus
        # dessen Sicht heizte der Stab „manchmal trotzdem noch", und die
        # einzige Abhilfe war, den Boost separat zu finden und zu beenden.
        # Ein Ausschalter, den man zweimal betätigen muss, ist keiner.
        if s.mode == "off":
            self.on = False
            self.boost = False
            return WaterHeaterDecision(0.0, "deaktiviert")

        if self.boost:
            if temp is not None and temp >= s.boost_temp_c:
                self.boost = False
            else:
                return WaterHeaterDecision(s.max_power_w, "Boost aktiv")

        if temp is not None and temp >= s.target_temp_c:
            self.on = False
            return WaterHeaterDecision(0.0, f"Zieltemperatur {s.target_temp_c:.0f} °C erreicht")

        if s.mode == "schedule":
            if ctx.schedule_active:
                return WaterHeaterDecision(s.max_power_w, "Zeitfenster aktiv")
            return WaterHeaterDecision(0.0, "außerhalb Zeitfenster")

        if s.mode == "price":
            if price_window_open(ctx, s.price_limit_ct):
                # Wie bei der Wallbox: Netzheizen darf nicht heißen, den
                # Hausspeicher ins Warmwasser umzufüllen.
                if ctx.battery_drain_unblockable and not s.price_allow_battery_drain:
                    self.on = False
                    return WaterHeaterDecision(
                        0.0, "günstiger Tarif, aber Hausspeicher entlädt und lässt sich nicht sperren"
                    )
                self.on = True
                self.stop_timer.reset()
                return WaterHeaterDecision(s.max_power_w, "günstiger Tarif")
            # Preis über der Grenze: Netzheizen endet, Überschussbetrieb läuft
            # weiter. Die Zieltemperatur-Prüfung oben hat bereits gegriffen.

        # PV-Überschuss
        min_w = s.min_power_w if s.modulating else s.max_power_w
        if not self.on:
            if self.start_timer.check(budget_w >= max(s.start_threshold_w, min_w)):
                self.on = True
                self.stop_timer.reset()
            else:
                return WaterHeaterDecision(0.0, f"warte auf Überschuss ({budget_w:.0f} W)")

        if budget_w < min_w:
            if ctx.battery_protected:
                # Ohne diesen Zweig würde die Stopp-Verzögerung den Heizstab
                # noch minutenlang auf Mindestleistung halten – bezahlt aus
                # dem Speicher, weil am Netzpunkt nichts übrig ist.
                self.on = False
                self.start_timer.reset()
                return WaterHeaterDecision(0.0, "Batterie hat Vorrang → Stopp")
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
        # „price" zählt mit: Außerhalb des günstigen Fensters läuft das Gerät
        # als reine Überschusslast und darf den Rest genauso aufnehmen wie im
        # Modus „pv_only". Innerhalb des Fensters heizt es ohnehin schon mit
        # voller Leistung – dann ist `max_power_w - power_w` gleich null und
        # der Lückenfüller greift von selbst nicht.
        if self.s.mode not in ("pv_only", "price"):
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
#: Der Schutz greift sofort, sobald die Entladung erkannt ist – aber „erkannt“
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

    Der Kern des Problems: `compute_budget` sieht nur den Netzpunkt. Entlädt
    sich die Hausbatterie, um Auto und Warmwasser zu versorgen, steht am Netz
    trotzdem 0 W – das Budget wirkt also unverändert groß, obwohl die Sonne
    längst nicht mehr reicht. Ohne diese Korrektur füttert der Speicher die
    steuerbaren Lasten, bis er leer ist.

    Was hier abgezogen wird, ist genau die Entladeleistung.

    Wichtig: Der Hausverbrauch bleibt davon unberührt. Geschützt wird nur
    gegen die *steuerbaren* Lasten; die Batterie darf weiterhin ganz normal
    den Haushalt versorgen – dafür ist sie da.
    """
    if battery_power >= -BATTERY_IDLE_W:
        return 0.0
    discharge = -battery_power
    # Standardfall: Der Speicher hat immer Vorrang, unabhängig vom Ladestand.
    # Bewusst ohne Schwellenvergleich – jede Schwelle hatte bisher genau am
    # Grenzwert die umgekehrte Wirkung (99,9 bei vollem Speicher = ungeschützt).
    if cfg.battery_priority:
        return discharge
    if cfg.ev_lock_soc >= 100:
        return discharge
    if battery_soc is not None and battery_soc >= cfg.ev_lock_soc:
        return 0.0  # Nutzer erlaubt die Entladung ab diesem Ladestand
    return discharge


def releasable_battery_charge(
    battery_soc: float | None, battery_power: float, cfg: RegulationConfig
) -> float:
    """Ladeleistung des Speichers, die steuerbare Lasten ihm abnehmen dürfen.

    Gegenstück zu :func:`protected_battery_discharge` – aber mit einer
    wichtigen Asymmetrie, die vorher übersehen wurde.

    Die Ladeleistung des Speichers ist am Netzpunkt **schon verbraucht**:
    ``budget = P_pv − P_haus − P_laden``. Sie steht also gar nicht im Topf und
    muss auch nicht daraus entfernt werden. Wer sie trotzdem abzieht, rechnet
    dieselben Watt zweimal weg. Das war messbar:

        6 kW Sonne, 0,7 kW Haus, Speicher nimmt max. 2 kW.
        budget = 6000 − 700 − 2000 = 3300 W  ← so viel ist wirklich frei
        alt:  pool = 3300 − 2000 = 1300 W → Auto startet nie (braucht 1380 W),
              Speicher bleibt bei 2 kW, **3,3 kW gehen ins Netz**
        neu:  pool = 3300 W → Auto lädt mit 14 A, Netz bleibt bei null

    Die Rückkopplung machte es schlimmer: Je weniger die Lasten nahmen, desto
    mehr lud der Speicher, desto größer der Abzug – bis die Lasten bei null
    standen und der Überschuss vollständig verschenkt wurde.

    Vorrang des Speichers heißt deshalb schlicht: **null**. Seine Ladeleistung
    wird ihm nicht weggenommen, aber auch nicht doppelt verbucht. (So lösen es
    auch etablierte Überschussregler: Der Batterieanteil wird bei aktivem
    Vorrang auf 0 gesetzt, nicht subtrahiert.)

    Ohne `battery_priority` gilt wieder die Schwelle `battery_reserve_soc`:
    Oberhalb davon darf die Kette dem Speicher seine Ladeleistung abnehmen –
    dann wird sie dem Topf *zugeschlagen*, denn dafür muss der Speicher
    zurückstecken.
    """
    if battery_power <= BATTERY_IDLE_W:
        return 0.0  # lädt nicht
    if cfg.battery_priority:
        return 0.0  # der Speicher behält, was er nimmt
    if battery_soc is None or battery_soc < cfg.battery_reserve_soc:
        return 0.0  # unter der Reserve bleibt der Speicher unangetastet
    return battery_power


def wasted_power(grid_power_smoothed: float, cfg: RegulationConfig) -> float:
    """Verschenkter Solarstrom in W: Einspeisung über dem Netz-Sollwert.

    Das ist Überschuss, der am Netzpunkt vorbeigeflossen ist, ohne dass ihn
    eine steuerbare Last aufgenommen hat. Bei `grid_target_w = 0` (Standard)
    ist das schlicht die aktuelle Einspeiseleistung. Ist bewusst eine
    Mindest-Einspeisung eingestellt (negativer Sollwert), zählt nur, was
    darüber hinausgeht."""
    return max(0.0, cfg.grid_target_w - grid_power_smoothed)
