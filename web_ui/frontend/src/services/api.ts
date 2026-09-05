import { ApiError, detailMessage, getStoredToken } from '../utils/auth';

// Browser-facing requests must remain same-origin. In development Vite proxies
// these paths to the backend; deployed builds are served by FastAPI directly.
const API_BASE = '/api';

export { ApiError } from '../utils/auth';

export interface ProcessStatus {
    pid: number;
    script: string;
    status: 'running' | 'stopped' | 'failed';
    start_time: string | null;
    end_time: string | null;
    config_path: string | null;
}

export interface GridInfo {
    grid_id: string;
    config_path: string;
    symbol?: string;
    exchange?: string;
    grid_type?: string;
    status: string;
    mode?: string;
    data_status?: string;
    command_id?: string | null;
    command_status?: string | null;
    lower_price?: number;
    upper_price?: number;
    grid_interval?: number;
    order_amount?: number;
    leverage?: number;
    created_by?: string;
}

export interface GridListResponse {
    total: number;
    active: number;
    grids: GridInfo[];
}

export interface GridSummaryItem {
    grid_id: string;
    symbol: string;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
    total_pnl: number | null;
    current_price: number | null;
    status: string;
    stale: boolean;
    data_status?: string;
}

export interface GridSummary {
    total_grids: number;
    active_grids: number;
    total_realized_pnl: number;
    total_unrealized_pnl: number;
    total_pnl: number;
    grids: GridSummaryItem[];
}

export interface GridStatsPendingOrders {
    buy: number | null;
    sell: number | null;
    total: number | null;
}

export interface GridStatsFilledOrders {
    buy: number | null;
    sell: number | null;
    cycles: number | null;
}

export interface GridStatsProfit {
    grid_profit?: number | null;
    realized: number | null;
    unrealized: number | null;
    total: number | null;
    fees: number | null;
    net: number | null;
    rate: number | null;
    account_unrealized?: number | null;
}

export interface GridStatsBalance {
    spot: number | null;
    collateral: number | null;
    total: number | null;
    utilization: number | null;
}

export interface GridStatsTime {
    running_time: string;
    last_trade: string | null;
}

export interface GridStats {
    grid_id: string;
    symbol: string;
    status: string;
    stale: boolean;
    current_price: number | null;
    current_position: number | null;
    grid_position?: number | null;
    account_position?: number | null;
    average_cost: number | null;
    pending_orders: GridStatsPendingOrders | null;
    filled_orders: GridStatsFilledOrders | null;
    profit: GridStatsProfit | null;
    grid_utilization: number | null;
    balance: GridStatsBalance | null;
    time: GridStatsTime | null;
    last_update: number | null;
}

export type DataStatus = 'live' | 'stale' | 'unavailable' | 'degraded';

export interface DashboardAggregate {
    total_grids: number;
    running_grids: number;
    available_grids: number;
    stale_grids: number;
    grid_profit?: number;
    realized_pnl: number;
    unrealized_pnl: number;
    total_pnl?: number;
    fees: number;
    net_pnl: number;
    volume: number;
    completed_cycles: number;
}

export interface DashboardGridItem {
    grid_id: string;
    config_path: string;
    symbol: string | null;
    exchange: string | null;
    grid_type: string | null;
    mode: 'live' | 'paper' | 'unknown';
    status: string;
    command_id: string | null;
    command_status: string | null;
    data_status: DataStatus;
    last_updated_at: number | null;
    current_price: number | null;
    current_position: number | null;
    grid_position?: number | null;
    account_position?: number | null;
    equity: number | null;
    grid_profit?: number | null;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
    account_unrealized_pnl?: number | null;
    total_pnl?: number | null;
    fees: number | null;
    net_pnl: number | null;
    pnl_rate: number | null;
    volume: number | null;
    completed_cycles: number | null;
    filled_buy: number | null;
    filled_sell: number | null;
    pending_orders: number | null;
    lower_price: number | null;
    upper_price: number | null;
    grid_interval: number | null;
    order_amount: number | null;
    leverage: number | null;
    last_trade: string | null;
    source: string | null;
    start_blocked_reason: string | null;
    desired_running: boolean;
}

export interface DashboardSnapshot {
    generated_at: number;
    data_status: DataStatus;
    account_mode?: 'live' | 'paper' | 'mixed' | 'unknown';
    aggregate: DashboardAggregate;
    aggregate_live?: DashboardAggregate;
    aggregate_paper?: DashboardAggregate;
    grids: DashboardGridItem[];
}

export interface SystemHealth {
    status: string;
    circuit_breaker: { is_open: boolean; error_code?: string | null; until?: string | null };
    api_throttle: { total_calls: number; window_current: number; window_max: number };
    engine: { process_running: boolean; pid: number | null };
    running_grids: number;
    desired_running_grids: number;
    running_grid_configs: string[];
    max_recommended_grids: number;
    can_accept_new_grid: boolean;
    live_trading_enabled?: boolean;
}

