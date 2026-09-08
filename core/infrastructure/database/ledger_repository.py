"""Ledger repository facade (Phase 2).

Phase 1 added the stable-bot identity, fill ledger, monotonic guard and the
asynchronous writer.  Phase 2 moves the storage implementation behind the
``LedgerStore`` interface and introduces a SQLAlchemy-backed SQLite store plus
an optional PostgreSQL truth source.

This facade keeps the original ``LedgerRepository`` public surface and
singleton semantics, so the engine, Web API, migrators and tests keep working
without changing their call sites.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.database.guard import EnvironmentGuard
from core.infrastructure.database.ledger_store import (
    LedgerIntegrityError,
    LedgerStore,
    LedgerStoreError,
    create_ledger_store,
)
from core.infrastructure.database.models import (
    BotEvent,
    BotIdentityEvent,
    BotPosition,
    BotRecord,
    BotSnapshot,
    TradeFill,
)
from web_ui.backend.lifecycle import connect_sqlite

__all__ = [
    "LedgerIntegrityError",
    "LedgerRepository",
    "LedgerStore",
    "create_ledger_store",
]


class LedgerRepository:
    """Relational Ledger Repository with immutable fills and OCC.

    Delegate facade over the SQLAlchemy-backed store selected by the factory
    (SQLite by default; PostgreSQL when ``DATABASE_URL`` is set).
    """

    _instance: Optional["LedgerRepository"] = None
    _instance_lock = threading.Lock()

    # P1-4: 單調防護開關（可於測試/除錯時暫時關閉）
    MONOTONIC_GUARD_ENABLED = True

    def __init__(self, database_path: Optional[str] = None):
        self.database_path = EnvironmentGuard.get_safe_database_path(database_path)
        self._lock = threading.Lock()
        self._store: LedgerStore = create_ledger_store(database_path)
        self._store.monotonic_guard_enabled = self.MONOTONIC_GUARD_ENABLED

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

    @property
    def store(self) -> LedgerStore:
        """The active LedgerStore backend (SQLite or PostgreSQL)."""
        return self._store

    def _connect(self):
        """Back-compatible sqlite3 connection for local/semi-direct inspection.

        F3 fix: in PostgreSQL mode this raw-SQLite handle is a *ghost data
        source* (writes go to PG, inspection would read a local SQLite file).
        It is now rejected loudly; use ``repo.store`` / SQLAlchemy connections.
        """
        self._reject_raw_sqlite_in_postgres_mode()
        return connect_sqlite(self.database_path)

    def _reject_raw_sqlite_in_postgres_mode(self) -> None:
        from core.infrastructure.database.ledger_store import SqliteLedgerStore

        if not isinstance(self._store, SqliteLedgerStore):
            raise LedgerStoreError(
                "LedgerRepository._connect() 僅支援 SQLite 後端。"
                "目前為 PostgreSQL 真相源，請改用 repo.store（SQLAlchemy）讀寫，"
                "以免誤讀本機 SQLite 造成雙來源分歧（稽核 F3）。"
            )

    def _ensure_schema(self, connection=None) -> None:
        """Idempotent schema creation.

        A passed SQLite connection executes the original ``schema.sql`` for
        callers that inspect non-SQLAlchemy connections (kept for tests that
        reuse ``_connect``).  Otherwise the active SQLAlchemy store handles
        ``create_all``.
        """
        if connection is None:
            self._store.initialize()
            return
        self._reject_raw_sqlite_in_postgres_mode()
        if hasattr(connection, "executescript"):
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as f:
                    connection.executescript(f.read())

    # ══════════════════ 機器人實體管理 ══════════════════
    def register_or_update_bot(self, bot: BotRecord) -> None:
        self._store.register_or_update_bot(bot)

    def record_identity_event(
        self,
        bot_id: str,
        event_type: str,
        config_path: Optional[str] = None,
        old_bot_id: Optional[str] = None,
        new_bot_id: Optional[str] = None,
        recorded_at: Optional[float] = None,
    ) -> None:
        self._store.record_identity_event(
            bot_id, event_type, config_path, old_bot_id, new_bot_id, recorded_at
        )

    def get_identity_events(self, bot_id: str, limit: int = 100) -> List[BotIdentityEvent]:
        return self._store.get_identity_events(bot_id, limit)

    def get_bot(self, bot_id: str) -> Optional[BotRecord]:
        return self._store.get_bot(bot_id)

    def get_bot_by_config(self, config_path: str) -> Optional[BotRecord]:
        return self._store.get_bot_by_config(config_path)

    def get_all_bots(self) -> List[BotRecord]:
        return self._store.get_all_bots()

    # ══════════════════ 不可變成交流水 ══════════════════
    def record_fill(self, fill: TradeFill) -> None:
        self._store.record_fill(fill)

    def get_fills(self, bot_id: str, limit: int = 500, since: float = 0.0, order_desc: bool = False) -> List[TradeFill]:
        return self._store.get_fills(bot_id, limit=limit, since=since, order_desc=order_desc)

    # ══════════════════ P3-1 事件流 ══════════════════
    def append_event(
        self,
        bot_id: str,
        event_type: str,
        payload: Optional[dict] = None,
        created_at: Optional[float] = None,
        dedupe_key: Optional[str] = None,
    ) -> int:
        return self._store.append_event(bot_id, event_type, payload, created_at, dedupe_key)

    def get_events(self, bot_id: str, since_seq: int = 0, limit: int = 100000) -> List[BotEvent]:
        return self._store.get_events(bot_id, since_seq, limit)

    # ══════════════════ 持倉與 OCC 樂觀鎖 ══════════════════
    def get_position(self, bot_id: str) -> Optional[BotPosition]:
        return self._store.get_position(bot_id)

    def update_position_occ(self, bot_id: str, position: BotPosition, expected_version: int) -> bool:
        return self._store.update_position_occ(bot_id, position, expected_version)

    def rehydrate_position_from_fills(self, bot_id: str) -> BotPosition:
        return self._store.rehydrate_position_from_fills(bot_id)

    # ══════════════════ 快照與向下相容 ══════════════════
    def save_snapshot(
        self,
        bot_id: str,
        config_path: str,
        stats: dict,
        timestamp: Optional[float] = None,
        force_history: bool = False,
    ) -> float:
        return self._store.save_snapshot(bot_id, config_path, stats, timestamp, force_history=force_history)

    def load_all_snapshots(self) -> Dict[str, Tuple[dict, float]]:
        return self._store.load_all_snapshots()

    def load_snapshot_by_bot_id(self, bot_id: str) -> Optional[Tuple[dict, float]]:
        return self._store.load_snapshot_by_bot_id(bot_id)

    def load_all_snapshots_by_bot_id(self) -> Dict[str, Tuple[dict, float]]:
        return self._store.load_all_snapshots_by_bot_id()

    def load_snapshot_history(self, config_path: str, since: float, limit: int = 1000) -> List[Tuple[dict, float]]:
        return self._store.load_snapshot_history(config_path, since, limit)

    def load_history(self, bot_id_or_config: str, limit: int = 1000) -> List[Tuple[dict, float]]:
        return self._store.load_history(bot_id_or_config, limit=limit)

    def remove_snapshot(self, config_path: str) -> None:
        self._store.remove_snapshot(config_path)
