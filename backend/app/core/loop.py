"""Der geschlossene Regelkreis (Control-Loop).

Ablauf je Tick (Default alle 3 s, konfigurierbar):
  1. Messwerte aller aktivierten Geräte parallel lesen (mit Timeout).
  2. Überschuss-Budget berechnen (geglättete Netzleistung, adaptives Totband).
  3. Die Entladeleistung des Hausspeichers abziehen (sie ist kein
     Solarüberschuss), den Rest entlang der Prioritätskette auf die
     steuerbaren Lasten verteilen. Seine *Lade*leistung wird nicht abgezogen –
     sie steht am Netzpunkt schon nicht mehr im Budget (siehe
     `regulation.releasable_battery_charge`).
  4. **Lückenfüller:** Was danach übrig bleibt und für die nächste grob
     gestufte Last zu klein wäre, geht an eine fein modulierbare Last –
     unabhängig von deren Platz in der Kette.
  5. Stellgrößen an die Geräte senden (nur bei Änderung – kein Hammering),
     Übernahme im Gerät gegenprüfen (Readback).
  6. Messwerte puffern, Snapshot per WebSocket senden, Sessions verfolgen.

Sicherheit: Fällt die Netzmessung aus, werden alle steuerbaren Lasten
pausiert (definiertes Fallback-Verhalten) statt blind weiterzuladen.
Geräte-Timeouts crashen den Loop nie.

Verschenkte Energie: Jeder Tick misst, wie viel Überschuss über dem
Netz-Sollwert eingespeist wurde, ohne dass eine steuerbare Last ihn
aufgenommen hat, und protokolliert dazu den Grund. Das macht das Regelziel
„keinen Watt verschenken“ überprüfbar statt behauptet.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db import async_session
from ..drivers import registry
from ..drivers.base import (
    BatteryData,
    BatteryDriver,
    DeviceCategory,
    InverterData,
    InverterDriver,
    MeterData,
    MeterDriver,
    WallboxData,
    WallboxDriver,
    WallboxState,
    WaterHeaterData,
    WaterHeaterDriver,
)
from ..models import ChargeSession, Device, Schedule, Setting
from ..services.audit import log_event
from ..services.measurements import MeasurementBuffer
from ..services.ws import ws_manager
from .regulation import (
    BATTERY_PROTECT_CONFIRM_S,
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
from .schedules import any_window_active

log = logging.getLogger(__name__)

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
#: Stellraster für modulierende Heizstäbe (W). Siehe _apply_heater.
HEATER_STEP_W = 10.0


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
        self.driver = registry.create_driver(row.driver_id, self.config)
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
        # „Befehl gesendet“ allein ist keine Erfolgsmeldung.
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
        „Nulleinspeisung" unmöglich macht.

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
        self.buffer = MeasurementBuffer()
        # Vollständig vorbelegt: Das Frontend bekommt beim allerersten Abruf
        # (vor dem ersten Tick) dieselbe Struktur wie später – sonst müsste es
        # überall gegen fehlende Felder absichern.
        self.snapshot: dict[str, Any] = {
            "time": None, "pv_power": 0.0, "grid_power": None, "house_power": 0.0,
            "wallbox_power": 0.0, "water_power": 0.0, "battery": None, "surplus": 0.0,
            "price_ct": None, "price_cheap": None, "safety": None, "paused": False, "waste_w": 0.0,
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
        for dev in self.devices.values():
            try:
                await dev.driver.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def request_reload(self) -> None:
        """Von der API gerufen, wenn Geräte/Einstellungen geändert wurden."""
        self._reload_requested = True

    async def _run(self) -> None:
        log.info("Control-Loop gestartet (Intervall %.1f s)", self.cfg.interval_s)
        while self.running:
            started = time.monotonic()
            try:
                if self._reload_requested:
                    await self._reload()
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 – der Loop darf nie sterben
                log.exception("Fehler im Control-Loop: %s", exc)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.5, self.cfg.interval_s - elapsed))

    # ------------------------------------------------------------ Reload

    async def _reload(self) -> None:
        self._reload_requested = False
        async with async_session() as session:
            rows = (await session.scalars(select(Device))).all()
            schedules = (await session.scalars(select(Schedule))).all()
            settings_rows = (await session.scalars(select(Setting))).all()

        settings = {s.key: s.value for s in settings_rows}
        reg = settings.get("regulation", {})
        self.cfg = RegulationConfig(
            interval_s=float(reg.get("interval_s", self.cfg.interval_s)),
            grid_target_w=float(reg.get("grid_target_w", 0)),
            deadband_w=float(reg.get("deadband_w", 100)),
            smoothing_samples=int(reg.get("smoothing_samples", 3)),
            house_limit_a=float(reg.get("house_limit_a", 35)),
            battery_reserve_soc=float(reg.get("battery_reserve_soc", 20)),
            ev_lock_soc=float(reg.get("ev_lock_soc", 30)),
            battery_priority=bool(reg.get("battery_priority", True)),
            battery_drain_limit_w=float(reg.get("battery_drain_limit_w", 250)),
            adaptive_deadband=bool(reg.get("adaptive_deadband", True)),
            deadband_min_w=float(reg.get("deadband_min_w", 40)),
            deadband_max_w=float(reg.get("deadband_max_w", 400)),
            gap_fill_min_w=float(reg.get("gap_fill_min_w", 25)),
            use_forecast=bool(reg.get("use_forecast", True)),
        )
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
                    try:
                        await existing.driver.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                new_devices[row.id] = candidate
        for old_id, old in self.devices.items():
            if old_id not in new_devices:
                try:
                    await old.driver.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self.devices = new_devices

        by_device: dict[int, list[dict]] = {}
        for s in schedules:
            by_device.setdefault(s.device_id, []).append(
                {"days_mask": s.days_mask, "start_time": s.start_time, "end_time": s.end_time, "enabled": s.enabled}
            )
        for dev in self.devices.values():
            dev.schedules = by_device.get(dev.id, [])
        log.info("Konfiguration geladen: %d Geräte, Priorität %s", len(self.devices), self.priority)

    # ------------------------------------------------------------ Mess-Zyklus

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
            dev.last_error = None
            dev.last_seen = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            dev.failures += 1
            dev.last_error = f"{type(exc).__name__}: {exc}"
            dev.connected = False  # Auto-Reconnect beim nächsten Tick
            if dev.online and dev.failures >= OFFLINE_AFTER_FAILURES:
                dev.online = False
                await log_event(
                    f"Gerät offline: {dev.name} ({dev.last_error})", level="warning", category="device"
                )
                if self.notifier:
                    await self.notifier.send(f"⚠️ {dev.name} ist offline: {dev.last_error}")

    async def _note_vehicle_link(self, dev: ManagedDevice) -> None:
        """Abgerissenen Funkkontakt zum Fahrzeug sichtbar machen.

        Ein Fahrzeug, das per Funk nicht mehr antwortet, meldet nach einer
        Weile schlicht „nicht verbunden". Das ist von einem abgesteckten Auto
        nicht zu unterscheiden, und beides ist ein völlig normaler Zustand –
        deshalb nimmt der Loop das Gerät dafür bewusst *nicht* offline.

        Genau daraus wurde aber ein stiller Ausfall: zwei Sonnentage ohne eine
        einzige Ladung, während das Gerät durchgehend als online angezeigt
        wurde und im Protokoll nichts stand. Nicht offline melden heißt nicht
        gar nichts melden – einmal beim Wechsel, nicht in jedem Takt.
        """
        reachable = getattr(dev.data, "vehicle_reachable", None)
        if reachable is None or reachable == dev.vehicle_reachable:
            return
        first = dev.vehicle_reachable is None
        dev.vehicle_reachable = reachable
        if reachable:
            if not first:
                await log_event(f"Fahrzeug wieder erreichbar: {dev.name}", category="control")
            return
        if first:
            return  # beim Start nichts melden – da weiß noch niemand etwas
        await log_event(
            f"Fahrzeug antwortet nicht mehr: {dev.name}. Es wird kein Überschuss "
            f"mehr dorthin verteilt, bis es sich wieder meldet.",
            level="warning", category="control",
        )
        if self.notifier:
            await self.notifier.send(f"⚠️ {dev.name}: Fahrzeug antwortet nicht mehr – keine Ladung möglich")

    async def _step(self) -> None:
        active = [d for d in self.devices.values() if d.enabled]
        await asyncio.gather(*(self._read_device(d) for d in active))

        # --- Messgrößen aggregieren -----------------------------------
        pv_power = 0.0
        grid_power: float | None = None
        grid_from_inverter: float | None = None
        battery: BatteryData | None = None
        meter_online = False

        battery_from_inverter: BatteryData | None = None

        for dev in active:
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
                    battery_from_inverter = BatteryData(
                        soc=dev.data.battery_soc or 0.0,
                        power=dev.data.battery_power or 0.0,
                        capacity_kwh=dev.data.battery_capacity_kwh,
                        status="über Wechselrichter gelesen",
                    )
            elif isinstance(dev.data, MeterData):
                grid_power = dev.data.grid_power
                meter_online = True
            elif isinstance(dev.data, BatteryData):
                battery = dev.data

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
        # dem DTSU666). Der Zähler „sieht" die Ladung nicht, zeigt den PV-Überschuss
        # weiter als Einspeisung. Deshalb die Ladeleistung zur gemessenen Netzleistung
        # addieren → korrekte Regelung und Anzeige (kein Phantom-Export durchs Auto).
        if grid_power is not None:
            unmetered = sum(
                d.data.power for d in wallboxes
                if d.online and isinstance(d.data, WallboxData)
                and not d.settings.get("grid_metered", True)
            )
            grid_power += unmetered

        # --- Sicherheits-Fallback: keine verlässliche Netzmessung ------
        if not grid_ok or self.paused:
            reason = "Regelung pausiert" if self.paused else "Netzmessung ausgefallen – Lasten sicher pausiert"
            for dev in wallboxes + heaters:
                await self._apply_safe_stop(dev, reason)
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
        controllable = wb_power + wh_power
        budget = compute_budget(grid_smoothed, controllable, self.cfg, deadband_w=deadband)

        # --- Batterie schonen ------------------------------------------
        # Geglättet, weil hieran eine Abschaltung hängt: Ein Hybrid-
        # Wechselrichter regelt seine Batterie ständig um die Null herum, und
        # ein einzelner Ausreißer darf keine laufende Ladung beenden.
        bat_power_s = self.battery_smoother.add(bat_power)
        # Entlädt sich der Speicher, ist diese Leistung kein Solarüberschuss.
        # Ohne den Abzug würde die Batterie Auto und Warmwasser versorgen, bis
        # sie leer ist – am Netzpunkt sähe alles nach „ausgeglichen" aus.
        battery_guard = protected_battery_discharge(
            battery.soc if battery else None, bat_power_s, self.cfg
        )
        # Umgekehrter Fall – und hier steckte der teuerste Fehler der bisherigen
        # Fassung: Die LADEleistung wurde ebenfalls abgezogen. Sie ist am
        # Netzpunkt aber längst verbraucht und damit gar nicht im Budget; der
        # zweite Abzug rechnete dieselben Watt doppelt weg und ließ den Topf
        # bei ladendem Speicher gegen null laufen. Jetzt wird sie nur noch
        # *freigegeben*, wenn der Nutzer den Vorrang abgeschaltet hat – dann
        # darf die Kette dem Speicher etwas abnehmen, und das kommt hinzu.
        battery_release = releasable_battery_charge(
            battery.soc if battery else None, bat_power_s, self.cfg
        )
        # Für Snapshot und Diagnose festhalten: Ob der Schutz greift, muss
        # ablesbar sein – sonst bleibt bei „das Auto zieht aus der Batterie"
        # nur Raten übrig.
        self.battery_guard_w = battery_guard
        self.battery_release_w = battery_release
        # Abschalten erst bei nennenswerter *und* bestätigter Entladung,
        # Freigeben sofort. Abgeregelt wird davon unabhängig immer – dafür
        # sorgt der Abzug von `battery_guard` weiter unten im Topf.
        battery_protected = self.battery_protect_timer.check(
            battery_drain_alarm(battery_guard, self.cfg)
        )
        self.battery_protected = battery_protected

        # --- Überschuss-Topf & Prioritätskette -------------------------
        # Ein einziger Topf. Früher waren es zwei (PV + Batterie-Ladeleistung
        # für Lasten „vor der Batterie“), was von der Reihenfolge in der Kette
        # abhing und dadurch schwer vorhersagbar war: Dieselbe Wetterlage
        # führte je nach Sortierung zu anderem Verhalten. Jetzt entscheidet
        # allein die Einstellung, ob die Ladeleistung freigegeben wird – für
        # alle Lasten gleich.
        order = self._priority_order(wallboxes, heaters, battery_devs)
        pool = max(0.0, budget - battery_guard + battery_release)

        price_ct = cheap = None
        if self.tariff:
            price_ct = self.tariff.current_price_ct()
            cheap = self.tariff.is_cheap_hour()

        house_power = max(0.0, float(grid_power) + pv_power - wb_power - wh_power - bat_power)
        other_wb_current = 0.0
        recovery, forecast_surplus = self._forecast_context(pv_power, house_power)
        #: Für den Lückenfüller gemerkte Entscheidungen fein modulierbarer Lasten
        fine_loads: list[tuple[ManagedDevice, WaterHeaterDecision]] = []
        #: Warum blieb Überschuss liegen? (für die Kennzahl „verschenkt“)
        blockers: list[str] = []
        if self.cfg.battery_priority and bat_power_s > 0:
            blockers.append(
                f"Batterie lädt mit {bat_power_s:.0f} W – sie hat Vorrang"
            )
        if battery_guard > 0:
            blockers.append(
                f"Batterie entlädt mit {battery_guard:.0f} W – das ist kein Solarüberschuss"
            )

        for dev in order:
            if dev.category == DeviceCategory.BATTERY.value:
                continue  # der Speicher ist über battery_reserved schon bedient
            if not dev.online or dev.data is None or dev.controller is None:
                if dev.enabled and not dev.online:
                    blockers.append(f"{dev.name} offline")
                continue

            avail = pool

            ctx = ChargeContext(
                now=datetime.now(),
                price_ct=price_ct,
                cheap_hour=bool(cheap),
                schedule_active=any_window_active(dev.schedules),
                battery_soc=battery.soc if battery else None,
                battery_power=bat_power_s,
                house_current_a=house_power / (3 * VOLTAGE),
                other_wallbox_current_a=other_wb_current,
                house_limit_a=self.cfg.house_limit_a,
                solar_recovery_expected=recovery,
                forecast_surplus_kwh=forecast_surplus,
                battery_handled_globally=True,
                # „Geschont“ heißt: Der Speicher entlädt sich seit mindestens
                # BATTERY_PROTECT_CONFIRM_S. Nur dann müssen laufende Lasten
                # sofort abregeln. Beim Laden gibt es nichts abzuregeln – seine
                # Ladeleistung stand nie im Topf.
                battery_protected=battery_protected,
            )

            if isinstance(dev.controller, WallboxController):
                decision = dev.controller.decide(avail, dev.data, ctx)
                # Quantisierte Leistung verwenden: Die Box nimmt nur ganze
                # Ampere. Rechneten wir hier mit dem ungerundeten Wunschwert,
                # verschwände der abgerundete Rest aus der Bilanz – und würde
                # unbemerkt ins Netz gehen.
                allocated = decision.power_w
                other_wb_current += decision.current_a if decision.enable else 0.0
                if not decision.enable and decision.reason:
                    blockers.append(f"{dev.name}: {decision.reason}")
                await self._apply_wallbox(dev, decision)
            else:
                decision_wh = dev.controller.decide(avail, dev.data, ctx)
                allocated = decision_wh.power_w
                fine_loads.append((dev, decision_wh))
                if allocated <= 0 and decision_wh.reason:
                    blockers.append(f"{dev.name}: {decision_wh.reason}")

            pool = max(0.0, pool - allocated)

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
        Prioritätskette**, denn die Alternative ist nicht „später“, sondern
        „verschenkt“.

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

    def _priority_order(self, wallboxes, heaters, batteries) -> list[ManagedDevice]:
        chain = wallboxes + heaters + batteries
        by_id = {d.id: d for d in chain}
        ordered = [by_id[i] for i in self.priority if i in by_id]
        ordered += [d for d in chain if d.id not in self.priority]
        return ordered

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
        „manchmal trotzdem noch" lief: MinePower schickte die 0 W genau
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
        einem Abfahrtstimer. MinePower hatte „aus" längst gesendet, hält es für
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
                await log_event(f"{dev.name} folgt wieder der Regelung", category="control")
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
        msg = (f"{dev.name} folgt der Regelung nicht: {what}, gemessen "
               f"{measured_w:.0f} W. Befehl wird erneut gesendet.")
        await log_event(msg, level="warning", category="control")
        if self.notifier:
            await self.notifier.send(f"⚠️ {msg}")

    async def _apply_wallbox(self, dev: ManagedDevice, d: WallboxDecision) -> None:
        driver: WallboxDriver = dev.driver  # type: ignore[assignment]
        dev.decision = {"enable": d.enable, "current_a": round(d.current_a, 1), "phases": d.phases, "reason": d.reason}
        if isinstance(dev.data, WallboxData):
            # Zwei sehr verschiedene Fälle, deshalb zwei Toleranzen.
            #
            # „Soll aus, läuft trotzdem" ist das eigentliche Problem: ein
            # Fahrzeug, das sich selbst wieder einschaltet. Da genügt jede
            # nennenswerte Leistung als Beweis.
            #
            # „Läuft, zieht aber mehr als zugeteilt" ist meistens harmlos.
            # Beim Hochrampen liegt die Messung kurz über dem Sollwert, und
            # dreiphasig ist eine einzige Ampere-Stufe schon 690 W. Mit der
            # alten Pauschale von 25 % schlug das im Bericht viermal an einem
            # Tag an und löste sich in derselben Minute wieder auf – Meldungen,
            # die nichts bedeuten, kosten nur Vertrauen. Deshalb zusätzlich
            # eine ganze Stromstufe Luft.
            if not d.enable:
                allowed, what = UNCONTROLLED_W, "aus"
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
        if isinstance(dev.data, WaterHeaterData):
            # Gleiche Gegenprüfung wie bei der Wallbox. Ein my-PV-Gerät heizt
            # nach abgelaufenem Watchdog oder in einem eigenen Zeitprogramm
            # weiter — im Diagnosebericht nachts um vier mit 1163 W, während
            # der Speicher mit 1835 W leerlief.
            await self._obeyed(dev, dev.data.power, power_w * 1.15 + UNCONTROLLED_W,
                               f"höchstens {power_w:.0f} W")
        # 10-W-Raster, abgerundet: fein genug, damit der Lückenfüller kleine
        # Reste wirklich verwertet, und grob genug, dass nicht bei jedem
        # Rauschen ein neuer Sollwert geschrieben wird. Abrunden statt runden,
        # damit nie mehr angefordert wird, als an Überschuss da ist.
        stepped = math.floor(max(0.0, power_w) / HEATER_STEP_W) * HEATER_STEP_W
        await self._send(dev, "power", stepped, lambda: driver.set_power(stepped))

    async def _apply_battery(self, dev: ManagedDevice) -> None:
        driver: BatteryDriver = dev.driver  # type: ignore[assignment]
        # Aktive Batteriebefehle sind Zusatzfunktion, kein Automatismus:
        # Hybrid-Wechselrichter (Sungrow SH, Fronius GEN24, Huawei, SMA …)
        # regeln ihre Batterie selbst auf Nulleinspeisung. MinePower schreibt
        # deshalb standardmäßig NICHTS – die Software-Reserve (Prioritätskette
        # + EV-Sperre) schützt den Haushalt ohne Eingriff ins Gerät.
        if not dev.settings.get("manage_reserve", False):
            return
        if hasattr(driver, "control_enabled") and not driver.control_enabled():
            return  # Nutzer hat die aktive Steuerung nicht freigeschaltet
        reserve = int(dev.settings.get("reserve_soc", self.cfg.battery_reserve_soc))
        try:
            await self._send(dev, "reserve", reserve, lambda: driver.set_reserve_soc(reserve))
        except NotImplementedError:
            pass

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
                            # Fahrzeug kurz „lädt" meldet und sofort wieder
                            # abbricht – das Protokoll lief damit voll mit
                            # „0.0 kWh"-Paaren, die nichts aussagen und die
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
                # Fähigkeiten mitsenden, damit die Oberfläche nur Bedienelemente
                # zeigt, die das Gerät auch wirklich kann (z. B. Ladegrenze).
                "capabilities": sorted(dev.driver.meta.capabilities),
            }
            if dev.data is not None:
                payload["data"] = dev.data.model_dump()
            if isinstance(dev.controller, WallboxController):
                payload["mode"] = dev.controller.s.mode
                payload["override"] = dev.controller.override
                payload["phases"] = dev.controller.phases
                # Ladegrenze: bevorzugt der Live-Wert aus dem Fahrzeug; schläft
                # es gerade, greift der zuletzt gesetzte Wert aus den Settings.
                live_limit = getattr(dev.data, "charge_limit_soc", None)
                payload["charge_limit_soc"] = (
                    live_limit if live_limit is not None else dev.settings.get("charge_limit_soc")
                )
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
            "safety": safety,
            "paused": self.paused,
            # Kennzahl „verschenkter Solarstrom“: Einspeisung über dem
            # Netz-Sollwert, die keine steuerbare Last aufgenommen hat.
            "waste_w": round(waste_w, 1),
            "waste_reason": waste_reason,
            # Was der Lückenfüller in diesem Tick gerettet hat
            "gap_filled_w": round(gap_filled_w, 1),
            "deadband_w": round(self.deadband_effective, 1),
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

        await ws_manager.broadcast({"type": "snapshot", "data": self.snapshot})
        if self.mqtt:
            await self.mqtt.publish_snapshot(self.snapshot)
