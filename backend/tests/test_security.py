"""Sicherheit: Anmeldung, Token-Widerruf, Rollen, Kopfzeilen, Protokoll,
Eingabegrenzen. Anfragen laufen in-process gegen die echte App."""
import logging
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import security
from app.config import get_settings
from app.core import logsafe
from app.db import async_session
from app.drivers.netguard import blocked_reason
from app.models import Device, User
from app.security import LoginRateLimiter, create_token, hash_password, login_limiter, verify_password

from .asgi import request

TESLA = "tesla_vehicle"


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    login_limiter._attempts.clear()
    yield


async def make_user(email="admin@example.com", role="admin", password="geheim-1234", **extra) -> tuple[User, str]:
    async with async_session() as db:
        user = User(email=email, name="Test", password_hash=hash_password(password), role=role, **extra)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user, create_token(user)


# ---------------------------------------------------------------- Protokoll

@pytest.mark.parametrize("text, secret", [
    ('"WebSocket /api/ws?token=eyJhbGciOi.abc.def" [accepted]', "eyJhbGciOi.abc.def"),
    ("GET /mcp/k/mp_geheim123 HTTP/1.1", "mp_geheim123"),
    ("Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
    ("https://api.telegram.org/bot123456:AAH-geheim/sendMessage", "123456:AAH-geheim"),
    ("mqtt://nutzer:passwort@broker.lan:1883", "passwort"),
    ("http://192.0.2.5/api?apikey=s3cr3t&x=1", "s3cr3t"),
])
def test_redact_removes_credentials(text, secret):
    out = logsafe.redact(text)
    assert secret not in out and "***" in out


def test_log_filter_redacts_args_and_exceptions():
    record = logging.LogRecord("x", logging.WARNING, "x", 1, "Fehler bei %s: %s",
                               ("http://h/?token=abc123", RuntimeError("Bearer abcdefgh12345678")), None)
    logsafe.RedactFilter().filter(record)
    msg = record.getMessage()
    assert "abc123" not in msg and "abcdefgh12345678" not in msg


def test_redact_keeps_normal_text():
    text = "Gerät 192.0.2.10:502 antwortet nicht (Timeout 8 s)"
    assert logsafe.redact(text) == text


# ---------------------------------------------------------------- Passwörter und Bremse

def test_password_longer_than_bcrypt_limit():
    long = "ä" * 200  # 400 Byte
    hashed = hash_password(long)
    assert verify_password(long, hashed)
    assert not verify_password("falsch", hashed)


def test_rate_limiter_blocks_and_recovers():
    now = [0.0]
    limiter = LoginRateLimiter(limit=3, window_s=60, clock=lambda: now[0])
    for _ in range(3):
        assert limiter.check("k")
        limiter.record_failure("k")
    assert not limiter.check("k")
    assert 0 < limiter.retry_after("k") <= 61
    now[0] = 61.0
    assert limiter.check("k")
    assert "k" not in limiter._attempts  # abgelaufene Einträge verschwinden


def test_rate_limiter_memory_is_bounded():
    limiter = LoginRateLimiter(limit=3, window_s=600, max_keys=50)
    for i in range(500):
        limiter.record_failure(f"ip-{i}")
    assert len(limiter._attempts) <= 50


async def test_login_limits_per_account_and_per_address():
    await make_user()
    for _ in range(8):
        status, _, _ = await request("POST", "/api/auth/login", body={"email": "admin@example.com", "password": "x"})
        assert status == 401
    status, headers, _ = await request("POST", "/api/auth/login",
                                       body={"email": "admin@example.com", "password": "geheim-1234"})
    assert status == 429 and int(headers["retry-after"]) > 0
    # Andere Adresse ist nicht betroffen
    status, _, data = await request("POST", "/api/auth/login", client="192.0.2.9",
                                    body={"email": "admin@example.com", "password": "geheim-1234"})
    assert status == 200 and data["token"]
    # Viele Konten von einer Adresse: Bremse je Adresse
    login_limiter._attempts.clear()
    codes = []
    for i in range(security.LOGIN_IP_LIMIT + 2):
        status, _, _ = await request("POST", "/api/auth/login", client="192.0.2.50",
                                     body={"email": f"u{i}@example.com", "password": "x"})
        codes.append(status)
    assert codes[-1] == 429 and codes[0] == 401


async def test_login_rejects_oversized_password():
    status, _, _ = await request("POST", "/api/auth/login",
                                 body={"email": "a@example.com", "password": "x" * 5000})
    assert status == 422


# ---------------------------------------------------------------- Tokens

async def test_password_change_revokes_older_tokens():
    user, old = await make_user(password="geheim-1234")
    # Token "von gestern" – auch im alten Format ohne Ausstellzeit
    cfg = get_settings()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    legacy = jwt.encode({"sub": str(user.id), "role": "admin",
                         "exp": yesterday + timedelta(minutes=cfg.jwt_expire_minutes)},
                        cfg.jwt_secret, algorithm=cfg.jwt_algorithm)
    assert (await request("GET", "/api/auth/me", token=legacy))[0] == 200

    status, _, data = await request("POST", "/api/auth/me/password", token=old,
                                    body={"current_password": "geheim-1234", "new_password": "neu-und-lang-9"})
    assert status == 200
    fresh = data["token"]
    assert (await request("GET", "/api/auth/me", token=fresh))[0] == 200
    assert (await request("GET", "/api/auth/me", token=legacy))[0] == 401
    # Anmeldung mit dem neuen Passwort, nicht mehr mit dem alten
    assert (await request("POST", "/api/auth/login", client="192.0.2.2",
                          body={"email": user.email, "password": "geheim-1234"}))[0] == 401
    assert (await request("POST", "/api/auth/login", client="192.0.2.3",
                          body={"email": user.email, "password": "neu-und-lang-9"}))[0] == 200


async def test_password_change_needs_current_password():
    _, token = await make_user()
    status, _, _ = await request("POST", "/api/auth/me/password", token=token,
                                 body={"current_password": "falsch", "new_password": "neu-und-lang-9"})
    assert status == 403


async def test_token_without_subject_or_expiry_is_rejected():
    cfg = get_settings()
    for payload in ({"sub": "1"}, {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                    {"sub": "abc", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}):
        token = jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)
        assert (await request("GET", "/api/auth/me", token=token))[0] == 401
    forged = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "anderes-geheimnis", algorithm="HS256")
    assert (await request("GET", "/api/auth/me", token=forged))[0] == 401


