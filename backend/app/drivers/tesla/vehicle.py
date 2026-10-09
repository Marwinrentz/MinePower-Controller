"""Tesla-Fahrzeug als steuerbarer Ladepunkt (WallboxDriver-Interface).

Das Auto selbst wird moduliert (Ladestrom in A, Start/Stopp, Ladelimit) –
funktioniert damit auch an einer 'dummen" Steckdose oder Wallbox.
SoC über BLE ist Best-Effort; die Regelung hängt nicht davon ab.
"""
from __future__ import annotations

import asyncio
import math
import time

from ..base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
    WallboxData,
    WallboxDriver,
    WallboxState,
)
from ..registry import register
from ..validation import DriverError, checked, sanitized
from .clients import TeslaCommandRejected, make_client

CHARGING_STATES = {
    "Charging": WallboxState.CHARGING,
    "Starting": WallboxState.CHARGING,
    "Stopped": WallboxState.CONNECTED,
    "Complete": WallboxState.COMPLETE,
    "Disconnected": WallboxState.IDLE,
    "NoPower": WallboxState.CONNECTED,
}

# BLE-Proxy nicht fluten: Fahrzeugdaten höchstens alle X Sekunden frisch holen
MIN_POLL_INTERVAL_S = 20.0

#: Nennspannung, wenn das Fahrzeug keine eigene meldet.
NOMINAL_VOLTAGE = 230.0

#: So lange gilt ein Fahrzeug ohne neue Antwort noch als erreichbar – mit
#: seinem letzten Messwert. Ein einzelner BLE-Aussetzer ist Normalbetrieb.
REACHABLE_TRUST_S = 120.0
#: Danach: 'nicht erreichbar", aber mit letztem bekannten Zustand (angesteckt
#: oder nicht). Nicht mehr 'weg" – ein schlafendes, angestecktes Auto ist
#: kein abgestecktes (Diagnose 7 Tage: 18× länger als 30 min ohne Antwort).
STATE_TRUST_S = REACHABLE_TRUST_S


