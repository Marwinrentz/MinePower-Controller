"""Treiber-Abstraktion – zentrales Erweiterungs-Interface.

Vorzeichen-Konventionen (überall im System einheitlich):
  * grid_power:    + = Netzbezug, − = Einspeisung  [W]
  * battery_power: + = Batterie lädt, − = Batterie entlädt  [W]
  * Alle Leistungen in Watt, Energien in kWh, Ströme in Ampere.

Jeder Treiber deklariert seine Konfig-Felder über `DriverMeta.fields`;
daraus generiert der Setup-Assistent automatisch das Eingabeformular.
Neue Geräte = neue Datei unter app/drivers/ mit @register-Dekorator –
keine Änderungen am Kern nötig (siehe docs/adding-drivers.md).
"""
from __future__ import annotations

import abc
import asyncio
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class DeviceCategory(str, Enum):
    INVERTER = "inverter"
    METER = "meter"
    WALLBOX = "wallbox"
    WATER_HEATER = "water_heater"
    BATTERY = "battery"


class FieldType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    PASSWORD = "password"
    BOOLEAN = "boolean"
    SELECT = "select"


class ConfigField(BaseModel):
    """Ein Konfigurationsfeld, das der Treiber benötigt (Host, Port, Unit-ID …).
    `help` wird im GUI als Tooltip angezeigt – bitte immer verständlich befüllen."""

    key: str
    label: str
    type: FieldType = FieldType.TEXT
    required: bool = True
    default: Any = None
    options: list[dict[str, str]] | None = None  # für SELECT: [{value,label}]
    placeholder: str | None = None
    help: str | None = None


class Maturity(str, Enum):
    """Reifegrad – wird im GUI als Badge angezeigt, damit sich niemand blind
    auf ungeprüfte Registerkarten verlässt.

    Maßstab ist ausschließlich: Lief dieser Treiber schon einmal gegen das
    echte Gerät? Nicht, wie sorgfältig er geschrieben wurde. Ein Treiber, der
    sauber nach Herstellerdokumentation gebaut, aber nie angeschlossen war,
    ist EXPERIMENTAL – Registerbelegungen weichen in der Praxis regelmäßig
    von der Doku ab (Offsets, Skalierung, Vorzeichen).
    """

    STABLE = "stable"            # an echter Hardware im Dauerbetrieb erprobt
    BETA = "beta"                # an echter Hardware getestet, aber nicht dauerhaft
    EXPERIMENTAL = "experimental"  # nie an echter Hardware gelaufen


class DriverMeta(BaseModel):
    id: str  # eindeutig, z. B. "sungrow_sh"
    name: str  # Anzeigename, z. B. "Sungrow SH-Serie (Hybrid)"
    category: DeviceCategory
    description: str = ""
    fields: list[ConfigField] = Field(default_factory=list)
    # Fähigkeiten, z. B. {"phase_switch", "soc", "battery_control", "modulation",
    # "battery_monitor", "hybrid", "fine_modulation", "write_test"}
    capabilities: set[str] = Field(default_factory=set)
    #: Bewusst EXPERIMENTAL als Vorgabe: Ein neuer Treiber hat per Definition
    #: noch an keiner Anlage gelaufen. Wer STABLE behauptet, muss es hinschreiben.
    maturity: Maturity = Maturity.EXPERIMENTAL
    #: Kurzer Warnhinweis fürs GUI (z. B. 'Register an SH10RT verifiziert,
    #: andere Modelle bitte per Verbindungstest gegenprüfen")
    notes: str | None = None
    #: Abhilfe, wenn das Gerät offline ist (Diagnose). Ohne Angabe: allgemein.
    offline_hint: str | None = None
    #: Abhilfe, wenn das Gerät im eigenen Programm läuft (Capability "own_program").
    own_program_hint: str | None = None


# ---------------------------------------------------------------- Messdaten

