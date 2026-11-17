"""Auth-Endpunkte: Login (rate-limitiert), Erst-Setup, Benutzerverwaltung."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import (
    LoginRequest,
    PasswordChange,
    PrefsUpdate,
    SetupRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ..security import (
    LOGIN_IP_LIMIT,
    create_token,
    get_current_user,
    hash_password,
    login_limiter,
    password_changed_now,
    require_role,
    verify_password,
    verify_password_or_dummy,
)
from ..core.netutil import client_ip
from ..services.audit import log_event
from ..services.ws import ws_manager

router = APIRouter(prefix="/api/auth", tags=["auth"])


#: Erst-Setup läuft einzeln: Zwei gleichzeitige Aufrufe legten sonst beide
#: einen Admin an (Prüfen und Anlegen sind zwei Schritte).
_setup_lock = asyncio.Lock()


def _client_ip(request: Request) -> str:
    return client_ip(request)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _client_ip(request)
    email = body.email.strip().lower()
    account_key = f"{ip}:{email}"
    ip_key = f"ip:{ip}"
    if not login_limiter.check(account_key) or not login_limiter.check(ip_key, LOGIN_IP_LIMIT):
        blocked = account_key if not login_limiter.check(account_key) else ip_key
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche, Anmeldung vorübergehend gesperrt",
            headers={"Retry-After": str(login_limiter.retry_after(blocked))},
        )
    user = await db.scalar(select(User).where(User.email == email))
    # Immer rechnen, auch ohne Konto: gleiche Antwortzeit (siehe security.py).
    # Ausgelagert, damit bcrypt den Regelkreis nicht anhält.
    ok = await asyncio.to_thread(verify_password_or_dummy, body.password,
                                 user.password_hash if user is not None else None)
    if user is None or user.disabled or not ok:
        login_limiter.record_failure(account_key)
        login_limiter.record_failure(ip_key)
        await log_event(f"Anmeldung fehlgeschlagen: {email[:80]}", level="warning", category="auth",
                        data={"ip": ip})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-Mail oder Passwort falsch")
    login_limiter.reset(account_key)
    await log_event(f"Login: {user.email}", category="auth", user_id=user.id)
    return TokenResponse(token=create_token(user), user=UserOut.model_validate(user))


@router.post("/setup", response_model=TokenResponse)
async def initial_setup(body: SetupRequest, db: AsyncSession = Depends(get_db)):
    """Erststart: ersten Admin anlegen. Nur möglich, solange keine Benutzer existieren."""
    async with _setup_lock:
        count = await db.scalar(select(func.count(User.id)))
        if count:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Setup bereits abgeschlossen")
        user = User(
            email=body.email.lower(),
            name=body.name,
            password_hash=hash_password(body.password),
            role="admin",
            language=body.language,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    await log_event(f"Erst-Setup: Admin {user.email} angelegt", category="auth", user_id=user.id)
    return TokenResponse(token=create_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/me/seen", response_model=UserOut)
async def mark_version_seen(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Hinweisfenster nach einem Update geschlossen: laufende Version als gesehen speichern."""
    from .. import __version__

    row = await db.get(User, user.id)
    row.last_seen_version = __version__
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/me/password", response_model=TokenResponse)
async def change_own_password(body: PasswordChange, request: Request, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    """Eigenes Passwort ändern. Alle bisherigen Anmeldungen dieses Kontos
    enden; die Antwort enthält ein neues Token für dieses Gerät."""
    key = f"pw:{user.id}"
    if not login_limiter.check(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Zu viele Fehlversuche, bitte später erneut",
                            headers={"Retry-After": str(login_limiter.retry_after(key))})
    row = await db.get(User, user.id)
    if not await asyncio.to_thread(verify_password, body.current_password, row.password_hash):
        login_limiter.record_failure(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bisheriges Passwort falsch")
    row.password_hash = await asyncio.to_thread(hash_password, body.new_password)
    row.password_changed_at = password_changed_now()
    await db.commit()
    await db.refresh(row)
    await log_event(f"Passwort geändert: {row.email}", category="auth", user_id=row.id)
    await ws_manager.close_user(row.id)
    return TokenResponse(token=create_token(row), user=UserOut.model_validate(row))


@router.patch("/me/prefs", response_model=UserOut)
async def update_prefs(body: PrefsUpdate, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Persönliche Einstellungen der Oberfläche speichern (je Konto)."""
    row = await db.get(User, user.id)
    prefs = dict(row.prefs or {})
    prefs.update(body.model_dump(exclude_none=True))
    row.prefs = prefs
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/refresh", response_model=TokenResponse)
async def refresh(user: User = Depends(get_current_user)):
    """Gleitende Verlängerung: gültiges Token gegen ein frisches tauschen.

    Ohne das läuft ein Token irgendwann mitten im Betrieb ab, und die
    Oberfläche wirft den Nutzer auf den Anmeldeschirm – oft genau dann, wenn
    er etwas schalten will. Die Oberfläche ruft das hier beim Start und danach
    regelmäßig auf, solange sie offen ist.

    Bewusst kein zweites, langlebiges Refresh-Token: Das müsste zusätzlich
    gespeichert und widerrufbar gehalten werden, ohne hier etwas zu gewinnen –
    wer das alte Token besitzt, ist bereits angemeldet.
    """
    return TokenResponse(token=create_token(user), user=UserOut.model_validate(user))


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    return (await db.scalars(select(User).order_by(User.id))).all()


@router.post("/users", response_model=UserOut)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("admin"))):
    if await db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits vergeben")
    user = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
        rfid_tag=body.rfid_tag,
        language=body.language,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await log_event(f"Benutzer angelegt: {user.email} ({user.role})", category="auth", user_id=admin.id)
    return user


async def _other_active_admins(db: AsyncSession, user_id: int) -> int:
    return await db.scalar(
        select(func.count(User.id)).where(User.role == "admin", User.disabled.is_(False), User.id != user_id)
    ) or 0


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserUpdate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("admin"))):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    # Der letzte aktive Admin bleibt Admin: Sonst kann niemand mehr Geräte,
    # Einstellungen oder Konten ändern, und es bleibt nur die Datenbank.
    loses_admin = user.role == "admin" and not user.disabled and (
        (body.role is not None and body.role != "admin") or body.disabled is True
    )
    if loses_admin and not await _other_active_admins(db, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Der letzte Administrator kann nicht herabgestuft oder gesperrt werden")
    changes: list[str] = []
    for field in ("name", "role", "rfid_tag", "language", "disabled"):
        value = getattr(body, field)
        if value is not None and value != getattr(user, field):
            setattr(user, field, value)
            changes.append(field)
    if body.password:
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        # Bisherige Anmeldungen dieses Kontos enden.
        user.password_changed_at = password_changed_now()
        changes.append("password")
    await db.commit()
    await db.refresh(user)
    if "password" in changes or ("disabled" in changes and user.disabled) or "role" in changes:
        await ws_manager.close_user(user.id)
    if changes:
        await log_event(f"Benutzer geändert: {user.email} ({', '.join(changes)})", category="auth", user_id=admin.id)
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("admin"))):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenen Account nicht löschbar")
    user = await db.get(User, user_id)
    if user:
        email = user.email
        await db.delete(user)
        await db.commit()
        await ws_manager.close_user(user_id)
        await log_event(f"Benutzer gelöscht: {email}", category="auth", user_id=admin.id)
