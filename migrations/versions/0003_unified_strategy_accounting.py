"""unified strategy accounting (Phase 4, P4-1)

Revision ID: 0003_unified_accounting
Revises: 0002_bot_events
Create Date: 2026-09-16

把 grid / dca / coinfull_prime 三個策略的記帳收斂到同一個 canonical ledger：
* bot_baskets           — 多標的策略的受管持倉籃子（取代 coinfull_prime_active_basket.json）
* bot_regime_decisions  — 風險閘門決策（取代 coinfull_prime_regime_history.jsonl）
* bot_runtime_state     — per-bot 執行期狀態 KV（取代 coinfull_prime_scheduler.json）

既有 SQLite 資料庫請改用 ``LedgerStore.initialize()``（SQLAlchemy create_all，
冪等）或 ``tools/ledger_consistency_audit.py --backfill``；本 migration 供全新
資料庫與 PostgreSQL 真相源使用（同 AGENTS.md 之既有約束）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_unified_accounting"
down_revision = "0002_bot_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_baskets",
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("side", sa.String(16), nullable=False, server_default="long"),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("notional_usdt", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("opened_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "account_id", "symbol"),
    )
    op.create_index("idx_bot_baskets_account_status", "bot_baskets", ["account_id", "status"])
    op.create_index("idx_bot_baskets_updated", "bot_baskets", ["bot_id", sa.text("updated_at DESC")])

    op.create_table(
        "bot_regime_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.Float(), nullable=False),
        sa.Column("pts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scale", sa.Numeric(12, 6), nullable=False, server_default="1"),
        sa.Column("allow_new_entries", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("force_flatten", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("crash_defer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(512), nullable=True),
    )
    op.create_index(
        "idx_regime_decisions_bot_time", "bot_regime_decisions", ["bot_id", sa.text("decided_at DESC")]
    )
    bind = op.get_bind()
    if bind.dialect.name in ("postgresql", "sqlite"):
        bind.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_decisions_dedupe "
            "ON bot_regime_decisions (bot_id, dedupe_key) WHERE dedupe_key IS NOT NULL"
        )

    op.create_table(
        "bot_runtime_state",
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "key"),
    )
    op.create_index("idx_runtime_state_updated", "bot_runtime_state", ["bot_id", sa.text("updated_at DESC")])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("DROP INDEX IF EXISTS idx_regime_decisions_dedupe")
    op.drop_index("idx_runtime_state_updated", table_name="bot_runtime_state")
    op.drop_table("bot_runtime_state")
    op.drop_index("idx_regime_decisions_bot_time", table_name="bot_regime_decisions")
    op.drop_table("bot_regime_decisions")
    op.drop_index("idx_bot_baskets_updated", table_name="bot_baskets")
    op.drop_index("idx_bot_baskets_account_status", table_name="bot_baskets")
    op.drop_table("bot_baskets")
