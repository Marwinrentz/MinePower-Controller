"""Audit-Log / Ereignis-Historie.

Ereignisse, die wegen eines Datenbank-Ausfalls nicht gespeichert werden
konnten, bleiben in einer kleinen Warteschlange und werden mit dem nächsten
Ereignis nachgetragen (mit ihrer ursprünglichen Zeit). So steht nach einem
Ausfall auch im Protokoll, was währenddessen geschah.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

from ..db import async_session
from ..models import Event

log = logging.getLogger(__name__)

#: Nicht gespeicherte Ereignisse (älteste fallen heraus)
_pending: deque[dict] = deque(maxlen=500)


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
    queued = list(_pending)
    try:
        async with async_session() as session:
            for item in (*queued, entry):
                session.add(Event(**item))
            await session.commit()
        for _ in queued:
            _pending.popleft()
    except Exception as exc:  # noqa: BLE001
        _pending.append(entry)
        log.error("Event konnte nicht gespeichert werden (wird nachgetragen): %s (%s)", message, exc)
    getattr(log, level if level in ("info", "warning", "error") else "info")("[%s] %s", category, message)


def pending_events() -> int:
    return len(_pending)
