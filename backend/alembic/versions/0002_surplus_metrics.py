"""Regelungs-Einstellungen für die Überschussnutzung ergänzen.

Neu in dieser Version:
  * adaptives Totband (adaptive_deadband, deadband_min_w, deadband_max_w)
  * Lückenfüller-Schwelle (gap_fill_min_w)
  * aktive Nutzung der Solarprognose (use_forecast)

Bestehende Werte bleiben unangetastet – ergänzt wird nur, was fehlt. Das
Zeitreihen-Schema ändert sich nicht: Die neue Kennzahl „verschenkte Energie“
läuft als zusätzliches `field` ('waste_power') durch die vorhandene
`measurements`-Hypertable und braucht deshalb keine Strukturänderung.

Revision ID: 0002
Revises: 0001
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW_REGULATION_DEFAULTS = {
    "adaptive_deadband": True,
    "deadband_min_w": 40,
    "deadband_max_w": 400,
    "gap_fill_min_w": 25,
    "use_forecast": True,
}


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'regulation'")
    ).fetchone()
    if row is None:
        return  # Frische Installation – seed_defaults() legt alles an
    current = row[0]
    if isinstance(current, str):  # je nach Treiber kommt JSON als Text
        current = json.loads(current)
    merged = {**NEW_REGULATION_DEFAULTS, **(current or {})}  # Bestehendes gewinnt
    bind.execute(
        sa.text("UPDATE settings SET value = CAST(:value AS JSON) WHERE key = 'regulation'"),
        {"value": json.dumps(merged)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'regulation'")
    ).fetchone()
    if row is None:
        return
    current = row[0]
    if isinstance(current, str):
        current = json.loads(current)
    reduced = {k: v for k, v in (current or {}).items() if k not in NEW_REGULATION_DEFAULTS}
    bind.execute(
        sa.text("UPDATE settings SET value = CAST(:value AS JSON) WHERE key = 'regulation'"),
        {"value": json.dumps(reduced)},
    )
