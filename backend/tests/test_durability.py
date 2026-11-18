"""Robustheit: Messwerte überleben einen Datenbank-Ausfall, Sicherungen
lassen sich zurückspielen, das Schema migriert sich selbst."""
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app import startup
from app.config import get_settings
from app.db import Base, async_session, engine
from app.models import ChargeSession, Device, Event, Measurement, Setting, User
from app.services import audit, backup, measurements
from app.services.measurements import MeasurementBuffer

from .asgi import request


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.run_sync(Base.metadata.create_all)
    audit._pending.clear()
    yield


T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


async def count(model) -> int:
    async with async_session() as db:
        return await db.scalar(select(func.count()).select_from(model)) or 0


def fill(buf: MeasurementBuffer, start: int, n: int) -> None:
    for i in range(start, start + n):
        buf.add("site", "pv_power", 1000.0 + i, T0 + timedelta(seconds=10 * i))


# ---------------------------------------------------------------- Messwert-Puffer

async def test_buffer_writes_and_skips_duplicates(tmp_path):
    buf = MeasurementBuffer(spool_path=tmp_path / "spool.jsonl")
    fill(buf, 0, 5)
    assert await buf.flush() and await count(Measurement) == 5
    fill(buf, 3, 5)  # drei davon sind schon da
    assert await buf.flush() and await count(Measurement) == 8


async def test_buffer_ignores_values_that_are_no_numbers(tmp_path):
    buf = MeasurementBuffer(spool_path=tmp_path / "spool.jsonl")
    for value in (None, float("nan"), float("inf"), "abc"):
        buf.add("site", "pv_power", value, T0)
    assert buf.status()["pending_rows"] == 0


async def test_outage_is_spooled_and_replayed_without_gap_or_duplicate(tmp_path, monkeypatch):
    spool = tmp_path / "spool.jsonl"
    buf = MeasurementBuffer(spool_path=spool)
    real_write = measurements._write
    down = True

    async def flaky(rows):
        if down:
            raise ConnectionError("Datenbank nicht erreichbar")
        await real_write(rows)

    monkeypatch.setattr(measurements, "_write", flaky)
    for batch in range(3):
        fill(buf, batch * 10, 10)
        assert await buf.flush() is False
    assert await count(Measurement) == 0
    assert spool.exists() and len(spool.read_text().splitlines()) == 30
    assert buf.status()["failures"] == 3 and "ConnectionError" in buf.status()["last_error"]

    # Neustart der Anwendung, Datenbank noch weg: neuer Puffer, gleiche Datei
    buf = MeasurementBuffer(spool_path=spool)
    fill(buf, 30, 10)
    assert await buf.flush() is False
    assert len(spool.read_text().splitlines()) == 40

    # Datenbank wieder da
    down = False
    fill(buf, 40, 10)
    assert await buf.flush() is True
    assert await count(Measurement) == 50
    assert not spool.exists() and buf.status()["replayed"] == 40 and buf.status()["failures"] == 0
    async with async_session() as db:
        times = (await db.scalars(select(Measurement.time).order_by(Measurement.time))).all()
    gaps = {(b - a).total_seconds() for a, b in zip(times, times[1:])}
    assert gaps == {10.0}  # keine Lücke, kein Duplikat


async def test_replay_is_repeatable_after_interruption(tmp_path, monkeypatch):
    """Bricht das Nachschreiben ab, bleibt die Datei liegen; der zweite
    Versuch schreibt nichts doppelt."""
    spool = tmp_path / "spool.jsonl"
    buf = MeasurementBuffer(spool_path=spool)
    real_write = measurements._write
    calls = {"n": 0}

    async def fail_first(rows):
        calls["n"] += 1
        raise ConnectionError("weg")

    monkeypatch.setattr(measurements, "_write", fail_first)
    fill(buf, 0, 20)
    await buf.flush()
    monkeypatch.setattr(measurements, "_write", real_write)
    # Ein Teil der Zeilen ist schon in der Datenbank (abgebrochener Versuch)
    half = MeasurementBuffer(spool_path=None)
    fill(half, 0, 8)
    await half.flush()
    assert await buf.flush() is True
    assert await count(Measurement) == 20 and not spool.exists()


async def test_truncated_spool_line_is_skipped(tmp_path):
    spool = tmp_path / "spool.jsonl"
    good = json.dumps({"t": T0.isoformat(), "s": "site", "f": "pv_power", "v": 5.0})
    spool.write_text(good + "\n" + '{"t": "2026-05-01T12:00:10+00:00", "s": "si')  # Absturz mitten in der Zeile
    buf = MeasurementBuffer(spool_path=spool)
    assert await buf.flush() is True
    assert await count(Measurement) == 1 and not spool.exists()


