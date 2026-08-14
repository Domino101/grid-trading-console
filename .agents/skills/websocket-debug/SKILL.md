---
name: websocket-debug
description: "WebSocket connection pool debugging for grid-trading-console. Use when investigating exchange WS disconnections, orderbook data staleness, connection leaks, or reconnection storms across Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack, O1Exchange, Paper."
---

# WebSocket Debug Skill

## When to Activate
- Orderbook data appears stale or missing for tickers/symbols
- Frequent WebSocket disconnections or reconnection loops in logs
- Debugging WebSocket managers under `core/adapters/exchanges/websocket_manager.py` or exchange-specific `*_websocket.py` adapters

## Exchange WebSocket Adapters Location
`core/adapters/exchanges/adapters/`:
- `binance_websocket.py`
- `okx_websocket.py`
- `hyperliquid_websocket.py`
- `lighter_websocket.py`
- `edgex_websocket.py`
- `backpack_websocket.py`
- `websocket_manager.py` (Unified pool & reconnection logic)

## Common Issues & Solutions
1. **Heartbeat / Ping-Pong Disconnection**: Ensure ping/pong frames are handled every 15-30s.
2. **Reconnection Storms**: Verify exponential backoff delay is applied during reconnection attempts.
3. **Data Format Changes**: Verify JSON payload parsing in `_on_message()` matches exchange message schemas.