@register
class TeslaVehicle(WallboxDriver):
    meta = DriverMeta(
        id="tesla_vehicle",
        name="Tesla Fahrzeug (BLE-Proxy oder Fleet-API)",
        category=DeviceCategory.WALLBOX,
        description="Steuert das Tesla-Fahrzeug direkt als Ladepunkt: Ladestrom modulieren, "
                    "Start/Stopp, Ladelimit. Methode A: externer TeslaBleHttpProxy (cloud-frei, "
                    "läuft z. B. auf einem Raspberry Pi – NICHT Teil dieser App). "
                    "Methode B: Tesla Fleet-API (Cloud, liefert zuverlässig SoC).",
        capabilities={"soc", "charge_limit", "write_test", "wake"},
        offline_hint="Gateway (TeslaBleHttpProxy) prüfen: Dienst, IP-Adresse, Port, Abstand zum Fahrzeug.",
        maturity=Maturity.STABLE,
        notes=(
            "Über einen BLE-Proxy im Dauerbetrieb erprobt.\n\n"
            "Erreichbarkeit: Antwortet das Auto 2 min nicht, gilt es als 'nicht erreichbar' – "
            "MinePower behält den letzten Stand (angesteckt oder nicht), sendet keine Befehle ins "
            "Leere und weckt ein angestecktes Auto bei Überschuss oder günstigem Strom (höchstens "
            "alle 30 min). Ins Protokoll kommt es erst nach 30 min.\n\n"
            "'Schläft angesteckt' = letzter Stand angesteckt; 'unterwegs' = letzter Stand nicht "
            "angesteckt oder außer Bluetooth-Reichweite; 'BLE-Proxy offline' = der Proxy selbst "
            "antwortet nicht (Raspberry Pi, IP/Port prüfen).\n\n"
            "Phasen: MinePower erkennt aus Leistung und Strom, ob das Auto 1- oder 3-phasig lädt. "
            "Mehr Phasen werden sofort übernommen, weniger erst nach einer Minute Bestätigung.\n\n"
            "Selbststart beim Anstecken oder durch 'Geplantes Laden' in der Fahrzeug-App wird "
            "sofort gestoppt, wenn kein Überschuss da ist."
        ),
        fields=[
            ConfigField(key="backend", label="Anbindung", type=FieldType.SELECT, default="ble_proxy",
                        options=[{"value": "ble_proxy", "label": "BLE-Proxy (lokal, empfohlen)"},
                                 {"value": "fleet", "label": "Fleet-API (Cloud)"}],
                        help="BLE-Proxy: cloud-frei über einen bereits laufenden TeslaBleHttpProxy. "
                             "Fleet-API: ohne lokale Hardware, benötigt OAuth-Token."),
            ConfigField(key="proxy_url", label="Proxy-Adresse", required=False,
                        placeholder="http://192.0.2.99",
                        help="Adresse des extern laufenden TeslaBleHttpProxy (nur Methode A), "
                             "ohne Port. Die Schlüssel-Kopplung erfolgt im Proxy selbst."),
            ConfigField(key="proxy_port", label="Proxy-Port", type=FieldType.NUMBER, default=8080, required=False,
                        help="Port des TeslaBleHttpProxy. Standard 8080 – je nach Installation "
                             "abweichend (z. B. 80). Wird ignoriert, wenn die Proxy-Adresse "
                             "bereits einen Port enthält."),
            ConfigField(key="vin", label="Fahrzeug-VIN", required=False, placeholder="5YJ3E7EB...",
                        help="17-stellige Fahrzeug-Identnummer (nur Methode A)."),
            ConfigField(key="proxy_auth", label="Proxy-Auth-Header", type=FieldType.PASSWORD, required=False,
                        help="Optionaler Authorization-Header, falls der Proxy abgesichert ist."),
            ConfigField(key="access_token", label="Fleet-API Access-Token", type=FieldType.PASSWORD,
                        required=False, help="OAuth-Token für die Tesla Fleet-API (nur Methode B)."),
            ConfigField(key="vehicle_id", label="Fahrzeug-ID (Fleet)", required=False,
                        help="Numerische Fahrzeug-ID aus der Fleet-API (nur Methode B)."),
            ConfigField(key="region", label="Fleet-Region", type=FieldType.SELECT, default="eu", required=False,
                        options=[{"value": "eu", "label": "Europa"}, {"value": "na", "label": "Nordamerika"}],
                        help="Region des Tesla-Kontos (nur Methode B)."),
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="Obergrenze für den Ladestrom (abhängig von Kabel/Anschluss)."),
            ConfigField(key="capacity_kwh", label="Akku-Kapazität (kWh)", type=FieldType.NUMBER, default=60,
                        required=False, help="Für die Zielladung mit Deadline (Modus 'Zielladung')."),
        ],
    )

    min_current = 5.0  # Tesla erlaubt ab 5 A
    #: Ein schlafendes Fahrzeug antwortet über BLE erst nach etlichen Sekunden.
    #: Mit dem Standard-Timeout von 6 s flattert das Gerät im Minutentakt
    #: zwischen online und offline – und fällt dabei aus der Regelung.
    read_timeout_s = 25.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.client = make_client(config)
        self.max_current = float(config.get("max_current") or 16)
        self._cache: WallboxData | None = None
        self._cache_time = 0.0
        #: Letzter über eine echte Antwort bestätigter Zustand. Dient als
        #: Rückfallwert bei Timeout (siehe read_data) – ohne das würde ein
        #: Fahrzeug, das gar nicht angesteckt ist, beim ersten Timeout auf
        #: 'verbunden' springen und dauerhaft Budget in der Prioritätskette
        #: blockieren, das nie abgerufen wird.
        self._last_known_state = WallboxState.IDLE
        #: Wann der Zustand zuletzt durch eine echte Antwort bestätigt wurde.
        self._state_confirmed_at = 0.0
        # Fleet-API konservativer pollen (Rate-Limits)
        self._poll_interval = 60.0 if config.get("backend") == "fleet" else MIN_POLL_INTERVAL_S

    async def connect(self) -> None:
        pass  # zustandslos (HTTP); Erreichbarkeit prüft der erste Read

    async def disconnect(self) -> None:
        await self.client.close()

    async def read_data(self) -> WallboxData:
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_time) < self._poll_interval:
            return self._cache
        try:
            data = await self.client.vehicle_data("charge_state")
        except TimeoutError:
            silent = now - self._state_confirmed_at
            reachable = self._state_confirmed_at > 0 and silent <= REACHABLE_TRUST_S
            last = self._cache if self._cache is not None and self._cache.vehicle_reachable else None
            state = self._last_known_state
            hint = ("antwortet gerade nicht" if reachable
                    else "schläft angesteckt oder unterwegs – letzter Stand: "
                         + ("angesteckt" if self.likely_plugged_in else "nicht angesteckt"))
            self._cache = WallboxData(
                state=state,
                # Kurz nach der letzten Antwort gilt der letzte Messwert weiter
                # (lädt vermutlich noch); danach ist die Leistung unbekannt → 0.
                power=(last.power if (reachable and last is not None) else 0.0),
                current_set=last.current_set if (reachable and last is not None) else None,
                phases_active=last.phases_active if last is not None else None,
                soc=last.soc if last is not None else None,
                charge_limit_soc=last.charge_limit_soc if last is not None else None,
                vehicle_reachable=reachable,
                extra={"Fahrzeug": hint, "capacity_kwh": self.config.get("capacity_kwh"),
                       "silent_s": round(silent) if self._state_confirmed_at > 0 else None},
            )
            self._cache_time = now
            return self._cache
        charge = data.get("charge_state") or data  # Proxy liefert teils flach
        if not isinstance(charge, dict) or "charging_state" not in charge:
            raise DriverError(
                "Die Antwort enthält keinen Ladezustand ('charge_state'). Beim BLE-Proxy heißt "
                "das meist: Das Fahrzeug ist außer Reichweite oder der Schlüssel ist nicht "
                "gekoppelt; bei der Fleet-API fehlt dem Token der Scope 'vehicle_device_data'."
            )
        state = CHARGING_STATES.get(str(charge.get("charging_state")), WallboxState.CONNECTED)
        self._last_known_state = state
        self._state_confirmed_at = now
        amps = _f(charge.get("charger_actual_current"))
        volts_raw = _f(charge.get("charger_voltage"))
        volts = volts_raw if 180.0 <= volts_raw <= 260.0 else None
        reported_kw = _f(charge.get("charger_power"))
        phases = _phases(charge, amps, volts, reported_kw)
        power = _charge_power(amps, volts, phases, reported_kw)
        self._cache = WallboxData(
            state=state,
            power=checked("wallbox_power", power, source="Tesla-Ladeleistung"),
            current_set=sanitized("wallbox_current", _f(charge.get("charge_amps")), zero_is_none=True),
            phases_active=phases,
            vehicle_reachable=True,
            # Gemessene Spannung durchreichen – aber nur, wenn sie wirklich
            # gemeldet wurde. Ein stillschweigend eingesetzter Nennwert sähe
            # für den Regler wie eine Messung aus und würde die
            # Einschwingprüfung in `observe` umgehen.
            voltage=volts,
            energy_session_kwh=sanitized("energy_kwh", _f(charge.get("charge_energy_added")), zero_is_none=True),
            soc=sanitized("vehicle_soc", charge.get("battery_level")),
            charge_limit_soc=sanitized("vehicle_soc", charge.get("charge_limit_soc")),
            extra={"Ladelimit": _limit_text(charge.get("charge_limit_soc")),
                   "capacity_kwh": self.config.get("capacity_kwh")},
        )
        self._cache_time = now
        return self._cache

    def _invalidate(self) -> None:
        self._cache_time = 0.0

    @property
    def presence_known(self) -> bool:
        """Hat das Fahrzeug seit dem Start überhaupt einmal geantwortet?"""
        return self._state_confirmed_at > 0

    @property
    def likely_plugged_in(self) -> bool:
        """War das Fahrzeug bei der letzten echten Antwort angesteckt und noch
        nicht fertig geladen?

        Grundlage fürs Wecken bei Überschuss (ControlLoop._maybe_wake): Ein
        Auto, das angesteckt eingeschlafen ist, steckt mit großer
        Wahrscheinlichkeit noch – es steckt sich nicht selbst ab. Ist es
        dagegen weggefahren, schlägt das Wecken über BLE fehl (außer
        Reichweite), und genau das ist dann die Antwort."""
        return self._last_known_state in (WallboxState.CONNECTED, WallboxState.CHARGING)

    async def set_current(self, amps: float) -> None:
        """Tesla nimmt den Sollstrom nur ganzzahlig und ≥ 5 A an. Werte
        darunter interpretiert das Fahrzeug je nach Software als 5 A oder
        ignoriert sie – deshalb hier sauber klemmen.

        **Abgerundet, nicht gerundet.** Der Regler hat den Strom aus dem
        verfügbaren Überschuss berechnet; kaufmännisch aufzurunden würde ein
        Ampere mehr anfordern, als zugeteilt war – bei einphasig 230 W, die
        aus Speicher oder Netz kommen müssten."""
        wanted = max(self.min_current, min(amps, self.max_current))
        target = int(math.floor(wanted + 1e-9))
        await self.client.command("set_charging_amps", {"charging_amps": target})
        self._invalidate()

    async def start_charging(self) -> None:
        try:
            await self.client.command("charge_start")
        except TeslaCommandRejected as exc:
            # 'lädt bereits" / 'Ladeziel erreicht" sind keine Fehlerzustände
            if not any(k in exc.reason.lower() for k in ("is_charging", "complete")):
                raise
        self._invalidate()

    async def stop_charging(self) -> None:
        try:
            await self.client.command("charge_stop")
        except TeslaCommandRejected as exc:
            if "not_charging" not in exc.reason.lower():
                raise
        self._invalidate()

    async def set_charge_limit(self, soc: int) -> None:
        await self.client.command("set_charge_limit", {"percent": int(soc)})
        self._invalidate()

    async def wake_up(self) -> None:
        await self.client.wake_up()
        self._invalidate()

    async def test_command(self) -> dict | None:
        """Ladestrom auf den aktuellen Wert setzen und zurücklesen.

        Das Fahrzeug übernimmt den Wert nicht sofort; deshalb wird der Cache
        verworfen und nach kurzer Wartezeit erneut gelesen."""
        before = await self.read_data()
        if before.state == WallboxState.IDLE:
            return {"ok": True, "message": "Kein Ladekabel verbunden – Schreibtest übersprungen.",
                    "sent": None, "readback": None}
        target = int(before.current_set or self.min_current)
        target = int(max(self.min_current, min(target, self.max_current)))
        await self.client.command("set_charging_amps", {"charging_amps": target})
        self._invalidate()
        await asyncio.sleep(3.0)  # Fahrzeug/Proxy brauchen einen Moment
        after = await self.read_data()
        got = after.current_set
        ok = got is None or abs(float(got) - target) <= 1.0
        return {
            "ok": ok,
            "message": f"{target} A gesendet, Fahrzeug meldet "
                       + (f"{float(got):.0f} A zurück." if got is not None else "keinen Wert zurück.")
                       + ("" if ok else " Der Wert wurde nicht übernommen."),
            "sent": target,
            "readback": None if got is None else round(float(got)),
        }


