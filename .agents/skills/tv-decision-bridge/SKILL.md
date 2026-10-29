---
name: tv-decision-bridge
description: TradingView MCP analysis and automated strategy execution bridge for grid-trading-console. Use when analyzing market opportunities with TradingView (technicals rating, screener, batch symbol data, economic calendar), converting TradingView symbols to canonical CCXT formats, or wiring analysis decisions into live/paper Grid Trading and DCA bots through the Hermes REST control plane.
---

# TradingView Decision Execution Bridge (`tv-decision-bridge`)

Bridge between **TradingView MCP analysis / market intelligence** and the **grid-trading-console execution engine** (Hermes REST API, Grid Trading, and DCA).

---

## When to Activate

- When you or the user query TradingView MCP tools (`mcp-tv-get-technicals-rating`, `mcp-tv-get-symbol-data-batch`, `mcp-tv-get-economic-calendar`, `mcp-tv-run-screener`) to discover market opportunities.
- When transforming a TradingView symbol (e.g. `BINANCE:SOLUSDC.P`, `BINANCE:BTCUSDT`, `HYPERLIQUID:SOLUSDC.P`) to project canonical format (`SOL/USDC:USDC`, `SOL/USDC`).
- When proposing, pre-testing, or executing Grid or DCA trading bots based on technicals (RSI, ADX, Bollinger Bands, ATR).
- When reacting to high-impact macroeconomic events (FOMC, CPI, NFP) by temporarily pausing DCA re-entries (`pause_reentry`) or setting price alerts.

---

## Architecture & Method Reference

| Component | File Path | Function / Class | Description |
|---|---|---|---|
| Symbol Resolver | `core/services/symbol_manager/tv_symbol_resolver.py` | `parse_tv_symbol`, `resolve_to_project_symbol`, `to_tv_symbol` | Bidirectional mapping between TV tickers and CCXT canonical formats |
| Execution Bridge | `tools/tv_execution_bridge.py` | `TradingViewExecutionBridge` | Python client & CLI for Hermes REST API with safety guards |
| Grid API | `web_ui/backend/routes/grids.py` | `POST /api/v1/grids` | Creates Grid bots with `SafetyGuard` & audit logging |
| DCA API | `web_ui/backend/routes/dca.py` | `POST /api/v1/dca`, `POST /api/v1/dca/preview`, `PATCH /api/v1/dca/{id}/config` | Creates DCA bots, previews ladders, and toggles `repeat_after_take_profit` |
| Market Recommendation | `web_ui/backend/routes/market.py` | `GET /api/v1/market/recommendation/{symbol}` | Historical backtest & recommended grid interval/range |
| Safety & Limits | `web_ui/backend/safety_guard.py` | `SafetyGuard` | Concurrency limits (10 grids, 10 DCAs, 30 total), rate limits, paper/live gates |

---

## Decision Heuristics: Mapping TradingView Signals to Strategies

### 1. Market Regime Classification
Use `mcp-tv-get-technicals-rating` and `mcp-tv-get-symbol-data-batch`:

| Regime | Indicators | Strategy Recommendation | Execution Action |
|---|---|---|---|
| **Ranging / Consolidation** | `ADX < 20`, `RSI 40..60`, BB width stable | **Neutral Grid** | Propose `POST /api/v1/grids` with `grid_type="neutral"`. Set range around BB Upper / Lower and step from ATR. |
| **Bullish Trend** | `ADX > 25`, `Recommend.All > 0.1`, Price > EMA50 > EMA200 | **Long DCA** | Propose `POST /api/v1/dca` with `direction="long"`. Set safety step based on ATR percentage. |
| **Bearish Trend** | `ADX > 25`, `Recommend.All < -0.1`, Price < EMA50 < EMA200 | **Short DCA** | Propose `POST /api/v1/dca` with `direction="short"` (or stop/reduce existing Long bots). |
| **High Volatility / Macro Event** | High-impact event (FOMC, CPI) in `< 2h` | **Risk Pause** | Call `pause_dca_reentry` to complete active cycles without opening new ones. |

