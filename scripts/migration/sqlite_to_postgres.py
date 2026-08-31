#!/usr/bin/env python3
"""One-shot migration from SQLite ledger to PostgreSQL (P2-2).

The migration is facts-first: bots, immutable fills and OCC positions are copied
before projections (snapshots) so a partially completed migration still leaves
a coherent truth source.  It is idempotent and safe to re-run.

Usage:
    python scripts/migration/sqlite_to_postgres.py \
        --sqlite data/web_ui_runtime.sqlite3 \
        --postgres-url "postgresql://user:pass@host:5432/grid"

If DATABASE_URL is set, --postgres-url may be omitted.  Use --dry-run to print
counts without writing.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.infrastructure.database.ledger_store import (  # noqa: E402
    PostgresLedgerStore,
    SqliteLedgerStore,
)
from core.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


def migrate(sqlite_path: str, postgres_url: str, dry_run: bool = False) -> Dict[str, int]:
    src = SqliteLedgerStore(sqlite_path)
    dst = PostgresLedgerStore(postgres_url)

    bots = src.get_all_bots()
    stats = {
        "bots": len(bots),
        "fills": 0,
        "positions": 0,
        "snapshots": 0,
    }

    for bot in bots:
        if dry_run:
            continue
        dst.register_or_update_bot(bot)

        for fill in src.get_fills(bot.bot_id, limit=1_000_000):
            dst.record_fill(fill)
            stats["fills"] += 1

        pos = src.get_position(bot.bot_id)
        if pos is not None:
            dst.update_position_occ(bot.bot_id, pos, expected_version=getattr(pos, "version", 1) or 1)
            stats["positions"] += 1

        snap = src.load_snapshot_by_bot_id(bot.bot_id)
        if snap:
            payload, updated_at = snap
            dst.save_snapshot(bot.bot_id, bot.config_path or bot.bot_id, payload, updated_at)
            stats["snapshots"] += 1

    logger.info(f"✅ sqlite_to_postgres 遷移完成: {stats}" + ("（dry-run，未寫入）" if dry_run else ""))
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite ledger -> PostgreSQL")
    parser.add_argument("--sqlite", default="data/web_ui_runtime.sqlite3")
    parser.add_argument("--postgres-url", default=None, help="Defaults to $DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    postgres_url = args.postgres_url or os.getenv("DATABASE_URL")
    if not postgres_url:
        print("❌ 未指定 postgres-url，也未設定 DATABASE_URL。", file=sys.stderr)
        return 2

    migrate(args.sqlite, postgres_url, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