export interface GridEvent { grid_id: string; event_type: string; status: string; detail: string | null; occurred_at: number; }

export interface MarketInfo {
    symbol: string;
    exchange: string;
    price_precision: number | null;
    quantity_precision: number | null;
    min_order_amount: number | null;
    min_notional: number | null;
    contract_size: number | null;
    cached: boolean;
}

export interface GridRecommendationPeriod {
    days: number;
    lower_price: number;
    upper_price: number;
    grid_count: number;
    grid_interval: number;
    recommended_order_amount: number;
    estimated_apr: number;
    completed_cycles: number;
    price_range_percent: number;
    period_low: number;
    period_high: number;
}

export interface GridRecommendationResponse {
    symbol: string;
    exchange: string;
    current_price: number;
    price_precision: number;
    quantity_precision: number;
    min_notional: number;
    selected_period: string;
    recommended: GridRecommendationPeriod;
    periods: Record<string, GridRecommendationPeriod>;
}

export interface MarketRankingItem {
    rank: number;
    symbol: string;
    exchange: string;
    current_price: number;
    volume_24h: number;
    price_change_24h_percent: number;
    estimated_apr: number;
    completed_cycles: number;
    lower_price: number;
    upper_price: number;
    grid_count: number;
    grid_interval: number;
    recommended_order_amount: number;
    rating: string;
}

export interface MarketRankingsResponse {
    exchange: string;
    period: string;
    updated_at: number;
    cached: boolean;
    items: MarketRankingItem[];
}

// ── 網格完整回測型別 ──

export interface GridBacktestRequest {
    symbol: string;
    exchange: string;
    grid_type: string;
    lower_price: number;
    upper_price: number;
    grid_count?: number | null;
    grid_interval?: number | null;
    per_grid_notional?: number | null;
    per_grid_capital_percent?: number | null;
    initial_capital: number;
    leverage?: number;
    fee_rate?: number | null;
    maintenance_margin_rate?: number | null;
    timeframe?: string;
    start_ts?: number | null;
    end_ts?: number | null;
    bars?: number | null;
    source?: string;
    sweep_grid_counts?: number[];
    sweep_per_grid_notional?: number[];
    sweep_leverages?: number[];
    max_total_bars?: number;
    runs_limit?: number;
}

export interface GridBacktestResult {
    spec: Record<string, unknown>;
    bars: number;
    timeframe: string;
    first_ts: number | null;
    last_ts: number | null;
    metrics: Record<string, unknown>;
    risk: Record<string, unknown>;
    equity: unknown[];
    marks: unknown[];
    cycles: unknown[];
    warnings: string[];
    data_status: string;
    source: string;
    skipped_margin_fills: number;
    candles: unknown[];
}

export interface GridBacktestResponse {
    result: GridBacktestResult;
}

export interface GridBacktestStressScenario {
    movement_pct: number;
    liquidation_occurred: boolean;
    final_equity: number;
    min_equity: number | null;
    additional_capital_to_prevent_liquidation: number | null;
}

export interface GridBacktestStressResponse {
    start_price: number;
    scenarios: GridBacktestStressScenario[];
    warnings: string[];
}

export interface GridBacktestSweepRun {
    params: Record<string, unknown>;
    metrics: Record<string, unknown>;
    risk: Record<string, unknown>;
    error: string | null;
}

export interface GridBacktestSweepResponse {
    runs: GridBacktestSweepRun[];
    budget: Record<string, unknown>;
    warnings: string[];
}



export interface GridHistoryPoint {
    recorded_at: number;
    current_price: number | null;
    total_pnl?: number | null;
    net_pnl: number | null;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
    fees?: number | null;
    equity: number | null;
}

export interface GridHistory { grid_id: string; range_hours: number; points: GridHistoryPoint[]; }

export interface GridOrderRow {
    side?: string;
    price?: number | null;
    amount?: number | null;
    grid_id?: string | number | null;
    status?: string;
}

export interface GridOrdersResponse {
    grid_id: string;
    available: boolean;
    orders: GridOrderRow[];
    message: string | null;
}

export interface GridFillRow {
    side?: string;
    price?: number | null;
    amount?: number | null;
    fee?: number | null;
    time?: string | number | null;
    profit?: number | null;
}

export interface GridFillsResponse {
    grid_id: string;
    available: boolean;
    fills: GridFillRow[];
    message: string | null;
}

