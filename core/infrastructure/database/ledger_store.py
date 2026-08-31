"""SQLAlchemy-backed ledger stores (Phase 2).

This module introduces the P2-1 LedgerStore abstraction:

* ``LedgerStore``      — typing.Protocol describing the port used by the
                         engine and web API.
* ``SQLAlchemyLedgerStore`` — common SQLAlchemy Core implementation shared by
                         SQLite and PostgreSQL.
* ``SqliteLedgerStore``    — default local store (single-host, zero-dependency).
* ``PostgresLedgerStore``  — production source of truth behind ``DATABASE_URL``.

The public surface intentionally mirrors the old ``LedgerRepository`` facade so
all existing DB tests and engine call sites keep working unchanged.
"""

from __future__ import annotations

import json
import os
import threading
import time
import typing
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.database.guard import EnvironmentGuard
from core.infrastructure.database.models import (
    BotIdentityEvent,
    BotPosition,
    BotRecord,
    TradeFill,
)
from core.logging import get_logger

logger = get_logger(__name__)

# ── SQLAlchemy (lazy import with a clear error / fallback) ──────────────────
try:  # pragma: no cover - exercised when SQLAlchemy is installed
    from sqlalchemy import (
        Column,
        Float,
        Index,
        Integer,
        MetaData,
        Numeric,
        String,
        Table,
        Text,
        create_engine,
        delete,
        func,
        select,
    )
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.pool import NullPool, StaticPool

    SQLALCHEMY_AVAILABLE = True
except Exception:  # pragma: no cover - graceful fallback when not installed
    SQLALCHEMY_AVAILABLE = False
    pg_insert = sqlite_insert = None  # type: ignore


class LedgerStoreError(RuntimeError):
    """Raised when a backend cannot satisfy a ledger operation."""


class LedgerIntegrityError(RuntimeError):
    """P1-6 / ledger integrity violation (e.g. writing fills for unknown bot)."""


class LedgerStore(typing.Protocol):
    """Ledger write/read port used by the engine and web API."""

    def initialize(self) -> None: ...
    def register_or_update_bot(self, bot: BotRecord) -> None: ...
    def record_identity_event(
        self,
        bot_id: str,
        event_type: str,
        config_path: Optional[str] = None,
        old_bot_id: Optional[str] = None,
        new_bot_id: Optional[str] = None,
        recorded_at: Optional[float] = None,
    ) -> None: ...
    def get_identity_events(self, bot_id: str, limit: int = 100) -> List[BotIdentityEvent]: ...
    def get_bot(self, bot_id: str) -> Optional[BotRecord]: ...
    def get_bot_by_config(self, config_path: str) -> Optional[BotRecord]: ...
    def get_all_bots(self) -> List[BotRecord]: ...
    def record_fill(self, fill: TradeFill) -> None: ...
    def get_fills(self, bot_id: str, limit: int = 500, since: float = 0.0) -> List[TradeFill]: ...
    def get_position(self, bot_id: str) -> Optional[BotPosition]: ...
    def update_position_occ(self, bot_id: str, position: BotPosition, expected_version: int) -> bool: ...
    def rehydrate_position_from_fills(self, bot_id: str) -> BotPosition: ...
    def save_snapshot(self, bot_id: str, config_path: str, stats: dict, timestamp: Optional[float] = None) -> float: ...
    def load_all_snapshots(self) -> Dict[str, Tuple[dict, float]]: ...
    def load_snapshot_by_bot_id(self, bot_id: str) -> Optional[Tuple[dict, float]]: ...
    def load_all_snapshots_by_bot_id(self) -> Dict[str, Tuple[dict, float]]: ...
    def load_snapshot_history(self, config_path: str, since: float, limit: int = 1000) -> List[Tuple[dict, float]]: ...
    def remove_snapshot(self, config_path: str) -> None: ...


# ── Metadata / Core schema (mirrors schema.sql) ──────────────────────────────
meta = MetaData()

