"""Konten: Zeitpunkt des letzten Passwortwechsels, persönliche Einstellungen.

``password_changed_at`` entwertet Anmelde-Tokens, die vor einem
Passwortwechsel ausgestellt wurden. ``prefs`` hält Einstellungen der
Oberfläche je Konto (z. B. BETA-Oberfläche).

Die Anwendung ergänzt die Spalten beim Start auch selbst (startup.init_db);
diese Revision ist für Installationen, die Migrationen mit Alembic fahren.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "password_changed_at" not in columns:
        op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    if "prefs" not in columns:
        op.add_column("users", sa.Column("prefs", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "prefs")
    op.drop_column("users", "password_changed_at")
