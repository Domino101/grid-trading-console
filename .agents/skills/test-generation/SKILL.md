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
`grid-trading-console` tests are `unittest`-style and can be run through the repo entrypoint (or pytest since it is installed):
```bash
# Run the canonical full suite (Python + frontend + lint + build)
./scripts/ci.sh

# Run a targeted module list with unittest (repo convention)
python3 -m unittest tests.test_dca_ladder tests.services.grid.backtest.test_simulator

# Run the full Python suite with pytest (also works)
python3 -m pytest

# Run a specific test file
python3 -m pytest tests/services/grid/backtest/test_risk.py -v

# Run frontend tests
cd web_ui/frontend && npm test
```

## Key Test Areas
- **Exchange Adapters**: `tests/` mocking REST and WebSocket payloads
- **Grid Coordinator / Math**: Precision rounding, min notional, order spacing
- **Safety Guards**: Circuit breakers, paper trading boundaries, input validation
