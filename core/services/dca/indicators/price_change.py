"""
Price Change（區間漲跌幅，§4.3.4）

計算式：
    change% = (close_i − close_{i−lookback}) / close_{i−lookback} × 100

索引語意（凍結，稽核項 1.5b）：
    lookback_bars=24 指「往回第 24 根」——close[119] 對 close[95]；
    不是 Python 的 close[-24]（那是往回第 23 根）。
    實際時間長度 = lookback_bars × timeframe（例：24 × 15m = 6 小時）。

首個有效值位於索引 lookback_bars；暖機需求 = lookback_bars + 1 根。

判定方式：
    多: change% ≤ long_threshold_percent（跌幅達標，預設 -3.0）
    空: change% ≥ short_threshold_percent（漲幅達標，預設 +3.0）
"""

from typing import List, Optional, Sequence

from .base import (
    IndicatorResult,
    Number,
    drop_unclosed,
    validate_direction,
)
from .config import PriceChangeConfig


def price_change_series(closes: Sequence[Number],
                        lookback_bars: int = 24) -> List[Optional[float]]:
    """回傳與輸入等長的漲跌幅序列；索引 < lookback_bars 處為 None。"""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    for i in range(lookback_bars, n):
        base = closes[i - lookback_bars]
        if base == 0:
            continue
        out[i] = (closes[i] - base) / base * 100.0
    return out


def _triggered_at(change: Optional[float], cfg: PriceChangeConfig,
                  direction: str) -> bool:
    if change is None:
        return False
    if direction == "long":
        return change <= cfg.long_threshold_percent
    return change >= cfg.short_threshold_percent


def trigger_series(closes: Sequence[Number], cfg: PriceChangeConfig,
                   direction: str) -> List[bool]:
    """逐棒判定序列（供 EntryEvaluator 的 confirm_bars 使用）。"""
    validate_direction(direction)
    series = price_change_series(closes, cfg.lookback_bars)
    return [_triggered_at(v, cfg, direction) for v in series]


def evaluate(closes: Sequence[Number], cfg: Optional[PriceChangeConfig] = None,
             direction: str = "long", last_bar_closed: bool = True) -> IndicatorResult:
    """評估最後一根已收線 K 棒上的區間漲跌幅。"""
    validate_direction(direction)
    cfg = cfg or PriceChangeConfig()
    values = drop_unclosed(closes, last_bar_closed)
    series = price_change_series(values, cfg.lookback_bars)

    warmup_need = cfg.lookback_bars + 1
    n = len(values)
    as_of = n - 1 if n else None
    value = series[-1] if series else None

    if value is None:
        return IndicatorResult(
            value=None,
            triggered=False,
            insufficient_data=True,
            warmup_have=min(n, warmup_need),
            warmup_need=warmup_need,
            as_of=as_of,
            details={"change_percent": None},
        )

    return IndicatorResult(
        value=value,
        triggered=_triggered_at(value, cfg, direction),
        insufficient_data=False,
        warmup_have=warmup_need,
        warmup_need=warmup_need,
        as_of=as_of,
        details={"change_percent": value, "lookback_bars": float(cfg.lookback_bars)},
    )
