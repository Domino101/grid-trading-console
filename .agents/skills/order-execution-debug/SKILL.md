---
name: order-execution-debug
description: "Order execution full-chain diagnostics for crypto-trading-open. Use when debugging order placement, fill simulation, slippage, maker/taker order routing, or exchange-rejected orders across Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack."
---

# Order Execution Debug Skill

## When to Activate
- Orders not filling, being rejected, or cancelled unexpectedly
- Debugging order placement in Grid Trading or Volume Maker services
- Investigating exchange min notional errors, price tick precision, or quantity precision
- Testing exchange adapter order methods in `core/adapters/exchanges/`

## Execution Pipeline Architecture

```
Trading Service (Grid / Volume Maker) → Risk / Boundary Check
    → Exchange Adapter (core/adapters/exchanges/adapters/*.py)
        → REST / WebSocket API Call → Exchange Matching Engine
```

## Key Modules

| Module | Location | Purpose |
|---|---|---|
| **Exchange Factory** | `core/adapters/exchanges/factory.py` | Instantiates appropriate exchange adapter |
| **Adapter Interface** | `core/adapters/exchanges/interface.py` | Standardized methods (`create_order`, `cancel_order`, `get_open_orders`, etc.) |
| **Exchange Adapters** | `core/adapters/exchanges/adapters/` | Implementations for Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack |
| **WebSocket Manager** | `core/adapters/exchanges/websocket_manager.py` | Connection pool & BBO orderbook streams |

## Common Execution Issues & Solutions

| Issue | Symptom | Cause | Fix |
|---|---|---|---|
| **Min Notional Error** | `Filter failure: MIN_NOTIONAL` | Order total (`price * amount`) < exchange minimum | Check `test_min_notional_fix.py` and enforce min order value |
| **Invalid Quantity Precision** | `LOT_SIZE` error | Quantity decimal places exceed exchange tick size | Round quantity using exchange `stepSize` |
| **Invalid Price Precision** | `PRICE_FILTER` error | Price decimal places exceed exchange `tickSize` | Round price using exchange `tickSize` |
| **Rate Limit / Ban** | HTTP `418` / `429` | Request frequency too high | Increase poll interval or enable WS feed |

