"""
Pydantic 請求/回應模型

定義 /api/v1/ 路由的請求體與回應結構。
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


# ────────────────────────────────────────────────────────
# 請求模型
# ────────────────────────────────────────────────────────

class GridMetadata(BaseModel):
    """Hermes 操作元資料"""
    source: str = Field(default="hermes", description="操作來源: hermes / dashboard / manual")
    reason: Optional[str] = Field(default=None, description="決策理由")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="信心度 0-1")


class CreateGridRequest(BaseModel):
    """POST /api/v1/grids 請求體"""
    # 必填
    symbol: str = Field(..., description="交易對符號，例如 SOL/USDC:USDC")
    exchange: str = Field(..., description="交易所名稱: binance / lighter / backpack / hyperliquid")
    grid_type: str = Field(..., description="網格類型: long/short/neutral/follow_long/follow_short/martingale_long/martingale_short")
    grid_interval: float = Field(..., gt=0, description="網格間隔")
    order_amount: float = Field(..., gt=0, description="每格訂單數量")

    # 價格區間（非 follow 模式必填）
    lower_price: Optional[float] = Field(default=None, description="價格下限")
    upper_price: Optional[float] = Field(default=None, description="價格上限")

    # 精度與槓桿
    quantity_precision: int = Field(default=4, ge=0, le=10, description="數量精度（小數位數）")
    price_decimals: int = Field(default=2, ge=0, le=10, description="價格精度（小數位數）")
    leverage: int = Field(default=10, ge=1, le=125, description="槓桿倍數")
    margin_mode: str = Field(default="cross", description="保證金模式: cross / isolated")
    account_mode: str = Field(default="paper", pattern="^(paper|live)$", description="帳戶模式: paper / live")
    bot_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description="穩定機器人身份（編輯/重建既有網格時傳入以保留交易紀錄；未提供時自動產生）",
    )

    # 速率保護（Binance 會自動填入安全預設值）
    limit_only: Optional[bool] = None
    active_window_each_side: Optional[int] = None
    active_window_refresh_interval: Optional[int] = None
    order_health_check_enabled: Optional[bool] = None
    order_health_check_interval: Optional[int] = None
    health_check_adjust_position_enabled: Optional[bool] = None
    rest_position_query_interval: Optional[int] = None
    fee_rate: Optional[float] = None
    reverse_order_grid_distance: Optional[int] = None
    ws_only_mode: Optional[bool] = None
    rest_backup_enabled: Optional[bool] = None

    # Follow 模式專用
    follow_grid_count: Optional[int] = Field(default=None, description="Follow 模式網格數量")
    follow_timeout: Optional[int] = Field(default=None, description="脫離超時（秒）")
    follow_distance: Optional[int] = Field(default=None, description="脫離距離（格數）")
    price_offset_grids: Optional[int] = Field(default=None, description="價格偏移網格數")

    # 剝頭皮
    scalping_enabled: Optional[bool] = None
    scalping_trigger_percent: Optional[float] = None
    scalping_take_profit_grids: Optional[int] = None
    smart_scalping_enabled: Optional[bool] = None
    allowed_deep_drops: Optional[int] = None
    min_drop_threshold_percent: Optional[float] = None

    # 本金保護
    capital_protection_enabled: Optional[bool] = None
    capital_protection_trigger_percent: Optional[float] = None

    # 止盈
    take_profit_enabled: Optional[bool] = None
    take_profit_percentage: Optional[float] = None

    # 價格鎖定
    price_lock_enabled: Optional[bool] = None
    price_lock_threshold: Optional[float] = None
    price_lock_start_at_threshold: Optional[bool] = None

    # 止損保護
    stop_loss_protection_enabled: Optional[bool] = None
    stop_loss_trigger_percent: Optional[float] = None
    stop_loss_escape_timeout: Optional[int] = None
    stop_loss_apr_threshold: Optional[float] = None

    # 馬丁格爾
    martingale_increment: Optional[float] = None

    # 持倉容差
    position_tolerance: Optional[Dict[str, float]] = None

    # 控制
    auto_start: bool = Field(default=False, description="建立後提交啟動意圖；PAPER 走模擬盤，LIVE 需 feature flag")

    # 元資料
    metadata: Optional[GridMetadata] = None


# ────────────────────────────────────────────────────────
# 回應模型
# ────────────────────────────────────────────────────────

class GridInfo(BaseModel):
    """單一網格資訊"""
    grid_id: str
    config_path: str
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    grid_type: Optional[str] = None
    status: str = "unknown"  # starting / running / stopping / stopped / unknown
    mode: Optional[str] = None
    data_status: Optional[str] = None
    command_id: Optional[str] = None
    command_status: Optional[str] = None
    lower_price: Optional[float] = None
    upper_price: Optional[float] = None
    grid_interval: Optional[float] = None
    order_amount: Optional[float] = None
    leverage: Optional[int] = None
    created_by: Optional[str] = None


class GridListResponse(BaseModel):
    """GET /api/v1/grids 回應"""
    total: int
    active: int
    grids: List[GridInfo]


class GridCreateResponse(BaseModel):
    """POST /api/v1/grids 回應"""
    grid_id: str
    config_path: str
    status: str  # created / pending_engine
    yaml_content: str
    message: str
    command_id: Optional[str] = None


class GridActionResponse(BaseModel):
    """Start/Stop/Delete 操作回應；accepted 表示已記錄，非引擎已確認。"""
    grid_id: str
    action: str
    status: str
    message: str
    command_id: Optional[str] = None


class GridCommandResponse(BaseModel):
    """可稽核的策略生命週期命令。"""
    command_id: str
    grid_id: str
    config_path: str
    action: str
    status: str  # accepted / pending_engine / running / stopped / failed / timed_out
    source: str
    reason: Optional[str] = None
    detail: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: float
    updated_at: float


class GridCommandListResponse(BaseModel):
    grid_id: str
    commands: List[GridCommandResponse]


class CircuitBreakerInfo(BaseModel):
    is_open: bool
    error_code: Optional[str] = None
    until: Optional[str] = None


class ApiThrottleInfo(BaseModel):
    total_calls: int
    window_current: int
    window_max: int


class EngineInfo(BaseModel):
    process_running: bool
    pid: Optional[int] = None


class SystemHealthResponse(BaseModel):
    """GET /api/v1/system/health 回應"""
    status: str  # healthy / degraded / unhealthy
    circuit_breaker: CircuitBreakerInfo
    api_throttle: ApiThrottleInfo
    engine: EngineInfo
    running_grids: int
    desired_running_grids: int = 0
    running_grid_configs: List[str]
    max_recommended_grids: int
    can_accept_new_grid: bool
    live_trading_enabled: bool = False



class MarketInfoResponse(BaseModel):
    """GET /api/v1/market/info/{symbol} 回應"""
    symbol: str
    exchange: str
    price_precision: Optional[int] = None
    quantity_precision: Optional[int] = None
    min_order_amount: Optional[float] = None
    min_notional: Optional[float] = None
    contract_size: Optional[float] = None
    cached: bool = False


class GridRecommendationPeriod(BaseModel):
    """單一週期（如 7D、30D、180D）之 AI 推薦網格參數與歷史回測指標"""
    days: int
    lower_price: float
    upper_price: float
    grid_count: int
    grid_interval: float
    recommended_order_amount: float
    estimated_apr: float
    completed_cycles: int
    price_range_percent: float
    period_low: float
    period_high: float


class GridRecommendationResponse(BaseModel):
    """GET /api/v1/market/recommendation/{symbol} 回應"""
    symbol: str
    exchange: str
    current_price: float
    price_precision: int
    quantity_precision: int
    min_notional: float
    selected_period: str
    recommended: GridRecommendationPeriod
    periods: Dict[str, GridRecommendationPeriod]


class MarketRankingItem(BaseModel):
    """單一代幣之年化率排行項目"""
    rank: int
    symbol: str
    exchange: str
    current_price: float
    volume_24h: float
    price_change_24h_percent: float
    estimated_apr: float
    completed_cycles: int
    lower_price: float
    upper_price: float
    grid_count: int
    grid_interval: float
    recommended_order_amount: float
    rating: str  # S / A / B / C


class MarketRankingsResponse(BaseModel):
    """GET /api/v1/market/rankings 回應"""
    exchange: str
    period: str
    updated_at: float
    cached: bool = False
    items: List[MarketRankingItem]




# ── 階段二：統計與摘要模型 ──

class GridStatsPendingOrders(BaseModel):
    buy: Optional[int] = Field(default=None, description="買單數量")
    sell: Optional[int] = Field(default=None, description="賣單數量")
    total: Optional[int] = Field(default=None, description="總掛單數")


class GridStatsFilledOrders(BaseModel):
    buy: Optional[int] = Field(default=None, description="買單成交次數")
    sell: Optional[int] = Field(default=None, description="賣單成交次數")
    cycles: Optional[int] = Field(default=None, description="網格循環完成次數")


class GridStatsProfit(BaseModel):
    grid_profit: Optional[float] = Field(default=None, description="網格套利利潤（真實配對毛利）")
    realized: Optional[float] = Field(default=None, description="已實現盈虧")
    unrealized: Optional[float] = Field(default=None, description="未實現盈虧")
    total: Optional[float] = Field(default=None, description="總盈虧")
    fees: Optional[float] = Field(default=None, description="手續費支出")
    net: Optional[float] = Field(default=None, description="淨盈虧（扣除手續費）")
    rate: Optional[float] = Field(default=None, description="盈虧率 %")
    account_unrealized: Optional[float] = Field(default=None, description="交易所全帳戶未實現盈虧")


class GridStatsBalance(BaseModel):
    spot: Optional[float] = Field(default=None, description="現貨餘額")
    collateral: Optional[float] = Field(default=None, description="合約保證金/抵押品")
    total: Optional[float] = Field(default=None, description="帳戶總資產估值")
    utilization: Optional[float] = Field(default=None, description="資金利用率 %")


class GridStatsTime(BaseModel):
    running_time: str = Field(default="", description="運行時間長度描述")
    last_trade: Optional[str] = Field(default=None, description="最後交易時間（ISO字串）")


class GridStatsResponse(BaseModel):
    """GET /api/v1/grids/{grid_id}/stats 回應"""
    grid_id: str
    symbol: str
    status: str = Field(default="stopped", description="網格狀態: starting / running / stopping / stopped")
    data_status: str = Field(default="unavailable", description="live / stale / unavailable")
    stale: bool = Field(default=True, description="資料是否過期或尚未同步")
    current_price: Optional[float] = Field(default=None, description="當前市價；未知為 null")
    current_position: Optional[float] = Field(default=None, description="當前網格持倉數量；未知為 null")
    grid_position: Optional[float] = Field(default=None, description="網格自身虛擬持倉；未知為 null")
    account_position: Optional[float] = Field(default=None, description="交易所全帳戶持倉；未知為 null")
    average_cost: Optional[float] = Field(default=None, description="持倉均價")
    pending_orders: Optional[GridStatsPendingOrders] = None
    filled_orders: Optional[GridStatsFilledOrders] = None
    profit: Optional[GridStatsProfit] = None
    grid_utilization: Optional[float] = None
    balance: Optional[GridStatsBalance] = None
    time: Optional[GridStatsTime] = None
    last_update: Optional[float] = None


class GridEvent(BaseModel):
    grid_id: str
    event_type: str
    status: str
    detail: Optional[str] = None
    occurred_at: float


class GridEventListResponse(BaseModel):
    grid_id: str
    events: List[GridEvent]


class GridHistoryPoint(BaseModel):
    recorded_at: float
    current_price: Optional[float] = None
    net_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    equity: Optional[float] = None


class GridHistoryResponse(BaseModel):
    grid_id: str
    range_hours: int
    points: List[GridHistoryPoint]


class GridOrdersResponse(BaseModel):
    grid_id: str
    available: bool
    orders: List[Dict[str, Any]] = []
    message: Optional[str] = None


class GridFillsResponse(BaseModel):
    grid_id: str
    available: bool
    fills: List[Dict[str, Any]] = []
    message: Optional[str] = None


class GridSummaryItem(BaseModel):
    """全局摘要中的單一網格狀態項目"""
    grid_id: str
    symbol: Optional[str] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    total_pnl: Optional[float] = None
    current_price: Optional[float] = None
    status: str = "stopped"
    stale: bool = True
    data_status: str = "unavailable"


class GridSummaryResponse(BaseModel):
    """GET /api/v1/grids/summary 回應"""
    total_grids: int
    active_grids: int
    total_realized_pnl: float
    total_unrealized_pnl: float
    total_pnl: float
    grids: List[GridSummaryItem]


# ── Dashboard 批次快照模型 ──

class DashboardAggregate(BaseModel):
    """總覽 KPI；所有金額均只彙總已知的 runtime 資料。"""
    total_grids: int
    running_grids: int
    available_grids: int
    stale_grids: int
    grid_profit: float = 0.0
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    net_pnl: float
    volume: float
    completed_cycles: int


class DashboardGridItem(BaseModel):
    """總覽卡片的單一網格讀模型；未知數值保留 null。"""
    grid_id: str
    config_path: str
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    grid_type: Optional[str] = None
    mode: str = "unknown"  # live / paper / unknown
    status: str
    command_id: Optional[str] = None
    command_status: Optional[str] = None
    data_status: str  # live / stale / unavailable
    last_updated_at: Optional[float] = None
    current_price: Optional[float] = None
    current_position: Optional[float] = None
    grid_position: Optional[float] = None
    account_position: Optional[float] = None
    equity: Optional[float] = None
    grid_profit: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    account_unrealized_pnl: Optional[float] = None
    fees: Optional[float] = None
    net_pnl: Optional[float] = None
    pnl_rate: Optional[float] = None
    volume: Optional[float] = None
    completed_cycles: Optional[int] = None
    filled_buy: Optional[int] = None
    filled_sell: Optional[int] = None
    pending_orders: Optional[int] = None
    lower_price: Optional[float] = None
    upper_price: Optional[float] = None
    grid_interval: Optional[float] = None
    order_amount: Optional[float] = None
    leverage: Optional[int] = None
    last_trade: Optional[str] = None
    source: Optional[str] = None
    start_blocked_reason: Optional[str] = None
    desired_running: bool = False


class DashboardSnapshotResponse(BaseModel):
    """GET /api/v1/dashboard/snapshot 回應。"""
    generated_at: float
    data_status: str  # live / degraded / unavailable
    account_mode: str = "unknown"  # live / paper / mixed / unknown
    aggregate: DashboardAggregate
    aggregate_live: Optional[DashboardAggregate] = None
    aggregate_paper: Optional[DashboardAggregate] = None
    grids: List[DashboardGridItem]


# ── 階段三：審計日誌模型 ──

class AuditLogItem(BaseModel):
    """審計日誌項目"""
    id: int
    timestamp: str
    source: str
    action: str
    grid_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    result: str
    reason: Optional[str] = None
    error_msg: Optional[str] = None


class AuditLogListResponse(BaseModel):
    """GET /api/v1/system/audit 回應"""
    total: int
    logs: List[AuditLogItem]


# ── Phase 4：DCA 機器人契約 ──

class DcaIndicatorConfig(BaseModel):
    """單一指標設定（含靜態參數與執行期快照即時值）。"""
    type: str
    period: Optional[int] = None
    oversold: Optional[float] = None
    overbought: Optional[float] = None
    fast: Optional[int] = None
    slow: Optional[int] = None
    signal: Optional[int] = None
    std_dev: Optional[float] = None
    lookback_bars: Optional[int] = None
    long_threshold_percent: Optional[float] = None
    short_threshold_percent: Optional[float] = None
    condition: Optional[str] = None
    # 執行期即時值（快照讀模型附加）
    current_value: Optional[float] = None
    triggered: Optional[bool] = None
    triggered_long: Optional[bool] = None
    triggered_short: Optional[bool] = None
    insufficient_data: Optional[bool] = None
    warmup_have: Optional[int] = None
    warmup_need: Optional[int] = None
    details: Optional[Dict[str, Optional[float]]] = None


class DcaEntryConfig(BaseModel):
    """入場設定（§4.2-B / §4.3.0 共用管線）。"""
    mode: str = Field(default="immediate", description="immediate / indicator")
    logic: str = Field(default="all", description="all(AND) / any(OR)")
    timeframe: str = Field(default="15m", description="全部指標共用")
    fetch_limit: int = Field(default=300, ge=100, le=500)
    price_source: str = Field(default="close", description="close / hlc3 / ohlc4")
    confirm_bars: int = Field(default=1, ge=1, le=5)
    max_wait_seconds: Optional[int] = Field(default=None, gt=0)
    indicators: List[DcaIndicatorConfig] = Field(default_factory=list)


class DcaPreviewLevel(BaseModel):
    """單層階梯（index=0 為基礎單）。"""
    index: int
    deviation_percent: Optional[float] = None
    price: float
    notional: float
    quantity: float
    avg_price: float
    take_profit_price: Optional[float] = None


class DcaPreviewLadder(BaseModel):
    """單方向完整階梯摘要。"""
    direction: str
    entry_price: float
    required_capital: float
    max_drawdown_percent: float
    final_average_price: float
    final_take_profit_price: float
    levels: List[DcaPreviewLevel]


class DcaPreviewResponse(BaseModel):
    """POST /api/v1/dca/preview 回應。direction=both 時同時回傳兩組階梯。"""
    direction: Optional[str] = None
    required_capital: float
    ladder: Optional[DcaPreviewLadder] = None
    ladder_long: Optional[DcaPreviewLadder] = None
    ladder_short: Optional[DcaPreviewLadder] = None
    data_status: str = "live"


class DcaPreviewRequest(BaseModel):
    """POST /api/v1/dca/preview 請求體（純函式試算，零外部呼叫）。"""
    symbol: str = Field(..., description="交易對符號")
    exchange: str = Field(..., description="binance / lighter / backpack / hyperliquid")
    direction: str = Field(default="long", description="long / short / both")
    entry_price: float = Field(..., gt=0, description="入場價 E")
    base_order_amount: float = Field(..., gt=0, description="基礎單金額")
    amount_unit: str = Field(default="quote", description="quote(USD) / base(幣)")
    dca_mode: str = Field(default="order", description="order / position")
    averaging_method: str = Field(default="fixed_value", description="fixed_value / fixed_coin")
    max_safety_orders: int = Field(default=20, ge=1, le=50)
    price_deviation_percent: float = Field(default=0.8, gt=0, le=50)
    price_deviation_multiplier: float = Field(default=1.2, ge=1.0, le=3.0)
    order_size_multiplier: float = Field(default=1.0, ge=1.0, le=3.0)
    take_profit_percent: float = Field(default=1.0, gt=0, le=100)
    take_profit_order_type: str = Field(default="limit", description="limit / market")
    stop_loss_percent: Optional[float] = Field(default=None, gt=0)
    max_position_notional: Optional[float] = Field(default=None, gt=0)
    fee_rate: Optional[float] = Field(default=None, ge=0)
    leverage: int = Field(default=10, ge=1, le=125)
    margin_mode: str = Field(default="isolated", description="cross / isolated")
    quantity_precision: Optional[int] = Field(default=None, ge=0, le=10)
    price_decimals: Optional[int] = Field(default=None, ge=0, le=10)
    repeat_after_take_profit: bool = True
    cooldown_seconds: int = Field(default=300, ge=0)
    max_cycles: Optional[int] = Field(default=None, gt=0)
    both_tiebreak: str = Field(default="long_first", description="long_first / short_first")
    active_safety_orders_window: int = Field(default=5, ge=1, le=50)
    monitoring_interval: int = Field(default=15, ge=5, le=300)
    indicator_refresh_interval: int = Field(default=60, ge=30)
    order_health_check_interval: int = Field(default=300, ge=60)
    entry: DcaEntryConfig = Field(default_factory=DcaEntryConfig)


class CreateDcaRequest(BaseModel):
    """POST /api/v1/dca 請求體。"""
    symbol: str = Field(..., description="交易對符號")
    exchange: str = Field(..., description="binance / lighter / backpack / hyperliquid")
    direction: str = Field(default="long", description="long / short / both")
    account_mode: str = Field(default="paper", description="paper / live")
    bot_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description="穩定機器人身份（編輯/重建既有 DCA 時傳入以保留交易紀錄；未提供時自動產生）",
    )
    base_order_amount: float = Field(..., gt=0, description="基礎單金額")
    amount_unit: str = Field(default="quote", description="quote(USD) / base(幣)")
    dca_mode: str = Field(default="order", description="order / position")
    averaging_method: str = Field(default="fixed_value", description="fixed_value / fixed_coin")
    max_safety_orders: int = Field(default=20, ge=1, le=50)
    price_deviation_percent: float = Field(default=0.8, gt=0, le=50)
    price_deviation_multiplier: float = Field(default=1.2, ge=1.0, le=3.0)
    order_size_multiplier: float = Field(default=1.0, ge=1.0, le=3.0)
    take_profit_percent: float = Field(default=1.0, gt=0, le=100)
    take_profit_order_type: str = Field(default="limit", description="limit / market")
    stop_loss_percent: Optional[float] = Field(default=None, gt=0)
    max_position_notional: Optional[float] = Field(default=None, gt=0)
    fee_rate: Optional[float] = Field(default=None, ge=0)
    leverage: int = Field(default=10, ge=1, le=125)
    margin_mode: str = Field(default="isolated", description="cross / isolated")
    quantity_precision: Optional[int] = Field(default=None, ge=0, le=10)
    price_decimals: Optional[int] = Field(default=None, ge=0, le=10)
    repeat_after_take_profit: bool = True
    cooldown_seconds: int = Field(default=300, ge=0)
    max_cycles: Optional[int] = Field(default=None, gt=0)
    both_tiebreak: str = Field(default="long_first", description="long_first / short_first")
    active_safety_orders_window: int = Field(default=5, ge=1, le=50)
    monitoring_interval: int = Field(default=15, ge=5, le=300)
    indicator_refresh_interval: int = Field(default=60, ge=30)
    order_health_check_interval: int = Field(default=300, ge=60)
    entry: DcaEntryConfig = Field(default_factory=DcaEntryConfig)
    # auto_start 為 false 時僅寫入 YAML（default），true 則提交啟動命令。
    auto_start: bool = Field(default=False, description="建立後提交啟動意圖")
    # 僅用於 LIVE 建立時的市場規則校驗；PAPER / preview 不需要。
    entry_price: Optional[float] = Field(default=None, gt=0, description="LIVE 校驗用入場價")
    metadata: Optional[GridMetadata] = None


class DcaCreateResponse(BaseModel):
    """POST /api/v1/dca 回應。"""
    bot_id: str
    config_path: str
    status: str  # created / pending_engine
    yaml_content: str
    message: str
    command_id: Optional[str] = None


class DcaActionResponse(BaseModel):
    """Start/Stop/Delete 操作回應；accepted 表示已記錄，非引擎已確認。"""
    bot_id: str
    action: str
    status: str
    message: str
    command_id: Optional[str] = None


class DcaPosition(BaseModel):
    quantity: Optional[float] = None
    average_cost: Optional[float] = None
    total_cost: Optional[float] = None


class DcaLadderState(BaseModel):
    max_safety_orders: Optional[int] = None
    filled_safety_orders: Optional[int] = None
    active_safety_orders: Optional[int] = None
    take_profit_orders: Optional[int] = None


class DcaProfit(BaseModel):
    realized: Optional[float] = None
    unrealized: Optional[float] = None
    fees: Optional[float] = None


class DcaInfo(BaseModel):
    """GET /api/v1/dca 單一項目。"""
    bot_id: str
    config_path: str
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    direction: Optional[str] = None
    status: str = "stopped"
    mode: Optional[str] = None
    data_status: str = "unavailable"
    command_id: Optional[str] = None
    command_status: Optional[str] = None
    current_price: Optional[float] = None
    avg_price: Optional[float] = None
    position_quantity: Optional[float] = None
    filled_safety_orders: Optional[int] = None
    active_safety_orders: Optional[int] = None
    cycle_count: Optional[int] = None
    unrealized_pnl: Optional[float] = None
    base_order_amount: Optional[float] = None
    max_safety_orders: Optional[int] = None
    take_profit_percent: Optional[float] = None


class DcaListResponse(BaseModel):
    """GET /api/v1/dca 回應。"""
    total: int
    active: int
    bots: List[DcaInfo]


class DcaSnapshotItem(BaseModel):
    """單台 DCA 的批次快照讀模型；未知值保留 null。"""
    bot_id: str
    config_path: str
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    direction: Optional[str] = None
    mode: str = "unknown"
    status: str = "stopped"
    desired_running: bool = False
    command_id: Optional[str] = None
    command_status: Optional[str] = None
    data_status: str = "unavailable"
    source: Optional[str] = None
    last_updated_at: Optional[float] = None
    current_price: Optional[float] = None
    active_direction: Optional[str] = None
    position: Optional[DcaPosition] = None
    ladder: Optional[DcaLadderState] = None
    cycles: Optional[int] = None
    profit: Optional[DcaProfit] = None
    base_order_amount: Optional[float] = None
    max_safety_orders: Optional[int] = None
    take_profit_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None
    # Phase 5（卡片欄位表）：入場設定與執行期導出值；未知一律 None（G-5）。
    entry_mode: Optional[str] = None
    entry_logic: Optional[str] = None
    entry_timeframe: Optional[str] = None
    entry_indicators: List[DcaIndicatorConfig] = Field(default_factory=list)
    entry_price: Optional[float] = None
    next_safety_order_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    armed_since: Optional[float] = None
    start_blocked_reason: Optional[str] = None


class DcaSnapshotAggregate(BaseModel):
    """DCA 總覽 KPI；只彙總已知的 runtime 資料。"""
    total_bots: int
    running_bots: int
    available_bots: int
    stale_bots: int
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    net_pnl: float
    filled_safety_orders: int
    completed_cycles: int


class DcaSnapshotResponse(BaseModel):
    """GET /api/v1/dca/snapshot 回應。"""
    generated_at: float
    data_status: str
    account_mode: str = "unknown"
    aggregate: DcaSnapshotAggregate
    aggregate_live: Optional[DcaSnapshotAggregate] = None
    aggregate_paper: Optional[DcaSnapshotAggregate] = None
    bots: List[DcaSnapshotItem]


class DcaStatsResponse(BaseModel):
    """GET /api/v1/dca/{bot_id}/stats 回應。"""
    bot_id: str
    status: str = "stopped"
    data_status: str = "unavailable"
    last_updated_at: Optional[float] = None
    current_price: Optional[float] = None
    active_direction: Optional[str] = None
    position: Optional[DcaPosition] = None
    ladder: Optional[DcaLadderState] = None
    cycles: Optional[int] = None
    profit: Optional[DcaProfit] = None


class DcaLevelView(BaseModel):
    """GET /api/v1/dca/{bot_id}/ladder 單層。"""
    index: int
    role: str  # base / safety
    status: str = "waiting"
    planned_price: Optional[float] = None
    planned_quantity: Optional[float] = None
    actual_price: Optional[float] = None
    filled_price: Optional[float] = None
    filled_quantity: Optional[float] = None


class DcaLadderResponse(BaseModel):
    """GET /api/v1/dca/{bot_id}/ladder 回應。"""
    bot_id: str
    available: bool
    data_status: str = "unavailable"
    levels: List[DcaLevelView] = []
    message: Optional[str] = None


class DcaOrdersResponse(BaseModel):
    bot_id: str
    available: bool
    orders: List[Dict[str, Any]] = []
    message: Optional[str] = None


class DcaFillsResponse(BaseModel):
    bot_id: str
    available: bool
    fills: List[Dict[str, Any]] = []
    message: Optional[str] = None


class DcaEvent(BaseModel):
    bot_id: str
    event_type: str
    status: str
    detail: Optional[str] = None
    occurred_at: float


class DcaEventListResponse(BaseModel):
    bot_id: str
    events: List[DcaEvent]


class MarketIndicatorsResponse(BaseModel):
    """GET /api/v1/market/indicators/{symbol} 回應。"""
    symbol: str
    exchange: str
    timeframe: str
    fetch_limit: int
    current_price: Optional[float] = None
    rsi: Optional[Dict[str, Any]] = None
    macd: Optional[Dict[str, Any]] = None
    bollinger: Optional[Dict[str, Any]] = None
    price_change: Optional[Dict[str, Any]] = None
    data_status: str = "unavailable"
    cached: bool = False


# ── Phase C1：K 線歷史資料模型 ──

class KlineCandle(BaseModel):
    """單一 K 棒（OHLCV + 時間戳）。"""
    t: int = Field(..., description="棒起點 unix 秒 (UTC)")
    o: float = Field(..., description="開盤價")
    h: float = Field(..., description="最高價")
    l: float = Field(..., description="最低價")
    c: float = Field(..., description="收盤價")
    v: float = Field(..., description="成交量")


class KlinesResponse(BaseModel):
    """GET /api/v1/market/klines/{symbol} 回應。"""
    symbol: str
    exchange: str
    timeframe: str
    candles: List[KlineCandle]
    source: str = Field(..., description="cache / exchange / paper_synthetic / cache+exchange")
    data_status: str = Field(..., description="live / paper / stale / unavailable")
    gaps: int = Field(default=0, description="缺棒數量")
    warnings: List[str] = Field(default_factory=list)
    oldest_ts: Optional[int] = Field(default=None, description="最早一根 K 棒 unix 秒")


# ── Phase C5：DCA 圖表與回測 API 契約 ──

class DcaChartLine(BaseModel):
    """圖表上的價格線（§4.7 價格線語意）。"""
    kind: str = Field(..., description="entry / average_cost / take_profit / next_safety / stop_loss")
    price: Optional[float] = Field(default=None, description="價格；未知為 null（G-5，禁止 0）")


class DcaChartMark(BaseModel):
    """圖表上的交易標記（§4.7 標記語意）。"""
    ts: int = Field(..., description="時間戳 unix 秒（棒時間）")
    price: float = Field(..., description="成交/掛單價")
    kind: str = Field(..., description="order_placed / base_filled / safety_filled / take_profit_filled / stop_loss_filled / skipped")
    role: str = Field(..., description="base / safety / take_profit / stop_loss")
    side: str = Field(..., description="buy / sell")
    quantity: Optional[float] = Field(default=None, description="數量；未知為 null（G-5）")
    notional: Optional[float] = Field(default=None, description="名目金額；未知為 null")
    slot_index: Optional[int] = Field(default=None, description="階梯槽索引")
    cycle_index: int = Field(..., description="第幾輪（1-based）")
    realized_pct: Optional[float] = Field(default=None, description="本輪實現報酬%；僅 TP/SL 有值")


class DcaChartResponse(BaseModel):
    """GET /api/v1/dca/{bot_id}/chart 回應。"""
    bot_id: str
    candles: List[KlineCandle] = Field(default_factory=list, description="K 線資料")
    marks: List[DcaChartMark] = Field(default_factory=list, description="交易標記")
    lines: List[DcaChartLine] = Field(default_factory=list, description="價格線")
    current_price: Optional[float] = Field(default=None, description="當前市價；未知為 null")
    data_status: str = Field(default="unavailable", description="live / paper / stale / unavailable")
    source: str = Field(default="engine_runtime", description="資料來源")
    warnings: List[str] = Field(default_factory=list, description="警告訊息")


class BacktestTradeMark(BaseModel):
    """回測交易標記（§4.4 TradeMark）。"""
    ts: int = Field(..., description="棒 unix 秒")
    price: float = Field(..., description="成交/掛單價")
    kind: str = Field(..., description="order_placed / base_filled / safety_filled / take_profit_filled / stop_loss_filled / skipped")
    role: str = Field(..., description="base / safety / take_profit / stop_loss")
    side: str = Field(..., description="buy / sell")
    quantity: Optional[float] = Field(default=None, description="數量；未知為 null（G-5）")
    notional: Optional[float] = Field(default=None, description="名目金額；未知為 null")
    slot_index: Optional[int] = Field(default=None, description="階梯槽索引")
    cycle_index: int = Field(..., description="第幾輪（1-based）")
    realized_pct: Optional[float] = Field(default=None, description="本輪實現報酬%；僅 TP/SL 有值")


class BacktestEquityPoint(BaseModel):
    """回測權益點（§4.4 EquityPoint）。"""
    ts: int = Field(..., description="棒 unix 秒")
    equity: float = Field(..., description="initial_capital + realized + unrealized - fees")
    position_quantity: float = Field(..., description="持倉數量")
    average_cost: Optional[float] = Field(default=None, description="均價；無部位為 null（G-5）")
    status: str = Field(..., description="該棒結束時的 DcaStatus.value")


class BacktestCycleRecord(BaseModel):
    """回測輪次紀錄（§4.4 CycleRecord）。"""
    index: int = Field(..., description="輪次索引（1-based）")
    direction: str = Field(..., description="long / short")
    entry_ts: Optional[int] = Field(default=None, description="基礎單成交棒 unix 秒")
    exit_ts: Optional[int] = Field(default=None, description="平倉棒 unix 秒")
    entry_price: Optional[float] = Field(default=None, description="入場價")
    average_cost: Optional[float] = Field(default=None, description="平倉時均價")
    exit_price: Optional[float] = Field(default=None, description="平倉價")
    safety_filled: int = Field(default=0, description="本輪成交之安全單數")
    exit_reason: str = Field(default="running", description="take_profit / stop_loss / max_cycles / end_of_data / running")
    realized_pnl: Optional[float] = Field(default=None, description="已實現損益")
    realized_pct: Optional[float] = Field(default=None, description="已實現報酬%")
    mfe_pct: Optional[float] = Field(default=None, description="最大有利偏移%")
    mae_pct: Optional[float] = Field(default=None, description="最大不利偏移%")
    bars: int = Field(default=0, description="本輪持倉棒數")


class BacktestMetrics(BaseModel):
    """回測績效指標（§4.5）。"""
    initial_capital: Optional[float] = Field(default=None, description="初始資本")
    final_equity: Optional[float] = Field(default=None, description="期末權益")
    total_return_pct: Optional[float] = Field(default=None, description="總報酬%")
    buy_hold_return_pct: Optional[float] = Field(default=None, description="買入持有報酬%")
    alpha_pct: Optional[float] = Field(default=None, description="超額報酬%")
    realized_pnl: Optional[float] = Field(default=None, description="已實現損益")
    total_fees: Optional[float] = Field(default=None, description="總手續費")
    cycles_completed: Optional[int] = Field(default=None, description="完成輪次數")
    win_rate_pct: Optional[float] = Field(default=None, description="勝率%")
    max_drawdown_pct: Optional[float] = Field(default=None, description="最大回撤%")
    exposure_pct: Optional[float] = Field(default=None, description="持倉棒數占比%")
    avg_cycle_bars: Optional[float] = Field(default=None, description="平均每輪棒數")
    mfe_pct: Optional[float] = Field(default=None, description="平均最大有利偏移%")
    mae_pct: Optional[float] = Field(default=None, description="平均最大不利偏移%")
    profit_factor: Optional[float] = Field(default=None, description="獲利因子；分母 0 為 null（G-5）")


class BacktestResult(BaseModel):
    """回測結果（§4.4 BacktestResult）。"""
    bars: int = Field(..., description="總棒數")
    timeframe: str = Field(..., description="K 線時框")
    first_ts: Optional[int] = Field(default=None, description="第一根棒 unix 秒")
    last_ts: Optional[int] = Field(default=None, description="最後一根棒 unix 秒")
    metrics: BacktestMetrics = Field(..., description="績效指標")
    equity: List[BacktestEquityPoint] = Field(default_factory=list, description="權益曲線")
    marks: List[BacktestTradeMark] = Field(default_factory=list, description="交易標記")
    cycles: List[BacktestCycleRecord] = Field(default_factory=list, description="輪次紀錄")
    candles: List[KlineCandle] = Field(default_factory=list, description="回測區間 K 線")
    data_status: str = Field(..., description="live / paper / stale / unavailable")
    source: str = Field(..., description="cache / exchange / paper_synthetic")
    warnings: List[str] = Field(..., description="簡化事項揭露（D-11），不得為空")
    bars_per_second: float = Field(..., description="效能基準")


class BacktestRequest(BaseModel):
    """POST /api/v1/dca/backtest 請求體（§4.6 A-3）。
    
    支援兩種來源：bot_id（讀既有 YAML）或 draft（精靈 JSON）。
    """
    bot_id: Optional[str] = Field(default=None, description="既有 DCA bot_id；與 draft 互斥")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="精靈參數 JSON；與 bot_id 互斥")
    timeframe: str = Field(default="15m", description="K 線時框 (1m/5m/15m/30m/1h/4h/1d)")
    bars: int = Field(default=2000, ge=1, le=20000, description="回測棒數 (上限 20000)")
    initial_capital: Optional[str] = Field(default=None, description="初始資本（字串，支持 Decimal 格式）")
    exchange: Optional[str] = Field(default=None, description="交易所名稱")
    symbol: Optional[str] = Field(default=None, description="交易對符號")


class BacktestResponse(BaseModel):
    """POST /api/v1/dca/backtest 回應。"""
    result: BacktestResult = Field(..., description="回測結果")


class SweepRunResult(BaseModel):
    """單一參數掃描運行結果。"""
    params: Dict[str, Any] = Field(..., description="本次掃描參數")
    metrics: BacktestMetrics = Field(..., description="績效指標")
    error: Optional[str] = Field(default=None, description="錯誤訊息；若有則 metrics 為空")


class SweepBudget(BaseModel):
    """掃描預算使用狀況。

    欄位名稱與 `core.services.dca.backtest.sweep.SweepBudget` 一對一對齊，
    避免序列化層改名造成 AttributeError（C5 曾以 `runs_used` 命名而 500）。
    `runs_completed` / `runs_total` 是稽核 7.4「已完成 N/M 組」的資料來源。
    """
    bars_used: int = Field(..., description="已使用棒數")
    bars_limit: int = Field(..., description="棒數上限")
    runs_completed: int = Field(..., description="已完成運行次數")
    runs_total: int = Field(..., description="展開後的參數組合總數")
    runs_limit: Optional[int] = Field(default=None, description="運行次數上限")


class SweepBest(BaseModel):
    """已完成範圍內的最優組合（由後端判定，前端零計算 G-4）。

    稽核 7.4：`truncated=true` 時這只是「已完成範圍內」的最優，
    不得被呈現為全域最佳。value/params 在無任何可比較運行時為 null（G-5）。
    """
    metric: str = Field(..., description="排序依據的指標名稱")
    value: Optional[float] = Field(default=None, description="最優值；無可比較運行時為 null")
    params: Optional[Dict[str, Any]] = Field(default=None, description="最優參數組合；無則為 null")


class SweepResponse(BaseModel):
    """POST /api/v1/dca/backtest/sweep 回應。"""
    runs: List[SweepRunResult] = Field(default_factory=list, description="掃描運行結果")
    best_by: str = Field(default="profit_factor", description="最優排序依據")
    best: Optional[SweepBest] = Field(default=None, description="已完成範圍內的最優組合")
    truncated: bool = Field(default=False, description="是否因預算限制而截斷")
    budget: SweepBudget = Field(..., description="預算使用狀況")


class SweepRequest(BaseModel):
    """POST /api/v1/dca/backtest/sweep 請求體。"""
    bot_id: Optional[str] = Field(default=None, description="既有 DCA bot_id；與 draft 互斥")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="精靈參數 JSON；與 bot_id 互斥")
    timeframe: str = Field(default="15m", description="K 線時框")
    bars: int = Field(default=2000, ge=1, le=20000, description="回測棒數")
    initial_capital: Optional[str] = Field(default=None, description="初始資本")
    
    # 掃描參數範圍
    base_order_amount_range: Optional[List[float]] = Field(default=None, description="基礎單金額範圍")
    price_deviation_percent_range: Optional[List[float]] = Field(default=None, description="價格偏差百分比範圍")
    take_profit_percent_range: Optional[List[float]] = Field(default=None, description="止盈百分比範圍")
    max_safety_orders_range: Optional[List[int]] = Field(default=None, description="最大安全單數範圍")
    
    # 掃描控制
    max_total_bars: int = Field(default=300000, ge=1, description="總棒數預算上限")
    max_workers: int = Field(default=4, ge=1, le=8, description="並行工作數上限")
    best_by: str = Field(default="profit_factor", description="最優排序依據")
    exchange: Optional[str] = Field(default=None, description="交易所名稱")
    symbol: Optional[str] = Field(default=None, description="交易對符號")




# ────────────────────────────────────────────────────────
# 網格完整回測（P2）
# ────────────────────────────────────────────────────────

class GridBacktestRequest(BaseModel):
    """POST /api/v1/grid-backtest/run 請求體。"""
    exchange: str = Field(..., description="交易所名稱")
    symbol: str = Field(..., description="交易對符號")
    grid_type: str = Field(default="neutral", description="long / short / neutral")
    lower_price: Optional[float] = Field(default=None, description="價格下限")
    upper_price: Optional[float] = Field(default=None, description="價格上限")
    grid_count: Optional[int] = Field(default=None, ge=2, le=120, description="網格數")
    grid_interval: Optional[float] = Field(default=None, gt=0, description="網格間距（與 grid_count 二選一）")
    per_grid_notional: Optional[float] = Field(default=None, gt=0, description="每格名目金額 (USDT)")
    per_grid_capital_percent: Optional[float] = Field(default=None, gt=0, le=100, description="每格佔總資金百分比")
    initial_capital: float = Field(..., gt=0, description="初始資金 (USDT)")
    leverage: int = Field(default=10, ge=1, le=20, description="槓桿倍數")
    fee_rate: Optional[float] = Field(default=None, gt=0, description="手續費率，未填用預設 0.0002")
    maintenance_margin_rate: Optional[float] = Field(default=None, gt=0, description="維持保證金率，未填用預設 0.005")
    timeframe: str = Field(default="1h", description="K 線時框 (1m/5m/15m/30m/1h/4h/1d)")
    start_ts: Optional[int] = Field(default=None, description="開始時間 unix 秒 (UTC)")
    end_ts: Optional[int] = Field(default=None, description="結束時間 unix 秒 (UTC)")
    bars: Optional[int] = Field(default=None, ge=10, le=20000, description="回測棒數上限")
    source: str = Field(default="manual", description="manual / market_ranking / existing_yaml")
    sweep_grid_counts: Optional[List[int]] = Field(default=None, description="參數掃描網格數列表")
    sweep_per_grid_notional: Optional[List[float]] = Field(default=None, description="參數掃描每格名目金額列表")
    sweep_leverages: Optional[List[int]] = Field(default=None, description="參數掃描槓桿倍數列表")
    max_total_bars: Optional[int] = Field(default=300000, description="總棒數預算上限")
    runs_limit: Optional[int] = Field(default=500, description="最大掃描次數上限")


class GridBacktestAllocation(BaseModel):
    order_amount: float
    per_grid_notional: float
    max_fillable_cells: int
    required_capital: float
    supplied_capital: float
    insufficient_capital: bool
    message: str


class GridBacktestEquityPoint(BaseModel):
    ts: int
    equity: float
    wallet_balance: float
    position_quantity: float
    average_cost: Optional[float] = None
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0
    maintenance_margin: float = 0.0
    margin_ratio: Optional[float] = None
    liquidation_price: Optional[float] = None
    status: str = "open"


class GridBacktestFillMark(BaseModel):
    ts: int
    price: float
    side: str
    quantity: float
    notional: float
    fee: float
    kind: str
    cell_index: int = 0
    cycle_index: int = 0


class GridBacktestCycleRecord(BaseModel):
    index: int
    direction: str
    entry_ts: Optional[int] = None
    exit_ts: Optional[int] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    quantity: float = 0.0
    realized_pnl: Optional[float] = None
    realized_pct: Optional[float] = None
    bars: int = 0
    cell_index: int = 0


class GridBacktestRiskMetrics(BaseModel):
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


class GridBacktestMetrics(BaseModel):
    initial_capital: float
    final_equity: float
    total_return_pct: float
    buy_hold_return_pct: float
    alpha_pct: float
    realized_pnl: float
    total_fees: float
    cycles_completed: int
    win_rate_pct: Optional[float] = None
    max_drawdown_pct: float
    exposure_pct: float
    profit_factor: Optional[float] = None


class GridBacktestResult(BaseModel):
    spec: Dict[str, Any]
    bars: int
    timeframe: str
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    metrics: GridBacktestMetrics
    risk: GridBacktestRiskMetrics
    allocation: GridBacktestAllocation
    equity: List[GridBacktestEquityPoint] = Field(default_factory=list)
    marks: List[GridBacktestFillMark] = Field(default_factory=list)
    cycles: List[GridBacktestCycleRecord] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    data_status: str = "live"
    source: str = "exchange"
    skipped_margin_fills: int = 0
    candles: List[KlineCandle] = Field(default_factory=list)


class GridBacktestResponse(BaseModel):
    result: GridBacktestResult


class GridBacktestStressScenario(BaseModel):
    movement_pct: float
    liquidation_occurred: bool
    liquidation_ts: Optional[int] = None
    liquidation_price: Optional[float] = None
    final_equity: float
    min_equity: Optional[float] = None
    additional_capital_to_prevent_liquidation: Optional[float] = None
    max_drawdown_pct: Optional[float] = None


class GridBacktestStressResponse(BaseModel):
    start_price: float
    scenarios: List[GridBacktestStressScenario]
    warnings: List[str] = Field(default_factory=list)


class GridBacktestSweepRun(BaseModel):
    params: Dict[str, Any] = Field(..., description="本次掃描參數")
    metrics: GridBacktestMetrics
    risk: GridBacktestRiskMetrics
    error: Optional[str] = None


class GridBacktestSweepResponse(BaseModel):
    runs: List[GridBacktestSweepRun] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
