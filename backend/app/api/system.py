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
from ..core.secrets import (SETTINGS_SECRETS, device_secret_keys, protect_config, protect_settings, readable,
                            strip_config, strip_settings)

router = APIRouter(prefix="/api/system", tags=["system"])

EXPORT_VERSION = 1


#: Der Regelkreis gilt als stehend, wenn so lange kein Takt begonnen hat (s).
#: Großzügig: Ein Takt darf auf langsame Geräte warten.
LOOP_STALL_S = 180.0


def loop_health() -> dict[str, Any]:
    """Zustand des Regelkreises: läuft die Aufgabe, und kommen noch Takte?"""
    import time as _time

    try:
        loop = runtime.get_loop()
    except RuntimeError:
        return {"control_loop": "stopped", "healthy": False, "last_step_age_s": None}
    running = loop._task is not None and not loop._task.done()
    last = getattr(loop, "last_step_at", None)
    age = None if last is None else round(_time.monotonic() - last, 1)
    stalled = running and age is not None and age > max(LOOP_STALL_S, 6 * loop.cfg.interval_s)
    state = "stalled" if stalled else ("running" if running else "stopped")
    return {"control_loop": state, "healthy": running and not stalled, "last_step_age_s": age}


def _health_body() -> tuple[dict[str, Any], bool]:
    from .. import __version__

    state = loop_health()
    return ({"status": "ok" if state["healthy"] else "degraded", "control_loop": state["control_loop"],
             "last_step_age_s": state["last_step_age_s"], "version": __version__,
             "time": datetime.now(timezone.utc).isoformat()}, state["healthy"])


@router.get("/health")
async def health():
    """Ungeschützte Auskunft für Oberfläche und Apps: Version und Zustand.
    Antwortet immer mit 200 – auch eine Anlage mit stehendem Regelkreis muss
    sich noch öffnen lassen, um nachzusehen. `status` nennt den Zustand."""
    return _health_body()[0]


@router.get("/ready")
async def ready():
    """Healthcheck für Docker und Monitoring: 503, wenn der Regelkreis nicht
    läuft oder steht. Ohne ihn regelt nichts mehr, auch wenn die Oberfläche
    noch antwortet; Docker markiert den Container dann als 'unhealthy'."""
    from fastapi.responses import JSONResponse

    body, healthy = _health_body()
    return JSONResponse(body, status_code=200 if healthy else 503)


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


@router.get("/backups")
async def list_backups(_: User = Depends(require_role("admin"))):
    """Sicherungsdateien (Konfiguration, Verlauf), Speicherplatz und der
    Zustand des Messwert-Puffers."""
    from ..services import backup

    out: dict[str, Any] = {"backups": backup.list_backups(), "status": backup.status(),
                           "storage": await backup.storage_status()}
    try:
        out["measurements"] = runtime.get_loop().buffer.status()
    except RuntimeError:
        out["measurements"] = None
    return out


@router.post("/backups")
async def create_backup(user: User = Depends(require_role("admin"))):
    """Jetzt sichern: Konfiguration und Verlauf."""
    from ..services import backup

    config_path = await backup.run_once()
    data_path = await backup.run_data_dump()
    await log_event("Sicherung manuell erstellt", category="config", user_id=user.id)
    return {"ok": True, "config": config_path.name, "data": data_path.name}


@router.post("/backups/{name}/restore")
async def restore_backup(name: str, user: User = Depends(require_role("admin"))):
    """Eine Sicherung einspielen.

    * Konfiguration: Geräte, Einstellungen, Zeitpläne und Fahrzeuge kommen
      auf den Stand der Sicherung. Konten bleiben unverändert.
    * Verlauf: füllt nur auf, was fehlt (Stundenwerte, Ladevorgänge)."""
    from ..services import backup

    try:
        path = backup.resolve(name)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sicherung nicht gefunden")
    if name.startswith("pre-migration-"):
        raise HTTPException(422, "Sicherungen von vor einer Schema-Migration lassen sich nur von Hand einspielen")
    try:
        if name.endswith(".jsonl.gz"):
            counts = await backup.restore_data(path)
            result: dict[str, Any] = {"ok": True, "kind": "data", **counts}
        else:
            data = backup.read_config(path)
            # Vorher den jetzigen Stand sichern – das Einspielen ist sonst
            # nicht rückgängig zu machen.
            await backup.run_once()
            devices = await backup.restore_config(path, data)
            result = {"ok": True, "kind": "config", "devices": devices}
    except ConfigImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(422, f"Sicherung nicht lesbar: {exc}") from exc
    await log_event(f"Sicherung eingespielt: {name}", category="config", user_id=user.id)
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
    return result


