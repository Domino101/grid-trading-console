"""Ledger repository providing relational persistence, immutable trade fills,
and optimistic concurrency control (OCC) for trading bots.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.database.guard import EnvironmentGuard
from core.infrastructure.database.models import BotPosition, BotRecord, BotSnapshot, TradeFill
from core.logging import get_logger
from web_ui.backend.lifecycle import connect_sqlite

logger = get_logger(__name__)


def _num(value: Any) -> Optional[float]:
    """Coerce a value to float for monotonic comparison; non-numeric/None/bool → None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _monotonic_metrics(payload: dict) -> Dict[str, Optional[float]]:
    """Extract strictly-nondecreasing cumulative counters from a snapshot payload.

    只挑選「嚴格單調遞增」的計數器；已實現盈虧會因虧損循環/停損合法下降，
    故刻意排除，避免誤判。
    """
    filled = payload.get("filled_orders")
    profit = payload.get("profit")
    if not isinstance(filled, dict):
        filled = {}
    if not isinstance(profit, dict):
        profit = {}

    def first(*keys: str) -> Optional[float]:
        for key in keys:
            value = _num(payload.get(key))
            if value is not None:
                return value
        return None

    cycles = first("cycles", "cycle_count", "completed_cycles")
    if cycles is None:
        cycles = _num(filled.get("cycles"))
    buy = _num(filled.get("buy"))
    if buy is None:
        buy = _num(payload.get("filled_buy_count"))
    sell = _num(filled.get("sell"))
    if sell is None:
        sell = _num(payload.get("filled_sell_count"))
    fees = _num(profit.get("fees"))
    if fees is None:
        fees = _num(payload.get("total_fees"))
    return {"cycles": cycles, "buy": buy, "sell": sell, "fees": fees}


def _monotonic_regression(old: dict, new: dict) -> List[str]:
    """Return the counter keys that regressed (new < old); empty list = no regression."""
    old_m = _monotonic_metrics(old)
    new_m = _monotonic_metrics(new)
    regressed: List[str] = []
    for key, old_value in old_m.items():
        new_value = new_m.get(key)
        if old_value is None or new_value is None:
            continue
        if new_value < old_value - 1e-9:
            regressed.append(key)
    return regressed


