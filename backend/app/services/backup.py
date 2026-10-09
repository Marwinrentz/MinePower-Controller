"""Automatische lokale Sicherung von Geräten, Einstellungen und Konten.

Der Auslöser für dieses Modul: Ein harter Stromausfall killt den
Postgres-Container ohne jede Chance auf einen sauberen Checkpoint. Postgres
repariert das normalerweise selbst über sein WAL – *sofern* der Datenträger
fsync ehrlich beantwortet. Tut er das nicht (USB-Sticks, mancher NAS-Cache,
Consumer-SSDs ohne Power-Loss-Protection, das ist bei Heimservern die Regel
und keine Ausnahme), bleibt dem Betreiber oft nur, das kaputte
Datenverzeichnis zu löschen und Postgres neu zu initialisieren – leer.

Genau in diesem Moment greift dieses Modul: Beim nächsten Start findet
`startup.maybe_restore_from_backup()` eine leere, aber wieder funktionierende
Datenbank vor und spielt automatisch die letzte lokale Sicherung zurück –
ohne dass am Server irgendjemand manuell etwas einspielen muss.

Bewusst getrennt vom Export-Endpunkt (`/api/system/export`): Der ist für die
Übertragung auf eine andere Instanz gedacht und lässt Benutzerkonten aus.
Diese Sicherung bleibt lokal (eigenes Volume, verlässt den Server nie
automatisch) und schließt Konten samt Passwort-Hash ein – sonst stünde man
nach einer automatischen Wiederherstellung zwar mit allen Geräten, aber vor
dem Anmeldebildschirm da.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import async_session
from ..models import Device, Schedule, Setting, User, Vehicle

log = logging.getLogger(__name__)

BACKUP_VERSION = 1
_PREFIX = "minepower-"
_SUFFIX = ".json"


def _backup_dir() -> Path:
    path = Path(get_settings().backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _snapshot() -> dict[str, Any]:
    async with async_session() as session:
        devices = (await session.scalars(select(Device))).all()
        settings = (await session.scalars(select(Setting))).all()
        vehicles = (await session.scalars(select(Vehicle))).all()
        schedules = (await session.scalars(select(Schedule))).all()
        users = (await session.scalars(select(User))).all()
        return {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": {s.key: s.value for s in settings},
            "devices": [
                {"_id": d.id, "name": d.name, "category": d.category, "driver_id": d.driver_id,
                 "config": d.config, "settings": d.settings, "enabled": d.enabled}
                for d in devices
            ],
            "vehicles": [
                {"name": v.name, "capacity_kwh": v.capacity_kwh,
                 "wallbox_device_id": v.wallbox_device_id, "soc_source": v.soc_source,
                 "default_target_soc": v.default_target_soc}
                for v in vehicles
            ],
            "schedules": [
                {"device_id": s.device_id, "days_mask": s.days_mask, "start_time": s.start_time,
                 "end_time": s.end_time, "enabled": s.enabled}
                for s in schedules
            ],
            # Nur hier, nicht im manuellen Export: Ohne Konten wäre man nach
            # einer automatischen Wiederherstellung zwar bei den Geräten,
            # aber vor der Anmeldung ausgesperrt.
            "users": [
                {"email": u.email, "name": u.name, "password_hash": u.password_hash,
                 "role": u.role, "rfid_tag": u.rfid_tag, "language": u.language, "disabled": u.disabled}
                for u in users
            ],
        }


def _write_atomic(path: Path, data: dict) -> None:
    """Erst vollständig in eine Temp-Datei im selben Verzeichnis schreiben,
    dann umbenennen. `os.replace` ist auf demselben Dateisystem atomar – ein
    Absturz mittendrin hinterlässt entweder die alte oder gar keine neue
    Datei, nie eine halb geschriebene. Denselben Fehler, den wir bei
    Postgres reparieren, würden wir sonst bei der Sicherung selbst begehen."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=_SUFFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _prune(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted(directory.glob(f"{_PREFIX}*{_SUFFIX}"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


