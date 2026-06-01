# 🔍 Binance REST API 調用分析

## 回答你的核心問題

### Q: 放價格上下各10格的訂單，調用了幾次 API？

**答案：20 次 REST 調用。每放一張訂單 = 1 次 `POST /fapi/v1/order`。**

程式碼證據在 [grid_engine_impl.py:800-806](file:///root/nofx_recovered/crypto-trading-open/core/services/grid/implementations/grid_engine_impl.py#L800-L806)：

```python
# 目前的下單方式：逐筆串行
for order in orders_to_place:
    try:
        result = await self.place_order(order)       # ← 每筆 = 1 次 REST
        results.append(result)
        await asyncio.sleep(0.1)                     # ← 100ms 間隔
    except Exception as e:
        results.append(e)
        await asyncio.sleep(0.5)
```

> [!IMPORTANT]
> Binance 期貨有 **批量下單 API** `POST /fapi/v1/batchOrders`，一次請求可放 **5 張訂單**。
> 用批量 API：20 張訂單 = **4 次** REST（而不是 20 次）。
> ccxt 支援 `exchange.create_orders()` 方法可直接使用此端點。

---

## 啟動時 REST 調用爆量分析

以 **5 個網格同時啟動** 為例，分析初始化時產生的 REST 調用：

### 共享適配器初始化（1 次）

| 動作 | REST 調用 | 備註 |
|------|----------|------|
| `load_markets` | 1 | 加載市場資訊 |
| `fetch_time` | 1 | 測試連接 |
| **小計** | **2** | |

### 每個網格的啟動流程

| 動作 | REST 調用 | 出處 |
|------|----------|------|
| `get_current_price()` (窗口選擇前) | 1 | `place_batch_orders:692` |
| `get_current_price()` (再次/批次處理) | 1 | `place_batch_orders:753` |
| `_get_open_grid_ids_from_exchange()` | 1 | `place_batch_orders:781` |
| `_get_open_grid_ids_from_exchange(force_refresh=True)` | 1 | `place_batch_orders:790` |
| **下單** (上下各10格) | **20** | `place_order()` × 20 |
| `get_positions()` (初始持倉同步) | 1 | `grid_coordinator:1149` |
| `get_current_price()` (中性窗口布置) | 1 | `grid_coordinator:1199` |
| `get_current_price()` (剝頭皮檢查) | 1 | `grid_coordinator:1244` |
| **小計** | **~27** | |

### 5 個網格的啟動總量

| 項目 | 次數 |
|------|------|
| 共享適配器初始化 | 2 |
| 5 × 每個網格啟動 | 5 × 27 = **135** |
| **總計** | **~137** 次 REST 調用 |

> [!CAUTION]
> 原始程式碼中 5 個網格是**同時**啟動（`asyncio.create_task`），如果沒有快取命中，這 137 次調用可能在**幾秒內**全部打出去。
> 
> Binance 期貨 API 請求權重限制是 **2400/分鐘**，但 IP BAN 閾值遠低於此（通常在短時間內突然打出大量請求就會觸發 -1003）。

---

## 運行時週期性 REST 調用

### 每個網格在運行中的週期性調用

| 模組 | 間隔 | REST 調用 | 說明 |
|------|------|----------|------|
| `_active_window_refresh_loop` | 90s | `get_current_price()` + `get_open_orders()` + 補單 | 窗口刷新 |
| `_smart_monitor_loop` → REST fallback | 15s+ | `_check_pending_orders()` → `get_open_orders()` | WS 靜默時觸發 |
| `sync_with_exchange()` | 按需 | `get_open_orders()` | 深度同步 |
| 統計彙報 (run_multiple_grids) | 60s | `get_statistics()` → 可能觸發多個 REST | 定期統計 |
| PositionMonitor | 每輪 | `get_positions()` + `get_balances()` | 持倉/餘額監控 |

---

## 🔧 可減少的不必要調用

### 1. ⭐ 批量下單（最大改善）

**現狀**: 20 張訂單 = 20 次 REST  
**改善**: 使用 `batchOrders` API → 20 張 = 4 次 REST  
**節省**: 80% 下單 API 調用

```python
# 改善方案：使用 ccxt 的 create_orders 批量下單
# Binance futures 支持一次最多5筆訂單
batch_size = 5
for i in range(0, len(orders), batch_size):
    batch = orders[i:i+batch_size]
    await exchange.create_orders(batch)  # 1 次 REST = 5 張訂單
```

### 2. ⭐ `place_batch_orders` 內重複的 `get_current_price()`

**現狀**: 同一個 `place_batch_orders` 調用中有 **2 次** `get_current_price()`
- 第 692 行：窗口選擇前
- 第 753 行：批次處理中又查一次

**改善**: 只查 1 次，傳給後續邏輯使用。

### 3. ⭐ `_get_open_grid_ids_from_exchange` 的雙查

**現狀**: 第 781 行查一次（`force_refresh=False`），第 790 行又查一次（`force_refresh=True`）  
如果第一次快取未命中，2 次都會打 REST → **同一個動作 2 次 REST**。

**改善**: 首次查詢直接用 `force_refresh=True`，去掉二次確認邏輯。

### 4. 中性網格啟動窗口下單 + place_batch_orders 重複

**現狀**: `grid_coordinator:1231` 呼叫 `place_batch_orders(window_orders)`，裡面又會重新查 `get_current_price()` 和 `_get_open_grid_ids_from_exchange()`。  
**但** coordinator 在第 1199 行已經查過一次價格了。

### 5. ⭐ 5 個網格同時啟動的 API burst

**現狀**: `run_multiple_grids.py` 一次性 `create_task` 啟動全部  
**改善** (已做): 錯開 2 秒啟動，避免 burst  

---

## 📊 改善前後的 API 調用對比（5 網格啟動）

| 項目 | 改善前 | 改善後 |
|------|--------|--------|
| 下單 API | 100 (5×20) | 20 (5×4) |
| 重複價格查詢 | 10 (5×2) | 0 |
| 重複掛單查詢 | 10 (5×2) | 5 (5×1) |
| 啟動 burst | 同時 | 錯開 2s |
| **總計** | ~137 | ~42 |
| **節省** | | **~70%** |
