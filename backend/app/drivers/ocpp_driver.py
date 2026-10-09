"""Generischer OCPP-1.6J-Treiber (EXPERIMENTELL).

MinePower agiert als schlanke OCPP-Zentrale: ein WebSocket-Server nimmt
Verbindungen der Wallbox entgegen (ws://<app-host>:<port>/<charge-point-id>).
In der Wallbox als 'Central System / Backend-URL" eintragen.

Unterstützt: BootNotification, Heartbeat, StatusNotification, MeterValues,
Start-/StopTransaction, Authorize (alles akzeptierend) sowie ausgehend
RemoteStart/RemoteStop und SetChargingProfile (Strom-Limit in A).
Hinweis Docker: Port im docker-compose freigeben (z. B. "8887:8887").
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone


from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    Maturity,
    WallboxData,
    WallboxDriver,
    WallboxState,
)
from .registry import register
from .validation import ConnectionProblem, DeviceRejected, DriverError, checked, sanitized

log = logging.getLogger(__name__)

CALL = 2
CALLRESULT = 3
CALLERROR = 4

STATUS_MAP = {
    "Available": WallboxState.IDLE,
    "Preparing": WallboxState.CONNECTED,
    "Charging": WallboxState.CHARGING,
    "SuspendedEVSE": WallboxState.CONNECTED,
    "SuspendedEV": WallboxState.CONNECTED,
    "Finishing": WallboxState.COMPLETE,
    "Reserved": WallboxState.CONNECTED,
    "Unavailable": WallboxState.ERROR,
    "Faulted": WallboxState.ERROR,
}


class ChargePointSession:
    """Zustand einer verbundenen OCPP-Wallbox."""

    def __init__(self, cp_id: str) -> None:
        self.cp_id = cp_id
        self.ws = None
        self.status = "Available"
        self.power = 0.0
        self.energy_kwh: float | None = None
        self.session_start_kwh: float | None = None
        self.current_offered: float | None = None
        self.commanded_current: float | None = None
        self.transaction_id: int | None = None
        self.id_tag: str | None = None
        self.error_code: str | None = None
        self.last_seen = 0.0
        self._pending: dict[str, asyncio.Future] = {}
        self._tx_counter = int(time.time()) % 100000

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def call(self, action: str, payload: dict) -> dict:
        if self.ws is None:
            raise ConnectionProblem(
                f"OCPP: Wallbox '{self.cp_id}' ist nicht verbunden. Backend-URL in der Wallbox "
                f"prüfen (ws://<server-ip>:<port>/{self.cp_id}) und ob der Port erreichbar ist."
            )
        msg_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self.ws.send(json.dumps([CALL, msg_id, action, payload]))
        except Exception as exc:  # noqa: BLE001 – Socket kann zwischenzeitlich tot sein
            self._pending.pop(msg_id, None)
            self.ws = None
            raise ConnectionProblem(f"OCPP: Senden an '{self.cp_id}' fehlgeschlagen – {exc}") from exc
        try:
            return await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError as exc:
            raise ConnectionProblem(
                f"OCPP: Wallbox '{self.cp_id}' antwortet nicht auf {action} (15 s Zeitüberschreitung)."
            ) from exc
        finally:
            self._pending.pop(msg_id, None)

    def check_alive(self, max_silence_s: float) -> None:
        """Eine OCPP-Verbindung kann 'offen' aussehen, obwohl die Gegenstelle
        längst weg ist (typisch bei WLAN-Wallboxen und NAT-Timeouts). Bleiben
        Heartbeats aus, gilt sie als getrennt – sonst regelt MinePower gegen
        eine Box, die gar nicht mehr zuhört."""
        if self.ws is None or self.last_seen <= 0:
            return
        silence = time.monotonic() - self.last_seen
        if silence > max_silence_s:
            raise ConnectionProblem(
                f"OCPP: Von '{self.cp_id}' kam seit {silence:.0f} s keine Nachricht mehr "
                f"(Heartbeat ausgeblieben). Die Verbindung gilt als tot."
            )

    def resolve(self, msg_id: str, payload: dict, error: str | None = None) -> None:
        future = self._pending.get(msg_id)
        if future and not future.done():
            if error:
                future.set_exception(IOError(f"OCPP-Fehler: {error}"))
            else:
                future.set_result(payload)


class OcppCentralServer:
    """Singleton-WebSocket-Server für alle OCPP-Wallboxen."""

    _instance: "OcppCentralServer | None" = None

    def __init__(self) -> None:
        self.sessions: dict[str, ChargePointSession] = {}
        self._server = None
        self._port: int | None = None

    @classmethod
    def instance(cls) -> "OcppCentralServer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def session(self, cp_id: str) -> ChargePointSession:
        if cp_id not in self.sessions:
            self.sessions[cp_id] = ChargePointSession(cp_id)
        return self.sessions[cp_id]

    async def ensure_started(self, port: int) -> None:
        if self._server is not None:
            if self._port != port:
                log.warning("OCPP-Server läuft bereits auf Port %s (angefragt: %s)", self._port, port)
            return
        import websockets

        self._server = await websockets.serve(
            self._handle, "0.0.0.0", port, subprotocols=["ocpp1.6"]
        )
        self._port = port
        log.info("OCPP-Zentrale lauscht auf Port %d", port)

    async def _handle(self, ws) -> None:
        cp_id = (getattr(ws, "path", "") or getattr(getattr(ws, "request", None), "path", "/")).strip("/").split("/")[-1]
        if not cp_id:
            await ws.close()
            return
        session = self.session(cp_id)
        session.ws = ws
        log.info("OCPP: %s verbunden", cp_id)
        try:
            async for raw in ws:
                try:
                    await self._on_message(session, json.loads(raw))
                except Exception as exc:  # noqa: BLE001
                    log.warning("OCPP: fehlerhafte Nachricht von %s: %s", cp_id, exc)
        finally:
            session.ws = None
            log.info("OCPP: %s getrennt", cp_id)

    async def _on_message(self, session: ChargePointSession, msg: list) -> None:
        session.last_seen = time.monotonic()
        kind = msg[0]
        if kind == CALLRESULT:
            session.resolve(msg[1], msg[2])
            return
        if kind == CALLERROR:
            session.resolve(msg[1], {}, error=f"{msg[2]}: {msg[3]}")
            return
        if kind != CALL:
            return
        _, msg_id, action, payload = msg[0], msg[1], msg[2], (msg[3] if len(msg) > 3 else {})
        result = await self._dispatch(session, action, payload)
        await session.ws.send(json.dumps([CALLRESULT, msg_id, result]))

    async def _dispatch(self, s: ChargePointSession, action: str, p: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        if action == "BootNotification":
            return {"currentTime": now, "interval": 300, "status": "Accepted"}
        if action == "Heartbeat":
            return {"currentTime": now}
        if action == "StatusNotification":
            # Meldungen zu Connector 0 betreffen die Box als Ganzes, nicht den
            # Ladepunkt – sie dürfen den Ladepunkt-Status nicht überschreiben.
            if int(p.get("connectorId") or 1) != 0:
                s.status = p.get("status", s.status)
            s.error_code = p.get("errorCode") or None
            return {}
        if action == "Authorize":
            s.id_tag = p.get("idTag")
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "StartTransaction":
            s._tx_counter += 1
            s.transaction_id = s._tx_counter
            s.id_tag = p.get("idTag") or s.id_tag
            s.session_start_kwh = (p.get("meterStart") or 0) / 1000.0
            return {"transactionId": s.transaction_id, "idTagInfo": {"status": "Accepted"}}
        if action == "StopTransaction":
            s.transaction_id = None
            s.session_start_kwh = None
            s.power = 0.0
            return {"idTagInfo": {"status": "Accepted"}}
        if action == "MeterValues":
            self._parse_meter_values(s, p)
            return {}
        if action == "DataTransfer":
            return {"status": "Accepted"}
        return {}

    def _parse_meter_values(self, s: ChargePointSession, p: dict) -> None:
        for mv in p.get("meterValue", []):
            for sample in mv.get("sampledValue", []):
                measurand = sample.get("measurand", "Energy.Active.Import.Register")
                try:
                    value = float(sample.get("value", 0))
                except (TypeError, ValueError):
                    continue
                unit = sample.get("unit", "")
                if measurand == "Power.Active.Import":
                    s.power = value * 1000.0 if unit == "kW" else value
                elif measurand == "Energy.Active.Import.Register":
                    s.energy_kwh = value / 1000.0 if unit in ("Wh", "") else value
                elif measurand == "Current.Offered":
                    s.current_offered = value


@register
class OcppWallbox(WallboxDriver):
    meta = DriverMeta(
        id="ocpp_wallbox",
        name="Generische Wallbox (OCPP 1.6J)",
        category=DeviceCategory.WALLBOX,
        description="Breite Kompatibilität: Die Wallbox verbindet sich per OCPP 1.6J zu dieser App "
                    "(Backend-URL in der Wallbox: ws://<server-ip>:<port>/<charge-point-id>). "
                    "Experimentell – Ladestromvorgabe via SmartCharging-Profil.",
        capabilities={"rfid", "write_test"},
        maturity=Maturity.EXPERIMENTAL,
        notes="Deckt die für Überschussladen nötige Teilmenge von OCPP 1.6J ab. Der Ladestrom "
              "wird über ein SmartCharging-Profil vorgegeben – Boxen ohne SmartCharging lassen "
              "sich nur ein-/ausschalten. Der Schreibtest im Verbindungstest zeigt das sofort.",
        fields=[
            ConfigField(key="charge_point_id", label="Charge-Point-ID", placeholder="wallbox1",
                        help="Kennung, mit der sich die Wallbox verbindet (letzter Teil der Backend-URL). "
                             "Muss exakt mit der Einstellung in der Wallbox übereinstimmen."),
            ConfigField(key="port", label="Server-Port", type=FieldType.NUMBER, default=8887,
                        help="Port, auf dem MinePower auf die Wallbox wartet. Im Docker-Setup freigeben "
                             "(ports: [\"8887:8887\"]) – sonst erreicht die Wallbox den Server nie."),
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="Hardware-Limit der Wallbox."),
            ConfigField(key="phases", label="Phasenanzahl", type=FieldType.NUMBER, default=3,
                        help="Fest verdrahtete Phasenzahl (für die Leistungsrechnung)."),
            ConfigField(key="connector_id", label="Connector-ID", type=FieldType.NUMBER, default=1,
                        required=False,
                        help="Ladepunkt-Nummer innerhalb der Wallbox. Bei Einzelladepunkten 1; "
                             "bei Doppelladern 1 oder 2."),
            ConfigField(key="id_tag", label="ID-Tag für Fernstart", default="minepower", required=False,
                        help="Kennung, mit der MinePower Ladevorgänge startet. Manche Boxen "
                             "akzeptieren nur in ihrer Whitelist hinterlegte Tags."),
            ConfigField(key="max_silence_s", label="Heartbeat-Timeout (s)", type=FieldType.NUMBER,
                        default=900, required=False,
                        help="Nach dieser Stille ohne Heartbeat gilt die Wallbox als getrennt. "
                             "Verhindert, dass gegen eine tote Verbindung geregelt wird."),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.cp_id = str(config.get("charge_point_id") or "wallbox1").strip()
        self.port = int(config.get("port") or 8887)
        self.max_current = float(config.get("max_current") or 16)
        self.phases = int(config.get("phases") or 3)
        self.connector_id = int(config.get("connector_id") or 1)
        self.id_tag = str(config.get("id_tag") or "minepower")
        # Heartbeat-Intervall wird der Box im BootNotification vorgegeben (300 s);
        # nach dem Dreifachen gilt sie als tot.
        self.max_silence_s = float(config.get("max_silence_s") or 900)

    async def connect(self) -> None:
        server = OcppCentralServer.instance()
        await server.ensure_started(self.port)

    async def read_data(self) -> WallboxData:
        s = OcppCentralServer.instance().session(self.cp_id)
        if not s.connected:
            raise ConnectionProblem(
                f"OCPP: Wallbox '{self.cp_id}' hat sich noch nicht verbunden. In der Wallbox als "
                f"Backend-URL ws://<server-ip>:{self.port}/{self.cp_id} eintragen und prüfen, "
                f"ob der Port im Docker-Compose freigegeben ist."
            )
        s.check_alive(self.max_silence_s)
        if s.error_code and s.error_code != "NoError":
            raise DriverError(f"OCPP: Wallbox meldet Fehler '{s.error_code}' (Status {s.status}).")

        state = STATUS_MAP.get(s.status, WallboxState.IDLE)
        if s.transaction_id is not None and state == WallboxState.CONNECTED:
            state = WallboxState.CHARGING if s.power > 100 else WallboxState.CONNECTED
        session_kwh = None
        if s.energy_kwh is not None and s.session_start_kwh is not None:
            session_kwh = max(0.0, s.energy_kwh - s.session_start_kwh)
        return WallboxData(
            state=state,
            power=checked("wallbox_power", s.power, source="OCPP MeterValues"),
            current_set=sanitized("wallbox_current", s.current_offered),
            phases_active=self.phases,
            energy_session_kwh=sanitized("energy_kwh", session_kwh),
            rfid_tag=s.id_tag,
            extra={"OCPP-Status": s.status, "Transaktion": s.transaction_id or "keine"},
        )

    async def set_current(self, amps: float) -> None:
        s = OcppCentralServer.instance().session(self.cp_id)
        limit = round(max(0.0, min(amps, self.max_current)), 1)
        profile = {
            "connectorId": self.connector_id,
            "csChargingProfiles": {
                "chargingProfileId": 1,
                "stackLevel": 0,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "chargingSchedule": {
                    "chargingRateUnit": "A",
                    "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit}],
                },
            },
        }
        result = await s.call("SetChargingProfile", profile)
        _require_accepted(
            result, f"Ladestrom {limit} A",
            "Die Wallbox unterstützt SmartCharging entweder nicht oder hat es deaktiviert. "
            "Ohne SmartCharging lässt sich der Ladestrom über OCPP nicht stufenlos vorgeben.",
        )
        s.commanded_current = limit

    async def start_charging(self) -> None:
        s = OcppCentralServer.instance().session(self.cp_id)
        if s.transaction_id is not None:
            return
        result = await s.call(
            "RemoteStartTransaction", {"connectorId": self.connector_id, "idTag": self.id_tag}
        )
        _require_accepted(
            result, "Ladefreigabe",
            "Manche Wallboxen lehnen Fernstarts ab, solange kein Fahrzeug verbunden ist oder "
            "eine lokale Autorisierung (RFID) erzwungen wird.",
        )

    async def stop_charging(self) -> None:
        s = OcppCentralServer.instance().session(self.cp_id)
        if s.transaction_id is None:
            return
        result = await s.call("RemoteStopTransaction", {"transactionId": s.transaction_id})
        _require_accepted(result, "Ladestopp", "")

    async def test_command(self) -> dict | None:
        """SmartCharging-Profil mit dem Hardware-Maximum setzen: ändert nichts
        am aktuellen Ladeverhalten, zeigt aber, ob die Box Profile annimmt."""
        s = OcppCentralServer.instance().session(self.cp_id)
        if not s.connected:
            return {"ok": False, "message": "Wallbox ist nicht verbunden – Schreibtest nicht möglich."}
        try:
            await self.set_current(self.max_current)
        except DriverError as exc:
            return {"ok": False, "message": str(exc), "sent": self.max_current, "readback": None}
        return {
            "ok": True,
            "message": f"SmartCharging-Profil über {self.max_current:.0f} A wurde von der "
                       f"Wallbox mit 'Accepted' bestätigt.",
            "sent": self.max_current,
            "readback": s.current_offered,
        }


def _require_accepted(result: dict, what: str, hint: str) -> None:
    """OCPP quittiert jeden Call – der eigentliche Erfolg steckt im `status`."""
    status = str((result or {}).get("status", "Accepted"))
    if status == "Accepted":
        return
    raise DeviceRejected(f"OCPP: {what} wurde von der Wallbox abgelehnt (Status '{status}'). {hint}".strip())