async def test_without_spool_rows_stay_in_memory_bounded(monkeypatch):
    buf = MeasurementBuffer(spool_path=None)

    async def down(rows):
        raise ConnectionError("weg")

    monkeypatch.setattr(measurements, "_write", down)
    monkeypatch.setattr(measurements, "BACKLOG_MAX_ROWS", 15)
    fill(buf, 0, 10)
    await buf.flush()
    fill(buf, 10, 10)
    await buf.flush()
    status = buf.status()
    assert status["backlog_rows"] == 15 and status["dropped"] == 5


async def test_full_spool_drops_and_counts(tmp_path, monkeypatch):
    spool = tmp_path / "spool.jsonl"
    buf = MeasurementBuffer(spool_path=spool)

    async def down(rows):
        raise ConnectionError("weg")

    monkeypatch.setattr(measurements, "_write", down)
    monkeypatch.setattr(measurements, "SPOOL_MAX_BYTES", 200)
    fill(buf, 0, 5)
    await buf.flush()
    fill(buf, 5, 5)
    await buf.flush()
    assert buf.status()["dropped"] == 5 and buf.status()["backlog_rows"] == 0


async def test_flush_soon_does_not_block_the_caller(tmp_path, monkeypatch):
    import asyncio

    buf = MeasurementBuffer(spool_path=tmp_path / "s.jsonl")

    async def slow(rows):
        await asyncio.sleep(0.3)

    monkeypatch.setattr(measurements, "_write", slow)
    fill(buf, 0, 3)
    started = asyncio.get_running_loop().time()
    buf.flush_soon()
    buf.flush_soon()  # zweiter Anstoß während des ersten: kein zweiter Task
    assert asyncio.get_running_loop().time() - started < 0.05
    await buf.close()


# ---------------------------------------------------------------- Ereignisse

async def test_events_are_kept_during_outage_and_written_later(monkeypatch):
    import app.services.audit as audit_module

    real = audit_module.async_session

    def broken():
        raise ConnectionError("weg")

    monkeypatch.setattr(audit_module, "async_session", broken)
    await audit.log_event("Erstes Ereignis im Ausfall", level="warning")
    await audit.log_event("Zweites Ereignis im Ausfall")
    assert audit.pending_events() == 2 and await count(Event) == 0
    monkeypatch.setattr(audit_module, "async_session", real)
    await audit.log_event("Datenbank wieder da")
    assert audit.pending_events() == 0
    async with async_session() as db:
        messages = (await db.scalars(select(Event.message).order_by(Event.time))).all()
    assert messages == ["Erstes Ereignis im Ausfall", "Zweites Ereignis im Ausfall", "Datenbank wieder da"]


# ---------------------------------------------------------------- Sicherung

async def seed_plant() -> int:
    async with async_session() as db:
        dev = Device(name="Wallbox", category="wallbox", driver_id="sim_wallbox",
                     config={"max_current": 16}, settings={"mode": "pv_only"})
        db.add_all([dev, Setting(key="priority", value={"order": []}),
                    User(email="a@example.com", name="A", password_hash="hash", role="admin")])
        await db.flush()
        db.add(ChargeSession(device_id=dev.id, started_at=T0, ended_at=T0 + timedelta(hours=2),
                             energy_kwh=11.5, solar_kwh=9.0, cost_eur=0.8))
        db.add(Event(time=T0, level="info", category="control", message="Ladevorgang gestartet: Wallbox"))
        await db.commit()
        device_id = dev.id
    buf = MeasurementBuffer(spool_path=None)
    for i in range(360):  # eine Stunde im 10-s-Takt
        when = T0 + timedelta(seconds=10 * i)
        buf.add("site", "pv_power", 4000.0, when)
        buf.add(f"device:{device_id}", "wallbox_power", 3600.0, when)
    await buf.flush()
    return device_id


async def wipe() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def test_data_dump_roundtrip_after_database_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    old_id = await seed_plant()
    await backup.run_once()
    dump = await backup.run_data_dump()
    assert list(tmp_path.glob(".tmp-*")) == []
    with gzip.open(dump, "rt") as f:
        kinds = [json.loads(line)["k"] for line in f]
    assert kinds[0] == "header" and kinds.count("s") == 1 and kinds.count("e") == 1 and kinds.count("m") == 2

    # Datenbank verloren; ein anderes Gerät belegt inzwischen die alte ID
    await wipe()
    async with async_session() as db:
        db.add(Device(name="Platzhalter", category="meter", driver_id="sim_meter", config={}, settings={}))
        await db.commit()
        await db.execute(text("DELETE FROM devices"))
        await db.commit()
    assert await startup.maybe_restore_from_backup() is True
    async with async_session() as db:
        dev = (await db.scalars(select(Device))).one()
        session = (await db.scalars(select(ChargeSession))).one()
        assert session.device_id == dev.id and session.energy_kwh == 11.5
        rows = (await db.scalars(select(Measurement).order_by(Measurement.source))).all()
        assert {(r.source, r.field, r.value) for r in rows} == {
            (f"device:{dev.id}", "wallbox_power", 3600.0), ("site", "pv_power", 4000.0)}
        assert await db.scalar(select(func.count(Event.id))) == 1
    # Noch einmal einspielen: nichts doppelt
    counts = await backup.restore_data(dump)
    assert counts["sessions"] == 0 and await count(ChargeSession) == 1 and await count(Measurement) == 2
    assert old_id  # nur zur Dokumentation: die ID darf sich ändern