class InverterData(BaseModel):
    pv_power: float = 0.0            # W, aktuelle PV-Erzeugung
    daily_yield_kwh: float | None = None
    total_yield_kwh: float | None = None
    grid_power: float | None = None  # W, falls der WR den Netzzähler integriert hat
    status: str | None = None
    # Hybrid-Wechselrichter liefern die Batteriewerte gleich mit. Sie werden
    # rein zur Anzeige und für die Budgetrechnung gelesen – der Wechselrichter
    # regelt seine Batterie selbst (siehe BatteryDriver). Ein separates
    # Batteriegerät ist dafür ausdrücklich NICHT nötig.
    battery_soc: float | None = None
    battery_power: float | None = None   # + laden / − entladen
    battery_capacity_kwh: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MeterData(BaseModel):
    grid_power: float = 0.0          # W, + Bezug / − Einspeisung
    power_l1: float | None = None
    power_l2: float | None = None
    power_l3: float | None = None
    energy_import_kwh: float | None = None
    energy_export_kwh: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class WallboxState(str, Enum):
    IDLE = "idle"            # kein Fahrzeug verbunden
    CONNECTED = "connected"  # Fahrzeug verbunden, lädt nicht
    CHARGING = "charging"
    COMPLETE = "complete"    # Fahrzeug voll / Ladeziel erreicht
    ERROR = "error"


class WallboxData(BaseModel):
    state: WallboxState = WallboxState.IDLE
    power: float = 0.0               # W, aktuelle Ladeleistung
    current_set: float | None = None # A, aktuell gesetzter Sollstrom
    phases_active: int | None = None # 1 oder 3
    #: Gemessene Ladespannung je Phase in V, falls das Gerät sie meldet.
    #: Zusammen mit phases_active ergibt das den exakten Umrechnungsfaktor
    #: Ampere → Watt. Ohne diese Angabe muss der Regler mit 230 V rechnen,
    #: und das liegt je nach Netz und Ladeverlusten spürbar daneben.
    voltage: float | None = None
    energy_session_kwh: float | None = None
    soc: float | None = None         # % Fahrzeug-SoC, falls verfügbar (Best-Effort)
    #: Im Fahrzeug hinterlegte Ladegrenze in % (nur Fahrzeug-Treiber).
    #: Dauerhaft auf 100 % zu laden schadet Lithium-Akkus – deshalb ist das
    #: hier eine sichtbare, direkt bedienbare Stellgröße statt einer
    #: Einstellung, die man in der Hersteller-App suchen muss.
    charge_limit_soc: float | None = None
    #: Konnte der Ladepunkt das Fahrzeug zuletzt wirklich erreichen?
    #:
    #: Nur für Anbindungen, bei denen das eine eigene Frage ist (Funk, Cloud).
    #: ``None`` = der Treiber verfolgt das nicht, ``False`` = das Gerät selbst
    #: antwortet, das Fahrzeug daran aber seit Längerem nicht mehr.
    #:
    #: Ohne diese Unterscheidung sieht ein abgerissener Funkkontakt exakt aus
    #: wie ein abgestecktes Auto: Der Treiber meldet ``IDLE``, das Gerät gilt
    #: als online, und der Regler schweigt. Im Feld kostete das zwei volle
    #: Sonnentage Ladung, ohne dass irgendwo ein Hinweis auftauchte.
    vehicle_reachable: bool | None = None
    rfid_tag: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class WaterHeaterData(BaseModel):
    power: float = 0.0               # W, aktuelle Heizleistung
    temperature_c: float | None = None
    is_on: bool = False
    #: Läuft gerade ein Programm des Geräts selbst, das externe Sollwerte
    #: übergeht (my-PV: Status 'Boost' = Warmwasser-Sicherstellung oder
    #: Legionellenschutz)? Klartext, sonst None.
    #:
    #: Ohne diese Angabe meldete der Regelkreis jeden dieser Heizvorgänge als
    #: 'folgt der Regelung nicht' – im Diagnosebericht täglich um 06:00 und
    #: zwischen 17 und 19 Uhr Ortszeit, also exakt in den Zeitfenstern der
    #: Warmwasser-Sicherstellung des Geräts. Und er zählte die 3 kW als
    #: verteilbaren Überschuss, den es nie gab.
    device_mode: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class BatteryMode(str, Enum):
    AUTO = "auto"                 # Eigenverbrauchsoptimierung (Standard des Hybrid-WR)
    FORCE_CHARGE = "force_charge"
    FORCE_DISCHARGE = "force_discharge"
    HOLD = "hold"                 # weder laden noch entladen


