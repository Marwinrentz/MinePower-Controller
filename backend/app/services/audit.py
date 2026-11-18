"""Audit-Log / Ereignis-Historie.

Ereignisse, die wegen eines Datenbank-Ausfalls nicht gespeichert werden
konnten, bleiben in einer kleinen Warteschlange und werden mit dem nächsten
Ereignis nachgetragen (mit ihrer ursprünglichen Zeit). So steht nach einem
Ausfall auch im Protokoll, was währenddessen geschah.

Schreiben läuft nacheinander (Lock): Zwei gleichzeitige Aufrufe während
eines Ausfalls schrieben vorher dieselbe Warteschlange doppelt oder
verloren Einträge. Und der Aufrufer wartet höchstens EVENT_WAIT_S – der
Regeltakt ruft log_event direkt auf und darf an einer hängenden Datenbank
nicht stehen bleiben. Der Schreibvorgang läuft dann im Hintergrund fertig.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

from ..db import async_session
from ..models import Event

log = logging.getLogger(__name__)

#: Nicht gespeicherte Ereignisse (älteste fallen heraus)
_pending: deque[dict] = deque(maxlen=500)
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None
_tasks: set[asyncio.Task] = set()

#: Höchstwartezeit des Aufrufers; der Schreibvorgang selbst läuft weiter.
EVENT_WAIT_S = 3.0


def _get_lock() -> asyncio.Lock:
    # Ein Lock gehört zu einer Event-Loop; Tests starten je Test eine neue.
    global _lock, _lock_loop
    current = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not current:
        _lock, _lock_loop = asyncio.Lock(), current
    return _lock


async def _store(entry: dict) -> None:
    async with _get_lock():
        queued = list(_pending)
        try:
            async with async_session() as session:
                for item in (*queued, entry):
                    session.add(Event(**item))
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            _pending.append(entry)
            log.error("Event konnte nicht gespeichert werden (wird nachgetragen): %s (%s)", entry["message"], exc)
            return
        for item in queued:
            try:
                _pending.remove(item)
            except ValueError:
                pass  # inzwischen aus der vollen Warteschlange gefallen


async def log_event(
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    data: dict | None = None,
    user_id: int | None = None,
) -> None:
    entry = {"time": datetime.now(timezone.utc), "level": level, "category": category, "message": message,
             "data": data or {}, "user_id": user_id}
    getattr(log, level if level in ("info", "warning", "error") else "info")("[%s] %s", category, message)
    task = asyncio.create_task(_store(entry), name="log-event")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=EVENT_WAIT_S)
    except asyncio.TimeoutError:
        log.warning("Ereignis-Protokoll: Datenbank antwortet langsam, Eintrag wird im Hintergrund gespeichert")


def pending_events() -> int:
    return len(_pending)