@router.get("/backups/{name}/download")
async def download_backup(name: str, _: User = Depends(require_role("admin"))):
    """Verlaufssicherung herunterladen (Stundenwerte, Ladevorgänge,
    Ereignisse – keine Zugangsdaten). Die Konfiguration gibt es als Export
    ohne Zugangsdaten unter /api/system/export."""
    from fastapi.responses import FileResponse

    from ..services import backup

    if not name.endswith(".jsonl.gz"):
        raise HTTPException(422, "Nur Verlaufssicherungen lassen sich herunterladen")
    try:
        path = backup.resolve(name)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sicherung nicht gefunden")
    return FileResponse(path, media_type="application/gzip", filename=name)


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
    try:
        await apply_config_import(data)
    except ConfigImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    await log_event("Konfiguration importiert", category="config", user_id=user.id)
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
    return {"ok": True}


class ConfigImportError(ValueError):
    """Die Importdatei passt nicht (Aufbau, Treiber, Werte)."""


#: Obergrenzen einer Importdatei – eine Anlage hat eine Handvoll Geräte.
_IMPORT_MAX = {"devices": 200, "schedules": 500, "vehicles": 100}


def _check_import(data: dict[str, Any]) -> None:
    """Aufbau und Werte prüfen, bevor irgendetwas geändert wird."""
    from .devices import validate_device_settings

    for key, limit in _IMPORT_MAX.items():
        items = data.get(key, [])
        if not isinstance(items, list) or len(items) > limit or not all(isinstance(i, dict) for i in items):
            raise ConfigImportError(f"'{key}' muss eine Liste mit höchstens {limit} Einträgen sein")
    if not isinstance(data.get("settings") or {}, dict):
        raise ConfigImportError("'settings' muss ein Objekt sein")
    for dev in data.get("devices", []):
        name = str(dev.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ConfigImportError("Gerät ohne gültigen Namen")
        try:
            meta = registry.get_driver_class(str(dev.get("driver_id"))).meta
        except KeyError:
            raise ConfigImportError(f"{name}: unbekannter Treiber '{dev.get('driver_id')}'") from None
        if meta.category.value != dev.get("category"):
            raise ConfigImportError(f"{name}: Kategorie passt nicht zum Treiber")
        if not isinstance(dev.get("config", {}), dict):
            raise ConfigImportError(f"{name}: 'config' muss ein Objekt sein")
        try:
            validate_device_settings(meta.category.value, dev.get("settings", {}))
        except HTTPException as exc:
            raise ConfigImportError(f"{name}: {exc.detail}") from exc
    for sch in data.get("schedules", []):
        for key in ("device_id", "start_time", "end_time"):
            if key not in sch:
                raise ConfigImportError(f"Zeitplan ohne '{key}'")
    for veh in data.get("vehicles", []):
        if not str(veh.get("name") or "").strip():
            raise ConfigImportError("Fahrzeug ohne Namen")


async def apply_config_import(data: dict[str, Any], create_admin_if_missing: bool = False) -> None:
    """Import-Kern – auch vom Headless-Start (startup.py) genutzt.

    Bestehende Geräte werden **an Ort und Stelle** aktualisiert, wenn die
    Datei sie wiedererkennt (gleiche ID und gleicher Treiber). Früher wurden
    alle Geräte gelöscht und neu angelegt – damit verschwanden die
    Ladevorgänge (Fremdschlüssel mit Löschweitergabe) und alle gespeicherten
    Zugangsdaten, weil der Export sie nicht enthält. Jetzt bleiben beide
    erhalten; gelöscht wird nur, was in der Datei nicht mehr vorkommt."""
    _check_import(data)
    async with async_session() as session:
        existing = {d.id: d for d in (await session.scalars(select(Device))).all()}
        old_schedules = (await session.scalars(select(Schedule))).all()
        for s in old_schedules:
            await session.delete(s)

        # Mapping alter → neuer IDs für Priorität/Zeitpläne
        id_map: dict[int, int] = {}
        kept: set[int] = set()
        for dev in data.get("devices", []):
            driver_id = dev["driver_id"]
            config = dev.get("config", {}) or {}
            old_id = int(dev["_id"]) if str(dev.get("_id", "")).lstrip("-").isdigit() else None
            row = existing.get(old_id) if old_id is not None else None
            if row is not None and row.driver_id == driver_id and row.id not in kept:
                # Leere Zugangsdaten in der Datei = gespeicherten Wert behalten
                secrets = device_secret_keys(driver_id)
                merged = {k: v for k, v in config.items() if not (k in secrets and v in (None, ""))}
                row.name = dev["name"]
                row.category = dev["category"]
                row.config = protect_config(driver_id, merged, row.config or {})
                row.settings = dev.get("settings", {}) or {}
                row.enabled = bool(dev.get("enabled", True))
            else:
                row = Device(
                    name=dev["name"], category=dev["category"], driver_id=driver_id,
                    config=protect_config(driver_id, config), settings=dev.get("settings", {}) or {},
                    enabled=bool(dev.get("enabled", True)),
                )
                session.add(row)
                await session.flush()
            kept.add(row.id)
            if old_id is not None:
                id_map[old_id] = row.id
        for device_id, row in existing.items():
            if device_id not in kept:
                await session.delete(row)
        await session.flush()

        for key, value in (data.get("settings") or {}).items():
            if key in ("system", "runtime", "schema"):
                continue  # instanzeigen, siehe export_config
            if not isinstance(value, dict):
                continue
            if key == "priority":
                order = value.get("order", [])
                value = {"order": [id_map.get(i, i) for i in order if isinstance(i, int) and id_map.get(i, i) in kept]}
            row = await session.get(Setting, key)
            old_value = row.value if row is not None and isinstance(row.value, dict) else None
            # Wie bei den Geräten: leere Zugangsdaten überschreiben nichts.
            secret_keys = SETTINGS_SECRETS.get(key, set())
            value = {k: v for k, v in value.items() if not (k in secret_keys and v in (None, "") and old_value)}
            value = protect_settings(key, value, old_value)
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value

        for sch in data.get("schedules", []):
            try:
                device_id = id_map.get(int(sch["device_id"]), int(sch["device_id"]))
            except (TypeError, ValueError):
                continue
            if device_id not in kept:
                continue  # Zeitplan eines Geräts, das es nicht (mehr) gibt
            session.add(Schedule(device_id=device_id, days_mask=sch.get("days_mask", 127),
                                 start_time=str(sch["start_time"])[:5], end_time=str(sch["end_time"])[:5],
                                 enabled=sch.get("enabled", True)))

        # Fahrzeuge über den Namen zuordnen: Ein zweiter Import legte sie
        # sonst jedes Mal doppelt an.
        vehicles = {v.name: v for v in (await session.scalars(select(Vehicle))).all()}
        for veh in data.get("vehicles", []):
            name = str(veh["name"]).strip()[:120]
            row = vehicles.get(name)
            if row is None:
                row = Vehicle(name=name)
                session.add(row)
                vehicles[name] = row
            row.capacity_kwh = float(veh.get("capacity_kwh", 60) or 60)
            row.soc_source = str(veh.get("soc_source", "none"))[:20]
            row.default_target_soc = int(veh.get("default_target_soc", 80) or 80)

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
        stored = (settings.get("tariff") or {}).get("tibber_token")
        tariff["tibber_token_set"] = bool(stored) and readable(stored)
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
        # Regelkreis (läuft/steht) und Messwert-Puffer (wartet etwas auf die Datenbank?)
        "health": loop_health(),
        "measurements": loop.buffer.status(),
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
                # Antwortzeit (letzte, gleitendes Mittel) und Zähler seit dem Start
                "read_ms": None if d.read_ms is None else round(d.read_ms, 1),
                "read_ms_avg": None if d.read_ms_avg is None else round(d.read_ms_avg, 1),
                "reads_ok": d.reads_ok, "reads_failed": d.reads_failed,
                "last_error_at": d.last_error_at.isoformat() if d.last_error_at else None,
                "raw_data": d.data.model_dump() if d.data is not None else None,
                "decision": d.decision,
                "sent": {k: v for k, v in d.sent.items()},
            }
            for d in loop.devices.values()
        ],
    }


