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
    # 風險閘門 (regime gate) 本次執行所套用的參數（稽核軌跡用；None = 未套用/未知）
    gate_pts: Optional[int] = None
    gate_scale: Optional[float] = None
    gate_allow_new_entries: Optional[bool] = None
    gate_force_flatten: Optional[bool] = None
    # P4-1 統一記帳：本批換倉由 canonical ledger（bot_baskets 均價法）結算出的損益
    realized_pnl_usdt: Optional[float] = None
    fees_usdt: Optional[float] = None
    batch_id: Optional[str] = None


class RegimeDecision(BaseModel):
    """風險閘門 (regime gate) 決策結果

    pts(t) = [ BTC 收盤 > BTC 50 日 SMA ]
           + [ 宇宙中站上 50 日 SMA 的比例 > 30% ]
           + [ 全市場資金費率（252 日百分位）> 45 ]

    參數與語意來源：docs/CoinFull_Prime_02_Regime_Playbook.md、
    docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md、
    docs/CoinFull_Prime_04_Position_Sizing_Timing.md
    """
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    enabled: bool = True
    log_only: bool = False           # True = 僅記錄不改變下單行為（上線初期 2 週建議）
    mode: str = "flatten"            # flatten(保守) | soft(積極)

    # 三個構成元件
    trend_flag: int = 0              # BTC > 50 日 SMA
    breadth_flag: int = 0            # 廣度 > 門檻
    funding_flag: int = 0            # 資金費率百分位 > 門檻
    pts: int = 0                     # 三者合計 (0~3)

    # 原始量測值 (None = 該元件資料缺漏，已保守處理)
    btc_close: Optional[float] = None
    btc_ma50: Optional[float] = None
    breadth_pct: Optional[float] = None       # 宇宙站上 50 日 SMA 比例 (%)
    funding_pctile: Optional[float] = None    # 全市場資金費率 252 日百分位 (0~100)
    btc_ret_1d_pct: Optional[float] = None    # 最近一個已收盤交易日 BTC 報酬 (%)

    # 決策輸出
    scale: float = 1.0               # 目標總名義倍數 (0 / 0.35 / 0.5 / 1.0 / 1.15)
    allow_new_entries: bool = True   # 是否允許建立新倉
    force_flatten: bool = False      # 是否強制平掉所有受管部位 (T1)
    crash_defer: bool = False        # T2: 崩跌日 → 換倉延後 24 小時
    degraded: bool = False           # 資料缺漏（保守處理）標記
    reasons: List[str] = Field(default_factory=list)
