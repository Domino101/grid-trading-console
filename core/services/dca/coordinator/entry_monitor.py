"""
EntryMonitor —— armed 期間的入場條件輪詢（計畫 Phase 2 交付物）

職責：
- 依 EntryConfig 決定 immediate / indicator 路徑
- indicator 模式透過 ohlcv_provider 取得 closes 後交給 EntryEvaluator
- ohlcv_provider 為注入介面（async callable(symbol, timeframe, limit) -> list[float]），
  Phase 3 由共享 OhlcvCache 實作；Phase 2 測試注入假資料
- 記錄最近一次的評估結果（last_verdicts）供 UI 指標面板（Phase 5/7）

無熔斷 / 交易所寫入權限：本類只讀 K 線、評估條件，絕不下單（armed 不變式）。
"""

from typing import Awaitable, Callable, Dict, List, Optional

from ..entry.evaluator import EntryEvaluator, EntryVerdict, TriggerSeriesFn
from ..indicators import evaluate_indicator
from ..indicators.base import IndicatorResult
from ..models.dca_config import DcaConfig, EntryMode

OhlcvProvider = Callable[[str, str, int], Awaitable[List[float]]]


class EntryMonitor:
    """單台 DCA 的入場監控器。"""

    def __init__(self, config: DcaConfig,
                 ohlcv_provider: Optional[OhlcvProvider] = None,
                 series_fns: Optional[Dict[type, TriggerSeriesFn]] = None):
        self._config = config
        self._evaluator = EntryEvaluator(config.entry, series_fns=series_fns)
        self._ohlcv_provider = ohlcv_provider
        self.last_verdicts: Dict[str, EntryVerdict] = {}
        self.last_indicator_results: Dict[str, List[IndicatorResult]] = {}

    async def evaluate(self, direction: str) -> EntryVerdict:
        """
        評估指定方向的入場條件。

        immediate 模式：恆真且不觸碰 OHLCV provider（稽核 1.11 語意延伸至引擎層）。
        indicator 模式：需要 ohlcv_provider；未注入時拋明確錯誤。
        """
        entry = self._config.entry
        if entry.mode is EntryMode.IMMEDIATE:
            verdict = self._evaluator.should_enter([], direction)
            self.last_verdicts[direction] = verdict
            return verdict

        if self._ohlcv_provider is None:
            raise RuntimeError(
                "entry.mode=indicator 需要 ohlcv_provider（Phase 3 由 OhlcvCache 提供）"
            )
        closes = await self._ohlcv_provider(
            self._config.symbol, entry.timeframe, entry.fetch_limit
        )
        # 稽核 3.9：OHLCV 不可得（無快取）時回傳 None → 視為資料不足，
        # 指標評估 insufficient_data、triggered=False，不得觸發入場。
        if closes is None:
            closes = []
        verdict = self._evaluator.should_enter(
            closes, direction, last_bar_closed=True
        )
        self.last_verdicts[direction] = verdict
        self.last_indicator_results[direction] = [
            evaluate_indicator(cfg, closes, direction, last_bar_closed=True)
            for cfg in entry.indicators
        ]
        return verdict
