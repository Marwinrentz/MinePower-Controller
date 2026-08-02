"""System: Health, Setup-Status, Config-Export/-Import, Demo-Modus, Diagnose."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime
from ..db import async_session, get_db
from ..drivers import registry
from ..drivers.base import FieldType
from ..models import Device, Schedule, Setting, User, Vehicle
from ..security import get_current_user, hash_password, require_role
from ..services.audit import log_event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

EXPORT_VERSION = 1


@router.get("/health")
async def health():
    """Ungeschützter Healthcheck für Docker/Monitoring."""
    loop_ok = False
    try:
        loop = runtime.get_loop()
        loop_ok = loop._task is not None and not loop._task.done()
    except RuntimeError:
        pass
    return {"status": "ok", "control_loop": "running" if loop_ok else "stopped",
            "time": datetime.now(timezone.utc).isoformat()}


@router.get("/setup-state")
async def setup_state(db: AsyncSession = Depends(get_db)):
    """Ungeschützt: braucht das Frontend, um Erst-Setup vs. Login zu entscheiden."""
    users = await db.scalar(select(func.count(User.id)))
    devices = await db.scalar(select(func.count(Device.id)))
    return {"needs_admin": (users or 0) == 0, "needs_devices": (devices or 0) == 0}


@router.get("/export")
async def export_config(db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    """Komplette Konfiguration als JSON (Backup / Übertragung auf andere Instanz).
    Hinweis: Geräte-Configs können Zugangsdaten enthalten – Datei sicher aufbewahren."""
    devices = (await db.scalars(select(Device))).all()
    settings = (await db.scalars(select(Setting))).all()
    vehicles = (await db.scalars(select(Vehicle))).all()
    schedules = (await db.scalars(select(Schedule))).all()
    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": {s.key: s.value for s in settings},
        "devices": [
            {"name": d.name, "category": d.category, "driver_id": d.driver_id,
             "config": d.config, "settings": d.settings, "enabled": d.enabled, "_id": d.id}
            for d in devices
        ],
        "vehicles": [
            {"name": v.name, "capacity_kwh": v.capacity_kwh, "soc_source": v.soc_source,
             "default_target_soc": v.default_target_soc}
            for v in vehicles
        ],
        "schedules": [
            {"device_id": s.device_id, "days_mask": s.days_mask, "start_time": s.start_time,
             "end_time": s.end_time, "enabled": s.enabled}
            for s in schedules
        ],
    }


@router.post("/import")
async def import_config(data: dict[str, Any], user: User = Depends(require_role("admin"))):
    """Konfiguration aus Export-JSON wiederherstellen (ersetzt Geräte & Einstellungen)."""
    if data.get("version") != EXPORT_VERSION:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unbekannte Export-Version")
    await apply_config_import(data)
    await log_event("Konfiguration importiert", category="config", user_id=user.id)
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
    return {"ok": True}


async def apply_config_import(data: dict[str, Any], create_admin_if_missing: bool = False) -> None:
    """Import-Kern – auch vom Headless-Start (startup.py) genutzt."""
    async with async_session() as session:
        # Geräte ersetzen; Mapping alter → neuer IDs für Priorität/Zeitpläne
        old_devices = (await session.scalars(select(Device))).all()
        for d in old_devices:
            await session.delete(d)
        old_schedules = (await session.scalars(select(Schedule))).all()
        for s in old_schedules:
            await session.delete(s)
        await session.flush()

        id_map: dict[int, int] = {}
        for dev in data.get("devices", []):
            row = Device(
                name=dev["name"], category=dev["category"], driver_id=dev["driver_id"],
                config=dev.get("config", {}), settings=dev.get("settings", {}),
                enabled=dev.get("enabled", True),
            )
            session.add(row)
            await session.flush()
            if "_id" in dev:
                id_map[int(dev["_id"])] = row.id

        for key, value in (data.get("settings") or {}).items():
            if key == "priority" and id_map:
                value = {"order": [id_map.get(i, i) for i in value.get("order", [])]}
            row = await session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value

        for sch in data.get("schedules", []):
            device_id = id_map.get(int(sch["device_id"]), sch["device_id"])
            session.add(Schedule(device_id=device_id, days_mask=sch.get("days_mask", 127),
                                 start_time=sch["start_time"], end_time=sch["end_time"],
                                 enabled=sch.get("enabled", True)))

        for veh in data.get("vehicles", []):
            session.add(Vehicle(name=veh["name"], capacity_kwh=veh.get("capacity_kwh", 60),
                                soc_source=veh.get("soc_source", "none"),
                                default_target_soc=veh.get("default_target_soc", 80)))

        if create_admin_if_missing:
            users = await session.scalar(select(func.count(User.id)))
            admin = (data.get("admin") or {})
            if not users and admin.get("email") and admin.get("password"):
                session.add(User(email=admin["email"].lower(), name=admin.get("name", "Admin"),
                                 password_hash=hash_password(admin["password"]), role="admin"))

        await session.commit()


@router.post("/demo")
async def enable_demo(user: User = Depends(require_role("admin"))):
    """Demo-Modus nachträglich aktivieren: legt simulierte Geräte an (nur bei leerer Anlage)."""
    from ..startup import setup_demo_mode

    await setup_demo_mode()
    await log_event("Demo-Modus aktiviert", category="config", user_id=user.id)
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
    return {"ok": True}


def _redact_config(driver_id: str, config: dict) -> dict:
    """Als Passwort deklarierte Konfig-Felder für den Diagnosebericht schwärzen.
    IP-Adressen, Ports, Unit-IDs u. Ä. bleiben sichtbar – die braucht man fürs
    Troubleshooting; nur echte Zugangsdaten werden ersetzt."""
    try:
        meta = registry.get_driver_class(driver_id).meta
    except KeyError:
        return config
    secret_keys = {f.key for f in meta.fields if f.type == FieldType.PASSWORD}
    return {k: ("•••" if k in secret_keys and v not in (None, "") else v) for k, v in config.items()}


@router.get("/diagnostic-report")
async def diagnostic_report(
    range: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Diagnosebericht als Ein-Datei-Download: Regelgüte-Kennzahlen,
    Rohverlauf (Leistungen, verschenkte Energie, Preis) und Ereignisse eines
    Zeitraums in einer Datei – gedacht, um sie z. B. für Troubleshooting
    weiterzugeben, ohne einzeln Screenshots oder Log-Ausschnitte sammeln zu
    müssen. Zugangsdaten in Geräte-Konfigurationen werden geschwärzt.

    Baut auf den bestehenden Endpunkten auf (Historie, Statistik, Ereignisse) –
    keine zusätzliche Abfragelogik, nur ein gebündelter Export."""
    from .events import list_events
    from .history import get_history
    from .statistics import statistics as compute_statistics

    fields = (
        "pv_power,grid_power,house_power,wallbox_power,water_power,"
        "battery_power,battery_soc,surplus,waste_power,price_ct"
    )
    history = await get_history(fields=fields, range=range, source="site", db=db, _=user)
    summary = await compute_statistics(range=range, db=db, _=user)
    events = await list_events(limit=500, level=None, category=None, db=db, _=user)

    loop = runtime.get_loop()
    devices = (await db.scalars(select(Device))).all()

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": range,
        "regulation": {
            "config": vars(loop.cfg),
            "priority_order": loop.priority,
            "deadband_effective_w": round(loop.deadband_effective, 1),
            "volatility_index": round(loop.volatility.index, 3),
            "weather": loop.volatility.describe(),
        },
        "summary": summary,
        "history": history,
        "events": [
            {"time": e.time.isoformat(), "level": e.level, "category": e.category, "message": e.message}
            for e in events
        ],
        "devices": [
            {
                "id": d.id, "name": d.name, "category": d.category, "driver_id": d.driver_id,
                "enabled": d.enabled, "status": d.status, "last_error": d.last_error,
                "config": _redact_config(d.driver_id, d.config or {}),
                "settings": d.settings or {},
            }
            for d in devices
        ],
    }


