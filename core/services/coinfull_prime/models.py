"""
CoinFull Prime 數據模型定義
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class RawFeature(BaseModel):
    """單幣原始特徵指標"""
    symbol: str
    momentum: float = 0.0      # 24h/7D 價格動能
    flow: float = 1.0          # Taker 買賣比例 (Taker Buy / Taker Sell)
    funding: float = 0.0       # 資金費率（通常反向因子：-1.0 * funding_rate）
    liquidation: float = 1.0   # 大戶帳戶多空比 (Top Trader Long/Short Ratio)


class RawMetrics(BaseModel):
    """原始客觀指標數值"""
    price: float = 0.0
    price_change_pct: float = 0.0   # 24h 漲跌幅 %
    taker_flow_ratio: float = 1.0   # Taker 買賣比
    funding_rate_pct: float = 0.0   # 資金費率 % (例如 0.01%)
    top_trader_ratio: float = 1.0   # 大戶帳戶多空比


class ParameterEvaluations(BaseModel):
    """各項指標之中文語意評價 (例如: 過熱, 極強, 弱勢)"""
    momentum_eval: str = "中性"      # 動能評價
    flow_eval: str = "平衡"          # 買賣流
    funding_eval: str = "正常"       # 費率評價
    liquidation_eval: str = "持平"   # 大戶博弈
    overall_eval: str = "觀望"       # 綜合結論


class SignalZScores(BaseModel):
    """標準化後的 Z-Score 分項"""
    mom_z: float = 0.0
    flow_z: float = 0.0
    fund_z: float = 0.0
    liq_z: float = 0.0


class RankedItem(BaseModel):
    """截面評分後的單幣結果 (含原始數值、Z 分與語意評價)"""
    symbol: str
    score: float
    rank: int
    signals: SignalZScores
    raw_params: RawMetrics = Field(default_factory=RawMetrics)
    evaluations: ParameterEvaluations = Field(default_factory=ParameterEvaluations)
    side: Optional[str] = None       # "long" | "short" | None
    target_weight: float = 0.0       # 目標權重 (例如 0.125)
    target_notional: float = 0.0     # 目標名義金額 (USDT)


class UniverseRankResult(BaseModel):
    """每日截面打分完整產出"""
    version: str = "1.0"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    strategy: str = "coinfull_prime"
    universe_size: int = 79
    prices: Dict[str, float] = Field(default_factory=dict)
    long_list: List[RankedItem] = Field(default_factory=list)
    short_list: List[RankedItem] = Field(default_factory=list)
    all_rankings: List[RankedItem] = Field(default_factory=list)
    status: str = "success"
    message: Optional[str] = None


class RebalanceOrder(BaseModel):
    """換倉差異訂單"""
    symbol: str
    side: str                        # "buy" | "sell"
    order_type: str = "market"       # "market" | "limit"
    amount: float                    # 標的數量（幣數）
    notional_usdt: float             # 名義價值 (USDT)
    reduce_only: bool = False        # 是否為平倉/減倉
    reason: str                      # "exit_old" | "enter_new" | "adjust_weight"
    status: str = "pending"          # "pending" | "filled" | "skipped" | "failed"
    error: Optional[str] = None


class RebalanceReport(BaseModel):
    """換倉執行報告"""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dry_run: bool = True
    exchange: str = "paper"
    total_capital_usdt: float = 1000.0
    leverage: float = 1.0
    orders: List[RebalanceOrder] = Field(default_factory=list)
    closed_symbols: List[str] = Field(default_factory=list)
    opened_symbols: List[str] = Field(default_factory=list)
    managed_symbols: List[str] = Field(default_factory=list)
    protected_symbols: List[str] = Field(default_factory=list)
    status: str = "completed"
    error: Optional[str] = None