class BatteryData(BaseModel):
    soc: float = 0.0                 # %
    power: float = 0.0               # W, + laden / − entladen
    mode: BatteryMode = BatteryMode.AUTO
    #: True, wenn `mode` wirklich aus dem Gerät gelesen wurde. Ohne diese
    #: Unterscheidung stünde bei jedem Treiber 'auto", auch wenn der
    #: Wechselrichter seit Stunden im Zwangsladen hängt – genau der Zustand,
    #: den der Regelkreis nach einem Absturz erkennen und beenden muss.
    mode_known: bool = False
    #: Im Gerät hinterlegte Lade-Obergrenze bzw. Entlade-Untergrenze (%),
    #: sofern lesbar. Eine Obergrenze von 20 % erklärt 'Batterie lädt nicht"
    #: sofort – ohne sie sucht man stundenlang in der Regelung.
    max_soc: float | None = None
    min_soc: float | None = None
    temperature_c: float | None = None
    capacity_kwh: float | None = None
    status: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    ok: bool
    message: str = ""
    values: dict[str, Any] = Field(default_factory=dict)  # Live-Readback fürs GUI
    #: Hinweise, die kein Fehler sind, aber Aufmerksamkeit verdienen
    #: (z. B. 'SoC nicht verfügbar", 'Registerkarte nicht verifiziert")
    warnings: list[str] = Field(default_factory=list)
    #: Ergebnis des optionalen Schreibtests (Befehl → Readback → übernommen?)
    write_test: dict[str, Any] | None = None


# ---------------------------------------------------------------- Interfaces

class BaseDriver(abc.ABC):
    """Gemeinsame Basis. Treiber dürfen NIE Exceptions in den Control-Loop
    durchlassen, die diesen crashen – Verbindungsfehler werden vom Loop
    gefangen und als offline markiert; trotzdem: intern Timeouts setzen!"""

    meta: ClassVar[DriverMeta]

    #: Wie lange der Control-Loop auf eine Antwort wartet, bevor er das Gerät
    #: als gestört zählt. Treiber mit langsamem Transport (Funk, BLE-Proxy,
    #: Cloud-API) erhöhen den Wert – sonst melden sie sich im Minutentakt
    #: fälschlich offline, obwohl sie nur langsam sind.
    read_timeout_s: float = 6.0

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def connect(self) -> None:
        """Verbindung aufbauen (idempotent, wird bei Reconnect erneut gerufen)."""

    async def disconnect(self) -> None:
        """Verbindung sauber schließen."""

    async def test_connection(self, include_write: bool = False) -> TestResult:
        """Verbindungstest mit Live-Readback für den Setup-Assistenten.

        `include_write=True` führt zusätzlich einen unkritischen Testbefehl
        aus (siehe :meth:`test_command`) und zeigt dessen Readback – damit
        sieht der Nutzer schon im Assistenten, ob das Gerät Befehle nicht nur
        quittiert, sondern auch wirklich übernimmt.
        """
        try:
            await self.connect()
            values = await self._probe()
            warnings = list(self._warnings(values))
            write_test = None
            if include_write:
                try:
                    write_test = await self.test_command()
                except Exception as exc:  # noqa: BLE001 – Schreibtest darf den Lesetest nicht kippen
                    write_test = {"ok": False, "message": _human(exc)}
            message = "Verbindung erfolgreich"
            if write_test is not None and not write_test.get("ok", False):
                message = "Verbindung steht, aber der Testbefehl wurde nicht übernommen"
            return TestResult(
                ok=True, message=message, values=values, warnings=warnings, write_test=write_test
            )
        except Exception as exc:  # noqa: BLE001 – bewusst breit für klare GUI-Meldung
            return TestResult(ok=False, message=_human(exc))
        finally:
            try:
                await self.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def _probe(self) -> dict[str, Any]:
        """Kategorie-spezifischer Beispiel-Read für test_connection()."""
        return {}

    def _warnings(self, values: dict[str, Any]) -> list[str]:
        """Hinweise zum Testergebnis (überschreibbar). Standard: Reifegrad."""
        out: list[str] = []
        if self.meta.maturity == Maturity.EXPERIMENTAL:
            out.append(
                "Dieser Treiber ist experimentell: Die Registerbelegung stammt aus der "
                "Herstellerdokumentation bzw. Community-Quellen und ist nicht an echter "
                "Hardware verifiziert. Bitte die angezeigten Live-Werte auf Plausibilität prüfen."
            )
        elif self.meta.maturity == Maturity.BETA:
            out.append(
                "Beta-Treiber: Grundfunktionen sind geprüft, einzelne Modell-/Firmware-Varianten "
                "können abweichen. Live-Werte bitte gegenprüfen."
            )
        if self.meta.notes:
            out.append(self.meta.notes)
        return out

    async def test_command(self) -> dict[str, Any] | None:
        """Unkritischer Testbefehl mit Readback (nur steuerbare Geräte).

        Rückgabe: ``{"ok": bool, "message": str, "sent": …, "readback": …}``.
        ``None`` bedeutet: Dieses Gerät kennt keinen sinnvollen Testbefehl.
        """
        return None


