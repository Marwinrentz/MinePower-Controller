"""System: Health, Setup-Status, Config-Export/-Import, Demo-Modus, Diagnose."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
from ..core.secrets import protect_config, protect_settings, strip_config, strip_settings

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
    from .. import __version__

    return {"status": "ok", "control_loop": "running" if loop_ok else "stopped",
            "version": __version__, "time": datetime.now(timezone.utc).isoformat()}


@router.get("/changelog")
async def changelog(user: User = Depends(get_current_user)):
    """Alle Versionen aus CHANGELOG.md und die seit dem letzten Besuch neuen."""
    from .. import __version__
    from ..core import changelog as cl

    return {"version": __version__, "entries": cl.entries(), "unseen": cl.since(user.last_seen_version)}


@router.get("/setup-state")
async def setup_state(db: AsyncSession = Depends(get_db)):
    """Ungeschützt: braucht das Frontend, um Erst-Setup vs. Login zu entscheiden."""
    users = await db.scalar(select(func.count(User.id)))
    rows = (await db.scalars(select(Device).where(Device.enabled.is_(True)))).all()
    return {"needs_admin": (users or 0) == 0, "needs_devices": not rows,
            "has_grid_measurement": has_grid_measurement(rows)}


def has_grid_measurement(devices) -> bool:
    """Ohne Netzmessung bleibt die Regelung im Sicherheitsstopp: Es braucht
    einen Netzzähler oder einen Wechselrichter mit integriertem Zähler."""
    for d in devices:
        if d.category == "meter":
            return True
        if d.category == "inverter":
            try:
                if "grid_meter" in registry.get_driver_class(d.driver_id).meta.capabilities:
                    return True
            except KeyError:
                continue
    return False


@router.get("/backup-status")
async def backup_status(_: User = Depends(require_role("admin"))):
    """Zeigt, dass die automatische lokale Sicherung läuft, statt dass sie
    sich erst beim nächsten Stromausfall beweisen muss (siehe
    services/backup.py)."""
    from ..services import backup

    return backup.status()


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
        # Ohne JWT-Secret und Laufzeitzustand: Eine weitergegebene Export-
        # Datei darf niemandem erlauben, Anmelde-Tokens dieser Instanz zu
        # fälschen, und ein Import soll keinen fremden Boost 'fortsetzen".
        # Ohne Zugangsdaten: Sie sind mit dem Schlüssel dieser Instanz
        # verschlüsselt und gehören nicht in eine weitergegebene Datei.
        "settings": {s.key: strip_settings(s.key, s.value) if isinstance(s.value, dict) else s.value
                     for s in settings if s.key not in ("system", "runtime", "schema")},
        "devices": [
            {"name": d.name, "category": d.category, "driver_id": d.driver_id,
             "config": strip_config(d.driver_id, d.config or {}), "settings": d.settings,
             "enabled": d.enabled, "_id": d.id}
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
        raise HTTPException(422, "Unbekannte Export-Version")
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
                config=protect_config(dev["driver_id"], dev.get("config", {})), settings=dev.get("settings", {}),
                enabled=dev.get("enabled", True),
            )
            session.add(row)
            await session.flush()
            if "_id" in dev:
                id_map[int(dev["_id"])] = row.id

        for key, value in (data.get("settings") or {}).items():
            if key in ("system", "runtime", "schema"):
                continue  # instanzeigen, siehe export_config
            if key == "priority" and id_map:
                value = {"order": [id_map.get(i, i) for i in value.get("order", [])]}
            if isinstance(value, dict):
                value = protect_settings(key, value)
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
    settings_rows = (await db.scalars(select(Setting))).all()
    settings = {r.key: r.value for r in settings_rows if r.key not in ("system", "runtime")}

    from ..services.diagnostics import findings, mask_vin
    from .battery import status_payload

    def device_entry(d: Device) -> dict:
        managed = loop.devices.get(d.id)
        config = _redact_config(d.driver_id, d.config or {})
        if "vin" in config:
            config["vin"] = mask_vin(config["vin"])
        entry = {
            "id": d.id, "name": d.name, "category": d.category, "driver_id": d.driver_id,
            "enabled": d.enabled,
            "config": config,
            "settings": d.settings or {},
        }
        # Laufzeitstatus statt des nie gepflegten DB-Felds – im alten Bericht
        # stand bei jedem Gerät 'unknown", egal ob es lief oder nicht.
        if managed is not None:
            entry.update({
                "status": "online" if managed.online else ("error" if managed.last_error else "offline"),
                "failures": managed.failures,
                "last_error": managed.last_error,
                "command_error": managed.command_error,
                "last_seen": managed.last_seen.isoformat() if managed.last_seen else None,
                "decision": managed.decision,
                "sent": dict(managed.sent),
                "duplicate_ignored": managed.name in loop.duplicate_devices,
            })
        else:
            entry.update({"status": "nicht im Regelkreis", "last_error": d.last_error})
        return entry

    tariff = loop.tariff.status() if loop.tariff and hasattr(loop.tariff, "status") else None
    if tariff is not None:
        tariff["tibber_token_set"] = bool((settings.get("tariff") or {}).get("tibber_token"))
    snap = loop.snapshot
    return {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": range,
        # Zuerst, was auffällt – der Rest ist Beleg dafür.
        "findings": findings(loop, devices, settings, events=events, summary=summary, history=history),
        "regulation": {
            "config": vars(loop.cfg),
            "priority_order": loop.priority,
            "deadband_effective_w": round(loop.deadband_effective, 1),
            "volatility_index": round(loop.volatility.index, 3),
            "weather": loop.volatility.describe(),
        },
        "live": {
            key: snap.get(key) for key in (
                "time", "price_ct", "price_cheap", "price_reason", "grid_price_window",
                "battery_locked", "battery_grid_charging", "battery_gate_reason",
                "battery_released", "battery_manual", "battery_manual_last", "grid_limit_w",
                "grid_budget_left_w", "warnings", "safety", "paused", "waste_reason",
            )
        },
        "battery": status_payload(loop),
        "tariff": tariff,
        "summary": summary,
        "history": history,
        "events": [
            {"time": e.time.isoformat(), "level": e.level, "category": e.category, "message": e.message}
            for e in events
        ],
        "devices": [device_entry(d) for d in devices],
    }


@router.get("/findings")
async def findings_endpoint(
    range: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Befunde für die Diagnose-Seite: Zustand jetzt + Verhalten im Zeitraum
    (Ereignisse, verschenkte Energie, Batterie-Verlauf), jeweils mit Ursache
    und Lösungsvorschlag. Siehe services/diagnostics.py."""
    from ..models import Event
    from ..services.diagnostics import findings
    from .history import get_history
    from .statistics import statistics as compute_statistics

    days = {"24h": 1, "7d": 7, "30d": 30}.get(range, 7)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    loop = runtime.get_loop()
    devices = (await db.scalars(select(Device))).all()
    settings_rows = (await db.scalars(select(Setting))).all()
    settings = {r.key: r.value for r in settings_rows if r.key not in ("system", "runtime")}
    events = (await db.scalars(
        select(Event).where(Event.time >= since).order_by(Event.time).limit(5000)
    )).all()
    # Geladene Objekte von der Sitzung lösen: Ein Rollback nach einer
    # fehlgeschlagenen Kennzahl-Abfrage würde sie sonst 'ablaufen" lassen,
    # und der nächste Attributzugriff (e.time) liefe als Nachlade-Abfrage in
    # eine abgebrochene Transaktion – die ganze Seite bekam dann einen 500.
    db.expunge_all()
    summary = history = None
    # Kennzahlen und Verlauf nutzen PostgreSQL-Funktionen; fehlen sie (z. B.
    # Testbetrieb mit SQLite), gibt es die übrigen Befunde trotzdem.
    try:
        summary = await compute_statistics(range=range, db=db, _=user)
    except Exception:  # noqa: BLE001
        await db.rollback()
    try:
        history = await get_history(fields="battery_soc", range=range, source="site", db=db, _=user)
    except Exception:  # noqa: BLE001
        await db.rollback()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": range,
        "findings": findings(loop, devices, settings, events=events, summary=summary, history=history),
    }