export interface CreateGridRequest {
    symbol: string;
    exchange: string;
    grid_type: string;
    grid_interval: number;
    order_amount: number;
    lower_price?: number;
    upper_price?: number;
    quantity_precision?: number;
    price_decimals?: number;
    leverage?: number;
    margin_mode?: string;
    account_mode?: 'paper' | 'live';
    limit_only?: boolean;
    active_window_each_side?: number;
    active_window_refresh_interval?: number;
    order_health_check_enabled?: boolean;
    order_health_check_interval?: number;
    health_check_adjust_position_enabled?: boolean;
    rest_position_query_interval?: number;
    fee_rate?: number;
    reverse_order_grid_distance?: number;
    ws_only_mode?: boolean;
    rest_backup_enabled?: boolean;
    follow_grid_count?: number;
    follow_timeout?: number;
    follow_distance?: number;
    price_offset_grids?: number;
    scalping_enabled?: boolean;
    scalping_trigger_percent?: number;
    scalping_take_profit_grids?: number;
    smart_scalping_enabled?: boolean;
    allowed_deep_drops?: number;
    min_drop_threshold_percent?: number;
    capital_protection_enabled?: boolean;
    capital_protection_trigger_percent?: number;
    take_profit_enabled?: boolean;
    take_profit_percentage?: number;
    price_lock_enabled?: boolean;
    price_lock_threshold?: number;
    price_lock_start_at_threshold?: boolean;
    stop_loss_protection_enabled?: boolean;
    stop_loss_trigger_percent?: number;
    stop_loss_escape_timeout?: number;
    stop_loss_apr_threshold?: number;
    martingale_increment?: number;
    position_tolerance?: Record<string, number>;
    auto_start?: boolean;
    metadata?: {
        source?: string;
        reason?: string;
        confidence?: number;
    };
}

export interface GridCreateResponse {
    grid_id: string;
    config_path: string;
    status: string;
    yaml_content: string;
    message: string;
    command_id?: string | null;
}

export interface GridActionResponse {
    grid_id: string;
    action: string;
    status: string;
    message: string;
    command_id?: string | null;
}

export interface GridCommand {
    command_id: string;
    grid_id: string;
    config_path: string;
    action: string;
    status: 'accepted' | 'pending_engine' | 'running' | 'stopped' | 'failed' | 'timed_out';
    source: string;
    reason: string | null;
    detail: string | null;
    created_at: number;
    updated_at: number;
}

// ── DCA 機器人（Phase 4 後端契約 → Phase 5 分頁資料來源）──────────────
// 未知值一律 null（禁止 0 代替，G-5）；所有金額顯示走 utils/format.ts。

export interface DcaIndicatorView {
    type?: string;
    period?: number | null;
    oversold?: number | null;
    overbought?: number | null;
    fast?: number | null;
    slow?: number | null;
    signal?: number | null;
    std_dev?: number | null;
    lookback_bars?: number | null;
    long_threshold_percent?: number | null;
    short_threshold_percent?: number | null;
    condition?: string | null;
    // 執行期即時值（快照讀模型附加）
    current_value?: number | null;
    triggered?: boolean | null;
    triggered_long?: boolean | null;
    triggered_short?: boolean | null;
    insufficient_data?: boolean | null;
    warmup_have?: number | null;
    warmup_need?: number | null;
    details?: Record<string, number | null> | null;
}

export interface DcaPosition {
    quantity: number | null;
    average_cost: number | null;
    total_cost: number | null;
}

export interface DcaLadderState {
    max_safety_orders: number | null;
    filled_safety_orders: number | null;
    active_safety_orders: number | null;
    take_profit_orders: number | null;
}

export interface DcaProfit {
    realized: number | null;
    unrealized: number | null;
    fees: number | null;
}

export interface DcaSnapshotItem {
    bot_id: string;
    config_path: string;
    symbol: string | null;
    exchange: string | null;
    direction: 'long' | 'short' | 'both' | (string & {}) | null;
    mode: 'live' | 'paper' | 'unknown' | (string & {});
    status: string;
    desired_running: boolean;
    command_id: string | null;
    command_status: string | null;
    data_status: DataStatus;
    source: string | null;
    last_updated_at: number | null;
    current_price: number | null;
    active_direction: 'long' | 'short' | null;
    position: DcaPosition | null;
    ladder: DcaLadderState | null;
    cycles: number | null;
    total_trades?: number | null;
    profit: DcaProfit | null;
    base_order_amount: number | null;
    max_safety_orders: number | null;
    take_profit_percent: number | null;
    stop_loss_percent: number | null;
    // Phase 5 卡片欄位（入場設定與執行期導出值）
    entry_mode: 'immediate' | 'indicator' | (string & {}) | null;
    entry_logic: 'all' | 'any' | (string & {}) | null;
    entry_timeframe: string | null;
    entry_indicators: DcaIndicatorView[];
    entry_price: number | null;
    next_safety_order_price: number | null;
    take_profit_price: number | null;
    stop_loss_price: number | null;
    armed_since: number | null;
    start_blocked_reason: string | null;
}

export interface DcaSnapshotAggregate {
    total_bots: number;
    running_bots: number;
    available_bots: number;
    stale_bots: number;
    realized_pnl: number;
    unrealized_pnl: number;
    fees: number;
    net_pnl: number;
    filled_safety_orders: number;
    completed_cycles: number;
}

