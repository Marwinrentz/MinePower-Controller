"""Tests für die automatische lokale Sicherung (services/backup.py).

Der Zweck dieses Moduls ist, eine durch Stromausfall geleerte Datenbank
automatisch wiederherzustellen – ein Fehler in dieser Rückspur wäre ein
Fehler in genau dem Sicherheitsnetz, das den ursprünglichen Bug beheben
soll. Deshalb ein echter Rundlauf statt gemockter Bausteine: Geräte,
Einstellungen, Konten und ein Fahrzeug anlegen, sichern, die Datenbank
leeren (das simuliert eine neu initialisierte Postgres-Datendatei),
zurückspielen, vergleichen.
"""
from sqlalchemy import delete, select

from app import startup
from app.config import get_settings
from app.db import Base, async_session, engine
from app.models import Device, Setting, User, Vehicle
from app.services import backup


async def _clear_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed() -> int:
    async with async_session() as session:
        dev = Device(name="Wallbox", category="wallbox", driver_id="sim_wallbox",
                     config={"max_current": 16}, settings={"mode": "pv_only"})
        session.add(dev)
        session.add(Setting(key="priority", value={"order": []}))
        session.add(User(email="a@b.de", name="Admin", password_hash="hash", role="admin"))
        await session.flush()
        session.add(Vehicle(name="Auto", wallbox_device_id=dev.id))
        await session.commit()
        return dev.id


async def _empty_all():
    async with async_session() as session:
        await session.execute(delete(Vehicle))
        await session.execute(delete(Device))
        await session.execute(delete(Setting))
        await session.execute(delete(User))
        await session.commit()


async def test_run_once_writes_atomic_json(tmp_path, monkeypatch):
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await _seed()

    path = await backup.run_once()

    assert path.exists()
    assert path.parent == tmp_path
    # keine Tmp-Leiche liegen geblieben (siehe _write_atomic)
    assert list(tmp_path.glob(".tmp-*")) == []


def test_prune_keeps_only_last_n(tmp_path):
    for i in range(5):
        (tmp_path / f"minepower-2024010{i}-000000.json").write_text("{}")

    backup._prune(tmp_path, keep=2)

    left = sorted(tmp_path.glob("minepower-*.json"))
    assert [p.name for p in left] == ["minepower-20240103-000000.json", "minepower-20240104-000000.json"]


def test_prune_keep_zero_disables_pruning(tmp_path):
    for i in range(3):
        (tmp_path / f"minepower-2024010{i}-000000.json").write_text("{}")

    backup._prune(tmp_path, keep=0)

    assert len(list(tmp_path.glob("minepower-*.json"))) == 3


async def test_restore_round_trip(tmp_path, monkeypatch):
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await _seed()
    path = await backup.run_once()

    # Simuliert eine neu initialisierte Postgres-Datendatei nach einem
    # Stromausfall: alles weg, aber die DB selbst funktioniert wieder.
    await _empty_all()

    count = await backup.restore(path)
    assert count == 1

    async with async_session() as session:
        devices = (await session.scalars(select(Device))).all()
        users = (await session.scalars(select(User))).all()
        vehicles = (await session.scalars(select(Vehicle))).all()
        settings = (await session.scalars(select(Setting))).all()

    assert [d.name for d in devices] == ["Wallbox"]
    assert [u.email for u in users] == ["a@b.de"]
    # Passwort-Hash wandert 1:1 mit – kein Re-Hash, sonst könnte sich
    # niemand nach einer automatischen Wiederherstellung mehr anmelden.
    assert users[0].password_hash == "hash"
    assert len(vehicles) == 1
    # Fremdschlüssel korrekt auf die NEU vergebene Geräte-ID nachgezogen,
    # nicht auf die alte (die nach dem Neuanlegen nicht mehr existiert).
    assert vehicles[0].wallbox_device_id == devices[0].id
    assert {s.key for s in settings} == {"priority"}


async def test_maybe_restore_from_backup_leaves_populated_db_alone(tmp_path, monkeypatch):
    """Darf eine Anlage mit vorhandenen Geräten nie anfassen – sonst würde
    jeder Neustart frische Änderungen mit einer alten Sicherung überschreiben."""
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await _seed()
    await backup.run_once()

    restored = await startup.maybe_restore_from_backup()

    assert restored is False


async def test_maybe_restore_from_backup_restores_when_empty(tmp_path, monkeypatch):
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await _seed()
    await backup.run_once()
    await _empty_all()

    restored = await startup.maybe_restore_from_backup()
    assert restored is True

    async with async_session() as session:
        devices = (await session.scalars(select(Device))).all()
    assert [d.name for d in devices] == ["Wallbox"]


async def test_maybe_restore_from_backup_noop_without_backup(tmp_path, monkeypatch):
    """Frische Erstinstallation: leere DB, aber noch keine Sicherung
    vorhanden – darf nichts tun, damit der normale Setup-Assistent greift."""
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))

    restored = await startup.maybe_restore_from_backup()
    assert restored is False


def test_status_reports_no_backup_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    assert backup.status() == {"count": 0, "latest": None, "dir": str(tmp_path)}


async def test_status_reports_latest_after_backup(tmp_path, monkeypatch):
    await _clear_schema()
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    await _seed()
    await backup.run_once()

    info = backup.status()
    assert info["count"] == 1
    assert info["latest"] is not None
