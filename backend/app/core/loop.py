"""Der geschlossene Regelkreis (Control-Loop).

Ablauf je Tick (Standard alle 10 s):
  1. Messwerte aller aktivierten Geräte parallel lesen (mit Timeout).
  2. Überschuss-Budget berechnen (geglättete Netzleistung, adaptives Totband).
  3. Batterie-Plan bestimmen (core/battery_policy.py – eine Tabelle):
     manuell > Netzladen > Entladesperre für Lasten mit Netzstrom bzw. im
     Eigenbetrieb > Reserve > Automatik. Erst danach laufen die Lasten an.
  4. Die Entladeleistung des Hausspeichers abziehen (sie ist kein
     Solarüberschuss), den Rest entlang der Prioritätskette auf die
     steuerbaren Lasten verteilen. Seine *Lade*leistung wird nicht abgezogen –
     sie steht am Netzpunkt schon nicht mehr im Budget (siehe
     `regulation.releasable_battery_charge`).
  5. **Lückenfüller:** Was danach übrig bleibt und für die nächste grob
     gestufte Last zu klein wäre, geht an eine fein modulierbare Last –
     unabhängig von deren Platz in der Kette.
  6. Stellgrößen an die Geräte senden (nur bei Änderung – kein Hammering),
     Übernahme im Gerät gegenprüfen (Readback).
  7. Messwerte puffern, Snapshot per WebSocket senden, Sessions verfolgen.

Sicherheit: Fällt die Netzmessung aus, werden alle steuerbaren Lasten
pausiert (definiertes Fallback-Verhalten) statt blind weiterzuladen.
Geräte-Timeouts crashen den Loop nie.

Verschenkte Energie: Jeder Tick misst, wie viel Überschuss über dem
Netz-Sollwert eingespeist wurde, ohne dass eine steuerbare Last ihn
aufgenommen hat, und protokolliert dazu den Grund. Das macht das Regelziel
'keinen Watt verschenken' überprüfbar statt behauptet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..db import async_session
from ..drivers import registry
from ..drivers.base import (
    BatteryData,
    BatteryDriver,
    BatteryMode,
    DeviceCategory,
    InverterData,
    MeterData,
    WallboxData,
    WallboxDriver,
    WallboxState,
    WaterHeaterData,
    WaterHeaterDriver,
)
from ..models import ChargeSession, Device, Schedule, Setting
from ..services.audit import log_event
from ..services.diagnostics import waste_is_avoidable
from ..services.measurements import MeasurementBuffer
from ..services.ws import ws_manager
from .regulation import (
    BATTERY_PROTECT_CONFIRM_S,
    config_from_settings,
    ChargeContext,
    DelayTimer,
    RegulationConfig,
    Smoother,
    VolatilityTracker,
    WallboxController,
    WallboxDecision,
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
from .battery_policy import (
    INTENT_LABELS,
    BatteryInputs,
    BatteryIntent,
    BatteryPlan,
    DischargeGuard,
    GuardMode,
    heater_grid_intent,
    plan_battery,
    wallbox_grid_intent,
)
from .schedules import any_window_active
from .secrets import reveal_config
from .clock import local_now

log = logging.getLogger(__name__)


def _implements(driver: Any, method: str) -> bool:
    """Beherrscht dieser Treiber den optionalen Befehl wirklich?

    `BatteryDriver` definiert `set_mode`/`set_reserve_soc` als Stumpf, der
    NotImplementedError wirft – ein `hasattr` ist also immer True und sagt
    nichts. Verglichen wird deshalb gegen die Basisklasse: Nur wer die Methode
    überschreibt, kann sie auch."""
    own = getattr(type(driver), method, None)
    base = getattr(BatteryDriver, method, None)
    return own is not None and own is not base

READ_TIMEOUT_S = 6.0
OFFLINE_AFTER_FAILURES = 3
VOLTAGE = 230.0

#: Ab dieser gemessenen Leistung gilt eine Last, die aus sein soll, als laufend.
#: Über dem Messrauschen einer Wallbox, unter jeder echten Ladestufe.
UNCONTROLLED_W = 300.0
#: Wie viele Takte die Messung dem Befehl widersprechen muss, bevor der Befehl
#: erneut geschickt wird. Ein Fahrzeug antwortet über Funk verzögert, und der
#: Tesla-Treiber liefert bis zu 20 s alte Werte aus seinem Cache – ein
#: einzelner Takt Abweichung ist deshalb noch kein Ungehorsam.
OBEY_CONFIRM_TICKS = 4
#: Wie lange nach einem Stellbefehl nicht ueber Gehorsam geurteilt wird.
#: Treiber mit langsamem Transport (BLE-Proxy, Cloud-API) erhoehen das ueber
#: ihr `read_timeout_s` automatisch -- siehe _obeyed().
OBEY_SETTLE_S = 60.0
#: Stellraster für modulierende Heizstäbe (W). Siehe _apply_heater.
HEATER_STEP_W = 10.0

#: So lange wartet ein Takt höchstens auf frische Messwerte.
#:
#: Vorher wartete jeder Takt auf das langsamste Gerät – ein Fahrzeug über den
#: BLE-Proxy braucht bis zu 25 s. Alle anderen Geräte, auch ein soeben per
#: Knopfdruck umgeschalteter Heizstab, standen so lange still. Im
#: Diagnosebericht wurde deshalb viermal in 25 s auf 'Modus" gedrückt.
#: Langsame Geräte lesen jetzt im Hintergrund weiter; der Takt rechnet mit
#: ihrem letzten Wert.
READ_BUDGET_S = 3.0
#: Eine Warnung 'folgt der Regelung nicht" je Gerät höchstens so oft. Weitere
#: Fälle werden gezählt (Diagnose), nicht einzeln ins Protokoll geschrieben.
OBEY_WARN_REPEAT_S = 6 * 3600.0
#: Fahrzeug wecken, wenn Überschuss ins Netz geht und es angesteckt
#: eingeschlafen ist: erst nach so langer, durchgehender Überschuss-Phase …
WAKE_SURPLUS_CONFIRM_S = 300.0
#: … und höchstens so oft (Wecken kostet das Auto selbst etwas Energie).
WAKE_RETRY_S = 1800.0
WAKE_TIMEOUT_S = 45.0
#: Verschenkt-Protokoll: Episoden ab dieser Leistung zählen, ab dieser Menge
#: wird eine Zeile ins Ereignisprotokoll geschrieben.
WASTE_EPISODE_MIN_W = 150.0
WASTE_EPISODE_LOG_KWH = 0.2
WASTE_EPISODE_IDLE_S = 300.0
WASTE_EPISODE_MAX_S = 3600.0

#: Mindestabstand zwischen zwei Takten, wenn ein Befehl den Loop vorzeitig
#: weckt. Schnelle Klickfolgen sollen den Modbus-Dongle nicht fluten.
MIN_STEP_GAP_S = 0.4
#: Batterie-Stellleistung: Raster und Mindeständerung für einen neuen
#: Schreibbefehl. Der Netzbudget-Anteil schwankt mit jeder Hauslast; ohne
#: diese Schwelle schriebe der Loop in jedem Takt einen neuen Wert.
BATTERY_POWER_STEP_W = 100
BATTERY_POWER_RESEND_W = 300
#: Unter dieser Leistung lohnt kein Zwangsladen – dann nur sperren.
BATTERY_MIN_FORCE_W = 200
#: So lange nach einem Batteriebefehl wird der zurückgelesene Modus nicht
#: bewertet (Wechselrichter übernehmen Zwangsbefehle mit Verzögerung).
BATTERY_SETTLE_S = 20.0
#: Takte in Folge, in denen der Modus dem Befehl widersprechen muss, bevor
#: erneut gesendet wird.
BATTERY_MISMATCH_TICKS = 2
#: Wie lange beim Beenden auf die Rücksetzung eines Speichers gewartet wird.
BATTERY_RELEASE_TIMEOUT_S = 8.0
#: Fahrzeug: Ab wann 'antwortet nicht mehr" ins Protokoll kommt. Für die
#: Regelung gilt es schon nach 2 min als nicht erreichbar (kein Budget,
#: keine Befehle) – gemeldet wird erst, wenn es dauert.
VEHICLE_SILENT_LOG_S = 1800.0
#: Lesen nach Ausfall: Abstand wächst bis zu diesem Wert (s)
READ_BACKOFF_MAX_S = 60.0
#: Batterie-Ereignisse: höchstens so oft je Zustand (s)
BATTERY_EVENT_MIN_S = 60.0


@dataclass
class BatteryManual:
    """Manueller Batteriebefehl aus dem Dashboard.

    Endet immer: nach `until`, beim Ziel-SoC, per 'Automatik", beim Beenden
    von MinePower. Er überlebt einen Neustart bewusst NICHT – ein Container,
    der nach einem Absturz neu startet, soll den Speicher in den
    Eigenverbrauch zurücksetzen, statt ein halb vergessenes Zwangsladen
    fortzuführen."""

    mode: BatteryMode
    power_w: float
    minutes: float
    started: datetime
    until: datetime
    target_soc: float | None = None
    device_id: int | None = None
    #: Absicht in Worten der Entscheidungstabelle (charge, discharge, hold,
    #: no_discharge). 'Entladung sperren" hat keinen eigenen Gerätemodus.
    intent: str | None = None

    def as_dict(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        return {
            "intent": self.intent,
            "mode": self.mode.value,
            "power_w": round(self.power_w),
            "minutes": self.minutes,
            "started": self.started.isoformat(),
            "until": self.until.isoformat(),
            "remaining_s": max(0, int((self.until - now).total_seconds())),
            "target_soc": self.target_soc,
            "device_id": self.device_id,
        }


def combine_inverter_batteries(items: list[InverterData]) -> BatteryData:
    """Batteriewerte mehrerer Hybrid-Wechselrichter zusammenfassen."""
    power = sum(float(i.battery_power or 0.0) for i in items)
    caps = [i.battery_capacity_kwh for i in items]
    socs = [float(i.battery_soc or 0.0) for i in items]
    if all(c for c in caps):
        total = float(sum(caps))  # type: ignore[arg-type]
        soc = sum(s * float(c) for s, c in zip(socs, caps)) / total  # type: ignore[arg-type]
        capacity: float | None = total
    else:
        soc = sum(socs) / len(socs)
        capacity = caps[0] if len(caps) == 1 else None
    return BatteryData(soc=soc, power=power, capacity_kwh=capacity, status="über Wechselrichter gelesen")


def _endpoint_key(dev: "ManagedDevice") -> tuple:
    """Kennung der physischen Quelle: gleicher Treiber + gleiche Konfiguration
    heißt dasselbe Gerät. Zwei so angelegte Wechselrichter zählen die
    PV-Leistung sonst doppelt."""
    cfg = {k: v for k, v in (dev.config or {}).items() if k in ("host", "port", "unit_id", "serial_port", "url", "ip")}
    if not cfg:
        cfg = dev.config or {}
    return (dev.driver_id, json.dumps(cfg, sort_keys=True, default=str))


class ManagedDevice:
    """Laufzeit-Hülle um ein konfiguriertes Gerät + Treiber-Instanz."""

    def __init__(self, row: Device) -> None:
        self.id: int = row.id
        self.name: str = row.name
        self.category: str = row.category
        self.driver_id: str = row.driver_id
        self.config: dict = dict(row.config or {})
        self.settings: dict = dict(row.settings or {})
        self.enabled: bool = row.enabled
        # Zugangsdaten liegen verschlüsselt in der DB, der Treiber bekommt sie im Klartext.
        self.driver = registry.create_driver(row.driver_id, reveal_config(row.driver_id, self.config))
        self.online = False
        self.connected = False
        self.failures = 0
        self.last_error: str | None = None
        self.last_seen: datetime | None = None
        self.data: Any = None
        self.decision: dict = {}
        self.schedules: list[dict] = []

        # Controller nur für steuerbare Kategorien
        self.controller: WallboxController | WaterHeaterController | None = None
        if self.category == DeviceCategory.WALLBOX.value:
            self.controller = WallboxController(self._wallbox_settings())
        elif self.category == DeviceCategory.WATER_HEATER.value:
            self.controller = WaterHeaterController(WaterHeaterSettings.from_dict(self.settings))

        # Dedup der zuletzt gesendeten Stellgrößen …
        self.sent: dict[str, Any] = {}
        # … und wann sie zuletzt wirklich auf dem Draht waren (Watchdog-
        # Auffrischung, siehe _send).
        self.sent_at: dict[str, float] = {}
        # Sende-Resilienz: Fehlversuche je Stellgröße + Backoff-Fenster
        # (value, retry_at) – verhindert das Hämmern fehlschlagender Schreibbefehle
        self.send_fail_count: dict[str, int] = {}
        self.send_backoff: dict[str, tuple[Any, float]] = {}
        # Befehls-Gegenprüfung: Klartext, ob die letzte Stellgröße vom Gerät
        # übernommen wurde. Wird im Snapshot und in der Diagnose angezeigt –
        # 'Befehl gesendet' allein ist keine Erfolgsmeldung.
        self.command_error: str | None = None
        self.command_ok_since: float = time.monotonic()
        #: Takte in Folge, in denen die Messung dem letzten Befehl widerspricht,
        #: und ob das bereits gemeldet wurde (siehe ControlLoop._obeyed).
        self.ignored_ticks = 0
        self.uncontrolled = False
        #: Zuletzt gemeldete Erreichbarkeit des Fahrzeugs (siehe
        #: WallboxData.vehicle_reachable). Nur für die einmalige Meldung beim
        #: Wechsel – ohne sie stünde jede Minute dieselbe Zeile im Protokoll.
        self.vehicle_reachable: bool | None = None
        #: Batterie: zuletzt kommandierter Sollmodus (Modus, Leistung) und
        #: wie oft der zurückgelesene Modus ihm in Folge widersprach.
        self.battery_target: tuple[BatteryMode, int | None] | None = None
        self.battery_mismatch = 0
        self.battery_mismatch_reported = False
        #: Beim Start einmal gemeldet, dass der Speicher in einem fremden
        #: Zwangsmodus stand (z. B. nach Absturz oder durch die Hersteller-App).
        self.battery_foreign_reported = False
        #: 'Entladung gesperrt" → konkrete Befehle (siehe battery_policy)
        self.discharge_guard = DischargeGuard()
        #: Reserve im Wechselrichter: zuletzt bestätigt geschriebener Wert
        self.inverter_reserve: float | None = None
        self.inverter_reserve_error: str | None = None
        #: Lesen nach Ausfall erst wieder ab (monotonic)
        self.read_retry_at = 0.0
        #: Fahrzeug: seit wann nicht erreichbar und ob schon gemeldet
        self.unreachable_since: float | None = None
        self.unreachable_logged = False
        #: Gerät läuft in einem eigenen Programm (siehe WaterHeaterData.
        #: device_mode): seit wann, mit welcher Energie – für ein einziges,
        #: aussagekräftiges Protokoll am Ende statt Warnungen im Minutentakt.
        self.self_mode_since: float | None = None
        self.self_mode_wh = 0.0
        #: Hat das Gerät gegen den Befehl selbst gestartet (Auto beim
        #: Anstecken), und wurde darauf schon reagiert?
        self.self_start_handled = False
        #: Wann zuletzt 'folgt der Regelung nicht" protokolliert wurde und wie
        #: oft es seitdem vorkam (Diagnose).
        self.last_obey_warning = -1e9
        self.obey_count = 0
        #: Wecken bei Überschuss (nur Fahrzeuge): seit wann genug Überschuss
        #: da ist, wann zuletzt versucht, und ob gerade ein Versuch läuft.
        self.wake_surplus_since: float | None = None
        self.last_wake_attempt = -1e9
        self.wake_task: asyncio.Task | None = None
        self.wake_note: str | None = None
        # Session-Verfolgung (nur Wallbox)
        self.session_id: int | None = None
        self.session_energy_kwh = 0.0
        self.session_solar_kwh = 0.0
        self.session_cost = 0.0
        self._last_integrate = time.monotonic()

    def _wallbox_settings(self) -> WallboxSettings:
        """Regler-Einstellungen, angehoben auf die Grenzen der Hardware.

        Ein Ladepunkt hat einen Mindeststrom, unter den er physisch nicht
        geht: Ein Tesla nimmt erst ab 5 A an, viele Wallboxen ab 6 A. Wird
        weniger kommandiert, klemmt der Treiber den Wert nach oben – das
        Fahrzeug zieht dann **mehr**, als der Regler zugeteilt hat, und die
        Differenz kommt aus Speicher oder Netz. Genau die Lücke, die
        'Nulleinspeisung" unmöglich macht.

        Der Regler muss deshalb mit dem echten Mindeststrom rechnen, nicht mit
        dem gewünschten. Dasselbe für den Höchststrom: Was das Gerät nicht
        kann, darf auch nicht ins Budget eingeplant werden.
        """
        settings = WallboxSettings.from_dict(self.settings)
        floor = float(getattr(self.driver, "min_current", 0.0) or 0.0)
        if floor > settings.min_current:
            log.info(
                "%s: Mindeststrom auf %.0f A angehoben – das Gerät kann nicht weniger",
                self.name, floor,
            )
            settings.min_current = floor
        ceiling = float(getattr(self.driver, "max_current", 0.0) or 0.0)
        if ceiling:
            settings.max_current = min(settings.max_current, ceiling)
        if settings.min_pv_current < settings.min_current:
            settings.min_pv_current = settings.min_current
        return settings

    def fingerprint(self) -> tuple:
        import json

        return (self.driver_id, json.dumps(self.config, sort_keys=True), json.dumps(self.settings, sort_keys=True), self.enabled)


class ControlLoop:
    def __init__(self, default_interval_s: float = 3.0) -> None:
        self.cfg = RegulationConfig(interval_s=default_interval_s)
        self.priority: list[int] = []
        self.devices: dict[int, ManagedDevice] = {}
        self.grid_smoother = Smoother(self.cfg.smoothing_samples)
        # Die Batterieleistung wird genauso geglättet wie die Netzleistung.
        # Sie steuert eine Abschaltung – ein einzelner Ausreißer darf das
        # nicht auslösen, und ein Hybrid-Wechselrichter regelt seine Batterie
        # ständig um die Null herum.
        self.battery_smoother = Smoother(self.cfg.smoothing_samples)
        self.battery_protect_timer = DelayTimer(BATTERY_PROTECT_CONFIRM_S)
        self.volatility = VolatilityTracker()
        self.deadband_effective = self.cfg.deadband_w
        self.battery_guard_w = 0.0
        self.battery_release_w = 0.0
        self.battery_protected = False
        #: Läuft gerade ein Netzladefenster (mindestens ein Gerät im Modus
        #: 'price" unter seiner Preisgrenze)?
        self.grid_price_window = False
        #: Konnte der Hausspeicher dafür gesperrt werden?
        self.battery_locked = False
        #: Hat der Speicher den Überschuss schon an die Lasten freigegeben?
        #: Gemerkt, weil die Hysterese den vorherigen Zustand braucht – ohne
        #: ihn pendelt die Freigabe an der Grenze im Minutentakt.
        self.battery_released = False
        self.battery_gate_reason = ""
        #: 'Batterie zuerst" im letzten Takt (Hysterese)
        self.battery_first_state = True
        self.price_cheap_now = False
        #: Lasten, die gerade absichtlich Netzstrom ziehen (Namen)
        self.grid_loads: list[str] = []
        #: Aktueller Batterie-Plan und wann er zuletzt protokolliert wurde
        self.battery_plan = BatteryPlan(BatteryIntent.AUTO, "Automatik")
        self._battery_plan_logged: tuple[str, str] | None = None
        self._battery_plan_logged_at = -1e9
        #: Lädt der Speicher gerade aktiv aus dem Netz, weil er unter seiner
        #: eigenen Preisgrenze liegt? Getrennt von `battery_locked`, weil
        #: 'gesperrt" und 'lädt aktiv" für den Nutzer zwei verschiedene
        #: Zustände sind, auch wenn beides über denselben Mechanismus läuft.
        self.battery_grid_charging = False
        #: Begruendung der Preisentscheidung (siehe core/pricing.py).
        self.price_reason = ""
        #: Manueller Batteriebefehl (Dashboard) und wie der letzte endete.
        self.battery_manual: BatteryManual | None = None
        self.battery_manual_last: dict | None = None
        #: Netzbezugs-Grenze und was davon nach der Verteilung übrig blieb (W).
        self.grid_limit_w = 0.0
        self.grid_budget_left_w: float | None = None
        #: Hinweise für Dashboard und Diagnose (doppelte Geräte, Speicher im
        #: fremden Zwangsmodus …) – Klartext, je Takt neu ermittelt.
        self.warnings: list[str] = []
        #: Namen der Geräte, die wegen identischer Quelle ignoriert werden.
        self.duplicate_devices: list[str] = []
        # Weckt den Loop vor Ablauf des Takts – nach einem Bedienbefehl soll
        # das Gerät sofort reagieren, nicht erst im nächsten Takt (bei 20 s
        # Takt bis zu 20 s später).
        self._wake = asyncio.Event()
        self._runtime_restored = False
        #: Laufende Lesezugriffe je Gerät (siehe _read_all).
        self._read_tasks: dict[int, asyncio.Task] = {}
        #: Laufende Verschenkt-Episode: Energie, Gründe, Zeitstempel.
        self._waste_wh = 0.0
        self._waste_reasons: dict[str, list] = {}
        self._waste_started: float | None = None
        self._waste_last_seen: float | None = None
        self._last_publish_at: float | None = None
        #: Zuletzt bekannte Phasenzahl je Ladepunkt – überlebt Neustarts, weil
        #: ein Fahrzeug sie nur während des Ladens meldet.
        self._phases_saved: dict[int, int] = {}
        self.buffer = MeasurementBuffer()
        # Vollständig vorbelegt: Das Frontend bekommt beim allerersten Abruf
        # (vor dem ersten Tick) dieselbe Struktur wie später – sonst müsste es
        # überall gegen fehlende Felder absichern.
        self.snapshot: dict[str, Any] = {
            "time": None, "pv_power": 0.0, "grid_power": None, "house_power": 0.0,
            "wallbox_power": 0.0, "water_power": 0.0, "battery": None, "surplus": 0.0,
            "price_ct": None, "price_cheap": None,
            "grid_price_window": False, "battery_locked": False,
            "price_reason": "", "battery_gate_reason": "", "battery_released": False,
            "battery_grid_charging": False,
            "battery_manual": None, "battery_manual_last": None,
            "grid_limit_w": 0.0, "grid_budget_left_w": None, "warnings": [],
            "safety": None, "paused": False, "waste_w": 0.0,
            "waste_reason": None, "gap_filled_w": 0.0,
            "deadband_w": self.cfg.deadband_w, "weather": None, "devices": [],
        }
        self.running = False
        self.paused = False           # globaler Not-Aus über API
        self._reload_requested = True
        self._task: asyncio.Task | None = None
        self.tariff = None            # services.tariff.TariffService (optional, Phase 7)
        self.forecast = None          # services.forecast.ForecastService (optional)
        self.mqtt = None              # services.mqtt.MqttPublisher (optional)
        self.notifier = None          # services.notifications.Notifier (optional)

    # ------------------------------------------------------------ Lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.running = True
            self._task = asyncio.create_task(self._run(), name="control-loop")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for dev_id in list(self._read_tasks):
            self._cancel_read(dev_id)
        for dev in self.devices.values():
            if dev.wake_task is not None and not dev.wake_task.done():
                dev.wake_task.cancel()
        # Fail-safe: Keinen Speicher in einem Zwangsmodus zurücklassen – weder
        # gesperrt (das Haus zöge bis zum nächsten Start alles aus dem Netz)
        # noch im Zwangsladen/-entladen. Gilt für Preisfenster UND manuelle
        # Befehle; vorher wurde nur bei offenem Preisfenster zurückgesetzt.
        self.battery_manual = None
        for dev in list(self.devices.values()):
            if dev.category == DeviceCategory.BATTERY.value:
                await self._release_battery(dev, "MinePower beendet")
        self.grid_price_window = False
        for dev in self.devices.values():
            try:
                await dev.driver.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def request_reload(self) -> None:
        """Von der API gerufen, wenn Geräte/Einstellungen geändert wurden."""
        self._reload_requested = True
        self.kick()

    def kick(self) -> None:
        """Nächsten Takt sofort auslösen (nach einem Bedienbefehl).

        Alle Schreibzugriffe auf Geräte laufen weiterhin ausschließlich im
        Loop – die API ändert nur den Sollzustand und weckt ihn. So gibt es
        keinen Wettlauf zwischen manuellem Befehl und Automatik, und die
        Reihenfolge der Befehle ist die Reihenfolge der Takte."""
        self._wake.set()

    async def _run(self) -> None:
        log.info("Control-Loop gestartet (Intervall %.1f s)", self.cfg.interval_s)
        while self.running:
            started = time.monotonic()
            # Vor dem Takt zurücksetzen, nicht danach: Ein Befehl, der
            # *während* des Takts kommt, soll sofort den nächsten auslösen
            # statt verloren zu gehen.
            self._wake.clear()
            try:
                if self._reload_requested:
                    await self._reload()
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 – der Loop darf nie sterben
                log.exception("Fehler im Control-Loop: %s", exc)
            elapsed = time.monotonic() - started
            # Bis zum nächsten Takt schlafen – oder bis ein Befehl weckt.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.5, self.cfg.interval_s - elapsed))
                woke = True
            except asyncio.TimeoutError:
                woke = False
            if woke:
                gap = MIN_STEP_GAP_S - (time.monotonic() - started)
                if gap > 0:
                    await asyncio.sleep(gap)

    # ------------------------------------------------------------ Reload

    async def _reload(self) -> None:
        self._reload_requested = False
        async with async_session() as session:
            rows = (await session.scalars(select(Device))).all()
            schedules = (await session.scalars(select(Schedule))).all()
            settings_rows = (await session.scalars(select(Setting))).all()

        settings = {s.key: s.value for s in settings_rows}
        reg = settings.get("regulation", {})
        self.cfg = config_from_settings(reg, self.cfg.interval_s)
        if self.grid_smoother.values.maxlen != max(1, self.cfg.smoothing_samples):
            self.grid_smoother = Smoother(self.cfg.smoothing_samples)
            self.battery_smoother = Smoother(self.cfg.smoothing_samples)
        self.priority = list(settings.get("priority", {}).get("order", []))

        new_devices: dict[int, ManagedDevice] = {}
        for row in rows:
            existing = self.devices.get(row.id)
            candidate = ManagedDevice(row)
            if existing and existing.fingerprint() == candidate.fingerprint():
                new_devices[row.id] = existing  # Controller-/Verbindungszustand behalten
            else:
                if existing:
                    # Neue Einstellungen dürfen keinen laufenden Eingriff
                    # verschlucken: Vorher beendete jede Änderung am Gerät
                    # (Zieltemperatur, Leistung …) einen laufenden Boost still.
                    self._carry_over(existing, candidate)
                    self._cancel_read(existing.id)
                    if existing.category == DeviceCategory.BATTERY.value:
                        # Mit dem ALTEN Treiber zurücksetzen – womöglich wurde
                        # gerade die Schreibfreigabe entzogen, dann könnte der
                        # neue es gar nicht mehr.
                        await self._release_battery(existing, "Gerätekonfiguration geändert")
                    try:
                        await existing.driver.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                new_devices[row.id] = candidate
        for old_id, old in self.devices.items():
            if old_id not in new_devices:
                self._cancel_read(old_id)
                if old.category == DeviceCategory.BATTERY.value:
                    await self._release_battery(old, "Gerät entfernt")
                try:
                    await old.driver.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self.devices = new_devices
        if not self._runtime_restored:
            self._runtime_restored = True
            self._restore_runtime(settings.get("runtime") or {})

        by_device: dict[int, list[dict]] = {}
        for s in schedules:
            by_device.setdefault(s.device_id, []).append(
                {"days_mask": s.days_mask, "start_time": s.start_time, "end_time": s.end_time, "enabled": s.enabled}
            )
        for dev in self.devices.values():
            dev.schedules = by_device.get(dev.id, [])
        log.info("Konfiguration geladen: %d Geräte, Priorität %s", len(self.devices), self.priority)

    @staticmethod
    def _carry_over(old: ManagedDevice, new: ManagedDevice) -> None:
        """Laufende manuelle Eingriffe auf die neu erzeugte Geräte-Hülle
        übertragen (Boost, Override)."""
        if old.category != new.category:
            return
        if isinstance(old.controller, WaterHeaterController) and isinstance(new.controller, WaterHeaterController):
            if old.controller.boost:
                new.controller._boost = True
                new.controller.boost_started_utc = old.controller.boost_started_utc
                new.controller.boost_until_utc = old.controller.boost_until_utc
        elif isinstance(old.controller, WallboxController) and isinstance(new.controller, WallboxController):
            new.controller.override = old.controller.override
            new.controller.override_since_utc = old.controller.override_since_utc
            new.controller.override_until_utc = old.controller.override_until_utc
            new.controller._phases_seen = old.controller._phases_seen

    # ------------------------------------------------------------ Laufzeitzustand

    def _restore_runtime(self, runtime: dict) -> None:
        """Nach einem Neustart laufende Boosts wiederherstellen.

        Definiertes Verhalten beim Neustart des Containers:
          * Warmwasser-Boost: läuft weiter, wenn seine Endzeit noch nicht
            erreicht ist – mit derselben Endzeit wie vorher. Abgelaufene
            Boosts werden verworfen.
          * Manueller Batteriebefehl: endet. Der Speicher wird beim ersten
            Takt in den Eigenverbrauch zurückgesetzt (siehe _reconcile_battery).
        """
        for key, phases in (runtime.get("phases") or {}).items():
            try:
                dev = self.devices.get(int(key))
                value = int(phases)
            except (TypeError, ValueError):
                continue
            if dev is not None and isinstance(dev.controller, WallboxController) and value in (1, 3):
                dev.controller._phases_seen = value
                self._phases_saved[dev.id] = value
        now = datetime.now(timezone.utc)
        for key, state in (runtime.get("boost") or {}).items():
            try:
                dev = self.devices.get(int(key))
                until = datetime.fromisoformat(state["until"])
                started = datetime.fromisoformat(state["started"])
            except (KeyError, TypeError, ValueError):
                continue
            if dev is None or not isinstance(dev.controller, WaterHeaterController) or until <= now:
                continue
            dev.controller._boost = True
            dev.controller.boost_started_utc = started
            dev.controller.boost_until_utc = until
            log.info("Boost für %s nach Neustart fortgesetzt (bis %s)", dev.name, until.isoformat())

    async def save_runtime(self) -> None:
        """Laufende Boosts persistieren (eigener Schlüssel 'runtime", nicht
        über die Einstellungs-API erreichbar – sonst überschriebe das
        Speichern der Einstellungsseite den Zustand mit einer alten Kopie)."""
        boosts = {}
        for dev in self.devices.values():
            c = dev.controller
            if isinstance(c, WaterHeaterController) and c.boost and c.boost_until_utc and c.boost_started_utc:
                boosts[str(dev.id)] = {
                    "started": c.boost_started_utc.isoformat(),
                    "until": c.boost_until_utc.isoformat(),
                }
        try:
            async with async_session() as session:
                row = await session.get(Setting, "runtime")
                value = {"boost": boosts, "phases": {str(k): v for k, v in self._phases_saved.items()}}
                if row is None:
                    session.add(Setting(key="runtime", value=value))
                else:
                    row.value = value
                await session.commit()
        except Exception as exc:  # noqa: BLE001 – Persistenz darf den Loop nie stören
            log.warning("Laufzeitzustand nicht gespeichert: %s", exc)

    # ------------------------------------------------------------ Mess-Zyklus

    def _cancel_read(self, dev_id: int) -> None:
        task = self._read_tasks.pop(dev_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _read_all(self, active: list[ManagedDevice]) -> None:
        """Alle Geräte lesen – aber höchstens READ_BUDGET_S darauf warten.

        Ein Gerät, dessen letzter Lesezugriff noch läuft, bekommt keinen
        zweiten; es behält bis dahin seinen letzten Messwert. So bestimmt das
        schnellste Gerät den Takt, nicht das langsamste."""
        tasks: list[asyncio.Task] = []
        now = time.monotonic()
        for dev in active:
            task = self._read_tasks.get(dev.id)
            if task is None or task.done():
                # Ausgefallenes Gerät (z. B. BLE-Proxy aus) nicht in jedem Takt
                # anfragen: Abstand wächst 10 → 20 → 40 → 60 s.
                if now < dev.read_retry_at:
                    continue
                task = asyncio.create_task(self._read_device(dev), name=f"read:{dev.id}")
                self._read_tasks[dev.id] = task
            tasks.append(task)
        if tasks:
            await asyncio.wait(tasks, timeout=READ_BUDGET_S)

    async def _read_device(self, dev: ManagedDevice) -> None:
        # Nicht jedes Gerät antwortet gleich schnell: Ein Modbus-Zähler im LAN
        # ist in Millisekunden da, ein schlafendes Fahrzeug über einen
        # BLE-Proxy braucht auch mal 20 Sekunden. Ein pauschaler Timeout würde
        # so ein Gerät im Minutentakt fälschlich offline melden.
        timeout = float(getattr(dev.driver, "read_timeout_s", READ_TIMEOUT_S))
        try:
            if not dev.connected:
                await asyncio.wait_for(dev.driver.connect(), timeout=timeout)
                dev.connected = True
            dev.data = await asyncio.wait_for(dev.driver.read_data(), timeout=timeout)
            if not dev.online and dev.failures >= OFFLINE_AFTER_FAILURES:
                await log_event(f"Gerät wieder online: {dev.name}", category="device")
                if self.notifier:
                    await self.notifier.send(f"✅ {dev.name} ist wieder online")
            dev.online = True
            dev.failures = 0
            dev.read_retry_at = 0.0
            dev.last_error = None
            dev.last_seen = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            dev.failures += 1
            dev.last_error = f"{type(exc).__name__}: {exc}"
            dev.connected = False  # Auto-Reconnect beim nächsten Tick
            if dev.failures >= OFFLINE_AFTER_FAILURES:
                backoff = min(READ_BACKOFF_MAX_S, 10.0 * (2 ** (dev.failures - OFFLINE_AFTER_FAILURES)))
                dev.read_retry_at = time.monotonic() + backoff
            if dev.online and dev.failures >= OFFLINE_AFTER_FAILURES:
                dev.online = False
                await log_event(
                    f"Gerät offline: {dev.name} ({dev.last_error})", level="warning", category="device"
                )
                if self.notifier:
                    await self.notifier.send(f"⚠️ {dev.name} ist offline: {dev.last_error}")

    async def _note_vehicle_link(self, dev: ManagedDevice) -> None:
        """Abgerissenen Funkkontakt zum Fahrzeug sichtbar machen – ohne Spam.

        Für die Regelung gilt ein Fahrzeug nach 2 min ohne Antwort als nicht
        erreichbar (kein Budget, keine Befehle, letzter Stand bleibt). Ins
        Protokoll kommt es erst nach VEHICLE_SILENT_LOG_S: Ein schlafendes
        Auto ist Normalbetrieb, und 18 Meldungen in 7 Tagen las niemand."""
        reachable = getattr(dev.data, "vehicle_reachable", None)
        if reachable is None:
            return
        now = time.monotonic()
        first = dev.vehicle_reachable is None
        dev.vehicle_reachable = reachable
        if reachable:
            dev.unreachable_since = None
            if dev.unreachable_logged:
                dev.unreachable_logged = False
                await log_event(f"{dev.name} wieder erreichbar", category="control")
            return
        if dev.unreachable_since is None:
            dev.unreachable_since = now
            if first:
                return
        if dev.unreachable_logged or now - dev.unreachable_since < VEHICLE_SILENT_LOG_S:
            return
        dev.unreachable_logged = True
        plugged = bool(getattr(dev.driver, "likely_plugged_in", False))
        await log_event(
            f"{dev.name} antwortet nicht ({'schläft angesteckt' if plugged else 'vermutlich unterwegs'})",
            level="info", category="control",
            data={"presence": "asleep_plugged" if plugged else "away"},
        )

    async def _step(self) -> None:
        step_started = time.monotonic()
        active = [d for d in self.devices.values() if d.enabled]
        await self._read_all(active)

        # --- Messgrößen aggregieren -----------------------------------
        pv_power = 0.0
        grid_power: float | None = None
        grid_from_inverter: float | None = None
        battery: BatteryData | None = None
        meter_online = False

        battery_from_inverter: BatteryData | None = None
        inverter_batteries: list[InverterData] = []
        warnings: list[str] = []

        # Dieselbe physische Quelle zweimal angelegt (z. B. Wechselrichter per
        # Doppelklick zweimal gespeichert) → nur einmal zählen. Im
        # Diagnosebericht standen zwei identische Sungrow SH, die PV-Leistung
        # und damit der errechnete Hausverbrauch waren doppelt so hoch.
        seen_sources: set[tuple] = set()
        duplicates: list[str] = []
        counted: list[ManagedDevice] = []
        for dev in active:
            if dev.category in (DeviceCategory.INVERTER.value, DeviceCategory.METER.value):
                key = _endpoint_key(dev)
                if key in seen_sources:
                    duplicates.append(dev.name)
                    continue
                seen_sources.add(key)
            counted.append(dev)
        self.duplicate_devices = duplicates
        if duplicates:
            warnings.append(
                f"Doppelt angelegt und deshalb ignoriert: {', '.join(duplicates)} "
                f"(gleicher Treiber, gleiche Adresse). Bitte das Duplikat löschen."
            )

        for dev in counted:
            if not dev.online or dev.data is None:
                continue
            if isinstance(dev.data, InverterData):
                pv_power += dev.data.pv_power
                if dev.data.grid_power is not None:
                    grid_from_inverter = dev.data.grid_power
                # Hybrid-Wechselrichter liefern die Batteriewerte gleich mit.
                # Damit funktioniert Batterie-Monitoring ohne separates
                # Batteriegerät – dieses bleibt optional (und ist für die
                # Nulleinspeisung nicht nötig, der WR regelt sie selbst).
                if dev.data.battery_soc is not None or dev.data.battery_power is not None:
                    inverter_batteries.append(dev.data)
            elif isinstance(dev.data, MeterData):
                grid_power = dev.data.grid_power
                meter_online = True
            elif isinstance(dev.data, BatteryData):
                battery = dev.data

        # Mehrere Hybrid-Wechselrichter: Leistung summiert, Ladestand nach
        # Kapazität gewichtet (ohne Kapazitätsangabe: Mittelwert).
        if inverter_batteries:
            battery_from_inverter = combine_inverter_batteries(inverter_batteries)

        # Ein eigenes Batteriegerät hat Vorrang; sonst der Hybrid-WR.
        if battery is None:
            battery = battery_from_inverter

        has_meter_device = any(d.category == DeviceCategory.METER.value for d in active)
        if grid_power is None:
            grid_power = grid_from_inverter
        grid_ok = grid_power is not None and (not has_meter_device or meter_online)

        wallboxes = [d for d in active if d.category == DeviceCategory.WALLBOX.value]
        for dev in wallboxes:
            await self._note_vehicle_link(dev)
        heaters = [d for d in active if d.category == DeviceCategory.WATER_HEATER.value]
        battery_devs = [d for d in active if d.category == DeviceCategory.BATTERY.value]

        wb_power = sum(d.data.power for d in wallboxes if d.online and isinstance(d.data, WallboxData))
        wh_power = sum(d.data.power for d in heaters if d.online and isinstance(d.data, WaterHeaterData))
        bat_power = battery.power if battery else 0.0

        # Wallboxen, deren Ladepunkt NICHT vom Netzzähler erfasst wird (Ladepunkt
        # sitzt auf der Netzseite des Zählers – z. B. Tesla an einer Steckdose vor
        # dem DTSU666). Der Zähler 'sieht" die Ladung nicht, zeigt den PV-Überschuss
        # weiter als Einspeisung. Deshalb die Ladeleistung zur gemessenen Netzleistung
        # addieren → korrekte Regelung und Anzeige (kein Phantom-Export durchs Auto).
        if grid_power is not None:
            unmetered = sum(
                d.data.power for d in wallboxes
                if d.online and isinstance(d.data, WallboxData)
                and not d.settings.get("grid_metered", True)
            )
            grid_power += unmetered

        # Speicher, deren zurückgelesener Modus nicht zum Sollzustand passt,
        # zuerst abgleichen – auch im Sicherheits-Stopp (siehe unten).
        for dev in battery_devs:
            await self._reconcile_battery(dev, warnings)
        self.warnings = warnings

        # --- Sicherheits-Fallback: keine verlässliche Netzmessung ------
        if not grid_ok or self.paused:
            reason = "Regelung pausiert" if self.paused else "Netzmessung ausgefallen – Lasten sicher pausiert"
            for dev in wallboxes + heaters:
                await self._apply_safe_stop(dev, reason)
            # Ohne Netzmessung kein Netzladen: Ein Zwangsladen 'im Blindflug"
            # könnte den Hausanschluss überlasten. Manuelle Befehle ruhen
            # (ihre Endzeit läuft weiter), der Speicher geht auf Automatik.
            for dev in battery_devs:
                if dev.online and self.battery_controllable(dev):
                    dev.discharge_guard.reset()
                    await self._command_battery(dev, BatteryMode.AUTO)
            self.grid_price_window = False
            self.grid_loads = []
            self.battery_locked = False
            self.battery_grid_charging = False
            # Auch während des Sicherheits-Stopps wird Überschuss verschenkt –
            # das gehört ehrlich in die Kennzahl, sofern eine Netzmessung
            # überhaupt vorliegt (beim Ausfall wissen wir es schlicht nicht).
            # Die Integrationsuhr muss auch hier weiterlaufen. Sonst rechnet
            # der nächste reguläre Takt die dann gemessene Leistung über die
            # gesamte Ausfallzeit hoch. Lädt das Fahrzeug während des Ausfalls
            # weiter, gehört diese Energie ohnehin ehrlich in die Bilanz.
            for dev in wallboxes:
                await self._track_session(dev, float(grid_power or 0.0), None)
            waste = wasted_power(self.grid_smoother.value, self.cfg) if grid_ok else 0.0
            await self._publish(
                pv_power, grid_power, battery, wb_power, wh_power, budget=0.0,
                safety=reason, waste_w=waste,
                waste_reason=reason if waste >= 50 else None,
            )
            return

        grid_smoothed = self.grid_smoother.add(float(grid_power))
        self.volatility.add(pv_power)
        deadband = self.volatility.deadband(self.cfg)
        self.deadband_effective = deadband
        # Nur, was die Regelung auch umverteilen kann. Was ein Gerät gegen den
        # Befehl zieht (eigenes Programm, Selbststart), ist kein Überschuss.
        controllable = sum(self._redirectable_power(d) for d in wallboxes + heaters if d.online)
        budget = compute_budget(grid_smoothed, controllable, self.cfg, deadband_w=deadband)

        # --- Batterie schonen ------------------------------------------
        # Geglättet, weil hieran eine Abschaltung hängt: Ein Hybrid-
        # Wechselrichter regelt seine Batterie ständig um die Null herum, und
        # ein einzelner Ausreißer darf keine laufende Ladung beenden.
        bat_power_s = self.battery_smoother.add(bat_power)
        soc = battery.soc if battery else None
        # Entlädt sich der Speicher, ist diese Leistung kein Solarüberschuss.
        battery_guard = protected_battery_discharge(soc, bat_power_s, self.cfg)
        # 'Batterie zuerst bis …" – mit Hysterese, damit die Reihenfolge an
        # der Grenze nicht im Minutentakt wechselt.
        battery_first = self.cfg.battery_first(soc, self.battery_first_state)
        self.battery_first_state = battery_first
        battery_release = releasable_battery_charge(soc, bat_power_s, self.cfg, battery_first)
        self.battery_guard_w = battery_guard
        self.battery_release_w = battery_release
        battery_protected = self.battery_protect_timer.check(
            battery_drain_alarm(battery_guard, self.cfg)
        )
        self.battery_protected = battery_protected
        self.battery_released = not battery_first
        self.battery_gate_reason = self._priority_text(soc, battery_first)

        # --- Überschuss-Topf & Reihenfolge -------------------------------
        order = self._priority_order(wallboxes, heaters, battery_devs)
        pool = max(0.0, budget - battery_guard + battery_release)

        price_ct = cheap = None
        if self.tariff:
            price_ct = self.tariff.current_price_ct()
            decision = getattr(self.tariff, "decide", None)
            if callable(decision):
                result = decision()
                cheap = result.should_charge
                self.price_reason = result.reason
            else:
                cheap = self.tariff.is_cheap_hour()
                self.price_reason = ""
        else:
            self.price_reason = "Kein Tarif konfiguriert"

        self.price_cheap_now = bool(cheap)
        house_power = max(0.0, float(grid_power) + pv_power - wb_power - wh_power - bat_power)
        recovery, forecast_surplus = self._forecast_context(pv_power, house_power)

        # --- Manueller Batteriebefehl (Dashboard) -------------------------
        # Schlägt die Automatik, endet aber immer (Zeit, Ziel-SoC).
        manual = await self._manual_battery_tick(battery)
        # Manuelles Entladen und gleichzeitig Lasten mit günstigem Netzstrom
        # hieße: Der Speicher füllt das Auto. Solange entladen wird, laden
        # Preis-Lasten deshalb nur Sonnenstrom.
        load_cheap = bool(cheap) and not (manual is not None and manual.mode == BatteryMode.FORCE_DISCHARGE)

        # --- Netzbezugs-Budget ----------------------------------------------
        #   Netzbezug = Haus + Lasten + Batterieladung − PV ≤ Grenze
        # Manuelle Eingriffe (Batteriebefehl, Boost) werden vorab reserviert.
        self.grid_limit_w = self.cfg.grid_limit_w()
        grid_left = self.grid_limit_w - house_power + pv_power
        battery_max_w = self._battery_max_power(battery_devs)
        manual_power: float | None = None
        if manual is not None:
            manual_power = min(manual.power_w, battery_max_w)
            if manual.mode == BatteryMode.FORCE_CHARGE:
                manual_power = max(0.0, min(manual_power, grid_left))
                grid_left -= manual_power
        boost_reserved: dict[int, float] = {}
        for dev in heaters:
            c = dev.controller
            if isinstance(c, WaterHeaterController) and c.boost and dev.online:
                share = max(0.0, min(c.s.max_power_w, grid_left))
                boost_reserved[dev.id] = share
                grid_left -= share

        # --- Absichten der Lasten – VOR der Kette ---------------------------
        # Der Speicher muss gesichert sein, bevor die erste Last Netzstrom
        # zieht. Abgeleitet aus Zustand + Einstellung, nicht aus Wunschdenken:
        # Ein volles, schlafendes oder abgestecktes Auto zieht nichts.
        intents = self._grid_intents(wallboxes, heaters, load_cheap)
        foreign = self._foreign_loads(wallboxes, heaters)
        price_loads = [name for name, _w, cheap_load in intents.values() if cheap_load]
        other_loads = [name for name, _w, cheap_load in intents.values() if not cheap_load]
        self.grid_loads = price_loads + other_loads
        protect_all = self.cfg.battery_protect_other_loads
        expected_grid_w = sum(w for _name, w, cheap_load in intents.values() if cheap_load or protect_all)

        # Netzbudget für den Speicher: Hat er Vorrang, zuerst; sonst bekommt
        # er, was die Lasten mit Netzwunsch übrig lassen.
        ahead_demand = 0.0 if battery_first else expected_grid_w
        battery_grid_w = max(0.0, min(battery_max_w, grid_left - ahead_demand))
        manual_intent = None
        if manual is not None:
            manual_intent = BatteryIntent(manual.intent) if manual.intent else {
                BatteryMode.FORCE_CHARGE: BatteryIntent.CHARGE,
                BatteryMode.FORCE_DISCHARGE: BatteryIntent.DISCHARGE,
                BatteryMode.HOLD: BatteryIntent.HOLD,
            }.get(manual.mode, BatteryIntent.NO_DISCHARGE)
        plan = plan_battery(BatteryInputs(
            soc=soc,
            reserve_soc=self.cfg.battery_reserve_soc,
            inverter_holds_reserve=self._inverter_holds_reserve(battery_devs),
            price_cheap=bool(cheap),
            price_ct=price_ct,
            grid_charge_enabled=self.cfg.battery_grid_charge_enabled,
            grid_charge_soc=self.cfg.battery_grid_charge_soc,
            forecast_surplus_kwh=forecast_surplus,
            use_forecast=self.cfg.battery_grid_charge_forecast and self.cfg.use_forecast,
            capacity_kwh=(battery.capacity_kwh if battery and battery.capacity_kwh else None)
            or (self.cfg.battery_capacity_kwh or None),
            grid_loads=tuple(price_loads),
            other_loads=tuple(other_loads),
            foreign_loads=tuple(foreign),
            protect_other_loads=protect_all,
            manual=manual_intent,
            was_grid_charging=self.battery_grid_charging,
            grid_budget_w=battery_grid_w,
        ))
        if plan.intent == BatteryIntent.DISCHARGE and manual_power is not None:
            plan = BatteryPlan(plan.intent, plan.reason, plan.source, power_w=manual_power)
        elif plan.intent == BatteryIntent.CHARGE and manual is not None and manual_power is not None:
            plan = BatteryPlan(plan.intent, plan.reason, plan.source, power_w=manual_power)
        battery_locked, battery_share_w = await self._execute_battery_plan(
            battery_devs, plan, battery, bat_power_s=bat_power_s, grid_power=float(grid_power),
            pv_power=pv_power, house_power=house_power, expected_grid_w=expected_grid_w,
        )
        await self._note_battery_plan(plan)
        if manual is None:
            grid_left -= battery_share_w
        # Wurde in diesem Takt ein Batteriebefehl geschrieben, den Speicher
        # gleich noch einmal lesen: Der Snapshot soll den Modus NACH dem
        # Befehl zeigen.
        for dev in battery_devs:
            if dev.sent_at.get("battery_mode", 0.0) >= step_started:
                await self._read_device(dev)
        # Ein Speicher, der sich nicht sperren lässt und noch Ladung hat, WIRD
        # in eine Netzlast entladen – der Hybrid-Wechselrichter sieht nur eine
        # Last am Netzpunkt. Nicht erst warten, bis er es tut: vorbeugend
        # blockieren, außer er ist leer (an seiner Untergrenze).
        battery_empty = battery is not None and battery.soc is not None and (
            battery.soc <= (battery.min_soc if battery.min_soc is not None else 5.0) + 1.0
        )
        drain_unblockable = bool(
            price_loads and not battery_locked and battery is not None
            and (bat_power_s < -50 or not battery_empty)
        )
        self.grid_price_window = bool(price_loads)
        self.battery_locked = battery_locked
        self.battery_grid_charging = plan.intent == BatteryIntent.CHARGE and battery_locked and manual is None
        self.battery_plan = plan
        blocker_hint = (
            "Netzladen ausgesetzt: Hausspeicher entlädt und lässt sich nicht sperren"
            if drain_unblockable else None
        )

        # Hausanschluss je Phase: Zwangsladen der Batterie zählt mit.
        manual_charge_w = (
            manual_power or 0.0
            if manual is not None and manual.mode == BatteryMode.FORCE_CHARGE else 0.0
        )
        other_wb_current = (battery_share_w + manual_charge_w) / (3 * VOLTAGE)
        #: Für den Lückenfüller gemerkte Entscheidungen fein modulierbarer Lasten
        fine_loads: list[tuple[ManagedDevice, WaterHeaterDecision]] = []
        #: Warum blieb Überschuss liegen? (für die Kennzahl 'verschenkt')
        blockers: list[str] = []
        if battery_first and bat_power_s > 0:
            blockers.append(
                f"Batterie lädt mit {bat_power_s:.0f} W – sie hat Vorrang"
            )
        if battery_guard > 0:
            blockers.append(
                f"Batterie entlädt mit {battery_guard:.0f} W – das ist kein Solarüberschuss"
            )
        if blocker_hint:
            blockers.append(blocker_hint)

        for dev in order:
            if not dev.online or dev.data is None or dev.controller is None:
                if dev.enabled and not dev.online:
                    blockers.append(f"{dev.name} offline")
                continue

            avail = pool
            dev.last_avail_w = avail
            reserved = boost_reserved.get(dev.id)
            ctx = ChargeContext(
                now=local_now(),
                price_ct=price_ct,
                cheap_hour=load_cheap,
                schedule_active=any_window_active(dev.schedules),
                battery_soc=soc,
                battery_power=bat_power_s,
                house_current_a=house_power / (3 * VOLTAGE),
                other_wallbox_current_a=other_wb_current,
                house_limit_a=self.cfg.house_limit_a,
                price_limit_ct=getattr(self.tariff, "cheap_limit_ct", None),
                solar_recovery_expected=recovery,
                forecast_surplus_kwh=forecast_surplus,
                battery_handled_globally=True,
                # 'Geschont' heißt: Der Speicher entlädt sich seit mindestens
                # BATTERY_PROTECT_CONFIRM_S. Nur dann müssen laufende Lasten
                # sofort abregeln.
                battery_protected=battery_protected,
                battery_locked=battery_locked,
                battery_drain_unblockable=drain_unblockable,
                grid_budget_w=reserved if reserved is not None else max(0.0, grid_left),
            )

            if isinstance(dev.controller, WallboxController):
                decision = dev.controller.decide(avail, dev.data, ctx)
                await self._note_phases(dev)
                await self._note_override_end(dev)
                # Quantisierte Leistung verwenden: Die Box nimmt nur ganze
                # Ampere. Rechneten wir hier mit dem ungerundeten Wunschwert,
                # verschwände der abgerundete Rest aus der Bilanz.
                allocated = decision.power_w
                other_wb_current += decision.current_a if decision.enable else 0.0
                if not decision.enable and decision.reason:
                    blockers.append(f"{dev.name}: {decision.reason}")
                await self._apply_wallbox(dev, decision)
            else:
                decision_wh = dev.controller.decide(avail, dev.data, ctx)
                allocated = decision_wh.power_w
                fine_loads.append((dev, decision_wh))
                other_wb_current += allocated / (3 * VOLTAGE)
                if allocated <= 0 and decision_wh.reason:
                    blockers.append(f"{dev.name}: {decision_wh.reason}")
                await self._note_boost_end(dev)

            pool = max(0.0, pool - allocated)
            if reserved is None:
                grid_left -= allocated
        self.grid_budget_left_w = round(grid_left, 1)

        # --- Schlafendes Fahrzeug wecken, wenn Überschuss verloren geht ---
        for dev in wallboxes:
            self._maybe_wake(dev)

        # --- Lückenfüller ------------------------------------------------
        pool, filled_w = self._fill_gaps(fine_loads, pool)

        # Warmwasser-Sollwerte erst jetzt senden – nach dem Lückenfüller steht
        # der endgültige Wert fest, sonst würde jede Runde zweimal gestellt.
        for dev, decision_wh in fine_loads:
            await self._apply_heater(dev, decision_wh.power_w, decision_wh.reason)

        # --- Batterie-Management (Reserve nachführen) -------------------
        for dev in battery_devs:
            await self._apply_battery(dev)

        # --- Sessions, Messwerte, Broadcast -----------------------------
        for dev in wallboxes:
            await self._track_session(dev, float(grid_power), price_ct)

        waste_w = wasted_power(grid_smoothed, self.cfg)
        await self._publish(
            pv_power, float(grid_power), battery, wb_power, wh_power,
            budget=budget, price_ct=price_ct, price_cheap=cheap,
            waste_w=waste_w,
            waste_reason=self._waste_reason(waste_w, blockers, wallboxes + heaters),
            gap_filled_w=filled_w,
        )

    async def _note_phases(self, dev: ManagedDevice) -> None:
        """Vom Fahrzeug gemeldete Phasenzahl merken (überlebt Neustarts) und
        einmal melden, wenn sie nicht zur Einstellung passt.

        Ein Fahrzeug meldet seine Phasen nur während es lädt. Nach jedem
        Neustart rechnete der Regler deshalb wieder mit der Einstellung –
        '1-phasig", Mindestleistung 1,4 kW – und startete das Auto bei 1,4 kW
        Überschuss, worauf es dreiphasig 4,1 kW zog."""
        ctrl = dev.controller
        seen = getattr(ctrl, "_phases_seen", None)
        if seen not in (1, 3) or self._phases_saved.get(dev.id) == seen:
            return
        self._phases_saved[dev.id] = seen
        configured = {"fixed1": 1, "fixed3": 3}.get(ctrl.s.phases_mode)
        if configured is not None and configured != seen:
            await log_event(
                f"{dev.name} lädt {seen}-phasig, eingestellt ist {configured}-phasig. MinePower "
                f"rechnet ab jetzt mit {seen} Phasen (Mindestleistung {ctrl._min_power() / 1000:.1f} kW).",
                category="control",
            )
        await self.save_runtime()

    # ------------------------------------------------------------ Umverteilbar

    def _redirectable_power(self, dev: ManagedDevice) -> float:
        """Wie viel der gemessenen Leistung eines Geräts kann die Regelung
        umverteilen?

        Was ein Gerät *gegen* den Befehl zieht – ein eigenes Programm (my-PV
        Warmwasser-Sicherstellung) oder ein Auto, das beim Anstecken von
        selbst lädt –, lässt sich keinem anderen Gerät geben. Vorher zählte es
        voll: Eine selbst heizende ELWA mit 3 kW sah für die Kette aus wie
        3 kW freier Solarstrom, und das Auto hätte darauf starten können."""
        data = dev.data
        measured = float(getattr(data, "power", 0.0) or 0.0)
        if measured <= 0:
            return 0.0
        if getattr(data, "device_mode", None):
            return 0.0
        if isinstance(dev.controller, WallboxController):
            if dev.sent.get("enable") is False and measured > UNCONTROLLED_W:
                return 0.0
        elif isinstance(dev.controller, WaterHeaterController):
            sent = dev.sent.get("power")
            if sent is not None and float(sent) <= 0 and measured > UNCONTROLLED_W:
                return 0.0
        return measured

    # ------------------------------------------------------------ Wecken

    def _maybe_wake(self, dev: ManagedDevice) -> None:
        """Ein angesteckt eingeschlafenes Fahrzeug wecken, wenn sein Anteil am
        Überschuss sonst ins Netz ginge.

        Der Tesla-Treiber gibt ein Fahrzeug nach 30 Minuten ohne Antwort als
        'nicht erreichbar" auf – richtig, wenn es weggefahren ist. Schläft es
        aber angesteckt in der Garage, geht der ganze Überschuss ins Netz: Im
        Diagnosebericht am 02.10. mittags bis zu 4,8 kW. Ob es da ist, klärt
        der Weckversuch selbst: Außer BLE-Reichweite schlägt er fehl."""
        ctrl = dev.controller
        driver = dev.driver
        data = dev.data
        now = time.monotonic()
        if not (self.cfg.vehicle_wake_enabled and isinstance(ctrl, WallboxController)
                and isinstance(data, WallboxData) and "wake" in driver.meta.capabilities
                and hasattr(driver, "wake_up")):
            return
        # Wecken lohnt, wenn sonst Überschuss ins Netz geht – oder ein
        # günstiges Preisfenster läuft, in dem das Auto laden soll.
        wants_power = (
            getattr(dev, "last_avail_w", 0.0) >= ctrl._min_power()
            or (ctrl.s.mode in ("price", "pv_price") and self.price_cheap_now)
        )
        eligible = (
            data.vehicle_reachable is False
            and bool(getattr(driver, "likely_plugged_in", False))
            and ctrl.override != "stop"
            and ctrl.s.mode != "off"
            and wants_power
        )
        if not eligible:
            dev.wake_surplus_since = None
            return
        if dev.wake_surplus_since is None:
            dev.wake_surplus_since = now
            return
        if now - dev.wake_surplus_since < WAKE_SURPLUS_CONFIRM_S:
            return
        if now - dev.last_wake_attempt < WAKE_RETRY_S:
            return
        if dev.wake_task is not None and not dev.wake_task.done():
            return
        dev.last_wake_attempt = now
        dev.wake_task = asyncio.create_task(self._wake_vehicle(dev, dev.last_avail_w), name=f"wake:{dev.id}")

    async def _wake_vehicle(self, dev: ManagedDevice, avail_w: float) -> None:
        try:
            await asyncio.wait_for(dev.driver.wake_up(), timeout=WAKE_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            dev.wake_note = "Wecken fehlgeschlagen – vermutlich unterwegs oder außer BLE-Reichweite"
            await log_event(
                f"{dev.name} ließ sich nicht wecken ({type(exc).__name__}) – vermutlich unterwegs "
                f"oder außer Reichweite des BLE-Proxys. Nächster Versuch frühestens in "
                f"{WAKE_RETRY_S / 60:.0f} min.",
                category="control",
            )
            return
        dev.wake_note = "geweckt, weil Überschuss ins Netz ging"
        await log_event(
            f"{dev.name} schlief angesteckt – geweckt, weil {avail_w / 1000:.1f} kW Überschuss "
            f"sonst ins Netz gegangen wären.",
            category="control",
        )
        self.kick()

    # ------------------------------------------------------------ Verschenkt

    async def _track_waste(self, waste_w: float, reason: str | None) -> None:
        """Verschenkte Energie je Episode sammeln und mit Grund protokollieren.

        Die Momentaufnahme 'verschenkt: 4,8 kW, weil …" verschwand bisher mit
        dem nächsten Takt. Für die Diagnose zählt aber, *wie viel* über den Tag
        verloren ging und *warum* – deshalb eine Zeile je Episode."""
        now = time.monotonic()
        last = self._last_publish_at
        self._last_publish_at = now
        dt = 0.0 if last is None else min(now - last, max(2.5 * self.cfg.interval_s, 10.0))
        if waste_w >= WASTE_EPISODE_MIN_W:
            if self._waste_started is None:
                self._waste_started = now
            self._waste_last_seen = now
            wh = waste_w * dt / 3600.0
            self._waste_wh += wh
            key = (reason or "ohne erkennbaren Grund").split("; ")[0]
            # Zahlen im Grund ändern sich jeden Takt – zum Zusammenfassen weg damit.
            norm = re.sub(r"\d+([.,]\d+)?", "#", key)
            entry = self._waste_reasons.setdefault(norm, [0.0, key])
            entry[0] += wh
            entry[1] = key
        if self._waste_started is None:
            return
        idle = self._waste_last_seen is not None and now - self._waste_last_seen >= WASTE_EPISODE_IDLE_S
        if idle or now - self._waste_started >= WASTE_EPISODE_MAX_S:
            await self._flush_waste(now)

    async def _flush_waste(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        kwh = self._waste_wh / 1000.0
        minutes = (now - (self._waste_started or now)) / 60.0
        reasons = sorted(self._waste_reasons.values(), key=lambda e: e[0], reverse=True)
        self._waste_wh = 0.0
        self._waste_reasons = {}
        self._waste_started = None
        self._waste_last_seen = None
        if kwh < WASTE_EPISODE_LOG_KWH:
            return
        top_reason = reasons[0][1] if reasons else "ohne erkennbaren Grund"
        avoidable = waste_is_avoidable(top_reason)
        await log_event(
            f"{kwh:.1f} kWh ins Netz ({minutes:.0f} min, {'vermeidbar' if avoidable else 'unvermeidbar'}): "
            f"{top_reason}",
            category="waste",
            data={"kwh": round(kwh, 2), "minutes": round(minutes), "avoidable": avoidable,
                  "reasons": [t for _w, t in reasons[:5]]},
        )

    # ------------------------------------------------------------ Lückenfüller

    def _fill_gaps(
        self,
        fine_loads: list[tuple["ManagedDevice", WaterHeaterDecision]],
        pool: float,
    ) -> tuple[float, float]:
        """Rest-Überschuss an fein modulierbare Lasten weiterreichen.

        Nach der Prioritätsverteilung bleibt regelmäßig ein Rest übrig, der für
        die nächste Last in der Kette zu klein ist – etwa 180 W, während eine
        Wallbox mindestens 6 A (1380 W) braucht. Dieser Rest ginge sonst ins
        Netz. Kann ihn eine stufenlos modulierbare Last (Warmwasser) noch
        aufnehmen, bekommt sie ihn – **unabhängig von ihrem Platz in der
        Prioritätskette**, denn die Alternative ist nicht 'später', sondern
        'verschenkt'.

        Nur Erhöhungen; Absenken bleibt der regulären Entscheidung vorbehalten.
        """
        filled = 0.0
        for index, (dev, decision) in enumerate(fine_loads):
            if pool < self.cfg.gap_fill_min_w:
                break
            controller = dev.controller
            if not isinstance(controller, WaterHeaterController) or not controller.fine_modulating:
                continue
            improved = controller.absorb(decision, pool, dev.data)
            added = improved.power_w - decision.power_w
            if added <= 0:
                continue
            fine_loads[index] = (dev, improved)
            pool = max(0.0, pool - added)
            filled += added
        return pool, filled

    @staticmethod
    def _waste_reason(waste_w: float, blockers: list[str], loads: list["ManagedDevice"]) -> str | None:
        """Warum wurde Überschuss verschenkt? Klartext fürs Dashboard.

        Ohne Begründung ist die Kennzahl nur ein Vorwurf; mit Begründung ist
        sie eine Anleitung (Schwelle senken, Gerät ergänzen, Box reparieren)."""
        if waste_w < 50:
            return None
        if not loads:
            return "Keine steuerbare Last eingerichtet – Überschuss kann nirgends hin."
        if not any(d.online for d in loads):
            return "Alle steuerbaren Lasten sind offline."
        if blockers:
            return "; ".join(blockers[:3])
        return "Alle steuerbaren Lasten laufen bereits am Maximum."

    # ------------------------------------------------------------ Prognose

    def _forecast_context(self, pv_power: float, house_power: float) -> tuple[bool, float | None]:
        """Prognose in Entscheidungsgrößen übersetzen.

        Liefert (a) ob gerade ein Wolkendurchzug mit absehbarer Erholung
        vorliegt und (b) wie viel nutzbarer Überschuss bis zum nächsten Morgen
        noch zu erwarten ist (für die Zielladung)."""
        if not (self.cfg.use_forecast and self.forecast):
            return False, None
        try:
            recovery = self.forecast.is_transient_dip(pv_power)
            surplus = self.forecast.expected_surplus_kwh(house_power)
        except Exception as exc:  # noqa: BLE001 – Prognose darf nie den Loop stören
            log.debug("Prognose nicht auswertbar: %s", exc)
            return False, None
        return recovery, surplus

    def _priority_order(self, wallboxes, heaters, batteries=None) -> list[ManagedDevice]:
        """Lasten in Reihenfolge der Kette (Auto, Warmwasser …).

        Der Speicher steht nicht mehr in der Kette: Sein Platz ergibt sich aus
        'Batterie zuerst bis …" (RegulationConfig.battery_priority_soc). Die
        Kette [Tesla, Batterie, ELWA] plus 'Batterie hat Vorrang" plus
        Freigabe-SoC waren drei Antworten auf dieselbe Frage. Nicht
        einsortierte Lasten kommen ans Ende."""
        loads = wallboxes + heaters
        by_id = {d.id: d for d in loads}
        ordered = [by_id[i] for i in self.priority if i in by_id]
        ordered += [d for d in loads if d.id not in self.priority]
        return ordered

    @staticmethod
    def _presence(dev: ManagedDevice) -> str:
        """Wo ist das Auto? Für die Oberfläche: 'lädt/angesteckt", 'schläft
        angesteckt", 'unterwegs", 'Proxy offline" – statt eines pauschalen
        'nicht erreichbar"."""
        data = dev.data
        if not dev.online:
            err = (dev.last_error or "").lower()
            return "proxy_offline" if ("proxy" in err or "connection" in err) else "offline"
        if not isinstance(data, WallboxData):
            return "unknown"
        if data.vehicle_reachable is False:
            if not getattr(dev.driver, "presence_known", True):
                return "unknown"
            return "asleep_plugged" if getattr(dev.driver, "likely_plugged_in", False) else "away"
        if data.state == WallboxState.IDLE:
            return "unplugged"
        if data.state == WallboxState.CHARGING:
            return "charging"
        if data.state == WallboxState.COMPLETE:
            return "complete"
        return "plugged"

    def _priority_text(self, soc: float | None, battery_first: bool) -> str:
        limit = self.cfg.battery_priority_soc
        if limit >= 100:
            return "Batterie zuerst"
        if limit <= 0:
            return "Auto und Warmwasser zuerst"
        now = f" (jetzt {soc:.0f} %)" if soc is not None else ""
        if battery_first:
            return f"Batterie zuerst bis {limit:.0f} %{now}"
        return f"Auto und Warmwasser zuerst – Batterie über {limit:.0f} %{now}"

    # ------------------------------------------------------------ Batterie

    @staticmethod
    def _battery_max_power(battery_devs: list[ManagedDevice]) -> float:
        total = 0.0
        for dev in battery_devs:
            driver = dev.driver
            value = getattr(driver, "max_power_w", None)
            if value is None:
                value = (dev.config or {}).get("max_power_w") or (dev.config or {}).get("max_power") or 5000
            try:
                total += float(value)
            except (TypeError, ValueError):
                total += 5000.0
        return total or 5000.0

    @staticmethod
    def _battery_control_problem(battery_devs: list[ManagedDevice]) -> str:
        """Warum lässt sich der Speicher nicht ansteuern? Klartext."""
        if not battery_devs:
            return "kein steuerbares Batteriegerät angelegt"
        for dev in battery_devs:
            if not dev.online:
                return f"{dev.name} offline"
            if hasattr(dev.driver, "control_enabled") and not dev.driver.control_enabled():
                return f"aktive Steuerung für {dev.name} nicht freigeschaltet"
            if not _implements(dev.driver, "set_mode"):
                return f"{dev.name} unterstützt kein Zwangsladen"
            if dev.command_error:
                return f"{dev.name}: {dev.command_error}"
        return "Befehl nicht übernommen"

    def battery_controllable(self, dev: ManagedDevice) -> bool:
        driver = dev.driver
        enabled = not hasattr(driver, "control_enabled") or driver.control_enabled()
        return bool(enabled and _implements(driver, "set_mode"))

    async def _command_battery(
        self, dev: ManagedDevice, mode: BatteryMode, power_w: float | None = None
    ) -> None:
        """Sollmodus an einen Speicher senden – nur bei Änderung.

        Die Leistung wird auf 100 W gerastert und erst bei einer Änderung ab
        BATTERY_POWER_RESEND_W neu geschrieben: Der Netzbudget-Anteil folgt
        jeder Hauslast, und jeder Schreibzugriff kostet beim WiNet-S spürbar
        Zeit."""
        driver: BatteryDriver = dev.driver  # type: ignore[assignment]
        watts: int | None = None
        if mode in (BatteryMode.FORCE_CHARGE, BatteryMode.FORCE_DISCHARGE) and power_w is not None:
            watts = int(round(float(power_w) / BATTERY_POWER_STEP_W) * BATTERY_POWER_STEP_W)
            prev = getattr(dev, "battery_target", None)
            if prev and prev[0] == mode and prev[1] is not None and abs(prev[1] - watts) < BATTERY_POWER_RESEND_W:
                watts = prev[1]
        dev.battery_target = (mode, watts)
        value = mode.value if watts is None else f"{mode.value}@{watts}"
        await self._send(dev, "battery_mode", value, lambda: driver.set_mode(mode, watts))

    async def _reconcile_battery(self, dev: ManagedDevice, warnings: list[str]) -> None:
        """Zurückgelesenen Modus gegen den Sollzustand prüfen.

        Drei Fälle:
          * Beim Start steht der Speicher in einem Zwangsmodus, den MinePower
            nicht (mehr) will – etwa nach einem harten Absturz mitten im
            Zwangsladen. Dann einmal melden; der Takt setzt ihn zurück.
          * Der Speicher weicht nach einem Befehl dauerhaft ab (Hersteller-
            App, eigenes EMS, verworfener Befehl): Dedup verwerfen und neu
            senden, einmal melden.
          * Keine Schreibfreigabe, aber fremder Zwangsmodus: nur warnen –
            MinePower darf nichts ändern."""
        data = dev.data
        if not dev.online or not isinstance(data, BatteryData) or not data.mode_known:
            return
        controllable = self.battery_controllable(dev)
        if not controllable:
            if data.mode != BatteryMode.AUTO:
                warnings.append(
                    f"{dev.name} steht im Modus '{data.mode.value}', MinePower darf ihn aber nicht "
                    f"zurücksetzen (aktive Steuerung nicht freigeschaltet)."
                )
            return
        if data.max_soc is not None and self.cfg.battery_priority_soc < 100 \
                and self.cfg.battery_priority_soc > data.max_soc + 0.5:
            warnings.append(
                f"{dev.name}: 'Batterie zuerst bis {self.cfg.battery_priority_soc:.0f} %' liegt über "
                f"der Ladegrenze im Wechselrichter ({data.max_soc:.0f} %)."
            )
        target = getattr(dev, "battery_target", None)
        if target is None:
            if data.mode != BatteryMode.AUTO and not dev.battery_foreign_reported:
                dev.battery_foreign_reported = True
                await log_event(
                    f"{dev.name} stand beim Start im Modus '{data.mode.value}' – wird auf "
                    f"Eigenverbrauch zurückgesetzt, sofern MinePower ihn nicht selbst braucht.",
                    level="warning", category="control",
                )
            return
        if data.mode == target[0]:
            dev.battery_mismatch = 0
            if dev.battery_mismatch_reported:
                dev.battery_mismatch_reported = False
                await log_event(f"{dev.name} folgt wieder dem Batteriebefehl", category="control")
            return
        last = dev.sent_at.get("battery_mode", 0.0)
        if time.monotonic() - last < BATTERY_SETTLE_S:
            return
        dev.battery_mismatch += 1
        if dev.battery_mismatch < BATTERY_MISMATCH_TICKS:
            return
        dev.battery_mismatch = 0
        dev.sent.pop("battery_mode", None)
        dev.send_backoff.pop("battery_mode", None)
        if not dev.battery_mismatch_reported:
            dev.battery_mismatch_reported = True
            await log_event(
                f"{dev.name} folgt dem Batteriebefehl nicht (Soll '{target[0].value}', "
                f"Ist '{data.mode.value}') – Befehl wird erneut gesendet.",
                level="warning", category="control",
            )

    async def _release_battery(self, dev: ManagedDevice, reason: str) -> None:
        """Speicher in den Eigenverbrauch zurücksetzen (Fail-safe).

        Nur wenn MinePower ihn in einen anderen Modus gebracht hat oder der
        Readback einen Zwangsmodus zeigt – ein unbeteiligter Speicher bleibt
        unangetastet."""
        if not self.battery_controllable(dev):
            return
        target = getattr(dev, "battery_target", None)
        data = getattr(dev, "data", None)
        forced = (target is not None and target[0] != BatteryMode.AUTO) or (
            isinstance(data, BatteryData) and data.mode_known and data.mode != BatteryMode.AUTO
        )
        if not forced:
            return
        try:
            await asyncio.wait_for(dev.driver.set_mode(BatteryMode.AUTO), timeout=BATTERY_RELEASE_TIMEOUT_S)
            dev.battery_target = (BatteryMode.AUTO, None)
            dev.sent["battery_mode"] = BatteryMode.AUTO.value
            log.info("%s auf Eigenverbrauch zurückgesetzt (%s)", dev.name, reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: Rücksetzen auf Eigenverbrauch fehlgeschlagen (%s): %s", dev.name, reason, exc)
            try:
                await log_event(
                    f"{dev.name}: Rücksetzen auf Eigenverbrauch fehlgeschlagen ({reason}) – {exc}. "
                    f"Bitte den Modus im Wechselrichter prüfen.",
                    level="error", category="control",
                )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ Manuell

    def start_battery_manual(
        self, mode: BatteryMode, power_w: float, minutes: float,
        target_soc: float | None = None, device_id: int | None = None, intent: str | None = None,
    ) -> BatteryManual:
        """Manuellen Batteriebefehl setzen (Validierung in der API)."""
        now = datetime.now(timezone.utc)
        self.battery_manual = BatteryManual(
            mode=mode, power_w=float(power_w), minutes=float(minutes), started=now,
            until=now + timedelta(minutes=float(minutes)), target_soc=target_soc, device_id=device_id,
            intent=intent,
        )
        self.kick()
        return self.battery_manual

    async def stop_battery_manual(self, reason: str = "zurück auf Automatik") -> None:
        if self.battery_manual is not None:
            await self._end_manual(reason)
        self.kick()

    async def _end_manual(self, reason: str) -> None:
        m = self.battery_manual
        self.battery_manual = None
        if m is None:
            return
        self.battery_manual_last = {
            "mode": m.mode.value, "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        label = {"no_discharge": "Entladesperre", "hold": "Sperre"}.get(
            m.intent or "", "Laden" if m.mode == BatteryMode.FORCE_CHARGE else "Entladen")
        await log_event(f"Batterie: manuelle {label} beendet – {reason}", category="battery")

    async def _manual_battery_tick(self, battery: BatteryData | None) -> BatteryManual | None:
        m = self.battery_manual
        if m is None:
            return None
        now = datetime.now(timezone.utc)
        soc = battery.soc if battery else None
        reason = None
        if now >= m.until:
            reason = f"Zeit abgelaufen ({m.minutes:.0f} min)"
        elif soc is not None and m.target_soc is not None:
            if m.mode == BatteryMode.FORCE_CHARGE and soc >= m.target_soc:
                reason = f"Ziel-SoC {m.target_soc:.0f} % erreicht"
            elif m.mode == BatteryMode.FORCE_DISCHARGE and soc <= m.target_soc:
                reason = f"Untergrenze {m.target_soc:.0f} % erreicht"
        if reason:
            await self._end_manual(reason)
            return None
        return m

    async def _apply_manual_battery(
        self, battery_devs: list[ManagedDevice], manual: BatteryManual,
        power_w: float | None, battery: BatteryData | None,
    ) -> bool:
        """Manuellen Befehl ausführen. Gibt True zurück, wenn jeder Speicher
        gerade NICHT entlädt (Zwangsladen oder – bei 0 W Budget – Sperre)."""
        mode = manual.mode
        if mode == BatteryMode.FORCE_CHARGE and (power_w or 0.0) < BATTERY_MIN_FORCE_W:
            mode = BatteryMode.HOLD  # Netzanschluss ausgeschöpft: nur sperren
        targets = [d for d in battery_devs if manual.device_id in (None, d.id)]
        if not targets:
            return battery is None
        ok = True
        for dev in targets:
            if not dev.online or not self.battery_controllable(dev):
                ok = False
                continue
            await self._command_battery(dev, mode, power_w)
            if dev.command_error and dev.command_error.startswith("battery_mode:"):
                ok = False
        return ok and mode != BatteryMode.FORCE_DISCHARGE

    async def _note_override_end(self, dev: ManagedDevice) -> None:
        ctrl = dev.controller
        if not isinstance(ctrl, WallboxController):
            return
        end = ctrl.pop_override_end()
        if end is None:
            return
        label = {"fast": "Sofort laden", "stop": "Aus"}.get(end[0], end[0])
        await log_event(f"{dev.name}: '{label}' beendet – {end[1]}", category="control")

    async def _note_boost_end(self, dev: ManagedDevice) -> None:
        """Beendeten Boost protokollieren und den Laufzeitzustand sichern."""
        c = dev.controller
        if not isinstance(c, WaterHeaterController):
            return
        reason = c.pop_boost_end()
        if reason is None:
            return
        await log_event(f"Boost beendet: {dev.name} – {reason}", category="control")
        await self.save_runtime()

    # ------------------------------------------------------------ Aktoren

    async def _send(self, dev: ManagedDevice, key: str, value: Any, coro_factory) -> None:
        """Stellgröße senden – nur bei Wertänderung, mit Fehler-Backoff.

        Schlägt ein Schreibbefehl fehl (Gerät rechnet den Wert nicht an,
        falscher Modus, Timeout …), wird derselbe Wert nicht mehr jeden Tick
        wiederholt, sondern mit exponentiell wachsendem Abstand (max. 60 s).
        Ein NEUER Zielwert setzt den Backoff sofort zurück – legitime
        Sollwertänderungen werden also nie verzögert.

        **Auffrischen unveränderter Sollwerte.** Manche Geräte haben einen
        Watchdog: Bleibt eine Weile ein Schreibbefehl aus, verwerfen sie die
        externe Vorgabe und fallen auf ihre Eigenregelung zurück. Der
        my-PV AC•THOR macht genau das (Register 1004, max. 600 s). Zusammen
        mit dem Dedup oben ergab das einen Heizstab, der nach dem Abschalten
        'manchmal trotzdem noch" lief: MinePower schickte die 0 W genau
        einmal, danach nie wieder – und nach Ablauf des Watchdogs heizte das
        Gerät wieder nach eigenem Gutdünken.

        Treiber mit Watchdog setzen deshalb `resend_interval_s` deutlich
        unter ihre Watchdog-Zeit. Der Wert wird dann periodisch erneut
        geschrieben, auch wenn er sich nicht geändert hat."""
        now = time.monotonic()
        if dev.sent.get(key) == value:
            refresh_after = float(getattr(dev.driver, "resend_interval_s", 0.0) or 0.0)
            if not refresh_after or (now - dev.sent_at.get(key, 0.0)) < refresh_after:
                return
        backoff = dev.send_backoff.get(key)
        if backoff is not None and backoff[0] == value and now < backoff[1]:
            return  # gleicher, weiterhin fehlschlagender Wert → Wiederholung aussetzen
        try:
            await asyncio.wait_for(coro_factory(), timeout=READ_TIMEOUT_S)
            dev.sent[key] = value
            dev.sent_at[key] = now
            dev.send_fail_count.pop(key, None)
            dev.send_backoff.pop(key, None)
            if dev.command_error and dev.command_error.startswith(f"{key}:"):
                dev.command_error = None
                dev.command_ok_since = now
        except Exception as exc:  # noqa: BLE001
            n = dev.send_fail_count.get(key, 0) + 1
            dev.send_fail_count[key] = n
            # Die Treiber lesen Schreibbefehle zurück und melden im Klartext,
            # wenn ein Gerät sie nur quittiert, aber nicht übernommen hat.
            # Genau diese Meldung gehört ins GUI – nicht nur ins Log.
            dev.last_error = f"Sendefehler ({key}): {exc}"
            dev.command_error = f"{key}: {exc}"
            delay = min(60.0, 3.0 * (2 ** min(n - 1, 5)))  # 3,6,12,24,48,60,60…
            dev.send_backoff[key] = (value, now + delay)
            if n <= 2 or n % 30 == 0:  # nicht spammen: nur erste Fehler + gelegentlich
                log.warning("%s: %s (Versuch %d, nächster in %.0fs)", dev.name, dev.last_error, n, delay)
            if n == OFFLINE_AFTER_FAILURES:
                await log_event(
                    f"{dev.name}: Befehl wird nicht übernommen – {exc}",
                    level="warning", category="control",
                )
                if self.notifier:
                    await self.notifier.send(f"⚠️ {dev.name}: Befehl wird nicht übernommen – {exc}")

    async def _obeyed(self, dev: ManagedDevice, measured_w: float, allowed_w: float, what: str) -> None:
        """Gegenprüfen, ob die Last dem letzten Befehl wirklich folgt.

        Der Regelkreis war bis hier einseitig: Ein Sollwert galt als erledigt,
        sobald der Treiber ihn ohne Fehler quittiert hatte. Ob die Last ihn
        auch befolgt, wurde nie gemessen — und `_send` schickt denselben Wert
        aus Dedup-Gründen kein zweites Mal. Beides zusammen ergibt eine Lücke,
        aus der die Last nie wieder herausfindet:

        Ein Tesla beginnt beim Einstecken von sich aus zu laden, ebenso nach
        einem Abfahrtstimer. MinePower hatte 'aus" längst gesendet, hält es für
        erledigt und wiederholt es nie. Im Diagnosebericht lud das Auto so um
        18 Uhr mit 5,3 kW bei 435 W Sonne — 4,2 kW kamen aus dem Hausspeicher,
        855 W aus dem Netz. Am nächsten Morgen dasselbe bei 5 % Ladestand:
        5,2 kW ins Auto, 5,8 kW aus dem Netz.

        Deshalb hier die zweite Richtung: Widerspricht die Messung dem Befehl
        über mehrere Takte, wird der Dedup-Eintrag verworfen und der Befehl
        erneut geschickt. Bleibt es dabei, erfährt es der Nutzer — dann liegt
        es am Gerät, nicht mehr an der Regelung.
        """
        if measured_w <= allowed_w:
            dev.ignored_ticks = 0
            if dev.uncontrolled:
                dev.uncontrolled = False
                # Nur, wenn der Ungehorsam auch gemeldet wurde – sonst stünde
                # ein 'folgt wieder" ohne vorheriges 'folgt nicht" im Protokoll.
                if getattr(dev, "obey_logged", False):
                    dev.obey_logged = False
                    await log_event(f"{dev.name} folgt wieder", category="control")
            return

        # Karenzzeit nach dem letzten Stellbefehl.
        #
        # Ein Geraet folgt einem neuen Sollwert nicht in derselben Sekunde:
        # Ein Fahrzeug ueber den BLE-Proxy braucht Sekunden bis Minuten, ein
        # Heizstab regelt seine Leistung langsam herunter, und beide melden
        # in der Zwischenzeit noch den alten Wert zurueck.
        #
        # Ohne diese Wartezeit meldete der Regelkreis genau das als Verstoss.
        # Im Bericht steht zweimal 'Tesla folgt der Regelung nicht: aus,
        # gemessen 11136 W" -- das sind exakt 16 A auf drei Phasen, also der
        # Messwert VOR dem Stoppbefehl, abgelesen unmittelbar danach. Das
        # Fahrzeug hatte nichts falsch gemacht.
        driver = getattr(dev, "driver", None)
        settle_s = max(OBEY_SETTLE_S, float(getattr(driver, "read_timeout_s", 0.0) or 0.0) * 2)
        last_command = max(getattr(dev, "sent_at", {}).values(), default=0.0)
        if time.monotonic() - last_command < settle_s:
            return

        dev.ignored_ticks += 1
        if dev.ignored_ticks < OBEY_CONFIRM_TICKS:
            return
        dev.ignored_ticks = 0
        # Dedup verwerfen: Nur so kommt derselbe Sollwert noch einmal aufs Gerät.
        dev.sent.clear()
        dev.send_backoff.clear()
        if dev.uncontrolled:
            return  # schon gemeldet – weiter nachfassen, aber nicht weiter melden
        dev.uncontrolled = True
        dev.obey_count = getattr(dev, "obey_count", 0) + 1
        msg = f"{dev.name} folgt der Regelung nicht (Soll {what}, gemessen {measured_w:.0f} W)"
        # Höchstens eine Warnung je OBEY_WARN_REPEAT_S und Gerät. Im
        # Diagnosebericht standen an einem Nachmittag sieben Paare 'folgt
        # nicht"/'folgt wieder" – dieselbe Ursache, siebenmal gemeldet. Die
        # Diagnose zählt die Fälle trotzdem (obey_count, Ereignisse).
        now = time.monotonic()
        if now - getattr(dev, "last_obey_warning", -1e9) >= OBEY_WARN_REPEAT_S:
            dev.last_obey_warning = now
            dev.obey_logged = True
            await log_event(msg, level="warning", category="control")
            if self.notifier:
                await self.notifier.send(f"⚠️ {msg}")
        else:
            log.info(msg)

    async def _apply_wallbox(self, dev: ManagedDevice, d: WallboxDecision) -> None:
        driver: WallboxDriver = dev.driver  # type: ignore[assignment]
        dev.decision = {"enable": d.enable, "current_a": round(d.current_a, 1), "phases": d.phases, "reason": d.reason}
        if getattr(dev.data, "vehicle_reachable", None) is False:
            # Kein BLE-Befehl ins Leere: Er kostet 25 s Timeout, schlägt fehl
            # und landet im Fehler-Backoff. Sobald das Fahrzeug wieder
            # antwortet, geht der aktuelle Sollzustand ohnehin raus.
            return
        if isinstance(dev.data, WallboxData):
            # Zwei sehr verschiedene Fälle, deshalb zwei Toleranzen.
            #
            # 'Soll aus, läuft trotzdem" ist das eigentliche Problem: ein
            # Fahrzeug, das sich selbst wieder einschaltet. Da genügt jede
            # nennenswerte Leistung als Beweis.
            #
            # 'Läuft, zieht aber mehr als zugeteilt" ist meistens harmlos.
            # Beim Hochrampen liegt die Messung kurz über dem Sollwert, und
            # dreiphasig ist eine einzige Ampere-Stufe schon 690 W. Mit der
            # alten Pauschale von 25 % schlug das im Bericht viermal an einem
            # Tag an und löste sich in derselben Minute wieder auf – Meldungen,
            # die nichts bedeuten, kosten nur Vertrauen. Deshalb zusätzlich
            # eine ganze Stromstufe Luft.
            if not d.enable:
                allowed, what = UNCONTROLLED_W, "aus"
                # Selbststart (Auto lädt beim Anstecken von selbst los, oder
                # ein Abfahrtstimer startet): nicht erst vier Takte zuschauen,
                # sondern den Stopp sofort erneut senden. Im Diagnosebericht
                # lief das Auto so jedes Mal rund eine Minute mit 4–5 kW, bevor
                # die Regelung reagierte – und schrieb eine Warnung dazu.
                if dev.data.power > UNCONTROLLED_W and not dev.self_start_handled:
                    dev.self_start_handled = True
                    dev.ignored_ticks = 0
                    dev.sent.pop("enable", None)
                    dev.send_backoff.pop("enable", None)
                    await log_event(
                        f"{dev.name} startet selbst ({dev.data.power / 1000:.1f} kW) – gestoppt",
                        category="control", data={"reason": d.reason or "kein Überschuss"},
                    )
                elif dev.data.power <= UNCONTROLLED_W:
                    dev.self_start_handled = False
            else:
                step = getattr(dev.controller, "watts_per_amp", lambda: VOLTAGE)()
                allowed = d.power_w + max(UNCONTROLLED_W, step, d.power_w * 0.25)
                what = f"höchstens {d.power_w:.0f} W"
            await self._obeyed(dev, dev.data.power, allowed, what)
        if d.phases is not None and "phase_switch" in driver.meta.capabilities:
            # Vor der Umschaltung Ladung stoppen (viele Boxen verlangen das)
            await self._send(dev, "enable", False, lambda: driver.stop_charging())
            await self._send(dev, "phases", d.phases, lambda: driver.set_phases(d.phases))
        if d.enable:
            # Abrunden, nicht runden: `d.power_w` – und damit die gesamte
            # Topf-Rechnung der Prioritätskette – gilt für genau diesen Strom.
            # Ein kaufmännisch aufgerundetes Ampere wären einphasig 230 W, die
            # niemand zugeteilt hat und die aus Speicher oder Netz kämen.
            amps = math.floor(d.current_a + 1e-9)
            if abs(float(dev.sent.get("current", -1)) - amps) >= 1:
                await self._send(dev, "current", amps, lambda: driver.set_current(amps))
            await self._send(dev, "enable", True, lambda: driver.start_charging())
        else:
            await self._send(dev, "enable", False, lambda: driver.stop_charging())

    async def _apply_heater(self, dev: ManagedDevice, power_w: float, reason: str) -> None:
        driver: WaterHeaterDriver = dev.driver  # type: ignore[assignment]
        dev.decision = {"power_w": round(power_w), "reason": reason}
        data = dev.data
        if isinstance(data, WaterHeaterData):
            if data.device_mode:
                # Eigenes Programm des Geräts: kein Ungehorsam, sondern eine
                # Einstellung am Gerät. Einmal sauber melden, nicht im
                # Minutentakt warnen, und die Energie für die Diagnose zählen.
                await self._track_self_mode(dev, data)
            else:
                if dev.self_mode_since is not None:
                    await self._end_self_mode(dev)
                if power_w <= 0 and data.power > UNCONTROLLED_W and not dev.self_start_handled:
                    # Heizt trotz 0 W (z. B. nach abgelaufenem Watchdog):
                    # Sollwert sofort erneut senden statt vier Takte warten.
                    dev.self_start_handled = True
                    dev.ignored_ticks = 0
                    dev.sent.pop("power", None)
                    dev.send_backoff.pop("power", None)
                elif data.power <= UNCONTROLLED_W:
                    dev.self_start_handled = False
                # Gleiche Gegenprüfung wie bei der Wallbox.
                await self._obeyed(dev, data.power, power_w * 1.15 + UNCONTROLLED_W,
                                   f"höchstens {power_w:.0f} W")
        # 10-W-Raster, abgerundet: fein genug, damit der Lückenfüller kleine
        # Reste wirklich verwertet, und grob genug, dass nicht bei jedem
        # Rauschen ein neuer Sollwert geschrieben wird. Abrunden statt runden,
        # damit nie mehr angefordert wird, als an Überschuss da ist.
        stepped = math.floor(max(0.0, power_w) / HEATER_STEP_W) * HEATER_STEP_W
        await self._send(dev, "power", stepped, lambda: driver.set_power(stepped))

    async def _track_self_mode(self, dev: ManagedDevice, data: WaterHeaterData) -> None:
        now = time.monotonic()
        if dev.self_mode_since is None:
            dev.self_mode_since = now
            dev.self_mode_wh = 0.0
            dev._self_mode_last = now  # type: ignore[attr-defined]
            dev.ignored_ticks = 0
            dev.uncontrolled = False
            await log_event(
                f"{dev.name} heizt per Geräteprogramm ({data.power / 1000:.1f} kW)",
                category="control", data={"kind": "device_program", "detail": data.device_mode},
            )
            return
        last = getattr(dev, "_self_mode_last", now)
        dt = min(now - last, max(2.5 * self.cfg.interval_s, 10.0))
        dev._self_mode_last = now  # type: ignore[attr-defined]
        dev.self_mode_wh += data.power * dt / 3600.0

    async def _end_self_mode(self, dev: ManagedDevice) -> None:
        minutes = (time.monotonic() - (dev.self_mode_since or time.monotonic())) / 60.0
        kwh = dev.self_mode_wh / 1000.0
        dev.self_mode_since = None
        dev.self_mode_wh = 0.0
        await log_event(
            f"{dev.name}: Geräteprogramm beendet ({kwh:.1f} kWh, {minutes:.0f} min)",
            category="control",
            data={"kwh": round(kwh, 2), "minutes": round(minutes), "kind": "device_program"},
        )

    # -------------------------------------------------- Batterie-Plan

    def _grid_intents(
        self, wallboxes: list[ManagedDevice], heaters: list[ManagedDevice], cheap: bool,
    ) -> dict[int, tuple[str, float, bool]]:
        """Lasten, die gerade (oder in diesem Takt) absichtlich Netzstrom
        ziehen: {id: ("Name (Grund)", erwartete Leistung W, wegen günstigem Preis?)}."""
        out: dict[int, tuple[str, float, bool]] = {}
        for dev in wallboxes:
            ctrl = dev.controller
            data = dev.data
            if not (dev.online and isinstance(ctrl, WallboxController) and isinstance(data, WallboxData)):
                continue
            target_grid = False
            if ctrl.s.mode == "target":
                try:
                    target_grid = ctrl._target_needs_grid(data, ChargeContext(now=local_now()))
                except Exception:  # noqa: BLE001 – Zielzeit kaputt → kein Netzwunsch
                    target_grid = False
            why = wallbox_grid_intent(
                mode=ctrl.s.mode, override=ctrl.override, state=data.state,
                reachable=data.vehicle_reachable, price_cheap=cheap,
                schedule_active=any_window_active(dev.schedules), target_needs_grid=target_grid,
            )
            if why:
                out[dev.id] = (f"{dev.name} ({why})", max(data.power, ctrl._max_power()), why == "günstiger Strom")
        for dev in heaters:
            ctrl = dev.controller
            data = dev.data
            if not (dev.online and isinstance(ctrl, WaterHeaterController) and isinstance(data, WaterHeaterData)):
                continue
            if data.device_mode:
                continue  # Eigenbetrieb – eigene Zeile der Tabelle
            why = heater_grid_intent(
                mode=ctrl.s.mode, boost=ctrl.boost, temperature_c=data.temperature_c,
                target_c=ctrl.s.target_temp_c, price_cheap=cheap,
                schedule_active=any_window_active(dev.schedules),
            )
            if why:
                out[dev.id] = (f"{dev.name} ({why})", max(data.power, ctrl.s.max_power_w), why == "günstiger Strom")
        return out

    def _foreign_loads(self, wallboxes: list[ManagedDevice], heaters: list[ManagedDevice]) -> list[str]:
        """Lasten, die gegen den Befehl laufen: Geräteprogramm (ELWA) oder
        ein Auto, das selbst angefangen hat zu laden."""
        names: list[str] = []
        for dev in heaters:
            data = dev.data
            if dev.online and isinstance(data, WaterHeaterData) and data.device_mode and data.power > UNCONTROLLED_W:
                names.append(f"{dev.name} (Geräteprogramm)")
        for dev in wallboxes:
            data = dev.data
            if (dev.online and isinstance(data, WallboxData) and dev.sent.get("enable") is False
                    and data.power > UNCONTROLLED_W and data.vehicle_reachable is not False):
                names.append(f"{dev.name} (Selbststart)")
        return names

    def _inverter_holds_reserve(self, battery_devs: list[ManagedDevice]) -> bool:
        """Hält der Wechselrichter die Reserve selbst (Min-SoC bestätigt)?"""
        if not battery_devs:
            return False
        reserve = float(self.cfg.battery_reserve_soc)
        for dev in battery_devs:
            if dev.inverter_reserve is None or dev.inverter_reserve + 0.5 < reserve:
                return False
        return True

    async def _execute_battery_plan(
        self, battery_devs: list[ManagedDevice], plan: BatteryPlan, battery: BatteryData | None, *,
        bat_power_s: float, grid_power: float, pv_power: float, house_power: float,
        expected_grid_w: float,
    ) -> tuple[bool, float]:
        """Plan in Gerätebefehle umsetzen.

        Rückgabe: (geschützt, Netzanteil W). 'Geschützt" heißt: Der Speicher
        gibt garantiert nichts an Lasten mit Netzstrom ab – nur dann dürfen
        sie Netzstrom ziehen. Ein fehlgeschlagener oder (laut Readback) nicht
        übernommener Befehl zählt NICHT als geschützt; bis 2.15 meldete
        `_lock_battery` 'gesperrt", sobald der Treiber set_mode kannte – auch
        wenn das Schreiben scheiterte."""
        if not battery_devs:
            # Kein steuerbares Batteriegerät: geschützt nur, wenn es gar keinen
            # Speicher gibt (Fronius/Huawei/SMA liefern ihn im WR mit).
            return battery is None, 0.0
        now = time.monotonic()
        protected = True
        share = 0.0
        for dev in battery_devs:
            driver = dev.driver
            enabled = not hasattr(driver, "control_enabled") or driver.control_enabled()
            if not dev.online or not enabled:
                protected = False
                continue
            intent = plan.intent
            power: float | None = None
            if intent == BatteryIntent.AUTO:
                dev.discharge_guard.reset()
                mode = BatteryMode.AUTO
            elif intent == BatteryIntent.CHARGE:
                dev.discharge_guard.reset()
                power = plan.power_w
                mode = BatteryMode.FORCE_CHARGE
                if power is not None and power < 200:
                    mode, power = BatteryMode.HOLD, None  # Netzanschluss ausgeschöpft
                share = power or 0.0
            elif intent == BatteryIntent.HOLD:
                mode = BatteryMode.HOLD
            elif intent == BatteryIntent.DISCHARGE:
                mode, power = BatteryMode.FORCE_DISCHARGE, plan.power_w
            else:  # NO_DISCHARGE
                step = dev.discharge_guard.step(
                    now, battery_w=bat_power_s, grid_w=grid_power, pv_w=pv_power,
                    house_w=house_power, expected_load_w=expected_grid_w,
                    soc=battery.soc if battery else None, reserve_soc=self.cfg.battery_reserve_soc,
                )
                if step.mode == GuardMode.AUTO:
                    mode = BatteryMode.AUTO
                elif step.mode == GuardMode.HOUSE:
                    mode, power = BatteryMode.FORCE_DISCHARGE, step.power_w
                else:
                    mode = BatteryMode.HOLD
            if _implements(driver, "set_mode"):
                await self._command_battery(dev, mode, power)
            elif _implements(driver, "set_reserve_soc") and intent != BatteryIntent.CHARGE:
                # Rückfall ohne Moduswahl: Reserve auf den aktuellen Stand →
                # er entlädt nicht weiter (auch nicht fürs Haus).
                hold = intent in (BatteryIntent.NO_DISCHARGE, BatteryIntent.HOLD)
                soc_now = battery.soc if battery else 0.0
                value = int(min(100, math.ceil(soc_now))) if hold else int(self.cfg.battery_reserve_soc)
                await self._send(dev, "price_reserve", value, lambda v=value: driver.set_reserve_soc(v))
            else:
                protected = False
                continue
            failed = bool(dev.command_error and dev.command_error.startswith(("battery_mode:", "price_reserve:")))
            if failed or dev.battery_mismatch_reported:
                protected = False
        protected = protected and plan.intent in (
            BatteryIntent.NO_DISCHARGE, BatteryIntent.HOLD, BatteryIntent.CHARGE,
        )
        return protected, share

    async def _note_battery_plan(self, plan: BatteryPlan) -> None:
        """Automatische Wechsel des Speichers protokollieren – kurz.

        Bis 2.15 stand im Protokoll nichts, wenn die Automatik den Speicher
        sperrte oder aus dem Netz lud. Am 08.10. lud er so 1,5 h mit 5 kW,
        ohne einen einzigen Eintrag."""
        key = (plan.intent.value, plan.source)
        if key == self._battery_plan_logged:
            return
        now = time.monotonic()
        if plan.source == "manual":
            self._battery_plan_logged = key  # manuelle Befehle protokolliert die API
            return
        if (now - self._battery_plan_logged_at) < BATTERY_EVENT_MIN_S and plan.intent != BatteryIntent.CHARGE:
            return
        first = self._battery_plan_logged is None
        self._battery_plan_logged = key
        self._battery_plan_logged_at = now
        if first and plan.intent == BatteryIntent.AUTO:
            return
        text = INTENT_LABELS[plan.intent] if plan.intent == BatteryIntent.AUTO else plan.reason
        await log_event(f"Batterie: {text}", category="battery",
                        data={"intent": plan.intent.value, "source": plan.source})

    async def _apply_battery(self, dev: ManagedDevice) -> None:
        """Die Reserve in den Wechselrichter schreiben (Min-SoC).

        Standard seit 2.16 – vorher nur auf ausdrücklichen Wunsch, und dann
        lehnte der Sungrow den Wert 55 % ab (erlaubt sind dort 0–50 %), was
        niemand sah: Der Speicher fiel jede Nacht bis auf 5 %. Jetzt wird auf
        das geklemmt, was das Gerät annimmt; den Rest bis zur Reserve hält
        MinePower selbst (Entladesperre, Zeile 5 der Tabelle)."""
        driver: BatteryDriver = dev.driver  # type: ignore[assignment]
        if not dev.settings.get("manage_reserve", True):
            dev.inverter_reserve = None
            return
        if not dev.online or not self.battery_controllable(dev) or not _implements(driver, "set_reserve_soc"):
            return
        if not _implements(driver, "set_mode") and self.battery_plan.intent != BatteryIntent.AUTO:
            return  # Rückfall ohne Moduswahl: Die Reserve dient gerade als Sperre
        cap = float(getattr(driver, "reserve_soc_max", 100.0) or 100.0)
        value = int(max(0.0, min(float(self.cfg.battery_reserve_soc), cap)))
        before = dev.command_error
        await self._send(dev, "reserve", value, lambda: driver.set_reserve_soc(value))
        if dev.sent.get("reserve") == value:
            dev.inverter_reserve = float(value)
            dev.inverter_reserve_error = None
        elif dev.command_error and dev.command_error != before and dev.command_error.startswith("reserve:"):
            dev.inverter_reserve = None
            if dev.inverter_reserve_error != dev.command_error:
                dev.inverter_reserve_error = dev.command_error
                await log_event(
                    f"{dev.name}: Reserve {value} % nicht übernommen – MinePower hält sie selbst",
                    level="warning", category="battery",
                    data={"detail": dev.command_error},
                )

    async def _apply_safe_stop(self, dev: ManagedDevice, reason: str) -> None:
        if not dev.online:
            return
        dev.decision = {"enable": False, "reason": reason}
        if isinstance(dev.driver, WallboxDriver):
            await self._send(dev, "enable", False, lambda: dev.driver.stop_charging())
        elif isinstance(dev.driver, WaterHeaterDriver):
            await self._send(dev, "power", 0.0, lambda: dev.driver.set_power(0.0))

    # ------------------------------------------------------------ Sessions

    async def _track_session(self, dev: ManagedDevice, grid_power: float, price_ct: float | None) -> None:
        data = dev.data
        if not isinstance(data, WallboxData):
            return
        now = time.monotonic()
        # Obergrenze am Takt statt an einer festen Minute: Fällt ein Takt aus,
        # darf die aktuelle Leistung nicht über die gesamte Lücke hochgerechnet
        # werden. Mit den alten 60 s blähte ein einzelner Aussetzer bei 15 s
        # Takt die Sessionenergie auf das Vierfache auf – im Diagnosebericht
        # standen 1,1 kWh für sechs Minuten Ladung, also fast 10 kW an einem
        # einphasigen 16-A-Anschluss, der höchstens 3,7 kW kann.
        max_gap = max(2.5 * self.cfg.interval_s, 10.0)
        dt_h = min(now - dev._last_integrate, max_gap) / 3600.0
        dev._last_integrate = now
        charging = data.state == WallboxState.CHARGING and data.power > 100

        if charging:
            e = data.power * dt_h / 1000.0
            solar_w = max(0.0, data.power - max(0.0, grid_power))
            dev.session_energy_kwh += e
            dev.session_solar_kwh += solar_w * dt_h / 1000.0
            if price_ct is not None:
                grid_share = data.power - solar_w
                dev.session_cost += (grid_share * dt_h / 1000.0) * price_ct / 100.0

        try:
            if charging and dev.session_id is None:
                async with async_session() as session:
                    cs = ChargeSession(device_id=dev.id, rfid_tag=data.rfid_tag)
                    session.add(cs)
                    await session.commit()
                    await session.refresh(cs)
                    dev.session_id = cs.id
                dev.session_energy_kwh = dev.session_solar_kwh = dev.session_cost = 0.0
                await log_event(f"Ladevorgang gestartet: {dev.name}", category="control")
            elif dev.session_id is not None:
                async with async_session() as session:
                    cs = await session.get(ChargeSession, dev.session_id)
                    if cs:
                        cs.energy_kwh = round(dev.session_energy_kwh, 3)
                        cs.solar_kwh = round(min(dev.session_solar_kwh, dev.session_energy_kwh), 3)
                        cs.cost_eur = round(dev.session_cost, 2)
                        if not charging and data.state in (WallboxState.IDLE, WallboxState.COMPLETE, WallboxState.CONNECTED):
                            cs.ended_at = datetime.now(timezone.utc)
                            dev.session_id = None
                            # Eine Ladung, bei der nie nennenswert Energie
                            # geflossen ist, war keine. Sie entsteht, wenn ein
                            # Fahrzeug kurz 'lädt" meldet und sofort wieder
                            # abbricht – das Protokoll lief damit voll mit
                            # '0.0 kWh"-Paaren, die nichts aussagen und die
                            # echten Vorgänge zudecken.
                            if cs.energy_kwh < 0.05:
                                await session.delete(cs)
                            else:
                                await log_event(
                                    f"Ladevorgang beendet: {dev.name} ({cs.energy_kwh:.1f} kWh, "
                                    f"davon Solar {cs.solar_kwh:.1f} kWh)",
                                    category="control",
                                )
                            if self.notifier:
                                await self.notifier.send(
                                    f"🔋 Ladung beendet an {dev.name}: {cs.energy_kwh:.1f} kWh "
                                    f"(Solaranteil {100 * cs.solar_kwh / cs.energy_kwh if cs.energy_kwh else 0:.0f} %)"
                                )
                    await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("Session-Tracking-Fehler bei %s: %s", dev.name, exc)

    # ------------------------------------------------------------ Publish

    async def _publish(
        self,
        pv: float,
        grid: float | None,
        battery: BatteryData | None,
        wb_power: float,
        wh_power: float,
        budget: float,
        price_ct: float | None = None,
        price_cheap: bool | None = None,
        safety: str | None = None,
        waste_w: float = 0.0,
        waste_reason: str | None = None,
        gap_filled_w: float = 0.0,
    ) -> None:
        grid_f = float(grid) if grid is not None else 0.0
        house = max(0.0, grid_f + pv - wb_power - wh_power - (battery.power if battery else 0.0))

        devices_payload = []
        for dev in self.devices.values():
            payload: dict[str, Any] = {
                "id": dev.id,
                "name": dev.name,
                "category": dev.category,
                "driver_id": dev.driver_id,
                "enabled": dev.enabled,
                "online": dev.online,
                "last_error": dev.last_error,
                "command_error": dev.command_error,
                # Gerät läuft, obwohl es das nicht soll – sichtbar machen,
                # nicht nur ins Protokoll schreiben.
                "uncontrolled": dev.uncontrolled,
                "decision": dev.decision,
                # Wie alt der letzte Messwert ist – langsame Geräte (Fahrzeug
                # über BLE) lesen im Hintergrund, die Oberfläche soll das
                # sehen können statt einen alten Wert für aktuell zu halten.
                "last_seen": dev.last_seen.isoformat() if dev.last_seen else None,
                # Fähigkeiten mitsenden, damit die Oberfläche nur Bedienelemente
                # zeigt, die das Gerät auch wirklich kann (z. B. Ladegrenze).
                "capabilities": sorted(dev.driver.meta.capabilities),
            }
            if dev.data is not None:
                payload["data"] = dev.data.model_dump()
            if isinstance(dev.controller, WallboxController):
                payload["mode"] = dev.controller.s.mode
                payload["override"] = dev.controller.override
                payload["override_state"] = dev.controller.override_state()
                payload["presence"] = self._presence(dev)
                payload["phases"] = dev.controller.phases
                # Ladegrenze: bevorzugt der Live-Wert aus dem Fahrzeug; schläft
                # es gerade, greift der zuletzt gesetzte Wert aus den Settings.
                live_limit = getattr(dev.data, "charge_limit_soc", None)
                payload["charge_limit_soc"] = (
                    live_limit if live_limit is not None else dev.settings.get("charge_limit_soc")
                )
                ctrl = dev.controller
                min_w = ctrl._min_power()
                # Phasen: was eingestellt ist und was das Fahrzeug wirklich
                # macht. Ein Tesla an einer dreiphasigen Wallbox lädt dreiphasig,
                # egal was hier steht – dann ist die Mindestleistung 4,1 kW,
                # nicht 1,4 kW.
                payload["phases_mode"] = ctrl.s.phases_mode
                payload["phases_detected"] = ctrl._phases_seen
                payload["min_power_w"] = round(min_w)
                payload["start_threshold_w"] = round(max(ctrl.s.start_threshold_w, min_w))
                payload["session"] = {
                    "active": dev.session_id is not None,
                    "energy_kwh": round(dev.session_energy_kwh, 3),
                    "solar_kwh": round(min(dev.session_solar_kwh, dev.session_energy_kwh), 3),
                    "cost_eur": round(dev.session_cost, 2),
                }
                payload["wake_note"] = dev.wake_note
            elif isinstance(dev.controller, WaterHeaterController):
                payload["mode"] = dev.controller.s.mode
                payload["boost"] = dev.controller.boost
                # Die beiden Temperaturschwellen gehören in den Snapshot: Die
                # Oberfläche zeigte bisher fest 60 °C an und bot den Boost auch
                # dann an, wenn der Speicher längst über der Boost-Grenze lag –
                # der Regler verwirft ihn dann sofort wieder, und für den
                # Nutzer sprang der Schalter ohne erkennbaren Grund zurück.
                payload["target_temp_c"] = dev.controller.s.target_temp_c
                payload["boost_temp_c"] = dev.controller.s.boost_temp_c
                # Restzeit und Abschaltbedingung des laufenden Boosts
                payload["boost_state"] = dev.controller.boost_state()
                payload["boost_end_mode"] = dev.controller.s.boost_end_mode
                payload["boost_duration_min"] = dev.controller.s.boost_duration_min
                payload["max_power_w"] = dev.controller.s.max_power_w
                payload["surplus_temp_c"] = dev.controller.surplus_cap()
                payload["self_mode_since_s"] = (
                    round(time.monotonic() - dev.self_mode_since) if dev.self_mode_since else None
                )
            elif dev.category == DeviceCategory.BATTERY.value:
                # Steuerbarkeit und Sollzustand – ohne das kann die Oberfläche
                # nicht sagen, WARUM ein Knopf nichts bewirkt.
                target = getattr(dev, "battery_target", None)
                payload["control_enabled"] = bool(
                    not hasattr(dev.driver, "control_enabled") or dev.driver.control_enabled()
                )
                payload["control_capable"] = _implements(dev.driver, "set_mode")
                payload["target_mode"] = target[0].value if target else None
                payload["target_power_w"] = target[1] if target else None
                payload["max_power_w"] = self._battery_max_power([dev])
                # Direkt nach einem Befehl ist ein abweichender Readback noch
                # kein Fehler – der Wechselrichter übernimmt mit Verzögerung.
                payload["mode_settling"] = (
                    time.monotonic() - dev.sent_at.get("battery_mode", -1e9) < BATTERY_SETTLE_S
                )
                payload["inverter_reserve"] = dev.inverter_reserve
                payload["reserve_max"] = getattr(dev.driver, "reserve_soc_max", None)
                payload["guard_mode"] = dev.discharge_guard.mode.value
            devices_payload.append(payload)

        self.snapshot = {
            "time": datetime.now(timezone.utc).isoformat(),
            "pv_power": round(pv, 1),
            "grid_power": round(grid_f, 1) if grid is not None else None,
            "house_power": round(house, 1),
            "wallbox_power": round(wb_power, 1),
            "water_power": round(wh_power, 1),
            "battery": {"soc": battery.soc, "power": battery.power} if battery else None,
            "surplus": round(budget, 1),
            "price_ct": price_ct,
            # Ob die aktuelle Stunde günstig ist, entscheidet der eingestellte
            # Grenzwert – das gehört hierher und nicht in die Oberfläche, die
            # ihn sonst raten müsste.
            "price_cheap": price_cheap,
            # Netzladefenster: Ein Gerät im Modus 'preisoptimiert" lädt gerade
            # bewusst aus dem Netz. `battery_locked` sagt, ob der Hausspeicher
            # dafür wirklich gesperrt werden konnte – ohne das würde der Strom
            # aus dem Speicher statt aus dem Netz kommen, und genau das soll
            # ablesbar sein statt geraten werden müssen.
            "grid_price_window": self.grid_price_window,
            "battery_locked": self.battery_locked,
            # Warum gerade so geregelt wird, im Klartext. Ohne diese beiden
            # Felder muss der Nutzer aus Zahlen erraten, ob die Preisschwelle
            # greift oder der Speicher Vorrang hat – die haeufigste Frage
            # ueberhaupt bei preisoptimiertem Laden.
            "price_reason": self.price_reason,
            "battery_gate_reason": self.battery_gate_reason,
            "battery_released": self.battery_released,
            "battery_grid_charging": self.battery_grid_charging,
            # Manueller Batteriebefehl: Modus, Leistung, Restzeit, Ziel.
            "battery_manual": self.battery_manual.as_dict() if self.battery_manual else None,
            "battery_manual_last": self.battery_manual_last,
            "battery_reserve_soc": self.cfg.battery_reserve_soc,
            # Was der Speicher gerade tut und warum (battery_policy)
            "battery_plan": {
                "intent": self.battery_plan.intent.value,
                "label": INTENT_LABELS[self.battery_plan.intent],
                "reason": self.battery_plan.reason,
                "source": self.battery_plan.source,
            },
            "battery_first": self.battery_first_state,
            "battery_priority_soc": self.cfg.battery_priority_soc,
            "grid_loads": list(self.grid_loads),
            # Netzanschluss: Grenze und was nach der Verteilung frei blieb.
            "grid_limit_w": round(self.grid_limit_w, 0),
            "grid_budget_left_w": self.grid_budget_left_w,
            "warnings": list(self.warnings),
            "safety": safety,
            "paused": self.paused,
            # Kennzahl 'verschenkter Solarstrom': Einspeisung über dem
            # Netz-Sollwert, die keine steuerbare Last aufgenommen hat.
            "waste_w": round(waste_w, 1),
            "waste_reason": waste_reason,
            # Was der Lückenfüller in diesem Tick gerettet hat
            "gap_filled_w": round(gap_filled_w, 1),
            "deadband_w": round(self.deadband_effective, 1),
            # Für die Anzeige des adaptiven Totbands: Spanne und Einstellung.
            "deadband_min_w": self.cfg.deadband_min_w,
            "deadband_max_w": self.cfg.deadband_max_w,
            "adaptive_deadband": self.cfg.adaptive_deadband,
            "interval_s": self.cfg.interval_s,
            "price_limit_ct": getattr(self.tariff, "cheap_limit_ct", None),
            "weather": self.volatility.describe(),
            "devices": devices_payload,
        }

        b = self.buffer
        b.add("site", "pv_power", pv)
        if grid is not None:
            b.add("site", "grid_power", grid_f)
        b.add("site", "house_power", house)
        b.add("site", "surplus", budget)
        b.add("site", "waste_power", waste_w)
        # Summen auch auf Anlagenebene führen: Ohne sie bleiben Verlaufs-Chart
        # und Diagnosebericht für Auto und Warmwasser leer, weil dort nur
        # `source="site"` abgefragt wird.
        b.add("site", "wallbox_power", wb_power)
        b.add("site", "water_power", wh_power)
        if battery:
            b.add("site", "battery_power", battery.power)
            b.add("site", "battery_soc", battery.soc)
        if price_ct is not None:
            b.add("site", "price_ct", price_ct)
        for dev in self.devices.values():
            if isinstance(dev.data, WallboxData):
                b.add(f"device:{dev.id}", "wallbox_power", dev.data.power)
                if dev.data.soc is not None:
                    b.add(f"device:{dev.id}", "vehicle_soc", dev.data.soc)
            elif isinstance(dev.data, WaterHeaterData):
                b.add(f"device:{dev.id}", "water_power", dev.data.power)
                if dev.data.temperature_c is not None:
                    b.add(f"device:{dev.id}", "water_temp", dev.data.temperature_c)
        if b.due():
            await b.flush()

        await self._track_waste(waste_w, waste_reason)
        await ws_manager.broadcast({"type": "snapshot", "data": self.snapshot})
        if self.mqtt:
            await self.mqtt.publish_snapshot(self.snapshot)
