"""
RSI（相對強弱指標，§4.3.1）

計算式（Wilder 平滑）：
    Δ_i = close_i − close_{i−1}
    初始（第 period 根）avgGain/avgLoss = 前 period 根 gain/loss 的算術平均
    遞迴：avgGain = (avgGain × (period−1) + gain_i) / period
    RSI = 100 − 100 / (1 + RS)；avgLoss = 0 時 RSI = 100

第一個有效值位於索引 period（close[period]），暖機需求 = period + 1 根。

判定方式（§4.3.1 表格）：
    level      多: RSI ≤ oversold          空: RSI ≥ overbought
    cross_in   多: 由上向下穿越 oversold    空: 由下向上穿越 overbought
    cross_out  多: 由下向上穿回 oversold    空: 由上向下穿回 overbought
"""

from typing import List, Optional, Sequence

from .base import (
    IndicatorResult,
    Number,
    drop_unclosed,
    validate_direction,
)
from .config import RsiConfig


def rsi_series(closes: Sequence[Number], period: int = 14) -> List[Optional[float]]:
    """
    回傳與輸入等長的 RSI 序列；索引 < period 處為 None（暖機期）。
    序列長度 ≤ period 時全為 None。
    """
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out

    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            avg_gain += delta
        else:
            avg_loss -= delta
    avg_gain /= period
    avg_loss /= period

    def _to_rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        rs = g / l
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = _to_rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _to_rsi(avg_gain, avg_loss)
    return out


def _triggered_at(series: Sequence[Optional[float]], i: int,
                  cfg: RsiConfig, direction: str) -> bool:
    """單一 K 棒上的 RSI 判定；前一棒無有效值時穿越類條件為 False。"""
    cur = series[i]
    if cur is None:
        return False
    prev = series[i - 1] if i >= 1 else None

    if direction == "long":
        if cfg.condition == "level":
            return cur <= cfg.oversold
        if cfg.condition == "cross_in":
            return prev is not None and prev > cfg.oversold and cur <= cfg.oversold
        return prev is not None and prev <= cfg.oversold and cur > cfg.oversold  # cross_out
    else:
        if cfg.condition == "level":
            return cur >= cfg.overbought
        if cfg.condition == "cross_in":
            return prev is not None and prev < cfg.overbought and cur >= cfg.overbought
        return prev is not None and prev >= cfg.overbought and cur < cfg.overbought  # cross_out


def trigger_series(closes: Sequence[Number], cfg: RsiConfig, direction: str) -> List[bool]:
    """逐棒判定序列（供 EntryEvaluator 的 confirm_bars 使用）。"""
    validate_direction(direction)
    series = rsi_series(closes, cfg.period)
    return [_triggered_at(series, i, cfg, direction) for i in range(len(series))]


def evaluate(closes: Sequence[Number], cfg: Optional[RsiConfig] = None,
             direction: str = "long", last_bar_closed: bool = True) -> IndicatorResult:
    """評估最後一根已收線 K 棒上的 RSI 狀態。"""
    validate_direction(direction)
    cfg = cfg or RsiConfig()
    values = drop_unclosed(closes, last_bar_closed)
    series = rsi_series(values, cfg.period)

    warmup_need = cfg.period + 1
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
            details={"rsi": None},
        )

    return IndicatorResult(
        value=value,
        triggered=_triggered_at(series, n - 1, cfg, direction),
        insufficient_data=False,
        warmup_have=warmup_need,
        warmup_need=warmup_need,
        as_of=as_of,
        details={"rsi": value},
    )
