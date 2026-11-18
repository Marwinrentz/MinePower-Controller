"""Regeltakt und Ereignis-Protokoll bei hängender Datenbank.

Eine Datenbank, die nicht antwortet (statt die Verbindung abzulehnen), hielt
vorher den ganzen Regeltakt an – die Wallbox lud so lange mit dem letzten
Strom weiter. Und zwei gleichzeitige Ereignisse während eines Ausfalls
schrieben die Warteschlange doppelt.
"""
import asyncio
import time

import pytest
from sqlalchemy import func, select

from app.db import Base, async_session, engine
from app.drivers.base import WallboxData, WallboxState
from app.models import Event
from app.services import audit

from .test_price_window_integration import build_loop, wallbox_of


@pytest.fixture(autouse=True)
async def tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


class _HangingSession:
    async def __aenter__(self):
        await asyncio.sleep(3600)

    async def __aexit__(self, *exc):
        return False


async def test_log_event_does_not_block_caller_on_hanging_db(monkeypatch):
    audit._pending.clear()
    monkeypatch.setattr(audit, "async_session", lambda: _HangingSession())
    monkeypatch.setattr(audit, "EVENT_WAIT_S", 0.2)
    t0 = time.monotonic()
    await audit.log_event("Test")
    assert time.monotonic() - t0 < 1.0
    for t in list(audit._tasks):
        t.cancel()


async def test_concurrent_events_during_outage_not_duplicated(monkeypatch):
    """Zwei Ereignisse gleichzeitig während eines Ausfalls, dann ein drittes
    nach der Erholung: genau drei Zeilen, keine doppelt, keine verloren."""
    audit._pending.clear()
    real = audit.async_session
    down = {"v": True}

    class _Failing:
        async def __aenter__(self):
            await asyncio.sleep(0.01)
            raise ConnectionError("weg")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(audit, "async_session", lambda: _Failing() if down["v"] else real())
    async with async_session() as db:
        before = await db.scalar(select(func.count()).select_from(Event)) or 0
    await asyncio.gather(audit.log_event("A"), audit.log_event("B"))
    assert audit.pending_events() == 2
    down["v"] = False
    await audit.log_event("C")
    assert audit.pending_events() == 0
    async with async_session() as db:
        msgs = (await db.scalars(select(Event.message).order_by(Event.id))).all()
    assert len(msgs) - before == 3
    assert sorted(msgs[before:]) == ["A", "B", "C"]


async def test_control_step_not_blocked_by_hanging_session_write(monkeypatch):
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    wb = wallbox_of(loop)
    wb._sessions_checked = True
    wb.data = WallboxData(state=WallboxState.CHARGING, power=4000.0)
    monkeypatch.setattr("app.core.loop.async_session", lambda: _HangingSession())
    monkeypatch.setattr("app.core.loop.SESSION_DB_WAIT_S", 0.2)

    t0 = time.monotonic()
    await loop._track_session(wb, 0.0, None)
    await loop._track_session(wb, 0.0, None)  # zweiter Takt: kein zweiter Schreibvorgang
    assert time.monotonic() - t0 < 1.0
    assert wb._session_task is not None and not wb._session_task.done()
    wb._session_task.cancel()
