"""'Verbinden'-Knopf für Cloud-Geräte (OAuth2 Authorization Code, seit 3.5).

Statt ein Token mit einem Skript zu erzeugen, klickt der Nutzer im
Geräteformular auf 'Mit Viessmann/Daikin verbinden':

1. ``POST /api/oauth/start`` – MinePower baut die Anmeldeadresse des
   Herstellers (mit ``state`` und bei Viessmann PKCE) und merkt sich den
   Vorgang 15 Minuten im Speicher. Das Client-Secret verlässt den Server
   dabei nicht; bei einem gespeicherten Gerät darf die Oberfläche ``•••``
   schicken, dann gilt das gespeicherte.
2. Der Browser öffnet die Adresse, der Nutzer meldet sich an. Der Hersteller
   leitet auf die Redirect-URI um. Zeigt die auf MinePower selbst
   (``/oauth/callback``), schickt die Seite den Code an das Formular;
   sonst kopiert der Nutzer die Adresse aus der Adresszeile.
3. ``POST /api/oauth/finish`` tauscht den Code gegen ein Refresh-Token und
   gibt es dem Formular zurück, das es wie jedes Passwortfeld verschlüsselt
   speichert.

Nur Administratoren. Fremde Ziele sind ausgeschlossen: Token- und
Anmeldeadressen stehen fest im Code, die Redirect-URI wird nur
weitergereicht, nie aufgerufen.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.secrets import MASK, reveal_config
from ..db import get_db
from ..drivers.http_util import HttpDevice
from ..drivers.validation import DriverError
from ..models import Device, User
from ..security import require_role

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

PENDING_TTL_S = 900.0


@dataclass(frozen=True)
class Provider:
    name: str
    driver_id: str
    authorize: str
    token: str
    scope: str
    pkce: bool
    needs_secret: bool
    https_redirect: bool


PROVIDERS: dict[str, Provider] = {
    "viessmann": Provider(
        name="Viessmann", driver_id="viessmann_vicare",
        authorize="https://iam.viessmann.com/idp/v3/authorize", token="https://iam.viessmann.com/idp/v3/token",
        scope="IoT User offline_access", pkce=True, needs_secret=False, https_redirect=False),
    "daikin_onecta": Provider(
        name="Daikin", driver_id="daikin_onecta",
        authorize="https://idp.onecta.daikineurope.com/v1/oidc/authorize",
        token="https://idp.onecta.daikineurope.com/v1/oidc/token",
        scope="openid onecta:basic.integration", pkce=False, needs_secret=True, https_redirect=True),
}


@dataclass
class Pending:
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    verifier: str | None
    user_id: int
    created: float = field(default_factory=time.monotonic)


_pending: dict[str, Pending] = {}


def _prune() -> None:
    now = time.monotonic()
    for key in [k for k, p in _pending.items() if now - p.created > PENDING_TTL_S]:
        _pending.pop(key, None)


def _check_redirect(provider: Provider, uri: str) -> str:
    uri = (uri or "").strip()
    parts = urllib.parse.urlsplit(uri)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "Redirect-URI muss eine vollständige Adresse sein (http:// oder https://)")
    if provider.https_redirect:
        if parts.scheme != "https":
            raise HTTPException(422, f"{provider.name} verlangt eine https-Adresse als Redirect-URI")
        if (parts.hostname or "") in ("localhost", "127.0.0.1", "::1"):
            raise HTTPException(422, f"{provider.name} lehnt localhost als Redirect-URI ab – die IP-Adresse "
                                     "oder den Namen des Servers verwenden")
    return uri


class StartRequest(BaseModel):
    provider: str
    client_id: str
    client_secret: str = ""
    redirect_uri: str
    device_id: int | None = None


class StartResponse(BaseModel):
    auth_url: str
    state: str


class FinishRequest(BaseModel):
    state: str | None = None
    #: Code oder die komplette Adresse nach der Anmeldung (…?code=…&state=…)
    code_or_url: str


class FinishResponse(BaseModel):
    field: str
    refresh_token: str
    message: str


@router.post("/start", response_model=StartResponse)
async def start(body: StartRequest, db: AsyncSession = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    provider = PROVIDERS.get(body.provider)
    if provider is None:
        raise HTTPException(422, f"Unbekannter Anbieter: {body.provider}")
    client_id = body.client_id.strip()
    if not client_id:
        raise HTTPException(422, "Zuerst die Client-ID eintragen")
    secret = body.client_secret.strip()
    if secret == MASK and body.device_id is not None:
        stored = await db.get(Device, body.device_id)
        if stored is None or stored.driver_id != provider.driver_id:
            raise HTTPException(404, "Gerät nicht gefunden")
        secret = str(reveal_config(stored.driver_id, stored.config or {}).get("client_secret") or "")
    if provider.needs_secret and (not secret or secret == MASK):
        raise HTTPException(422, "Zuerst das Client-Secret eintragen")
    redirect = _check_redirect(provider, body.redirect_uri)
    _prune()
    state = secrets.token_urlsafe(24)
    params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
              "scope": provider.scope, "state": state}
    verifier = None
    if provider.pkce:
        verifier = secrets.token_urlsafe(64)[:96]
        params["code_challenge"] = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params["code_challenge_method"] = "S256"
    _pending[state] = Pending(provider=body.provider, client_id=client_id, client_secret=secret,
                              redirect_uri=redirect, verifier=verifier, user_id=user.id)
    return StartResponse(auth_url=provider.authorize + "?" + urllib.parse.urlencode(params), state=state)


def _parse(code_or_url: str) -> tuple[str, str | None, str | None]:
    """(code, state, error) aus Code oder kompletter Rückkehr-Adresse."""
    raw = (code_or_url or "").strip()
    if "code=" in raw or "error=" in raw:
        query = urllib.parse.urlsplit(raw).query or raw.split("?", 1)[-1]
        q = urllib.parse.parse_qs(query)
        return (q.get("code", [""])[0], q.get("state", [None])[0],
                q.get("error_description", q.get("error", [None]))[0])
    return raw, None, None


async def _exchange(provider: Provider, p: Pending, code: str) -> dict:
    data = {"grant_type": "authorization_code", "client_id": p.client_id, "redirect_uri": p.redirect_uri,
            "code": code}
    if p.verifier:
        data["code_verifier"] = p.verifier
    if p.client_secret:
        data["client_secret"] = p.client_secret
    http = HttpDevice(provider.token, name=provider.name, timeout=20.0)
    try:
        res = await http.request("POST", provider.token, data=data)
    finally:
        await http.close()
    return res if isinstance(res, dict) else {}


@router.post("/finish", response_model=FinishResponse)
async def finish(body: FinishRequest, user: User = Depends(require_role("admin"))):
    code, url_state, error = _parse(body.code_or_url)
    if error:
        raise HTTPException(422, f"Anmeldung abgebrochen: {error}")
    state = url_state or body.state
    if not state:
        raise HTTPException(422, "Vorgang unbekannt – bitte noch einmal auf 'Verbinden' klicken")
    if body.state and url_state and body.state != url_state:
        raise HTTPException(422, "Die Adresse gehört zu einem anderen Verbindungsversuch – neu verbinden")
    _prune()
    p = _pending.get(state)
    if p is None or p.user_id != user.id:
        raise HTTPException(422, "Verbindungsversuch abgelaufen (15 min) – bitte neu verbinden")
    if not code:
        raise HTTPException(422, "In der Adresse fehlt der Code – die komplette Adresse aus der Adresszeile "
                                 "einfügen")
    provider = PROVIDERS[p.provider]
    try:
        res = await _exchange(provider, p, code)
    except DriverError as exc:
        raise HTTPException(502, f"{provider.name} hat die Anmeldung abgelehnt ({exc}). Redirect-URI im "
                                 "Portal und hier müssen exakt gleich sein; der Code gilt nur wenige Minuten.")
    _pending.pop(state, None)
    token = res.get("refresh_token")
    if not token:
        raise HTTPException(502, f"{provider.name} hat kein Refresh-Token geliefert")
    return FinishResponse(field="refresh_token", refresh_token=str(token),
                          message=f"Mit {provider.name} verbunden – jetzt 'Verbindung testen' und speichern")
