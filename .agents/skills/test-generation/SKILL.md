---
name: test-generation
description: "Automated test generation for crypto-trading-open. Use when creating unit tests for trading services, exchange adapters, risk rules, or integration tests with pytest."
---

# Test Generation Skill

## When to Activate
- Creating unit tests for exchange adapters or trading strategies
- User asks to add test coverage or fix pytest failures
- Writing mocks for exchange REST/WS APIs

## Test Structure & Execution
- **Directory**: `tests/`
- **Execution**:
  ```bash
  python3 -m pytest tests/ -v
  ```

## Writing Tests for Exchange Adapters
1. **Mock Exchange Responses**: Avoid hitting live exchange APIs during unit tests. Use `unittest.mock` or `pytest-mock` to mock `aiohttp` or `ccxt` network calls.
2. **Order Placement Verification**: Verify quantity formatting, min notional checks, and exception handling.
3. **Symbol Format Mapping**: Test mapping between internal symbol representation (`BTC/USDT`) and exchange-specific raw tickers (`BTCUSDT`).

