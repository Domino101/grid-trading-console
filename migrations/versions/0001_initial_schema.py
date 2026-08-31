"""initial relational ledger schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("bot_id", sa.String(255), primary_key=True),
        sa.Column("strategy_type", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(255), nullable=False),
        sa.Column("exchange", sa.String(64), nullable=False),
        sa.Column("account_mode", sa.String(32), nullable=False),
        sa.Column("config_path", sa.String(1024), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("archived_at", sa.Float(), nullable=True),
    )
    op.create_table(
        "trade_fills",
        sa.Column("fill_id", sa.String(512), primary_key=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("exchange_order_id", sa.String(512), nullable=True),
        sa.Column("client_order_id", sa.String(512), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("fee_currency", sa.String(64), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("executed_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "bot_positions",
        sa.Column("bot_id", sa.String(255), primary_key=True),
        sa.Column("active_direction", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Numeric(38, 18), nullable=True),
        sa.Column("total_cost", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("completed_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("realized_profit", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("total_fees", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "bot_snapshots",
        sa.Column("bot_id", sa.String(255), primary_key=True),
        sa.Column("config_path", sa.String(1024), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "bot_snapshot_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("config_path", sa.String(1024), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "bot_identity_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("config_path", sa.String(1024), nullable=True),
        sa.Column("old_bot_id", sa.String(255), nullable=True),
        sa.Column("new_bot_id", sa.String(255), nullable=True),
        sa.Column("recorded_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "grid_runtime_snapshots",
        sa.Column("config_path", sa.String(1024), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "grid_runtime_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("config_path", sa.String(1024), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_trade_fills_bot_time", "trade_fills", ["bot_id", sa.text("executed_at DESC")])
    op.create_index("idx_trade_fills_client_order", "trade_fills", ["client_order_id"])
    op.create_index("idx_bots_config_path", "bots", ["config_path"])
    op.create_index("idx_snapshot_history_bot_time", "bot_snapshot_history", ["bot_id", sa.text("recorded_at DESC")])
    op.create_index("idx_snapshot_history_path_time", "bot_snapshot_history", ["config_path", sa.text("recorded_at DESC")])
    op.create_index("idx_identity_events_bot_time", "bot_identity_events", ["bot_id", sa.text("recorded_at DESC")])
    op.create_index("idx_legacy_runtime_history", "grid_runtime_history", ["config_path", sa.text("recorded_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_legacy_runtime_history", table_name="grid_runtime_history")
    op.drop_index("idx_identity_events_bot_time", table_name="bot_identity_events")
    op.drop_index("idx_snapshot_history_path_time", table_name="bot_snapshot_history")
    op.drop_index("idx_snapshot_history_bot_time", table_name="bot_snapshot_history")
    op.drop_index("idx_bots_config_path", table_name="bots")
    op.drop_index("idx_trade_fills_client_order", table_name="trade_fills")
    op.drop_index("idx_trade_fills_bot_time", table_name="trade_fills")
    op.drop_table("grid_runtime_history")
    op.drop_table("grid_runtime_snapshots")
    op.drop_table("bot_identity_events")
    op.drop_table("bot_snapshot_history")
    op.drop_table("bot_snapshots")
    op.drop_table("bot_positions")
    op.drop_table("trade_fills")
    op.drop_table("bots")