def _human(exc: BaseException) -> str:
    """Ausnahme → verständliche Meldung fürs GUI.

    Treiberfehler (validation.DriverError) tragen ihre Erklärung bereits im
    Text; alles andere bekommt einen Typ-Präfix, damit unerwartete Fehler
    nicht als vermeintliche Gerätemeldung durchgehen."""
    from .validation import DriverError

    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, DriverError):
        return text
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Zeitüberschreitung – Gerät antwortet nicht (IP, Port und Unit-ID prüfen)."
    return f"{type(exc).__name__}: {text}"


class InverterDriver(BaseDriver):
    @abc.abstractmethod
    async def read_data(self) -> InverterData: ...

    async def _probe(self) -> dict[str, Any]:
        d = await self.read_data()
        out: dict[str, Any] = {"PV-Leistung": _w(d.pv_power)}
        if d.grid_power is not None:
            out["Netzleistung"] = _grid(d.grid_power)
        if d.daily_yield_kwh is not None:
            out["Tagesertrag"] = f"{d.daily_yield_kwh:.2f} kWh"
        if d.total_yield_kwh is not None:
            out["Gesamtertrag"] = f"{d.total_yield_kwh:.0f} kWh"
        if d.status:
            out["Status"] = d.status
        for key, value in (d.extra or {}).items():
            out[key] = value
        return out

    def _warnings(self, values: dict[str, Any]) -> list[str]:
        out = super()._warnings(values)
        if values.get("PV-Leistung") == "0 W":
            out.append(
                "PV-Leistung ist 0 W. Nachts und bei Dunkelheit ist das korrekt – "
                "tagsüber deutet es auf eine falsche Registeradresse hin."
            )
        return out


class MeterDriver(BaseDriver):
    @abc.abstractmethod
    async def read_data(self) -> MeterData: ...

    async def _probe(self) -> dict[str, Any]:
        d = await self.read_data()
        out: dict[str, Any] = {"Netzleistung": _grid(d.grid_power)}
        for label, value in (("L1", d.power_l1), ("L2", d.power_l2), ("L3", d.power_l3)):
            if value is not None:
                out[label] = _w(value)
        if d.energy_import_kwh is not None:
            out["Bezug gesamt"] = f"{d.energy_import_kwh:.1f} kWh"
        if d.energy_export_kwh is not None:
            out["Einspeisung gesamt"] = f"{d.energy_export_kwh:.1f} kWh"
        for key, value in (d.extra or {}).items():
            out[key] = value
        return out

    def _warnings(self, values: dict[str, Any]) -> list[str]:
        out = super()._warnings(values)
        out.append(
            "Vorzeichen prüfen: Bei Netzbezug muss hier 'Bezug' stehen, bei Einspeisung "
            "'Einspeisung'. Stimmt das nicht, die Option 'Richtung invertieren' setzen – "
            "sonst regelt die Anlage in die falsche Richtung."
        )
        return out


