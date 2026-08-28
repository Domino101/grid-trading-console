"""
core/services/dca/backtest/replay_driver.py —— DCA 回放驅動器（真 Coordinator + 真 Executor）

本模組為 DCA 回測子系統之核心驅動器，落實以下關鍵工程約束：
- BP-1 同源：實例化真正的 DcaCoordinator 與真正的 DcaOrderExecutor，禁止存在第二套執行器或階梯數學
- BP-2 唯讀：交易所寫入呼叫數為 0，零檔案副作用，不碰 active_dca.json
- R-1～R-13 凍結撮合規則：
  - R-1 無未来函数：棒 i 決策，棒 j > i 才撮合
  - R-8 熔斷固定：注入 circuit_probe = lambda: False
  - R-9 棒時間時鐘：注入 time_fn = 當前棒 unix 秒
  - R-10 指標邊界：ohlcv_provider 僅回傳 <= 當前棒 closes 切片
  - R-11 確定性：同輸入執行兩次輸出完全一致
- 每棒狀態不變量守衛：INV-1（armed 零掛單）、INV-2（averaging 恰好 1 張 TP）、INV-6（單方向互斥）
- 顯示層時間蓋章：複製 fills_snapshot 副本並蓋上棒時間 ts，絕不修改 coord._fills 原物件
"""

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Union

from .candles import to_candles
from .metrics import compute_metrics
from .models import BacktestResult, CycleRecord, EquityPoint, TradeMark
from .replay_exchange import ReplayExchange
from ..coordinator.dca_coordinator import DcaCoordinator
from ..coordinator.entry_monitor import EntryMonitor
from ..entry.evaluator import TriggerSeriesFn
from ..execution.dca_order_executor import DcaOrderExecutor
from ..ladder import build_ladder
from ..models.dca_config import DcaConfig
from ..models.dca_state import DcaStatus
from core.logging import get_logger

logger = get_logger(__name__)


class _CycleBuilder:
    """內部輔助類別：追蹤單一 DCA 循環輪次之統計數據。"""

    def __init__(self, index: int, direction: str, entry_ts: int,
                 entry_price: float, average_cost: float):
        self.index = index
        self.direction = direction
        self.entry_ts = entry_ts
        self.entry_price = entry_price
        self.average_cost = average_cost
        self.exit_ts: Optional[int] = None
        self.exit_price: Optional[float] = None
        self.safety_filled = 0
        self.exit_reason = "running"
        self.realized_pnl: Optional[float] = None
        self.realized_pct: Optional[float] = None
        self.mfe_pct: Optional[float] = None
        self.mae_pct: Optional[float] = None
        self.bars = 0

    def update_excursions(self, high: float, low: float, current_avg: float) -> None:
        """更新最大有利 (MFE) 與最大不利 (MAE) 偏移百分比。"""
        self.bars += 1
        if current_avg <= 0:
            return
        if self.direction == "long":
            fav = (high - current_avg) / current_avg * 100.0
            adv = (current_avg - low) / current_avg * 100.0
        else:
            fav = (current_avg - low) / current_avg * 100.0
            adv = (high - current_avg) / current_avg * 100.0

        if self.mfe_pct is None or fav > self.mfe_pct:
            self.mfe_pct = max(fav, 0.0)
        if self.mae_pct is None or adv > self.mae_pct:
            self.mae_pct = max(adv, 0.0)

    def build(self) -> CycleRecord:
        return CycleRecord(
            index=self.index,
            direction=self.direction,
            entry_ts=self.entry_ts,
            exit_ts=self.exit_ts,
            entry_price=round(self.entry_price, 8) if self.entry_price is not None else None,
            average_cost=round(self.average_cost, 8) if self.average_cost is not None else None,
            exit_price=round(self.exit_price, 8) if self.exit_price is not None else None,
            safety_filled=self.safety_filled,
            exit_reason=self.exit_reason,
            realized_pnl=round(self.realized_pnl, 8) if self.realized_pnl is not None else None,
            realized_pct=round(self.realized_pct, 4) if self.realized_pct is not None else None,
            mfe_pct=round(self.mfe_pct, 4) if self.mfe_pct is not None else None,
            mae_pct=round(self.mae_pct, 4) if self.mae_pct is not None else None,
            bars=self.bars,
        )