@router.get("/diagnostics")
async def diagnostics(_: User = Depends(get_current_user)):
    """Diagnose-Seite: Geräte-Rohwerte + letzte Regel-Entscheidungen live."""
    loop = runtime.get_loop()
    snapshot = loop.snapshot
    return {
        "regulation_config": vars(loop.cfg),
        "priority": loop.priority,
        "paused": loop.paused,
        "grid_smoothed": loop.grid_smoother.value,
        # Regelgüte auf einen Blick: Wie ruhig ist das Wetter, wie eng wird
        # dadurch geregelt, und wie viel Überschuss geht gerade verloren?
        "deadband_effective_w": round(loop.deadband_effective, 1),
        "deadband_configured_w": loop.cfg.deadband_w,
        "volatility_index": round(loop.volatility.index, 3),
        "weather": loop.volatility.describe(),
        "waste_w": snapshot.get("waste_w"),
        "waste_reason": snapshot.get("waste_reason"),
        "gap_filled_w": snapshot.get("gap_filled_w"),
        "forecast": loop.forecast.summary() if loop.forecast else None,
        "devices": [
            {
                "id": d.id, "name": d.name, "driver_id": d.driver_id, "category": d.category,
                "online": d.online, "failures": d.failures, "last_error": d.last_error,
                "command_error": d.command_error,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "raw_data": d.data.model_dump() if d.data is not None else None,
                "decision": d.decision,
                "sent": {k: v for k, v in d.sent.items()},
            }
            for d in loop.devices.values()
        ],
    }
