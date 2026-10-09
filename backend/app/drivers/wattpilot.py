"""Fronius Wattpilot über die lokale WebSocket-Schnittstelle (ws://<ip>/ws).

Ablauf laut Protokoll (dokumentiert in den Open-Source-Clients
wattpilot (Go, Apache-2.0) und evcc (MIT)):

1. Box sendet ``hello`` (Seriennummer, ``secured``) und ``authRequired``
   (``token1``, ``token2``).
2. Passwort-Hash: PBKDF2-HMAC-SHA512(Passwort, Seriennummer, 100 000
   Runden, 256 Byte) → Base64 → erste 32 Zeichen.
3. Antwort ``auth`` mit zufälligem ``token3`` und
   ``hash = sha256(token3 + token2 + sha256(token1 + hash))``.
4. Danach kommen ``fullStatus``/``deltaStatus`` mit den Schlüsseln der
   go-e-API v2 (car, amp, nrg, frc, wh …).
5. Stellbefehle ``setValue``; bei ``secured`` eingepackt als
   ``securedMsg`` mit HMAC-SHA256 über die Nutzdaten.

Neuere Firmware (Wattpilot Flex) verwendet ein anderes Hashverfahren und
wird hier nicht unterstützt. Experimentell.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets as pysecrets
import time
from typing import Any

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxDriver, WallboxState
from .registry import register
from .validation import CommandNotApplied, ConnectionProblem, DeviceRejected
from .wallbox_common import MAX_CURRENT_FIELD, f, host_field, wallbox_data

log = logging.getLogger(__name__)

CAR_STATES = {1: WallboxState.IDLE, 2: WallboxState.CHARGING, 3: WallboxState.CONNECTED, 4: WallboxState.COMPLETE,
              5: WallboxState.ERROR}
STALE_S = 60.0


def hash_password(password: str, serial: str) -> str:
    raw = hashlib.pbkdf2_hmac("sha512", password.encode(), serial.encode(), 100_000, 256)
    return base64.b64encode(raw).decode()[:32]


def auth_message(hashed: str, token1: str, token2: str, token3: str | None = None) -> dict:
    token3 = token3 or pysecrets.token_hex(16)
    hash1 = hashlib.sha256((token1 + hashed).encode()).hexdigest()
    return {"type": "auth", "token3": token3,
            "hash": hashlib.sha256((token3 + token2 + hash1).encode()).hexdigest()}


def secured(hashed: str, message: dict) -> dict:
    payload = json.dumps(message, separators=(",", ":"))
    mac = hmac.new(hashed.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"type": "securedMsg", "data": payload, "requestId": f"{message['requestId']}sm", "hmac": mac}


@register
class FroniusWattpilot(WallboxDriver):
    meta = DriverMeta(
        id="fronius_wattpilot", name="Fronius Wattpilot (WebSocket)", category=DeviceCategory.WALLBOX,
        description="Fronius Wattpilot Home/Go 11 J und 22 J über die lokale WebSocket-Schnittstelle.",
        capabilities={"write_test"},
        notes="Passwort wie in der Solar.wattpilot-App. Lademodus 'Default' wählen, sonst regelt die Box selbst. "
              "Wattpilot Flex wird nicht unterstützt.",
        fields=[host_field("192.0.2.27"),
                ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD),
                MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.host = str(config.get("host", "")).strip()
        self.max_current = f(config.get("max_current"), 16.0) or 16.0
        self.status: dict[str, Any] = {}
        self.updated = 0.0
        self.serial = ""
        self.secured = False
        self.hashed: str | None = None
        self._ws = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._error: str | None = None
        self._request = 0
        self._pending: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------ Verbindung

    async def connect(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise ConnectionProblem(f"Wattpilot {self.host}: keine Anmeldung ({self._error or 'Zeitüberschreitung'})")

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._ready.clear()

    async def _run(self) -> None:
        from websockets.asyncio.client import connect

        while True:
            try:
                async with connect(f"ws://{self.host}/ws", open_timeout=10, ping_interval=20) as ws:
                    self._ws = ws
                    async for raw in ws:
                        await self.handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 – Box neu gestartet oder WLAN weg: später erneut
                self._error = str(exc) or exc.__class__.__name__
            self._ws = None
            self._ready.clear()
            await asyncio.sleep(5)

    async def _send(self, message: dict) -> None:
        if self._ws is None:
            raise ConnectionProblem(f"Wattpilot {self.host}: keine Verbindung")
        await self._ws.send(json.dumps(message, separators=(",", ":")))

    async def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            self.serial = str(msg.get("serial") or "")
            self.secured = bool(msg.get("secured"))
        elif kind == "authRequired":
            password = str(self.config.get("password") or "")
            if self.hashed is None:
                self.hashed = await asyncio.to_thread(hash_password, password, self.serial)
            await self._send(auth_message(self.hashed, str(msg.get("token1")), str(msg.get("token2"))))
        elif kind == "authSuccess":
            self._error = None
            self._ready.set()
        elif kind == "authError":
            self._error = "Passwort falsch"
            self.hashed = None
        elif kind in ("fullStatus", "deltaStatus"):
            self.status.update(msg.get("status") or {})
            self.updated = time.monotonic()
        elif kind == "response":
            fut = self._pending.pop(str(msg.get("requestId")), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def set_value(self, key: str, value: Any) -> None:
        self._request += 1
        message = {"type": "setValue", "requestId": self._request, "key": key, "value": value}
        # Antwort-ID je nach Firmware mit oder ohne 'sm'-Suffix
        rids = (str(self._request), f"{self._request}sm")
        fut = asyncio.get_running_loop().create_future()
        for rid in rids:
            self._pending[rid] = fut
        try:
            await self._send(secured(self.hashed or "", message) if self.secured else message)
            res = await asyncio.wait_for(fut, timeout=5)
        except asyncio.TimeoutError:
            raise CommandNotApplied(f"Wattpilot: keine Bestätigung für {key}={value}") from None
        finally:
            for rid in rids:
                self._pending.pop(rid, None)
        if not res.get("success", False):
            raise DeviceRejected(f"Wattpilot: {key}={value} abgelehnt ({res.get('message') or 'ohne Grund'})")

    # ------------------------------------------------------------ Daten

    async def read_data(self) -> WallboxData:
        if not self.updated or time.monotonic() - self.updated > STALE_S:
            raise ConnectionProblem(f"Wattpilot {self.host}: keine aktuellen Daten ({self._error or 'wartet'})")
        s = self.status
        nrg = s.get("nrg") or []
        power = f(nrg[11]) if len(nrg) > 11 else 0.0
        currents = [f(v) for v in nrg[4:7]] if len(nrg) > 6 else None
        voltage = f(nrg[0]) if nrg else None
        state = CAR_STATES.get(int(f(s.get("car"), 1)), WallboxState.IDLE)
        enabled = int(f(s.get("frc"))) != 1
        return wallbox_data(state, power, current_set=f(s.get("amp")) if enabled else 0.0, currents=currents,
                            voltage=voltage, energy_session_kwh=f(s.get("wh")) / 1000.0 if s.get("wh") is not None
                            else None, source="Wattpilot nrg[11]")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.set_value("amp", int(max(self.min_current, min(amps, self.max_current))))

    async def start_charging(self) -> None:
        await self.set_value("frc", 0)

    async def stop_charging(self) -> None:
        await self.set_value("frc", 1)