async def test_truncated_data_dump_restores_what_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await seed_plant()
    dump = await backup.run_data_dump()
    raw = dump.read_bytes()
    dump.write_bytes(raw[: len(raw) - 12])  # Datei nach Stromausfall unvollständig
    async with async_session() as db:
        await db.execute(text("DELETE FROM measurements"))
        await db.execute(text("DELETE FROM charge_sessions"))
        await db.commit()
    counts = await backup.restore_data(dump)
    assert counts["sessions"] == 1  # der Anfang der Datei ist lesbar


async def test_rotation_keeps_data_and_config_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "backup_keep", 2)
    monkeypatch.setattr(get_settings(), "backup_data_keep", 1)
    await seed_plant()
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (tmp_path / f"minepower-{stamp}.json").write_text("{}")
        (tmp_path / f"minepower-data-{stamp}.jsonl.gz").write_bytes(b"")
    await backup.run_once()
    await backup.run_data_dump()
    assert len(backup._config_files(tmp_path)) == 2 and len(backup._data_files(tmp_path)) == 1
    assert backup.status()["count"] == 2 and backup.status()["data_count"] == 1


@pytest.mark.parametrize("name", ["../secret.key", "secret.key", "minepower-20260101-000000.json/../x",
                                  "/etc/passwd", "minepower-data-1.jsonl.gz", ".tmp-abc.json"])
