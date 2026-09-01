"""
core/services/grid/backtest/models.py —— 網格回測資料契約模型

約束：
- 全數值以標準型別（int / float / str / Optional）序列化輸出，未知數值一律 None（G-5）。
- 凍結不可變 dataclass 提供 to_dict() 供 API 邊界與 JSON 序列化。
- 零 I/O、純資料容器。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GridBacktestSpec:
    """網格回測規格（純資料群組）。"""

    symbol: str
    exchange: str
    lower_price: Decimal
    upper_price: Decimal
    grid_count: int
    order_amount: Decimal  # 每格成交數量（base 幣）

    grid_type: str = "neutral"  # long / short / neutral
    account_mode: str = "paper"  # paper / live / unknown
    fee_rate: Decimal = Decimal("0.0002")
    leverage: int = 10
    margin_mode: str = "isolated"
    maintenance_margin_rate: Decimal = Decimal("0.005")

    initial_capital: Decimal = Decimal("10000")
    timeframe: str = "1h"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "grid_type": self.grid_type,
            "account_mode": self.account_mode,
            "lower_price": float(self.lower_price),
            "upper_price": float(self.upper_price),
            "grid_count": int(self.grid_count),
            "order_amount": float(self.order_amount),
            "fee_rate": float(self.fee_rate),
            "leverage": int(self.leverage),
            "margin_mode": self.margin_mode,
            "maintenance_margin_rate": float(self.maintenance_margin_rate),
            "initial_capital": float(self.initial_capital),
            "timeframe": self.timeframe,
        }


@dataclass(frozen=True)
class GridEquityPoint:
    """單一 K 棒結束時的權益快照。"""

    ts: int
    equity: float
    wallet_balance: float
    position_quantity: float
    average_cost: Optional[float] = None  # 無部位 → None（G-5）
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0
    maintenance_margin: float = 0.0
    margin_ratio: Optional[float] = None
    liquidation_price: Optional[float] = None
    status: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "equity": float(self.equity),
            "wallet_balance": float(self.wallet_balance),
            "position_quantity": float(self.position_quantity),
            "average_cost": self.average_cost,
            "unrealized_pnl": float(self.unrealized_pnl),
            "margin_used": float(self.margin_used),
            "maintenance_margin": float(self.maintenance_margin),
            "margin_ratio": self.margin_ratio,
            "liquidation_price": self.liquidation_price,
            "status": self.status,
        }


@dataclass(frozen=True)
class GridFillMark:
    """成交標記（供前端圖表標記，與 DCA TradeMark 風格一致）。"""

    ts: int
    price: float
    side: str  # buy / sell
    quantity: float
    notional: float
    fee: float
    kind: str  # open_long / close_long / open_short / close_short
    cell_index: int = 0
    cycle_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "price": float(self.price),
            "side": self.side,
            "quantity": float(self.quantity),
            "notional": float(self.notional),
            "fee": float(self.fee),
            "kind": self.kind,
            "cell_index": self.cell_index,
            "cycle_index": self.cycle_index,
        }


@dataclass(frozen=True)
class GridCycleRecord:
    """單一網格完成循環（每個 cell 空倉 → 持倉 → 空倉 記為一輪）。"""

    index: int
    direction: str  # long / short
    entry_ts: Optional[int] = None
    exit_ts: Optional[int] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    quantity: float = 0.0
    realized_pnl: Optional[float] = None
    realized_pct: Optional[float] = None
    bars: int = 0
    cell_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "direction": self.direction,
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": float(self.quantity),
            "realized_pnl": self.realized_pnl,
            "realized_pct": self.realized_pct,
            "bars": self.bars,
            "cell_index": self.cell_index,
        }


@dataclass(frozen=True)
class GridAllocation:
    """初始資金 → 每格下單量的分配結果。"""

    order_amount: Decimal
    per_grid_notional: Decimal
    max_fillable_cells: int
    required_capital: Decimal
    supplied_capital: Decimal
    insufficient_capital: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_amount": float(self.order_amount),
            "per_grid_notional": float(self.per_grid_notional),
            "max_fillable_cells": int(self.max_fillable_cells),
            "required_capital": float(self.required_capital),
            "supplied_capital": float(self.supplied_capital),
            "insufficient_capital": bool(self.insufficient_capital),
            "message": self.message,
        }


@dataclass(frozen=True)
class GridRiskMetrics:
    """風險 / 爆倉指標彙總。"""

    max_drawdown_pct: float = 0.0
    min_equity: Optional[float] = None
    max_margin_used_pct: Optional[float] = None
    liquidation_occurred: bool = False
    liquidation_ts: Optional[int] = None
    liquidation_price: Optional[float] = None
    worst_margin_buffer: Optional[float] = None
    worst_margin_buffer_ts: Optional[int] = None
    additional_capital_required_now: Optional[float] = None
    additional_capital_to_prevent_liquidation: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_drawdown_pct": float(self.max_drawdown_pct),
            "min_equity": self.min_equity,
            "max_margin_used_pct": self.max_margin_used_pct,
            "liquidation_occurred": bool(self.liquidation_occurred),
            "liquidation_ts": self.liquidation_ts,
            "liquidation_price": self.liquidation_price,
            "worst_margin_buffer": self.worst_margin_buffer,
            "worst_margin_buffer_ts": self.worst_margin_buffer_ts,
            "additional_capital_required_now": self.additional_capital_required_now,
            "additional_capital_to_prevent_liquidation": self.additional_capital_to_prevent_liquidation,
        }


@dataclass(frozen=True)
class GridBacktestResult:
    """完整網格回測輸出報告。"""

    spec: GridBacktestSpec
    bars: int
    timeframe: str
    first_ts: Optional[int]
    last_ts: Optional[int]
    metrics: Dict[str, Any]
    risk: GridRiskMetrics
    allocation: GridAllocation
    equity: List[GridEquityPoint]
    marks: List[GridFillMark]
    cycles: List[GridCycleRecord]
    warnings: List[str]
    data_status: str = "live"
    source: str = "exchange"
    skipped_margin_fills: int = 0
    candles: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "bars": self.bars,
            "timeframe": self.timeframe,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "metrics": dict(self.metrics),
            "risk": self.risk.to_dict(),
            "allocation": self.allocation.to_dict(),
            "equity": [e.to_dict() for e in self.equity],
            "marks": [m.to_dict() for m in self.marks],
            "cycles": [c.to_dict() for c in self.cycles],
            "warnings": list(self.warnings),
            "data_status": self.data_status,
            "source": self.source,
            "skipped_margin_fills": self.skipped_margin_fills,
            "candles": list(self.candles),
        }
