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



export interface GridHistoryPoint {
    recorded_at: number;
    current_price: number | null;
    net_pnl: number | null;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
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

    async startGrid(gridId: string, source: string = 'dashboard', idempotencyKey = crypto.randomUUID()): Promise<GridActionResponse> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/start?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(idempotencyKey) },
            `Failed to start grid ${gridId}`,
        );
    },

    async stopGrid(gridId: string, source: string = 'dashboard', idempotencyKey = crypto.randomUUID()): Promise<GridActionResponse> {
        return requestJson(
            `/v1/grids/${encodeURIComponent(gridId)}/stop?source=${encodeURIComponent(source)}`,
            { method: 'POST', headers: authHeaders(idempotencyKey) },
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
};

export const getWsBase = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/logs`;
};

export const WS_BASE = typeof window === 'undefined' ? '/ws/logs' : getWsBase();
