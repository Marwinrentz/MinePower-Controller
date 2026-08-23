"""Live-Status: Snapshot-Endpunkt + WebSocket-Push."""
from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..core import runtime
from ..db import async_session
from ..models import User
from ..security import authenticate_websocket, get_current_user
from ..services.ws import ws_manager

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status/live")
async def live_status(_: User = Depends(get_current_user)):
    return runtime.get_loop().snapshot


@router.post("/status/pause")
async def pause_regulation(_: User = Depends(get_current_user)):
    """Globaler Not-Aus: Regelung pausieren (Lasten werden sicher gestoppt)."""
    runtime.get_loop().paused = True
    return {"ok": True, "paused": True}


@router.post("/status/resume")
async def resume_regulation(_: User = Depends(get_current_user)):
    runtime.get_loop().paused = False
    return {"ok": True, "paused": False}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    async with async_session() as session:
        user = await authenticate_websocket(ws, session)
    if user is None:
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
        await ws.close(code=4401)
        return
    await ws_manager.connect(ws)
    try:
        # initialen Snapshot sofort liefern
        snapshot = runtime.get_loop().snapshot
        import json

        await ws.send_text(json.dumps({"type": "snapshot", "data": snapshot}, default=str))
        while True:
            await ws.receive_text()  # Keep-alive / Client-Pings
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)
