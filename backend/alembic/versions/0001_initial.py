"""Initiales Schema (entspricht app/models.py).

Revision ID: 0001
"""
from alembic import op

from app.db import Base
from app import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute("SELECT create_hypertable('measurements', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
        op.execute("SELECT add_retention_policy('measurements', INTERVAL '90 days', if_not_exists => TRUE)")
    except Exception:  # noqa: BLE001 – reines PostgreSQL ohne Timescale
        pass


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
