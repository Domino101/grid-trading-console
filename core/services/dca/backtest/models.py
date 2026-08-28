"""
core/services/dca/backtest/models.py —— DCA 回測子系統資料契約模型

依據：docs/dca_chart_backtest_plan_zh-TW.md §4.4 輸出契約與 §4.5 績效指標定義
約束：
- 全數值以標準型別（int, float, str, Optional）序列化輸出，未知數值一律 None（禁止以 0 代替，G-5）
- 凍結不可變 dataclass，提供 to_dict() 供 API 邊界與 JSON 序列化
- 零 I/O、純資料容器
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TradeMark:
    """
    回測交易標記（對應即時圖表 marks[] 規範 §4.7）。

    包含下單、基礎單成交、安全單成交、止盈成交、止損成交與急跌跳過等事件。
    """

    ts: int  # 棒 unix 秒（R-1 後置蓋章）
    price: float  # 成交/掛單價
    kind: str  # base_filled | safety_filled | take_profit_filled | stop_loss_filled | skipped | order_placed
    role: str  # base | safety | take_profit | stop_loss
    side: str  # buy | sell
    quantity: Optional[float] = None  # 未知 → None（G-5，禁止 0）
    notional: Optional[float] = None
    slot_index: Optional[int] = None
    cycle_index: int = 1  # 第幾輪（1-based）
    realized_pct: Optional[float] = None  # 僅 TP/SL：本輪實現報酬%（收益標記）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "price": self.price,
            "kind": self.kind,
            "role": self.role,
            "side": self.side,
            "quantity": self.quantity,
            "notional": self.notional,
            "slot_index": self.slot_index,
            "cycle_index": self.cycle_index,
            "realized_pct": self.realized_pct,
        }


@dataclass(frozen=True)
class EquityPoint:
    """單一 K 棒結束時的權益快照（§4.4）。"""

    ts: int  # 棒 unix 秒
    equity: float  # initial_capital + realized + unrealized − fees
    position_quantity: float
    average_cost: Optional[float] = None  # 無部位 → None（G-5）
    status: str = "armed"  # 該棒結束時的 DcaStatus.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "equity": self.equity,
            "position_quantity": self.position_quantity,
            "average_cost": self.average_cost,
            "status": self.status,
        }


@dataclass(frozen=True)
class CycleRecord:
    """單一 DCA 循環輪次紀錄（§4.4）。"""

    index: int  # 輪次索引（1-based）
    direction: str  # long | short
    entry_ts: Optional[int] = None  # 基礎單成交棒 unix 秒
    exit_ts: Optional[int] = None  # 平倉棒 unix 秒
    entry_price: Optional[float] = None
    average_cost: Optional[float] = None  # 平倉時均價
    exit_price: Optional[float] = None
    safety_filled: int = 0  # 本輪成交之安全單數
    exit_reason: str = "running"  # take_profit | stop_loss | max_cycles | end_of_data | running
    realized_pnl: Optional[float] = None
    realized_pct: Optional[float] = None
    mfe_pct: Optional[float] = None  # 最大有利偏移 %
    mae_pct: Optional[float] = None  # 最大不利偏移 %
    bars: int = 0  # 本輪持倉棒數

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "direction": self.direction,
            "entry_ts": self.entry_ts,
            "exit_ts": self.exit_ts,
            "entry_price": self.entry_price,
            "average_cost": self.average_cost,
            "exit_price": self.exit_price,
            "safety_filled": self.safety_filled,
            "exit_reason": self.exit_reason,
            "realized_pnl": self.realized_pnl,
            "realized_pct": self.realized_pct,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "bars": self.bars,
        }


@dataclass(frozen=True)
class BacktestRequest:
    """回測請求規格（§4.6）。"""

    bot_id: Optional[str] = None
    draft: Optional[Dict[str, Any]] = None
    timeframe: str = "15m"
    bars: int = 2000
    initial_capital: Optional[str] = None
    exchange: Optional[str] = None
    symbol: Optional[str] = None


@dataclass(frozen=True)
class BacktestResult:
    """完整回測輸出報告（§4.4 與 §4.5 凍結契約）。"""

    bars: int
    timeframe: str
    first_ts: Optional[int]
    last_ts: Optional[int]
    metrics: Dict[str, Any]  # §4.5 全部績效指標
    equity: List[EquityPoint]
    marks: List[TradeMark]
    cycles: List[CycleRecord]
    data_status: str  # live | paper | stale | unavailable
    source: str  # cache | exchange | paper_synthetic
    warnings: List[str]  # 簡化事項揭露（D-11），不得為空
    bars_per_second: float  # 效能基準（掃描預算依據）
    candles: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bars": self.bars,
            "timeframe": self.timeframe,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "metrics": dict(self.metrics),
            "equity": [e.to_dict() for e in self.equity],
            "marks": [m.to_dict() for m in self.marks],
            "cycles": [c.to_dict() for c in self.cycles],
            "data_status": self.data_status,
            "source": self.source,
            "warnings": list(self.warnings),
            "bars_per_second": self.bars_per_second,
            "candles": list(self.candles),
        }

