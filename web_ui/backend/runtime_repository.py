"""Small durable repository for dashboard runtime snapshots.

SQLite is deliberately used as the Phase 1 single-host baseline.  The API can
restart without losing the last engine heartbeat, while the repository interface
can later be replaced by Redis/PostgreSQL for a multi-instance deployment.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Optional


class RuntimeSnapshotRepository:
    _instance: Optional["RuntimeSnapshotRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        self.database_path = database_path or os.path.join(root, "data", "web_ui_runtime.sqlite3")
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "RuntimeSnapshotRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS grid_runtime_snapshots (
                    config_path TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS grid_runtime_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_path TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_history_path_time ON grid_runtime_history(config_path, recorded_at DESC)")

    def save(self, config_path: str, stats: dict, updated_at: Optional[float] = None) -> float:
        timestamp = updated_at if updated_at is not None else time.time()
        payload = json.dumps(stats, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO grid_runtime_snapshots(config_path, payload, updated_at)
                   VALUES(?, ?, ?)
                   ON CONFLICT(config_path) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (config_path, payload, timestamp),
            )
            connection.execute(
                "INSERT INTO grid_runtime_history(config_path, payload, recorded_at) VALUES(?, ?, ?)",
                (config_path, payload, timestamp),
            )
            # Keep a bounded local history (7 days) without needing a separate job.
            connection.execute("DELETE FROM grid_runtime_history WHERE recorded_at < ?", (timestamp - 7 * 86400,))
        return timestamp

    def load_all(self) -> dict[str, tuple[dict, float]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT config_path, payload, updated_at FROM grid_runtime_snapshots").fetchall()
        snapshots: dict[str, tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict):
                    snapshots[row["config_path"]] = (payload, float(row["updated_at"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def history(self, config_path: str, since: float, limit: int = 1000) -> list[tuple[dict, float]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT payload, recorded_at FROM grid_runtime_history
                   WHERE config_path = ? AND recorded_at >= ? ORDER BY recorded_at ASC LIMIT ?""",
                (config_path, since, limit),
            ).fetchall()
        result: list[tuple[dict, float]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict): result.append((payload, float(row["recorded_at"])))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def remove(self, config_path: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM grid_runtime_snapshots WHERE config_path = ?", (config_path,))
            connection.execute("DELETE FROM grid_runtime_history WHERE config_path = ?", (config_path,))
