"""Was tut der Hausspeicher gerade – und warum? Eine Tabelle, eine Funktion.

Bis 2.15 entschieden darüber vier Stellen mit eigenen Schaltern:
Preisfenster ('sperren"/'vollladen"), Batterie-Vorrang mit Freigabe-SoC,
Netzladen der Batterie und die Reserve. Sie widersprachen sich – am
08.10. lud der Speicher 1,5 h mit 4–5 kW aus dem Netz (33 ct), obwohl
'Netzladen" aus war: Ein Auto im Modus 'PV + Preis" hatte laut Einstellung
ein günstiges Fenster, zog aber selbst gar keinen Strom (voll bzw. nicht
erreichbar), und 'vollladen" gab dem Speicher das ganze Netzbudget.

Jetzt gilt in dieser Reihenfolge (erste passende Zeile gewinnt):

====  =======================================  ==========================
Rang  Lage                                     Speicher
====  =======================================  ==========================
1     manueller Befehl (Dashboard)             wie befohlen, endet immer
2     Netzladen an + Preis günstig + unter      lädt aus dem Netz
      Ziel + Sonne reicht laut Prognose nicht
3     eine Last läuft, WEIL der Strom gerade    Entladung gesperrt
      günstig ist (Preisfenster)
4     optional (Experte): Sofort laden, Boost,  Entladung gesperrt
      Zeitplan, Zielladung, Eigenbetrieb
      (ELWA-Programm, Auto-Selbststart)
5     Ladestand an/unter der Reserve und der    Entladung gesperrt
      Wechselrichter hält sie nicht selbst
6     sonst                                     Automatik (Eigenverbrauch)
====  =======================================  ==========================

Warum Zeile 3 Pflicht ist und Zeile 4 nicht: Lädt das Auto, weil Netzstrom
gerade billig ist, wäre es Unsinn, den teuer gespeicherten Strom dafür
herzugeben – der Speicher soll die teuren Stunden decken. Bei 'Sofort",
Boost oder dem ELWA-Programm um 6 Uhr ist der Netzstrom dagegen nicht
billig. Die Wiedergabe der Diagnose-Woche (tools/replay.py) zeigt: Sperrt
man den Speicher auch dafür, bleibt er voll, und am nächsten Mittag geht die
Sonne ins Netz – mehr verschenkt, mehr Netzbezug zum vollen Preis.

'Entladung gesperrt" heißt nicht 'Speicher aus": Lädt die Sonne, darf er
laden. Er gibt nur nichts an die geschützten Lasten ab. Das Haus versorgt er
weiter (siehe :class:`DischargeGuard`).

Alle Funktionen hier sind rein (keine Geräte, keine Uhr außer der
übergebenen) und damit vollständig testbar; der Control-Loop setzt nur um.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..drivers.base import WallboxState


class BatteryIntent(str, Enum):
    AUTO = "auto"                  # Wechselrichter regelt selbst (Eigenverbrauch)
    NO_DISCHARGE = "no_discharge"  # nicht an geschützte Lasten abgeben, PV-Laden erlaubt
    HOLD = "hold"                  # komplett gesperrt (weder laden noch entladen)
    CHARGE = "charge"              # aktiv laden (Netz + PV)
    DISCHARGE = "discharge"        # aktiv entladen (nur manuell)


#: Klartext für Oberfläche und Protokoll
INTENT_LABELS = {
    BatteryIntent.AUTO: "Automatik",
    BatteryIntent.NO_DISCHARGE: "Entladung gesperrt",
    BatteryIntent.HOLD: "gesperrt",
    BatteryIntent.CHARGE: "lädt aus dem Netz",
    BatteryIntent.DISCHARGE: "entlädt",
}


@dataclass(frozen=True)
class BatteryInputs:
    soc: float | None
    #: Die einzige Reserve (Einstellungen → Batterie)
    reserve_soc: float = 20.0
    #: Hält der Wechselrichter die Reserve selbst (Min-SoC geschrieben und
    #: bestätigt)? Dann muss MinePower an der Reserve nichts tun.
    inverter_holds_reserve: bool = False
    price_cheap: bool = False
    price_ct: float | None = None
    grid_charge_enabled: bool = False
    grid_charge_soc: float = 50.0
    #: Erwarteter nutzbarer Solarüberschuss bis morgen früh (kWh), None = keine Prognose
    forecast_surplus_kwh: float | None = None
    use_forecast: bool = True
    capacity_kwh: float | None = None
    #: Lasten, die gerade laufen (oder gleich anfangen), WEIL der Strom günstig ist
    grid_loads: tuple[str, ...] = ()
    #: Andere Lasten mit absichtlichem Netzstrom (Sofort, Boost, Zeitplan, Zielladung)
    other_loads: tuple[str, ...] = ()
    #: Lasten im Eigenbetrieb (gegen den Befehl)
    foreign_loads: tuple[str, ...] = ()
    #: Experte: Speicher auch für `other_loads` und `foreign_loads` sperren
    protect_other_loads: bool = False
    #: Manueller Befehl (Intent) oder None
    manual: BatteryIntent | None = None
    #: Lief im letzten Takt schon Netzladen? (Hysterese am Ziel-SoC)
    was_grid_charging: bool = False
    #: Netzbudget, das der Speicher fürs Netzladen bekommen kann (W)
    grid_budget_w: float | None = None


@dataclass(frozen=True)
class BatteryPlan:
    intent: BatteryIntent
    reason: str
    #: Woher die Entscheidung kommt (für Oberfläche, Protokoll, Tests)
    source: str = "auto"
    #: Leistung für CHARGE (W), None = Gerätegrenze
    power_w: float | None = None
    #: Welche Lasten geschützt werden
    protected: tuple[str, ...] = field(default_factory=tuple)


#: Hysterese am Ziel des Netzladens: Erst unter Ziel − 3 % neu starten.
GRID_CHARGE_RESTART_GAP = 3.0
#: Mindestbudget, damit sich Zwangsladen lohnt (W)
GRID_CHARGE_MIN_W = 200.0
#: Reicht die Prognose für so viel des fehlenden Ladestands, wird nicht
#: aus dem Netz geladen (Sicherheitsabschlag auf die Prognose).
FORECAST_TRUST = 0.8


def _names(items: tuple[str, ...]) -> str:
    return ", ".join(items[:2]) + (" …" if len(items) > 2 else "")


def grid_charge_wanted(i: BatteryInputs) -> tuple[bool, str]:
    """Soll der Speicher jetzt aus dem Netz laden? (Zeile 2 der Tabelle)"""
    if not i.grid_charge_enabled:
        return False, "Netzladen aus"
    if i.soc is None:
        return False, "Ladestand unbekannt"
    if not i.price_cheap:
        return False, "Strom nicht günstig"
    target = i.grid_charge_soc
    if i.soc >= target:
        return False, f"Ziel {target:.0f} % erreicht"
    if not i.was_grid_charging and i.soc >= target - GRID_CHARGE_RESTART_GAP:
        return False, f"knapp unter Ziel {target:.0f} %"
    # Ohne bekannte Kapazität ist kein Prognose-Abgleich möglich; dann
    # entscheiden allein Preis, Ziel und Netzbudget.
    if i.use_forecast and i.forecast_surplus_kwh is not None and i.capacity_kwh:
        capacity = i.capacity_kwh
        missing_kwh = (target - i.soc) / 100.0 * capacity
        if i.forecast_surplus_kwh * FORECAST_TRUST >= missing_kwh:
            return False, (f"Sonne füllt den Speicher bis zum Ziel "
                           f"(Prognose {i.forecast_surplus_kwh:.1f} kWh)")
    if i.grid_budget_w is not None and i.grid_budget_w < GRID_CHARGE_MIN_W:
        return False, f"Netzanschluss ausgelastet ({i.grid_budget_w:.0f} W frei)"
    return True, f"günstiger Strom, bis {target:.0f} %"


def plan_battery(i: BatteryInputs) -> BatteryPlan:
    """Die Entscheidungstabelle (siehe Moduldoku)."""
    # 1. Manuell
    if i.manual is not None:
        if i.manual == BatteryIntent.DISCHARGE and i.soc is not None and i.soc <= i.reserve_soc:
            return BatteryPlan(BatteryIntent.NO_DISCHARGE, "Reserve erreicht – manuelles Entladen ruht", "reserve")
        return BatteryPlan(i.manual, f"manuell: {INTENT_LABELS[i.manual]}", "manual")

    # 2. Netzladen
    wanted, why = grid_charge_wanted(i)
    if wanted:
        return BatteryPlan(BatteryIntent.CHARGE, f"lädt aus dem Netz: {why}", "grid_charge",
                           power_w=i.grid_budget_w)
    if why.startswith("Netzanschluss ausgelastet"):
        # Laden wäre richtig, geht aber gerade nicht: Dann wenigstens nicht
        # in der günstigen Stunde entladen – das Haus läuft billig aus dem Netz.
        return BatteryPlan(BatteryIntent.NO_DISCHARGE, f"Netzladen pausiert: {why}", "grid_charge")

    # 3. Lasten mit günstigem Netzstrom – immer
    if i.grid_loads:
        return BatteryPlan(
            BatteryIntent.NO_DISCHARGE,
            f"Entladung gesperrt: {_names(i.grid_loads)} lädt günstig aus dem Netz",
            "grid_loads", protected=i.grid_loads,
        )

    # 4. Sofort/Boost/Eigenbetrieb – nur auf Wunsch (Experte)
    if i.protect_other_loads and (i.other_loads or i.foreign_loads):
        names = i.other_loads + i.foreign_loads
        return BatteryPlan(
            BatteryIntent.NO_DISCHARGE,
            f"Entladung gesperrt: {_names(names)} läuft mit Netzstrom",
            "foreign_loads" if not i.other_loads else "other_loads", protected=names,
        )

    # 5. Reserve
    if i.soc is not None and i.soc <= i.reserve_soc and not i.inverter_holds_reserve:
        return BatteryPlan(
            BatteryIntent.NO_DISCHARGE, f"Reserve {i.reserve_soc:.0f} % erreicht", "reserve",
        )

    # 6. Automatik – mit Grund, falls Netzladen an ist, aber gerade nicht greift
    if i.grid_charge_enabled and i.price_cheap and why not in ("Netzladen aus", "Strom nicht günstig"):
        return BatteryPlan(BatteryIntent.AUTO, f"Automatik – {why}", "auto")
    return BatteryPlan(BatteryIntent.AUTO, "Automatik", "auto")


# --------------------------------------------------------------------- Lasten

def wallbox_grid_intent(
    *, mode: str, override: str | None, state: WallboxState | None, reachable: bool | None,
    price_cheap: bool, schedule_active: bool = False, target_needs_grid: bool = False,
) -> str | None:
    """Will dieser Ladepunkt gerade (oder gleich) Netzstrom ziehen? Grund oder None.

    Aus Zustand und Einstellung abgeleitet, nicht aus der Entscheidung des
    Reglers – der Speicher muss gesichert sein, BEVOR die Last anspringt.
    Anders als bis 2.15 zählt ein Fenster nur, wenn das Fahrzeug überhaupt
    laden kann: angesteckt, erreichbar, nicht voll. Ein volles oder
    schlafendes Auto 'öffnete" bisher ein Preisfenster, in dem dann der
    Speicher aus dem Netz vollgeladen wurde."""
    if override == "stop":
        return None
    if reachable is False:
        return None
    if state not in (WallboxState.CONNECTED, WallboxState.CHARGING):
        return None
    if override == "fast":
        return "Sofort laden"
    if mode == "off":
        return None
    if mode in ("price", "pv_price") and price_cheap:
        return "günstiger Strom"
    if mode == "schedule" and schedule_active:
        return "Zeitplan"
    if mode == "target" and target_needs_grid:
        return "Zielladung"
    if mode == "fast":
        return "Volllast"
    return None


def heater_grid_intent(
    *, mode: str, boost: bool, temperature_c: float | None, target_c: float,
    price_cheap: bool, schedule_active: bool = False,
) -> str | None:
    """Will der Heizstab gerade (oder gleich) Netzstrom ziehen? Grund oder None."""
    if mode == "off":
        return None
    if boost:
        return "Boost"
    below = temperature_c is None or temperature_c < target_c
    if mode in ("price", "pv_price") and price_cheap and below:
        return "günstiger Strom"
    if mode == "schedule" and schedule_active and below:
        return "Zeitplan"
    return None


# --------------------------------------------------------------------- Aktor

class GuardMode(str, Enum):
    AUTO = "auto"          # Speicher frei (PV deckt alles, er darf laden)
    HOLD = "hold"          # Speicher ruht
    HOUSE = "house"        # Speicher entlädt nur so viel, wie das Haus braucht


@dataclass
class GuardStep:
    mode: GuardMode
    #: Entladeleistung für HOUSE (W)
    power_w: float = 0.0


class DischargeGuard:
    """Setzt 'Entladung gesperrt" in Gerätebefehle um.

    * Deckt die Sonne Haus und Lasten, bleibt der Speicher in der Automatik
      und darf laden (Einspeisung ⇒ frei).
    * Fehlt Leistung, würde ein Hybrid-Wechselrichter sie aus dem Speicher
      nehmen – auch für das Auto. Dann entlädt er nur noch so viel, wie das
      Haus selbst braucht (Zwangsentladen mit der Hauslast). Den Rest liefert
      das Netz. Ist der Hausbedarf klein, ruht er ganz.

    Wechsel sind gedämpft: in den Schutz sofort (jede Sekunde Entladung in
    die Last kostet), heraus erst nach anhaltender Einspeisung, und nie
    häufiger als MIN_DWELL_S – jeder Wechsel sind drei Modbus-Schreibzugriffe.
    """

    #: Entladung in die Lasten, ab der eingegriffen wird (W)
    DISCHARGE_W = 150.0
    #: Einspeisung, ab der der Schutz gelockert wird (W) – und wie lange
    RELEASE_EXPORT_W = 300.0
    RELEASE_S = 60.0
    #: Hauslast, ab der sich Zwangsentladen fürs Haus lohnt (W)
    HOUSE_MIN_W = 300.0
    MIN_DWELL_S = 20.0

    def __init__(self) -> None:
        self.mode = GuardMode.AUTO
        self._changed_at = -1e9
        self._export_since: float | None = None

    def reset(self) -> None:
        self.mode = GuardMode.AUTO
        self._export_since = None

    def step(self, now: float, *, battery_w: float, grid_w: float, pv_w: float,
             house_w: float, expected_load_w: float = 0.0, soc: float | None = None,
             reserve_soc: float = 0.0) -> GuardStep:
        """Ein Takt. `house_w` ohne steuerbare Lasten und ohne Speicher;
        `expected_load_w` = Last, die gerade erst anspringt (vorbeugend)."""
        house_deficit = max(0.0, house_w - pv_w)
        at_reserve = soc is not None and soc <= reserve_soc
        house_step = (
            GuardStep(GuardMode.HOUSE, house_deficit)
            if house_deficit >= self.HOUSE_MIN_W and not at_reserve
            else GuardStep(GuardMode.HOLD)
        )
        dwell_ok = (now - self._changed_at) >= self.MIN_DWELL_S

        if self.mode == GuardMode.AUTO:
            # Entlädt er schon – oder wird er gleich entladen, weil eine Last
            # anspringt, für die die Sonne nicht reicht?
            predicted = pv_w - house_w - expected_load_w
            if battery_w < -self.DISCHARGE_W or predicted < -self.DISCHARGE_W:
                self._set(house_step.mode, now)
                self._export_since = None
                return house_step
            return GuardStep(GuardMode.AUTO)

        # Im Schutz: Speicher liefert höchstens die Hauslast. Geht trotzdem
        # nennenswert ins Netz, reicht die Sonne – dann darf er wieder laden.
        if grid_w < -self.RELEASE_EXPORT_W and pv_w > house_w:
            if self._export_since is None:
                self._export_since = now
            if now - self._export_since >= self.RELEASE_S and dwell_ok:
                self._set(GuardMode.AUTO, now)
                self._export_since = None
                return GuardStep(GuardMode.AUTO)
        else:
            self._export_since = None
        if house_step.mode != self.mode and dwell_ok:
            self._set(house_step.mode, now)
        if self.mode == GuardMode.HOUSE:
            return GuardStep(GuardMode.HOUSE, house_deficit)
        return GuardStep(self.mode)

    def _set(self, mode: GuardMode, now: float) -> None:
        if mode != self.mode:
            self.mode = mode
            self._changed_at = now
