"""Small durable repository for dashboard runtime snapshots.

Acts as a backward-compatible adapter facade over the normalized LedgerRepository.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from core.infrastructure.database.guard import EnvironmentGuard
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotPosition, BotRecord, TradeFill
from core.infrastructure.identity import resolve_bot_id
from web_ui.backend.lifecycle import connect_sqlite


class RuntimeSnapshotRepository:
    _instance: Optional["RuntimeSnapshotRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        self.database_path = EnvironmentGuard.get_safe_database_path(database_path)
        if self.database_path != ":memory:":
            os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        self._ledger = LedgerRepository(self.database_path)
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "RuntimeSnapshotRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def _connect(self):
        return connect_sqlite(self.database_path)

    def _ensure_schema(self, connection) -> None:
        self._ledger._ensure_schema(connection)

    def _initialize(self) -> None:
        pass

    def save(self, config_path: str, stats: dict, updated_at: Optional[float] = None) -> float:
        timestamp = updated_at if updated_at is not None else time.time()
        # P1-1: 帳本 key 使用穩定 bot_id（優先 stats.bot_id → YAML metadata.bot_id → 檔名 stem）
        bot_id = stats.get("bot_id") or resolve_bot_id(config_path)

        # 1. Update bot registration
        bot_record = BotRecord(
            bot_id=bot_id,
            strategy_type=stats.get("strategy_type", "dca" if "dca" in config_path else "grid"),
            symbol=stats.get("symbol", "unknown"),
            exchange=stats.get("exchange", "unknown"),
            account_mode=stats.get("account_mode", "paper"),
            config_path=config_path,
            status=stats.get("status", "running"),
            created_at=timestamp,
        )
        self._ledger.register_or_update_bot(bot_record)

        # 2. Extract and record any discrete fills
        fills = stats.get("fills") or []
        for f in fills:
            if isinstance(f, dict) and "price" in f and "quantity" in f:
                cid = f.get("client_order_id", f"{bot_id}_{f.get('role', 'fill')}_{f.get('price')}")
                fill = TradeFill(
                    fill_id=f.get("fill_id", cid),
                    bot_id=bot_id,
                    exchange_order_id=f.get("order_id") or f.get("exchange_order_id"),
                    client_order_id=cid,
                    side=f.get("side", "buy"),
                    role=f.get("role", "grid"),
                    price=float(f["price"]),
                    quantity=float(f["quantity"]),
                    fee=float(f.get("fee", 0.0) or 0.0),
                    fee_currency=f.get("fee_currency"),
                    realized_pnl=float(f.get("realized_pnl", 0.0) or 0.0),
                    executed_at=float(f.get("executed_at", timestamp) or timestamp),
                )
                self._ledger.record_fill(fill)
                # P3-1: 同步寫入 canonical 事件流（fill，以 fill_id 冪等）
                try:
                    from core.infrastructure.database.bot_events import emit_fill_event
                    emit_fill_event(fill)
                except Exception:
                    pass  # 事件擷取不影響主路徑

        # 3. Update Position & Metrics
        pos_data = stats.get("position") or {}
        profit_data = stats.get("profit") or {}
        # grid heartbeat 的 cycles 位於 filled_orders.cycles（無 top-level cycles）；
        # DCA 為 top-level cycles。與 migrator/重啟恢復邏輯一致。
        filled_orders = stats.get("filled_orders") or {}
        cycles_raw = stats.get("cycles", stats.get("completed_cycles", filled_orders.get("cycles", 0)))
        pos = BotPosition(
            bot_id=bot_id,
            active_direction=stats.get("active_direction"),
            quantity=float(pos_data.get("quantity", stats.get("grid_position", 0.0)) or 0.0),
            average_cost=float(pos_data.get("average_cost", 0.0)) if pos_data.get("average_cost") else None,
            total_cost=float(pos_data.get("total_cost", 0.0) or 0.0),
            entry_price=float(stats.get("entry_price", 0.0)) if stats.get("entry_price") else None,
            completed_cycles=int(cycles_raw or 0),
            realized_profit=float(profit_data.get("realized", profit_data.get("grid_profit", 0.0)) or 0.0),
            total_fees=float(profit_data.get("fees", 0.0) or 0.0),
            updated_at=timestamp,
        )
        # Fetch existing version for OCC update
        existing_pos = self._ledger.get_position(bot_id)
        expected_ver = existing_pos.version if existing_pos else 1
        self._ledger.update_position_occ(bot_id, pos, expected_ver)

        # 4. Save snapshots & history
        return self._ledger.save_snapshot(bot_id, config_path, stats, timestamp)

    def load_all(self) -> dict[str, tuple[dict, float]]:
        return self._ledger.load_all_snapshots()

    def load_by_bot_id(self, bot_id: str) -> Optional[tuple[dict, float]]:
        """P1-2: 以穩定 bot_id 讀取最新快照（bot_snapshots 表，bot_id 為 key）。"""
        return self._ledger.load_snapshot_by_bot_id(bot_id)

    def load_all_by_bot_id(self) -> dict[str, tuple[dict, float]]:
        """P1-2: 以 bot_id 為 key 讀取全部最新快照。"""
        return self._ledger.load_all_snapshots_by_bot_id()

    def history(self, config_path: str, since: float, limit: int = 1000) -> list[tuple[dict, float]]:
        return self._ledger.load_snapshot_history(config_path, since, limit)

    def remove(self, config_path: str) -> None:
        self._ledger.remove_snapshot(config_path)
