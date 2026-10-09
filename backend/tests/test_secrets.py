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