async def run_once() -> Path:
    """Eine Sicherung schreiben und alte über das Limit hinaus löschen."""
    cfg = get_settings()
    directory = _backup_dir()
    data = await _snapshot()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"{_PREFIX}{stamp}{_SUFFIX}"
    await asyncio.to_thread(_write_atomic, path, data)
    await asyncio.to_thread(_prune, directory, cfg.backup_keep)
    log.info("Lokale Sicherung geschrieben: %s (%d Geräte, %d Konten)",
              path.name, len(data["devices"]), len(data["users"]))
    return path


def latest_backup() -> Path | None:
    files = sorted(_backup_dir().glob(f"{_PREFIX}*{_SUFFIX}"))
    return files[-1] if files else None


def status() -> dict[str, Any]:
    """Für die Diagnose-/Einstellungsseite: sichtbar machen, dass der
    Mechanismus läuft, statt dass er sich erst beim nächsten Stromausfall
    beweisen muss."""
    path = latest_backup()
    if path is None:
        return {"count": 0, "latest": None, "dir": str(_backup_dir())}
    files = list(_backup_dir().glob(f"{_PREFIX}*{_SUFFIX}"))
    return {
        "count": len(files),
        "latest": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "dir": str(_backup_dir()),
    }


async def restore(path: Path) -> int:
    """Sicherung einspielen. Setzt eine leere Datenbank voraus (siehe
    `startup.maybe_restore_from_backup`) – es wird nichts gelöscht, nur
    eingefügt. Geräte-IDs werden neu vergeben und über `device_id_map` in
    den Fremdschlüsseln (Vehicle.wallbox_device_id, Schedule.device_id,
    Setting['priority']) nachgezogen, falls Postgres die Sequenz nicht exakt
    an der alten Stelle fortsetzt."""
    data = json.loads(path.read_text(encoding="utf-8"))
    async with async_session() as session:
        device_id_map: dict[int, int] = {}
        for dev in data.get("devices", []):
            row = Device(name=dev["name"], category=dev["category"], driver_id=dev["driver_id"],
                         config=dev.get("config", {}), settings=dev.get("settings", {}),
                         enabled=dev.get("enabled", True))
            session.add(row)
            await session.flush()
            if "_id" in dev:
                device_id_map[int(dev["_id"])] = row.id

        for key, value in (data.get("settings") or {}).items():
            if key == "priority" and device_id_map:
                value = {"order": [device_id_map.get(i, i) for i in value.get("order", [])]}
            session.add(Setting(key=key, value=value))

        for sch in data.get("schedules", []):
            device_id = device_id_map.get(int(sch["device_id"]), sch["device_id"])
            session.add(Schedule(device_id=device_id, days_mask=sch.get("days_mask", 127),
                                 start_time=sch["start_time"], end_time=sch["end_time"],
                                 enabled=sch.get("enabled", True)))

        for veh in data.get("vehicles", []):
            wallbox_id = veh.get("wallbox_device_id")
            session.add(Vehicle(
                name=veh["name"], capacity_kwh=veh.get("capacity_kwh", 60),
                wallbox_device_id=device_id_map.get(wallbox_id, wallbox_id) if wallbox_id else None,
                soc_source=veh.get("soc_source", "none"),
                default_target_soc=veh.get("default_target_soc", 80),
            ))

        for u in data.get("users", []):
            session.add(User(email=u["email"], name=u.get("name", u["email"]),
                             password_hash=u["password_hash"], role=u.get("role", "user"),
                             rfid_tag=u.get("rfid_tag"), language=u.get("language", "de"),
                             disabled=u.get("disabled", False)))

        await session.commit()
        return len(data.get("devices", []))


class BackupService:
    """Periodischer Hintergrund-Task, gestartet/gestoppt wie TariffService &
    Co. (siehe main.py lifespan)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="local-backup")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        cfg = get_settings()
        interval = max(cfg.backup_interval_h, 0.25) * 3600
        # Kurz nach dem Start eine erste Sicherung, statt die volle
        # Intervalldauer auf die allererste zu warten – sonst ist eine
        # frisch eingerichtete Anlage bei einem Stromausfall in der ersten
        # Stunde wieder komplett ungeschützt.
        await asyncio.sleep(60)
        while True:
            try:
                await run_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("Lokale Sicherung fehlgeschlagen: %s", exc)
            await asyncio.sleep(interval)
