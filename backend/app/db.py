"""Async-Datenbankzugriff (SQLAlchemy 2.x + asyncpg)."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict:
    """Zeitlimits für PostgreSQL: Eine Datenbank, die nicht antwortet, darf
    weder den Start noch den Regelkreis auf unbestimmte Zeit anhalten.
    Ohne sie wartet asyncpg bis zu 60 s auf die Verbindung und unbegrenzt
    auf eine Antwort."""
    options: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql+asyncpg"):
        options["connect_args"] = {"timeout": 10, "command_timeout": 60}
        options["pool_recycle"] = 1800
    return options


_url = get_settings().database_url
engine = create_async_engine(_url, **_engine_options(_url))
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
