-- Relational Ledger Schema for Grid & DCA Trading Bots

CREATE TABLE IF NOT EXISTS bots (
    bot_id TEXT PRIMARY KEY,
    strategy_type TEXT NOT NULL, -- 'grid' | 'dca' | 'volume'
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    account_mode TEXT NOT NULL,  -- 'live' | 'paper'
    config_path TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    archived_at REAL
);

CREATE TABLE IF NOT EXISTS trade_fills (
    fill_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    exchange_order_id TEXT,
    client_order_id TEXT NOT NULL,
    side TEXT NOT NULL,           -- 'buy' | 'sell'
    role TEXT NOT NULL,           -- 'base' | 'safety' | 'tp' | 'grid' | 'sl'
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_currency TEXT,
    realized_pnl REAL DEFAULT 0,
    executed_at REAL NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(bot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bot_positions (
    bot_id TEXT PRIMARY KEY,
    active_direction TEXT,        -- 'long' | 'short' | 'neutral'
    quantity REAL NOT NULL DEFAULT 0,
    average_cost REAL,
    total_cost REAL DEFAULT 0,
    entry_price REAL,
    completed_cycles INTEGER DEFAULT 0,
    realized_profit REAL DEFAULT 0,
    total_fees REAL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(bot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bot_snapshots (
    bot_id TEXT PRIMARY KEY,
    config_path TEXT,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    FOREIGN KEY(bot_id) REFERENCES bots(bot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bot_snapshot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    config_path TEXT,
    payload TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

-- Identity Audit (P1-6): created / config_linked / renamed / remapped
CREATE TABLE IF NOT EXISTS bot_identity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    config_path TEXT,
    old_bot_id TEXT,
    new_bot_id TEXT,
    recorded_at REAL NOT NULL
);

-- Legacy Compatibility Tables
CREATE TABLE IF NOT EXISTS grid_runtime_snapshots (
    config_path TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_runtime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_path TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

-- Optimized Indices
CREATE INDEX IF NOT EXISTS idx_trade_fills_bot_time ON trade_fills(bot_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_fills_client_order ON trade_fills(client_order_id);
CREATE INDEX IF NOT EXISTS idx_bots_config_path ON bots(config_path);
CREATE INDEX IF NOT EXISTS idx_snapshot_history_bot_time ON bot_snapshot_history(bot_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_history_path_time ON bot_snapshot_history(config_path, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_events_bot_time ON bot_identity_events(bot_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_runtime_history ON grid_runtime_history(config_path, recorded_at DESC);

-- Phase 3 (P3-1): append-only event stream (event-sourcing canonical facts).
CREATE TABLE IF NOT EXISTS bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    dedupe_key TEXT,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_bot_seq ON bot_events(bot_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_events_dedupe ON bot_events(bot_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bot_events_bot_time ON bot_events(bot_id, created_at DESC);
