"""
EntryEvaluator —— DCA 入場條件評估器（§4.2-B / 稽核 1.9–1.11）

語意：
- mode=immediate：should_enter() 恆為 True，且完全不觸碰任何指標函式
  （稽核 1.11 以 mock 斷言呼叫次數為 0）。
- mode=indicator：對每個啟用的指標產生逐棒判定序列，依 logic
  （all=AND / any=OR）逐棒合併，再要求「最後 confirm_bars 根已收線
  K 棒連續成立」才觸發（抗插針，稽核 1.10）。
- 資料不足（暖機期）視為不成立（運算約定 5）；指標清單為空時拋
  明確異常（理論上 DcaConfig 驗證已攔截，此為縱深防禦）。

direction 為單次評估的一個方向（"long" 或 "short"）；direction=both 的
同時評估與互斥鎖定由 Phase 2 的 coordinator 依 §4.4 實作。
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..indicators import evaluate_indicator, indicator_trigger_series
from ..models.dca_config import EntryConfig, EntryMode
from ..indicators.base import Number
from ..indicators.config import IndicatorConfig

# 逐棒判定函式的注入點：(closes, cfg, direction) -> list[bool]
TriggerSeriesFn = Callable[[Sequence[Number], IndicatorConfig, str], List[bool]]


@dataclass(frozen=True)
class EntryVerdict:
    """單次入場評估結果。"""
    should_enter: bool
    mode: EntryMode
    direction: str
    consecutive_true_bars: int          # 末端連續成立的已收線棒數
    confirm_bars_needed: int
    per_indicator_triggered: Tuple[bool, ...] = field(default_factory=tuple)


class EntryEvaluator:
    """
    入場評估器。

    series_fns 依指標設定型別對應逐棒判定函式，僅供測試注入（mock）；
    正式環境使用預設實作，immediate 模式下保證不被觸碰。
    """

    def __init__(self, entry: EntryConfig,
                 series_fns: Optional[Dict[type, TriggerSeriesFn]] = None):
        self._entry = entry
        self._series_fns = series_fns or {}

    def _trigger_series(self, closes: Sequence[Number],
                        cfg: IndicatorConfig, direction: str) -> List[bool]:
        fn = self._series_fns.get(type(cfg))
        if fn is not None:
            return fn(closes, cfg, direction)
        return indicator_trigger_series(cfg, closes, direction)

    def should_enter(self, closes: Sequence[Number], direction: str = "long",
                     last_bar_closed: bool = True) -> EntryVerdict:
        if self._entry.mode is EntryMode.IMMEDIATE:
            return EntryVerdict(
                should_enter=True,
                mode=EntryMode.IMMEDIATE,
                direction=direction,
                consecutive_true_bars=1,
                confirm_bars_needed=self._entry.confirm_bars,
                per_indicator_triggered=(),
            )

        indicators = tuple(self._entry.indicators)
        if not indicators:
            raise ValueError(
                "entry.mode=indicator 需要至少 1 個指標，請檢查 DcaConfig.entry.indicators"
            )

        closed = closes if last_bar_closed else list(closes)[:-1]
        if not closed:
            return EntryVerdict(
                should_enter=False,
                mode=EntryMode.INDICATOR,
                direction=direction,
                consecutive_true_bars=0,
                confirm_bars_needed=self._entry.confirm_bars,
                per_indicator_triggered=tuple(False for _ in indicators),
            )

        all_series: List[List[bool]] = [
            self._trigger_series(closed, cfg, direction) for cfg in indicators
        ]

        n = len(closed)
        need = self._entry.confirm_bars
        if len(closed) < need:
            return EntryVerdict(
                should_enter=False,
                mode=EntryMode.INDICATOR,
                direction=direction,
                consecutive_true_bars=0,
                confirm_bars_needed=need,
                per_indicator_triggered=tuple(
                    series[-1] if series else False for series in all_series
                ),
            )

        def _combined_at(i: int) -> bool:
            values = [series[i] for series in all_series]
            if self._entry.logic == "any":
                return any(values)
            return all(values)

        consecutive = 0
        for i in range(n - 1, -1, -1):
            if _combined_at(i):
                consecutive += 1
            else:
                break

        return EntryVerdict(
            should_enter=consecutive >= need,
            mode=EntryMode.INDICATOR,
            direction=direction,
            consecutive_true_bars=consecutive,
            confirm_bars_needed=need,
            per_indicator_triggered=tuple(
                series[-1] if series else False for series in all_series
            ),
        )

    def evaluate_indicators(self, closes: Sequence[Number], direction: str = "long",
                            last_bar_closed: bool = True):
        """回傳每個指標的 IndicatorResult（UI 指標面板用；immediate 模式為空）。"""
        if self._entry.mode is EntryMode.IMMEDIATE:
            return ()
        return tuple(
            evaluate_indicator(cfg, closes, direction, last_bar_closed)
            for cfg in self._entry.indicators
        )
