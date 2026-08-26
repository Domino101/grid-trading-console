"""
core/services/dca/backtest —— DCA 即時圖表與回測子系統核心套件

包含：
- Candle 不可變資料模型與序列轉換純函式（candles.py）
- ReplayExchange 確定性 K 線撮合交易所（replay_exchange.py）
- TradeMark, EquityPoint, CycleRecord, BacktestRequest, BacktestResult 資料模型（models.py）
- ReplayDriver 回放驅動器（replay_driver.py）
"""

from .candles import Candle, make_candle, to_candles
from .models import (
    BacktestRequest,
    BacktestResult,
    CycleRecord,
    EquityPoint,
    TradeMark,
)
from .replay_driver import ReplayDriver
from .replay_exchange import ReplayExchange, ReplayOrderRecord

__all__ = [
    "Candle",
    "make_candle",
    "to_candles",
    "ReplayExchange",
    "ReplayOrderRecord",
    "TradeMark",
    "EquityPoint",
    "CycleRecord",
    "BacktestRequest",
    "BacktestResult",
    "ReplayDriver",
]