export interface DcaSnapshotResponse {
    generated_at: number;
    data_status: DataStatus;
    account_mode: 'live' | 'paper' | 'mixed' | 'unknown';
    aggregate: DcaSnapshotAggregate;
    aggregate_live: DcaSnapshotAggregate | null;
    aggregate_paper: DcaSnapshotAggregate | null;
    bots: DcaSnapshotItem[];
}

export interface DcaActionResponse {
    bot_id: string;
    action: string;
    status: string;
    message: string;
    command_id: string | null;
}

// ── DCA 詳情工作台（Phase 7）契約 ────────────────────────────────
// 全部走同源 /api/v1/dca/*；未知一律 null（G-5），不以 0 代替。

export interface DcaInfoResponse {
    bot_id: string;
    config_path: string;
    symbol: string | null;
    exchange: string | null;
    direction: string | null;
    status: string;
    mode: string | null;
    data_status: DataStatus;
    command_id: string | null;
    command_status: string | null;
    current_price: number | null;
    avg_price: number | null;
    position_quantity: number | null;
    filled_safety_orders: number | null;
    active_safety_orders: number | null;
    cycle_count: number | null;
    unrealized_pnl: number | null;
    base_order_amount: number | null;
    max_safety_orders: number | null;
    take_profit_percent: number | null;
}

export interface DcaStatsResponse {
    bot_id: string;
    status: string;
    data_status: DataStatus;
    last_updated_at: number | null;
    current_price: number | null;
    active_direction: string | null;
    position: DcaPosition | null;
    ladder: DcaLadderState | null;
    cycles: number | null;
    total_trades?: number | null;
    profit: DcaProfit | null;
}

export interface DcaLevelView {
    index: number;
    role: 'base' | 'safety' | (string & {});
    status: 'filled' | 'active' | 'waiting' | (string & {});
    planned_price: number | null;
    planned_quantity: number | null;
    actual_price: number | null;
    filled_price: number | null;
    filled_quantity: number | null;
}

export interface DcaLadderResponse {
    bot_id: string;
    available: boolean;
    data_status: DataStatus | (string & {});
    levels: DcaLevelView[];
    message: string | null;
}

export interface DcaOrdersResponse {
    bot_id: string;
    available: boolean;
    orders: Array<Record<string, unknown>>;
    message: string | null;
}

export interface DcaFillsResponse {
    bot_id: string;
    available: boolean;
    fills: Array<Record<string, unknown>>;
    message: string | null;
}

export interface DcaEvent {
    bot_id: string;
    event_type: string;
    status: string;
    detail: string | null;
    occurred_at: number;
}

export interface DcaEventListResponse {
    bot_id: string;
    events: DcaEvent[];
}

export interface DcaPreviewLevel {
    index: number;
    deviation_percent: number | null;
    price: number;
    notional: number;
    quantity: number;
    avg_price: number;
    take_profit_price: number | null;
}

export interface DcaPreviewLadder {
    direction: string;
    entry_price: number;
    required_capital: number;
    max_drawdown_percent: number;
    final_average_price: number;
    final_take_profit_price: number;
    levels: DcaPreviewLevel[];
}

export interface DcaPreviewResponse {
    direction: string | null;
    required_capital: number;
    ladder: DcaPreviewLadder | null;
    ladder_long: DcaPreviewLadder | null;
    ladder_short: DcaPreviewLadder | null;
    data_status: string;
}

export interface DcaIndicatorRequest {
    type: 'rsi' | 'macd' | 'bollinger' | 'price_change';
    period?: number | null;
    oversold?: number | null;
    overbought?: number | null;
    fast?: number | null;
    slow?: number | null;
    signal?: number | null;
    std_dev?: number | null;
    lookback_bars?: number | null;
    long_threshold_percent?: number | null;
    short_threshold_percent?: number | null;
    condition?: string | null;
}

export interface DcaEntryRequest {
    mode?: 'immediate' | 'indicator';
    logic?: 'all' | 'any';
    timeframe?: string;
    fetch_limit?: number;
    price_source?: 'close' | 'hlc3' | 'ohlc4';
    confirm_bars?: number;
    max_wait_seconds?: number | null;
    indicators?: DcaIndicatorRequest[];
}

export interface DcaPreviewRequest {
    symbol: string;
    exchange: string;
    direction: 'long' | 'short' | 'both';
    entry_price: number;
    base_order_amount: number;
    amount_unit?: 'quote' | 'base';
    dca_mode?: 'order' | 'position';
    averaging_method?: 'fixed_value' | 'fixed_coin';
    max_safety_orders?: number;
    price_deviation_percent?: number;
    price_deviation_multiplier?: number;
    order_size_multiplier?: number;
    take_profit_percent?: number;
    take_profit_order_type?: 'limit' | 'market';
    stop_loss_percent?: number | null;
    max_position_notional?: number | null;
    fee_rate?: number | null;
    leverage?: number;
    margin_mode?: 'cross' | 'isolated';
    quantity_precision?: number | null;
    price_decimals?: number | null;
    repeat_after_take_profit?: boolean;
    cooldown_seconds?: number;
    max_cycles?: number | null;
    both_tiebreak?: 'long_first' | 'short_first';
    active_safety_orders_window?: number;
    monitoring_interval?: number;
    indicator_refresh_interval?: number;
    order_health_check_interval?: number;
    entry?: DcaEntryRequest;
}

