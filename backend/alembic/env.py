"""Alembic-Umgebung (async). Beim App-Start ruft startup.init_db() die
Revisionen auf (upgrade head); auf der Kommandozeile läuft Alembic wie gewohnt."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401 – Modelle registrieren

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url.replace("+asyncpg", ""),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


# Beim Start der Anwendung reicht startup.init_db() seine Verbindung durch
# (config.attributes["connection"]); auf der Kommandozeile baut Alembic
# selbst eine auf.
_connection = context.config.attributes.get("connection")
if _connection is not None:
    do_run_migrations(_connection)
elif context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
