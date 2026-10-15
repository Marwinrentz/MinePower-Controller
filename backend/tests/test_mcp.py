"""MCP-Server: Anmeldung per Zugangsschlüssel, Protokoll, Rollen und Werkzeuge.

Die Anfragen laufen in-process gegen die echte FastAPI-App (ohne Lifespan,
also ohne Regelkreis) – geprüft werden Protokoll, Rechte und die Weitergabe
an die REST-API mit ihren Prüfungen."""
import json

import pytest

from app.api import mcp
from app.db import async_session
from app.main import app
from app.models import ApiToken, User
from app.security import hash_password


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


async def make_token(role: str = "admin", user_role: str = "admin") -> str:
    async with async_session() as db:
        user = User(email=f"{role}-{user_role}@example.com", name="Test", password_hash=hash_password("x" * 8),
                    role=user_role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        token = mcp.TOKEN_PREFIX + "test-" + role + "-" + user_role
        db.add(ApiToken(user_id=user.id, name="Test", token_hash=mcp.hash_token(token), prefix=token[:7], role=role))
        await db.commit()
    return token


async def rpc(token: str | None, body, *, raw: bool = False, path: str = "/mcp"):
    """POST /mcp über ASGI; liefert (Status, JSON oder None)."""
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 0), "server": ("test", 80)}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    out = {"status": 0, "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    await app(scope, receive, send)
    data = json.loads(out["body"]) if out["body"] else None
    return out["status"], data


async def call(token, name, arguments=None):
    status, data = await rpc(token, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                     "params": {"name": name, "arguments": arguments or {}}})
    assert status == 200, data
    result = data["result"]
    text = result["content"][0]["text"]
    return result["isError"], text, result.get("structuredContent")


async def test_rejects_missing_or_wrong_token():
    status, _ = await rpc(None, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert status == 401
    status, _ = await rpc("mp_falsch", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert status == 401


async def test_initialize_and_notification():
    token = await make_token()
    status, data = await rpc(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "1"}}})
    assert status == 200
    assert data["result"]["protocolVersion"] == "2025-03-26"
    assert data["result"]["serverInfo"]["name"] == "MinePower"
    assert "tools" in data["result"]["capabilities"]
    status, data = await rpc(token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202 and data is None
    status, data = await rpc(token, {"jsonrpc": "2.0", "id": 2, "method": "gibt/es/nicht"})
    assert data["error"]["code"] == -32601


async def test_tool_list_follows_role():
    names = {}
    for role in ("readonly", "user", "admin"):
        token = await make_token(role, "admin")
        _, data = await rpc(token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names[role] = {t["name"] for t in data["result"]["tools"]}
        for t in data["result"]["tools"]:
            assert t["inputSchema"]["type"] == "object" and t["description"]
    assert "get_live_status" in names["readonly"] and "set_mode" not in names["readonly"]
    assert "set_mode" in names["user"] and "add_device" not in names["user"]
    assert {"add_device", "create_schedule", "update_settings"} <= names["admin"]


async def test_token_role_capped_by_account():
    token = await make_token("admin", "readonly")
    _, data = await rpc(token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "add_device" not in {t["name"] for t in data["result"]["tools"]}
    error, text, _ = await call(token, "pause_regulation")
    assert error and "Berechtigung" in text


async def test_drivers_settings_and_device_setup():
    token = await make_token()
    error, text, _ = await call(token, "list_drivers", {"category": "meter"})
    drivers = json.loads(text)
    assert not error and any(d["id"] == "shelly_3em" for d in drivers)
    assert all("fields" in d for d in drivers)

    error, text, _ = await call(token, "add_device", {"name": "Zähler", "category": "meter",
                                                      "driver_id": "shelly_3em", "config": {"host": "192.0.2.5"}})
    assert not error, text
    error, text, _ = await call(token, "add_device", {"name": "Zweiter", "category": "meter",
                                                      "driver_id": "shelly_3em", "config": {"host": "192.0.2.6"}})
    assert error and "409" in text  # höchstens ein Netzzähler
    error, text, _ = await call(token, "add_device", {"name": "WR", "category": "inverter",
                                                      "driver_id": "sunspec_inverter",
                                                      "config": {"host": "192.0.2.7", "port": 99999}})
    assert error and "Port" in text  # Feldprüfung kommt mit Feldname zurück

    error, text, data = await call(token, "list_devices")
    assert not error and len(data["devices"]) == 1 and data["limits"]["meter"] == 1


async def test_update_settings_merges_section():
    token = await make_token()
    error, text, _ = await call(token, "update_settings", {"section": "regulation", "values": {"grid_target_w": -50}})
    assert not error, text
    error, text, data = await call(token, "get_settings", {"section": "regulation"})
    assert data["regulation"]["grid_target_w"] == -50
    error, text, _ = await call(token, "get_settings", {"section": "gibtsnicht"})
    assert error and "unbekannt" in text


async def test_schedule_days_and_validation():
    assert mcp._days_mask("workdays") == 31
    assert mcp._days_mask(["mo", "Mi", "friday"]) == 1 | 4 | 16
    with pytest.raises(mcp.ApiError):
        mcp._days_mask(["xx"])
    token = await make_token()
    error, text, _ = await call(token, "set_mode", {"device_id": 1, "mode": "turbo"})
    assert error and "Betriebsart" in text
    error, text, _ = await call(token, "delete_device", {"device_id": 1, "confirm": False})
    assert error and "confirm" in text


def test_csv_summary():
    text = ("ID;Start;Ende;Energie (kWh);Solar (kWh);Solaranteil (%);Kosten (EUR);Geraet;Nutzer;RFID\n"
            "1;2026-10-01;2026-10-01;10,00;6,00;60;1,20;Box;;\n2;2026-10-02;;5,00;5,00;100;0,00;Box;;\n")
    s = mcp._csv_summary(text)
    assert s == {"sessions": 2, "energy_kwh": 15.0, "solar_kwh": 11.0, "solar_pct": 73.3, "cost_eur": 1.2}


async def test_token_api_hides_secret_and_respects_role():
    from fastapi import HTTPException

    async with async_session() as db:
        user = User(email="u@example.com", name="U", password_hash="x", role="user")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        created = await mcp.create_api_token(mcp.TokenCreate(name="Claude", role="user"), db, user)
        assert created["token"].startswith("mp_") and created["prefix"] == created["token"][:7]
        listed = await mcp.list_tokens(db, user)
        assert "token" not in listed[0]
        stored = (await db.get(ApiToken, created["id"])).token_hash
        assert stored == mcp.hash_token(created["token"]) and created["token"] not in stored
        with pytest.raises(HTTPException):
            await mcp.create_api_token(mcp.TokenCreate(name="X", role="admin"), db, user)


async def test_key_in_url_for_connectors():
    token = await make_token("readonly", "admin")
    status, data = await rpc(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, path=f"/mcp/k/{token}")
    assert status == 200 and any(t["name"] == "get_live_status" for t in data["result"]["tools"])
    status, _ = await rpc(None, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/mcp/k/mp_falsch")
    assert status == 401


def test_access_log_redacts_url_key():
    import logging
    record = logging.LogRecord("uvicorn.access", logging.INFO, "x", 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "POST", "/mcp/k/mp_geheim123", "1.1", 200), None)
    mcp.RedactKeyFilter().filter(record)
    assert "mp_geheim123" not in record.getMessage() and "/mcp/k/***" in record.getMessage()
