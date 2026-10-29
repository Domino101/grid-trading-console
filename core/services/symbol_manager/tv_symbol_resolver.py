"""
TradingView 符號解析與專案標準化對應模組 (TradingView Symbol Resolver)

職責:
1. 解析 TradingView 符號 (例如 BINANCE:SOLUSDC.P, BINANCE:BTCUSDT, HYPERLIQUID:SOLUSDC.P)
2. 轉換為本專案/CCXT 的標準交易對格式 (例如 SOL/USDC:USDC, SOL/USDC, SOL_USDC_PERP, BTC)
3. 將專案/交易所格式交易對反向轉換為 TradingView 檢索符號
4. 支援永續合約 (.P / swap) 與現貨 (spot) 的精確區分
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


# 常見計價幣種（長度由長到短排序，防止前綴誤匹配）
KNOWN_QUOTES = ("FDUSD", "USDC", "USDT", "BUSD", "USD", "EUR", "BTC", "ETH")

# 交易所名稱對齊 (TradingView exchange -> 專案 exchange)
EXCHANGE_MAP_TV_TO_PROJECT = {
    "BINANCE": "binance",
    "HYPERLIQUID": "hyperliquid",
    "BACKPACK": "backpack",
    "LIGHTER": "lighter",
    "BYBIT": "bybit",
    "OKX": "okx",
}

# 反向對齊 (專案 exchange -> TradingView exchange)
EXCHANGE_MAP_PROJECT_TO_TV = {v: k for k, v in EXCHANGE_MAP_TV_TO_PROJECT.items()}


@dataclass(frozen=True)
class TradingViewSymbolInfo:
    """TradingView 符號解析結果"""
    tv_symbol: str             # 原始 TV 符號，例如 "BINANCE:SOLUSDC.P"
    tv_exchange: str          # TV 交易所標識，例如 "BINANCE"
    ticker: str               # TV 代號主體，例如 "SOLUSDC.P"
    base: str                 # 基礎幣種，例如 "SOL"
    quote: str                # 計價幣種，例如 "USDC"
    is_perp: bool             # 是否為永續合約
    exchange: str             # 專案內部交易所 ID，例如 "binance"
    canonical_symbol: str     # 專案標準交易對格式，例如 "SOL/USDC:USDC"


def parse_tv_symbol(tv_symbol: str, default_exchange: str = "binance") -> TradingViewSymbolInfo:
    """
    解析 TradingView 格式的符號字符串。
    
    支援格式:
      - "BINANCE:SOLUSDC.P" -> Binance 永續 (SOL/USDC:USDC)
      - "BINANCE:BTCUSDT"   -> Binance 現貨 (BTC/USDT)
      - "HYPERLIQUID:SOLUSDC.P" -> Hyperliquid 永續 (SOL/USDC:USDC)
      - "BYBIT:BTCUSDT.P"   -> Bybit 永續 (BTC/USDT:USDT)
      - "SOLUSDC.P"         -> 預設 default_exchange 永續
      - "SOL/USDC"          -> 現貨符號
    """
    raw = tv_symbol.strip()
    if not raw:
        raise ValueError("TradingView 符號不能為空")

    tv_exchange = ""
    ticker = raw

    if ":" in raw:
        parts = raw.split(":", 1)
        tv_exchange = parts[0].upper()
        ticker = parts[1]

    # 判斷是否為永續合約 (.P 後綴或顯式 PERP/SWAP)
    is_perp = False
    clean_ticker = ticker
    if clean_ticker.upper().endswith(".P"):
        is_perp = True
        clean_ticker = clean_ticker[:-2]
    elif clean_ticker.upper().endswith("_PERP") or clean_ticker.upper().endswith("-SWAP"):
        is_perp = True
        clean_ticker = re.sub(r"(_PERP|-SWAP)$", "", clean_ticker, flags=re.IGNORECASE)

    # 去除斜線或減號以便提取 base/quote
    norm_ticker = clean_ticker.replace("/", "").replace("-", "").replace("_", "").upper()

    # 提取 base 與 quote
    matched_quote = ""
    matched_base = ""
    for q in KNOWN_QUOTES:
        if norm_ticker.endswith(q) and len(norm_ticker) > len(q):
            matched_quote = q
            matched_base = norm_ticker[:-len(q)]
            break

    if not matched_quote:
        # 特殊情況 (例如 LIGHTER:BTC 只有 base)
        matched_base = norm_ticker
        matched_quote = "USD" if is_perp else "USDT"

    # 解析目標交易所
    exchange = EXCHANGE_MAP_TV_TO_PROJECT.get(tv_exchange, default_exchange.lower())
    if not tv_exchange:
        tv_exchange = EXCHANGE_MAP_PROJECT_TO_TV.get(exchange, "BINANCE")

    # Lighter 專屬處理：Lighter 為純永續合約 DEX
    if exchange == "lighter":
        is_perp = True

    # 構造專案標準代號 (Canonical Symbol)
    canonical_symbol = build_canonical_symbol(
        exchange=exchange,
        base=matched_base,
        quote=matched_quote,
        is_perp=is_perp
    )

    full_tv_symbol = f"{tv_exchange}:{ticker}" if ":" not in raw else raw

    return TradingViewSymbolInfo(
        tv_symbol=full_tv_symbol,
        tv_exchange=tv_exchange,
        ticker=ticker,
        base=matched_base,
        quote=matched_quote,
        is_perp=is_perp,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
    )


def build_canonical_symbol(exchange: str, base: str, quote: str, is_perp: bool) -> str:
    """
    根據交易所及合約性質構造本系統/CCXT使用的規範符號。
    """
    ex = exchange.lower()
    b = base.upper()
    q = quote.upper()

    if ex == "binance":
        if is_perp:
            return f"{b}/{q}:{q}"
        return f"{b}/{q}"

    elif ex == "hyperliquid":
        if is_perp:
            return f"{b}/{q}:{q}"
        return f"{b}/{q}"

    elif ex == "backpack":
        if is_perp:
            return f"{b}_{q}_PERP"
        return f"{b}_{q}"

    elif ex == "lighter":
        if is_perp:
            return b
        return f"{b}-{q}"

    elif ex == "bybit":
        if is_perp:
            return f"{b}/{q}:{q}"
        return f"{b}/{q}"

    elif ex == "okx":
        if is_perp:
            return f"{b}/{q}:{q}"
        return f"{b}/{q}"

    # 預設通用 CCXT 規則
    if is_perp:
        return f"{b}/{q}:{q}"
    return f"{b}/{q}"


def to_tv_symbol(exchange: str, canonical_symbol: str) -> str:
    """
    將本專案/CCXT 規範交易對符號轉換為 TradingView 檢索代號。
    
    範例:
      - ("binance", "SOL/USDC:USDC") -> "BINANCE:SOLUSDC.P"
      - ("binance", "SOL/USDC")      -> "BINANCE:SOLUSDC"
      - ("binance", "BR/USDT:USDT")  -> "BINANCE:BRUSDT.P"
      - ("hyperliquid", "BTC/USDC:USDC") -> "HYPERLIQUID:BTCUSDC.P"
      - ("backpack", "SOL_USDC_PERP") -> "BACKPACK:SOLUSDC.P"
      - ("lighter", "BTC")           -> "LIGHTER:BTCUSD.P"
    """
    ex = exchange.lower()
    tv_ex = EXCHANGE_MAP_PROJECT_TO_TV.get(ex, ex.upper())
    s = canonical_symbol.strip()

    is_perp = False
    base = ""
    quote = ""

    # CCXT 永續標準 (例如 BASE/QUOTE:SETTLE)
    if ":" in s:
        is_perp = True
        parts = s.split(":")
        pair = parts[0].split("/")
        base = pair[0].upper()
        quote = pair[1].upper() if len(pair) > 1 else parts[1].upper()
    elif s.endswith("_PERP"):
        is_perp = True
        parts = s[:-5].split("_")
        base = parts[0].upper()
        quote = parts[1].upper() if len(parts) > 1 else "USDC"
    elif "/" in s:
        parts = s.split("/")
        base = parts[0].upper()
        quote = parts[1].upper()
    elif "_" in s:
        parts = s.split("_")
        base = parts[0].upper()
        quote = parts[1].upper()
    elif "-" in s:
        parts = s.split("-")
        base = parts[0].upper()
        quote = parts[1].upper()
    else:
        # 純代碼 (例如 Lighter 的 BTC)
        base = s.upper()
        quote = "USD"
        is_perp = True

    ticker = f"{base}{quote}"
    if is_perp:
        ticker += ".P"

    return f"{tv_ex}:{ticker}"


def resolve_to_project_symbol(
    tv_symbol: str,
    target_exchange: Optional[str] = None
) -> Tuple[str, str]:
    """
    快捷解析函數：將 TradingView 符號轉換為 (exchange, canonical_symbol) 元組。
    可由呼叫者強制指定 target_exchange。
    """
    info = parse_tv_symbol(tv_symbol, default_exchange=target_exchange or "binance")
    final_exchange = (target_exchange or info.exchange).lower()
    
    canonical = build_canonical_symbol(
        exchange=final_exchange,
        base=info.base,
        quote=info.quote,
        is_perp=info.is_perp
    )
    return final_exchange, canonical