class ReplayDriver:
    """
    DCA 回放驅動器。

    實體化真 DcaCoordinator + 真 DcaOrderExecutor + ReplayExchange，逐棒驅動狀態機。
    """

    def __init__(
        self,
        config: DcaConfig,
        candles: Sequence[Any],
        initial_capital: Optional[Union[Decimal, str, float]] = None,
        timeframe: str = "15m",
        source: str = "cache",
        data_status: str = "live",
        bot_id: Optional[str] = None,
        series_fns: Optional[Dict[type, TriggerSeriesFn]] = None,
    ):
        self._config = config
        self._candle_seq = to_candles(candles)
        self._timeframe = timeframe
        self._source = source
        self._data_status = data_status
        self._bot_id = bot_id or "dca-backtest"
        self._series_fns = series_fns

        if not self._candle_seq:
            raise ValueError("candles 不得為空序列")

        # 初始資金計算：未提供則以第一根收盤價計算所需資金 required_capital (§4.5)
        if initial_capital is not None:
            self._initial_capital = Decimal(str(initial_capital))
            if self._initial_capital <= Decimal("0"):
                raise ValueError(f"initial_capital 必須 > 0，收到: {self._initial_capital}")
        else:
            first_close = self._candle_seq[0].close
            ladder_plan = build_ladder(self._config, first_close)
            self._initial_capital = ladder_plan.required_capital()
            if self._initial_capital <= Decimal("0"):
                self._initial_capital = Decimal("1000")

        # 1. 撮合交易所
        self.exchange = ReplayExchange(self._candle_seq)

        # 2. 真 Executor（BP-1：min_notional=0 與實盤一致，circuit_probe=lambda: False 鎖死 R-8）
        self.executor = DcaOrderExecutor(
            config=self._config,
            bot_id=self._bot_id,
            exchange=self.exchange,
            min_notional=Decimal("0"),
            circuit_probe=lambda: False,
        )

        # 3. 虛擬時鐘與歷史 OHLCV 邊界注入 (R-9 / R-10)
        self._current_bar_index = 0

        def _virtual_clock() -> float:
            return float(self._candle_seq[self._current_bar_index].ts)

        async def _ohlcv_provider(symbol: str, tf: str, limit: int) -> List[float]:
            # R-10：只回傳 <= 當前棒的 closes 切片，絕不回傳未來棒
            idx = self._current_bar_index
            start = max(0, idx - limit + 1)
            return [float(c.close) for c in self._candle_seq[start : idx + 1]]

        # 4. 真 Coordinator (BP-1)；掃描模式可注入預計算 series_fns（稽核 4.8）
        entry_monitor = EntryMonitor(
            self._config,
            ohlcv_provider=_ohlcv_provider,
            series_fns=self._series_fns,
        )
        self.coordinator = DcaCoordinator(
            config=self._config,
            bot_id=self._bot_id,
            executor=self.executor,
            entry_monitor=entry_monitor,
            ohlcv_provider=_ohlcv_provider,
            time_fn=_virtual_clock,
        )

    async def run(self) -> BacktestResult:
        """
        執行完整回測回放迴圈。

        逐棒推進：set_bar(i) -> step() -> 收集快照/蓋章 -> advance(i+1)。
        """
        start_time = time.perf_counter()
        candles = self._candle_seq
        n_bars = len(candles)

        # 啟動協調器（DRAFT → ARMED，不建背景 task）
        self.coordinator.begin()

        equity_points: List[EquityPoint] = []
        marks: List[TradeMark] = []
        cycles: List[CycleRecord] = []

        current_cycle: Optional[_CycleBuilder] = None
        cycle_counter = 0
        processed_fills_count = 0
        holding_bars_count = 0

        for i, bar in enumerate(candles):
            self._current_bar_index = i
            self.exchange.set_bar(i)

            # 決策步：只看 <= i 的資料
            await self.coordinator.step()

            # ── 1. 處理新成交並生成 TradeMark ──
            # 複製快照避免影響原物件 (3.10)
            fills_snapshot = self.coordinator.fills_snapshot()
            if len(fills_snapshot) > processed_fills_count:
                new_fills = fills_snapshot[processed_fills_count:]
                for fill in new_fills:
                    role_str = str(fill["role"])
                    price = float(fill["price"])
                    qty = float(fill["quantity"])
                    notional = float(fill["notional"])
                    slot_idx = fill.get("slot_index")
                    side = str(fill["side"])

                    if role_str == "base":
                        kind = "base_filled"
                        cycle_counter += 1
                        current_direction = (
                            self.coordinator.state.active_direction
                            or (self._config.direction.value if hasattr(self._config.direction, "value") else str(self._config.direction))
                        )
                        current_cycle = _CycleBuilder(
                            index=cycle_counter,
                            direction=current_direction,
                            entry_ts=bar.ts,
                            entry_price=price,
                            average_cost=price,
                        )
                        mark = TradeMark(
                            ts=bar.ts,
                            price=price,
                            kind=kind,
                            role=role_str,
                            side=side,
                            quantity=qty,
                            notional=notional,
                            slot_index=slot_idx,
                            cycle_index=cycle_counter,
                            realized_pct=None,
                        )
                        marks.append(mark)

                    elif role_str == "safety":
                        kind = "safety_filled"
                        if current_cycle is not None:
                            current_cycle.safety_filled += 1
                        mark = TradeMark(
                            ts=bar.ts,
                            price=price,
                            kind=kind,
                            role=role_str,
                            side=side,
                            quantity=qty,
                            notional=notional,
                            slot_index=slot_idx,
                            cycle_index=cycle_counter if cycle_counter > 0 else 1,
                            realized_pct=None,
                        )
                        marks.append(mark)

                    elif role_str == "take_profit":
                        kind = "take_profit_filled"
                        avg = (
                            current_cycle.average_cost
                            if current_cycle and current_cycle.average_cost
                            else price
                        )
                        if (current_cycle and current_cycle.direction == "long") or self._config.direction == "long":
                            realized_pct = ((price - avg) / avg * 100.0) if avg > 0 else 0.0
                        else:
                            realized_pct = ((avg - price) / avg * 100.0) if avg > 0 else 0.0

                        mark = TradeMark(
                            ts=bar.ts,
                            price=price,
                            kind=kind,
                            role=role_str,
                            side=side,
                            quantity=qty,
                            notional=notional,
                            slot_index=slot_idx,
                            cycle_index=cycle_counter if cycle_counter > 0 else 1,
                            realized_pct=round(realized_pct, 4),
                        )
                        marks.append(mark)

                        if current_cycle is not None:
                            current_cycle.exit_ts = bar.ts
                            current_cycle.exit_price = price
                            current_cycle.exit_reason = "take_profit"
                            current_cycle.realized_pct = realized_pct
                            spread = (Decimal(str(price)) - Decimal(str(avg))) * Decimal(str(qty))
                            if current_cycle.direction == "short":
                                spread = -spread
                            current_cycle.realized_pnl = float(spread)
                            cycles.append(current_cycle.build())
                            current_cycle = None

                    elif role_str == "stop_loss":
                        kind = "stop_loss_filled"
                        avg = (
                            current_cycle.average_cost
                            if current_cycle and current_cycle.average_cost
                            else price
                        )
                        if (current_cycle and current_cycle.direction == "long") or self._config.direction == "long":
                            realized_pct = ((price - avg) / avg * 100.0) if avg > 0 else 0.0
                        else:
                            realized_pct = ((avg - price) / avg * 100.0) if avg > 0 else 0.0

                        mark = TradeMark(
                            ts=bar.ts,
                            price=price,
                            kind=kind,
                            role=role_str,
                            side=side,
                            quantity=qty,
                            notional=notional,
                            slot_index=slot_idx,
                            cycle_index=cycle_counter if cycle_counter > 0 else 1,
                            realized_pct=round(realized_pct, 4),
                        )
                        marks.append(mark)

                        if current_cycle is not None:
                            current_cycle.exit_ts = bar.ts
                            current_cycle.exit_price = price
                            current_cycle.exit_reason = "stop_loss"
                            current_cycle.realized_pct = realized_pct
                            spread = (Decimal(str(price)) - Decimal(str(avg))) * Decimal(str(qty))
                            if current_cycle.direction == "short":
                                spread = -spread
                            current_cycle.realized_pnl = float(spread)
                            cycles.append(current_cycle.build())
                            current_cycle = None

                processed_fills_count = len(fills_snapshot)

            # ── 2. 更新當前循環持倉與偏移 ──
            pos_qty = self.coordinator.state.position_quantity
            avg_cost = self.coordinator.state.average_cost
            if pos_qty > Decimal("0") and avg_cost is not None:
                holding_bars_count += 1
                if current_cycle is not None:
                    current_cycle.average_cost = float(avg_cost)
                    current_cycle.update_excursions(float(bar.high), float(bar.low), float(avg_cost))

            # ── 3. 計算並記錄 EquityPoint ──
            # 權益 = initial_capital + realized − fees + unrealized
            realized = self.coordinator._realized_profit
            fees = self.coordinator._total_fees
            unrealized = Decimal("0")
            if pos_qty > Decimal("0") and avg_cost is not None:
                diff = (bar.close - avg_cost) * pos_qty
                if self.coordinator.state.active_direction == "short":
                    diff = -diff
                unrealized = diff

            current_equity = self._initial_capital + realized - fees + unrealized
            equity_point = EquityPoint(
                ts=bar.ts,
                equity=round(float(current_equity), 6),
                position_quantity=float(pos_qty),
                average_cost=float(avg_cost) if (pos_qty > Decimal("0") and avg_cost is not None) else None,
                status=self.coordinator.state.status.value,
            )
            equity_points.append(equity_point)

            # ── 4. 每棒狀態不變量守衛 (INV-1 / INV-2 / INV-6) ──
            await self._assert_invariants(i, bar.ts)

            # ── 5. 次棒撮合 (R-1: 棒 i+1 撮合 <= i 送出的訂單) ──
            if i + 1 < n_bars:
                matched_records = self.exchange.advance(i + 1)
                # 依 R-4 保守撮合順序（安全單先、TP 後）將 WS fill 送入漏斗
                for rec in matched_records:
                    if rec.average is not None and rec.filled > Decimal("0"):
                        await self.coordinator.handle_ws_fill(
                            rec.client_id, rec.average, rec.filled
                        )

        # ── 6. 回測結束收尾處理 ──
        # 若資料結束時仍持倉，結算為 exit_reason="end_of_data" (3.13)
        if current_cycle is not None:
            last_bar = candles[-1]
            current_cycle.exit_ts = last_bar.ts
            current_cycle.exit_price = float(last_bar.close)
            current_cycle.exit_reason = "end_of_data"
            avg = current_cycle.average_cost or float(last_bar.close)
            if current_cycle.direction == "long":
                current_cycle.realized_pct = ((float(last_bar.close) - avg) / avg * 100.0) if avg > 0 else 0.0
            else:
                current_cycle.realized_pct = ((avg - float(last_bar.close)) / avg * 100.0) if avg > 0 else 0.0
            cycles.append(current_cycle.build())
            current_cycle = None

        elapsed = time.perf_counter() - start_time
        bars_per_sec = round(n_bars / elapsed, 2) if elapsed > 0 else 0.0

        # ── 7. 計算績效指標 (§4.5) ── 一律走純函式 compute_metrics；
        # 會計值取自引擎 build_statistics()（稽核 4.5，禁止加總 fills[].fee）
        stats = self.coordinator.build_statistics(last_price=candles[-1].close)
        direction = (
            self._config.direction.value
            if hasattr(self._config.direction, "value")
            else str(self._config.direction)
        )
        metrics = compute_metrics(
            equity=equity_points,
            cycles=cycles,
            candles=candles,
            initial_capital=float(self._initial_capital),
            realized_pnl=float(stats.realized_profit),
            total_fees=float(stats.total_fees),
            cycles_completed=int(stats.cycle_count),
            holding_bars=holding_bars_count,
            direction=direction,
            bars_per_second=bars_per_sec,
        )

        # ── 8. 警示與簡化事項揭露 (§A-6 / D-11) ──
        warnings = [
            "回測不模擬部分成交",
            "未套用交易所最小名目限制",
            "歷史績效不代表未來",
        ]
        if self._source == "paper_synthetic" or self._data_status == "paper":
            warnings.append("使用合成 K 線，非真實歷史")

        candles_dicts = [
            {
                "t": int(c.ts),
                "o": float(c.open),
                "h": float(c.high),
                "l": float(c.low),
                "c": float(c.close),
                "v": float(c.volume),
            }
            for c in candles
        ]

        return BacktestResult(
            bars=n_bars,
            timeframe=self._timeframe,
            first_ts=candles[0].ts if candles else None,
            last_ts=candles[-1].ts if candles else None,
            metrics=metrics,
            equity=equity_points,
            marks=marks,
            cycles=cycles,
            data_status=self._data_status,
            source=self._source,
            warnings=warnings,
            bars_per_second=bars_per_sec,
            candles=candles_dicts,
        )

    async def _assert_invariants(self, bar_idx: int, bar_ts: int) -> None:
        """斷言每棒結束時系統狀態不變量（INV-1, INV-2, INV-6）。"""
        status = self.coordinator.state.status
        open_orders = await self.exchange.get_open_orders(self._config.symbol)

        # INV-1: ARMED 狀態下在世掛單數必須為 0 (3.5)
        if status is DcaStatus.ARMED:
            if len(open_orders) > 0:
                raise AssertionError(
                    f"INV-1 違規（棒 {bar_idx}, ts={bar_ts}）：status 為 ARMED 但交易所仍有 {len(open_orders)} 筆在世掛單"
                )
            if self.coordinator.state.active_direction is not None:
                raise AssertionError(
                    f"INV-6 違規（棒 {bar_idx}）：status 為 ARMED 但 active_direction 為 {self.coordinator.state.active_direction}"
                )

        # INV-6: COOLING 狀態下 active_direction 必須為 None
        if status is DcaStatus.COOLING:
            if self.coordinator.state.active_direction is not None:
                raise AssertionError(
                    f"INV-6 違規（棒 {bar_idx}）：status 為 COOLING 但 active_direction 為 {self.coordinator.state.active_direction}"
                )

        # INV-2: AVERAGING 且持倉 > 0 時，在世 TP 掛單必須恰好 1 張 (3.6)
        if status is DcaStatus.AVERAGING and self.coordinator.state.position_quantity > Decimal("0"):
            tp_orders = [o for o in open_orders if getattr(o, "client_id", "").endswith("_tp")]
            if len(tp_orders) != 1:
                raise AssertionError(
                    f"INV-2 違規（棒 {bar_idx}, ts={bar_ts}）：status 為 AVERAGING 且持倉 {self.coordinator.state.position_quantity}，但 TP 掛單數為 {len(tp_orders)}（預期恰好 1 張）"
                )

        # INV-6: 單方向互斥，禁止同時存在 long 與 short 方向的在世安全/基礎單 (3.7)
        if len(open_orders) > 1:
            sides = {o.side for o in open_orders if not getattr(o, "client_id", "").endswith("_tp")}
            if len(sides) > 1:
                raise AssertionError(
                    f"INV-6 違規（棒 {bar_idx}）：在世基礎/安全單同時存在多個方向: {sides}"
                )

