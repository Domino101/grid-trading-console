---
name: strategy-development
description: "Guidelines for developing, testing, and deploying new trading strategies in crypto-trading-open. Use when creating new trading strategies, modifying existing ones (Grid Trading, Volume Maker, Arbitrage Monitor, Price Alert), or evaluating strategy performance."
---

# Strategy Development Skill

## When to Activate
- Creating a new trading strategy or service under `core/services/`
- Modifying existing strategies (`grid`, `volume_maker`, `arbitrage_monitor`, `price_alert`)
- Evaluating strategy performance or adding new exchange support

## Architecture Overview

Strategies in `crypto-trading-open` live in `core/services/` (e.g. `grid/`, `volume_maker/`, `arbitrage_monitor/`, `price_alert/`). They communicate with exchanges via `ExchangeAdapterFactory` and exchange adapters in `core/adapters/exchanges/adapters/`.

```
Strategy Service (core/services/)
    ↓
Exchange Adapter Factory (core/adapters/exchanges/factory.py)
    ↓
Exchange Adapter (Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack)
```

## Development Guidelines

1. **Service Class**: Implement lifecycle methods (`initialize()`, `run()`, `stop()`).
2. **Exchange Interaction**: Use `ExchangeAdapterFactory().create(exchange_name)` to instantiate exchange adapters. Always check `adapter.create_order()` return structure.
3. **Risk Verification**: Validate min notional values and precision before order submission.
4. **Testing**: Write unit tests in `tests/` to verify logic.

