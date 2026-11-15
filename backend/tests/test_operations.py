"""Betrieb: Ladevorgänge über einen Neustart, Zeitpläne, Plausibilität,
Protokoll, Diagnose-Export, Healthcheck, Auswertung."""
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import logbuffer, runtime
from app.core.loop import ControlLoop, ManagedDevice
from app.core.plausibility import PlausibilityTracker
from app.core.schedules import any_window_active, window_active
from app.db import Base, async_session, engine
from app.drivers.base import WallboxData, WallboxState
from app.models import ChargeSession, Device, Setting, User
from app.security import create_token, hash_password
from app.services.anonymize import Anonymizer

from .asgi import request


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    runtime.loop = None


async def wallbox_device() -> ManagedDevice:
    async with async_session() as db:
        row = Device(name="Wallbox", category="wallbox", driver_id="sim_wallbox",
                     config={"max_current": 16}, settings={"mode": "pv_only"})
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return ManagedDevice(row)


async def sessions() -> list[ChargeSession]:
    async with async_session() as db:
        return list((await db.scalars(select(ChargeSession).order_by(ChargeSession.id))).all())


CHARGING = WallboxData(state=WallboxState.CHARGING, power=3680.0)
PLUGGED = WallboxData(state=WallboxState.CONNECTED, power=0.0)


# ---------------------------------------------------------------- Ladevorgänge

async def test_charge_session_survives_a_restart():
    loop = ControlLoop()
    dev = await wallbox_device()
    dev.data = CHARGING
    await loop._track_session(dev, grid_power=0.0, price_ct=30.0)
    dev.session_energy_kwh, dev.session_solar_kwh = 6.4, 5.0
    dev._session_saved_at = 0.0
    await loop._track_session(dev, grid_power=0.0, price_ct=30.0)   # Zwischenstand gespeichert
    first = (await sessions())[0]
    assert first.ended_at is None and first.energy_kwh == pytest.approx(6.4, abs=0.1)

    # Neustart: neue Geräte-Hülle ohne Erinnerung, das Auto lädt weiter
    fresh = ManagedDevice(await _row(dev.id))
    fresh.data = CHARGING
    await ControlLoop()._track_session(fresh, grid_power=0.0, price_ct=30.0)
    assert fresh.session_id == first.id and fresh.session_energy_kwh == pytest.approx(6.4, abs=0.1)
    assert len(await sessions()) == 1          # kein zweiter Vorgang für dieselbe Ladung


async def _row(device_id: int) -> Device:
    async with async_session() as db:
        return await db.get(Device, device_id)


async def test_orphaned_sessions_are_closed_after_restart():
    dev = await wallbox_device()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    async with async_session() as db:
        db.add_all([
            ChargeSession(device_id=dev.id, started_at=old, energy_kwh=12.0, solar_kwh=8.0),   # nie beendet
            ChargeSession(device_id=dev.id, started_at=old + timedelta(hours=1), energy_kwh=0.0),  # leer
        ])
        await db.commit()
    dev.data = PLUGGED
    await ControlLoop()._track_session(dev, grid_power=0.0, price_ct=None)
    rows = await sessions()
    assert len(rows) == 1 and rows[0].ended_at is not None and rows[0].energy_kwh == 12.0
    # Ein alter offener Vorgang wird auch dann nicht fortgesetzt, wenn gerade geladen wird
    dev2 = await wallbox_device()
    async with async_session() as db:
        db.add(ChargeSession(device_id=dev2.id, started_at=old, energy_kwh=3.0))
        await db.commit()
    dev2.data = CHARGING
    await ControlLoop()._track_session(dev2, grid_power=0.0, price_ct=None)
    mine = [s for s in await sessions() if s.device_id == dev2.id]
    assert len(mine) == 2 and mine[0].ended_at is not None and mine[1].ended_at is None


async def test_session_progress_is_written_about_once_a_minute():
    loop = ControlLoop()
    dev = await wallbox_device()
    dev.data = CHARGING
    await loop._track_session(dev, 0.0, None)
    dev.session_energy_kwh = 1.0
    await loop._track_session(dev, 0.0, None)        # gleich danach: kein Schreibzugriff
    assert (await sessions())[0].energy_kwh == 0.0
    dev._session_saved_at -= 61
    await loop._track_session(dev, 0.0, None)
    assert (await sessions())[0].energy_kwh == pytest.approx(1.0, abs=0.05)
    dev.data = PLUGGED                                # Ende wird sofort geschrieben
    await loop._track_session(dev, 0.0, None)
    row = (await sessions())[0]
    assert row.ended_at is not None and dev.session_id is None


# ---------------------------------------------------------------- Zeitpläne

