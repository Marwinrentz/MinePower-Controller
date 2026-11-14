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
        self._lock = asyncio.Lock()

    #: Obergrenze gleichzeitiger Verbindungen. Jede kostet je Takt einen
    #: Sendevorgang; ohne Grenze ließe sich der Regelkreis ausbremsen.
    MAX_CLIENTS = 100

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        await self.add(ws)

    async def add(self, ws: WebSocket) -> bool:
        """Bereits angenommene Verbindung aufnehmen. False = voll."""
        async with self._lock:
            if len(self._clients) >= self.MAX_CLIENTS:
                return False
            self._clients.add(ws)
            return True

    @property
    def count(self) -> int:
        return len(self._clients)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload, default=str)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                # Zeitlimit: Ein Client, der nichts mehr abnimmt, darf weder
                # die anderen noch den Regelkreis aufhalten.
                await asyncio.wait_for(ws.send_text(message), timeout=5)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


ws_manager = WebSocketManager()
