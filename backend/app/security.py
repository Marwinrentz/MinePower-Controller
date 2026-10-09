"""Auth: bcrypt-Passwort-Hashing + JWT, FastAPI-Dependencies für Rollen."""
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


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    cfg = get_settings()
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=cfg.jwt_expire_minutes),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict:
    cfg = get_settings()
    return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])


async def _load_user(token: str, db: AsyncSession) -> User:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiges oder abgelaufenes Token")
    user = await db.scalar(select(User).where(User.id == int(payload["sub"])))
    if user is None or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer nicht gefunden oder deaktiviert")
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


async def authenticate_websocket(ws: WebSocket, db: AsyncSession) -> User | None:
    """WebSocket-Auth über ?token=… Query-Parameter."""
    token = ws.query_params.get("token")
    if not token:
        return None
    try:
        return await _load_user(token, db)
    except HTTPException:
        return None


class LoginRateLimiter:
    """Einfaches In-Memory-Rate-Limiting für den Login-Endpunkt
    (max. `limit` Fehlversuche pro `window_s` je Schlüssel/IP)."""

    def __init__(self, limit: int = 8, window_s: int = 300) -> None:
        self.limit = limit
        self.window_s = window_s
        self._attempts: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        import time

        now = time.monotonic()
        attempts = [t for t in self._attempts.get(key, []) if now - t < self.window_s]
        self._attempts[key] = attempts
        return len(attempts) < self.limit

    def record_failure(self, key: str) -> None:
        import time

        self._attempts.setdefault(key, []).append(time.monotonic())


login_limiter = LoginRateLimiter()
