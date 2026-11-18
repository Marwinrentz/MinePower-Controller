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
import time
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
    "notifications": {"telegram_token", "ntfy_url", "webhook_url", "smtp_password", "email_password"},
}


def _key_dirs() -> list[Path]:
    """Beschreibbare Ablageorte für die Schlüsseldatei.

    Datenverzeichnis und Sicherungsordner. Ältere Compose-Dateien binden nur
    den Sicherungsordner als Volume ein; lag der Schlüssel allein im
    Datenverzeichnis, war er nach einem Update weg und alle gespeicherten
    Zugangsdaten unlesbar (3.1.0: Tibber-Preise fehlten). Deshalb liegt er
    in beiden Ordnern und wird aus dem gefunden, der ihn noch hat."""
    from ..config import get_settings

    cfg = get_settings()
    out: list[Path] = []
    for candidate in (cfg.data_dir, cfg.backup_dir):
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("x")
            probe.unlink()
            if path not in out:
                out.append(path)
        except OSError:
            continue
    if not out:
        raise RuntimeError("Kein beschreibbares Datenverzeichnis für den Schlüssel")
    return out


def _data_dir() -> Path:
    return _key_dirs()[0]


def _write_key(path: Path, key: bytes) -> None:
    """Atomar schreiben: temporäre Datei, fsync, umbenennen, Ordner fsync.

    Ein Stromausfall direkt nach dem ersten Start hinterließ sonst eine leere
    Datei – und die gewann beim nächsten Start vor der intakten Kopie im
    anderen Ordner (Start brach ab, Docker startete endlos neu)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except OSError as exc:
        log.warning("Schlüsseldatei %s nicht schreibbar: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _valid_key(path: Path) -> bytes | None:
    """Inhalt der Schlüsseldatei, wenn er ein gültiger Fernet-Schlüssel ist."""
    try:
        key = path.read_bytes().strip()
        Fernet(key)
        return key
    except (OSError, ValueError, TypeError):
        return None


@lru_cache
def _fernet() -> Fernet:
    env = os.environ.get("MINEPOWER_SECRET_KEY", "").strip()
    if env:
        key = base64.urlsafe_b64encode(hashlib.sha256(env.encode()).digest())
        return Fernet(key)
    files = [d / "secret.key" for d in _key_dirs()]
    # Die erste GÜLTIGE Datei gewinnt, nicht die erste vorhandene.
    key = next((k for k in (_valid_key(f) for f in files) if k is not None), None)
    broken = [f for f in files if f.exists() and _valid_key(f) != key]
    if key is None:
        if broken:
            log.error(
                "Schlüsseldatei beschädigt (%s) – neuer Schlüssel erzeugt. Gespeicherte "
                "Zugangsdaten sind nicht mehr lesbar und müssen neu eingegeben werden.",
                ", ".join(str(f) for f in broken),
            )
        else:
            log.info("Schlüssel für Zugangsdaten erzeugt")
        key = Fernet.generate_key()
    for f in broken:
        # Beschädigte Kopie zur Analyse aufheben, dann ersetzen.
        log.warning("Schlüsseldatei %s war beschädigt und wird ersetzt", f)
        try:
            os.replace(f, f.with_name(f"secret.key.broken-{int(time.time())}"))
        except OSError:
            pass
    for f in files:
        if _valid_key(f) != key:
            _write_key(f, key)
    return Fernet(key)


def readable(value: Any) -> bool:
    """Lässt sich ein gespeicherter Wert mit dem aktuellen Schlüssel lesen?"""
    if not is_encrypted(value):
        return True
    try:
        _fernet().decrypt(value[len(PREFIX):].encode())
        return True
    except InvalidToken:
        return False


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
            # Unlesbar (Schlüssel verloren) gilt als nicht gesetzt: Die Oberfläche
            # fragt dann nach dem Wert, statt '•••' als gültig anzuzeigen.
            out[k] = MASK if out[k] not in (None, "") and readable(out[k]) else ""
    return out


def protect_config(driver_id: str, new: dict, old: dict | None = None) -> dict:
    """Vor dem Speichern: neue Werte verschlüsseln, ``•••`` = alten Wert behalten."""
    return _protect(device_secret_keys(driver_id), new, old)


#: Felder, die bestimmen, WOHIN ein Treiber seine Zugangsdaten schickt.
_TARGET_KEYS = {"host", "port", "scheme", "region", "verify_ssl", "modbus_port",
                "proxy_port", "broker", "server", "ip"}


def _is_target_key(key: str) -> bool:
    return key in _TARGET_KEYS or key.endswith(("url", "_host"))


def secret_reused_for_new_target(driver_id: str, new: dict, old: dict | None) -> bool:
    """Würde ein gespeichertes Geheimnis (••• oder weggelassen) an eine
    GEÄNDERTE Adresse gehen?

    Sonst konnte ein Admin den Host auf einen eigenen Rechner umstellen, •••
    stehen lassen und auf "Verbindung testen" drücken – der Server schickte
    ihm das gespeicherte Gerätepasswort, obwohl die API es nie ausliefert."""
    old = old or {}
    new = new or {}
    keys = device_secret_keys(driver_id)
    kept = [k for k in keys if old.get(k) and (k not in new or new.get(k) == MASK)]
    if not kept:
        return False
    for k in set(new) | set(old):
        if k in keys or not _is_target_key(k):
            continue
        if k in new and str(new.get(k) if new.get(k) is not None else "").strip() != \
                str(old.get(k) if old.get(k) is not None else "").strip():
            return True
    return False


SECRET_TARGET_CHANGED = (
    "Die Adresse wurde geändert – bitte das Passwort bzw. den Schlüssel neu eingeben. "
    "Gespeicherte Zugangsdaten werden nicht an eine neue Adresse geschickt."
)


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
    lost: list[str] = []
    async with async_session() as session:
        for dev in (await session.scalars(select(Device))).all():
            keys = device_secret_keys(dev.driver_id)
            config = dict(dev.config or {})
            lost += [f"{dev.name}: {k}" for k in keys if not readable(config.get(k))]
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
            lost += [f"{section}: {k}" for k in keys if not readable(value.get(k))]
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
    if lost:
        from ..services.audit import log_event

        await log_event(
            "Zugangsdaten nicht lesbar (Schlüssel geändert): " + ", ".join(lost) + " – neu eintragen",
            level="warning", category="config",
        )
    return count
