"""
交易對資訊查詢端點

GET /api/v1/market/info/{symbol} — 查詢交易對精度/最小下單量
使用 60 秒記憶體快取，避免重複 REST 呼叫。
"""

import os
import sys
import time
import math
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from auth import verify_token
from schemas import (
    MarketInfoResponse,
    GridRecommendationResponse,
    MarketRankingsResponse,
    MarketIndicatorsResponse,
)
from grid_recommendation import generate_recommendation_sync, get_market_rankings_sync
from core.services.dca.indicators.bollinger import evaluate as evaluate_bollinger
from core.services.dca.indicators.config import (
    BollingerConfig,
    MacdConfig,
    PriceChangeConfig,
    RsiConfig,
)
from core.services.dca.indicators.macd import evaluate as evaluate_macd
from core.services.dca.indicators.price_change import evaluate as evaluate_price_change
from core.services.dca.indicators.rsi import evaluate as evaluate_rsi

logger = logging.getLogger("GridAPI.Market")

router = APIRouter(prefix="/api/v1/market", tags=["market"])



# ── 記憶體快取 ──
_market_cache: dict = {}
_indicators_cache: dict = {}
_CACHE_TTL = 60  # 秒


def _precision_to_decimals(val: Optional[float | int], precision_mode: Optional[int] = None) -> Optional[int]:
    """
    將 CCXT 回傳的 precision 轉為小數位數整數 (int)。
    支援 TICK_SIZE (例: 0.01 -> 2, 0.001 -> 3, 0.00001 -> 5, 1.0 -> 0)
    以及 DECIMAL_PLACES (例: 2 -> 2, 4 -> 4)
    """
    if val is None:
        return None
    try:
        val_f = float(val)
    except (ValueError, TypeError):
        return None
    if val_f <= 0:
        return 0
    # 若 CCXT precisionMode 為 TICK_SIZE (2) 或數值 < 1.0 (典型 tick size)
    if precision_mode == 2 or val_f < 1.0:
        if val_f >= 1.0:
            return 0
        return int(round(-math.log10(val_f)))
    # DECIMAL_PLACES 模式 (0) 或整數
    return int(val_f)


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

    cache_key = f"{exchange.lower()}:{symbol}"

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

        # 彈性匹配交易對符號
        matched_symbol = None
        if symbol in ex.markets:
            matched_symbol = symbol
        elif symbol.upper() in ex.markets:
            matched_symbol = symbol.upper()
        else:
            s_upper = symbol.upper()
            if "/" in s_upper and ":" not in s_upper:
                parts = s_upper.split("/")
                candidate = f"{s_upper}:{parts[1]}"
                if candidate in ex.markets:
                    matched_symbol = candidate
            elif ":" in s_upper:
                candidate = s_upper.split(":")[0]
                if candidate in ex.markets:
                    matched_symbol = candidate
            if not matched_symbol:
                clean_id = s_upper.replace("/", "").replace(":", "").replace("-", "").replace("_", "")
                for m_sym, m in ex.markets.items():
                    if m.get("id") == clean_id or m.get("id") == s_upper:
                        matched_symbol = m_sym
                        break

        if not matched_symbol or matched_symbol not in ex.markets:
            return None

        market = ex.markets[matched_symbol]
        precision = market.get("precision", {})
        limits = market.get("limits", {})
        amount_limits = limits.get("amount", {})
        cost_limits = limits.get("cost", {})
        precision_mode = getattr(ex, "precisionMode", None)

        price_prec = _precision_to_decimals(precision.get("price"), precision_mode)
        qty_prec = _precision_to_decimals(precision.get("amount"), precision_mode)

        min_amount = amount_limits.get("min")
        if min_amount is not None:
            try:
                min_amount = float(min_amount)
            except (ValueError, TypeError):
                min_amount = None

        min_cost = cost_limits.get("min")
        if min_cost is not None:
            try:
                min_cost = float(min_cost)
            except (ValueError, TypeError):
                min_cost = None

        contract_sz = market.get("contractSize")
        if contract_sz is not None:
            try:
                contract_sz = float(contract_sz)
            except (ValueError, TypeError):
                contract_sz = None

        return MarketInfoResponse(
            symbol=symbol,
            exchange=exchange,
            price_precision=price_prec,
            quantity_precision=qty_prec,
            min_order_amount=min_amount,
            min_notional=min_cost,
            contract_size=contract_sz,
            cached=False,
        )

    except Exception as e:
        logger.warning(f"查詢市場資訊失敗 ({exchange}:{symbol}): {e}")
        return None


