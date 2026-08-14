---
name: api-diagnostics
description: "API connectivity diagnostics for grid-trading-console. Use when debugging exchange API connection issues (Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack, O1Exchange, Paper), testing REST/WS endpoint reachability, verifying API key authentication, checking rate limits/IP bans, or investigating response format changes."
---

# API Diagnostics Skill

## When to Activate
- Bot reports exchange REST or WebSocket API connection failures
- Investigating Binance IP ban, rate limit HTTP 429, or connection timeouts
- Setting up a new trading environment or validating API keys in `.env`
- Testing REST/WS reachability for Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack, O1Exchange, Paper

## Exchange API Endpoints & Adapters

| Exchange | Adapter Path | REST Base / WS Endpoint | Key Diagnostic Functions |
|---|---|---|---|
| **Binance** | `core/adapters/exchanges/adapters/binance.py` | `https://api.binance.com`, `wss://stream.binance.com:9443` | Ping, Ticker, Depth, Kline |
| **OKX** | `core/adapters/exchanges/adapters/okx.py` | `https://www.okx.com`, `wss://ws.okx.com:8443/ws/v5/public` | Market Tickers, Orderbook |
| **Hyperliquid** | `core/adapters/exchanges/adapters/hyperliquid.py` | `https://api.hyperliquid.xyz`, `wss://api.hyperliquid.xyz/ws` | Meta, AllMids, L2Book |
| **Lighter** | `core/adapters/exchanges/adapters/lighter.py` | `https://api.lighter.xyz` | Orderbook, Balance |
| **EdgeX** | `core/adapters/exchanges/adapters/edgex.py` | `https://api.edgex.exchange` | Tickers, Orderbook |
| **Backpack** | `core/adapters/exchanges/adapters/backpack.py` | `https://api.backpack.exchange` | Tickers, Depth |
| **O1Exchange** | `core/adapters/exchanges/adapters/o1exchange.py` | Configured O1 REST/WS endpoint | Tickers, Depth |
| **Paper** | `core/adapters/exchanges/adapters/paper.py` | Simulated in-memory adapter | Simulation testing |

## Diagnostic Procedures

### 1. Binance IP Ban & Connectivity Check
```bash
# Test basic reachability & IP ban status
curl -s -w "\nHTTP: %{http_code}\n" "https://api.binance.com/api/v3/ping"

# Check server time and latency
curl -s "https://api.binance.com/api/v3/time" | python3 -m json.tool

# Query ticker for BTCUSDT
curl -s "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
```

### 2. Hyperliquid API Connectivity Check
```bash
# Query exchange metadata
curl -s -X POST "https://api.hyperliquid.xyz/info" \
  -H "Content-Type: application/json" \
  -d '{"type": "meta"}' | python3 -m json.tool | head -30

# Query all mid prices
curl -s -X POST "https://api.hyperliquid.xyz/info" \
  -H "Content-Type: application/json" \
  -d '{"type": "allMids"}' | head -20
```

### 3. OKX API Connectivity Check
```bash
# Public ticker
curl -s "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT" | python3 -m json.tool
```

### 4. Adapter Factory & API Test in Python
```python
# Run quick Python adapter test
from core.adapters.exchanges.factory import ExchangeAdapterFactory

factory = ExchangeAdapterFactory()
adapter = factory.create("binance", paper_trading=True)
ticker = adapter.get_ticker("BTC/USDT")
print(f"Ticker: {ticker}")
```
