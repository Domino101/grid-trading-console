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
    grid_profit: Optional[float] = Field(default=None, description="網格套利利潤（理論毛利）")
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
    """單一指標設定（僅啟用時需填具該用途欄位）。"""
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