def test_backup_names_from_the_api_cannot_leave_the_folder(name, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    (tmp_path / "secret.key").write_text("x")
    with pytest.raises(FileNotFoundError):
        backup.resolve(name)


async def test_restore_api_brings_devices_back_and_keeps_accounts(tmp_path, monkeypatch):
    from app.security import create_token, hash_password

    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    async with async_session() as db:
        admin = User(email="admin@example.com", name="Admin", password_hash=hash_password("geheim-1234"), role="admin")
        dev = Device(name="Heizstab", category="water_heater", driver_id="sim_water_heater",
                     config={"rated_power": 3000}, settings={"mode": "pv_only", "max_power_w": 3000})
        db.add_all([admin, dev, Setting(key="priority", value={"order": []})])
        await db.commit()
        await db.refresh(admin)
        token = create_token(admin)
    status, _, created = await request("POST", "/api/system/backups", token=token)
    assert status == 200
    # Danach wird das Gerät verstellt und ein zweites angelegt
    async with async_session() as db:
        row = (await db.scalars(select(Device))).one()
        row.settings = {"mode": "off", "max_power_w": 500}
        db.add(Device(name="Zusatz", category="meter", driver_id="sim_meter", config={}, settings={}))
        await db.commit()
    status, _, listing = await request("GET", "/api/system/backups", token=token)
    assert status == 200 and {b["kind"] for b in listing["backups"]} == {"config", "data"}
    assert listing["storage"]["volumes"]
    status, _, result = await request("POST", f"/api/system/backups/{created['config']}/restore", token=token)
    assert status == 200 and result["devices"] == 1, result
    async with async_session() as db:
        devices = (await db.scalars(select(Device))).all()
        assert [d.name for d in devices] == ["Heizstab"] and devices[0].settings["max_power_w"] == 3000
        assert await db.scalar(select(func.count(User.id))) == 1
    assert (await request("POST", "/api/system/backups/..%2Fsecret.key/restore", token=token))[0] == 404
    assert (await request("GET", f"/api/system/backups/{created['config']}/download", token=token))[0] == 422
    status, headers, _ = await request("GET", f"/api/system/backups/{created['data']}/download", token=token)
    assert status == 200 and headers["content-type"] == "application/gzip"


async def test_storage_warning_when_space_runs_low(tmp_path, monkeypatch):
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: usage(100 * 1024 ** 3, 99 * 1024 ** 3, 1024 ** 3 // 2))
    status = await backup.storage_status()
    assert status["warning"] and "0.5 GB" in status["warning"]
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: usage(100 * 1024 ** 3, 50 * 1024 ** 3, 50 * 1024 ** 3))
    assert (await backup.storage_status())["warning"] is None


# ---------------------------------------------------------------- Schema

async def test_schema_migrates_an_older_database_and_backs_it_up_first(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    # Datenbank im Stand vor 3.3.0: ohne die neuen Spalten, ohne Alembic-Stand
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE users DROP COLUMN prefs"))
        await conn.execute(text("ALTER TABLE users DROP COLUMN password_changed_at"))
        await conn.execute(text(
            "INSERT INTO users (email, name, password_hash, role, language, disabled, created_at) "
            "VALUES ('alt@example.com', 'Alt', 'hash', 'admin', 'de', 0, '2026-01-01 00:00:00')"))
    await startup.init_db()
    async with engine.connect() as conn:
        columns = {r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).all()}
        revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert {"prefs", "password_changed_at"} <= columns
    assert revision == "0005"
    saved = list(tmp_path.glob("pre-migration-*.json"))
    assert len(saved) == 1
    dump = json.loads(saved[0].read_text())
    assert dump["tables"]["users"][0]["email"] == "alt@example.com" and dump["to_revision"] == "0005"
    # Das alte Konto ist mit dem neuen Schema lesbar
    async with async_session() as db:
        user = (await db.scalars(select(User))).one()
        assert user.prefs is None and user.password_changed_at is None

    # Zweiter Start: nichts zu tun, keine weitere Sicherung
    await startup.init_db()
    assert len(list(tmp_path.glob("pre-migration-*.json"))) == 1


async def test_fresh_database_gets_schema_without_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await startup.init_db()
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "0005"
    assert list(tmp_path.glob("pre-migration-*")) == []
    assert await count(User) == 0


async def test_reload_failure_keeps_request_and_old_config(monkeypatch):
    """Datenbank kurz weg, während neu geladen werden soll: Der Regelkreis
    läuft mit dem bisherigen Stand weiter und lädt beim nächsten Takt."""
    import asyncio

    from app.core.loop import ControlLoop

    loop = ControlLoop(default_interval_s=0.5)
    steps = {"n": 0}
    calls = {"n": 0}

    async def failing_reload():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("Datenbank weg")
        loop._reload_requested = False

    async def step():
        steps["n"] += 1
        loop._wake.set()  # sofort der nächste Takt
        if steps["n"] >= 4:
            loop.running = False

    monkeypatch.setattr(loop, "_reload", failing_reload)
    monkeypatch.setattr(loop, "_step", step)
    monkeypatch.setattr("app.core.loop.MIN_STEP_GAP_S", 0.0)
    loop._reload_requested = True
    loop.running = True
    await asyncio.wait_for(loop._run(), timeout=5)
    assert calls["n"] == 3 and steps["n"] == 4  # jeder Takt lief, auch ohne neue Konfiguration


async def test_replay_resumes_where_it_stopped(tmp_path, monkeypatch):
    """Großer Rückstand: Bricht das Nachschreiben mittendrin ab, beginnt der
    nächste Versuch beim gemerkten Stand statt wieder von vorn."""
    monkeypatch.setattr(measurements, "CHUNK_ROWS", 10)
    spool = tmp_path / "spool.jsonl"
    buf = MeasurementBuffer(spool_path=spool)
    real_write = measurements._write

    async def down(rows):
        raise ConnectionError("weg")

    monkeypatch.setattr(measurements, "_write", down)
    fill(buf, 0, 30)
    assert await buf.flush() is False

    seen: list[int] = []

    async def one_chunk_then_fail(rows):
        if seen:
            raise TimeoutError("Datenbank wieder hängend")
        seen.append(len(rows))
        await real_write(rows)

    monkeypatch.setattr(measurements, "_write", one_chunk_then_fail)
    assert await buf.flush() is False
    assert await count(Measurement) == 10

    written: list[int] = []

    async def record(rows):
        written.append(len(rows))
        await real_write(rows)

    monkeypatch.setattr(measurements, "_write", record)
    assert await buf.flush() is True
    assert sum(written) == 20, "die ersten 10 Zeilen wurden erneut gelesen"
    assert await count(Measurement) == 30
    assert not spool.exists() and not (tmp_path / "spool.jsonl.pos").exists()


async def test_value_after_torn_line_is_not_lost(tmp_path, monkeypatch):
    """Absturz mitten in einer Zeile, danach weiterer Ausfall: Der nächste Wert
    klebte an dem Bruchstück und ging mit ihm verloren."""
    spool = tmp_path / "spool.jsonl"
    spool.write_text('{"t": "2026-05-01T12:00:10+00:00", "s": "si')
    buf = MeasurementBuffer(spool_path=spool)
    real_write = measurements._write

    async def down(rows):
        raise ConnectionError("weg")

    monkeypatch.setattr(measurements, "_write", down)
    fill(buf, 0, 1)
    assert await buf.flush() is False
    monkeypatch.setattr(measurements, "_write", real_write)
    assert await buf.flush() is True
    assert await count(Measurement) == 1
