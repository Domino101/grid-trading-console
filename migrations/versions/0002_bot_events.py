"""bot_events append-only event stream (Phase 3, P3-1)

Revision ID: 0002_bot_events
Revises: 0001_initial_schema
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_bot_events"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("dedupe_key", sa.String(512), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_bot_events_bot_seq", "bot_events", ["bot_id", "seq"], unique=True)
    op.create_index("idx_bot_events_bot_time", "bot_events", ["bot_id", sa.text("created_at DESC")])
    # Partial unique index for idempotent re-sends (dialect-specific creation).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_dedupe "
            "ON bot_events (bot_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_dedupe "
            "ON bot_events (bot_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("DROP INDEX IF EXISTS idx_bot_events_dedupe")
    op.drop_index("idx_bot_events_bot_time", table_name="bot_events")
    op.drop_index("idx_bot_events_bot_seq", table_name="bot_events")
    op.drop_table("bot_events")
