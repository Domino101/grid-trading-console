# 🔴 Live Production Audit & Deliverables Report

## Summary of Changes

Three production modules have been delivered:

| # | Deliverable | File | Status |
|---|-------------|------|--------|
| 1 | **ExecutionRouter** — Delta-hedged cross-market order routing | [execution_router.py](file:///usr/local/lib/crypto-option-bot/src/execution/execution_router.py) | ✅ Implemented |
| 2 | **CexFeed Live** — Real Deribit option chain ingestion | [cex_feed.py](file:///usr/local/lib/crypto-option-bot/src/ingestion/cex_feed.py) | ✅ Rewritten |
| 3 | **Telegram Circuit Breaker** — 5-fail threshold, 60s recovery | [telegram_client.py](file:///usr/local/lib/crypto-option-bot/src/telemetry/telegram_client.py) | ✅ Patched |

---

## Deliverable 1: ExecutionRouter (Notional Delta Hedging)

### The Problem That Was Killing You
The old `main.py` (lines 370-382) did this:

```python
# OLD — CATASTROPHIC 1:1 copy
poly_order = execute(token_id="mock-token-id", size=target_usd / poly_price)   # 50 shares
cex_order  = execute(symbol="mock-symbol",   amount=target_usd / option_price)  # 142 contracts
```

With `target_size=$20`, `poly_price=$0.40`, `option_price=$0.14`:
- Polymarket: 50 shares × $0.40 = **$20 exposure**
- Deribit: 142 contracts × 1 BTC × $70,000 = **$9,940,000 exposure** ☠️

### The Fix: Dollar-Delta Neutrality

```
Poly_delta_exposure = shares × binary_delta × $1
CEX_contracts       = Poly_delta_exposure / (vanilla_delta × BTC_price)
```

Example with real numbers:
- `shares = 50`, `binary_delta ≈ 0.012`, `vanilla_delta ≈ 0.55`, `BTC = $70,000`
- `poly_delta = 50 × 0.012 × $1 = $0.60`
- `cex_contracts = $0.60 / (0.55 × $70,000) = 0.0000156` → rounded to **0.1** (Deribit minimum)

### Hard Safety Assertions

| Check | Limit | Behavior on Violation |
|-------|-------|-----------------------|
| Max Polymarket shares | 10,000 | `AssertionError` → trade blocked |
| Max CEX contracts | 0.5 BTC | `AssertionError` → trade blocked |
| Delta hedge ratio | 0.3x – 3.0x | `AssertionError` → trade blocked |
| Poly price range | (0, 1] | `AssertionError` → trade blocked |
| Token/symbol empty | N/A | `AssertionError` → trade blocked |

---

## Deliverable 2: CexFeed Live Deribit Integration

### What Changed
The old `cex_feed.py` called `_feed_aligned_chain()` which generated **100% synthetic** Black-Scholes prices. No real Deribit API call was ever made.

### New Architecture

```
TRADING_MODE=LIVE_PRODUCTION
  └─ _poll_loop()
       ├─ _fetch_underliers()       ← REST: BTC/USD:BTC, ETH/USD:ETH
       ├─ _ws_ticker_loop()         ← WebSocket: real-time index updates
       └─ _fetch_real_option_chain()
            ├─ Constructs Deribit symbols: "BTC-21MAY26-70000-C"
            ├─ Fetches: bid, ask, mark_iv from ticker.info
            ├─ Converts BTC-denominated prices → USD
            └─ Pushes to BufferAligner.update_cex()

TRADING_MODE=SANDBOX
  └─ _process_chain_for_target()
       └─ _feed_aligned_chain()     ← Synthetic BS model (unchanged)
```

### Key Conversion: Deribit Prices are in BTC

```python
c_bid_usd = c_bid * underlier_price  # 0.0050 BTC × $70,000 = $350
```

---

## Deliverable 3: Telegram Circuit Breaker

### State Machine

```
CLOSED (normal) ──5 consecutive failures──▶ OPEN (local-only)
        ▲                                         │
        └─────────60s cooldown + 1 success────────┘
```

### Behavior When Open
- `_queue_worker()`: Messages logged locally via `logger.warning()`, not sent to network
- `_update_polling_loop()`: Skips `getUpdates` entirely, sleeps 5s between checks
- **Zero impact on trading thread** — the queue worker never blocks

---

## Remaining Steps Before Going Live

> [!CAUTION]
> Do NOT set `TRADING_MODE=LIVE_PRODUCTION` until ALL items below are verified.

1. **Set real API keys** in `.env`:
   - `DERIBIT_API_KEY` / `DERIBIT_API_SECRET`
   - `POLYMARKET_API_KEY` / `POLYMARKET_API_SECRET` / `POLYMARKET_API_PASSPHRASE`
   - `POLYGON_PRIVATE_KEY`

2. **Shadow run test**: Set `TRADING_MODE=LIVE_PRODUCTION` with `MAX_SINGLE_TRADE_SIZE_USD=1.0` and observe:
   - Are Deribit tickers being fetched? (look for `DERIBIT_LIVE_TICKER` logs)
   - Are assertion checks passing? (look for `PRE_EXECUTION_ASSERT_OK` logs)
   - Are hedge quantities sane? (look for `EXECUTION_PLAN` logs)

3. **Verify Polymarket EIP-712 signing** works with your real wallet on Polygon mainnet

4. **Set `POLYGON_RPC_URL`** to a reliable provider (Alchemy/Infura/QuickNode)