@router.get("/diagnostics")
async def diagnostics(_: User = Depends(get_current_user)):
    """Diagnose-Seite: Geräte-Rohwerte + letzte Regel-Entscheidungen live."""
    from ..services.diagnostics import findings
    from .battery import status_payload

    loop = runtime.get_loop()
    snapshot = loop.snapshot
    return {
        # Automatische Befunde und Hinweise zuerst (siehe services/diagnostics.py)
        "findings": findings(loop, list(loop.devices.values()), {}),
        "warnings": snapshot.get("warnings", []),
        # Batterie: Steuerbarkeit, Soll-/Istmodus, letzte Registerzugriffe
        "battery_control": status_payload(loop),
        "tariff": loop.tariff.status() if loop.tariff and hasattr(loop.tariff, "status") else None,
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
        # Batterie-Schutz sichtbar machen: Greift er? Wie viel wird
        # abgezogen? Und mit welchem Ladestand wurde gerechnet?
        "battery": snapshot.get("battery"),
        # Entladung, die nicht als Überschuss gilt …
        "battery_guard_w": round(loop.battery_guard_w, 1),
        # … und Ladeleistung, die die Lasten dem Speicher abnehmen dürfen
        # (nur ohne Batterie-Vorrang, sonst immer 0).
        "battery_release_w": round(loop.battery_release_w, 1),
        # Greift der harte Schutz gerade? Erst nach bestätigter Entladung.
        "battery_protected": loop.battery_protected,
        "battery_priority_soc": loop.cfg.battery_priority_soc,
        "battery_first": loop.battery_first_state,
        "battery_ev_support_soc": loop.cfg.battery_ev_support_soc,
        "battery_reserve_soc": loop.cfg.battery_reserve_soc,
        "battery_plan": {"intent": loop.battery_plan.intent.value, "reason": loop.battery_plan.reason,
                         "source": loop.battery_plan.source},
        "grid_loads": list(loop.grid_loads),
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


# ---------------------------------------------------------------- Demo-Szenarien

@router.post("/demo/scenario")
async def demo_scenario(body: dict, _: User = Depends(require_role("admin"))):
    """Nur im Demo-Modus: Zustände der Simulation umschalten, um alle
    Darstellungen zu sehen (Auto lädt/wartet/schläft/unterwegs, BLE-Proxy aus,
    Heizstab im Geräteprogramm, Boost). Ohne DEMO_MODE gibt es den Endpunkt nicht."""
    from ..config import get_settings
    from ..drivers.simulation import SimulationWorld

    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nur im Demo-Modus")
    w = SimulationWorld.instance()
    car = body.get("car")
    if car:
        w.car_proxy_offline = car == "proxy_offline"
        w.car_reachable = car not in ("asleep", "away")
        w.car_connected = car in ("charging", "waiting", "asleep", "complete")
        if car == "complete":
            w.car_soc = w.car_charge_limit
        elif w.car_soc >= w.car_charge_limit:
            w.car_soc = 45.0
    if "heater_program" in body:
        w.wh_device_program = bool(body["heater_program"])
    if "sun" in body:
        w.weather = max(0.2, min(1.0, float(body["sun"])))
    for key, attr in (("battery_soc", "bat_soc"), ("water_temp", "wh_temp"), ("car_soc", "car_soc")):
        if key in body:
            setattr(w, attr, float(body[key]))
    runtime.get_loop().kick()
    return {"ok": True}
