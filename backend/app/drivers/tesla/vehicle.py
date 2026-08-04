"""Tesla-Fahrzeug als steuerbarer Ladepunkt (WallboxDriver-Interface).

Das Auto selbst wird moduliert (Ladestrom in A, Start/Stopp, Ladelimit) –
funktioniert damit auch an einer „dummen" Steckdose oder Wallbox.
SoC über BLE ist Best-Effort; die Regelung hängt nicht davon ab.
"""
from __future__ import annotations

import asyncio
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
        capabilities={"soc", "charge_limit", "write_test"},
        maturity=Maturity.STABLE,
        notes="Über einen BLE-Proxy im Dauerbetrieb erprobt.",
        fields=[
            ConfigField(key="backend", label="Anbindung", type=FieldType.SELECT, default="ble_proxy",
                        options=[{"value": "ble_proxy", "label": "BLE-Proxy (lokal, empfohlen)"},
                                 {"value": "fleet", "label": "Fleet-API (Cloud)"}],
                        help="BLE-Proxy: cloud-frei über einen bereits laufenden TeslaBleHttpProxy. "
                             "Fleet-API: ohne lokale Hardware, benötigt OAuth-Token."),
            ConfigField(key="proxy_url", label="Proxy-Adresse", required=False,
                        placeholder="http://192.168.1.99",
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
        #: „verbunden“ springen und dauerhaft Budget in der Prioritätskette
        #: blockieren, das nie abgerufen wird.
        self._last_known_state = WallboxState.IDLE
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
            # Nicht erreichbar heißt entweder „schläft, aber noch angesteckt“
            # oder schlicht „gar nicht verbunden“ – per BLE nicht unterscheidbar,
            # ohne das Fahrzeug regelmäßig zu wecken (Vampir-Drain). Wir nehmen
            # daher den zuletzt bestätigten Zustand an, statt pauschal
            # „verbunden“ zu unterstellen: Ein Fahrzeug, das nie angesteckt war,
            # bleibt so korrekt „idle“ und blockiert kein Budget in der
            # Prioritätskette, das nie abgerufen würde.
            self._cache = WallboxData(state=self._last_known_state, power=0.0,
                                      extra={"Fahrzeug": "nicht erreichbar (schläft oder nicht verbunden)",
                                             "capacity_kwh": self.config.get("capacity_kwh")})
            self._cache_time = now
            return self._cache
        charge = data.get("charge_state") or data  # Proxy liefert teils flach
        if not isinstance(charge, dict) or "charging_state" not in charge:
            raise DriverError(
                "Die Antwort enthält keinen Ladezustand („charge_state“). Beim BLE-Proxy heißt "
                "das meist: Das Fahrzeug ist außer Reichweite oder der Schlüssel ist nicht "
                "gekoppelt; bei der Fleet-API fehlt dem Token der Scope „vehicle_device_data“."
            )
        state = CHARGING_STATES.get(str(charge.get("charging_state")), WallboxState.CONNECTED)
        self._last_known_state = state
        amps = _f(charge.get("charger_actual_current"))
        volts = _f(charge.get("charger_voltage")) or 230.0
        phases = int(_f(charge.get("charger_phases")) or 1)
        reported_kw = _f(charge.get("charger_power"))
        power = reported_kw * 1000.0 if reported_kw > 0 else amps * volts * phases
        self._cache = WallboxData(
            state=state,
            power=checked("wallbox_power", power, source="Tesla charger_power"),
            current_set=sanitized("wallbox_current", _f(charge.get("charge_amps")), zero_is_none=True),
            phases_active=phases if phases in (1, 3) else None,
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

    async def set_current(self, amps: float) -> None:
        """Tesla nimmt den Sollstrom nur ganzzahlig und ≥ 5 A an. Werte
        darunter interpretiert das Fahrzeug je nach Software als 5 A oder
        ignoriert sie – deshalb hier sauber klemmen."""
        target = int(round(max(self.min_current, min(amps, self.max_current))))
        await self.client.command("set_charging_amps", {"charging_amps": target})
        self._invalidate()

    async def start_charging(self) -> None:
        try:
            await self.client.command("charge_start")
        except TeslaCommandRejected as exc:
            # „lädt bereits" / „Ladeziel erreicht" sind keine Fehlerzustände
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


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _limit_text(value) -> str:
    return f"{int(_f(value))} %" if value is not None else "unbekannt"