### 2. Parameter Tuning from TradingView Metrics
- **Grid Upper / Lower**: Check TradingView's `BB.upper` and `BB.lower` (or period high/low from `get-ohlcv`). Cross-check with `/api/v1/market/recommendation/{symbol}`.
- **DCA Safety Step (`price_deviation_percent`)**: Set to $\approx 0.5 \times \text{ATR}(\%)$ to avoid cluster fills during normal intra-hour fluctuations.
- **Leverage**: Keep $\le 3\text{x}$ for DCA and $\le 10\text{x}$ for Neutral Grid.

---

## Execution Workflow (Standard Operating Procedure)

```
[ Step 1: Scan & Analyze via TradingView MCP ]
  mcp-tv-get-technicals-rating(symbol="BINANCE:SOLUSDC.P", interval="4h")
  mcp-tv-get-symbol-data-batch(symbols=["BINANCE:SOLUSDC.P", ...], columns=["ATR", "ADX", "BB.upper", "BB.lower"])
                     │
                     ▼
[ Step 2: System Health & Pre-flight Scan ]
  python3 tools/tv_execution_bridge.py health
  python3 tools/tv_execution_bridge.py parse BINANCE:SOLUSDC.P
                     │
                     ▼
[ Step 3: Math Verification & Preview (Zero Risk) ]
  python3 tools/tv_execution_bridge.py preview-dca BINANCE:SOLUSDC.P --price 99.7 --amount 10
  python3 tools/tv_execution_bridge.py recommend-grid BINANCE:SOLUSDC.P
                     │
                     ▼
[ Step 4: Propose to User (Human-in-the-Loop) ]
  Present concrete plan: Symbol, Direction, Capital, Safety Drawdown, TV Indicator Reason.
                     │
                     ▼
[ Step 5: Execute via Bridge (Paper by Default) ]
  python3 tools/tv_execution_bridge.py create-dca-paper BINANCE:SOLUSDC.P --amount 10 --direction long
  (For LIVE: requires explicit user instruction + confirm_live=True + GRID_LIVE_TRADING_ENABLED=true)
```

---

## Common CLI Commands

```bash
# 1. Check system health & limits
python3 tools/tv_execution_bridge.py health

# 2. Check current active bots across Grid and DCA
python3 tools/tv_execution_bridge.py list

# 3. Parse and verify TradingView symbol format
python3 tools/tv_execution_bridge.py parse BINANCE:SOLUSDC.P

# 4. Preview DCA ladder for a specific price
python3 tools/tv_execution_bridge.py preview-dca BINANCE:SOLUSDC.P --price 99.7 --amount 10 --direction long

# 5. Query historical grid recommendations
python3 tools/tv_execution_bridge.py recommend-grid BINANCE:SOLUSDC.P --period 7d

# 6. Create a Paper DCA bot
python3 tools/tv_execution_bridge.py create-dca-paper BINANCE:SOLUSDC.P --amount 10 --direction long --price 99.7 --reason "TV ADX 27 trend breakout"

# 7. Pause DCA re-entry ahead of macro news
python3 tools/tv_execution_bridge.py pause-dca <bot_id> --reason "FOMC interest rate decision in 2 hours"

# 8. Resume DCA re-entry after news clears
python3 tools/tv_execution_bridge.py resume-dca <bot_id> --reason "FOMC passed volatility normalized"
```

---

## Common Issues & Troubleshooting

| Issue | Cause | Resolution |
|---|---|---|
| `Symbol cannot be resolved` | TradingView ticker does not match known quotes or exchange prefixes | Run `python3 tools/tv_execution_bridge.py parse <sym>` to inspect base/quote breakdown. |
| `PermissionError: confirm_live=True required` | Attempting to create LIVE bot without dual-safety confirmation | Keep `account_mode="paper"`. If live trading is genuinely intended, confirm live trading with the user and provide `confirm_live=True`. |
| `429 Too Many Requests` | `source="tv_bridge"` exceeded write rate limit (default 5/min) | Wait for the cooldown window (60s) before executing further write actions. |
| `Bot created but not running` | `auto_start=False` was used (default safe mode) | The YAML configuration has been saved. The user or controller can review it in Web UI or call the `start` endpoint. |
| `TradingView data not streaming` | Subscription or market closed / delayed data | Check `update_mode` from TV batch quote. If not `streaming`, treat data as indicative and do not trigger immediate orders. |
