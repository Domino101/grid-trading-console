"""
core/services/dca/backtest/sweep.py —— DCA 回測參數掃描（預算上限 + truncated）

依據：docs/dca_chart_backtest_plan_zh-TW.md Phase C4 / 稽核 4.7–4.10

- expand_grid()：笛卡兒積展開；非法組合標記 error（中文原因），不崩潰
- run_sweep()：ThreadPoolExecutor 並行，同時執行中的 replay ≤ SWEEP_MAX_WORKERS
- 總棒數預算 SWEEP_MAX_TOTAL_BARS；超預算停止並 truncated=true，不回報未完成 run 的指標
- 掃描加速：固定 entry 時預先以 indicator_trigger_series 計算每一個
  fetch_limit 視窗的觸發序列，經 EntryEvaluator(series_fns=...) 注入
  （必須與逐棒重算等價，稽核 4.8）
- 零檔案副作用、不碰 active_dca.json（BP-2 / G-12）
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from itertools import product
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .candles import Candle, to_candles
from .models import BacktestResult
from .replay_driver import ReplayDriver
from ..entry.evaluator import EntryEvaluator, TriggerSeriesFn
from ..indicators import indicator_trigger_series
from ..models.dca_config import DcaConfig, DcaConfigError, EntryConfig, EntryMode
from core.logging import get_logger

logger = get_logger(__name__)

SWEEP_MAX_WORKERS = int(os.environ.get("SWEEP_MAX_WORKERS", "4"))
SWEEP_MAX_TOTAL_BARS = int(os.environ.get("SWEEP_MAX_TOTAL_BARS", "300000"))

# 可掃描的 DcaConfig 頂層欄位（v1；不重寫階梯數學，只替換設定後走真引擎）
SWEEPABLE_FIELDS = frozenset({
    "take_profit_percent",
    "stop_loss_percent",
    "max_safety_orders",
    "price_deviation_percent",
    "price_deviation_multiplier",
    "order_size_multiplier",
    "base_order_amount",
    "cooldown_seconds",
    "active_safety_orders_window",
    "fee_rate",
    "max_cycles",
    "repeat_after_take_profit",
})

_DECIMAL_FIELDS = frozenset({
    "base_order_amount",
    "fee_rate",
    "price_deviation_percent",
    "price_deviation_multiplier",
    "order_size_multiplier",
    "take_profit_percent",
    "stop_loss_percent",
    "max_position_notional",
})

# 測試注入點：在單次 replay 開始/結束時呼叫 hook("start"|"end")，供稽核 4.7 計數
_run_hook: Optional[Callable[[str], None]] = None


@dataclass(frozen=True)
class SweepRun:
    """單一參數組合的掃描結果。未完成或失敗時 metrics 必須為 None（稽核 4.9）。"""

    params: Dict[str, Any]
    metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    bars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "params": dict(self.params),
            "metrics": dict(self.metrics) if self.metrics is not None else None,
            "error": self.error,
            "bars": self.bars,
        }


@dataclass(frozen=True)
class SweepBudget:
    bars_used: int
    bars_limit: int
    runs_limit: Optional[int]
    runs_completed: int
    runs_total: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bars_used": self.bars_used,
            "bars_limit": self.bars_limit,
            "runs_limit": self.runs_limit,
            "runs_completed": self.runs_completed,
            "runs_total": self.runs_total,
        }


@dataclass(frozen=True)
class SweepResult:
    runs: List[SweepRun]
    best_by: Dict[str, Any]
    truncated: bool
    budget: SweepBudget

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runs": [r.to_dict() for r in self.runs],
            "best_by": dict(self.best_by),
            "truncated": self.truncated,
            "budget": self.budget.to_dict(),
        }


def _coerce_param_value(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _DECIMAL_FIELDS:
        return Decimal(str(value))
    return value


def expand_grid(
    base: DcaConfig,
    axes: Mapping[str, Sequence[Any]],
) -> List[Tuple[Dict[str, Any], Optional[DcaConfig], Optional[str]]]:
    """
    將參數軸展開為笛卡兒積。

    回傳 ``(params, config_or_None, error_or_None)``。
    非法組合（含 DcaConfig 驗證失敗）標記中文 error，不拋出、不中斷其餘組合。
    """
    if not axes:
        return [({}, base, None)]

    known = {f.name for f in fields(DcaConfig)}
    keys = list(axes.keys())
    value_lists = [list(axes[k]) for k in keys]
    out: List[Tuple[Dict[str, Any], Optional[DcaConfig], Optional[str]]] = []

    for combo in product(*value_lists):
        params = {k: combo[i] for i, k in enumerate(keys)}
        unknown = [k for k in keys if k not in known]
        if unknown:
            out.append((params, None, f"未知掃描參數: {', '.join(unknown)}"))
            continue
        illegal_field = [k for k in keys if k not in SWEEPABLE_FIELDS]
        if illegal_field:
            out.append((
                params, None,
                f"不支援掃描的參數: {', '.join(illegal_field)}",
            ))
            continue
        try:
            kwargs = {k: _coerce_param_value(k, v) for k, v in params.items()}
            cfg = replace(base, **kwargs)
            out.append((params, cfg, None))
        except (DcaConfigError, TypeError, ValueError) as exc:
            out.append((params, None, str(exc)))
    return out


def precompute_series_fns(
    entry: EntryConfig,
    closes: Sequence[float],
    direction: str,
) -> Dict[type, TriggerSeriesFn]:
    """
    對固定 entry，預先計算每個指標在每一個 fetch_limit 視窗上的觸發序列。

    series_fn 收到與逐棒重算相同的 closes 視窗時，回傳快取結果；
    未命中則回退現場計算（等價性仍成立）。
    """
    if entry.mode is EntryMode.IMMEDIATE or not entry.indicators:
        return {}

    limit = int(entry.fetch_limit)
    n = len(closes)
    cache: Dict[Tuple[type, str, Tuple[float, ...]], List[bool]] = {}
    for cfg in entry.indicators:
        cfg_type = type(cfg)
        for i in range(n):
            start = max(0, i - limit + 1)
            window = tuple(float(x) for x in closes[start:i + 1])
            key = (cfg_type, direction, window)
            if key not in cache:
                cache[key] = indicator_trigger_series(cfg, window, direction)

    def _make_fn(ind_type: type) -> TriggerSeriesFn:
        def _fn(window_closes: Sequence[float], cfg: Any, dirn: str) -> List[bool]:
            key = (type(cfg), dirn, tuple(float(x) for x in window_closes))
            cached = cache.get(key)
            if cached is not None:
                return cached
            return indicator_trigger_series(cfg, window_closes, dirn)
        _fn.__name__ = f"precomputed_{ind_type.__name__}"
        return _fn

    return {type(cfg): _make_fn(type(cfg)) for cfg in entry.indicators}


def bar_should_enter_series(
    config: DcaConfig,
    candles: Sequence[Any],
    *,
    direction: str = "long",
    series_fns: Optional[Dict[type, TriggerSeriesFn]] = None,
) -> List[bool]:
    """每一棒的 should_enter verdict（供稽核 4.8 等價性比對，不跑完整回放）。"""
    evaluator = EntryEvaluator(config.entry, series_fns=series_fns)
    seq = to_candles(candles)
    closes = [float(c.close) for c in seq]
    limit = int(config.entry.fetch_limit)
    verdicts: List[bool] = []
    for i in range(len(closes)):
        start = max(0, i - limit + 1)
        window = closes[start:i + 1]
        verdict = evaluator.should_enter(window, direction, last_bar_closed=True)
        verdicts.append(bool(verdict.should_enter))
    return verdicts


def _direction_of(config: DcaConfig) -> str:
    direction = config.direction
    if hasattr(direction, "value"):
        value = str(direction.value)
    else:
        value = str(direction)
    if value == "both":
        return "long"
    return value


def _execute_one(
    config: DcaConfig,
    candles: Sequence[Candle],
    initial_capital: Union[str, float],
    timeframe: str,
    series_fns: Optional[Dict[type, TriggerSeriesFn]],
    bot_id: str,
) -> BacktestResult:
    if _run_hook is not None:
        _run_hook("start")
    try:
        driver = ReplayDriver(
            config,
            candles,
            initial_capital=initial_capital,
            timeframe=timeframe,
            bot_id=bot_id,
            series_fns=series_fns,
        )
        return asyncio.run(driver.run())
    finally:
        if _run_hook is not None:
            _run_hook("end")


def _pick_best(
    runs: Sequence[SweepRun],
    metric: str,
) -> Dict[str, Any]:
    best_params: Optional[Dict[str, Any]] = None
    best_value: Optional[float] = None
    for run in runs:
        if run.metrics is None or run.error:
            continue
        value = run.metrics.get(metric)
        if value is None:
            continue
        numeric = float(value)
        if best_value is None or numeric > best_value:
            best_value = numeric
            best_params = dict(run.params)
    return {
        "metric": metric,
        "value": best_value,
        "params": best_params,
    }


def run_sweep(
    base_config: DcaConfig,
    candles: Sequence[Any],
    axes: Mapping[str, Sequence[Any]],
    *,
    initial_capital: Union[str, float] = "1000",
    timeframe: str = "15m",
    max_workers: int = SWEEP_MAX_WORKERS,
    max_total_bars: int = SWEEP_MAX_TOTAL_BARS,
    max_runs: Optional[int] = None,
    best_metric: str = "total_return_pct",
    use_precomputed_indicators: bool = True,
) -> SweepResult:
    """
    對參數網格跑回測掃描。

    先依預算決定要執行哪些組合（超預算 → truncated），再以執行緒池並行。
    非法組合不進執行緒、不消耗棒數預算，只在結果裡標記 error。
    """
    seq = to_candles(candles)
    n_bars = len(seq)
    if n_bars == 0:
        raise ValueError("candles 不得為空序列")
    if max_workers < 1:
        raise ValueError(f"max_workers 必須 ≥ 1，收到: {max_workers}")

    expanded = expand_grid(base_config, axes)
    runs_total = len(expanded)

    scheduled: List[Optional[Tuple[Dict[str, Any], DcaConfig]]] = []
    error_runs: Dict[int, SweepRun] = {}
    bars_used = 0
    truncated = False
    runnable_count = 0

    for params, cfg, err in expanded:
        if err is not None:
            scheduled.append(None)
            error_runs[len(scheduled) - 1] = SweepRun(
                params=dict(params), metrics=None, error=err, bars=0,
            )
            continue
        assert cfg is not None
        if max_runs is not None and runnable_count >= max_runs:
            truncated = True
            break
        if bars_used + n_bars > max_total_bars:
            truncated = True
            break
        scheduled.append((dict(params), cfg))
        bars_used += n_bars
        runnable_count += 1

    if len(scheduled) < runs_total and not truncated:
        # expand 比 scheduled 長且不是因為 error 被記入：代表中途 break
        truncated = True

    # 被預算截斷、尚未排程的組合不進入 runs（不得回報未完成指標，稽核 4.9）
    # 已排程的 error 組合會出現在 runs 裡。

    series_fns: Optional[Dict[type, TriggerSeriesFn]] = None
    if use_precomputed_indicators and base_config.entry.mode is EntryMode.INDICATOR:
        closes = [float(c.close) for c in seq]
        series_fns = precompute_series_fns(
            base_config.entry, closes, _direction_of(base_config),
        )

    jobs: List[Tuple[int, Dict[str, Any], DcaConfig]] = []
    for idx, item in enumerate(scheduled):
        if item is None:
            continue
        jobs.append((idx, item[0], item[1]))

    results_by_index: Dict[int, SweepRun] = dict(error_runs)
    workers = max(1, min(int(max_workers), len(jobs))) if jobs else 1

    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(
                    _execute_one,
                    cfg,
                    seq,
                    initial_capital,
                    timeframe,
                    series_fns,
                    f"dca-sweep-{idx}",
                ): (idx, params)
                for idx, params, cfg in jobs
            }
            for fut in as_completed(future_map):
                idx, params = future_map[fut]
                try:
                    result = fut.result()
                    results_by_index[idx] = SweepRun(
                        params=params,
                        metrics=dict(result.metrics),
                        error=None,
                        bars=result.bars,
                    )
                except Exception as exc:  # 單組失敗不得拖垮整次掃描
                    logger.warning(
                        "掃描單組回測失敗",
                        extra={"params": params, "error": str(exc)},
                    )
                    results_by_index[idx] = SweepRun(
                        params=params,
                        metrics=None,
                        error=f"回測失敗: {exc}",
                        bars=0,
                    )

    ordered: List[SweepRun] = []
    for idx in range(len(scheduled)):
        run = results_by_index.get(idx)
        if run is not None:
            ordered.append(run)

    completed_ok = sum(1 for r in ordered if r.metrics is not None and not r.error)
    budget = SweepBudget(
        bars_used=bars_used,
        bars_limit=int(max_total_bars),
        runs_limit=max_runs,
        runs_completed=completed_ok,
        runs_total=runs_total,
    )
    return SweepResult(
        runs=ordered,
        best_by=_pick_best(ordered, best_metric),
        truncated=truncated,
        budget=budget,
    )
