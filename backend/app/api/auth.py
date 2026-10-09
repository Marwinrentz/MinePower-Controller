"""Auth-Endpunkte: Login (rate-limitiert), Erst-Setup, Benutzerverwaltung."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import (
    LoginRequest,
    SetupRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ..security import (
    create_token,
    get_current_user,
    hash_password,
    login_limiter,
    require_role,
    verify_password,
)
from ..services.audit import log_event

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    key = f"{request.client.host if request.client else '?'}:{body.email}"
    if not login_limiter.check(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Zu viele Fehlversuche, Anmeldung vorübergehend gesperrt")
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or user.disabled or not verify_password(body.password, user.password_hash):
        login_limiter.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-Mail oder Passwort falsch")
    await log_event(f"Login: {user.email}", category="auth", user_id=user.id)
    return TokenResponse(token=create_token(user), user=UserOut.model_validate(user))


@router.post("/setup", response_model=TokenResponse)
async def initial_setup(body: SetupRequest, db: AsyncSession = Depends(get_db)):
    """Erststart: ersten Admin anlegen. Nur möglich, solange keine Benutzer existieren."""
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
    if body.role not in ("admin", "user", "readonly"):
        raise HTTPException(422, "Ungültige Rolle")
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


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserUpdate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("admin"))):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    for field in ("name", "role", "rfid_tag", "language", "disabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(user, field, value)
    if body.password:
        user.password_hash = hash_password(body.password)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("admin"))):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenen Account nicht löschbar")
    user = await db.get(User, user_id)
    if user:
        await db.delete(user)
        await db.commit()