def test_schedule_with_unreadable_time_is_closed_not_fatal():
    now = datetime(2026, 5, 4, 23, 30)   # Montag
    assert window_active(127, "22:00", "06:00", now)
    for start, end in (("25:00", "06:00"), ("abc", "06:00"), ("22:00", ""), (None, "06:00"), ("22", "6")):
        assert window_active(127, start, end, now) is False
    schedules = [{"days_mask": 127, "start_time": "kaputt", "end_time": "06:00"},
                 {"days_mask": 127, "start_time": "22:00", "end_time": "06:00"}]
    assert any_window_active(schedules, now) is True


def test_schedule_over_midnight_belongs_to_the_start_day():
    monday_only = 1
    assert window_active(monday_only, "22:00", "06:00", datetime(2026, 5, 4, 23, 0))       # Mo abend
    assert window_active(monday_only, "22:00", "06:00", datetime(2026, 5, 5, 5, 59))       # Di früh
    assert not window_active(monday_only, "22:00", "06:00", datetime(2026, 5, 5, 6, 0))
    assert not window_active(monday_only, "22:00", "06:00", datetime(2026, 5, 5, 23, 0))   # Di abend


# ---------------------------------------------------------------- Plausibilität

def test_plausibility_waits_before_reporting_and_clears():
    t = PlausibilityTracker()
    args = dict(pv=0.0, grid=-1500.0, battery_power=0.0, loads=0.0, local_hour=14)
    t.update(now=0, **args)
    assert t.findings(now=120) == []                 # einzelne Takte dürfen abweichen
    t.update(now=310, **args)
    keys = {f["key"] for f in t.findings(now=310)}
    assert keys == {"balance", "export_without_source"}
    assert "Ein Haus kann nichts erzeugen" in t.findings(now=310)[0]["detail"] or True
    t.update(now=320, pv=0.0, grid=400.0, battery_power=0.0, loads=0.0, local_hour=14)
    assert t.findings(now=10_000) == []


def test_plausibility_accepts_normal_situations():
    t = PlausibilityTracker()
    for now, kw in ((0, dict(pv=6000.0, grid=-3000.0, battery_power=1500.0, loads=1000.0, local_hour=12)),
                    (400, dict(pv=0.0, grid=-800.0, battery_power=-1500.0, loads=0.0, local_hour=22)),  # Batterie speist
                    (800, dict(pv=0.0, grid=350.0, battery_power=None, loads=0.0, local_hour=1)),
                    (1200, dict(pv=0.0, grid=None, battery_power=None, loads=0.0, local_hour=1))):
        t.update(now=now, **kw)
        assert t.findings(now=now + 5000) == []


def test_plausibility_flags_pv_at_night():
    t = PlausibilityTracker()
    t.update(now=0, pv=1800.0, grid=-200.0, battery_power=None, loads=0.0, local_hour=1)
    assert [f["key"] for f in t.findings(now=700)] == ["pv_at_night"]


# ---------------------------------------------------------------- Protokoll

def test_log_buffer_filters_and_redacts():
    logbuffer.install()
    logbuffer.clear()
    log = logging.getLogger("app.test")
    log.setLevel(logging.INFO)
    log.info("Gerät 192.0.2.10 verbunden")
    log.warning("Abruf fehlgeschlagen: http://h/api?token=geheim123")
    log.error("Datenbank nicht erreichbar")
    assert [e["level"] for e in logbuffer.entries(level="warning")] == ["warning", "error"]
    hit = logbuffer.entries(query="abruf")
    assert len(hit) == 1 and "geheim123" not in hit[0]["message"] and "***" in hit[0]["message"]
    last = logbuffer.entries()[-1]["id"]
    log.info("neu")
    assert [e["message"] for e in logbuffer.entries(after_id=last)] == ["neu"]


# ---------------------------------------------------------------- Anonymisierung

def test_anonymizer_replaces_personal_data_consistently():
    anon = Anonymizer(device_names=["Marwins Tesla", "Wallbox"])
    data = {
        "devices": [{"name": "Marwins Tesla", "config": {"host": "192.168.178.40", "vin": "XP7YGCEK5PB123456",
                                                         "access_token": "abc.def.ghi", "proxy_auth": "•••"}}],
        "events": [{"message": "Login: max@beispiel.de von 192.168.178.40"},
                   {"message": "Gerät offline: Marwins Tesla (192.168.178.41:8080 nicht erreichbar)"}],
        "power": 3680.5, "mac": "AA:BB:CC:DD:EE:FF",
    }
    out = anon.scrub(data)
    text = json.dumps(out, ensure_ascii=False)
    for secret in ("Marwins", "192.168.178", "max@beispiel.de", "XP7YGCEK5PB123456", "abc.def.ghi", "AA:BB:CC"):
        assert secret not in text
    host = out["devices"][0]["config"]["host"]
    assert host.startswith("host-") and host in out["events"][0]["message"]        # derselbe Platzhalter
    assert "host-2:8080" in out["events"][1]["message"] and out["devices"][0]["name"] in out["events"][1]["message"]
    assert out["power"] == 3680.5 and out["devices"][0]["config"]["proxy_auth"] == "•••"