export interface DcaCreateRequest extends DcaPreviewRequest {
    account_mode: 'paper' | 'live';
    auto_start?: boolean;
    quantity_precision?: number | null;
    price_decimals?: number | null;
}

export interface DcaCreateResponse {
    bot_id: string;
    config_path: string;
    status: string; // created / pending_engine
    yaml_content: string;
    message: string;
    command_id: string | null;
}

// ── Phase C6：K 線與 DCA 即時圖表契約 ────────────────────────────────
// 對齊後端 web_ui/backend/schemas.py 的 KlineCandle / DcaChartResponse。
// 未知一律 null（G-5）；前端不做任何計算（G-4）。

export interface KlineCandle {
    t: number; // 棒起點 unix 秒 (UTC)
    o: number;
    h: number;
    l: number;
    c: number;
    v: number;
}

export interface KlinesResponse {
    symbol: string;
    exchange: string;
    timeframe: string;
    candles: KlineCandle[];
    source: string; // cache / exchange / paper_synthetic / cache+exchange
    data_status: DataStatus | (string & {});
    gaps: number;
    warnings: string[];
    oldest_ts: number | null;
}

/** §4.7 凍結標記語意；kind 由後端決定，前端只做視覺映射。 */
export type DcaChartMarkKind =
    | 'order_placed'
    | 'base_filled'
    | 'safety_filled'
    | 'take_profit_filled'
    | 'stop_loss_filled'
    | 'skipped'
    | (string & {});

export interface DcaChartMark {
    ts: number;
    price: number;
    kind: DcaChartMarkKind;
    role: string;
    side: string;
    quantity: number | null;
    notional: number | null;
    slot_index: number | null;
    cycle_index: number;
    realized_pct: number | null;
}

export type DcaChartLineKind =
    | 'entry'
    | 'average_cost'
    | 'take_profit'
    | 'next_safety'
    | 'stop_loss'
    | (string & {});

export interface DcaChartLine {
    kind: DcaChartLineKind;
    price: number | null; // 未知為 null，前端不得畫 0（稽核 6.3）
}

export interface DcaChartResponse {
    bot_id: string;
    candles: KlineCandle[];
    marks: DcaChartMark[];
    lines: DcaChartLine[];
    current_price: number | null;
    data_status: DataStatus | (string & {});
    source: string;
    warnings: string[];
}

// ── Phase C7：回測與參數掃描契約 ──────────────────────────────────────
// 對齊後端 web_ui/backend/schemas.py 的 BacktestRequest/Response 與 Sweep*。
// 全部數字直讀 API（G-4）；未知一律 null，前端不得以 0 代替（G-5）。

export type DcaBacktestTimeframe = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d';

export interface BacktestTradeMark {
    ts: number;
    price: number;
    kind: DcaChartMarkKind;
    role: string;
    side: string;
    quantity: number | null;
    notional: number | null;
    slot_index: number | null;
    cycle_index: number;
    realized_pct: number | null;
}

export interface BacktestEquityPoint {
    ts: number;
    equity: number;
    position_quantity: number;
    average_cost: number | null;
    status: string;
}

export interface BacktestCycleRecord {
    index: number;
    direction: string;
    entry_ts: number | null;
    exit_ts: number | null;
    entry_price: number | null;
    average_cost: number | null;
    exit_price: number | null;
    safety_filled: number;
    exit_reason: string;
    realized_pnl: number | null;
    realized_pct: number | null;
    mfe_pct: number | null;
    mae_pct: number | null;
    bars: number;
}

/** §4.5 績效指標。每一欄都可能是 null（G-5），顯示層必須以「—」呈現。 */
export interface BacktestMetrics {
    initial_capital: number | null;
    final_equity: number | null;
    total_return_pct: number | null;
    buy_hold_return_pct: number | null;
    alpha_pct: number | null;
    realized_pnl: number | null;
    total_fees: number | null;
    cycles_completed: number | null;
    win_rate_pct: number | null;
    max_drawdown_pct: number | null;
    exposure_pct: number | null;
    avg_cycle_bars: number | null;
    mfe_pct: number | null;
    mae_pct: number | null;
    profit_factor: number | null;
}

export interface BacktestResult {
    bars: number;
    timeframe: string;
    first_ts: number | null;
    last_ts: number | null;
    metrics: BacktestMetrics;
    equity: BacktestEquityPoint[];
    marks: BacktestTradeMark[];
    cycles: BacktestCycleRecord[];
    candles?: KlineCandle[];
    data_status: DataStatus | (string & {});
    source: string;
    /** D-11 簡化事項揭露；後端保證非空，UI 必須全部渲染（稽核 7.2）。 */
    warnings: string[];
    bars_per_second: number;
}