class WallboxDriver(BaseDriver):
    """Auch Fahrzeug-Direktsteuerung (z. B. Tesla via BLE-Proxy) implementiert
    dieses Interface – das Auto wird damit selbst zum steuerbaren Ladepunkt."""

    #: Hardware-Grenzen (Treiber können sie aus config ableiten)
    min_current: float = 6.0
    max_current: float = 16.0

    @abc.abstractmethod
    async def read_data(self) -> WallboxData: ...

    @abc.abstractmethod
    async def set_current(self, amps: float) -> None:
        """Ladestrom in Ampere setzen (0 = Laden stoppen, sofern unterstützt)."""

    @abc.abstractmethod
    async def start_charging(self) -> None: ...

    @abc.abstractmethod
    async def stop_charging(self) -> None: ...

    async def set_phases(self, phases: int) -> None:
        """1↔3-phasig umschalten. Nur wenn capability 'phase_switch' gesetzt ist."""
        raise NotImplementedError

    async def set_charge_limit(self, soc: int) -> None:
        """Ziel-SoC im Fahrzeug setzen (nur Fahrzeug-Treiber)."""
        raise NotImplementedError

    async def _probe(self) -> dict[str, Any]:
        return format_wallbox_values(await self.read_data())

    async def test_command(self) -> dict[str, Any] | None:
        """Unkritischer Schreibtest: aktuellen Sollstrom neu setzen und
        zurücklesen. Ändert am Ladeverhalten nichts (derselbe Wert), zeigt
        aber, ob die Box Sollwerte überhaupt annimmt."""
        before = await self.read_data()
        target = before.current_set or self.min_current
        target = max(self.min_current, min(float(target), self.max_current))
        await self.set_current(target)
        await asyncio.sleep(1.0)  # Boxen übernehmen Sollwerte nicht instantan
        after = await self.read_data()
        got = after.current_set
        if got is None:
            message = (
                f"Sollstrom {target:.0f} A gesendet. Das Gerät meldet keinen Sollstrom zurück – "
                "die Übernahme lässt sich hier nicht prüfen (im Betrieb übernimmt das der Regelkreis)."
            )
            ok = True
        else:
            ok = abs(float(got) - target) <= 1.0
            message = (
                f"Sollstrom {target:.0f} A gesendet, Gerät meldet {float(got):.1f} A zurück"
                + (" – übernommen." if ok else " – NICHT übernommen.")
            )
        return {
            "ok": ok,
            "message": message,
            "sent": round(target, 1),
            "readback": round(float(got), 1) if got is not None else None,
        }


class WaterHeaterDriver(BaseDriver):
    #: Nennleistung in W (für Relais-Geräte = geschaltete Last)
    rated_power: float = 3000.0
    #: True, wenn stufenlos modulierbar (Lückenfüller-Logik nutzt das)
    modulating: bool = True

    @abc.abstractmethod
    async def read_data(self) -> WaterHeaterData: ...

    @abc.abstractmethod
    async def set_power(self, watts: float) -> None:
        """Leistung setzen. Modulierende Geräte (my-PV) stufenlos;
        Relais-Geräte interpretieren >0 als EIN, 0 als AUS."""

    async def _probe(self) -> dict[str, Any]:
        d = await self.read_data()
        out: dict[str, Any] = {"Heizleistung": _w(d.power), "Zustand": "ein" if d.is_on else "aus"}
        if d.temperature_c is not None:
            out["Temperatur"] = f"{d.temperature_c:.1f} °C"
        for key, value in (d.extra or {}).items():
            out[key] = value
        return out

    async def test_command(self) -> dict[str, Any] | None:
        """Unkritischer Schreibtest: 0 W setzen und Rückmeldung prüfen.
        0 W ist immer sicher – das Gerät heizt danach lediglich nicht."""
        await self.set_power(0.0)
        await asyncio.sleep(1.0)
        after = await self.read_data()
        ok = after.power <= max(50.0, self.rated_power * 0.05)
        return {
            "ok": ok,
            "message": (
                "0 W gesendet, Gerät meldet " + _w(after.power)
                + ("." if ok else " – der Sollwert wurde nicht übernommen. "
                   "Im Gerät den externen Steuerungstyp aktivieren.")
            ),
            "sent": 0,
            "readback": round(after.power),
        }


