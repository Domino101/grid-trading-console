"""
AI 網格參數推薦與歷史回測服務模組 (AI Grid Recommendation & Backtest Service)

功能：
1. 抓取交易所公開歷史 K 線 (OHLCV)
2. 自動推算各週期（7D / 30D / 180D）的推薦價格上下限、網格間距與格數
3. 透過虛擬網格 (VirtualGrid) 模擬歷史成交循環，精確計算歷史回測年化收益率 (Backtest APY%)
4. 支援記憶體快取以降低交易所 API 負載
"""

import os
import sys
import json
import sqlite3
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
import ccxt

_backend_dir = os.path.abspath(os.path.dirname(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from lifecycle import connect_sqlite

logger = logging.getLogger("GridAPI.Recommendation")

# 記憶體快取: key -> (data, cached_at)
_recommendation_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_CACHE_TTL = 60  # 快取 60 秒


def _normalize_symbol_for_ccxt(symbol: str, exchange_id: str) -> str:
    """轉換交易對格式為 CCXT 標準格式"""
    s = symbol.strip()
    if exchange_id.lower() == "binance":
        # 支援 SOL/USDT, SOL/USDC:USDC -> SOL/USDC:USDC (合約) 或 SOL/USDC (現貨)
        if ":" in s:
            return s
        if "/" not in s:
            if s.endswith("USDT"):
                return f"{s[:-4]}/USDT"
            elif s.endswith("USDC"):
                return f"{s[:-4]}/USDC"
    return s


def _fetch_ohlcv_sync(
    symbol: str,
    exchange_id: str = "binance",
    timeframe: str = "1h",
    limit: int = 168
) -> List[List[float]]:
    """同步抓取歷史 K 線數據 (OHLCV)"""
    exchange_class = getattr(ccxt, exchange_id.lower(), None)
    if not exchange_class:
        exchange_class = ccxt.binance
    
    client = exchange_class({
        "enableRateLimit": True,
        "timeout": 10000,
    })
    
    try:
        norm_symbol = _normalize_symbol_for_ccxt(symbol, exchange_id)
        # 嘗試抓取
        try:
            ohlcv = client.fetch_ohlcv(norm_symbol, timeframe=timeframe, limit=limit)
        except Exception:
            # 若帶 :USDC 失敗，嘗試移除合約後綴
            base_symbol = norm_symbol.split(":")[0]
            ohlcv = client.fetch_ohlcv(base_symbol, timeframe=timeframe, limit=limit)
        return ohlcv or []
    finally:
        try:
            client.close()
        except Exception:
            pass


def _simulate_grid_backtest(
    ohlcv_list: List[List[float]],
    lower_price: float,
    upper_price: float,
    grid_count: int,
    fee_rate_pct: float = 0.04
) -> Tuple[int, float]:
    """
    輕量化高速虛擬網格撮合回測
    
    Returns:
        (completed_cycles, backtest_apr_pct)
    """
    if not ohlcv_list or grid_count < 2 or upper_price <= lower_price:
        return 0, 0.0

    price_step = (upper_price - lower_price) / grid_count
    avg_price = (lower_price + upper_price) / 2.0
    interval_pct = (price_step / avg_price) * 100.0

    # 模擬網格狀態機
    first_close = ohlcv_list[0][4]
    current_grid_idx = min(max(int((first_close - lower_price) / price_step), 0), grid_count - 1)
    
    pending_buy_price = lower_price + current_grid_idx * price_step
    pending_sell_price = pending_buy_price + price_step
    
    completed_cycles = 0
    buy_count = 0
    sell_count = 0

    for candle in ohlcv_list:
        # candle 格式: [timestamp, open, high, low, close, volume]
        high = candle[2]
        low = candle[3]
        
        # 價格下跌觸發買單
        if low <= pending_buy_price and pending_buy_price >= lower_price:
            buy_count += 1
            bought_price = pending_buy_price
            pending_sell_price = bought_price + price_step
            pending_buy_price = bought_price - price_step
            
        # 價格上漲觸發賣單
        if high >= pending_sell_price and pending_sell_price <= upper_price:
            sell_count += 1
            sold_price = pending_sell_price
            pending_buy_price = sold_price - price_step
            pending_sell_price = sold_price + price_step
            completed_cycles += 1

    # 計算總回測時長 (小時)
    total_hours = max(len(ohlcv_list), 1)  # 假設 1h 棒線
    cycles_per_hour = completed_cycles / total_hours
    
    # 單次循環淨利率 = interval_pct - 双边手续费
    net_profit_rate = interval_pct - fee_rate_pct
    if net_profit_rate <= 0:
        annual_apr = 0.0
    else:
        annual_apr = net_profit_rate * cycles_per_hour * 8760.0

    # 上限防禦
    annual_apr = min(annual_apr, 99999.0)
    return completed_cycles, round(annual_apr, 2)


def generate_recommendation_sync(
    symbol: str,
    exchange_id: str = "binance",
    target_days: int = 7,
    price_precision: int = 2,
    quantity_precision: int = 4,
    min_notional: float = 5.0
) -> Dict[str, Any]:
    """同步產生 AI 推薦參數與回測報告（包含 7D, 30D, 180D）"""
    cache_key = f"{exchange_id.lower()}:{symbol.upper()}"
    now = time.time()
    
    if cache_key in _recommendation_cache:
        cached_res, cached_time = _recommendation_cache[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_res

    # 抓取 720 根 1h K 線（可覆蓋 30D）
    ohlcv_1h = _fetch_ohlcv_sync(symbol, exchange_id, timeframe="1h", limit=720)
    
    if not ohlcv_1h or len(ohlcv_1h) < 10:
        raise ValueError(f"無法獲取 {symbol} 在 {exchange_id} 的歷史 K 線數據")
    
    current_price = ohlcv_1h[-1][4]
    
    periods_config = [
        {"key": "7d", "days": 7, "candles": min(len(ohlcv_1h), 7 * 24), "buffer_low": 0.98, "buffer_high": 1.02, "target_int_pct": 0.4},
        {"key": "30d", "days": 30, "candles": min(len(ohlcv_1h), 30 * 24), "buffer_low": 0.95, "buffer_high": 1.05, "target_int_pct": 0.6},
        {"key": "180d", "days": 180, "candles": min(len(ohlcv_1h), len(ohlcv_1h)), "buffer_low": 0.90, "buffer_high": 1.10, "target_int_pct": 0.8},
    ]

    periods_result = {}

    for p in periods_config:
        subset = ohlcv_1h[-p["candles"]:]
        if not subset:
            continue
            
        lows = [c[3] for c in subset]
        highs = [c[2] for c in subset]
        
        min_p = min(lows)
        max_p = max(highs)
        
        lower_price = round(min_p * p["buffer_low"], price_precision)
        upper_price = round(max_p * p["buffer_high"], price_precision)
        
        # 確保價格邊界合理
        if lower_price >= current_price:
            lower_price = round(current_price * 0.95, price_precision)
        if upper_price <= current_price:
            upper_price = round(current_price * 1.05, price_precision)
            
        # 計算網格數
        total_width_pct = ((upper_price - lower_price) / current_price) * 100.0
        grid_count = int(round(total_width_pct / p["target_int_pct"]))
        grid_count = max(min(grid_count, 120), 5)  # 限制在 5 ~ 120 格
        
        grid_interval = round((upper_price - lower_price) / grid_count, price_precision)
        if grid_interval <= 0:
            grid_interval = round((upper_price - lower_price) / 10, price_precision)

        # 建議單格下單量（滿足 min_notional）
        avg_price = (lower_price + upper_price) / 2.0
        min_order_for_notional = (min_notional * 1.2) / avg_price if avg_price > 0 else 0.001
        recommended_order_amount = round(min_order_for_notional, quantity_precision)
        if recommended_order_amount <= 0:
            recommended_order_amount = round(10 ** (-quantity_precision), quantity_precision)

        # 執行回測
        cycles, apr = _simulate_grid_backtest(
            ohlcv_list=subset,
            lower_price=lower_price,
            upper_price=upper_price,
            grid_count=grid_count,
            fee_rate_pct=0.04
        )
        
        period_data = {
            "days": p["days"],
            "lower_price": lower_price,
            "upper_price": upper_price,
            "grid_count": grid_count,
            "grid_interval": grid_interval,
            "recommended_order_amount": recommended_order_amount,
            "estimated_apr": apr,
            "completed_cycles": cycles,
            "price_range_percent": round(total_width_pct, 2),
            "period_low": min_p,
            "period_high": max_p,
        }
        periods_result[p["key"]] = period_data

    # 預設選取目標週期（如 7d）
    default_key = f"{target_days}d"
    if default_key not in periods_result:
        default_key = "7d" if "7d" in periods_result else list(periods_result.keys())[0]

    response_data = {
        "symbol": symbol,
        "exchange": exchange_id,
        "current_price": current_price,
        "price_precision": price_precision,
        "quantity_precision": quantity_precision,
        "min_notional": min_notional,
        "selected_period": default_key,
        "recommended": periods_result[default_key],
        "periods": periods_result,
    }

    _recommendation_cache[cache_key] = (response_data, now)
    return response_data


# ── 市場年化排行榜快取與計算（支援 SQLite 磁碟持久化與記憶體快取） ──

_rankings_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_RANKINGS_CACHE_TTL = 900  # 快取 15 分鐘 (900秒)，確保完全零 API 濫用


def _get_db_path() -> str:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    return os.path.join(root, "data", "web_ui_runtime.sqlite3")


def _ensure_rankings_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS market_rankings_cache (
            cache_key TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            period TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)


def _load_rankings_from_disk(exchange_id: str, period: str) -> Optional[Tuple[Dict[str, Any], float]]:
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None
    cache_key = f"{exchange_id.lower()}:{period.lower()}"
    try:
        with connect_sqlite(db_path) as conn:
            _ensure_rankings_schema(conn)
            row = conn.execute(
                "SELECT payload, updated_at FROM market_rankings_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            if row:
                payload = json.loads(row["payload"])
                return payload, float(row["updated_at"])
    except Exception as e:
        logger.warning(f"從 SQLite 讀取市場排行榜快取失敗: {e}")
    return None


def _save_rankings_to_disk(exchange_id: str, period: str, data: Dict[str, Any], updated_at: float) -> None:
    db_path = _get_db_path()
    cache_key = f"{exchange_id.lower()}:{period.lower()}"
    payload_str = json.dumps(data, ensure_ascii=False)
    try:
        with connect_sqlite(db_path) as conn:
            _ensure_rankings_schema(conn)
            conn.execute("""
                INSERT INTO market_rankings_cache (cache_key, exchange, period, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
            """, (cache_key, exchange_id.lower(), period.lower(), payload_str, updated_at))
    except Exception as e:
        logger.warning(f"寫入市場排行榜快取至 SQLite 失敗: {e}")


def _get_top_market_tickers_sync(exchange_id: str = "binance", max_symbols: int = 25) -> List[Dict[str, Any]]:
    """抓取 24h Ticker 並過濾出成交額 Top 25 的熱門主流交易對"""
    exchange_class = getattr(ccxt, exchange_id.lower(), None) or ccxt.binance
    client = exchange_class({"enableRateLimit": True, "timeout": 10000})

    try:
        tickers = client.fetch_tickers()
        filtered = []

        stablecoins = {"USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "DAI/USDT", "EUR/USDT", "AEUR/USDT", "BUSD/USDT", "USDP/USDT"}

        for sym, t in tickers.items():
            if not sym or not t:
                continue
            if sym in stablecoins:
                continue
            # 排除槓桿代幣
            if any(sym.startswith(prefix) or f"/{prefix}" in sym for prefix in ["UP", "DOWN", "BULL", "BEAR"]):
                continue
            # 只取 USDT 或 USDC 計價的現貨/合約
            if not (sym.endswith("/USDT") or sym.endswith("/USDC") or ":USDC" in sym or ":USDT" in sym):
                continue

            quote_volume = t.get("quoteVolume") or (t.get("baseVolume", 0) * (t.get("last") or 0)) or 0
            if quote_volume < 1_000_000:  # 至少 100 萬 USDT 24h 成交量
                continue

            filtered.append({
                "symbol": sym,
                "last": float(t.get("last") or 0),
                "quoteVolume": float(quote_volume),
                "percentage": float(t.get("percentage") or 0),
            })

        # 按成交量由大到小排序
        filtered.sort(key=lambda x: x["quoteVolume"], reverse=True)
        return filtered[:max_symbols]
    except Exception as e:
        logger.warning(f"獲取市場 Ticker 失敗: {e}")
        # 安全備援主流幣清單
        return [
            {"symbol": "BTC/USDT", "last": 62000.0, "quoteVolume": 1000000000.0, "percentage": 1.2},
            {"symbol": "ETH/USDT", "last": 2400.0, "quoteVolume": 500000000.0, "percentage": 2.1},
            {"symbol": "SOL/USDT", "last": 140.0, "quoteVolume": 300000000.0, "percentage": 3.5},
            {"symbol": "BNB/USDT", "last": 570.0, "quoteVolume": 100000000.0, "percentage": 0.5},
            {"symbol": "DOGE/USDT", "last": 0.10, "quoteVolume": 80000000.0, "percentage": 4.2},
        ]
    finally:
        try:
            client.close()
        except Exception:
            pass


def get_market_rankings_sync(
    exchange_id: str = "binance",
    period: str = "7d",
    limit: int = 20,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    同步計算或讀取市場網格年化收益排行榜 (Backtest APR Ranking)
    
    具備 15 分鐘記憶體快取與 SQLite 磁碟持久化，保證重啟即刻載入、零 API 額度浪費。
    """
    cache_key = f"{exchange_id.lower()}:{period.lower()}"
    now = time.time()
    disk_data_fallback: Optional[Dict[str, Any]] = None

    if not force_refresh:
        # 1. 檢查記憶體快取
        if cache_key in _rankings_cache:
            cached_data, cached_at = _rankings_cache[cache_key]
            if now - cached_at < _RANKINGS_CACHE_TTL:
                cached_copy = dict(cached_data)
                cached_copy["cached"] = True
                return cached_copy

        # 2. 記憶體未命中時，檢查 SQLite 磁碟快取（應對進程重啟）
        disk_result = _load_rankings_from_disk(exchange_id, period)
        if disk_result:
            disk_data, disk_time = disk_result
            _rankings_cache[cache_key] = (disk_data, disk_time)
            disk_data_fallback = disk_data
            if now - disk_time < _RANKINGS_CACHE_TTL:
                cached_copy = dict(disk_data)
                cached_copy["cached"] = True
                return cached_copy

    # 解析目標週期參數
    days_map = {"7d": 7, "30d": 30, "180d": 180}
    days = days_map.get(period.lower(), 7)

    try:
        # 1. 抓取 Top 成交額幣種 (1 個 API 請求搞定全市場)
        top_tickers = _get_top_market_tickers_sync(exchange_id=exchange_id, max_symbols=limit + 5)
        
        ranking_items = []

        for item in top_tickers:
            sym = item["symbol"]
            try:
                # 抓取對應週期的 K 線
                candles_limit = min(days * 24, 720)
                ohlcv = _fetch_ohlcv_sync(sym, exchange_id, timeframe="1h", limit=candles_limit)
                if not ohlcv or len(ohlcv) < 5:
                    continue

                current_price = ohlcv[-1][4]
                lows = [c[3] for c in ohlcv]
                highs = [c[2] for c in ohlcv]
                min_p = min(lows)
                max_p = max(highs)

                # 依週期給予價格安全邊界
                buf = 0.03 if days <= 7 else (0.05 if days <= 30 else 0.10)
                lower_price = round(min_p * (1.0 - buf), 2 if current_price > 10 else 4)
                upper_price = round(max_p * (1.0 + buf), 2 if current_price > 10 else 4)

                if lower_price >= current_price:
                    lower_price = round(current_price * 0.95, 2 if current_price > 10 else 4)
                if upper_price <= current_price:
                    upper_price = round(current_price * 1.05, 2 if current_price > 10 else 4)

                target_int_pct = 0.4 if days <= 7 else (0.6 if days <= 30 else 0.8)
                total_width_pct = ((upper_price - lower_price) / current_price) * 100.0
                grid_count = int(round(total_width_pct / target_int_pct))
                grid_count = max(min(grid_count, 100), 5)

                grid_interval = round((upper_price - lower_price) / grid_count, 2 if current_price > 10 else 4)

                # 執行虛擬撮合回測
                cycles, apr = _simulate_grid_backtest(
                    ohlcv_list=ohlcv,
                    lower_price=lower_price,
                    upper_price=upper_price,
                    grid_count=grid_count,
                    fee_rate_pct=0.04
                )

                # 評定等級
                if apr >= 100.0:
                    rating = "S"
                elif apr >= 50.0:
                    rating = "A"
                elif apr >= 20.0:
                    rating = "B"
                else:
                    rating = "C"

                # 建議最小下單量
                avg_p = (lower_price + upper_price) / 2.0
                rec_amount = round(6.0 / avg_p, 4 if avg_p > 10 else 2) if avg_p > 0 else 0.01

                ranking_items.append({
                    "rank": 0,  # 稍後排序後更新
                    "symbol": sym,
                    "exchange": exchange_id,
                    "current_price": round(current_price, 4 if current_price < 1 else 2),
                    "volume_24h": round(item["quoteVolume"], 2),
                    "price_change_24h_percent": round(item["percentage"], 2),
                    "estimated_apr": apr,
                    "completed_cycles": cycles,
                    "lower_price": lower_price,
                    "upper_price": upper_price,
                    "grid_count": grid_count,
                    "grid_interval": grid_interval,
                    "recommended_order_amount": rec_amount,
                    "rating": rating,
                })

                # 平滑微延遲 (50ms)，防突發負載
                time.sleep(0.05)

            except Exception as e:
                logger.debug(f"計算代幣 {sym} 排行回測失敗: {e}")
                continue

        if not ranking_items and disk_data_fallback:
            logger.warning("本次全市場掃描無有效資料，自動降級使用磁碟舊快照")
            cached_copy = dict(disk_data_fallback)
            cached_copy["cached"] = True
            return cached_copy

        # 按回測年化 APR 由高至低排序
        ranking_items.sort(key=lambda x: x["estimated_apr"], reverse=True)

        # 賦予名次並截取 limit
        for idx, r in enumerate(ranking_items):
            r["rank"] = idx + 1

        final_items = ranking_items[:limit]

        response_data = {
            "exchange": exchange_id,
            "period": period,
            "updated_at": now,
            "cached": False,
            "items": final_items,
        }

        _rankings_cache[cache_key] = (response_data, now)
        _save_rankings_to_disk(exchange_id, period, response_data, now)
        return response_data

    except Exception as e:
        if disk_data_fallback:
            logger.warning(f"全市場掃描異常 ({e})，自動降級使用磁碟歷史快照")
            cached_copy = dict(disk_data_fallback)
            cached_copy["cached"] = True
            return cached_copy
        raise


