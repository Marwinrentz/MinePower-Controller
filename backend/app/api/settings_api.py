"""Globale Einstellungen, Prioritätskette, Zeitpläne."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime
from ..db import get_db
from ..models import Device, Schedule, Setting, User
from ..schemas import PriorityUpdate, ScheduleCreate, ScheduleOut, SettingsOut, SettingsUpdate
from ..security import get_current_user, require_role
from ..services.audit import log_event

from ..core.secrets import mask_settings, protect_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Abschnitte, die NIE über diese API gelesen oder geschrieben werden:
#:  * 'system" enthält das JWT-Secret. Es wurde bisher an jeden angemeldeten
#:    Nutzer ausgeliefert – wer es kennt, kann sich beliebige Admin-Tokens
#:    ausstellen. Die Oberfläche schickte es außerdem bei jedem Speichern
#:    zurück (daher 'system" in jeder Protokollzeile).
#:  * 'runtime" ist Laufzeitzustand des Regelkreises (laufende Boosts). Eine
#:    alte Kopie aus der Einstellungsseite würde ihn sonst überschreiben.
INTERNAL_KEYS = {"system", "runtime", "schema"}
def _visible(rows, user: User) -> dict:
    """Zugangsdaten (Tokens) für alle Rollen nur als '•••" – auch Admins
    sehen sie nach dem Speichern nicht mehr im Klartext."""
    out = {}
    for r in rows:
        if r.key in INTERNAL_KEYS:
            continue
        value = r.value
        if isinstance(value, dict):
            value = mask_settings(r.key, value)
        out[r.key] = value
    return out


@router.get("", response_model=SettingsOut)
async def get_all_settings(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (await db.scalars(select(Setting))).all()
    return SettingsOut(values=_visible(rows, user))


@router.put("", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    changed: list[str] = []
    for key, value in body.values.items():
        if key in INTERNAL_KEYS:
            continue  # stillschweigend ignorieren – ältere Oberflächen schicken es mit
        if not isinstance(value, dict):
            raise HTTPException(422, f"Wert für '{key}' muss ein Objekt sein")
        row = await db.get(Setting, key)
        value = protect_settings(key, value, row.value if row is not None and isinstance(row.value, dict) else None)
        if row is None:
            db.add(Setting(key=key, value=value))
            changed.append(key)
        elif row.value != value:
            row.value = value
            changed.append(key)
    await db.commit()
    if changed:
        # Nur was sich wirklich geändert hat – vorher stand jede Speicherung
        # mit allen sieben Abschnitten im Protokoll und sagte damit nichts.
        await log_event(f"Einstellungen geändert: {', '.join(changed)}", category="config", user_id=user.id)
        _reload()
    rows = (await db.scalars(select(Setting))).all()
    return SettingsOut(values=_visible(rows, user))


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
    times = []
    for t in (body.start_time, body.end_time):
        parts = t.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[0]) > 23 or int(parts[1]) > 59:
            raise HTTPException(422, f"Ungültige Uhrzeit: {t}")
        times.append(f"{int(parts[0]):02d}:{int(parts[1]):02d}")
    if times[0] == times[1]:
        raise HTTPException(422, "Beginn und Ende sind gleich.")
    if not 1 <= body.days_mask <= 127:
        raise HTTPException(422, "Mindestens einen Wochentag wählen.")
    if await db.get(Device, body.device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gerät nicht gefunden")
    schedule = Schedule(**{**body.model_dump(), "start_time": times[0], "end_time": times[1]})
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
