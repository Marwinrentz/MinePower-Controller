"""Konten: zuletzt gesehene Version (Hinweisfenster nach Updates).

Die Anwendung ergänzt die Spalte beim Start auch selbst (startup.init_db);
diese Revision ist für Installationen, die Migrationen mit Alembic fahren.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "last_seen_version" not in columns:
        op.add_column("users", sa.Column("last_seen_version", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_seen_version")
