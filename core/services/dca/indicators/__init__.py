"""
DCA 技術指標套件（§4.3）

四種指標共用同一條 K 線管線的計算結果（純函式、純 stdlib）：
RSI / MACD / Bollinger Bands / Price Change。

本套件提供兩種介面：
- evaluate_indicator()：最後一根已收線 K 棒的 IndicatorResult（含暖機狀態）
- indicator_trigger_series()：逐棒 bool 序列（供 EntryEvaluator 的
  confirm_bars 連續確認使用）
"""

from typing import Callable, Dict, List, Sequence

from . import bollinger, macd, price_change, rsi
from .base import IndicatorResult, Number, drop_unclosed, first_valid_index
from .config import (
    BollingerConfig,
    IndicatorConfig,
    IndicatorType,
    MacdConfig,
    PriceChangeConfig,
    RsiConfig,
)

_CONFIG_TYPE_MAP: Dict[type, IndicatorType] = {
    RsiConfig: IndicatorType.RSI,
    MacdConfig: IndicatorType.MACD,
    BollingerConfig: IndicatorType.BOLLINGER,
    PriceChangeConfig: IndicatorType.PRICE_CHANGE,
}

_EVALUATORS: Dict[IndicatorType, Callable[..., IndicatorResult]] = {
    IndicatorType.RSI: rsi.evaluate,
    IndicatorType.MACD: macd.evaluate,
    IndicatorType.BOLLINGER: bollinger.evaluate,
    IndicatorType.PRICE_CHANGE: price_change.evaluate,
}

_TRIGGER_SERIES: Dict[IndicatorType, Callable[..., List[bool]]] = {
    IndicatorType.RSI: rsi.trigger_series,
    IndicatorType.MACD: macd.trigger_series,
    IndicatorType.BOLLINGER: bollinger.trigger_series,
    IndicatorType.PRICE_CHANGE: price_change.trigger_series,
}


def _indicator_type(cfg: IndicatorConfig) -> IndicatorType:
    ind_type = _CONFIG_TYPE_MAP.get(type(cfg))
    if ind_type is None:
        raise ValueError(f"未知的指標設定型別: {type(cfg).__name__}")
    return ind_type


def evaluate_indicator(cfg: IndicatorConfig, closes: Sequence[Number],
                       direction: str = "long",
                       last_bar_closed: bool = True) -> IndicatorResult:
    """對最後一根已收線 K 棒評估單一指標（未收線的尾棒一律丟棄）。"""
    return _EVALUATORS[_indicator_type(cfg)](
        closes, cfg, direction, last_bar_closed
    )


def indicator_trigger_series(cfg: IndicatorConfig, closes: Sequence[Number],
                             direction: str) -> List[bool]:
    """產生單一指標的逐棒判定序列（已收線部分）。"""
    return _TRIGGER_SERIES[_indicator_type(cfg)](closes, cfg, direction)


__all__ = [
    "BollingerConfig",
    "IndicatorConfig",
    "IndicatorResult",
    "IndicatorType",
    "MacdConfig",
    "PriceChangeConfig",
    "RsiConfig",
    "drop_unclosed",
    "evaluate_indicator",
    "first_valid_index",
    "indicator_trigger_series",
]
