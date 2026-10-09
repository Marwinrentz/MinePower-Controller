"""Version (eine Quelle), Changelog-Format und Hinweisfenster nach Updates."""
import json
import re
from pathlib import Path

import pytest

from app import __version__
from app.api.auth import mark_version_seen
from app.api.system import changelog
from app.core import changelog as cl
from app.db import async_session
from app.models import Setting, User
from app.startup import track_version

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


def test_single_version_source():
    version = (ROOT / "VERSION").read_text().strip()
    assert __version__ == version
    assert json.loads((ROOT / "frontend" / "package.json").read_text())["version"] == version
    assert cl.entries()[0]["version"] == version, "neueste Version im Changelog = VERSION"


def test_changelog_format_and_wording():
    entries = cl.entries()
    assert entries
    for e in entries:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
        lines = e["new"] + e["changed"] + e["fixed"]
        assert lines, e["version"]
        for line in lines:
            low = line.lower()
            for word in ("verbessert", "optimiert", "nahtlos", "erweitert"):
                assert word not in low, line
            assert " – " not in line and " — " not in line, line
            assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", line), line


def test_since_compares_semver():
    assert [e["version"] for e in cl.since("0.9.0")] == [e["version"] for e in cl.entries()]
    assert cl.since(__version__) == []
    assert cl.version_key("2.17.0") < cl.version_key("3.0.0") < cl.version_key("10.0.0")


async def test_fresh_install_shows_no_popup():
    assert await track_version() is None
    async with async_session() as db:
        db.add(User(email="a@example.org", name="A", password_hash="x", role="admin"))
        await db.commit()
        user = (await db.execute(User.__table__.select())).first()
    assert user.last_seen_version == __version__


async def test_upgrade_from_217_shows_changes_until_seen():
    async with async_session() as db:
        db.add(User(email="a@example.org", name="A", password_hash="x", role="admin", last_seen_version=None))
        await db.commit()
    async with async_session() as db:
        # Simulierter Altbestand: Spalte leer (vor 3.0 nicht vorhanden)
        await db.execute(User.__table__.update().values(last_seen_version=None))
        await db.commit()
    await track_version()
    async with async_session() as db:
        user = (await db.scalars(User.__table__.select())).first()
        row = await db.get(User, 1)
        assert row.last_seen_version == "2.17.0"
        data = await changelog(user=row)
        assert [e["version"] for e in data["unseen"]] == [__version__]
        out = await mark_version_seen(db=db, user=row)
        assert out.last_seen_version == __version__
        assert (await changelog(user=row))["unseen"] == []
        system = await db.get(Setting, "system")
        assert system.value["app_version"] == __version__
    assert user is not None