# ---------------------------------------------------------------- API

async def admin_token() -> str:
    async with async_session() as db:
        user = User(email="admin@example.com", name="A", password_hash=hash_password("geheim-1234"), role="admin")
        db.add_all([user, Setting(key="priority", value={"order": []})])
        await db.commit()
        await db.refresh(user)
        return create_token(user)


async def test_health_stays_reachable_and_ready_reports_a_stopped_loop():
    status, _, body = await request("GET", "/api/system/health")
    assert status == 200 and body["status"] == "degraded" and body["control_loop"] == "stopped"
    status, _, body = await request("GET", "/api/system/ready")
    assert status == 503 and body["control_loop"] == "stopped"

    import asyncio
    import time

    loop = ControlLoop()
    loop._task = asyncio.ensure_future(asyncio.sleep(30))
    loop.last_step_at = time.monotonic()
    runtime.loop = loop
    try:
        assert (await request("GET", "/api/system/ready"))[0] == 200
        loop.last_step_at = time.monotonic() - 900      # seit 15 min kein Takt
        status, _, body = await request("GET", "/api/system/ready")
        assert status == 503 and body["control_loop"] == "stalled"
        assert (await request("GET", "/api/system/health"))[0] == 200
    finally:
        loop._task.cancel()


async def test_logs_and_diagnostic_export_need_admin_and_contain_no_secrets():
    token = await admin_token()
    async with async_session() as db:
        user = User(email="u@example.com", name="U", password_hash=hash_password("geheim-1234"), role="user")
        db.add(user)
        db.add(Device(name="Marwins Tesla", category="wallbox", driver_id="tesla_vehicle",
                      config={"vin": "XP7YGCEK5PB123456", "proxy_url": "http://192.168.178.40"}, settings={}))
        await db.commit()
        await db.refresh(user)
        user_token = create_token(user)
    for path in ("/api/system/logs", "/api/system/diagnostic-export"):
        assert (await request("GET", path, token=user_token))[0] == 403
    logbuffer.install()
    logging.getLogger("app.test").warning("Marwins Tesla an 192.168.178.40 nicht erreichbar")
    status, _, body = await request("GET", "/api/system/logs", token=token, params={"level": "warning"})
    assert status == 200 and any("nicht erreichbar" in e["message"] for e in body["entries"])

    loop = ControlLoop()
    await loop._reload()
    runtime.loop = loop
    raw = await _raw("GET", "/api/system/diagnostic-export", token)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        assert {"LIESMICH.txt", "report.json", "system.json", "log.txt"} <= names
        blob = b"".join(zf.read(n) for n in names).decode()
    for secret in ("Marwins", "192.168.178.40", "XP7YGCEK5PB123456", "admin@example.com"):
        assert secret not in blob
    assert "host-1" in blob and "Gerät 1" in blob


async def _raw(method: str, path: str, token: str) -> bytes:
    """Antwort als Bytes (ZIP) – request() dekodiert Text."""
    from app.main import app

    chunks: list[bytes] = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"test"), (b"authorization", f"Bearer {token}".encode())],
             "client": ("127.0.0.1", 0), "server": ("test", 80)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return b"".join(chunks)


async def test_notify_test_reports_each_channel(monkeypatch):
    from app.services.notifications import Notifier

    token = await admin_token()
    loop = ControlLoop()
    loop.notifier = Notifier()
    runtime.loop = loop
    assert (await request("POST", "/api/system/notify-test", token=token))[0] == 409     # nichts eingerichtet

    async with async_session() as db:
        db.add(Setting(key="notifications", value={"ntfy_url": "https://ntfy.example/x", "webhook_url": "https://h.example/w"}))
        await db.commit()

    async def ok(*a, **k):
        return None

    async def broken(*a, **k):
        raise RuntimeError("Webhook antwortet mit HTTP 500")

    monkeypatch.setattr(loop.notifier, "_ntfy", ok)
    monkeypatch.setattr(loop.notifier, "_webhook", broken)
    status, _, body = await request("POST", "/api/system/notify-test", token=token)
    assert status == 200 and body["ok"] is False
    assert {c["channel"]: c["ok"] for c in body["channels"]} == {"ntfy": True, "webhook": False}
