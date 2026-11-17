"""WebSocket-Manager: Live-Push des Regelungs-Snapshots an alle Clients."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        #: Konto je Verbindung, damit ein Passwortwechsel, eine Sperre oder
        #: das Löschen eines Kontos auch offene Live-Verbindungen beendet.
        self._users: dict[WebSocket, int] = {}
        self._lock = asyncio.Lock()

    #: Obergrenze gleichzeitiger Verbindungen. Jede kostet je Takt einen
    #: Sendevorgang; ohne Grenze ließe sich der Regelkreis ausbremsen.
    MAX_CLIENTS = 100

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        await self.add(ws)

    async def add(self, ws: WebSocket, user_id: int | None = None) -> bool:
        """Bereits angenommene Verbindung aufnehmen. False = voll."""
        async with self._lock:
            if len(self._clients) >= self.MAX_CLIENTS:
                return False
            self._clients.add(ws)
            if user_id is not None:
                self._users[ws] = user_id
            return True

    async def close_user(self, user_id: int, code: int = 4401) -> int:
        """Alle Live-Verbindungen eines Kontos schließen (Anzahl).

        Die Oberfläche prüft nach 4401 ihre Anmeldung und verbindet mit einem
        noch gültigen Token sofort neu; ein widerrufenes führt zur Anmeldung.
        """
        async with self._lock:
            targets = [ws for ws, uid in self._users.items() if uid == user_id]
            for ws in targets:
                self._clients.discard(ws)
                self._users.pop(ws, None)
        for ws in targets:
            try:
                await asyncio.wait_for(ws.close(code=code), timeout=2)
            except Exception:  # noqa: BLE001 – schon getrennt
                pass
        return len(targets)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            self._users.pop(ws, None)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload, default=str)
        async with self._lock:
            clients = list(self._clients)

        async def send(ws: WebSocket) -> WebSocket | None:
            try:
                # Zeitlimit: Ein Client, der nichts mehr abnimmt, darf weder
                # die anderen noch den Regelkreis aufhalten.
                await asyncio.wait_for(ws.send_text(message), timeout=5)
                return None
            except Exception:  # noqa: BLE001
                return ws

        # Gleichzeitig senden: nacheinander hätten wenige hängende Clients den
        # Takt um je 5 s verzögert.
        dead = [ws for ws in await asyncio.gather(*(send(ws) for ws in clients)) if ws is not None]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
                    self._users.pop(ws, None)


ws_manager = WebSocketManager()
