// Browser-facing requests must remain same-origin. In development Vite proxies
// these paths to the backend; deployed builds are served by FastAPI directly.
const API_BASE = '/api';

export interface ProcessStatus {
    pid: number;
    script: string;
    status: 'running' | 'stopped' | 'failed';
    start_time: string | null;
    end_time: string | null;
    config_path: string | null;
}

// ==========================================
// Grid Interfaces
// ==========================================

export interface GridInfo {
    grid_id: string;
    config_path: string;
    symbol?: string;
    exchange?: string;
    grid_type?: string;
    status: string;
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
    realized_pnl: number;
    unrealized_pnl: number;
    total_pnl: number;
    current_price: number;
    status: string;
    stale: boolean;
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
    buy: number;
    sell: number;
    total: number;
}

export interface GridStatsFilledOrders {
    buy: number;
    sell: number;
    cycles: number;
}

export interface GridStatsProfit {
    realized: number;
    unrealized: number;
    total: number;
    fees: number;
    net: number;
    rate: number;
}

export interface GridStatsBalance {
    spot: number;
    collateral: number;
    total: number;
    utilization: number;
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
    current_price: number;
    current_position: number;
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
    equity: number | null;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
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
}

export interface DashboardSnapshot {
    generated_at: number;
    data_status: DataStatus;
    aggregate: DashboardAggregate;
    grids: DashboardGridItem[];
}

export interface GridEvent { grid_id: string; event_type: string; status: string; detail: string | null; occurred_at: number; }

export interface GridHistoryPoint {
    recorded_at: number;
    current_price: number | null;
    net_pnl: number | null;
    realized_pnl: number | null;
    unrealized_pnl: number | null;
    equity: number | null;
}

export interface GridHistory { grid_id: string; range_hours: number; points: GridHistoryPoint[]; }

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

// helper to get auth token
const getAuthHeaders = (idempotencyKey?: string): HeadersInit => {
    // 假設 token 儲存在 localStorage 或可以透過環境變數傳入
    const token = localStorage.getItem('GRID_API_TOKEN');
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    return headers;
};

export const api = {
    // ------------------------------------------
    // 原有 Methods
    // ------------------------------------------
    async getScripts(): Promise<string[]> {
        const res = await fetch(`${API_BASE}/scripts`);
        return res.json();
    },

    async getConfigs(): Promise<string[]> {
        const res = await fetch(`${API_BASE}/configs`);
        return res.json();
    },

    async getConfig(path: string): Promise<string> {
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error('Failed to load config');
        return res.text();
    },

    async updateConfig(path: string, content: string): Promise<void> {
        const res = await fetch(`${API_BASE}/config/${encodeURIComponent(path)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'text/plain' },
            body: content,
        });
        if (!res.ok) throw new Error('Failed to save config');
    },

    async getProcesses(): Promise<ProcessStatus[]> {
        const res = await fetch(`${API_BASE}/processes`);
        return res.json();
    },

    async startProcess(script: string, configPath: string): Promise<{pid: number, status: string}> {
        const res = await fetch(`${API_BASE}/process/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script, config_path: configPath }),
        });
        if (!res.ok) throw new Error('Failed to start process');
        return res.json();
    },

    async stopProcess(pid: number): Promise<void> {
        const res = await fetch(`${API_BASE}/process/stop/${pid}`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error('Failed to stop process');
    },

    // ------------------------------------------
    // 新增: Grid API Methods
    // ------------------------------------------
    async getDashboardSnapshot(): Promise<DashboardSnapshot> {
        const res = await fetch(`${API_BASE}/v1/dashboard/snapshot`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error('Failed to get dashboard snapshot');
        return res.json();
    },

    async listGrids(): Promise<GridListResponse> {
        const res = await fetch(`${API_BASE}/v1/grids`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error('Failed to list grids');
        return res.json();
    },

    async getGridSummary(): Promise<GridSummary> {
        const res = await fetch(`${API_BASE}/v1/grids/summary`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error('Failed to get grids summary');
        return res.json();
    },

    async getGrid(gridId: string): Promise<GridInfo> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}`, { headers: getAuthHeaders() });
        if (!res.ok) throw new Error(`Failed to get grid ${gridId}`);
        return res.json();
    },

    async getGridHistory(gridId: string, hours = 24): Promise<GridHistory> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}/history?hours=${hours}`, { headers: getAuthHeaders() });
        if (!res.ok) throw new Error(`Failed to get history for ${gridId}`);
        return res.json();
    },

    async getGridStats(gridId: string): Promise<GridStats> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}/stats`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to get stats for grid ${gridId}`);
        return res.json();
    },

    async createGrid(request: CreateGridRequest): Promise<GridCreateResponse> {
        const res = await fetch(`${API_BASE}/v1/grids`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(request),
        });
        if (!res.ok) {
            const error = await res.json().catch(() => ({}));
            throw new Error(error.detail?.message || error.detail || 'Failed to create grid');
        }
        return res.json();
    },

    async getCommand(commandId: string): Promise<GridCommand> {
        const res = await fetch(`${API_BASE}/v1/commands/${encodeURIComponent(commandId)}`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to get command ${commandId}`);
        return res.json();
    },

    async listGridCommands(gridId: string): Promise<{ grid_id: string; commands: GridCommand[] }> {
        const res = await fetch(`${API_BASE}/v1/commands?grid_id=${encodeURIComponent(gridId)}`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to get commands for ${gridId}`);
        return res.json();
    },

    async listGridEvents(gridId: string): Promise<{ grid_id: string; events: GridEvent[] }> {
        const res = await fetch(`${API_BASE}/v1/commands/events?grid_id=${encodeURIComponent(gridId)}`, { headers: getAuthHeaders() });
        if (!res.ok) throw new Error(`Failed to get events for ${gridId}`);
        return res.json();
    },

    async startGrid(gridId: string, source: string = 'dashboard', idempotencyKey = crypto.randomUUID()): Promise<GridActionResponse> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}/start?source=${encodeURIComponent(source)}`, {
            method: 'POST',
            headers: getAuthHeaders(idempotencyKey),
        });
        if (!res.ok) throw new Error(`Failed to start grid ${gridId}`);
        return res.json();
    },

    async stopGrid(gridId: string, source: string = 'dashboard', idempotencyKey = crypto.randomUUID()): Promise<GridActionResponse> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}/stop?source=${encodeURIComponent(source)}`, {
            method: 'POST',
            headers: getAuthHeaders(idempotencyKey),
        });
        if (!res.ok) throw new Error(`Failed to stop grid ${gridId}`);
        return res.json();
    },

    async deleteGrid(gridId: string, source: string = 'dashboard'): Promise<GridActionResponse> {
        const res = await fetch(`${API_BASE}/v1/grids/${encodeURIComponent(gridId)}?source=${encodeURIComponent(source)}`, {
            method: 'DELETE',
            headers: getAuthHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to delete grid ${gridId}`);
        return res.json();
    }
};

export const getWsBase = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/logs`;
};

export const WS_BASE = getWsBase();
