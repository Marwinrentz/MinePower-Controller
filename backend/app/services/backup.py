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
import gzip
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from ..config import get_settings
from ..db import async_session, engine
from ..models import ApiToken, ChargeSession, Device, Event, Schedule, Setting, User, Vehicle

log = logging.getLogger(__name__)

BACKUP_VERSION = 1
_PREFIX = "minepower-"
_SUFFIX = ".json"
#: Datensicherung: Stundenwerte, Ladevorgänge, Ereignisse (gzip, eine Zeile je Datensatz)
_DATA_PREFIX = "minepower-data-"
_DATA_SUFFIX = ".jsonl.gz"
DATA_VERSION = 1
#: So viele Ereignisse gehen in die Datensicherung (die jüngsten)
_DATA_EVENTS = 5000
#: Dateinamen, die die API annimmt (kein Pfad, keine fremden Dateien)
_NAME_RE = re.compile(r"^(minepower-(data-)?\d{8}-\d{6}|pre-migration-[A-Za-z0-9_.-]{1,60}-\d{8}-\d{6})\.(json|jsonl\.gz)$")


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
        tokens = (await session.execute(
            select(ApiToken, User.email).join(User, User.id == ApiToken.user_id))).all()
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
            # KI-Zugangsschlüssel (MCP): nur die Hash-Werte, nie der Schlüssel
            # selbst. Ohne sie wären nach einer Wiederherstellung alle
            # eingetragenen Schlüssel ungültig und müssten überall neu
            # angelegt werden. Nicht im manuellen Export, wie bei den Konten.
            "api_tokens": [
                {"user_email": email, "name": t.name, "token_hash": t.token_hash, "prefix": t.prefix,
                 "role": t.role, "created_at": t.created_at, "last_used_at": t.last_used_at}
                for t, email in tokens
            ],
        }


def _fsync_dir(directory: Path) -> None:
    """Den Verzeichniseintrag selbst auf die Platte bringen. Ohne das kann
    nach einem Stromausfall die fertig geschriebene Datei fehlen, weil nur
    ihr Inhalt, nicht aber ihr Name gesichert war."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # manche Dateisysteme (Netzlaufwerke) können das nicht
    finally:
        os.close(fd)


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
        _fsync_dir(path.parent)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _LineWriter:
    """gzip-Datei mit einer JSON-Zeile je Datensatz, atomar wie
    `_write_atomic`: erst Temp-Datei, am Ende umbenennen."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        fd, self._tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=_DATA_SUFFIX)
        self._raw = os.fdopen(fd, "wb")
        self._gz = gzip.GzipFile(fileobj=self._raw, mode="wb", compresslevel=6)

    def write(self, line: dict) -> None:
        self._gz.write(json.dumps(line, ensure_ascii=False, default=str, separators=(",", ":")).encode())
        self._gz.write(b"\n")
        self.count += 1

    def commit(self) -> None:
        self._gz.close()
        self._raw.flush()
        os.fsync(self._raw.fileno())
        self._raw.close()
        os.replace(self._tmp, self.path)
        _fsync_dir(self.path.parent)

    def abort(self) -> None:
        for closer in (self._gz.close, self._raw.close):
            try:
                closer()
            except (OSError, ValueError):
                pass
        Path(self._tmp).unlink(missing_ok=True)


