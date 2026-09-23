"""SQLite repository for historical and synthetic DCA candlestick (OHLCV) data.

This module manages the `dca_klines` table in `data/web_ui_runtime.sqlite3`.
All queries are parameterized (rule-04, K-1).
Prices and volumes are stored as TEXT decimal strings to avoid floating point drift (K-3).
Upsert operations are idempotent and protect real historical data from being overwritten by synthetic data (K-2).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from web_ui.backend.lifecycle import connect_sqlite

TIMEFRAME_SECONDS: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class StoredKline:
    exchange: str
    symbol: str
    timeframe: str
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    synthetic: int  # 0: real, 1: synthetic
    fetched_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": self.ts,
            "o": float(self.open),
            "h": float(self.high),
            "l": float(self.low),
            "c": float(self.close),
            "v": float(self.volume),
            "synthetic": bool(self.synthetic),
        }


class KlineRepository:
    """Thread-safe SQLite repository for DCA candlestick cache."""

    _instance: Optional["KlineRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        from core.infrastructure.database.guard import EnvironmentGuard
        self.database_path = EnvironmentGuard.get_safe_database_path(database_path)
        if self.database_path != ":memory:":
            os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls, database_path: Optional[str] = None) -> "KlineRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(database_path=database_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sqlite(self.database_path)
        self._ensure_schema(connection)
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS dca_klines (
                exchange   TEXT    NOT NULL,
                symbol     TEXT    NOT NULL,
                timeframe  TEXT    NOT NULL,
                ts         INTEGER NOT NULL,
                open       TEXT    NOT NULL,
                high       TEXT    NOT NULL,
                low        TEXT    NOT NULL,
                close      TEXT    NOT NULL,
                volume     TEXT    NOT NULL,
                synthetic  INTEGER NOT NULL DEFAULT 0,
                fetched_at REAL    NOT NULL,
                PRIMARY KEY (exchange, symbol, timeframe, ts)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_klines_lookup
                ON dca_klines(exchange, symbol, timeframe, ts DESC)
        """)

    def _initialize(self) -> None:
        with connect_sqlite(self.database_path) as connection:
            self._ensure_schema(connection)

    def save_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        candles: Sequence[Any],
        synthetic: int = 0,
        fetched_at: Optional[float] = None,
    ) -> int:
        """Upsert candles idempotently.
        
        Real historical data (synthetic=0) cannot be downgraded or overwritten
        by synthetic data (synthetic=1).
        """
        if not candles:
            return 0

        now = fetched_at if fetched_at is not None else time.time()
        ex = exchange.lower()
        tf = timeframe.lower()
        synth = 1 if synthetic else 0

        rows: List[Tuple[str, str, str, int, str, str, str, str, str, int, float]] = []
        for c in candles:
            if isinstance(c, StoredKline):
                ts = c.ts
                o_str = str(c.open)
                h_str = str(c.high)
                l_str = str(c.low)
                c_str = str(c.close)
                v_str = str(c.volume)
                s_val = c.synthetic
            elif isinstance(c, dict):
                ts_raw = c.get("ts") or c.get("t") or 0
                ts = int(ts_raw // 1000 if ts_raw > 10_000_000_000 else ts_raw)
                o_str = str(c.get("open") or c.get("o") or "0")
                h_str = str(c.get("high") or c.get("h") or "0")
                l_str = str(c.get("low") or c.get("l") or "0")
                c_str = str(c.get("close") or c.get("c") or "0")
                v_str = str(c.get("volume") or c.get("v") or "0")
                s_val = 1 if c.get("synthetic") else synth
            elif isinstance(c, (list, tuple)) and len(c) >= 6:
                ts_raw = c[0]
                ts = int(ts_raw // 1000 if ts_raw > 10_000_000_000 else ts_raw)
                o_str = str(c[1])
                h_str = str(c[2])
                l_str = str(c[3])
                c_str = str(c[4])
                v_str = str(c[5])
                s_val = synth
            elif hasattr(c, "timestamp") and hasattr(c, "close"):
                # e.g. OHLCVData dataclass
                dt = getattr(c, "timestamp")
                ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(dt)
                o_str = str(getattr(c, "open", "0"))
                h_str = str(getattr(c, "high", "0"))
                l_str = str(getattr(c, "low", "0"))
                c_str = str(getattr(c, "close", "0"))
                v_str = str(getattr(c, "volume", "0"))
                raw_d = getattr(c, "raw_data", {}) or {}
                s_val = 1 if raw_d.get("synthetic") else synth
            else:
                continue

            rows.append((ex, symbol, tf, ts, o_str, h_str, l_str, c_str, v_str, s_val, now))

        if not rows:
            return 0

        upsert_sql = """
            INSERT INTO dca_klines(
                exchange, symbol, timeframe, ts, open, high, low, close, volume, synthetic, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol, timeframe, ts) DO UPDATE SET
                open = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.open ELSE excluded.open END,
                high = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.high ELSE excluded.high END,
                low = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.low ELSE excluded.low END,
                close = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.close ELSE excluded.close END,
                volume = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.volume ELSE excluded.volume END,
                synthetic = CASE WHEN dca_klines.synthetic = 0 THEN 0 ELSE excluded.synthetic END,
                fetched_at = CASE WHEN dca_klines.synthetic = 0 AND excluded.synthetic = 1 THEN dca_klines.fetched_at ELSE excluded.fetched_at END
        """

        with self._lock, self._connect() as connection:
            connection.executemany(upsert_sql, rows)

        return len(rows)

    def get_range(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
        desc: bool = False,
        synthetic: Optional[int] = None,
    ) -> List[StoredKline]:
        """Fetch klines matching range constraints with fully parameterized query."""
        conditions = ["exchange = ?", "symbol = ?", "timeframe = ?"]
        params: List[Any] = [exchange.lower(), symbol, timeframe.lower()]

        if synthetic is not None:
            conditions.append("synthetic = ?")
            params.append(int(synthetic))
        if start_ts is not None:
            conditions.append("ts >= ?")
            params.append(int(start_ts))
        if end_ts is not None:
            conditions.append("ts <= ?")
            params.append(int(end_ts))

        where_clause = " AND ".join(conditions)
        order_clause = "ORDER BY ts DESC" if desc else "ORDER BY ts ASC"
        limit_clause = "LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(int(limit))

        query_sql = (
            "SELECT exchange, symbol, timeframe, ts, open, high, low, close, volume, synthetic, fetched_at "
            "FROM dca_klines WHERE " + where_clause + " " + order_clause + " " + limit_clause
        )

        with self._lock, self._connect() as connection:
            cursor = connection.execute(query_sql, params)
            rows = cursor.fetchall()

        result: List[StoredKline] = []
        for r in rows:
            result.append(
                StoredKline(
                    exchange=r["exchange"],
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    ts=int(r["ts"]),
                    open=Decimal(r["open"]),
                    high=Decimal(r["high"]),
                    low=Decimal(r["low"]),
                    close=Decimal(r["close"]),
                    volume=Decimal(r["volume"]),
                    synthetic=int(r["synthetic"]),
                    fetched_at=float(r["fetched_at"]),
                )
            )
        return result

    def get_latest(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int = 300,
        until: Optional[int] = None,
        synthetic: Optional[int] = None,
    ) -> List[StoredKline]:
        """Fetch the most recent `limit` candles up to `until`, sorted ascending."""
        desc_candles = self.get_range(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_ts=until,
            limit=limit,
            desc=True,
            synthetic=synthetic,
        )
        return list(reversed(desc_candles))

    def count(self, exchange: str, symbol: str, timeframe: str, synthetic: Optional[int] = None) -> int:
        """Count total candles stored for the given pair and timeframe."""
        conditions = ["exchange = ?", "symbol = ?", "timeframe = ?"]
        params: List[Any] = [exchange.lower(), symbol, timeframe.lower()]
        if synthetic is not None:
            conditions.append("synthetic = ?")
            params.append(int(synthetic))
        query_sql = (
            "SELECT COUNT(*) AS total FROM dca_klines WHERE " + " AND ".join(conditions)
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(query_sql, params)
            row = cursor.fetchone()
            return int(row["total"]) if row else 0

    def get_time_bounds(
        self, exchange: str, symbol: str, timeframe: str, synthetic: Optional[int] = None
    ) -> Tuple[Optional[int], Optional[int]]:
        """Get (min_ts, max_ts) for stored candles."""
        conditions = ["exchange = ?", "symbol = ?", "timeframe = ?"]
        params: List[Any] = [exchange.lower(), symbol, timeframe.lower()]
        if synthetic is not None:
            conditions.append("synthetic = ?")
            params.append(int(synthetic))
        query_sql = (
            "SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM dca_klines WHERE " + " AND ".join(conditions)
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(query_sql, params)
            row = cursor.fetchone()
            if row and row["min_ts"] is not None:
                return int(row["min_ts"]), int(row["max_ts"])
        return None, None

    def prune(self, retention_days: int = 180, now: Optional[float] = None) -> int:
        """Prune candles whose fetched_at is older than retention_days."""
        current_time = now if now is not None else time.time()
        cutoff = current_time - (float(retention_days) * 86400.0)
        delete_sql = "DELETE FROM dca_klines WHERE fetched_at < ?"
        with self._lock, self._connect() as connection:
            cursor = connection.execute(delete_sql, (cutoff,))
            return cursor.rowcount

    @staticmethod
    def count_gaps(
        candles_or_timestamps: Sequence[Any], timeframe: str
    ) -> int:
        """Detect missing bars in an equidistant timeframe series."""
        step = TIMEFRAME_SECONDS.get(str(timeframe).lower(), 900)
        if not candles_or_timestamps or len(candles_or_timestamps) < 2:
            return 0

        timestamps: List[int] = []
        for item in candles_or_timestamps:
            if isinstance(item, StoredKline):
                timestamps.append(int(item.ts))
            elif isinstance(item, dict):
                ts_raw = item.get("ts") or item.get("t") or 0
                timestamps.append(int(ts_raw // 1000 if ts_raw > 10_000_000_000 else ts_raw))
            elif isinstance(item, (int, float)):
                timestamps.append(int(item))
            elif hasattr(item, "timestamp"):
                dt = getattr(item, "timestamp")
                ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(dt)
                timestamps.append(ts)
            elif isinstance(item, (list, tuple)) and len(item) > 0:
                ts_raw = item[0]
                timestamps.append(int(ts_raw // 1000 if ts_raw > 10_000_000_000 else ts_raw))

        timestamps.sort()
        gaps = 0
        for i in range(len(timestamps) - 1):
            diff = timestamps[i + 1] - timestamps[i]
            if diff > step:
                missing = (diff // step) - 1
                if missing > 0:
                    gaps += missing
        return gaps
