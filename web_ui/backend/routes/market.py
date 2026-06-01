"""
交易對資訊查詢端點

GET /api/v1/market/info/{symbol} — 查詢交易對精度/最小下單量
使用 60 秒記憶體快取，避免重複 REST 呼叫。
"""

import os
import sys
import time
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from auth import verify_token
from schemas import MarketInfoResponse

logger = logging.getLogger("GridAPI.Market")

router = APIRouter(prefix="/api/v1/market", tags=["market"])

# ── 記憶體快取 ──
_market_cache: dict = {}
_CACHE_TTL = 60  # 秒


@router.get("/info/{symbol:path}", response_model=MarketInfoResponse)
async def get_market_info(
    symbol: str,
    exchange: str = Query(default="binance", description="交易所名稱"),
    _token: str = Depends(verify_token),
):
    """
    查詢交易對精度與最小下單量。

    首次查詢可能觸發一次 load_markets() REST 呼叫（~10 API 權重），
    後續 60 秒內從記憶體快取返回。

    注意：symbol 中的 / 會被 FastAPI 路徑匹配，所以使用 {symbol:path}。
    例如：GET /api/v1/market/info/SOL/USDC:USDC?exchange=binance
    """
    import asyncio

    cache_key = f"{exchange}:{symbol}"

    # 檢查快取
    if cache_key in _market_cache:
        cached_data, cached_at = _market_cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            cached_data.cached = True
            return cached_data

    # ccxt load_markets() 是同步阻塞操作，放入線程池執行以免阻塞 event loop
    loop = asyncio.get_running_loop()
    market_data = await loop.run_in_executor(
        None, _lookup_market_from_ccxt_sync, symbol, exchange
    )

    if market_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"找不到交易對 {symbol} (exchange={exchange})。"
                   "請確認交易對格式正確（如 SOL/USDC:USDC）",
        )

    # 寫入快取
    _market_cache[cache_key] = (market_data, time.time())

    return market_data


def _lookup_market_from_ccxt_sync(symbol: str, exchange: str) -> Optional[MarketInfoResponse]:
    """
    從 ccxt 查找市場資訊（純同步函式，由 run_in_executor 包裝調用）。

    若 ccxt 已經 load_markets()（引擎啟動時），直接讀取記憶體。
    否則建立臨時唯讀 ccxt 實例並同步載入。
    """
    try:
        import ccxt

        exchange_lower = exchange.lower()

        exchange_class = getattr(ccxt, exchange_lower, None)
        if exchange_class is None:
            return None

        # 建立臨時實例並同步載入 markets
        ex = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        ex.load_markets()

        if symbol not in ex.markets:
            return None

        market = ex.markets[symbol]
        precision = market.get("precision", {})
        limits = market.get("limits", {})
        amount_limits = limits.get("amount", {})
        cost_limits = limits.get("cost", {})

        return MarketInfoResponse(
            symbol=symbol,
            exchange=exchange,
            price_precision=precision.get("price"),
            quantity_precision=precision.get("amount"),
            min_order_amount=amount_limits.get("min"),
            min_notional=cost_limits.get("min"),
            contract_size=market.get("contractSize"),
            cached=False,
        )

    except Exception as e:
        logger.warning(f"查詢市場資訊失敗 ({exchange}:{symbol}): {e}")
        return None
