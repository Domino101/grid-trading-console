---
name: position-lifecycle
description: "Position lifecycle management for grid-trading-console. Use when debugging position tracking, entry/exit orders, TP/SL settings, realized/unrealized PnL calculation, or position state transitions in Grid Trading and Volume Maker strategies."
---

# Position Lifecycle Skill

## When to Activate
- Debugging position opening, closing, or partial fills
- Checking TP/SL execution or grid level re-balancing
- Analyzing realized PnL, unrealized PnL, or position tracking in database/memory

## Position State Transitions

```
[No Position] → BUY / Entry Order → Status: "OPEN"
       ↓
[Open Position] → Grid Rebalance / Partial Fill → Update size & avg_entry_price
       ↓
[Open Position] → SELL / TP / SL Fill → Status: "CLOSED" (if remaining size ≤ 0)
```

## Key PnL Metrics
- **Volume-Weighted Average Entry Price**: `(sum(price_i * size_i)) / sum(size_i)`
- **Realized PnL**: `(exit_price - avg_entry_price) * exit_size - fees`
- **Unrealized PnL**: `(current_mid_price - avg_entry_price) * position_size`

## Verification & Debugging
- Canonical state lives in the relational ledger (`data/web_ui_runtime.sqlite3`): `bots`, `trade_fills`, `bot_positions`, `bot_snapshots` (or `DATABASE_URL` PostgreSQL for the ledger store). `grid_runtime_snapshots` are legacy compat tables.
- API endpoints: `/api/v1/grids`, `/api/v1/dca`, and per-bot chart endpoints.
- Verify position updates match exchange WebSocket fills; reconcile through `core/services/grid/reconciliation/` and `bot_events` append-only stream when there is drift.
