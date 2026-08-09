---
name: position-lifecycle
description: "Position lifecycle management for crypto-trading-open. Use when debugging position tracking, entry/exit orders, TP/SL settings, realized/unrealized PnL calculation, or position state transitions in Grid Trading and Volume Maker strategies."
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
- Check positions database table or API endpoints (`web_ui/app.py`).
- Verify position updates match exchange WebSocket fills.

