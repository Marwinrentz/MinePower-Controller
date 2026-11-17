"""Live-Status: Snapshot-Endpunkt + WebSocket-Push."""
from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..core import runtime
from ..db import async_session
from ..models import User
from ..security import get_current_user, require_role, user_for_token, websocket_token
from ..services.ws import ws_manager

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status/live")
async def live_status(_: User = Depends(get_current_user)):
    return runtime.get_loop().snapshot


@router.post("/status/pause")
async def pause_regulation(_: User = Depends(require_role("user"))):
    """Globaler Not-Aus: Regelung pausieren (Lasten werden sicher gestoppt)."""
    runtime.get_loop().paused = True
    return {"ok": True, "paused": True}


@router.post("/status/resume")
async def resume_regulation(_: User = Depends(require_role("user"))):
    runtime.get_loop().paused = False
    return {"ok": True, "paused": False}


#: So lange darf eine Verbindung ohne Anmeldung offen bleiben (Sekunden)
WS_AUTH_TIMEOUT_S = 10.0
#: Schlusscodes: 4401 = nicht angemeldet, 4429 = zu viele Verbindungen
WS_UNAUTHORIZED = 4401
WS_TOO_MANY = 4429


async def _first_message_token(ws: WebSocket) -> str | None:
    """Anmeldung der Weboberfläche: erste Nachricht ``{"type":"auth","token":…}``."""
    import asyncio
    import json

    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=WS_AUTH_TIMEOUT_S)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        return None
    if isinstance(msg, dict) and msg.get("type") == "auth" and isinstance(msg.get("token"), str):
        return msg["token"]
    return None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    import json

    # Erst den Handshake abschließen, dann mit Grund schließen.
    #
    # Ein close() *vor* accept() lehnt den Handshake auf HTTP-Ebene ab
    # (403 Forbidden). Der Browser sieht dann nur einen abgebrochenen
    # Verbindungsversuch – Code 1006, ohne jede Begründung –, und der
    # Schlusscode 4401 kommt dort nie an. Das Frontend konnte das
    # abgelaufene Token deshalb nicht von einem Netzwerkabbruch
    # unterscheiden und hat endlos weiter verbunden: Die Oberfläche fror
    # ein, ohne je zur Anmeldung zu führen.
    await ws.accept()
    # Token aus dem Handshake (Kopfzeile bei Apps, ?token= bei älteren
    # Clients) oder als erste Nachricht (Weboberfläche ab 3.3.0).
    token = websocket_token(ws) or await _first_message_token(ws)
    async with async_session() as session:
        user = await user_for_token(token, session)
    if user is None:
        await _close(ws, WS_UNAUTHORIZED)
        return
    if not await ws_manager.add(ws, user_id=user.id):
        await _close(ws, WS_TOO_MANY)
        return
    try:
        # initialen Snapshot sofort liefern
        snapshot = runtime.get_loop().snapshot
        await ws.send_text(json.dumps({"type": "snapshot", "data": snapshot}, default=str))
        while True:
            await ws.receive_text()  # Keep-alive / Client-Pings
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await ws_manager.disconnect(ws)


async def _close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass  # Gegenstelle ist schon weg