async def write_named(label: str, data: dict) -> Path:
    """Eine einzelne Sicherungsdatei mit eigenem Namen (z. B. vor einer
    Schema-Migration). Unterliegt nicht der Rotation."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)[:80]
    path = _backup_dir() / f"{safe}-{stamp}{_SUFFIX}"
    await asyncio.to_thread(_write_atomic, path, data)
    return path


def _free_name(directory: Path, prefix: str, suffix: str) -> Path:
    """Dateiname mit Zeitstempel, der noch nicht vergeben ist. Zwei
    Sicherungen in derselben Sekunde überschrieben einander sonst."""
    when = datetime.now(timezone.utc)
    while True:
        path = directory / f"{prefix}{when.strftime('%Y%m%d-%H%M%S')}{suffix}"
        if not path.exists():
            return path
        when += timedelta(seconds=1)


def _config_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob(f"{_PREFIX}*{_SUFFIX}") if not p.name.startswith(_DATA_PREFIX))


def _data_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"{_DATA_PREFIX}*{_DATA_SUFFIX}"))


def _prune(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    for old in _config_files(directory)[:-keep]:
        old.unlink(missing_ok=True)
    # Sicherungen von vor Schema-Migrationen: die letzten fünf behalten
    for old in sorted(directory.glob(f"pre-migration-*{_SUFFIX}"))[:-5]:
        old.unlink(missing_ok=True)


def _prune_data(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    for old in _data_files(directory)[:-keep]:
        old.unlink(missing_ok=True)


async def run_once() -> Path:
    """Eine Sicherung schreiben und alte über das Limit hinaus löschen."""
    cfg = get_settings()
    directory = _backup_dir()
    data = await _snapshot()
    path = _free_name(directory, _PREFIX, _SUFFIX)
    await asyncio.to_thread(_write_atomic, path, data)
    await asyncio.to_thread(_prune, directory, cfg.backup_keep)
    log.info("Lokale Sicherung geschrieben: %s (%d Geräte, %d Konten)",
              path.name, len(data["devices"]), len(data["users"]))
    return path


def latest_backup() -> Path | None:
    files = _config_files(_backup_dir())
    return files[-1] if files else None


def latest_data_backup() -> Path | None:
    files = _data_files(_backup_dir())
    return files[-1] if files else None


def status() -> dict[str, Any]:
    """Für die Diagnose-/Einstellungsseite: sichtbar machen, dass der
    Mechanismus läuft, statt dass er sich erst beim nächsten Stromausfall
    beweisen muss."""
    directory = _backup_dir()
    files = _config_files(directory)
    data_files = _data_files(directory)

    def mtime(path: Path | None) -> str | None:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if path else None

    return {
        "count": len(files),
        "latest": mtime(files[-1] if files else None),
        "dir": str(directory),
        "data_count": len(data_files),
        "data_latest": mtime(data_files[-1] if data_files else None),
        "data_bytes": data_files[-1].stat().st_size if data_files else 0,
    }


def list_backups() -> list[dict[str, Any]]:
    """Alle Sicherungsdateien, neueste zuerst."""
    directory = _backup_dir()
    out: list[dict[str, Any]] = []
    for path, kind in ([(p, "config") for p in _config_files(directory)]
                       + [(p, "data") for p in _data_files(directory)]
                       + [(p, "pre-migration") for p in directory.glob(f"pre-migration-*{_SUFFIX}")]):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({"name": path.name, "kind": kind, "bytes": stat.st_size,
                    "time": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()})
    return sorted(out, key=lambda e: e["time"], reverse=True)


def resolve(name: str) -> Path:
    """Dateiname aus der API → Pfad im Sicherungsordner. Nimmt nur Namen an,
    die diese Anwendung selbst vergibt (kein Pfad, kein '..')."""
    if not _NAME_RE.match(name):
        raise FileNotFoundError(name)
    path = _backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _parse_time(value) -> datetime | None:
    """Zeitstempel aus der JSON-Sicherung (ISO-Text) zurück in ein datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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
        await session.flush()

        # Schlüssel dem Konto über die E-Mail zuordnen (IDs werden neu vergeben).
        user_ids = {email: uid for uid, email in (await session.execute(select(User.id, User.email))).all()}
        for tok in data.get("api_tokens", []):
            user_id = user_ids.get(tok.get("user_email"))
            if user_id is None or not tok.get("token_hash"):
                continue
            session.add(ApiToken(
                user_id=user_id, name=tok.get("name", "KI"), token_hash=tok["token_hash"],
                prefix=tok.get("prefix", ""), role=tok.get("role", "readonly"),
                created_at=_parse_time(tok.get("created_at")), last_used_at=_parse_time(tok.get("last_used_at")),
            ))

        await session.commit()
        return len(data.get("devices", []))


# ---------------------------------------------------------------- Daten

async def _hourly_rows(session):
    """Stundenmittel je Quelle und Größe über den gesamten Bestand."""
    from .. import startup

    dialect = engine.dialect.name
    if dialect == "postgresql" and startup.HOURLY_VIEW:
        sql = "SELECT bucket AS ts, source, field, avg_value AS value FROM measurements_hourly ORDER BY bucket"
    elif dialect == "postgresql":
        sql = ("SELECT date_trunc('hour', time) AS ts, source, field, avg(value) AS value "
               "FROM measurements GROUP BY 1, 2, 3 ORDER BY 1")
    else:  # SQLite (Tests)
        sql = ("SELECT strftime('%Y-%m-%dT%H:00:00+00:00', time) AS ts, source, field, avg(value) AS value "
               "FROM measurements GROUP BY 1, 2, 3 ORDER BY 1")
    result = await session.stream(text(sql))
    async for row in result:
        ts = row.ts.isoformat() if hasattr(row.ts, "isoformat") else str(row.ts)
        yield {"k": "m", "t": ts, "s": row.source, "f": row.field, "v": round(float(row.value), 3)}


