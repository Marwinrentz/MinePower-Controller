"""Audit-Log / Ereignis-Historie."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Event, User
from ..schemas import EventOut
from ..security import get_current_user

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
async def list_events(
    limit: int = 200,
    level: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Event).order_by(Event.time.desc()).limit(min(limit, 1000))
    if level:
        query = query.where(Event.level == level)
    if category:
        query = query.where(Event.category == category)
    return (await db.scalars(query)).all()
