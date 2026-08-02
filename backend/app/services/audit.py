"""Audit-Log / Ereignis-Historie."""
from __future__ import annotations

import logging

from ..db import async_session
from ..models import Event

log = logging.getLogger(__name__)


async def log_event(
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    data: dict | None = None,
    user_id: int | None = None,
) -> None:
    try:
        async with async_session() as session:
            session.add(Event(level=level, category=category, message=message, data=data or {}, user_id=user_id))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("Event konnte nicht gespeichert werden: %s (%s)", message, exc)
    getattr(log, level if level in ("info", "warning", "error") else "info")("[%s] %s", category, message)