export interface BacktestResponse {
    result: BacktestResult;
}

/** `bot_id` 與 `draft` 互斥；draft 走與建立機器人同一套驗證（A-3 / 稽核 7.9）。 */
export interface BacktestRequest {
    bot_id?: string;
    draft?: Record<string, unknown>;
    timeframe?: DcaBacktestTimeframe;
    bars?: number;
    initial_capital?: string;
    exchange?: string;
    symbol?: string;
}

export interface SweepRunResult {
    params: Record<string, unknown>;
    metrics: BacktestMetrics;
    error: string | null;
}

export interface SweepBudget {
    bars_used: number;
    bars_limit: number;
    runs_completed: number;
    runs_total: number;
    runs_limit: number | null;
}

/** 最優組合由後端判定（G-4）；truncated 時僅為「已完成範圍內」最優（稽核 7.4）。 */
export interface SweepBest {
    metric: string;
    value: number | null;
    params: Record<string, unknown> | null;
}

export interface SweepResponse {
    runs: SweepRunResult[];
    best_by: string;
    best: SweepBest | null;
    truncated: boolean;
    budget: SweepBudget;
}

export interface SweepRequest {
    bot_id?: string;
    draft?: Record<string, unknown>;
    timeframe?: DcaBacktestTimeframe;
    bars?: number;
    initial_capital?: string;
    base_order_amount_range?: number[];
    price_deviation_percent_range?: number[];
    take_profit_percent_range?: number[];
    max_safety_orders_range?: number[];
    max_total_bars?: number;
    max_workers?: number;
    best_by?: string;
}

export interface MarketIndicatorResult {
    value: number | null;
    triggered_long: boolean | null;
    triggered_short: boolean | null;
    insufficient_data: boolean;
    warmup_have: number | null;
    warmup_need: number | null;
    as_of: string | null;
    details: Record<string, unknown> | null;
}

export interface MarketIndicatorsResponse {
    symbol: string;
    exchange: string;
    timeframe: string;
    fetch_limit: number;
    current_price: number | null;
    rsi: MarketIndicatorResult | null;
    macd: MarketIndicatorResult | null;
    bollinger: MarketIndicatorResult | null;
    price_change: MarketIndicatorResult | null;
    data_status: string;
    cached: boolean;
}

export const generateUUID = (): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // Fallback for non-secure contexts (e.g. HTTP access by IP)
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
};

const authHeaders = (idempotencyKey?: string): Headers => {
    const headers = new Headers();
    headers.set('Content-Type', 'application/json');
    const token = getStoredToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
    return headers;
};

const parseBody = async (res: Response): Promise<unknown> => {
    const text = await res.text();
    if (!text) return {};
    try {
        return JSON.parse(text);
    } catch {
        return {};
    }
};

const requestJson = async <T>(path: string, init: RequestInit = {}, fallback: string): Promise<T> => {
    const headers = new Headers(init.headers);
    const token = getStoredToken();
    if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
    const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
    const body = await parseBody(res);
    if (res.status === 401 || res.status === 403) {
        throw new ApiError(detailMessage(body, '未授權'), res.status);
    }
    if (!res.ok) {
        throw new ApiError(detailMessage(body, fallback), res.status);
    }
    return body as T;
};

