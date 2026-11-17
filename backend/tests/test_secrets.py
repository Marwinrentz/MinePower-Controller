"""Zugangsdaten: verschlüsselt gespeichert, nie im Klartext ausgeliefert."""
from pathlib import Path

import pytest

from app.api.devices import create_device, list_devices, update_device
from app.core import secrets
from app.db import async_session
from app.models import Device
from app.schemas import DeviceCreate, DeviceUpdate


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


class _Admin:
    id = None
    role = "admin"


def test_roundtrip_and_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MINEPOWER_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()
    secrets.reset_cache()
    try:
        token = secrets.encrypt("geheim")
        assert token.startswith("enc:v1:") and "geheim" not in token
        assert secrets.decrypt(token) == "geheim"
        key = Path(tmp_path) / "secret.key"
        assert key.exists() and oct(key.stat().st_mode)[-3:] == "600"
        assert secrets.decrypt("klartext") == "klartext"          # Altbestand
        assert secrets.encrypt(secrets.MASK) == secrets.MASK
    finally:
        get_settings.cache_clear()
        secrets.reset_cache()


async def test_device_secrets_encrypted_and_masked():
    body = DeviceCreate(name="Fahrzeug", category="wallbox", driver_id="tesla_vehicle",
                        config={"backend": "fleet", "vin": "XP7TEST0000000001", "access_token": "tok-123",
                                "region": "eu"})
    async with async_session() as db:
        out = await create_device(body, db=db, user=_Admin())
    assert out.config["access_token"] == "•••"
    async with async_session() as db:
        row = await db.get(Device, out.id)
        assert row.config["access_token"].startswith("enc:v1:")
        assert secrets.reveal_config("tesla_vehicle", row.config)["access_token"] == "tok-123"
        listed = [d for d in await list_devices(db=db, _=_Admin()) if d.id == out.id][0]
        assert listed.config["access_token"] == "•••"
        # '•••" zurückschicken behält den Wert, ein neuer Wert ersetzt ihn
        await update_device(out.id, DeviceUpdate(config={**listed.config}), db=db, user=_Admin())
        row = await db.get(Device, out.id)
        assert secrets.reveal_config("tesla_vehicle", row.config)["access_token"] == "tok-123"
        await update_device(out.id, DeviceUpdate(config={**listed.config, "access_token": "neu"}), db=db, user=_Admin())
        row = await db.get(Device, out.id)
        await db.refresh(row)
        assert secrets.reveal_config("tesla_vehicle", row.config)["access_token"] == "neu"


async def test_migrate_encrypts_plaintext_once():
    async with async_session() as db:
        db.add(Device(name="Alt", category="wallbox", driver_id="tesla_vehicle",
                      config={"backend": "fleet", "access_token": "klar"}, settings={}))
        await db.commit()
    assert await secrets.migrate() >= 1
    async with async_session() as db:
        rows = [d for d in (await db.execute(Device.__table__.select())).all() if d.name == "Alt"]
        assert rows[0].config["access_token"].startswith("enc:v1:")
    assert await secrets.migrate() == 0



def test_key_kept_in_backup_dir_survives_lost_data_dir(tmp_path, monkeypatch):
    """Update ohne Volume für /app/data: Schlüssel kommt aus dem Sicherungsordner."""
    data, backup = tmp_path / "data", tmp_path / "backup"
    monkeypatch.delenv("MINEPOWER_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("BACKUP_DIR", str(backup))
    from app.config import get_settings
    get_settings.cache_clear()
    secrets.reset_cache()
    try:
        token = secrets.encrypt("tibber-geheim")
        assert (data / "secret.key").exists() and (backup / "secret.key").exists()
        (data / "secret.key").unlink()  # Container neu erstellt, Datenverzeichnis leer
        secrets.reset_cache()
        assert secrets.decrypt(token) == "tibber-geheim"
        assert (data / "secret.key").exists()  # wieder angelegt
    finally:
        get_settings.cache_clear()
        secrets.reset_cache()


def test_unreadable_secret_is_shown_as_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEPOWER_SECRET_KEY", "alt")
    secrets.reset_cache()
    token = secrets.encrypt("x")
    monkeypatch.setenv("MINEPOWER_SECRET_KEY", "neu")
    secrets.reset_cache()
    try:
        assert not secrets.readable(token)
        assert secrets.mask_settings("tariff", {"tibber_token": token})["tibber_token"] == ""
    finally:
        monkeypatch.delenv("MINEPOWER_SECRET_KEY")
        secrets.reset_cache()


def _key_env(tmp_path, monkeypatch):
    data, backup = tmp_path / "data", tmp_path / "backup"
    monkeypatch.delenv("MINEPOWER_SECRET_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("BACKUP_DIR", str(backup))
    from app.config import get_settings
    get_settings.cache_clear()
    secrets.reset_cache()
    return data, backup


def test_empty_key_file_falls_back_to_intact_copy(tmp_path, monkeypatch):
    """Stromausfall nach dem ersten Start: leere Datei im Datenordner darf die
    intakte Kopie im Sicherungsordner nicht verdrängen (vorher: Start-Abbruch)."""
    data, backup = _key_env(tmp_path, monkeypatch)
    from app.config import get_settings
    try:
        token = secrets.encrypt("geheim")
        (data / "secret.key").write_bytes(b"")
        secrets.reset_cache()
        assert secrets.decrypt(token) == "geheim"
        # repariert
        assert (data / "secret.key").read_bytes().strip() == (backup / "secret.key").read_bytes().strip()
        assert list(data.glob("secret.key.broken-*"))
    finally:
        get_settings.cache_clear()
        secrets.reset_cache()


def test_all_key_files_broken_still_starts(tmp_path, monkeypatch):
    data, backup = _key_env(tmp_path, monkeypatch)
    from app.config import get_settings
    try:
        data.mkdir(parents=True)
        backup.mkdir(parents=True)
        (data / "secret.key").write_bytes(b"kaputt")
        (backup / "secret.key").write_bytes(b"")
        token = secrets.encrypt("neu")  # kein ValueError, kein Crash-Loop
        secrets.reset_cache()
        assert secrets.decrypt(token) == "neu"
    finally:
        get_settings.cache_clear()
        secrets.reset_cache()


def test_key_file_written_atomically(tmp_path, monkeypatch):
    data, backup = _key_env(tmp_path, monkeypatch)
    from app.config import get_settings
    try:
        secrets.encrypt("x")
        assert not list(data.glob("*.tmp")) and not list(backup.glob("*.tmp"))
        assert ((data / "secret.key").stat().st_mode & 0o777) == 0o600
    finally:
        get_settings.cache_clear()
        secrets.reset_cache()
