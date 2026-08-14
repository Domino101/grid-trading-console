---
name: reconciler-debug
description: "Reconciler debugging and state repair for grid-trading-console. Use when investigating order/position state mismatches between local database/memory and exchange REST/WS state across Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack, O1Exchange, Paper."
---

# Reconciler Debug Skill

## When to Activate
- Investigating position or order state drift between local database and exchange
- Resolving orphaned orders or missing fill events
- Validating open positions against exchange REST endpoints

## Key Diagnostic Steps
1. **Fetch Live Exchange Positions/Orders**: Query exchange via adapter (`get_open_orders()`, `get_positions()`).
2. **Fetch Local State**: Query `data/web_ui_runtime.sqlite3` (`grid_runtime_snapshots`) and in-memory `GridCoordinator`.
3. **Compare & Reconcile**:
   - Cancel local open orders if exchange reports cancelled/filled.
   - Update local position sizes to match exchange reported balances.
   - Mark phantom orders as cancelled or rejected.
