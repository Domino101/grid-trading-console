"""Candle models and pure sequence utilities for backtesting.

Zero I/O, pure Decimal representations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Candle:
    """Immutable candlestick representation with strict validation."""
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close):
            raise ValueError(f"high ({self.high}) cannot be lower than open ({self.open}) or close ({self.close})")
        if self.low > min(self.open, self.close):
            raise ValueError(f"low ({self.low}) cannot be higher than open ({self.open}) or close ({self.close})")


def make_candle(
    ts: int,
    open_price: Any,
    high_price: Any,
    low_price: Any,
    close_price: Any,
    volume: Any = 0,
) -> Candle:
    """Convenience constructor from numeric, string, or Decimal values."""
    return Candle(
        ts=int(ts),
        open=Decimal(str(open_price)),
        high=Decimal(str(high_price)),
        low=Decimal(str(low_price)),
        close=Decimal(str(close_price)),
        volume=Decimal(str(volume)),
    )


def to_candles(raw_candles: Sequence[Any]) -> List[Candle]:
    """Convert various candle representations (StoredKline, dict, tuple, OHLCVData) to Candle list."""
    out: List[Candle] = []
    for c in raw_candles:
        if isinstance(c, Candle):
            out.append(c)
        elif hasattr(c, "ts") and hasattr(c, "close"):
            # e.g. StoredKline
            out.append(
                Candle(
                    ts=int(c.ts),
                    open=Decimal(str(c.open)),
                    high=Decimal(str(c.high)),
                    low=Decimal(str(c.low)),
                    close=Decimal(str(c.close)),
                    volume=Decimal(str(getattr(c, "volume", 0))),
                )
            )
        elif hasattr(c, "timestamp") and hasattr(c, "close"):
            # e.g. OHLCVData
            dt = getattr(c, "timestamp")
            ts_sec = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(dt)
            out.append(
                Candle(
                    ts=ts_sec,
                    open=Decimal(str(getattr(c, "open", 0))),
                    high=Decimal(str(getattr(c, "high", 0))),
                    low=Decimal(str(getattr(c, "low", 0))),
                    close=Decimal(str(getattr(c, "close", 0))),
                    volume=Decimal(str(getattr(c, "volume", 0))),
                )
            )
        elif isinstance(c, dict):
            ts = c.get("ts") or c.get("t") or 0
            ts_sec = int(ts // 1000 if ts > 10_000_000_000 else ts)
            out.append(
                Candle(
                    ts=ts_sec,
                    open=Decimal(str(c.get("open") or c.get("o") or "0")),
                    high=Decimal(str(c.get("high") or c.get("h") or "0")),
                    low=Decimal(str(c.get("low") or c.get("l") or "0")),
                    close=Decimal(str(c.get("close") or c.get("c") or "0")),
                    volume=Decimal(str(c.get("volume") or c.get("v") or "0")),
                )
            )
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            ts = c[0]
            ts_sec = int(ts // 1000 if ts > 10_000_000_000 else ts)
            vol = c[5] if len(c) > 5 else 0
            out.append(
                Candle(
                    ts=ts_sec,
                    open=Decimal(str(c[1])),
                    high=Decimal(str(c[2])),
                    low=Decimal(str(c[3])),
                    close=Decimal(str(c[4])),
                    volume=Decimal(str(vol)),
                )
            )
    return out
