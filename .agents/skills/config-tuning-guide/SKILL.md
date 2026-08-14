---
name: config-tuning-guide
description: "Strategy parameter tuning guide for grid-trading-console. Use when adjusting strategy parameters, understanding parameter interactions, choosing conservative vs aggressive profiles, or backtesting settings for Grid Trading, Multi-Grid Engine, Volume Maker, Arbitrage Monitor, and Price Alerts."
---

# Config Tuning Guide Skill

## When to Activate
- User asks about configuration parameters or tuning strategy settings
- Adjusting strategy parameters for Grid Trading, Multi-Grid Engine, Volume Maker, Arbitrage Monitor, or Price Alert
- Setting up conservative vs aggressive trading profiles

## Configuration Structure

Configuration parameters in `grid-trading-console` are specified in `config/` (e.g. `config/grid/`, `config/volume_maker/`, `config/arbitrage/`, `config/price_alert/`) or entry scripts (`run_multiple_grids.py`, `run_grid_trading.py`, `run_volume_maker.py`, `run_arbitrage_monitor.py`, `run_price_alert.py`).

```yaml
exchange: "binance"
symbol: "BTC/USDT"
paper_trading: true
grid:
  grid_levels: 10
  lower_price: 50000.0
  upper_price: 70000.0
  order_amount: 0.001
  stop_loss_price: 48000.0
  take_profit_price: 72000.0
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
- Wider price band (+/- 25% from current price)
- Fewer levels (10–15 levels)
- Higher profit per grid (> 1.0% net after exchange fees)

### Aggressive Grid
- Narrower price band (+/- 8% from current price)
- Higher density (20–40 levels)
- Lower profit per grid (0.3%–0.6% net)
- Requires tight monitoring of exchange maker/taker fee tier
