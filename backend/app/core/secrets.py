"""Zugangsdaten verschlüsselt speichern, maskiert ausliefern.

Betroffen sind Gerätefelder vom Typ PASSWORD (aus den Treiber-Metadaten) und
die Token-Felder in Tarif- und Benachrichtigungseinstellungen.

* **Speichern:** Fernet (AES-128-CBC + HMAC-SHA256), Präfix ``enc:v1:``.
* **Schlüssel:** ``MINEPOWER_SECRET_KEY`` (beliebige Zeichenkette, wird per
  SHA-256 abgeleitet) oder, falls nicht gesetzt, eine beim ersten Start
  erzeugte Schlüsseldatei ``secret.key`` im Datenverzeichnis. Der Schlüssel
  liegt damit nie in der Datenbank – ein DB-Dump enthält keine lesbaren
  Zugangsdaten.
* **Ausliefern:** Die API gibt gesetzte Werte nur als ``•••`` zurück. Schickt
  die Oberfläche ``•••`` zurück, bleibt der gespeicherte Wert erhalten.
* **Altbestand:** Werte ohne Präfix gelten als Klartext und werden beim Start
  einmal verschlüsselt (``migrate``).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"
MASK = "•••"

#: Geheime Felder in Einstellungsabschnitten
SETTINGS_SECRETS: dict[str, set[str]] = {
    "tariff": {"tibber_token"},
    "notifications": {"telegram_token", "ntfy_url", "smtp_password", "email_password"},
}


def _data_dir() -> Path:
    from ..config import get_settings

    cfg = get_settings()
    for candidate in (cfg.data_dir, cfg.backup_dir):
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("x")
            probe.unlink()
            return path
        except OSError:
            continue
    raise RuntimeError("Kein beschreibbares Datenverzeichnis für den Schlüssel")


@lru_cache
def _fernet() -> Fernet:
    env = os.environ.get("MINEPOWER_SECRET_KEY", "").strip()
    if env:
        key = base64.urlsafe_b64encode(hashlib.sha256(env.encode()).digest())
        return Fernet(key)
    path = _data_dir() / "secret.key"
    if path.exists():
        return Fernet(path.read_bytes().strip())
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    log.info("Schlüssel für Zugangsdaten erzeugt: %s", path)
    return Fernet(key)


def reset_cache() -> None:
    """Für Tests: Schlüssel neu laden."""
    _fernet.cache_clear()


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: Any) -> Any:
    if value in (None, "") or not isinstance(value, str) or is_encrypted(value) or value == MASK:
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: Any) -> Any:
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        log.error("Zugangsdaten nicht lesbar: Schlüssel passt nicht (MINEPOWER_SECRET_KEY bzw. secret.key geändert?)")
        return ""


# ---------------------------------------------------------------- Geräte

def device_secret_keys(driver_id: str) -> set[str]:
    from ..drivers import registry
    from ..drivers.base import FieldType

    try:
        meta = registry.get_driver_class(driver_id).meta
    except KeyError:
        return set()
    return {f.key for f in meta.fields if f.type == FieldType.PASSWORD}


def _protect(keys: set[str], new: dict, old: dict | None) -> dict:
    out = dict(new or {})
    old = old or {}
    for k in keys:
        if k not in out:
            if k in old:
                out[k] = old[k]
            continue
        value = out[k]
        if value == MASK:
            out[k] = old.get(k, "")
        else:
            out[k] = encrypt(value)
    return out


def _reveal(keys: set[str], data: dict) -> dict:
    out = dict(data or {})
    for k in keys:
        if k in out:
            out[k] = decrypt(out[k])
    return out


def _mask(keys: set[str], data: dict) -> dict:
    out = dict(data or {})
    for k in keys:
        if k in out:
            out[k] = MASK if out[k] not in (None, "") else ""
    return out


def protect_config(driver_id: str, new: dict, old: dict | None = None) -> dict:
    """Vor dem Speichern: neue Werte verschlüsseln, ``•••`` = alten Wert behalten."""
    return _protect(device_secret_keys(driver_id), new, old)


def reveal_config(driver_id: str, config: dict) -> dict:
    """Für den Treiber: entschlüsselt."""
    return _reveal(device_secret_keys(driver_id), config)


def mask_config(driver_id: str, config: dict) -> dict:
    """Für die API: gesetzte Geheimnisse als ``•••``."""
    return _mask(device_secret_keys(driver_id), config)


def strip_config(driver_id: str, config: dict) -> dict:
    """Für Exporte: Geheimnisse leeren."""
    out = dict(config or {})
    for k in device_secret_keys(driver_id):
        if k in out:
            out[k] = ""
    return out


# ---------------------------------------------------------------- Einstellungen

def protect_settings(section: str, new: dict, old: dict | None = None) -> dict:
    return _protect(SETTINGS_SECRETS.get(section, set()), new, old)


def reveal_settings(section: str, value: dict) -> dict:
    return _reveal(SETTINGS_SECRETS.get(section, set()), value)


def mask_settings(section: str, value: dict) -> dict:
    return _mask(SETTINGS_SECRETS.get(section, set()), value)


def strip_settings(section: str, value: dict) -> dict:
    out = dict(value or {})
    for k in SETTINGS_SECRETS.get(section, set()):
        if k in out:
            out[k] = ""
    return out


async def migrate() -> int:
    """Klartext-Altbestand einmal verschlüsseln. Idempotent."""
    from sqlalchemy import select

    from ..db import async_session
    from ..models import Device, Setting

    count = 0
    async with async_session() as session:
        for dev in (await session.scalars(select(Device))).all():
            keys = device_secret_keys(dev.driver_id)
            config = dict(dev.config or {})
            changed = False
            for k in keys:
                if config.get(k) not in (None, "") and not is_encrypted(config[k]):
                    config[k] = encrypt(config[k])
                    changed = True
            if changed:
                dev.config = config
                count += 1
        for section, keys in SETTINGS_SECRETS.items():
            row = await session.get(Setting, section)
            if row is None or not isinstance(row.value, dict):
                continue
            value = dict(row.value)
            changed = False
            for k in keys:
                if value.get(k) not in (None, "") and not is_encrypted(value[k]):
                    value[k] = encrypt(value[k])
                    changed = True
            if changed:
                row.value = value
                count += 1
        await session.commit()
    if count:
        log.info("Zugangsdaten verschlüsselt: %d Einträge", count)
    return count