bots = Table(
    "bots",
    meta,
    Column("bot_id", String(255), primary_key=True),
    Column("strategy_type", String(64), nullable=False),
    Column("symbol", String(255), nullable=False),
    Column("exchange", String(64), nullable=False),
    Column("account_mode", String(32), nullable=False),
    Column("config_path", String(1024), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("archived_at", Float, nullable=True),
)

trade_fills = Table(
    "trade_fills",
    meta,
    Column("fill_id", String(512), primary_key=True),
    Column("bot_id", String(255), nullable=False),
    Column("exchange_order_id", String(512), nullable=True),
    Column("client_order_id", String(512), nullable=False),
    Column("side", String(16), nullable=False),
    Column("role", String(32), nullable=False),
    Column("price", Numeric(38, 18), nullable=False),
    Column("quantity", Numeric(38, 18), nullable=False),
    Column("fee", Numeric(38, 18), nullable=False, server_default="0"),
    Column("fee_currency", String(64), nullable=True),
    Column("realized_pnl", Numeric(38, 18), nullable=False, server_default="0"),
    Column("executed_at", Float, nullable=False),
)

bot_positions = Table(
    "bot_positions",
    meta,
    Column("bot_id", String(255), primary_key=True),
    Column("active_direction", String(16), nullable=True),
    Column("quantity", Numeric(38, 18), nullable=False, server_default="0"),
    Column("average_cost", Numeric(38, 18), nullable=True),
    Column("total_cost", Numeric(38, 18), nullable=False, server_default="0"),
    Column("entry_price", Numeric(38, 18), nullable=True),
    Column("completed_cycles", Integer, nullable=False, server_default="0"),
    Column("realized_profit", Numeric(38, 18), nullable=False, server_default="0"),
    Column("total_fees", Numeric(38, 18), nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", Float, nullable=False),
)

bot_snapshots = Table(
    "bot_snapshots",
    meta,
    Column("bot_id", String(255), primary_key=True),
    Column("config_path", String(1024), nullable=True),
    Column("payload", Text, nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", Float, nullable=False),
)

bot_snapshot_history = Table(
    "bot_snapshot_history",
    meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bot_id", String(255), nullable=False),
    Column("config_path", String(1024), nullable=True),
    Column("payload", Text, nullable=False),
    Column("recorded_at", Float, nullable=False),
)

bot_identity_events = Table(
    "bot_identity_events",
    meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bot_id", String(255), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("config_path", String(1024), nullable=True),
    Column("old_bot_id", String(255), nullable=True),
    Column("new_bot_id", String(255), nullable=True),
    Column("recorded_at", Float, nullable=False),
)

grid_runtime_snapshots = Table(
    "grid_runtime_snapshots",
    meta,
    Column("config_path", String(1024), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

grid_runtime_history = Table(
    "grid_runtime_history",
    meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("config_path", String(1024), nullable=False),
    Column("payload", Text, nullable=False),
    Column("recorded_at", Float, nullable=False),
)

Index("idx_trade_fills_bot_time", trade_fills.c.bot_id, trade_fills.c.executed_at.desc())
Index("idx_trade_fills_client_order", trade_fills.c.client_order_id)
Index("idx_bots_config_path", bots.c.config_path)
Index("idx_snapshot_history_bot_time", bot_snapshot_history.c.bot_id, bot_snapshot_history.c.recorded_at.desc())
Index("idx_snapshot_history_path_time", bot_snapshot_history.c.config_path, bot_snapshot_history.c.recorded_at.desc())
Index("idx_identity_events_bot_time", bot_identity_events.c.bot_id, bot_identity_events.c.recorded_at.desc())
Index("idx_legacy_runtime_history", grid_runtime_history.c.config_path, grid_runtime_history.c.recorded_at.desc())


# ── Monotonic guard helpers (P1-4) ─────────────────────────────────────────────
def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _monotonic_metrics(payload: dict) -> Dict[str, Optional[float]]:
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


def is_postgres_url(url: Optional[str]) -> bool:
    return bool(url and url.strip().lower().startswith(("postgres://", "postgresql://")))


def get_database_url(database_url: Optional[str] = None) -> Optional[str]:
    return database_url or os.getenv("DATABASE_URL") or None


def _upsert_insert(table: Table, dialect: str, values: dict, conflict_cols: list[str], update: dict):
    """Return a dialect-aware INSERT ... ON CONFLICT statement."""
    if dialect == "postgresql":
        return pg_insert(table).values(**values).on_conflict_do_update(
            index_elements=conflict_cols, set_=update
        )
    if dialect == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_update(
            index_elements=conflict_cols, set_=update
        )
    raise LedgerStoreError(f"Unsupported SQLAlchemy dialect: {dialect}")


def _ignore_insert(table: Table, dialect: str, values: dict, conflict_cols: list[str]):
    if dialect == "postgresql":
        return pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict_cols)
    if dialect == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict_cols)
    raise LedgerStoreError(f"Unsupported SQLAlchemy dialect: {dialect}")


class SQLAlchemyLedgerStore:
    """Shared SQLAlchemy Core ledger implementation."""

    def __init__(self, engine: "Engine"):
        if not SQLALCHEMY_AVAILABLE:  # pragma: no cover
            raise LedgerStoreError("SQLAlchemy is not installed; install requirements.txt to use this store.")
        self._engine = engine
        self._dialect = engine.dialect.name
        self._lock = threading.Lock()
        self.monotonic_guard_enabled = True
        self._initialized = False

    # ── schema / lifecycle ──────────────────────────────────────────────────
    def initialize(self) -> None:
        meta.create_all(self._engine)
        self._engine.dispose(close=False)
        self._initialized = True

    def _ensure_ready(self) -> None:
        """Recreate schema if the underlying DB file disappeared (test isolation)."""
        if not self._initialized:
            self.initialize()

    def _begin(self):
        self._ensure_ready()
        return self._engine.begin()

    def _ro(self):
        self._ensure_ready()
        return self._engine.connect()

    def _connect(self) -> Any:
        return self._engine.connect()

    @property
    def database_url(self) -> str:
        return str(self._engine.url)

    # ── bots / identity audit ───────────────────────────────────────────────
    def _bot_values(self, bot: BotRecord) -> dict:
        return {
            "bot_id": bot.bot_id,
            "strategy_type": bot.strategy_type,
            "symbol": bot.symbol,
            "exchange": bot.exchange,
            "account_mode": bot.account_mode,
            "config_path": bot.config_path,
            "status": bot.status,
            "created_at": bot.created_at,
            "archived_at": bot.archived_at,
        }

    def register_or_update_bot(self, bot: BotRecord) -> None:
        with self._lock, self._begin() as conn:
            existing = conn.execute(
                select(bots.c.config_path).where(bots.c.bot_id == bot.bot_id)
            ).fetchone()
            values = self._bot_values(bot)
            update = {
                "strategy_type": _literal(bot.strategy_type),
                "symbol": _literal(bot.symbol),
                "exchange": _literal(bot.exchange),
                "account_mode": _literal(bot.account_mode),
                "config_path": _literal(bot.config_path),
                "status": _literal(bot.status),
                "archived_at": _literal(bot.archived_at),
            }
            if self._dialect == "sqlite":
                update["config_path"] = _coalesce_literal(bot.config_path, bots.c.config_path)
            elif self._dialect == "postgresql":
                update["config_path"] = func.coalesce(_literal(bot.config_path), bots.c.config_path)
            stmt = _upsert_insert(bots, self._dialect, values, ["bot_id"], update)
            conn.execute(stmt)

            if existing is None:
                self._record_identity_event_conn(
                    conn, bot.bot_id, "created",
                    config_path=bot.config_path,
                    recorded_at=bot.created_at or time.time(),
                )
            elif bot.config_path and existing.config_path != bot.config_path:
                self._record_identity_event_conn(
                    conn, bot.bot_id, "config_linked",
                    config_path=bot.config_path,
                    recorded_at=time.time(),
                )

    @staticmethod
    def _record_identity_event_conn(
        conn: Connection,
        bot_id: str,
        event_type: str,
        config_path: Optional[str] = None,
        old_bot_id: Optional[str] = None,
        new_bot_id: Optional[str] = None,
        recorded_at: Optional[float] = None,
    ) -> None:
        ts = recorded_at if recorded_at is not None else time.time()
        conn.execute(
            bot_identity_events.insert().values(
                bot_id=bot_id,
                event_type=event_type,
                config_path=config_path,
                old_bot_id=old_bot_id,
                new_bot_id=new_bot_id,
                recorded_at=ts,
            )
        )

    def record_identity_event(
        self,
        bot_id: str,
        event_type: str,
        config_path: Optional[str] = None,
        old_bot_id: Optional[str] = None,
        new_bot_id: Optional[str] = None,
        recorded_at: Optional[float] = None,
    ) -> None:
        with self._lock, self._begin() as conn:
            self._record_identity_event_conn(
                conn, bot_id, event_type, config_path, old_bot_id, new_bot_id, recorded_at
            )

    def get_identity_events(self, bot_id: str, limit: int = 100) -> List[BotIdentityEvent]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(
                select(bot_identity_events)
                .where(bot_identity_events.c.bot_id == bot_id)
                .order_by(bot_identity_events.c.recorded_at.asc(), bot_identity_events.c.id.asc())
                .limit(limit)
            ).all()
        return [
            BotIdentityEvent(
                bot_id=r.bot_id,
                event_type=r.event_type,
                config_path=r.config_path,
                old_bot_id=r.old_bot_id,
                new_bot_id=r.new_bot_id,
                recorded_at=float(r.recorded_at),
            )
            for r in rows
        ]

    def get_bot(self, bot_id: str) -> Optional[BotRecord]:
        with self._lock, self._ro() as conn:
            row = conn.execute(select(bots).where(bots.c.bot_id == bot_id)).fetchone()
        if not row:
            return None
        return self._bot_from_row(row)

    def get_bot_by_config(self, config_path: str) -> Optional[BotRecord]:
        with self._lock, self._ro() as conn:
            row = conn.execute(
                select(bots)
                .where(bots.c.config_path == config_path)
                .order_by(bots.c.created_at.desc())
                .limit(1)
            ).fetchone()
        if not row:
            return None
        return self._bot_from_row(row)

    def get_all_bots(self) -> List[BotRecord]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(select(bots).order_by(bots.c.created_at.asc())).all()
        return [self._bot_from_row(r) for r in rows]

    @staticmethod
    def _bot_from_row(row: Any) -> BotRecord:
        return BotRecord(
            bot_id=row.bot_id,
            strategy_type=row.strategy_type,
            symbol=row.symbol,
            exchange=row.exchange,
            account_mode=row.account_mode,
            config_path=row.config_path,
            status=row.status,
            created_at=float(row.created_at),
            archived_at=float(row.archived_at) if row.archived_at is not None else None,
        )

    # ── immutable fills ─────────────────────────────────────────────────────
    def record_fill(self, fill: TradeFill) -> None:
        with self._lock, self._begin() as conn:
            exists = conn.execute(
                select(bots.c.bot_id).where(bots.c.bot_id == fill.bot_id).limit(1)
            ).fetchone()
            if not exists:
                raise LedgerIntegrityError(
                    f"無法記錄成交：bot_id={fill.bot_id!r} 尚未註冊於 bots 表。"
                    "請先呼叫 register_or_update_bot() 再寫入成交（P1-6）。"
                )
            stmt = _ignore_insert(
                trade_fills,
                self._dialect,
                {
                    "fill_id": fill.fill_id,
                    "bot_id": fill.bot_id,
                    "exchange_order_id": fill.exchange_order_id,
                    "client_order_id": fill.client_order_id,
                    "side": fill.side,
                    "role": fill.role,
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "fee": fill.fee,
                    "fee_currency": fill.fee_currency,
                    "realized_pnl": fill.realized_pnl,
                    "executed_at": fill.executed_at,
                },
                ["fill_id"],
            )
            conn.execute(stmt)

    def get_fills(self, bot_id: str, limit: int = 500, since: float = 0.0) -> List[TradeFill]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(
                select(trade_fills)
                .where(trade_fills.c.bot_id == bot_id, trade_fills.c.executed_at >= since)
                .order_by(trade_fills.c.executed_at.asc())
                .limit(limit)
            ).all()
        return [self._fill_from_row(r) for r in rows]

    @staticmethod
    def _fill_from_row(row: Any) -> TradeFill:
        return TradeFill(
            fill_id=row.fill_id,
            bot_id=row.bot_id,
            exchange_order_id=row.exchange_order_id,
            client_order_id=row.client_order_id,
            side=row.side,
            role=row.role,
            price=float(row.price),
            quantity=float(row.quantity),
            fee=float(row.fee),
            fee_currency=row.fee_currency,
            realized_pnl=float(row.realized_pnl),
            executed_at=float(row.executed_at),
        )

    # ── positions / OCC ─────────────────────────────────────────────────────
    def get_position(self, bot_id: str) -> Optional[BotPosition]:
        with self._lock, self._ro() as conn:
            row = conn.execute(select(bot_positions).where(bot_positions.c.bot_id == bot_id)).fetchone()
        if not row:
            return None
        return self._position_from_row(row)

    @staticmethod
    def _position_from_row(row: Any) -> BotPosition:
        return BotPosition(
            bot_id=row.bot_id,
            active_direction=row.active_direction,
            quantity=float(row.quantity),
            average_cost=float(row.average_cost) if row.average_cost is not None else None,
            total_cost=float(row.total_cost),
            entry_price=float(row.entry_price) if row.entry_price is not None else None,
            completed_cycles=int(row.completed_cycles),
            realized_profit=float(row.realized_profit),
            total_fees=float(row.total_fees),
            version=int(row.version),
            updated_at=float(row.updated_at),
        )

    def update_position_occ(self, bot_id: str, position: BotPosition, expected_version: int) -> bool:
        now = position.updated_at if position.updated_at > 0 else time.time()
        with self._lock, self._begin() as conn:
            exists = conn.execute(
                select(bots.c.bot_id).where(bots.c.bot_id == bot_id).limit(1)
            ).fetchone()
            if not exists:
                raise LedgerIntegrityError(
                    f"無法更新持倉：bot_id={bot_id!r} 尚未註冊於 bots 表。"
                    "請先呼叫 register_or_update_bot() 再更新持倉（P1-6）。"
                )
            existing = conn.execute(
                select(bot_positions.c.version).where(bot_positions.c.bot_id == bot_id)
            ).fetchone()
            if existing is None:
                conn.execute(
                    bot_positions.insert().values(
                        bot_id=bot_id,
                        active_direction=position.active_direction,
                        quantity=position.quantity,
                        average_cost=position.average_cost,
                        total_cost=position.total_cost,
                        entry_price=position.entry_price,
                        completed_cycles=position.completed_cycles,
                        realized_profit=position.realized_profit,
                        total_fees=position.total_fees,
                        version=1,
                        updated_at=now,
                    )
                )
                position.version = 1
                position.updated_at = now
                return True

            result = conn.execute(
                bot_positions.update()
                .where(
                    bot_positions.c.bot_id == bot_id,
                    bot_positions.c.version == expected_version,
                )
                .values(
                    active_direction=position.active_direction,
                    quantity=position.quantity,
                    average_cost=position.average_cost,
                    total_cost=position.total_cost,
                    entry_price=position.entry_price,
                    completed_cycles=position.completed_cycles,
                    realized_profit=position.realized_profit,
                    total_fees=position.total_fees,
                    version=bot_positions.c.version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount > 0:
                position.version = expected_version + 1
                position.updated_at = now
                return True
            return False

    def rehydrate_position_from_fills(self, bot_id: str) -> BotPosition:
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
        return BotPosition(
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

    # ── snapshots / monotonic / legacy dual-write ──────────────────────────
    def save_snapshot(self, bot_id: str, config_path: str, stats: dict, timestamp: Optional[float] = None) -> float:
        ts = timestamp if timestamp is not None else time.time()
        clean_stats = {k: v for k, v in stats.items() if k != "_monotonic_bypass"}
        payload_str = json.dumps(clean_stats, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock, self._begin() as conn:
            if self.monotonic_guard_enabled:
                existing = conn.execute(
                    select(bot_snapshots.c.payload).where(bot_snapshots.c.bot_id == bot_id)
                ).fetchone()
                if existing:
                    try:
                        old_payload = json.loads(existing.payload)
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

            snapshot_update = {
                "config_path": _literal(config_path),
                "payload": _literal(payload_str),
                "version": bot_snapshots.c.version + 1,
                "updated_at": _literal(ts),
            }
            snapshot_insert = _upsert_insert(
                bot_snapshots,
                self._dialect,
                {
                    "bot_id": bot_id,
                    "config_path": config_path,
                    "payload": payload_str,
                    "version": 1,
                    "updated_at": ts,
                },
                ["bot_id"],
                snapshot_update,
            )
            conn.execute(snapshot_insert)

            conn.execute(
                bot_snapshot_history.insert().values(
                    bot_id=bot_id, config_path=config_path, payload=payload_str, recorded_at=ts
                )
            )
            legacy_update = {"payload": _literal(payload_str), "updated_at": _literal(ts)}
            legacy_insert = _upsert_insert(
                grid_runtime_snapshots,
                self._dialect,
                {"config_path": config_path, "payload": payload_str, "updated_at": ts},
                ["config_path"],
                legacy_update,
            )
            conn.execute(legacy_insert)
            conn.execute(
                grid_runtime_history.insert().values(
                    config_path=config_path, payload=payload_str, recorded_at=ts
                )
            )
            conn.execute(delete(grid_runtime_history).where(grid_runtime_history.c.recorded_at < ts - 7 * 86400))
            conn.execute(delete(bot_snapshot_history).where(bot_snapshot_history.c.recorded_at < ts - 7 * 86400))
        return ts

    def load_all_snapshots(self) -> Dict[str, Tuple[dict, float]]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(select(grid_runtime_snapshots)).all()
        snapshots: Dict[str, Tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row.payload)
                if isinstance(payload, dict):
                    snapshots[row.config_path] = (payload, float(row.updated_at))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def load_snapshot_by_bot_id(self, bot_id: str) -> Optional[Tuple[dict, float]]:
        with self._lock, self._ro() as conn:
            row = conn.execute(
                select(bot_snapshots.c.payload, bot_snapshots.c.updated_at)
                .where(bot_snapshots.c.bot_id == bot_id)
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row.payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return (payload, float(row.updated_at))

    def load_all_snapshots_by_bot_id(self) -> Dict[str, Tuple[dict, float]]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(select(bot_snapshots.c.bot_id, bot_snapshots.c.payload, bot_snapshots.c.updated_at)).all()
        snapshots: Dict[str, Tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row.payload)
                if isinstance(payload, dict):
                    snapshots[row.bot_id] = (payload, float(row.updated_at))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def load_snapshot_history(self, config_path: str, since: float, limit: int = 1000) -> List[Tuple[dict, float]]:
        with self._lock, self._ro() as conn:
            rows = conn.execute(
                select(grid_runtime_history.c.payload, grid_runtime_history.c.recorded_at)
                .where(grid_runtime_history.c.config_path == config_path, grid_runtime_history.c.recorded_at >= since)
                .order_by(grid_runtime_history.c.recorded_at.asc())
                .limit(limit)
            ).all()
        result: List[Tuple[dict, float]] = []
        for row in rows:
            try:
                payload = json.loads(row.payload)
                if isinstance(payload, dict):
                    result.append((payload, float(row.recorded_at)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def remove_snapshot(self, config_path: str) -> None:
        with self._lock, self._begin() as conn:
            conn.execute(delete(grid_runtime_snapshots).where(grid_runtime_snapshots.c.config_path == config_path))
            conn.execute(delete(grid_runtime_history).where(grid_runtime_history.c.config_path == config_path))
            conn.execute(delete(bot_snapshots).where(bot_snapshots.c.config_path == config_path))
            conn.execute(delete(bot_snapshot_history).where(bot_snapshot_history.c.config_path == config_path))


def _literal(value: Any):
    """Pass a Python value directly into an ON CONFLICT UPDATE set_ clause."""
    return value


def _coalesce_literal(value: Optional[str], column) -> Any:
    """SQLite-friendly COALESCE(excluded.value, column)."""
    return func.coalesce(_literal(value), column)


class SqliteLedgerStore(SQLAlchemyLedgerStore):
    """SQLite implementation using SQLAlchemy Core (P2-1)."""

    def __init__(self, database_path: Optional[str] = None):
        safe_path = EnvironmentGuard.get_safe_database_path(database_path)
        self.database_path = safe_path
        if safe_path == ":memory:":
            engine = create_engine(
                "sqlite:///:memory:",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
        else:
            directory = os.path.dirname(os.path.abspath(safe_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            engine = create_engine(
                f"sqlite:///{safe_path}",
                poolclass=NullPool,
                connect_args={"check_same_thread": False},
            )
        super().__init__(engine)
        self.initialize()

    def _ensure_ready(self) -> None:
        """SQLite is the default; recreate parent dir + schema if needed."""
        if self.database_path == ":memory:":
            if not self._initialized:
                self.initialize()
            return
        directory = os.path.dirname(os.path.abspath(self.database_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not self._initialized or not os.path.exists(self.database_path):
            self.initialize()


class PostgresLedgerStore(SQLAlchemyLedgerStore):
    """PostgreSQL source-of-truth store (P2-2)."""

    def __init__(self, database_url: Optional[str] = None):
        url = get_database_url(database_url)
        if not is_postgres_url(url):
            raise LedgerStoreError("PostgresLedgerStore requires a postgresql:// DATABASE_URL.")
        engine = create_engine(
            url,
            pool_size=max(2, int(os.getenv("GRID_PG_POOL_SIZE", "5") or 5)),
            max_overflow=max(1, int(os.getenv("GRID_PG_MAX_OVERFLOW", "10") or 10)),
            pool_pre_ping=True,
        )
        super().__init__(engine)
        self.initialize()


def create_ledger_store(database_path: Optional[str] = None, database_url: Optional[str] = None):
    """Factory choosing SQLite (default) or PostgreSQL (when DATABASE_URL is set)."""
    url = get_database_url(database_url)
    if is_postgres_url(url):
        return PostgresLedgerStore(database_url=url)
    return SqliteLedgerStore(database_path=database_path)
