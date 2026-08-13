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
    auto_start: bool = Field(default=True, description="建立後自動啟動")

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
    status: str = "unknown"  # running / stopped / unknown
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
    status: str  # created / created_and_started
    yaml_content: str
    message: str


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
    status: str  # accepted / pending_engine / running / stopped / failed
    source: str
    reason: Optional[str] = None
    detail: Optional[str] = None
    created_at: float
    updated_at: float


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
    running_grid_configs: List[str]
    max_recommended_grids: int
    can_accept_new_grid: bool


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


# ── 階段二：統計與摘要模型 ──

class GridStatsPendingOrders(BaseModel):
    buy: int = Field(default=0, description="買單數量")
    sell: int = Field(default=0, description="賣單數量")
    total: int = Field(default=0, description="總掛單數")


class GridStatsFilledOrders(BaseModel):
    buy: int = Field(default=0, description="買單成交次數")
    sell: int = Field(default=0, description="賣單成交次數")
    cycles: int = Field(default=0, description="網格循環完成次數")


class GridStatsProfit(BaseModel):
    realized: float = Field(default=0.0, description="已實現盈虧")
    unrealized: float = Field(default=0.0, description="未實現盈虧")
    total: float = Field(default=0.0, description="總盈虧")
    fees: float = Field(default=0.0, description="手續費支出")
    net: float = Field(default=0.0, description="淨盈虧（扣除手續費）")
    rate: float = Field(default=0.0, description="盈虧率 %")


class GridStatsBalance(BaseModel):
    spot: float = Field(default=0.0, description="現貨餘額")
    collateral: float = Field(default=0.0, description="合約保證金/抵押品")
    total: float = Field(default=0.0, description="帳戶總資產估值")
    utilization: float = Field(default=0.0, description="資金利用率 %")


class GridStatsTime(BaseModel):
    running_time: str = Field(default="", description="運行時間長度描述")
    last_trade: Optional[str] = Field(default=None, description="最後交易時間（ISO字串）")


class GridStatsResponse(BaseModel):
    """GET /api/v1/grids/{grid_id}/stats 回應"""
    grid_id: str
    symbol: str
    status: str = Field(default="running", description="網格狀態: running / stopped / stale")
    stale: bool = Field(default=False, description="資料是否過期（超過 180 秒未更新）")
    current_price: float = Field(default=0.0, description="當前市價")
    current_position: float = Field(default=0.0, description="當前網格持倉數量")
    average_cost: Optional[float] = Field(default=None, description="持倉均價")
    pending_orders: Optional[GridStatsPendingOrders] = None
    filled_orders: Optional[GridStatsFilledOrders] = None
    profit: Optional[GridStatsProfit] = None
    grid_utilization: Optional[float] = None
    balance: Optional[GridStatsBalance] = None
    time: Optional[GridStatsTime] = None
    last_update: Optional[float] = None


class GridSummaryItem(BaseModel):
    """全局摘要中的單一網格狀態項目"""
    grid_id: str
    symbol: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    current_price: float = 0.0
    status: str = "running"
    stale: bool = False


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
    data_status: str  # live / stale / unavailable
    last_updated_at: Optional[float] = None
    current_price: Optional[float] = None
    current_position: Optional[float] = None
    equity: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
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


class DashboardSnapshotResponse(BaseModel):
    """GET /api/v1/dashboard/snapshot 回應。"""
    generated_at: float
    data_status: str  # live / degraded / unavailable
    aggregate: DashboardAggregate
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