class BatteryDriver(BaseDriver):
    """Hausbatterie.

    **Standard ist reines Monitoring.** Bei Hybrid-Wechselrichtern (Sungrow SH,
    Fronius GEN24, Huawei, Kostal, SMA SBS …) regelt der Wechselrichter die
    Batterie selbst auf Nulleinspeisung – MinePower muss dafür nichts
    schreiben. SoC und Leistung werden nur gelesen, damit die Prioritätskette
    die Ladeleistung der Batterie korrekt einrechnen kann.

    Aktive Befehle (`set_mode`, `set_reserve_soc`) sind eine bewusst zu
    aktivierende Zusatzfunktion für Sonderfälle und werden vom Control-Loop
    nur ausgeführt, wenn der Nutzer sie explizit freigeschaltet hat.
    """

    #: True, wenn der Treiber aktive Batteriebefehle beherrscht *und* sie
    #: in der Gerätekonfiguration freigeschaltet sind.
    def control_enabled(self) -> bool:
        return bool(self.config.get("allow_active_control", False))

    @abc.abstractmethod
    async def read_data(self) -> BatteryData: ...

    async def set_mode(self, mode: BatteryMode, power_w: float | None = None) -> None:
        """Betriebsmodus setzen (Zwangsladen/-entladen/Sperren/Auto)."""
        raise NotImplementedError

    async def set_reserve_soc(self, soc: int) -> None:
        """Minimale Entladegrenze (Reserve fürs Haus) setzen."""
        raise NotImplementedError

    async def _probe(self) -> dict[str, Any]:
        d = await self.read_data()
        out: dict[str, Any] = {
            "Ladestand": f"{d.soc:.1f} %",
            "Batterieleistung": (
                f"lädt {_w(d.power)}" if d.power > 30
                else f"entlädt {_w(abs(d.power))}" if d.power < -30
                else "Ruhe (0 W)"
            ),
        }
        if d.temperature_c is not None:
            out["Temperatur"] = f"{d.temperature_c:.1f} °C"
        if d.capacity_kwh:
            out["Kapazität"] = f"{d.capacity_kwh:.1f} kWh"
        out["Betriebsart"] = d.mode.value
        for key, value in (d.extra or {}).items():
            out[key] = value
        return out

    def _warnings(self, values: dict[str, Any]) -> list[str]:
        out = super()._warnings(values)
        if not self.control_enabled():
            out.append(
                "Nur Monitoring – dein Wechselrichter regelt die Batterie selbst. "
                "MinePower liest SoC und Leistung ausschließlich mit, um sie in der "
                "Überschussverteilung zu berücksichtigen. Für die Nulleinspeisung ist "
                "keine aktive Batteriesteuerung nötig."
            )
        return out


#: Anzeigetexte für den Verbindungstest
WALLBOX_STATE_LABELS = {
    WallboxState.IDLE: "kein Fahrzeug verbunden",
    WallboxState.CONNECTED: "Fahrzeug verbunden, lädt nicht",
    WallboxState.CHARGING: "lädt",
    WallboxState.COMPLETE: "Ladung abgeschlossen",
    WallboxState.ERROR: "Fehler / nicht verfügbar",
}


def format_wallbox_values(d: WallboxData) -> dict[str, Any]:
    """WallboxData → beschriftete Live-Werte für den Verbindungstest."""
    out: dict[str, Any] = {
        "Status": WALLBOX_STATE_LABELS.get(d.state, d.state.value),
        "Ladeleistung": _w(d.power),
    }
    if d.current_set is not None:
        out["Sollstrom"] = f"{d.current_set:.1f} A"
    if d.phases_active:
        out["Phasen"] = f"{d.phases_active}-phasig"
    if d.energy_session_kwh is not None:
        out["Geladen (Sitzung)"] = f"{d.energy_session_kwh:.2f} kWh"
    if d.soc is not None:
        out["Fahrzeug-SoC"] = f"{d.soc:.0f} %"
    if d.rfid_tag:
        out["RFID"] = d.rfid_tag
    for key, value in (d.extra or {}).items():
        out[key] = value
    return out


def _w(watts: float) -> str:
    return f"{watts / 1000:.2f} kW" if abs(watts) >= 1000 else f"{watts:.0f} W"


def _grid(watts: float) -> str:
    if watts > 30:
        return f"Bezug {_w(watts)}"
    if watts < -30:
        return f"Einspeisung {_w(abs(watts))}"
    return "ausgeglichen (0 W)"


CATEGORY_INTERFACES: dict[DeviceCategory, type[BaseDriver]] = {
    DeviceCategory.INVERTER: InverterDriver,
    DeviceCategory.METER: MeterDriver,
    DeviceCategory.WALLBOX: WallboxDriver,
    DeviceCategory.WATER_HEATER: WaterHeaterDriver,
    DeviceCategory.BATTERY: BatteryDriver,
}
