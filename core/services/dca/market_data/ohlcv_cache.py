"""
共享 OHLCV 快取（Phase 3 風險核心，稽核 3.6–3.9）

問題：N 台 DCA 監控同一 symbol+timeframe，若各自拉 OHLCV 會把 REST 權重
線性放大。本快取以 `(exchange, symbol, timeframe)` 去重，並滿足：

- 同 key 同時 miss 只需要一次底層呼叫（asyncio.Lock 防 cache stampede）
- TTL = min(timeframe_seconds, 60)，與 K 線管線節奏對齊
- 交易所拋例外 → 回傳上一份快取並標記 stale=True；無快取時回傳 None
  （指標評估為 insufficient_data，不得觸發入場）
- 只保留**已收線** K 棒：儲存時丟棄最後一根（運算約定 1）

`OhlcvCache` 不直接碰交易所；`fetch_fn` 為注入的 async 呼叫
`(exchange, symbol, timeframe, limit) -> list[OHLCVData]`。監督者以
ExchangeAdapterPool 的共享 adapter 為每個 exchange 建立一個 cache，
`provider(exchange)` 回傳 EntryMonitor 需要的
`(symbol, timeframe, limit) -> list[float]` closure。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("dca.ohlcv_cache")

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

MAX_TTL_SECONDS = 60.0

# 底層 fetch 函式：async (exchange, symbol, timeframe, limit) -> list[OHLCVData]
FetchFn = Callable[[str, str, str, int], Awaitable[Sequence]]


@dataclass(frozen=True)
class OhlcvSnapshot:
    """一次 `get()` 的結果。stale=True 表示由上一份快取降級提供。"""
    closes: List[float]
    synthetic: bool
    stale: bool
    as_of: float
    fetch_limit: int

    def closes_only(self, limit: Optional[int] = None) -> Optional[List[float]]:
        """回傳 EntryMonitor 用的 close 序列；None 表示無可用資料。"""
        if not self.closes:
            return None
        if limit and len(self.closes) > limit:
            return self.closes[-limit:]
        return self.closes


@dataclass
class _CacheEntry:
    closes: List[float]
    synthetic: bool
    as_of: float
    expires_at: float
    fetch_limit: int


def timeframe_ttl(timeframe: str) -> float:
    """TTL = min(timeframe_seconds, 60)。"""
    return float(min(TIMEFRAME_SECONDS.get(timeframe, MAX_TTL_SECONDS),
                     MAX_TTL_SECONDS))


class OhlcvCache:
    """共享、去重、防 stampede 的 OHLCV 快取。"""

    def __init__(
        self,
        fetch_fn: FetchFn,
        *,
        now_fn: Callable[[], float] = time.monotonic,
    ):
        self._fetch_fn = fetch_fn
        self._now_fn = now_fn
        self._entries: Dict[tuple, _CacheEntry] = {}
        self._locks: Dict[tuple, asyncio.Lock] = {}

    # ── 主介面 ──────────────────────────────────────────────

    async def get(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: Optional[int] = None,
    ) -> Optional[OhlcvSnapshot]:
        """去重取 closes；失敗時回傳 stale 前值，無前值回傳 None。"""
        key = (str(exchange).lower(), symbol, str(timeframe).lower())
        requested = int(limit) if limit else 300
        now = self._now_fn()

        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > now \
                and entry.fetch_limit >= requested:
            return self._snapshot(entry, requested)

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            now = self._now_fn()
            if entry is not None and entry.expires_at > now \
                    and entry.fetch_limit >= requested:
                return self._snapshot(entry, requested)
            try:
                raw = await self._fetch_fn(key[0], key[1], key[2], requested)
            except Exception as exc:
                logger.warning(
                    "OHLCV 拉取失敗，回傳前值或 None",
                    extra={"exchange": key[0], "symbol": key[1],
                           "timeframe": key[2], "error": str(exc)},
                )
                return self._stale_or_none(key, requested)

            closes = [float(getattr(c, "close", 0)) for c in raw]
            if not closes:
                logger.warning(
                    "OHLCV 回傳空序列，視為無資料",
                    extra={"exchange": key[0], "symbol": key[1],
                           "timeframe": key[2]},
                )
                return self._stale_or_none(key, requested)
            # 運算約定 1：丟棄最後一根未收線 K 棒
            closes = closes[:-1]
            synthetic = any(
                bool((getattr(c, "raw_data", None) or {}).get("synthetic"))
                for c in raw
            )
            now = self._now_fn()
            self._entries[key] = _CacheEntry(
                closes=closes,
                synthetic=synthetic,
                as_of=now,
                expires_at=now + timeframe_ttl(key[2]),
                fetch_limit=requested,
            )
            return self._snapshot(self._entries[key], requested)

    def provider(self, exchange: str) -> Callable[[str, str, int], Awaitable[Optional[List[float]]]]:
        """回傳 EntryMonitor 可用的 OHLCV provider（只回傳 closes）。"""
        async def _provider(symbol: str, timeframe: str, limit: int) -> Optional[List[float]]:
            snapshot = await self.get(exchange, symbol, timeframe, limit)
            if snapshot is None:
                return None
            return snapshot.closes_only(limit)
        return _provider

    # ── 內部 ──────────────────────────────────────────────

    @staticmethod
    def _snapshot(entry: _CacheEntry, limit: int) -> OhlcvSnapshot:
        closes = entry.closes
        if limit and len(closes) > limit:
            closes = closes[-limit:]
        return OhlcvSnapshot(
            closes=list(closes),
            synthetic=entry.synthetic,
            stale=False,
            as_of=entry.as_of,
            fetch_limit=entry.fetch_limit,
        )

    def _stale_or_none(self, key: tuple, limit: int) -> Optional[OhlcvSnapshot]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        closes = entry.closes
        if limit and len(closes) > limit:
            closes = closes[-limit:]
        return OhlcvSnapshot(
            closes=list(closes),
            synthetic=entry.synthetic,
            stale=True,
            as_of=entry.as_of,
            fetch_limit=entry.fetch_limit,
        )