# ---------------------------------------------------------------- Protokoll, Export

@router.get("/logs")
async def logs(
    level: str = Query("info"),
    q: str = Query("", max_length=200),
    limit: int = Query(200, ge=1, le=2000),
    after_id: int = Query(0, ge=0),
    _: User = Depends(require_role("admin")),
):
    """Die jüngsten Protokollzeilen der Anwendung (im Speicher, seit dem
    Start), gefiltert nach Mindeststufe und Suchtext. Zugangsdaten sind
    geschwärzt. `after_id` liefert nur neuere Zeilen (für das Mitlesen)."""
    from ..core import logbuffer

    return {"entries": logbuffer.entries(level=level, query=q, limit=limit, after_id=after_id)}


@router.post("/notify-test")
async def notify_test(user: User = Depends(require_role("admin"))):
    """Testnachricht an alle eingerichteten Kanäle; Ergebnis je Kanal."""
    loop = runtime.get_loop()
    if loop.notifier is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Benachrichtigungen sind nicht verfügbar")
    await loop.notifier.refresh_config()
    result = await loop.notifier.deliver("Testnachricht – die Benachrichtigung funktioniert.")
    if not result:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Kanal eingerichtet")
    return {"ok": all(v is None for v in result.values()),
            "channels": [{"channel": k, "ok": v is None, "error": v} for k, v in result.items()]}


