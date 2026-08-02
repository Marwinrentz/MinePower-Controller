"""Globale Einstellungen, Prioritätskette, Zeitpläne."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime
from ..db import get_db
from ..models import Schedule, Setting, User
from ..schemas import PriorityUpdate, ScheduleCreate, ScheduleOut, SettingsOut, SettingsUpdate
from ..security import get_current_user, require_role
from ..services.audit import log_event

router = APIRouter(prefix="/api/settings", tags=["settings"])

SECRET_KEYS = {"notifications", "tariff"}  # enthalten ggf. Tokens


@router.get("", response_model=SettingsOut)
async def get_all_settings(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    rows = (await db.scalars(select(Setting))).all()
    return SettingsOut(values={r.key: r.value for r in rows})


@router.put("", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    for key, value in body.values.items():
        if not isinstance(value, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Wert für '{key}' muss ein Objekt sein")
        row = await db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    await db.commit()
    await log_event(f"Einstellungen geändert: {', '.join(body.values)}", category="config", user_id=user.id)
    _reload()
    rows = (await db.scalars(select(Setting))).all()
    return SettingsOut(values={r.key: r.value for r in rows})


@router.put("/priority")
async def update_priority(body: PriorityUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    row = await db.get(Setting, "priority")
    if row is None:
        row = Setting(key="priority", value={})
        db.add(row)
    row.value = {"order": body.order}
    await db.commit()
    await log_event(f"Prioritätskette geändert: {body.order}", category="config", user_id=user.id)
    _reload()
    return {"ok": True, "order": body.order}


@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return (await db.scalars(select(Schedule).order_by(Schedule.id))).all()


@router.post("/schedules", response_model=ScheduleOut)
async def create_schedule(body: ScheduleCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    for t in (body.start_time, body.end_time):
        parts = t.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültige Uhrzeit: {t}")
    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    _reload()
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    row = await db.get(Schedule, schedule_id)
    if row:
        await db.delete(row)
        await db.commit()
        _reload()


def _reload() -> None:
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