def _phases(charge: dict, amps: float, volts: float | None, reported_kw: float) -> int | None:
    """Wie viele Phasen lädt das Fahrzeug gerade?

    Tesla meldet `charger_phases` in Europa je nach Fahrzeug und Software als
    **2**, wenn dreiphasig geladen wird. Einen zweiphasigen Ladebetrieb gibt es
    nicht – der Wert heißt 'mehrphasig". Ungeprüft übernommen fehlt in jeder
    Leistungsrechnung ein Drittel.

    Fehlt die Angabe ganz, entscheidet der grobe kW-Wert: Er taugt nicht, um
    1,38 von 1,15 kW zu unterscheiden, aber sehr wohl 1,4 von 4,1 kW. Jedes
    Signal für das, was es kann.
    """
    raw = int(_f(charge.get("charger_phases")))
    reported = 3 if raw in (2, 3) else 1 if raw == 1 else None
    # Leistung schlägt Meldung: Ab 5 A ist 1- von 3-phasig über den groben
    # kW-Wert sicher unterscheidbar (5 A: 1,15 vs. 3,45 kW). Am 07.10. meldete
    # das Fahrzeug beim Ladebeginn '1 Phase", zog aber dreiphasig 4,2 kW.
    if amps >= 5 and reported_kw >= 1:
        single = amps * (volts or NOMINAL_VOLTAGE)
        measured = reported_kw * 1000.0
        return 3 if abs(measured - single * 3) < abs(measured - single) else 1
    if reported is not None:
        return reported
    if amps > 0 and reported_kw > 0:
        single = amps * (volts or NOMINAL_VOLTAGE)
        measured = reported_kw * 1000.0
        return 3 if abs(measured - single * 3) < abs(measured - single) else 1
    return None