async def test_disabled_user_token_stops_working():
    admin, admin_token = await make_user()
    user, token = await make_user("u@example.com", role="user")
    assert (await request("GET", "/api/auth/me", token=token))[0] == 200
    assert (await request("PATCH", f"/api/auth/users/{user.id}", token=admin_token, body={"disabled": True}))[0] == 200
    assert (await request("GET", "/api/auth/me", token=token))[0] == 401


# ---------------------------------------------------------------- Rollen

async def test_roles_are_validated_and_last_admin_is_kept():
    admin, token = await make_user()
    status, _, _ = await request("PATCH", f"/api/auth/users/{admin.id}", token=token, body={"role": "superuser"})
    assert status == 422
    for body in ({"role": "user"}, {"disabled": True}):
        status, _, data = await request("PATCH", f"/api/auth/users/{admin.id}", token=token, body=body)
        assert status == 409, data
    # Mit einem zweiten Admin geht es
    second, _ = await make_user("b@example.com")
    status, _, _ = await request("PATCH", f"/api/auth/users/{second.id}", token=token, body={"role": "user"})
    assert status == 200


async def test_readonly_and_user_cannot_change_configuration():
    await make_user()
    _, user_token = await make_user("u@example.com", role="user")
    _, ro_token = await make_user("r@example.com", role="readonly")
    for token in (user_token, ro_token):
        assert (await request("PUT", "/api/settings", token=token, body={"values": {}}))[0] == 403
        assert (await request("GET", "/api/auth/users", token=token))[0] == 403
        assert (await request("GET", "/api/system/export", token=token))[0] == 403
        assert (await request("POST", "/api/devices", token=token,
                              body={"name": "x", "category": "meter", "driver_id": "sim_meter"}))[0] == 403
    assert (await request("POST", "/api/status/pause", token=ro_token))[0] == 403


async def test_every_api_route_needs_login():
    """Kein Endpunkt außer den bewusst offenen antwortet ohne Anmeldung."""
    from app.main import app

    public = {"/api/system/health", "/api/system/ready", "/api/system/setup-state", "/api/auth/login",
              "/api/auth/setup"}
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/") or path in public:
            continue
        for method in operations:
            status, _, _ = await request(method.upper(), path.replace("{", "1").replace("}", ""), body={})
            assert status in (401, 403), f"{method} {path} antwortet ohne Anmeldung mit {status}"
            checked += 1
    assert checked > 40


async def test_setup_only_once():
    body = {"email": "a@example.com", "password": "geheim-1234", "name": "A"}
    assert (await request("POST", "/api/auth/setup", body=body))[0] == 200
    assert (await request("POST", "/api/auth/setup", body={**body, "email": "b@example.com"}))[0] == 403


# ---------------------------------------------------------------- Kopfzeilen

async def test_security_headers_on_api_and_pages():
    status, headers, _ = await request("GET", "/api/system/health")
    assert status == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "unsafe-eval" not in csp
    assert "access-control-allow-origin" not in headers


