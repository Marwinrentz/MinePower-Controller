"""Rotierende OAuth-Refresh-Tokens dauerhaft merken (seit 3.5).

Manche Cloud-APIs (Daikin Onecta) geben bei jeder Erneuerung ein **neues**
Refresh-Token aus und machen das alte ungültig. In der Gerätekonfiguration
steht aber nur das Token, das der Nutzer einmal eingetragen hat. Ohne
Speicher wäre es nach dem ersten Erneuern verbraucht, und nach jedem
Neustart käme die Anmeldung nicht mehr durch.

Darum liegt das jeweils aktuelle Token verschlüsselt (wie alle
Zugangsdaten, siehe ``app.core.secrets``) im Datenverzeichnis unter
``oauth/<schlüssel>.json``. Der Schlüssel ist ein Hash aus Anbieter,
Client-ID und dem eingetragenen Token: Trägt der Nutzer ein neues Token
ein, beginnt die Kette von vorn; das alte Token steht nirgends im Klartext.

Fehler beim Schreiben (schreibgeschütztes Volume) brechen nichts ab: Die
Anmeldung läuft weiter, nur ein Neustart braucht dann ein neues Token.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _dir() -> Path:
    from ..config import get_settings

    return Path(get_settings().data_dir) / "oauth"


def store_key(provider: str, client_id: str, initial_token: str) -> str:
    raw = f"{provider}\n{client_id}\n{initial_token}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def load(key: str) -> str | None:
    """Zuletzt gespeichertes Refresh-Token oder None."""
    path = _dir() / f"{key}.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("OAuth-Speicher %s nicht lesbar: %s", path.name, exc)
        return None
    from ..core.secrets import decrypt

    token = decrypt(data.get("refresh_token"))
    return token or None


def save(key: str, refresh_token: str) -> bool:
    """Neues Refresh-Token sichern (atomar). True bei Erfolg."""
    from ..core.secrets import encrypt

    directory = _dir()
    path = directory / f"{key}.json"
    tmp = directory / f".{key}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"refresh_token": encrypt(refresh_token)}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 – Speichern ist ein Komfort, kein Muss
        log.warning("OAuth-Token konnte nicht gespeichert werden (%s) – nach einem Neustart "
                    "muss es neu erzeugt werden.", exc)
        return False


def forget(key: str) -> None:
    try:
        (_dir() / f"{key}.json").unlink()
    except OSError:
        pass
