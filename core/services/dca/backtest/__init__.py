"""
core/services/dca/backtest —— DCA 即時圖表與回測子系統核心套件

包含：
- Candle 不可變資料模型與序列轉換純函式（candles.py）
- ReplayExchange 確定性 K 線撮合交易所（replay_exchange.py）
- TradeMark, EquityPoint, CycleRecord, BacktestRequest, BacktestResult 資料模型（models.py）
- ReplayDriver 回放驅動器（replay_driver.py）
- compute_metrics 績效指標純函式（metrics.py）
- expand_grid / run_sweep 參數掃描（sweep.py）
"""

from .candles import Candle, make_candle, to_candles
from .metrics import buy_hold_return_pct, compute_metrics, max_drawdown_pct
from .models import (
    BacktestRequest,
    BacktestResult,
    CycleRecord,
    EquityPoint,
    TradeMark,
)
from .replay_driver import ReplayDriver
from .replay_exchange import ReplayExchange, ReplayOrderRecord
from .sweep import (
    SweepBudget,
    SweepResult,
    SweepRun,
    bar_should_enter_series,
    expand_grid,
    precompute_series_fns,
    run_sweep,
)

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
    "compute_metrics",
    "buy_hold_return_pct",
    "max_drawdown_pct",
    "SweepRun",
    "SweepBudget",
    "SweepResult",
    "expand_grid",
    "precompute_series_fns",
    "bar_should_enter_series",
    "run_sweep",
]