async def run_data_dump() -> Path:
    """Verlauf sichern: Stundenwerte, Ladevorgänge und die jüngsten
    Ereignisse. Zusammen mit der Konfigurationssicherung reicht das, um nach
    dem Verlust der Datenbank nicht bei null zu beginnen.

    Geschrieben wird Zeile für Zeile – der Speicherbedarf hängt nicht von
    der Länge des Verlaufs ab."""
    cfg = get_settings()
    directory = _backup_dir()
    path = _free_name(directory, _DATA_PREFIX, _DATA_SUFFIX)
    writer = _LineWriter(path)
    try:
        async with async_session() as session:
            devices = (await session.scalars(select(Device))).all()
            writer.write({"k": "header", "version": DATA_VERSION,
                          "created_at": datetime.now(timezone.utc).isoformat(),
                          # Geräte-IDs werden bei einer Wiederherstellung neu vergeben;
                          # über Treiber und Name finden die Daten ihr Gerät wieder.
                          "devices": [{"_id": d.id, "name": d.name, "driver_id": d.driver_id} for d in devices]})
            for cs in (await session.scalars(select(ChargeSession).order_by(ChargeSession.id))).all():
                writer.write({"k": "s", "device_id": cs.device_id, "rfid_tag": cs.rfid_tag,
                              "started_at": cs.started_at, "ended_at": cs.ended_at, "energy_kwh": cs.energy_kwh,
                              "solar_kwh": cs.solar_kwh, "cost_eur": cs.cost_eur, "avg_price_ct": cs.avg_price_ct})
            events = (await session.scalars(select(Event).order_by(Event.time.desc()).limit(_DATA_EVENTS))).all()
            for ev in reversed(events):
                writer.write({"k": "e", "time": ev.time, "level": ev.level, "category": ev.category,
                              "message": ev.message, "data": ev.data})
            async for row in _hourly_rows(session):
                writer.write(row)
                if writer.count % 2000 == 0:
                    await asyncio.sleep(0)  # dem Regelkreis Luft lassen
        await asyncio.to_thread(writer.commit)
    except BaseException:
        writer.abort()
        raise
    await asyncio.to_thread(_prune_data, directory, cfg.backup_data_keep)
    log.info("Datensicherung geschrieben: %s (%d Zeilen, %.1f MB)", path.name, writer.count,
             path.stat().st_size / 1e6)
    return path