def _charge_power(amps: float, volts: float | None, phases: int | None, reported_kw: float) -> float:
    """Ladeleistung in W – fein vor grob.

    `charger_power` ist bei Tesla ein **ganzzahliger kW-Wert**. Bei 6 A
    einphasig fließen real 1380 W, gemeldet wird '1" – also 1000 W. Bei 16 A
    sind es real 3680 W, gemeldet '4" – also 4000 W.

    Dieser Wert ging bisher als Messung in die Regelung ein, und der Fehler
    von bis zu ±500 W wirkt in beide teuren Richtungen: Zu niedrig gemeldet
    schrumpft das Budget, und Überschuss geht ins Netz. Zu hoch gemeldet
    wächst es, das Fahrzeug fordert mehr an, als die Sonne hergibt – und die
    Differenz kommt aus Speicher oder Netz.

    `charger_actual_current` und `charger_voltage` sind dagegen fein
    aufgelöst. Ihr Produkt ist die eigentliche Messung; der kW-Wert bleibt
    Rückfall für den Fall, dass das Fahrzeug keinen Strom meldet.
    """
    if amps > 0:
        return amps * (volts or NOMINAL_VOLTAGE) * (phases or 1)
    return reported_kw * 1000.0


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _limit_text(value) -> str:
    return f"{int(_f(value))} %" if value is not None else "unbekannt"
