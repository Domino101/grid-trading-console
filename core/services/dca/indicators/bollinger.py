"""
Bollinger Bands（布林通道，§4.3.3）

計算式：
    mid   = SMA(close, period)
    σ     = 母體標準差（除以 n，非 n−1；與 TradingView 一致，凍結規格）
    upper = mid + std_dev × σ
    lower = mid − std_dev × σ
    %B    = (close − lower) / (upper − lower)

首個有效值位於索引 period−1；暖機需求 = period 根。

判定方式（§4.3.3 表格，穿越類條件皆比較「當棒 close 對當棒通道」）：
    touch   多: close ≤ lower                       空: close ≥ upper
    break   多: 前棒 > 前棒 lower 且當棒 ≤ 當棒 lower  空: 前棒 < 前棒 upper 且當棒 ≥ 當棒 upper
    return  多: 前棒 ≤ 前棒 lower 且當棒 > 當棒 lower  空: 前棒 ≥ 前棒 upper 且當棒 < 當棒 upper
"""

from statistics import mean, pstdev
from typing import List, Optional, Sequence, Tuple

from .base import (
    IndicatorResult,
    Number,
    drop_unclosed,
    validate_direction,
)
from .config import BollingerConfig

Band = Tuple[float, float, float]  # (lower, mid, upper)


def bollinger_series(closes: Sequence[Number], period: int = 20,
                     std_dev: float = 2.0) -> List[Optional[Band]]:
    """回傳與輸入等長的 (lower, mid, upper) 序列；暖機期為 None。"""
    n = len(closes)
    out: List[Optional[Band]] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        mid = mean(window)
        sigma = pstdev(window)
        out[i] = (mid - std_dev * sigma, mid, mid + std_dev * sigma)
    return out


def _triggered_at(closes: Sequence[Number], bands: Sequence[Optional[Band]],
                  i: int, condition: str, direction: str) -> bool:
    cur_band = bands[i]
    if cur_band is None:
        return False
    lower, _, upper = cur_band
    close = closes[i]
    prev_band = bands[i - 1] if i >= 1 else None
    prev_close = closes[i - 1] if i >= 1 else None

    if direction == "long":
        if condition == "touch":
            return close <= lower
        if prev_band is None or prev_close is None:
            return False
        prev_lower = prev_band[0]
        if condition == "break":
            return prev_close > prev_lower and close <= lower
        return prev_close <= prev_lower and close > lower  # return
    else:
        if condition == "touch":
            return close >= upper
        if prev_band is None or prev_close is None:
            return False
        prev_upper = prev_band[2]
        if condition == "break":
            return prev_close < prev_upper and close >= upper
        return prev_close >= prev_upper and close < upper  # return


def trigger_series(closes: Sequence[Number], cfg: BollingerConfig,
                   direction: str) -> List[bool]:
    """逐棒判定序列（供 EntryEvaluator 的 confirm_bars 使用）。"""
    validate_direction(direction)
    bands = bollinger_series(closes, cfg.period, cfg.std_dev)
    return [
        _triggered_at(closes, bands, i, cfg.condition, direction)
        for i in range(len(closes))
    ]


def evaluate(closes: Sequence[Number], cfg: Optional[BollingerConfig] = None,
             direction: str = "long", last_bar_closed: bool = True) -> IndicatorResult:
    """評估最後一根已收線 K 棒上的布林通道狀態。value 固定為 %B。"""
    validate_direction(direction)
    cfg = cfg or BollingerConfig()
    values = drop_unclosed(closes, last_bar_closed)
    bands = bollinger_series(values, cfg.period, cfg.std_dev)

    warmup_need = cfg.period
    n = len(values)
    as_of = n - 1 if n else None
    cur_band = bands[-1] if bands else None

    if cur_band is None:
        return IndicatorResult(
            value=None,
            triggered=False,
            insufficient_data=True,
            warmup_have=min(n, warmup_need),
            warmup_need=warmup_need,
            as_of=as_of,
            details={"lower": None, "mid": None, "upper": None, "percent_b": None},
        )

    lower, mid, upper = cur_band
    width = upper - lower
    percent_b = (values[-1] - lower) / width if width != 0 else None

    return IndicatorResult(
        value=percent_b,
        triggered=_triggered_at(values, bands, n - 1, cfg.condition, direction),
        insufficient_data=False,
        warmup_have=warmup_need,
        warmup_need=warmup_need,
        as_of=as_of,
        details={"lower": lower, "mid": mid, "upper": upper, "percent_b": percent_b},
    )
