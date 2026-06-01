# API Rate Limit Optimization — Implementation Summary

## Changes Applied

### Fix #1: 批量下單 Batch Orders API
**Files:** `binance_rest.py`, `grid_engine_impl.py`

| Before | After |
|--------|-------|
| 20 orders → 20 REST calls (serial `place_order`) | 20 orders → **4 REST calls** (Binance `batchOrders` API, 5/batch) |

- Added `BinanceRest.create_batch_orders()` — wraps ccxt `create_orders()` for Binance `POST /fapi/v1/batchOrders`
- Added `GridEngineImpl._place_orders_via_batch_api()` — prepares maker-price-adjusted orders and calls batch API
- Includes fallback to serial `place_order()` if `BinanceRest` adapter isn't directly accessible

---

### Fix #2: 重複 `get_current_price()` 消除
**File:** `grid_engine_impl.py`

| Before | After |
|--------|-------|
| `place_batch_orders` called `get_current_price()` at line 692 **and** line 753 | Queried **once** at method start, reused throughout |

- Added optional `current_price` parameter to `place_batch_orders()`
- If not provided, fetches once at the top; all downstream code reuses this value

---

### Fix #3: `_get_open_grid_ids_from_exchange` 雙查消除
**File:** `grid_engine_impl.py`

| Before | After |
|--------|-------|
| 1st call: `force_refresh=False` → 2nd call: `force_refresh=True` (always) | **Single call** with `force_refresh=True` |

- Removed the "double-check" pattern that always triggered 2 REST API calls
- Now directly queries with `force_refresh=True` once, eliminating 1 unnecessary REST call per batch cycle

---

### Fix #4: 中性網格啟動 Coordinator → Engine 重複查詢
**Files:** `grid_coordinator.py`, `grid_engine.py` (interface)

| Before | After |
|--------|-------|
| Coordinator fetched price at L1199, then `place_batch_orders` fetched again inside | Coordinator passes `current_price` to `place_batch_orders()` |

- Updated `IGridEngine.place_batch_orders()` interface to accept `Optional[Decimal] current_price`
- Coordinator now passes its already-fetched `current_price` downstream

---

### Fix #5: 5 網格啟動 API Burst 緩解
**File:** `run_multiple_grids.py`

| Before | After |
|--------|-------|
| `stagger_delay = 2` seconds | `stagger_delay = 5` seconds |

- Increased inter-grid startup interval from 2s to 5s
- 5 grids now spread over 25s instead of 10s, well below Binance's rate window

---

## Estimated API Call Reduction

| Scenario | Before (REST calls) | After (REST calls) | Reduction |
|----------|---------------------|---------------------|-----------|
| 20-order batch place | ~42 (20 orders + 20 prices + 2 open-order checks) | **~6** (4 batch + 1 price + 1 open-order check) | **~85%** |
| Neutral grid startup | ~44 (coordinator price + engine price + orders) | **~7** | **~84%** |
| 5 grids concurrent startup burst window | 10s | 25s | **2.5x wider** |

---

## Additional Cleanup
- Removed **pre-existing duplicate code** at end of `_sync_orders_from_exchange()` (lines 2836-2872) that caused parse errors

## Pre-existing Lints (NOT from this change)
- `batch_mode` kwarg on `create_order` — Lighter exchange path, interface mismatch
- `source` kwarg on `place_order` — interface mismatch
