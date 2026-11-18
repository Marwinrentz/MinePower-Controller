"""Auth: bcrypt-Passwort-Hashing + JWT, FastAPI-Dependencies für Rollen."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)

#: Erlaubte Rollen eines Kontos
ROLES = ("admin", "user", "readonly")
#: bcrypt wertet nur die ersten 72 Byte aus. Längere Eingaben werden hier
#: ausdrücklich gekürzt (bcrypt 5 lehnt sie sonst ab); die Obergrenze der
#: Eingabe steht in den Schemas.
_BCRYPT_MAX_BYTES = 72
MAX_PASSWORD_LENGTH = 256


def _password_bytes(password: str) -> bytes:
    return password.encode()[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode())
    except ValueError:
        return False


#: Vergleichswert für unbekannte Konten: Die Anmeldung rechnet dann genauso
#: lange wie bei einem falschen Passwort. Sonst verrät die Antwortzeit, welche
#: E-Mail-Adressen ein Konto haben.
_DUMMY_HASH = bcrypt.hashpw(b"minepower-dummy", bcrypt.gensalt()).decode()


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Passwort prüfen; ohne Konto gegen einen Platzhalter (immer falsch)."""
    if password_hash is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, password_hash)


def create_token(user: User) -> str:
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.jwt_expire_minutes),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict:
    cfg = get_settings()
    return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm],
                      options={"require": ["exp", "sub"]})


def _issued_at(payload: dict) -> float:
    """Ausstellzeit eines Tokens. Tokens vor 3.3.0 tragen sie nicht; dort
    ergibt sie sich aus Ablaufzeit minus Gültigkeitsdauer."""
    iat = payload.get("iat")
    if isinstance(iat, (int, float)):
        return float(iat)
    return float(payload["exp"]) - get_settings().jwt_expire_minutes * 60


def password_changed_now() -> datetime:
    """Zeitstempel für einen Passwortwechsel, auf ganze Sekunden abgerundet
    (Tokens tragen ihre Ausstellzeit nur sekundengenau)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def token_revoked(payload: dict, user: User) -> bool:
    """Tokens von vor dem letzten Passwortwechsel gelten nicht mehr."""
    changed = getattr(user, "password_changed_at", None)
    if changed is None:
        return False
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    return _issued_at(payload) < changed.timestamp()


async def _load_user(token: str, db: AsyncSession) -> User:
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges oder abgelaufenes Token")
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer nicht gefunden oder deaktiviert")
    if token_revoked(payload, user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges oder abgelaufenes Token")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return await _load_user(credentials.credentials, db)


def require_role(*roles: str):
    """Dependency-Factory: erlaubt Zugriff nur für die angegebenen Rollen.
    'admin' hat implizit alle Rechte."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role != "admin" and user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Keine Berechtigung")
        return user

    return checker


def websocket_token(ws: WebSocket) -> str | None:
    """Token aus dem Handshake: Kopfzeile ``Authorization: Bearer …`` (Apps)
    oder, für ältere Clients, ``?token=…``.

    Browser können beim WebSocket-Handshake keine Kopfzeilen setzen. Die
    Weboberfläche schickt das Token deshalb als erste Nachricht (siehe
    api/status.py), nicht mehr in der Adresse – dort stand es in jedem
    Zugriffsprotokoll."""
    header = ws.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return ws.query_params.get("token") or None


async def user_for_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        return await _load_user(token, db)
    except HTTPException:
        return None


async def authenticate_websocket(ws: WebSocket, db: AsyncSession) -> User | None:
    """WebSocket-Auth über den Handshake (Kopfzeile oder ?token=)."""
    return await user_for_token(websocket_token(ws), db)


class LoginRateLimiter:
    """In-Memory-Bremse für Anmeldeversuche.

    Gezählt werden Fehlversuche je Schlüssel in einem gleitenden Fenster.
    Die Anmeldung nutzt zwei Schlüssel: ``IP:E-Mail`` (wenige Versuche gegen
    ein Konto) und ``IP`` allein (viele Konten von einer Adresse aus). Der
    Speicher ist begrenzt: Abgelaufene Einträge werden entfernt, und über
    ``max_keys`` hinaus fallen die ältesten heraus."""

    def __init__(self, limit: int = 8, window_s: int = 300, max_keys: int = 5000, clock=time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._clock = clock
        self._attempts: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = self._clock()
        attempts = [t for t in self._attempts.get(key, ()) if now - t < self.window_s]
        if attempts:
            self._attempts[key] = attempts
        else:
            self._attempts.pop(key, None)
        return attempts

    def check(self, key: str, limit: int | None = None) -> bool:
        return len(self._recent(key)) < (self.limit if limit is None else limit)

    def retry_after(self, key: str) -> int:
        """Sekunden, bis wieder ein Versuch frei ist."""
        attempts = self._recent(key)
        if not attempts:
            return 0
        return max(1, int(self.window_s - (self._clock() - attempts[0])) + 1)

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._attempts.setdefault(key, []).append(self._clock())
        if len(self._attempts) > self.max_keys:
            self._prune()

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def _prune(self) -> None:
        for key in list(self._attempts):
            self._recent(key)
        overflow = len(self._attempts) - self.max_keys
        if overflow > 0:
            oldest = sorted(self._attempts, key=lambda k: self._attempts[k][-1])[:overflow]
            for key in oldest:
                del self._attempts[key]


login_limiter = LoginRateLimiter()
#: Höchstzahl Fehlversuche je Adresse über alle Konten im selben Fenster
LOGIN_IP_LIMIT = 30
