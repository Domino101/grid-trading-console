"""K-line (OHLCV) service with SQLite cache-first, paginated backfill, and paper synthesis.

Specification:
- S-1: Cache-first lookup; only backfill if missing bars or gaps exist.
- S-2: Paginated backfill (limit <= 1000/1500 per page).
- S-3: Weight budget (max KLINE_MAX_FETCH_PAGES=8 calls). Partial fill returns stale + warning.
- S-4: Paper mode uses deterministic PaperExchangeAdapter synthetic generator (source="paper_synthetic", synthetic=1).
- S-5: Honest status reporting: 429/error returns cached+stale or empty+unavailable; never live on error.
- S-6: CCXT calls are executed in worker threads to never block the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from web_ui.backend.kline_repository import KlineRepository, StoredKline, TIMEFRAME_SECONDS

logger = logging.getLogger("GridAPI.KlineService")

KLINE_MAX_FETCH_PAGES_DEFAULT = 8
KLINE_FETCH_PAGE_SIZE_DEFAULT = 1000
KLINE_RETENTION_DAYS_DEFAULT = 180


def _normalize_symbol_for_ccxt(symbol: str, exchange_id: str) -> str:
    """Normalize trading pair symbol for CCXT."""
    s = symbol.strip()
    if exchange_id.lower() == "binance":
        if ":" in s:
            return s
        if "/" not in s:
            if s.endswith("USDT"):
                return f"{s[:-4]}/USDT"
            elif s.endswith("USDC"):
                return f"{s[:-4]}/USDC"
    return s


def _default_fetch_ccxt_ohlcv(
    symbol: str,
    exchange_id: str = "binance",
    timeframe: str = "15m",
    since_ms: Optional[int] = None,
    limit: int = 1000,
) -> List[List[Any]]:
    """Synchronous fetch from CCXT with contract fallback."""
    import ccxt

    exchange_class = getattr(ccxt, exchange_id.lower(), None)
    if not exchange_class:
        exchange_class = ccxt.binance

    client = exchange_class({
        "enableRateLimit": True,
        "timeout": 10000,
        "options": {"defaultType": "swap"},
    })

    try:
        norm_symbol = _normalize_symbol_for_ccxt(symbol, exchange_id)
        try:
            ohlcv = client.fetch_ohlcv(norm_symbol, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception:
            base_symbol = norm_symbol.split(":")[0]
            ohlcv = client.fetch_ohlcv(base_symbol, timeframe=timeframe, since=since_ms, limit=limit)
        return ohlcv or []
    finally:
        try:
            client.close()
        except Exception:
            pass


@dataclass(frozen=True)
class KlineResult:
    symbol: str
    exchange: str
    timeframe: str
    candles: List[StoredKline]
    source: str  # "cache", "exchange", "paper_synthetic", "cache+exchange"
    data_status: str  # "live", "paper", "stale", "unavailable"
    gaps: int = 0
    warnings: List[str] = field(default_factory=list)
    oldest_ts: Optional[int] = None


class KlineService:
    """Service providing reliable, cached historical and synthetic candlestick data."""

    _instance: Optional["KlineService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        repository: Optional[KlineRepository] = None,
        fetch_fn: Optional[Callable[[str, str, str, Optional[int], int], List[List[Any]]]] = None,
        max_pages: Optional[int] = None,
        page_size: Optional[int] = None,
        retention_days: Optional[int] = None,
    ):
        self.repository = repository or KlineRepository.instance()
        self.fetch_fn = fetch_fn or _default_fetch_ccxt_ohlcv
        self.max_pages = max_pages if max_pages is not None else int(
            os.getenv("KLINE_MAX_FETCH_PAGES", str(KLINE_MAX_FETCH_PAGES_DEFAULT))
        )
        self.page_size = page_size if page_size is not None else int(
            os.getenv("KLINE_FETCH_PAGE_SIZE", str(KLINE_FETCH_PAGE_SIZE_DEFAULT))
        )
        self.retention_days = retention_days if retention_days is not None else int(
            os.getenv("KLINE_RETENTION_DAYS", str(KLINE_RETENTION_DAYS_DEFAULT))
        )

    @classmethod
    def instance(cls, repository: Optional[KlineRepository] = None) -> "KlineService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(repository=repository)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    async def get_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 300,
        until: Optional[int] = None,
        account_mode: Optional[str] = None,
    ) -> KlineResult:
        """Asynchronous entry point that dispatches synchronous execution to a threadpool (S-6)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self.get_candles_sync,
            exchange,
            symbol,
            timeframe,
            limit,
            until,
            account_mode,
        )

    def get_candles_sync(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 300,
        until: Optional[int] = None,
        account_mode: Optional[str] = None,
    ) -> KlineResult:
        """Synchronous implementation of cached candlestick retrieval."""
        tf = str(timeframe).lower()
        if tf not in TIMEFRAME_SECONDS:
            return KlineResult(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                candles=[],
                source="exchange",
                data_status="unavailable",
                gaps=0,
                warnings=[f"不支援的 timeframe: {timeframe}"],
                oldest_ts=None,
            )

        limit = max(1, min(int(limit), 20000))
        ex = exchange.lower()
        is_paper = (
            account_mode == "paper"
            or ex in ("paper", "paper_synthetic")
        )

        if is_paper:
            return self._handle_paper_mode(exchange, symbol, tf, limit, until)

        return self._handle_exchange_mode(exchange, symbol, tf, limit, until)

    def _handle_paper_mode(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int,
        until: Optional[int] = None,
    ) -> KlineResult:
        """S-4: Deterministic synthetic candles via PaperExchangeAdapter."""
        from core.adapters.exchanges.adapters.paper import (
            PaperExchangeAdapter,
            _stable_symbol_seed,
            _synthetic_base_timestamp,
        )
        from core.adapters.exchanges.interface import ExchangeConfig, ExchangeType

        config = ExchangeConfig(
            exchange_id="paper",
            name="Paper",
            exchange_type=ExchangeType.PERPETUAL,
            api_key="",
            api_secret="",
            extra_params={"venue": exchange, "paper": True},
        )
        adapter = PaperExchangeAdapter(config)
        
        # PaperExchangeAdapter.get_ohlcv is an async method with zero I/O
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    ohlcv_list = pool.submit(asyncio.run, adapter.get_ohlcv(symbol, timeframe, limit=limit)).result()
            else:
                ohlcv_list = loop.run_until_complete(adapter.get_ohlcv(symbol, timeframe, limit=limit))
        except Exception:
            ohlcv_list = asyncio.run(adapter.get_ohlcv(symbol, timeframe, limit=limit))

        self.repository.save_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            candles=ohlcv_list,
            synthetic=1,
        )

        candles = self.repository.get_latest(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            until=until,
        )
        gaps = self.repository.count_gaps(candles, timeframe)
        oldest_ts = candles[0].ts if candles else None

        return KlineResult(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            candles=candles,
            source="paper_synthetic",
            data_status="paper",
            gaps=gaps,
            warnings=["使用合成 K 線，非真實歷史"],
            oldest_ts=oldest_ts,
        )

    def _handle_exchange_mode(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int,
        until: Optional[int] = None,
    ) -> KlineResult:
        """S-1, S-2, S-3, S-5: Cache-first, paginated gap backfilling with honesty."""
        cached = self.repository.get_latest(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            until=until,
        )
        cached_gaps = self.repository.count_gaps(cached, timeframe)

        # S-1: If cache is complete and gapless, return immediately
        if len(cached) >= limit and cached_gaps == 0:
            synth = cached[0].synthetic == 1
            data_status = "paper" if synth else "live"
            oldest_ts = cached[-limit].ts if cached else None
            return KlineResult(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                candles=cached[-limit:],
                source="cache",
                data_status=data_status,
                gaps=0,
                warnings=[],
                oldest_ts=oldest_ts,
            )

        # Need backfilling
        step_sec = TIMEFRAME_SECONDS.get(timeframe, 900)
        step_ms = step_sec * 1000
        now_sec = int(until) if until is not None else int(time.time())
        target_start_sec = now_sec - (limit * step_sec)
        current_since_ms = max(0, target_start_sec * 1000)
        page_size = min(limit, self.page_size)

        fetched_pages = 0
        all_fetched: List[List[Any]] = []
        fetch_error: Optional[Exception] = None

        while fetched_pages < self.max_pages:
            try:
                batch = self.fetch_fn(
                    symbol,
                    exchange,
                    timeframe,
                    current_since_ms,
                    page_size,
                )
            except Exception as exc:
                fetch_error = exc
                logger.warning(
                    "拉取歷史 K 線失敗 (%s:%s tf=%s since=%s): %s",
                    exchange,
                    symbol,
                    timeframe,
                    current_since_ms,
                    exc,
                )
                break

            if not batch:
                break

            fetched_pages += 1
            all_fetched.extend(batch)

            last_ts_ms = int(batch[-1][0])
            if len(batch) < page_size or last_ts_ms >= (now_sec * 1000 - step_ms):
                break

            current_since_ms = last_ts_ms + step_ms

        # S-5: Error handling with honesty
        if fetch_error is not None:
            if cached:
                return KlineResult(
                    symbol=symbol,
                    exchange=exchange,
                    timeframe=timeframe,
                    candles=cached,
                    source="cache",
                    data_status="stale",
                    gaps=cached_gaps,
                    warnings=[f"交易所連線異常 ({type(fetch_error).__name__})，使用本地快取"],
                    oldest_ts=cached[0].ts if cached else None,
                )
            return KlineResult(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                candles=[],
                source="exchange",
                data_status="unavailable",
                gaps=0,
                warnings=[f"無法取得 K 線資料: {fetch_error}"],
                oldest_ts=None,
            )

        if all_fetched:
            self.repository.save_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                candles=all_fetched,
                synthetic=0,
            )
            self.repository.prune(retention_days=self.retention_days)

        updated_candles = self.repository.get_latest(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            until=until,
        )
        updated_gaps = self.repository.count_gaps(updated_candles, timeframe)
        warnings: List[str] = []

        is_budget_exceeded = (fetched_pages >= self.max_pages and len(updated_candles) < limit)
        if is_budget_exceeded:
            warnings.append("僅回填部分歷史")
            data_status = "stale"
        elif updated_candles:
            data_status = "live"
        else:
            data_status = "unavailable"

        if len(cached) > 0 and len(all_fetched) > 0:
            source = "cache+exchange"
        elif len(all_fetched) > 0:
            source = "exchange"
        else:
            source = "cache"

        oldest_ts = updated_candles[0].ts if updated_candles else None

        return KlineResult(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            candles=updated_candles,
            source=source,
            data_status=data_status,
            gaps=updated_gaps,
            warnings=warnings,
            oldest_ts=oldest_ts,
        )
