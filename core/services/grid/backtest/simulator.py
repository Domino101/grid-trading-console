"""
core/services/grid/backtest/simulator.py —— 資金感知網格回測模擬器

設計目標：
- 不是輕量「穿越次數」估算，而是具備初始資金、逐筆保證金/手續費、權益曲線、
  逐倉清算估算的**資金感知模擬**。
- 純 Python 標準型別 + Decimal 計算；不依賴 injector / ccxt / FastAPI。
- 單一帳戶、isolated 逐倉近似。多格共用同一帳戶資金與保證金。

撮合規則（v1）：
- 固定區間、等差 grid lines。
- LONG / NEUTRAL：每格「買低（levels[i]）→ 賣高（levels[i+1]）」。
- SHORT：每格「賣高（levels[i+1]）→ 買低（levels[i]）」。
- 同一根 bar 先平倉既有倉位（釋放資金），再開新倉；同一格不允許同 bar 開後即重開。
- 保證金不足時跳過該筆成交並計入 skipped_margin_fills。
- 資金/未實現/已實現/手續費逐 bar 更新。

誠實揭露（warnings）：
- 近似逐倉保證金/清算，非交易所最終結果。
- 不模擬部分成交、資金費率、滑價。
- K 線路徑為 bar-level 近似。
- 未納入 follow / scalping / martingale / 止盈 / 止損。
- NEUTRAL v1 以做多循環近似。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from ...backtest.metrics import compute_metrics_from_equity
from .allocation import reference_price
from .models import (
    GridAllocation,
    GridBacktestResult,
    GridCycleRecord,
    GridEquityPoint,
    GridFillMark,
    GridBacktestSpec,
)
from .risk import build_risk_metrics, margin_state


def to_candles(raw_candles: Sequence[Any]) -> List[Any]:
    """
    將多種 K 線表達（dict / list / object 帶 close）轉成標準 dicts：
    ``{"ts": int, "open": Decimal, "high": Decimal, "low": Decimal, "close": Decimal, "volume": Decimal}``
    """
    out: List[Dict[str, Any]] = []
    for c in raw_candles:
        if isinstance(c, dict):
            ts = c.get("ts") or c.get("t") or 0
            ts = int(ts // 1000 if ts > 10_000_000_000 else ts)
            out.append({
                "ts": ts,
                "open": Decimal(str(c.get("open") or c.get("o") or "0")),
                "high": Decimal(str(c.get("high") or c.get("h") or "0")),
                "low": Decimal(str(c.get("low") or c.get("l") or "0")),
                "close": Decimal(str(c.get("close") or c.get("c") or "0")),
                "volume": Decimal(str(c.get("volume") or c.get("v") or "0")),
            })
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            ts_raw = c[0]
            ts = int(ts_raw // 1000 if ts_raw > 10_000_000_000 else ts_raw)
            vol = c[5] if len(c) > 5 else 0
            out.append({
                "ts": ts,
                "open": Decimal(str(c[1])),
                "high": Decimal(str(c[2])),
                "low": Decimal(str(c[3])),
                "close": Decimal(str(c[4])),
                "volume": Decimal(str(vol)),
            })
        elif hasattr(c, "close"):
            dt = getattr(c, "timestamp", None)
            ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(getattr(c, "ts", 0))
            out.append({
                "ts": ts,
                "open": Decimal(str(getattr(c, "open", "0"))),
                "high": Decimal(str(getattr(c, "high", "0"))),
                "low": Decimal(str(getattr(c, "low", "0"))),
                "close": Decimal(str(getattr(c, "close", "0"))),
                "volume": Decimal(str(getattr(c, "volume", 0))),
            })
    return out


class GridBacktestSimulator:
    """資金感知網格回測模擬器（deterministic、唯讀）。"""

    def __init__(
        self,
        spec: GridBacktestSpec,
        candles: Sequence[Any],
        allocation: Optional[GridAllocation] = None,
        source: str = "exchange",
        data_status: str = "live",
    ):
        self.spec = spec
        self._candles = to_candles(candles)
        if not self._candles:
            raise ValueError("candles 不得為空序列")
        if spec.upper_price <= spec.lower_price:
            raise ValueError("upper_price 必須大於 lower_price")

        self._grid_type = str(spec.grid_type).lower()
        self._direction = "short" if self._grid_type == "short" else "long"
        self._interval = (spec.upper_price - spec.lower_price) / Decimal(spec.grid_count)
        self._levels = [
            spec.lower_price + self._interval * Decimal(i)
            for i in range(spec.grid_count + 1)
        ]

        self.allocation = allocation or GridAllocation(
            order_amount=spec.order_amount,
            per_grid_notional=spec.order_amount * reference_price(spec.lower_price, spec.upper_price),
            max_fillable_cells=spec.grid_count,
            required_capital=spec.initial_capital,
            supplied_capital=spec.initial_capital,
            insufficient_capital=False,
            message="",
        )

        # 每個 cell: holding / side(+1 long, -1 short) / open_price / margin_locked / entry_qty
        self._cells: List[Dict[str, Any]] = [{
            "holding": False,
            "side": 1 if self._direction == "long" else -1,
            "open_price": Decimal("0"),
            "barrier_open": False,
            "barrier_close": False,
            "margin_locked": Decimal("0"),
            "bars": 0,
        } for _ in range(spec.grid_count)]

        # 帳戶會計
        self._margin = spec.maintenance_margin_rate
        self._fee_rate = spec.fee_rate
        self._leverage = max(1, spec.leverage)
        self._order_qty = self.allocation.order_amount
        self._cash = spec.initial_capital
        self._locked_margin = Decimal("0")
        self._position_qty = Decimal("0")
        self._avg_cost = Decimal("0")
        self._realized_pnl = Decimal("0")
        self._total_fees = Decimal("0")
        self._filled_buy_count = 0
        self._filled_sell_count = 0
        self._completed_cycles = 0
        self._skipped_margin_fills = 0

        self._source = source
        self._data_status = data_status
        self._warnings: List[str] = []

    def _build_warnings(self) -> List[str]:
        warnings = [
            "逐倉保證金/清算為近似，非交易所最終結果",
            "不模擬部分成交、資金費率、滑價",
            "K 線路徑為 bar-level 近似",
            "未納入 follow / scalping / martingale / 止盈 / 止損",
        ]
        if self._grid_type == "neutral":
            warnings.append("NEUTRAL 網格 v1 以做多循環近似")
        if self.allocation.insufficient_capital:
            warnings.append(self.allocation.message)
        if self._data_status == "paper" or self._source == "paper_synthetic":
            warnings.append("使用合成 K 線，非真實歷史")
        return warnings

    def _open_position(self, cell_idx: int, price: Decimal, ts: int) -> bool:
        """開啟一格倉位。不足保證金時回傳 False（不成交）。"""
        cell = self._cells[cell_idx]
        qty = self._order_qty
        notional = qty * price
        margin_locked = notional / Decimal(self._leverage)
        fee = notional * self._fee_rate

        if self._cash < margin_locked + fee:
            self._skipped_margin_fills += 1
            return False

        self._cash -= margin_locked + fee
        self._total_fees += fee
        self._locked_margin += margin_locked

        if cell["side"] == 1:
            qty_signed = qty
        else:
            qty_signed = -qty

        old_qty = self._position_qty
        old_avg = self._avg_cost
        self._position_qty += qty_signed
        if old_qty * qty_signed > 0 or old_qty == 0:
            self._avg_cost = (
                (old_qty * old_avg + qty_signed * price) / self._position_qty
                if self._position_qty != 0
                else price
            )
        else:
            # 反向減倉不重算均價（v1 簡化）
            pass

        cell["holding"] = True
        cell["open_price"] = price
        cell["margin_locked"] = margin_locked
        cell["entry_ts"] = ts
        cell["bars"] = 0

        if cell["side"] == 1:
            self._filled_buy_count += 1
        else:
            self._filled_sell_count += 1
        return True

    def _close_position(self, cell_idx: int, price: Decimal, ts: int) -> bool:
        """平掉一格倉位。"""
        cell = self._cells[cell_idx]
        if not cell["holding"]:
            return False
        qty = self._order_qty
        open_price = cell["open_price"]
        notional = qty * price
        fee = notional * self._fee_rate

        if cell["side"] == 1:
            realized = (price - open_price) * qty
            released = cell["margin_locked"]
        else:
            realized = (open_price - price) * qty
            released = cell["margin_locked"]

        self._cash += released + realized - fee
        self._total_fees += fee
        self._realized_pnl += realized
        self._locked_margin -= released
        if cell["side"] == 1:
            self._position_qty -= qty
            self._filled_sell_count += 1
        else:
            self._position_qty += qty
            self._filled_buy_count += 1

        self._completed_cycles += 1
        cell["holding"] = False
        cell["open_price"] = Decimal("0")
        cell["margin_locked"] = Decimal("0")
        cell["bars"] = 0
        return True

    def _mark_cycle_record(
        self,
        cycles: List[GridCycleRecord],
        cell_idx: int,
        entry_price: float,
        exit_price: float,
        entry_ts: Optional[int],
        exit_ts: Optional[int],
        realized: float,
        bars: int,
    ) -> None:
        direction = "short" if self._cells[cell_idx]["side"] == -1 else "long"
        cycles.append(GridCycleRecord(
            index=len(cycles) + 1,
            direction=direction,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=float(self._order_qty),
            realized_pnl=realized,
            realized_pct=((exit_price - entry_price) / entry_price * 100.0) if direction == "long" and entry_price else 0.0,
            bars=bars,
            cell_index=cell_idx,
        ))

    def _reset_cell_barrier(self) -> None:
        for cell in self._cells:
            cell["barrier_open"] = False
            cell["barrier_close"] = False

    def run(self) -> GridBacktestResult:
        """執行逐根 K 線回放。"""
        equity_points: List[GridEquityPoint] = []
        marks: List[GridFillMark] = []
        cycles: List[GridCycleRecord] = []
        candles = self._candles
        n_bars = len(candles)
        first_ts = candles[0]["ts"]
        last_ts = candles[-1]["ts"]

        liquidated = False
        count = 0
        prev_close = candles[0]["open"]
        for bar in candles:
            self._reset_cell_barrier()
            price = bar["close"]

            # 1. 先平已有倉位（釋放資金）。穿越偵測：prev_close 與 bar 的 high/low 須跨過格線。
            for i, cell in enumerate(self._cells):
                if not cell["holding"]:
                    continue
                if cell["side"] == 1 and prev_close < self._levels[i + 1] and bar["high"] >= self._levels[i + 1]:
                    exit_p = self._levels[i + 1]
                    entry = cell["open_price"]
                    if self._close_position(i, exit_p, bar["ts"]):
                        count += 1
                        realized = (exit_p - entry) * self._order_qty
                        self._mark_cycle_record(cycles, i, float(entry), float(exit_p), cell.get("entry_ts"), bar["ts"], float(realized), cell["bars"] + 1)
                        marks.append(GridFillMark(
                            ts=bar["ts"], price=float(exit_p), side="sell",
                            quantity=float(self._order_qty),
                            notional=float(exit_p * self._order_qty),
                            fee=float(exit_p * self._order_qty * self._fee_rate),
                            kind="close_long" if cell["side"] == 1 else "close_short",
                            cell_index=i, cycle_index=cycles[-1].index if cycles else 0,
                        ))
                        cell["barrier_close"] = True
                elif cell["side"] == -1 and prev_close > self._levels[i] and bar["low"] <= self._levels[i]:
                    exit_p = self._levels[i]
                    entry = cell["open_price"]
                    if self._close_position(i, exit_p, bar["ts"]):
                        count += 1
                        realized = (entry - exit_p) * self._order_qty
                        self._mark_cycle_record(cycles, i, float(entry), float(exit_p), cell.get("entry_ts"), bar["ts"], float(realized), cell["bars"] + 1)
                        marks.append(GridFillMark(
                            ts=bar["ts"], price=float(exit_p), side="buy",
                            quantity=float(self._order_qty),
                            notional=float(exit_p * self._order_qty),
                            fee=float(exit_p * self._order_qty * self._fee_rate),
                            kind="close_short",
                            cell_index=i, cycle_index=cycles[-1].index if cycles else 0,
                        ))
                        cell["barrier_close"] = True

            # 2. 再開新倉。同樣採穿越偵測，避免起始價上方/下方的格線誤成交。
            for i, cell in enumerate(self._cells):
                if not liquidated and not cell["holding"] and not cell["barrier_close"] and not cell["barrier_open"]:
                    if cell["side"] == 1 and prev_close > self._levels[i] and bar["low"] <= self._levels[i]:
                        entry_p = self._levels[i]
                        if self._open_position(i, entry_p, bar["ts"]):
                            cell["barrier_open"] = True
                            marks.append(GridFillMark(
                                ts=bar["ts"], price=float(entry_p), side="buy",
                                quantity=float(self._order_qty),
                                notional=float(entry_p * self._order_qty),
                                fee=float(entry_p * self._order_qty * self._fee_rate),
                                kind="open_long", cell_index=i, cycle_index=0,
                            ))
                    elif cell["side"] == -1 and prev_close < self._levels[i + 1] and bar["high"] >= self._levels[i + 1]:
                        entry_p = self._levels[i + 1]
                        if self._open_position(i, entry_p, bar["ts"]):
                            cell["barrier_open"] = True
                            marks.append(GridFillMark(
                                ts=bar["ts"], price=float(entry_p), side="sell",
                                quantity=float(self._order_qty),
                                notional=float(entry_p * self._order_qty),
                                fee=float(entry_p * self._order_qty * self._fee_rate),
                                kind="open_short", cell_index=i, cycle_index=0,
                            ))

            # 3. 更新此 bar 的會計與權益點
            for cell in self._cells:
                if cell["holding"]:
                    cell["bars"] += 1

            wallet_balance = self._cash + self._locked_margin
            state = margin_state(
                wallet_balance=wallet_balance,
                position_quantity=self._position_qty,
                average_cost=self._avg_cost if self._position_qty != 0 else Decimal("0"),
                price=price,
                leverage=self._leverage,
                maintenance_margin_rate=self._margin,
            )
            if state["liquidated"]:
                liquidated = True
            status = "liquidated" if state["liquidated"] else "open"

            equity_points.append(GridEquityPoint(
                ts=bar["ts"],
                equity=float(state["equity"]),
                wallet_balance=float(state["wallet_balance"]),
                position_quantity=float(state["position_quantity"]),
                average_cost=float(self._avg_cost) if self._position_qty != 0 else None,
                unrealized_pnl=float(state["unrealized_pnl"]),
                margin_used=float(state["margin_used"]),
                maintenance_margin=float(state["maintenance_margin"]),
                margin_ratio=state["margin_ratio"],
                liquidation_price=state["liquidation_price"],
                status=status,
            ))

            prev_close = price

        # 4. 績效與風險
        initial_capital = self.spec.initial_capital
        final_equity = float(equity_points[-1].equity) if equity_points else float(initial_capital)
        first_close = float(candles[0]["close"]) if candles else 0.0
        last_close = float(candles[-1]["close"]) if candles else final_equity
        holding_bars = sum(1 for p in equity_points if p.position_quantity != 0)
        completed_pnls = [c.realized_pnl or 0.0 for c in cycles]

        metrics = compute_metrics_from_equity(
            initial_capital=float(initial_capital),
            final_equity=final_equity,
            first_close=first_close,
            last_close=last_close,
            direction=self._direction,
            realized_pnl=float(self._realized_pnl),
            total_fees=float(self._total_fees),
            cycles_completed=self._completed_cycles,
            equity_values=[p.equity for p in equity_points],
            position_values=[p.position_quantity for p in equity_points],
            completed_cycle_pnls=completed_pnls,
            total_bars=max(1, n_bars),
            holding_bars=holding_bars,
        )
        risk = build_risk_metrics(equity_points)

        candles_out = [
            {
                "t": c["ts"],
                "o": float(c["open"]),
                "h": float(c["high"]),
                "l": float(c["low"]),
                "c": float(c["close"]),
                "v": float(c["volume"]),
            }
            for c in candles
        ]

        return GridBacktestResult(
            spec=self.spec,
            bars=n_bars,
            timeframe=self.spec.timeframe,
            first_ts=first_ts,
            last_ts=last_ts,
            metrics=metrics,
            risk=risk,
            allocation=self.allocation,
            equity=equity_points,
            marks=marks,
            cycles=cycles,
            warnings=self._build_warnings(),
            data_status=self._data_status,
            source=self._source,
            skipped_margin_fills=self._skipped_margin_fills,
            candles=candles_out,
        )