def test_csp_takes_only_a_clean_host():
    from app.main import content_security_policy

    assert "ws://anlage.lan:8080" in content_security_policy("anlage.lan:8080")
    assert "ws://[fd00::1]:8080" in content_security_policy("[fd00::1]:8080")
    for evil in ("evil.example; script-src *", "a b", "x'y", "h\r\nSet-Cookie: a=b"):
        assert "ws://" not in content_security_policy(evil)


async def test_api_docs_are_off_by_default():
    from app.main import app

    assert app.docs_url is None and app.openapi_url is None


# ---------------------------------------------------------------- Eingaben

@pytest.mark.parametrize("address, blocked", [
    ("169.254.169.254", True), ("http://169.254.169.254/latest/meta-data/", True),
    ("[fd00:ec2::254]:80", True), ("metadata.google.internal", True), ("0.0.0.0", True),
    ("::ffff:169.254.169.254", True), ("224.0.0.1", True),
    ("192.168.1.20", False), ("10.0.0.5:502", False), ("wallbox.lan", False), ("127.0.0.1", False),
    ("169.254.10.20", False), ("http://192.0.2.7/api/status", False),
])
def test_netguard(address, blocked):
    assert (blocked_reason(address) is not None) == blocked


async def test_device_settings_are_checked_on_create():
    _, token = await make_user()
    base = {"name": "Box", "category": "wallbox", "driver_id": "sim_wallbox", "config": {}}
    for settings in ({"max_current": 500}, {"min_current": 20, "max_current": 10}, {"mode": "vollgas"},
                     {"phases_mode": "zwei"}, {"max_current": "viel"}):
        status, _, data = await request("POST", "/api/devices", token=token, body={**base, "settings": settings})
        assert status == 422, (settings, data)
    status, _, _ = await request("POST", "/api/devices", token=token,
                                 body={**base, "settings": {"mode": "pv_only", "max_current": 16}})
    assert status == 200


def test_regulator_clamps_stored_settings():
    from app.core.regulation import WallboxSettings, WaterHeaterSettings

    w = WallboxSettings.from_dict({"min_current": "abc", "max_current": 5000, "target_soc": float("nan"),
                                   "phases_mode": "x", "start_delay_s": -5})
    assert w.min_current == 6.0 and w.max_current == 63.0 and w.target_soc == 80.0
    assert w.phases_mode == "fixed1" and w.start_delay_s == 0.0
    w = WallboxSettings.from_dict({"min_current": 16, "max_current": 8})
    assert w.max_current >= w.min_current
    h = WaterHeaterSettings.from_dict({"max_power_w": 10**9, "target_temp_c": 150, "boost_temp_c": "heiss"})
    assert h.max_power_w == 50000.0 and h.target_temp_c == 85.0 and 30.0 <= h.boost_temp_c <= 80.0


async def test_metadata_address_is_rejected_as_device_host():
    _, token = await make_user()
    status, _, data = await request("POST", "/api/devices", token=token, body={
        "name": "X", "category": "meter", "driver_id": "shelly_3em", "config": {"host": "169.254.169.254"}})
    assert status == 422, data


async def test_import_keeps_devices_secrets_and_rejects_bad_files():
    from app.core.secrets import decrypt, protect_config

    _, token = await make_user()
    async with async_session() as db:
        dev = Device(name="Auto", category="wallbox", driver_id=TESLA,
                     config=protect_config(TESLA, {"vin": "XP7TEST0000000001", "access_token": "geheim"}),
                     settings={"mode": "pv_only"})
        db.add(dev)
        await db.commit()
        await db.refresh(dev)
        device_id = dev.id
    status, _, exported = await request("GET", "/api/system/export", token=token)
    assert status == 200 and "geheim" not in str(exported)
    exported["devices"][0]["name"] = "Auto neu"
    status, _, data = await request("POST", "/api/system/import", token=token, body=exported)
    assert status == 200, data
    async with async_session() as db:
        row = await db.get(Device, device_id)
        # Dieselbe Zeile (Ladevorgänge hängen an der ID), Zugangsdaten erhalten
        assert row is not None and row.name == "Auto neu"
        secret_key = next(k for k, v in row.config.items() if str(v).startswith("enc:"))
        assert decrypt(row.config[secret_key]) == "geheim"
    # Zweiter Import legt nichts doppelt an
    await request("POST", "/api/system/import", token=token, body=exported)
    async with async_session() as db:
        from sqlalchemy import func, select
        assert await db.scalar(select(func.count(Device.id))) == 1
    for bad in ({**exported, "devices": [{"name": "x", "category": "wallbox", "driver_id": "gibt-es-nicht"}]},
                {**exported, "devices": [{**exported["devices"][0], "settings": {"max_current": 900}}]},
                {**exported, "devices": "kaputt"}):
        status, _, _ = await request("POST", "/api/system/import", token=token, body=bad)
        assert status == 422
    async with async_session() as db:
        from sqlalchemy import func, select
        assert await db.scalar(select(func.count(Device.id))) == 1  # nichts gelöscht