async def _or_error(fn) -> Any:
    """Einen Teil des Exports beschaffen; ein Fehler dabei wird als Text
    eingetragen, statt den ganzen Export zu verhindern."""
    import inspect

    try:
        result = fn()
        return await result if inspect.isawaitable(result) else result
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


@router.get("/diagnostic-export")
async def diagnostic_export(
    range: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Diagnose als ZIP für den Support: Bericht, Protokoll und Systemangaben.

    **Anonymisiert:** Zugangsdaten fehlen (wie im Diagnosebericht), dazu
    werden Netzwerkadressen, E-Mail-Adressen, Fahrgestellnummern und die
    Namen der Geräte durch Platzhalter ersetzt – gleiche Werte bekommen
    denselben Platzhalter, damit sich die Dateien weiter aufeinander
    beziehen lassen."""
    import io
    import json
    import platform
    import zipfile

    from fastapi.responses import Response

    from .. import __version__
    from ..core import logbuffer
    from ..services import backup
    from ..services.anonymize import Anonymizer

    devices = (await db.scalars(select(Device))).all()
    db.expunge_all()
    try:
        report: dict[str, Any] = await diagnostic_report(range=range, db=db, user=user)
    except Exception as exc:  # noqa: BLE001
        # Gerade wenn etwas klemmt (Datenbank, Auswertung), wird der Export
        # gebraucht: dann ohne Verlauf, mit dem, was der Regelkreis weiß.
        await db.rollback()
        loop = runtime.get_loop()
        from ..services.diagnostics import findings

        report = {
            "report_error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}",
            "findings": findings(loop, list(loop.devices.values()), {}),
            "live": loop.snapshot,
            "devices": [{"id": d.id, "name": d.name, "category": d.category, "driver_id": d.driver_id,
                         "enabled": d.enabled, "config": _redact_config(d.driver_id, d.config or {}),
                         "settings": d.settings or {}} for d in devices],
        }
    anon = Anonymizer(device_names=[d.name for d in devices])
    try:
        from .. import startup

        current, head = await startup._schema_revisions()
    except Exception:  # noqa: BLE001
        current = head = None
    system = {
        "version": __version__, "python": platform.python_version(), "platform": platform.platform(),
        "generated_at": datetime.now(timezone.utc).isoformat(), "health": loop_health(),
        "schema": {"database": current, "program": head},
        "storage": await _or_error(backup.storage_status), "backups": await _or_error(backup.status),
        "measurements": runtime.get_loop().buffer.status(),
        "drivers": sorted({d.driver_id for d in devices}),
    }
    lines = [f"{e['time']} {e['level'].upper():7s} {e['logger']}: {e['message']}"
             for e in logbuffer.entries(level="info", limit=logbuffer.CAPACITY)]
    readme = (
        "MinePower Diagnose-Export\n\n"
        "report.json  Befunde, Regelung, Verlauf und Ereignisse des Zeitraums\n"
        "system.json  Version, Zustand des Regelkreises, Speicherplatz, Sicherungen\n"
        "log.txt      Protokoll seit dem Start der Anwendung\n\n"
        "Anonymisiert: keine Zugangsdaten; Adressen, E-Mail-Adressen, Fahrgestellnummern und\n"
        "Gerätenamen sind durch Platzhalter ersetzt.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LIESMICH.txt", readme)
        zf.writestr("report.json", json.dumps(anon.scrub(report), ensure_ascii=False, indent=1, default=str))
        zf.writestr("system.json", json.dumps(anon.scrub(system), ensure_ascii=False, indent=1, default=str))
        zf.writestr("log.txt", anon.text("\n".join(lines)) + "\n")
    await log_event("Diagnose-Export erstellt", category="system", user_id=user.id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="minepower-diagnose-{stamp}.zip"'})


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
