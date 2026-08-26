"""
指標設定與預設值 —— §4.3 表格的唯一真相來源

四種指標（RSI / MACD / Bollinger / Price Change）的參數預設與合法範圍、
以及共用 K 線管線參數（timeframe / fetch_limit / price_source / logic /
confirm_bars），全部在本檔集中定義，供 DcaConfig、EntryEvaluator、
後端 API 與前端預設值共用。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple, Union


class IndicatorType(Enum):
    RSI = "rsi"
    MACD = "macd"
    BOLLINGER = "bollinger"
    PRICE_CHANGE = "price_change"


# ── §4.3.0 共用 K 線管線預設（所有指標強制共用） ──
DEFAULT_TIMEFRAME = "15m"
VALID_TIMEFRAMES: Tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
DEFAULT_FETCH_LIMIT = 300
VALID_FETCH_LIMIT_RANGE: Tuple[int, int] = (100, 500)
DEFAULT_PRICE_SOURCE = "close"
VALID_PRICE_SOURCES: Tuple[str, ...] = ("close", "hlc3", "ohlc4")
DEFAULT_ENTRY_LOGIC = "all"
DEFAULT_CONFIRM_BARS = 1

# 各指標合法判定方式（§4.3.1–4.3.4）
RSI_CONDITIONS: Tuple[str, ...] = ("level", "cross_in", "cross_out")
MACD_CONDITIONS: Tuple[str, ...] = ("cross", "hist_sign", "zero_cross")
BOLLINGER_CONDITIONS: Tuple[str, ...] = ("touch", "break", "return")


@dataclass(frozen=True)
class RsiConfig:
    """RSI（§4.3.1）。condition: level（預設）/ cross_in / cross_out"""
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    condition: str = "level"

    def __post_init__(self):
        if not 2 <= self.period <= 100:
            raise ValueError(f"RSI period 必須在 2-100 之間，收到: {self.period}")
        if not 1 <= self.oversold <= 50:
            raise ValueError(f"RSI oversold 必須在 1-50 之間，收到: {self.oversold}")
        if not 50 <= self.overbought <= 99:
            raise ValueError(f"RSI overbought 必須在 50-99 之間，收到: {self.overbought}")
        if self.condition not in RSI_CONDITIONS:
            raise ValueError(
                f"無效的 RSI condition: {self.condition!r}，合法值: {', '.join(RSI_CONDITIONS)}"
            )


@dataclass(frozen=True)
class MacdConfig:
    """MACD（§4.3.2）。condition: cross（預設）/ hist_sign / zero_cross"""
    fast: int = 12
    slow: int = 26
    signal: int = 9
    condition: str = "cross"

    def __post_init__(self):
        if not 2 <= self.fast <= 100:
            raise ValueError(f"MACD fast 必須在 2-100 之間，收到: {self.fast}")
        if not 3 <= self.slow <= 200:
            raise ValueError(f"MACD slow 必須在 3-200 之間，收到: {self.slow}")
        if not 2 <= self.signal <= 50:
            raise ValueError(f"MACD signal 必須在 2-50 之間，收到: {self.signal}")
        if self.slow <= self.fast:
            raise ValueError(f"MACD slow（{self.slow}）必須大於 fast（{self.fast}）")
        if self.condition not in MACD_CONDITIONS:
            raise ValueError(
                f"無效的 MACD condition: {self.condition!r}，合法值: {', '.join(MACD_CONDITIONS)}"
            )


@dataclass(frozen=True)
class BollingerConfig:
    """Bollinger Bands（§4.3.3）。condition: touch（預設）/ break / return"""
    period: int = 20
    std_dev: float = 2.0
    condition: str = "touch"

    def __post_init__(self):
        if not 2 <= self.period <= 200:
            raise ValueError(f"Bollinger period 必須在 2-200 之間，收到: {self.period}")
        if not 0.5 <= self.std_dev <= 5.0:
            raise ValueError(f"Bollinger std_dev 必須在 0.5-5.0 之間，收到: {self.std_dev}")
        if self.condition not in BOLLINGER_CONDITIONS:
            raise ValueError(
                f"無效的 Bollinger condition: {self.condition!r}，"
                f"合法值: {', '.join(BOLLINGER_CONDITIONS)}"
            )


@dataclass(frozen=True)
class PriceChangeConfig:
    """
    Price Change（§4.3.4）。

    門檻依方向分開指定：做多預設 -3.0（跌幅達標進場）、做空預設 +3.0。
    lookback_bars=24 指「往回第 24 根」（close[i] vs close[i-24]），
    索引語意已被鎖定向量 §4.5 凍結，禁止更改。
    """
    lookback_bars: int = 24
    long_threshold_percent: float = -3.0
    short_threshold_percent: float = 3.0

    def __post_init__(self):
        if not 1 <= self.lookback_bars <= 500:
            raise ValueError(
                f"PriceChange lookback_bars 必須在 1-500 之間，收到: {self.lookback_bars}"
            )
        if not -100 <= self.long_threshold_percent <= 100:
            raise ValueError(
                f"PriceChange long_threshold_percent 必須在 -100~100 之間，"
                f"收到: {self.long_threshold_percent}"
            )
        if not -100 <= self.short_threshold_percent <= 100:
            raise ValueError(
                f"PriceChange short_threshold_percent 必須在 -100~100 之間，"
                f"收到: {self.short_threshold_percent}"
            )


IndicatorConfig = Union[RsiConfig, MacdConfig, BollingerConfig, PriceChangeConfig]