async def test_notifier_does_not_block_and_reports_errors(monkeypatch):
    import asyncio

    from app.services.notifications import Notifier

    n = Notifier()
    n._loaded = True
    n._cfg = {"ntfy_url": "https://ntfy.example/thema", "telegram_token": "123:geheim", "telegram_chat_id": "1"}
    started = asyncio.Event()

    async def slow(*a, **k):
        started.set()
        await asyncio.sleep(0.2)

    async def failing(token, chat_id, text):
        raise RuntimeError(f"https://api.telegram.org/bot{token}/sendMessage nicht erreichbar")

    monkeypatch.setattr(n, "_ntfy", slow)
    monkeypatch.setattr(n, "_telegram", failing)
    await asyncio.wait_for(n.send("Test"), timeout=0.05)  # kehrt sofort zurück
    result = await n.deliver("Test")
    assert result["ntfy"] is None
    assert "geheim" not in result["telegram"] and "***" in result["telegram"]
    await n.close()


# --------------------------------------------------- Live-Verbindungen je Konto
class _FakeWS:
    def __init__(self, slow: bool = False):
        self.closed: int | None = None
        self.sent: list[str] = []
        self.slow = slow

    async def send_text(self, text: str) -> None:
        if self.slow:
            import asyncio
            await asyncio.sleep(60)
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


async def test_close_user_ends_only_that_accounts_sockets():
    from app.services.ws import WebSocketManager

    m = WebSocketManager()
    a1, a2, b = _FakeWS(), _FakeWS(), _FakeWS()
    await m.add(a1, user_id=1)
    await m.add(a2, user_id=1)
    await m.add(b, user_id=2)
    assert await m.close_user(1) == 2
    assert a1.closed == 4401 and a2.closed == 4401 and b.closed is None
    assert m.count == 1
    await m.broadcast({"x": 1})
    assert b.sent and not a1.sent


async def test_broadcast_slow_clients_do_not_add_up(monkeypatch):
    """Mehrere hängende Clients verzögern den Takt nur einmal um das Zeitlimit."""
    import asyncio
    import time

    from app.services import ws as wsmod

    real_wait_for = asyncio.wait_for

    async def short_wait_for(coro, timeout):
        return await real_wait_for(coro, timeout=0.2)

    monkeypatch.setattr(wsmod.asyncio, "wait_for", short_wait_for)
    m = wsmod.WebSocketManager()
    fast = _FakeWS()
    for _ in range(5):
        await m.add(_FakeWS(slow=True))
    await m.add(fast)
    t0 = time.monotonic()
    await m.broadcast({"x": 1})
    assert time.monotonic() - t0 < 0.8
    assert fast.sent
    assert m.count == 1


def test_netguard_nat64_metadata_blocked():
    assert blocked_reason("http://[64:ff9b::a9fe:a9fe]/") is not None


async def test_guarded_resolver_blocks_dns_name_pointing_to_metadata(monkeypatch):
    import aiohttp
    import socket

    from app.drivers.http_util import GuardedResolver

    async def fake(self, host, port=0, family=0):
        return [{"hostname": host, "host": "169.254.169.254", "port": port, "family": socket.AF_INET,
                 "proto": 0, "flags": 0}]

    monkeypatch.setattr(aiohttp.ThreadedResolver, "resolve", fake)
    with pytest.raises(OSError):
        await GuardedResolver().resolve("169.254.169.254.nip.io", 80)


async def test_scheme_relative_redirect_to_blocked_address_is_refused():
    from aiohttp import web

    from app.drivers.http_util import HttpDevice

    async def red(_r):
        raise web.HTTPFound("//0.0.0.0:18767/secret")

    async def sec(_r):
        return web.json_response({"leak": True})

    app = web.Application()
    app.router.add_get("/r", red)
    app.router.add_get("/secret", sec)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18767).start()
    dev = HttpDevice("http://127.0.0.1:18767")
    try:
        with pytest.raises(Exception) as exc:
            await dev.get_json("/r")
        assert "gesperrte Adresse" in str(exc.value)
    finally:
        await dev.close()
        await runner.cleanup()


def test_traceback_is_redacted():
    import io
    import logging

    from app.core import logsafe

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(logsafe.RedactFilter())
    logger = logging.getLogger("test.redact")
    logger.addHandler(handler)
    try:
        try:
            raise ConnectionError("GET https://api.example/v1?apikey=SUPERGEHEIM123 fehlgeschlagen")
        except ConnectionError:
            logger.exception("Abruf fehlgeschlagen")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "SUPERGEHEIM123" not in out and "apikey=***" in out