class LedgerRepository:
    """Relational Ledger Repository with immutable fills and OCC."""

    _instance: Optional["LedgerRepository"] = None
    _instance_lock = threading.Lock()

    # P1-4: 單調防護開關（可於測試/除錯時暫時關閉）
    MONOTONIC_GUARD_ENABLED = True

    def __init__(self, database_path: Optional[str] = None):
        self.database_path = EnvironmentGuard.get_safe_database_path(database_path)
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "LedgerRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self.database_path)
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        else:
            # Fallback basic schema creation
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bots (
                    bot_id TEXT PRIMARY KEY,
                    strategy_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    account_mode TEXT NOT NULL,
                    config_path TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    archived_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_fills (
                    fill_id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL,
                    exchange_order_id TEXT,
                    client_order_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    role TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    fee_currency TEXT,
                    realized_pnl REAL DEFAULT 0,
                    executed_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_positions (
                    bot_id TEXT PRIMARY KEY,
                    active_direction TEXT,
                    quantity REAL NOT NULL DEFAULT 0,
                    average_cost REAL,
                    total_cost REAL DEFAULT 0,
                    entry_price REAL,
                    completed_cycles INTEGER DEFAULT 0,
                    realized_profit REAL DEFAULT 0,
                    total_fees REAL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                )
            """)

    def _initialize(self) -> None:
        with self._lock:
            with connect_sqlite(self.database_path) as conn:
                self._ensure_schema(conn)

    # ══════════════════ 機器人實體管理 ══════════════════

    def register_or_update_bot(self, bot: BotRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bots(bot_id, strategy_type, symbol, exchange, account_mode, config_path, status, created_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    strategy_type=excluded.strategy_type,
                    symbol=excluded.symbol,
                    exchange=excluded.exchange,
                    account_mode=excluded.account_mode,
                    config_path=coalesce(excluded.config_path, bots.config_path),
                    status=excluded.status,
                    archived_at=excluded.archived_at
                """,
                (
                    bot.bot_id,
                    bot.strategy_type,
                    bot.symbol,
                    bot.exchange,
                    bot.account_mode,
                    bot.config_path,
                    bot.status,
                    bot.created_at,
                    bot.archived_at,
                ),
            )

    def get_bot(self, bot_id: str) -> Optional[BotRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM bots WHERE bot_id = ?", (bot_id,)).fetchone()
            if not row:
                return None
            return BotRecord(
                bot_id=row["bot_id"],
                strategy_type=row["strategy_type"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                account_mode=row["account_mode"],
                config_path=row["config_path"],
                status=row["status"],
                created_at=row["created_at"],
                archived_at=row["archived_at"],
            )

    def get_bot_by_config(self, config_path: str) -> Optional[BotRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM bots WHERE config_path = ? ORDER BY created_at DESC LIMIT 1", (config_path,)).fetchone()
            if not row:
                return None
            return BotRecord(
                bot_id=row["bot_id"],
                strategy_type=row["strategy_type"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                account_mode=row["account_mode"],
                config_path=row["config_path"],
                status=row["status"],
                created_at=row["created_at"],
                archived_at=row["archived_at"],
            )

    # ══════════════════ 不可變成交流水 ══════════════════

    def record_fill(self, fill: TradeFill) -> None:
        """Append-only recording of a trade execution fill."""
        with self._lock, self._connect() as conn:
            # Ensure parent bot entry exists or placeholder
            bot_exists = conn.execute("SELECT 1 FROM bots WHERE bot_id = ?", (fill.bot_id,)).fetchone()
            if not bot_exists:
                conn.execute(
                    """INSERT OR IGNORE INTO bots(bot_id, strategy_type, symbol, exchange, account_mode, status, created_at)
                       VALUES (?, 'unknown', 'unknown', 'unknown', 'unknown', 'running', ?)""",
                    (fill.bot_id, fill.executed_at),
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO trade_fills(
                    fill_id, bot_id, exchange_order_id, client_order_id,
                    side, role, price, quantity, fee, fee_currency, realized_pnl, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.fill_id,
                    fill.bot_id,
                    fill.exchange_order_id,
                    fill.client_order_id,
                    fill.side,
                    fill.role,
                    fill.price,
                    fill.quantity,
                    fill.fee,
                    fill.fee_currency,
                    fill.realized_pnl,
                    fill.executed_at,
                ),
            )

    def get_fills(self, bot_id: str, limit: int = 500, since: float = 0.0) -> List[TradeFill]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trade_fills
                WHERE bot_id = ? AND executed_at >= ?
                ORDER BY executed_at ASC LIMIT ?
                """,
                (bot_id, since, limit),
            ).fetchall()
            return [
                TradeFill(
                    fill_id=r["fill_id"],
                    bot_id=r["bot_id"],
                    exchange_order_id=r["exchange_order_id"],
                    client_order_id=r["client_order_id"],
                    side=r["side"],
                    role=r["role"],
                    price=float(r["price"]),
                    quantity=float(r["quantity"]),
                    fee=float(r["fee"]),
                    fee_currency=r["fee_currency"],
                    realized_pnl=float(r["realized_pnl"]),
                    executed_at=float(r["executed_at"]),
                )
                for r in rows
            ]

    # ══════════════════ 持倉與 OCC 樂觀鎖 ══════════════════

    def get_position(self, bot_id: str) -> Optional[BotPosition]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM bot_positions WHERE bot_id = ?", (bot_id,)).fetchone()
            if not row:
                return None
            return BotPosition(
                bot_id=row["bot_id"],
                active_direction=row["active_direction"],
                quantity=float(row["quantity"]),
                average_cost=float(row["average_cost"]) if row["average_cost"] is not None else None,
                total_cost=float(row["total_cost"]),
                entry_price=float(row["entry_price"]) if row["entry_price"] is not None else None,
                completed_cycles=int(row["completed_cycles"]),
                realized_profit=float(row["realized_profit"]),
                total_fees=float(row["total_fees"]),
                version=int(row["version"]),
                updated_at=float(row["updated_at"]),
            )

    def update_position_occ(self, bot_id: str, position: BotPosition, expected_version: int) -> bool:
        """Update position with Optimistic Concurrency Control (OCC).

        Returns True if update succeeded, False if version mismatch.
        """
        now = position.updated_at if position.updated_at > 0 else time.time()
        with self._lock, self._connect() as conn:
            # Ensure bot exists
            conn.execute(
                """INSERT OR IGNORE INTO bots(bot_id, strategy_type, symbol, exchange, account_mode, status, created_at)
                   VALUES (?, 'unknown', 'unknown', 'unknown', 'unknown', 'running', ?)""",
                (bot_id, now),
            )

            # Check if record exists
            existing = conn.execute("SELECT version FROM bot_positions WHERE bot_id = ?", (bot_id,)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO bot_positions(
                        bot_id, active_direction, quantity, average_cost, total_cost,
                        entry_price, completed_cycles, realized_profit, total_fees, version, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        bot_id,
                        position.active_direction,
                        position.quantity,
                        position.average_cost,
                        position.total_cost,
                        position.entry_price,
                        position.completed_cycles,
                        position.realized_profit,
                        position.total_fees,
                        now,
                    ),
                )
                position.version = 1
                position.updated_at = now
                return True

            cursor = conn.execute(
                """
                UPDATE bot_positions SET
                    active_direction = ?,
                    quantity = ?,
                    average_cost = ?,
                    total_cost = ?,
                    entry_price = ?,
                    completed_cycles = ?,
                    realized_profit = ?,
                    total_fees = ?,
                    version = version + 1,
                    updated_at = ?
                WHERE bot_id = ? AND version = ?
                """,
                (
                    position.active_direction,
                    position.quantity,
                    position.average_cost,
                    position.total_cost,
                    position.entry_price,
                    position.completed_cycles,
                    position.realized_profit,
                    position.total_fees,
                    now,
                    bot_id,
                    expected_version,
                ),
            )
            if cursor.rowcount > 0:
                position.version = expected_version + 1
                position.updated_at = now
                return True
            return False

    def rehydrate_position_from_fills(self, bot_id: str) -> BotPosition:
        """Reconstruct the true position and cycle metrics from immutable trade fills."""
        fills = self.get_fills(bot_id, limit=10000)
        net_qty = 0.0
        total_cost = 0.0
        realized_profit = 0.0
        total_fees = 0.0
        cycles = 0
        direction = None
        entry_price = None

        for f in fills:
            total_fees += f.fee
            realized_profit += f.realized_pnl
            if f.role == "tp":
                cycles += 1
            if f.role == "base" and entry_price is None:
                entry_price = f.price
                direction = "long" if f.side == "buy" else "short"

            if f.side == "buy":
                net_qty += f.quantity
                total_cost += f.price * f.quantity
            else:
                net_qty -= f.quantity
                total_cost -= f.price * f.quantity

        avg_cost = (total_cost / net_qty) if net_qty > 0.00000001 else None

        pos = BotPosition(
            bot_id=bot_id,
            active_direction=direction,
            quantity=max(0.0, net_qty),
            average_cost=avg_cost,
            total_cost=max(0.0, total_cost),
            entry_price=entry_price,
            completed_cycles=cycles,
            realized_profit=realized_profit,
            total_fees=total_fees,
            updated_at=time.time(),
        )
        return pos

    # ══════════════════ 快照與向下相容 ══════════════════

    def save_snapshot(self, bot_id: str, config_path: str, stats: dict, timestamp: Optional[float] = None) -> float:
        ts = timestamp if timestamp is not None else time.time()
        # P1-4: 剝除內部控制旗標（不得進入持久化 payload）
        clean_stats = {k: v for k, v in stats.items() if k != "_monotonic_bypass"}
        payload_str = json.dumps(clean_stats, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock, self._connect() as conn:
            # ── P1-4 單調防護：防止「恢復失敗的新引擎」以 0 值覆寫累積計數 ──
            if self.MONOTONIC_GUARD_ENABLED:
                existing = conn.execute(
                    "SELECT payload FROM bot_snapshots WHERE bot_id = ?", (bot_id,)
                ).fetchone()
                if existing:
                    try:
                        old_payload = json.loads(existing["payload"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        old_payload = None
                    if isinstance(old_payload, dict):
                        bypass = bool(stats.get("_monotonic_bypass")) or bool(stats.get("reset_id"))
                        regressed = _monotonic_regression(old_payload, stats)
                        if regressed and not bypass:
                            logger.warning(
                                f"⚠️ [MonotonicGuard] 拒絕覆寫快照（累計計數倒退）: "
                                f"bot_id={bot_id} keys={','.join(regressed)} "
                                f"（若為合法重置，須帶 _monotonic_bypass/reset_id 標記）"
                            )
                            return ts

            # 1. Update bot_snapshots
            conn.execute(
                """
                INSERT INTO bot_snapshots(bot_id, config_path, payload, version, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    config_path=excluded.config_path,
                    payload=excluded.payload,
                    version=bot_snapshots.version + 1,
                    updated_at=excluded.updated_at
                """,
                (bot_id, config_path, payload_str, ts),
            )
            # 2. Append history
            conn.execute(
                """INSERT INTO bot_snapshot_history(bot_id, config_path, payload, recorded_at) VALUES (?, ?, ?, ?)""",
                (bot_id, config_path, payload_str, ts),
            )
            # 3. Dual write legacy compatibility tables
            conn.execute(
                """
                INSERT INTO grid_runtime_snapshots(config_path, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(config_path) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (config_path, payload_str, ts),
            )
            conn.execute(
                "INSERT INTO grid_runtime_history(config_path, payload, recorded_at) VALUES (?, ?, ?)",
                (config_path, payload_str, ts),
            )
            # Prune old legacy history older than 7 days
            conn.execute("DELETE FROM grid_runtime_history WHERE recorded_at < ?", (ts - 7 * 86400,))
            conn.execute("DELETE FROM bot_snapshot_history WHERE recorded_at < ?", (ts - 7 * 86400,))
        return ts

    def load_all_snapshots(self) -> Dict[str, Tuple[dict, float]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT config_path, payload, updated_at FROM grid_runtime_snapshots").fetchall()
        snapshots: Dict[str, Tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict):
                    snapshots[row["config_path"]] = (payload, float(row["updated_at"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def load_snapshot_by_bot_id(self, bot_id: str) -> Optional[Tuple[dict, float]]:
        """P1-2: 以穩定 bot_id 讀取最新快照（bot_snapshots 以 bot_id 為 key）。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM bot_snapshots WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return (payload, float(row["updated_at"]))

    def load_all_snapshots_by_bot_id(self) -> Dict[str, Tuple[dict, float]]:
        """P1-2: 以 bot_id 為 key 讀取全部最新快照。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT bot_id, payload, updated_at FROM bot_snapshots").fetchall()
        snapshots: Dict[str, Tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict):
                    snapshots[row["bot_id"]] = (payload, float(row["updated_at"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def load_snapshot_history(self, config_path: str, since: float, limit: int = 1000) -> List[Tuple[dict, float]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload, recorded_at FROM grid_runtime_history
                WHERE config_path = ? AND recorded_at >= ? ORDER BY recorded_at ASC LIMIT ?
                """,
                (config_path, since, limit),
            ).fetchall()
        result: List[Tuple[dict, float]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict):
                    result.append((payload, float(row["recorded_at"])))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def remove_snapshot(self, config_path: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM grid_runtime_snapshots WHERE config_path = ?", (config_path,))
            conn.execute("DELETE FROM grid_runtime_history WHERE config_path = ?", (config_path,))
            conn.execute("DELETE FROM bot_snapshots WHERE config_path = ?", (config_path,))
            conn.execute("DELETE FROM bot_snapshot_history WHERE config_path = ?", (config_path,))
