"""
MACD（指數平滑異同移動平均，§4.3.2）

計算式：
    EMA(x, n)：k = 2/(n+1)，種子 = 前 n 根的 SMA，之後遞迴
    MACD line = EMA(close, fast) − EMA(close, slow)（首個有效值於索引 slow−1）
    signal    = EMA(line 的有效值序列, signal)（首個有效值於索引 slow+signal−2）
    histogram = line − signal

判定方式（§4.3.2 表格）：
    cross      多: hist 由 ≤0 轉 >0   空: hist 由 ≥0 轉 <0
    hist_sign  多: hist > 0           空: hist < 0
    zero_cross 多: line 由負轉正      空: line 由正轉負
"""

from typing import List, Optional, Sequence, Tuple

from .base import (
    IndicatorResult,
    Number,
    drop_unclosed,
    validate_direction,
)
from .config import MacdConfig


def ema_series(values: Sequence[Number], period: int) -> List[Optional[float]]:
    """
    EMA 序列（種子為前 period 根的 SMA）。索引 < period−1 處為 None。
    種子定義影響數值，屬 §4.3.2 凍結規格，禁止更改。
    """
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    ema = sum(float(v) for v in values[:period]) / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = values[i] * k + ema * (1.0 - k)
        out[i] = ema
    return out


def macd_series(closes: Sequence[Number], fast: int = 12, slow: int = 26,
                signal: int = 9
                ) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """
    回傳 (line, signal, histogram) 三條等長序列。
    line 首個有效值於索引 slow−1；signal 於索引 slow+signal−2。
    """
    n = len(closes)
    efast = ema_series(closes, fast)
    eslow = ema_series(closes, slow)

    line: List[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(efast, eslow)
    ]

    # signal 在 line 的「有效值序列」上再做 EMA（§4.3.2）
    compact = [v for v in line if v is not None]
    signal_line: List[Optional[float]] = [None] * n
    compact_sig = ema_series(compact, signal)
    offset = n - len(compact)
    for j, value in enumerate(compact_sig):
        if value is not None:
            signal_line[offset + j] = value

    hist: List[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(line, signal_line)
    ]
    return line, signal_line, hist


def _triggered_at(line: Sequence[Optional[float]],
                  sig: Sequence[Optional[float]],
                  hist: Sequence[Optional[float]],
                  i: int, condition: str, direction: str) -> bool:
    cur_l, cur_s, cur_h = line[i], sig[i], hist[i]
    prev_l = line[i - 1] if i >= 1 else None
    prev_h = hist[i - 1] if i >= 1 else None

    if direction == "long":
        if condition == "cross":
            return prev_h is not None and cur_h is not None and prev_h <= 0 < cur_h
        if condition == "hist_sign":
            return cur_h is not None and cur_h > 0
        return prev_l is not None and cur_l is not None and prev_l <= 0 < cur_l  # zero_cross
    else:
        if condition == "cross":
            return prev_h is not None and cur_h is not None and prev_h >= 0 > cur_h
        if condition == "hist_sign":
            return cur_h is not None and cur_h < 0
        return prev_l is not None and cur_l is not None and prev_l >= 0 > cur_l  # zero_cross


def trigger_series(closes: Sequence[Number], cfg: MacdConfig, direction: str) -> List[bool]:
    """逐棒判定序列（供 EntryEvaluator 的 confirm_bars 使用）。"""
    validate_direction(direction)
    line, sig, hist = macd_series(closes, cfg.fast, cfg.slow, cfg.signal)
    return [
        _triggered_at(line, sig, hist, i, cfg.condition, direction)
        for i in range(len(closes))
    ]


def evaluate(closes: Sequence[Number], cfg: Optional[MacdConfig] = None,
             direction: str = "long", last_bar_closed: bool = True) -> IndicatorResult:
    """評估最後一根已收線 K 棒上的 MACD 狀態。value 固定為 histogram。"""
    validate_direction(direction)
    cfg = cfg or MacdConfig()
    values = drop_unclosed(closes, last_bar_closed)
    line, sig, hist = macd_series(values, cfg.fast, cfg.slow, cfg.signal)

    # zero_cross 只需 line；cross/hist_sign 需 signal（暖機更深）
    needs_signal = cfg.condition in ("cross", "hist_sign")
    warmup_need = cfg.slow + cfg.signal - 1 if needs_signal else cfg.slow

    n = len(values)
    as_of = n - 1 if n else None
    cur_h = hist[-1] if hist else None
    cur_l = line[-1] if line else None
    cur_s = sig[-1] if sig else None
    value = cur_h
    ready = value is not None

    if not ready:
        return IndicatorResult(
            value=None,
            triggered=False,
            insufficient_data=True,
            warmup_have=min(n, warmup_need),
            warmup_need=warmup_need,
            as_of=as_of,
            details={"macd": cur_l, "signal": cur_s, "histogram": cur_h},
        )

    return IndicatorResult(
        value=value,
        triggered=_triggered_at(line, sig, hist, n - 1, cfg.condition, direction),
        insufficient_data=False,
        warmup_have=warmup_need,
        warmup_need=warmup_need,
        as_of=as_of,
        details={"macd": cur_l, "signal": cur_s, "histogram": cur_h},
    )
