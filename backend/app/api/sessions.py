"""Ladevorgänge (Charge-Sessions) inkl. Nutzer-/RFID-Zuordnung."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ChargeSession, User
from ..schemas import ChargeSessionOut
from ..security import get_current_user, require_role

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[ChargeSessionOut])
async def list_sessions(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = await db.scalars(
        select(ChargeSession).order_by(ChargeSession.started_at.desc()).limit(min(limit, 500)).offset(offset)
    )
    return rows.all()


@router.patch("/{session_id}/assign", response_model=ChargeSessionOut)
async def assign_session(
    session_id: int,
    user_id: int | None = None,
    vehicle_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Session nachträglich einem Nutzer/Fahrzeug zuordnen (kWh-Abrechnung)."""
    row = await db.get(ChargeSession, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session nicht gefunden")
    if user_id is not None:
        row.user_id = user_id
    if vehicle_id is not None:
        row.vehicle_id = vehicle_id
    await db.commit()
    await db.refresh(row)
    return row