@router.get("/recommendation/{symbol:path}", response_model=GridRecommendationResponse)
async def get_grid_recommendation(
    symbol: str,
    exchange: str = Query(default="binance", description="交易所名稱 (binance/lighter/hyperliquid)"),
    days: int = Query(default=7, description="回測週期天數 (7/30/180)"),
    _token: str = Depends(verify_token),
):
    """
    獲取 AI 推薦網格參數與歷史回測年化收益率。
    包含 7D, 30D, 180D 完整週期回測指標與推薦上下限、網格數。
    """
    import asyncio

    # 1. 取得交易對精度與最小下單限制 (預設值安全備援)
    market_info = None
    try:
        loop = asyncio.get_running_loop()
        market_info = await loop.run_in_executor(
            None, _lookup_market_from_ccxt_sync, symbol, exchange
        )
    except Exception as e:
        logger.warning(f"獲取市場資訊輔助參數失敗: {e}")

    price_precision = market_info.price_precision if (market_info and market_info.price_precision is not None) else 2
    quantity_precision = market_info.quantity_precision if (market_info and market_info.quantity_precision is not None) else 4
    min_notional = market_info.min_notional if (market_info and market_info.min_notional is not None) else 5.0

    # 2. 在線程池中執行 K 線抓取、參數推薦與虛擬網格回測
    loop = asyncio.get_running_loop()
    try:
        recommendation = await loop.run_in_executor(
            None,
            generate_recommendation_sync,
            symbol,
            exchange,
            days,
            price_precision,
            quantity_precision,
            min_notional,
        )
        return recommendation
    except Exception as e:
        logger.error(f"產出 AI 網格推薦失敗 ({exchange}:{symbol}): {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"獲取網格推薦與回測數據失敗: {str(e)}",
        )


@router.get("/rankings", response_model=MarketRankingsResponse)
async def get_market_rankings(
    exchange: str = Query(default="binance", description="交易所名稱 (binance/lighter/hyperliquid)"),
    period: str = Query(default="7d", description="回測週期 (7d/30d/180d)"),
    limit: int = Query(default=20, ge=1, le=50, description="回傳排名筆數"),
    _token: str = Depends(verify_token),
):
    """
    獲取全市場熱門主流代幣之網格年化收益排行榜 (Backtest APR Ranking)
    
    具備 15 分鐘後端記憶體快取，前端請求不產生交易所 API 負擔，絕對防封。
    """
    import asyncio

    loop = asyncio.get_running_loop()
    try:
        rankings = await loop.run_in_executor(
            None,
            get_market_rankings_sync,
            exchange,
            period,
            limit,
        )
        return rankings
    except Exception as e:
        logger.error(f"獲取市場年化排行榜失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"獲取市場年化排行榜失敗: {str(e)}",
        )


@router.get("/indicators/{symbol:path}", response_model=MarketIndicatorsResponse)
async def get_market_indicators(
    symbol: str,
    exchange: str = Query(default="binance", description="交易所名稱"),
    timeframe: str = Query(default="15m", description="K 線時框（所有指標共用）"),
    limit: int = Query(default=300, ge=100, le=500, description="取樣根數"),
    _token: str = Depends(verify_token),
):
    """
    指標即時值（供 DCA 精靈預覽 + 詳情面板）。

    60 秒記憶體快取（稽核 4.19）；ccxt 同步呼叫以 run_in_executor 包裝（稽核 4.20）。
    """
    import asyncio

    cache_key = f"{exchange.lower()}:{symbol}:{timeframe}:{limit}"
    if cache_key in _indicators_cache:
        cached_data, cached_at = _indicators_cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL:
            cached_data.cached = True
            return cached_data

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(
        None, _compute_indicators_sync, symbol, exchange, timeframe, limit
    )
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"無法取得 {symbol} 的 K 線資料 (exchange={exchange})",
        )
    _indicators_cache[cache_key] = (payload, time.time())
    return payload


def _fetch_ohlcv_sync(symbol: str, exchange: str, timeframe: str, limit: int):
    """同步拉取 OHLCV（由 run_in_executor 包裝）。"""
    import ccxt
    exchange_class = getattr(ccxt, exchange.lower(), None)
    if exchange_class is None:
        return None
    ex = exchange_class({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def _compute_indicators_sync(symbol: str, exchange: str, timeframe: str, limit: int) -> Optional[MarketIndicatorsResponse]:
    """同步計算四種指標；無資料時回傳 None。"""
    try:
        rows = _fetch_ohlcv_sync(symbol, exchange, timeframe, limit)
    except Exception as e:
        logger.warning("指標端點拉取 OHLCV 失敗 (%s:%s): %s", exchange, symbol, e)
        return None
    if not rows:
        return None
    # 丟棄最後一根未收線 K 棒（運算約定 1）
    closes = [float(row[4]) for row in rows[:-1]]
    current_price = float(rows[-1][4])
    if len(closes) < 2:
        return None

    rsi = evaluate_rsi(closes, RsiConfig(), direction="long")
    macd = evaluate_macd(closes, MacdConfig(), direction="long")
    bollinger = evaluate_bollinger(closes, BollingerConfig(), direction="long")
    price_change = evaluate_price_change(closes, PriceChangeConfig(), direction="long")

    def _result(result) -> dict:
        return {
            "value": result.value,
            "triggered_long": result.triggered,
            "triggered_short": None,
            "insufficient_data": result.insufficient_data,
            "warmup_have": result.warmup_have,
            "warmup_need": result.warmup_need,
            "as_of": result.as_of,
            "details": result.details,
        }

    data_status = "live" if closes and len(closes) >= 2 else "unavailable"
    return MarketIndicatorsResponse(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        fetch_limit=limit,
        current_price=current_price,
        rsi=_result(rsi),
        macd=_result(macd),
        bollinger=_result(bollinger),
        price_change=_result(price_change),
        data_status=data_status,
        cached=False,
    )