export const api = {
    async getScripts(): Promise<string[]> {
        return requestJson('/scripts', {}, 'Failed to list scripts');
    },

    async getConfigs(): Promise<string[]> {
        return requestJson('/configs', {}, 'Failed to list configs');
    },

    async getConfig(path: string): Promise<string> {
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`, { headers: authHeaders() });
        if (res.status === 401 || res.status === 403) throw new ApiError('未授權', res.status);
        if (!res.ok) throw new ApiError('Failed to load config', res.status);
        return res.text();
    },

    async updateConfig(path: string, content: string): Promise<void> {
        const headers = authHeaders();
        headers.set('Content-Type', 'text/plain');
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`, {
            method: 'PUT',
            headers,
            body: content,
        });
        if (res.status === 401 || res.status === 403) throw new ApiError('未授權', res.status);
        if (!res.ok) throw new ApiError('Failed to save config', res.status);
    },


    async getProcesses(): Promise<ProcessStatus[]> {
        return requestJson('/processes', {}, 'Failed to list processes');
    },

    async startProcess(script: string, configPath: string): Promise<{pid: number, status: string}> {
        return requestJson('/process/start', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ script, config_path: configPath }),
        }, 'Failed to start process');
    },

    async stopProcess(pid: number): Promise<void> {
        await requestJson(`/process/stop/${pid}`, { method: 'POST', headers: authHeaders() }, 'Failed to stop process');
    },

    async getDashboardSnapshot(signal?: AbortSignal): Promise<DashboardSnapshot> {
        return requestJson('/v1/dashboard/snapshot', { headers: authHeaders(), signal }, '無法讀取網格總覽');
    },

    async getSystemHealth(signal?: AbortSignal): Promise<SystemHealth> {
        return requestJson('/v1/system/health', { headers: authHeaders(), signal }, '無法讀取系統健康狀態');
    },

    async listGrids(): Promise<GridListResponse> {
        return requestJson('/v1/grids', { headers: authHeaders() }, 'Failed to list grids');
    },

    async getGridSummary(): Promise<GridSummary> {
        return requestJson('/v1/grids/summary', { headers: authHeaders() }, 'Failed to get grids summary');
    },

    async getMarketInfo(symbol: string, exchange: string): Promise<MarketInfo> {
        return requestJson(
            `/v1/market/info/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`,
            { headers: authHeaders() },
            '無法讀取交易對規則',
        );
    },

    async getGridRecommendation(symbol: string, exchange: string, days = 7): Promise<GridRecommendationResponse> {
        return requestJson(
            `/v1/market/recommendation/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}&days=${days}`,
            { headers: authHeaders() },
            '無法讀取 AI 網格推薦參數與回測數據',
        );
    },

    async getMarketRankings(exchange = 'binance', period = '7d', limit = 20): Promise<MarketRankingsResponse> {
        return requestJson(
            `/v1/market/rankings?exchange=${encodeURIComponent(exchange)}&period=${encodeURIComponent(period)}&limit=${limit}`,
            { headers: authHeaders() },
            '無法讀取市場年化排行榜',
        );
    },



    async getGrid(gridId: string, signal?: AbortSignal): Promise<GridInfo> {
        return requestJson(`/v1/grids/${encodeURIComponent(gridId)}`, { headers: authHeaders(), signal }, `Failed to get grid ${gridId}`);
    },

    async getGridHistory(gridId: string, hours = 24, signal?: AbortSignal): Promise<GridHistory> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/history?hours=${hours}`,
            { headers: authHeaders(), signal },
            `Failed to get history for ${gridId}`,
        );
    },

    async getGridOrders(gridId: string, signal?: AbortSignal): Promise<GridOrdersResponse> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/orders`,
            { headers: authHeaders(), signal },
            `Failed to get orders for ${gridId}`,
        );
    },

    async getGridFills(gridId: string, signal?: AbortSignal): Promise<GridFillsResponse> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/fills`,
            { headers: authHeaders(), signal },
            `Failed to get fills for ${gridId}`,
        );
    },

    async getGridStats(gridId: string, signal?: AbortSignal): Promise<GridStats> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/stats`,
            { headers: authHeaders(), signal },
            `Failed to get stats for grid ${gridId}`,
        );
    },

    async createGrid(request: CreateGridRequest): Promise<GridCreateResponse> {
        return requestJson('/v1/grids', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
        }, 'Failed to create grid');
    },

    async getCommand(commandId: string): Promise<GridCommand> {
        return requestJson(`/v1/commands/${encodeURIComponent(commandId)}`, { headers: authHeaders() }, `Failed to get command ${commandId}`);
    },

    async listGridCommands(gridId: string, signal?: AbortSignal): Promise<{ grid_id: string; commands: GridCommand[] }> {
        return requestJson(
            `/v1/commands?grid_id=${encodeURIComponent(gridId)}`,
            { headers: authHeaders(), signal },
            `Failed to get commands for ${gridId}`,
        );
    },

    async listGridEvents(gridId: string, signal?: AbortSignal): Promise<{ grid_id: string; events: GridEvent[] }> {
        return requestJson(
            `/v1/commands/events?grid_id=${encodeURIComponent(gridId)}`,
            { headers: authHeaders(), signal },
            `Failed to get events for ${gridId}`,
        );
    },

    async startGrid(gridId: string, source: string = 'dashboard', idempotencyKey?: string): Promise<GridActionResponse> {
        const key = idempotencyKey || generateUUID();
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/start?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(key) },
            `Failed to start grid ${gridId}`,
        );
    },

    async stopGrid(gridId: string, source: string = 'dashboard', idempotencyKey?: string): Promise<GridActionResponse> {
        const key = idempotencyKey || generateUUID();
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/stop?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(key) },
            `Failed to stop grid ${gridId}`,
        );
    },

    async deleteGrid(gridId: string, source: string = 'dashboard', force: boolean = true): Promise<GridActionResponse> {
        const forceParam = force ? '&force=true' : '';
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}?source=${encodeURIComponent(source)}${forceParam}`,
            { method: 'DELETE', headers: authHeaders() },
            `Failed to delete grid ${gridId}`,
        );
    },

    // ── DCA 機器人（Phase 5；同源相對 URL，禁止 localhost/127.0.0.1）──

    async getDcaSnapshot(signal?: AbortSignal): Promise<DcaSnapshotResponse> {
        return requestJson('/v1/dca/snapshot', { headers: authHeaders(), signal }, '無法讀取 DCA 總覽');
    },

    // ── DCA 詳情工作台（Phase 7）：階梯 / 訂單 / 成交 / 事件 / 設定 ──

    async getDcaBot(botId: string, signal?: AbortSignal): Promise<DcaInfoResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId}`);
    },

    async getDcaStats(botId: string, signal?: AbortSignal): Promise<DcaStatsResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/stats`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 統計`);
    },

    async getDcaLadder(botId: string, signal?: AbortSignal): Promise<DcaLadderResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/ladder`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 階梯`);
    },

    async getDcaOrders(botId: string, signal?: AbortSignal): Promise<DcaOrdersResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/orders`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 掛單`);
    },

    async getDcaFills(botId: string, signal?: AbortSignal): Promise<DcaFillsResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/fills`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 成交`);
    },

    async listDcaEvents(botId: string, signal?: AbortSignal): Promise<DcaEventListResponse> {
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/events`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 事件`);
    },

    async startDca(botId: string, source: string = 'dashboard', idempotencyKey?: string): Promise<DcaActionResponse> {
        const key = idempotencyKey || generateUUID();
        return requestJson(
            `/v1/dca/${encodeURIComponent(botId)}/start?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(key) },
            `無法啟動 DCA ${botId}`,
        );
    },

    async stopDca(botId: string, source: string = 'dashboard', idempotencyKey?: string): Promise<DcaActionResponse> {
        const key = idempotencyKey || generateUUID();
        return requestJson(
            `/v1/dca/${encodeURIComponent(botId)}/stop?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(key) },
            `無法停止 DCA ${botId}`,
        );
    },

    async deleteDca(botId: string, source: string = 'dashboard', force: boolean = true): Promise<DcaActionResponse> {
        const forceParam = force ? '&force=true' : '';
        return requestJson(
            `/v1/dca/${encodeURIComponent(botId)}?source=${encodeURIComponent(source)}${forceParam}`,
            { method: 'DELETE', headers: authHeaders() },
            `無法刪除 DCA ${botId}`,
        );
    },

    async previewDca(request: DcaPreviewRequest, signal?: AbortSignal): Promise<DcaPreviewResponse> {
        return requestJson('/v1/dca/preview', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法試算 DCA 階梯');
    },

    async createDca(request: DcaCreateRequest): Promise<DcaCreateResponse> {
        return requestJson('/v1/dca', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
        }, '無法建立 DCA 機器人');
    },

    // ── Phase C6：K 線與 DCA 即時圖表（同源相對 URL）──

    async getKlines(
        symbol: string,
        exchange: string,
        timeframe = '15m',
        limit = 300,
        signal?: AbortSignal,
    ): Promise<KlinesResponse> {
        const qs = `?exchange=${encodeURIComponent(exchange)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`;
        return requestJson(`/v1/market/klines/${encodeURIComponent(symbol)}${qs}`, { headers: authHeaders(), signal }, '無法讀取 K 線資料');
    },

    async getDcaChart(
        botId: string,
        timeframe = '15m',
        limit = 300,
        signal?: AbortSignal,
    ): Promise<DcaChartResponse> {
        const qs = `?timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`;
        return requestJson(`/v1/dca/${encodeURIComponent(botId)}/chart${qs}`, { headers: authHeaders(), signal }, `無法讀取 DCA ${botId} 圖表`);
    },

    // ── Phase C7：回測與參數掃描（同源相對 URL；長任務務必傳 signal 以支援取消）──

    async runDcaBacktest(request: BacktestRequest, signal?: AbortSignal): Promise<BacktestResponse> {
        return requestJson('/v1/dca/backtest', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法執行回測');
    },

    async runDcaSweep(request: SweepRequest, signal?: AbortSignal): Promise<SweepResponse> {
        return requestJson('/v1/dca/backtest/sweep', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法執行參數掃描');
    },

    async getMarketIndicators(
        symbol: string,
        exchange: string,
        timeframe = '15m',
        limit = 300,
        signal?: AbortSignal,
    ): Promise<MarketIndicatorsResponse> {
        const qs = `?exchange=${encodeURIComponent(exchange)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`;
        return requestJson(`/v1/market/indicators/${encodeURIComponent(symbol)}${qs}`, { headers: authHeaders(), signal }, '無法讀取指標即時值');
    },

    // ── 網格完整回測（P3）──
    async runGridBacktest(request: GridBacktestRequest, signal?: AbortSignal): Promise<GridBacktestResponse> {
        return requestJson('/v1/grid-backtest/run', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法執行網格回測');
    },

    async runGridStress(request: GridBacktestRequest, signal?: AbortSignal): Promise<GridBacktestStressResponse> {
        return requestJson('/v1/grid-backtest/stress', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法執行壓力情境');
    },

    async runGridSweep(request: GridBacktestRequest, signal?: AbortSignal): Promise<GridBacktestSweepResponse> {
        return requestJson('/v1/grid-backtest/sweep', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(request),
            signal,
        }, '無法執行參數掃描');
    },
};

export const getWsBase = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/logs`;
};

export const WS_BASE = typeof window === 'undefined' ? '/ws/logs' : getWsBase();
