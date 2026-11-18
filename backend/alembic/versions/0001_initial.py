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
    # Tabellen anlegen, soweit sie fehlen. Hypertable, Aufbewahrung,
    # Kompression und Stundenwerte richtet die Anwendung beim Start ein
    # (startup.setup_timeseries) – je Schritt abgesichert, weil ein Fehlschlag
    # in PostgreSQL sonst die ganze Migration zurückrollt.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
