---
name: test-generation
description: "Automated test generation for grid-trading-console. Use when creating unit tests for trading services, exchange adapters, risk rules, or integration tests with pytest."
---

# Test Generation Skill

## When to Activate
- Creating new trading strategies, adapters, or risk filters
- Adding test coverage for core components in `core/` or `web_ui/`
- Running test validation before merging or deployment

## Test Framework
`grid-trading-console` uses `pytest` for testing Python components:
```bash
# Run full test suite
python3 -m pytest

# Run specific test file
python3 -m pytest tests/test_grid_trading.py -v

# Run frontend tests
cd web_ui/frontend && npm run test
```

## Key Test Areas
- **Exchange Adapters**: `tests/` mocking REST and WebSocket payloads
- **Grid Coordinator / Math**: Precision rounding, min notional, order spacing
- **Safety Guards**: Circuit breakers, paper trading boundaries, input validation
