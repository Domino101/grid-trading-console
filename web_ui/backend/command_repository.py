"""Durable command journal for grid lifecycle requests."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from lifecycle import connect_sqlite
from runtime_repository import RuntimeSnapshotRepository


class GridCommandRepository:
    _instance: Optional["GridCommandRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        self.database_path = database_path or RuntimeSnapshotRepository.instance().database_path
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "GridCommandRepository":
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
        connection = connect_sqlite(self.database_path)
        self._ensure_schema(connection)
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS grid_commands (
                command_id TEXT PRIMARY KEY,
                grid_id TEXT NOT NULL,
                config_path TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                reason TEXT,
                detail TEXT,
                idempotency_key TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(grid_commands)")}
        if "idempotency_key" not in columns:
            connection.execute("ALTER TABLE grid_commands ADD COLUMN idempotency_key TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_grid_commands_grid_created "
            "ON grid_commands(grid_id, created_at DESC)"
        )
        connection.execute("DROP INDEX IF EXISTS idx_grid_commands_idempotency")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_commands_idempotency "
            "ON grid_commands(grid_id, action, idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS grid_command_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL,
                grid_id TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                created_at REAL NOT NULL
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_events_lookup "
            "ON grid_command_events(command_id, created_at ASC)"
        )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS grid_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                occurred_at REAL NOT NULL
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_grid_events_grid_time "
            "ON grid_events(grid_id, occurred_at DESC)"
        )

    def _initialize(self) -> None:
        with connect_sqlite(self.database_path) as connection:
            self._ensure_schema(connection)

    def create(
        self,
        grid_id: str,
        config_path: str,
        action: str,
        source: str,
        reason: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        command_id = str(uuid.uuid4())
        now = time.time()
        command = {
            "command_id": command_id,
            "grid_id": grid_id,
            "config_path": config_path,
            "action": action,
            "status": "accepted",
            "source": source,
            "reason": reason,
            "detail": None,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO grid_commands(
                       command_id, grid_id, config_path, action, status, source, reason,
                       detail, idempotency_key, created_at, updated_at
                   ) VALUES(
                       :command_id, :grid_id, :config_path, :action, :status, :source, :reason,
                       :detail, :idempotency_key, :created_at, :updated_at
                   )""",
                command,
            )
        return command

    def update_status(self, command_id: str, status: str, detail: Optional[str] = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE grid_commands SET status = ?, detail = ?, updated_at = ? WHERE command_id = ?",
                (status, detail, time.time(), command_id),
            )

    def get(self, command_id: str) -> Optional[dict[str, Any]]:
        self.mark_timeouts()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM grid_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return self._row_to_dict(row)

    def get_by_idempotency_key(self, action: str, key: str, grid_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if grid_id:
                row = connection.execute(
                    "SELECT * FROM grid_commands WHERE grid_id = ? AND action = ? AND idempotency_key = ?",
                    (grid_id, action, key),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM grid_commands WHERE action = ? AND idempotency_key = ?",
                    (action, key),
                ).fetchone()
        return self._row_to_dict(row)

    def mark_timeouts(self, timeout_seconds: int = 90) -> None:
        deadline = time.time() - timeout_seconds
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE grid_commands SET status = 'timed_out',
                   detail = '等待引擎確認逾時', updated_at = ?
                   WHERE status = 'pending_engine' AND updated_at < ?""",
                (time.time(), deadline),
            )

    def latest_for_grid(self, grid_id: str) -> Optional[dict[str, Any]]:
        self.mark_timeouts()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM grid_commands WHERE grid_id = ? ORDER BY created_at DESC LIMIT 1",
                (grid_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def list_for_grid(self, grid_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.mark_timeouts()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM grid_commands WHERE grid_id = ? ORDER BY created_at DESC LIMIT ?",
                (grid_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_event(self, grid_id: str, event_type: str, status: str, detail: Optional[str] = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO grid_events(grid_id, event_type, status, detail, occurred_at) VALUES(?, ?, ?, ?, ?)",
                (grid_id, event_type, status, detail, time.time()),
            )

    def list_events(self, grid_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT grid_id, event_type, status, detail, occurred_at
                   FROM grid_events WHERE grid_id = ? ORDER BY occurred_at DESC LIMIT ?""",
                (grid_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        return dict(row) if row is not None else None