def _read_lines(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        try:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # abgeschnittene Zeile
        except (EOFError, OSError):
            return  # Datei endet mitten im Block (Absturz beim Schreiben)


async def restore_data(path: Path) -> dict[str, int]:
    """Datensicherung einspielen. Gefahrlos wiederholbar: Messwerte mit
    gleicher Zeit, Quelle und Größe werden übersprungen, Ladevorgänge mit
    gleichem Gerät und Beginn ebenfalls. Füllt also auch nur Lücken.

    Zwei Durchgänge über die Datei, jeweils Zeile für Zeile: zuerst
    Ladevorgänge und Ereignisse, dann die Messwerte in Paketen."""
    from .measurements import CHUNK_ROWS, _write

    counts = {"measurements": 0, "sessions": 0, "events": 0}
    async with async_session() as session:
        current = {(d.driver_id, d.name): d.id for d in (await session.scalars(select(Device))).all()}
        known_ids = set(current.values())
        id_map: dict[int, int] = {}
        existing_sessions = {(s.device_id, _as_utc(s.started_at))
                             for s in (await session.scalars(select(ChargeSession))).all()}
        have_events = bool(await session.scalar(select(Event.id).limit(1)))
        for line in _read_lines(path):
            kind = line.get("k")
            if kind == "header":
                id_map = {int(d["_id"]): current[(d.get("driver_id"), d.get("name"))]
                          for d in line.get("devices", []) if (d.get("driver_id"), d.get("name")) in current}
            elif kind == "s":
                device_id = id_map.get(int(line["device_id"]), int(line["device_id"]))
                started = _parse_time(line.get("started_at"))
                if device_id not in known_ids or started is None or (device_id, _as_utc(started)) in existing_sessions:
                    continue
                session.add(ChargeSession(
                    device_id=device_id, rfid_tag=line.get("rfid_tag"), started_at=started,
                    ended_at=_parse_time(line.get("ended_at")), energy_kwh=float(line.get("energy_kwh") or 0),
                    solar_kwh=float(line.get("solar_kwh") or 0), cost_eur=float(line.get("cost_eur") or 0),
                    avg_price_ct=line.get("avg_price_ct")))
                counts["sessions"] += 1
            elif kind == "e" and not have_events:
                # Ereignisse nur in ein leeres Protokoll – sie haben keinen
                # Schlüssel, an dem sich Doppelte erkennen ließen.
                when = _parse_time(line.get("time"))
                if when is None:
                    continue
                session.add(Event(time=when, level=line.get("level", "info"), category=line.get("category", "system"),
                                  message=str(line.get("message", "")), data=line.get("data") or {}))
                counts["events"] += 1
            elif kind == "m":
                break  # Messwerte stehen am Ende: zweiter Durchgang
        await session.commit()

    rows: list[dict] = []
    for line in _read_lines(path):
        if line.get("k") != "m":
            continue
        when = _parse_time(line.get("t"))
        if when is None:
            continue
        source = str(line.get("s"))
        if source.startswith("device:"):
            try:
                old = int(source.split(":", 1)[1])
            except ValueError:
                continue
            new = id_map.get(old, old)
            if new not in known_ids:
                continue
            source = f"device:{new}"
        try:
            rows.append({"time": _as_utc(when), "source": source, "field": str(line.get("f")),
                         "value": float(line["v"])})
        except (KeyError, TypeError, ValueError):
            continue
        if len(rows) >= CHUNK_ROWS:
            await _write(rows)
            counts["measurements"] += len(rows)
            rows = []
    if rows:
        await _write(rows)
        counts["measurements"] += len(rows)
    if counts["measurements"]:
        from .. import startup

        await startup.refresh_hourly_view()
    return counts


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def restore_config(path: Path, data: dict | None = None) -> int:
    """Konfigurationssicherung in eine **laufende** Anlage einspielen:
    Geräte, Einstellungen, Zeitpläne und Fahrzeuge kommen auf den Stand der
    Sicherung. Konten und Zugangsschlüssel bleiben, wie sie sind – sonst
    sperrte man sich mit einer alten Sicherung selbst aus."""
    from ..api.system import apply_config_import

    if data is None:
        data = read_config(path)
    payload = {"devices": data.get("devices", []), "schedules": data.get("schedules", []),
               "vehicles": data.get("vehicles", []),
               "settings": {k: v for k, v in (data.get("settings") or {}).items()
                            if k not in ("system", "runtime", "schema")}}
    await apply_config_import(payload)
    return len(payload["devices"])


# ---------------------------------------------------------------- Speicherplatz

#: Warnschwellen: weniger als 1 GiB oder weniger als 5 % frei
_FREE_MIN_BYTES = 1024 ** 3
_FREE_MIN_SHARE = 0.05


async def storage_status() -> dict[str, Any]:
    """Belegung der Volumes und Größe der Datenbank. `warning` nennt den
    Grund, wenn der Platz knapp wird – bevor PostgreSQL mit voller Platte
    stehen bleibt."""
    cfg = get_settings()
    out: dict[str, Any] = {"volumes": [], "database_bytes": None, "warning": None}
    seen: set[int] = set()
    for label, directory in (("data", cfg.data_dir), ("backups", cfg.backup_dir)):
        try:
            usage = shutil.disk_usage(directory)
            device = os.stat(directory).st_dev
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        share = usage.free / usage.total if usage.total else 1.0
        out["volumes"].append({"name": label, "path": str(directory), "total_bytes": usage.total,
                               "free_bytes": usage.free, "free_share": round(share, 4)})
        if usage.free < _FREE_MIN_BYTES or share < _FREE_MIN_SHARE:
            out["warning"] = (f"Speicherplatz knapp: {usage.free / 1024 ** 3:.1f} GB frei "
                              f"({share * 100:.0f} %) auf {directory}")
    if engine.dialect.name == "postgresql":
        try:
            async with async_session() as session:
                out["database_bytes"] = int(await session.scalar(text("SELECT pg_database_size(current_database())")))
        except Exception as exc:  # noqa: BLE001
            log.debug("Datenbankgröße nicht lesbar: %s", exc)
    return out


class BackupService:
    """Periodischer Hintergrund-Task, gestartet/gestoppt wie TariffService &
    Co. (siehe main.py lifespan)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._storage_warned = False

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
        data_interval = timedelta(hours=max(cfg.backup_data_interval_h, 1.0))
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
            try:
                latest = latest_data_backup()
                age = (datetime.now(timezone.utc)
                       - datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)) if latest else None
                if cfg.backup_data_keep > 0 and (age is None or age >= data_interval):
                    await run_data_dump()
            except Exception as exc:  # noqa: BLE001
                log.warning("Datensicherung fehlgeschlagen: %s", exc)
            await self._check_storage()
            await asyncio.sleep(interval)

    async def _check_storage(self) -> None:
        """Knappen Speicherplatz melden – einmal, bis er sich wieder erholt."""
        try:
            warning = (await storage_status())["warning"]
        except Exception as exc:  # noqa: BLE001
            log.debug("Speicherplatz nicht prüfbar: %s", exc)
            return
        if warning and not self._storage_warned:
            from .audit import log_event

            await log_event(warning, level="warning", category="system")
            try:
                from ..core import runtime

                notifier = runtime.get_loop().notifier
                if notifier:
                    await notifier.send(warning)
            except RuntimeError:
                pass
        self._storage_warned = bool(warning)
