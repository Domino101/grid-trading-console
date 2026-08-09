---
name: config-tuning-guide
description: "Strategy parameter tuning guide for crypto-trading-open. Use when adjusting strategy parameters, understanding parameter interactions, choosing conservative vs aggressive profiles, or backtesting settings for Grid Trading, Volume Maker, Arbitrage Monitor, and Price Alerts."
---

# Config Tuning Guide Skill

## When to Activate
- User asks about configuration parameters or tuning strategy settings
- Adjusting strategy parameters for Grid Trading, Volume Maker, Arbitrage Monitor, or Price Alert
- Setting up conservative vs aggressive trading profiles

## Configuration Structure

Configuration parameters in `crypto-trading-open` are specified in `config/` or entry scripts (`run_grid_trading.py`, `run_volume_maker.py`, `run_arbitrage_monitor.py`, `run_price_alert.py`).

```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "paper_trading": true,
  "strategy": {
    "grid": {
      "grid_levels": 10,
      "lower_price": 50000.0,
      "upper_price": 70000.0,
      "order_amount": 0.001
    }
  }
}
```

## Strategy Parameters Reference

### Grid Trading
| Parameter | Default | Range | Description |
|---|---|---|---|
| `lower_price` | Dynamic | Min range | Lower grid boundary |
| `upper_price` | Dynamic | Max range | Upper grid boundary |
| `grid_levels` | 10 | 3–50 | Number of grid price levels |
| `order_amount` | 0.001 | Min lot size+ | Quantity per grid order |
| `stop_loss_price` | Optional | Below lower | Emergency stop-loss trigger |
| `take_profit_price` | Optional | Above upper | Take-profit trigger |

### Volume Maker
| Parameter | Default | Description |
|---|---|---|
| `maker_spread_pct` | 0.001 (0.1%) | Spread percentage around mid-price |
| `order_refresh_sec` | 5 | Time before re-quoting unfilled maker orders |
| `max_inventory_skew` | 0.05 | Max allowed asset inventory imbalance |

### Arbitrage Monitor
| Parameter | Default | Description |
|---|---|---|
| `min_spread_pct` | 0.003 (0.3%) | Minimum price spread between exchanges to trigger |
| `trade_amount` | Dynamic | Per-trade volume size |

## Tuning Profiles

### Conservative Grid
```json
{
  "grid_levels": 20,
  "spread_margin": "wide (5-10%)",
  "stop_loss_enabled": true,
  "paper_trading": true
}
```

### Aggressive Grid
```json
{
  "grid_levels": 50,
  "spread_margin": "tight (1-2%)",
  "order_amount": "higher",
  "paper_trading": false
}
```

