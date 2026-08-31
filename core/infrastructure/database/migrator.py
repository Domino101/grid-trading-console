"""Database Migration & Backfill Engine.

Inspects SQLite databases and automatically migrates legacy JSON blob snapshots
into normalized tables (bots, trade_fills, bot_positions, bot_snapshots).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Dict, Optional

from core.infrastructure.database.guard import EnvironmentGuard
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotPosition, BotRecord, TradeFill
from core.logging import get_logger
from web_ui.backend.lifecycle import connect_sqlite

logger = get_logger(__name__)


class DatabaseMigrator:
    """Migrates legacy grid_runtime_snapshots to relational ledger schema."""

    @classmethod
    def migrate(cls, database_path: Optional[str] = None) -> Dict[str, int]:
        """Execute migration and backfill on target database."""
        db_path = EnvironmentGuard.get_safe_database_path(database_path)
        if db_path != ":memory:" and not os.path.exists(db_path):
            return {"bots_migrated": 0, "positions_migrated": 0, "fills_migrated": 0}

        repo = LedgerRepository(db_path)
        stats = {
            "bots_migrated": 0,
            "positions_migrated": 0,
            "fills_migrated": 0,
        }

        with connect_sqlite(db_path) as conn:
            # Check if legacy tables exist
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "grid_runtime_snapshots" not in tables:
                return stats

            rows = conn.execute("SELECT config_path, payload, updated_at FROM grid_runtime_snapshots").fetchall()

            for row in rows:
                config_path = row["config_path"]
                try:
                    payload = json.loads(row["payload"])
                except Exception:
                    continue

                if not isinstance(payload, dict):
                    continue

                bot_id = payload.get("bot_id") or os.path.splitext(os.path.basename(config_path))[0]
                ts = float(row["updated_at"])

                # 1. Backfill BotRecord
                bot = BotRecord(
                    bot_id=bot_id,
                    strategy_type=payload.get("strategy_type", "dca" if "dca" in config_path else "grid"),
                    symbol=payload.get("symbol", "unknown"),
                    exchange=payload.get("exchange", "unknown"),
                    account_mode=payload.get("account_mode", "paper"),
                    config_path=config_path,
                    status=payload.get("status", "running"),
                    created_at=ts,
                )
                repo.register_or_update_bot(bot)
                stats["bots_migrated"] += 1

                # 2. Backfill Position
                pos_data = payload.get("position") or {}
                profit_data = payload.get("profit") or {}
                # legacy grid 快照的 cycles 位於 filled_orders.cycles（無 top-level cycles，
                # 與 grid 重啟恢復邏輯一致）；DCA 快照則為 top-level cycles。
                filled_orders = payload.get("filled_orders") or {}
                cycles_raw = payload.get("cycles", payload.get("completed_cycles", filled_orders.get("cycles", 0)))
                pos = BotPosition(
                    bot_id=bot_id,
                    active_direction=payload.get("active_direction"),
                    quantity=float(pos_data.get("quantity", payload.get("grid_position", 0.0)) or 0.0),
                    average_cost=float(pos_data.get("average_cost", 0.0)) if pos_data.get("average_cost") else None,
                    total_cost=float(pos_data.get("total_cost", 0.0) or 0.0),
                    entry_price=float(payload.get("entry_price", 0.0)) if payload.get("entry_price") else None,
                    completed_cycles=int(cycles_raw or 0),
                    realized_profit=float(profit_data.get("realized", profit_data.get("grid_profit", 0.0)) or 0.0),
                    total_fees=float(profit_data.get("fees", 0.0) or 0.0),
                    updated_at=ts,
                )
                existing_pos = repo.get_position(bot_id)
                expected_ver = existing_pos.version if existing_pos else 1
                repo.update_position_occ(bot_id, pos, expected_ver)
                stats["positions_migrated"] += 1

                # 3. Backfill Fills
                fills = payload.get("fills") or []
                for i, f in enumerate(fills):
                    if isinstance(f, dict) and "price" in f and "quantity" in f:
                        cid = f.get("client_order_id", f"{bot_id}_{f.get('role', 'fill')}_{f.get('price')}")
                        fill_ts = float(f.get("executed_at", 0) or 0)
                        if fill_ts <= 0:
                            fill_ts = ts + i * 0.001
                        fill = TradeFill(
                            fill_id=f.get("fill_id", cid),
                            bot_id=bot_id,
                            exchange_order_id=f.get("order_id"),
                            client_order_id=cid,
                            side=f.get("side", "buy"),
                            role=f.get("role", "grid"),
                            price=float(f["price"]),
                            quantity=float(f["quantity"]),
                            fee=float(f.get("fee", 0.0) or 0.0),
                            fee_currency=f.get("fee_currency"),
                            realized_pnl=float(f.get("realized_pnl", 0.0) or 0.0),
                            executed_at=fill_ts,
                        )
                        repo.record_fill(fill)
                        stats["fills_migrated"] += 1

                # 4. Backfill bot_snapshots（P1-2 穩定 bot_id 恢復主路徑）。
                # 遷移後引擎重啟若尚未產生新 heartbeat，load_by_bot_id(bot_snapshots)
                # 需立即可命中，否則恢復只能走 legacy config_path/symbol 啟發式。
                # 僅在無既有快照時寫入，確保重跑冪等（不重複 append history/不 bump version）。
                if repo.load_snapshot_by_bot_id(bot_id) is None:
                    repo.save_snapshot(bot_id, config_path, payload, ts)

        logger.info(f"✅ [Migrator] 資料庫遷移與回補完成: {stats}")
        return stats


if __name__ == "__main__":
    # If run standalone outside test, migrate production DB
    DatabaseMigrator.migrate()
