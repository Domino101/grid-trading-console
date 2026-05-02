# 診斷報告：Binance 永續網格 UI 統計／PnL／成交歷史顯示為空

- **報告日期**：2026-05-02
- **影響配置**：`config/grid/binance-neutral-perp-sol-usdc-100-160.yaml`
- **影響範圍**：所有使用 Binance 永續 + `ws_only_mode: true` 的網格策略
- **嚴重程度**：🔴 高（功能性數據完全失真，但執行層仍可運作）
- **是否已修改代碼**：否（本報告為診斷與計畫，未動任何檔案）

---

## 一、問題現象

執行 `binance-neutral-perp-sol-usdc-100-160.yaml` 時：
- ✅ 訂單能正常掛出、成交、補單（透過 60s 一次的 `_active_window_refresh_loop` 重建窗口）
- ❌ Web UI / Terminal UI 的「觸發次數統計」「盈虧統計」「最近成交訂單」全部顯示 **0 / 空**
- ❌ 日誌中從未出現 `✅ [成交]` 或 `📨 收到WebSocket订单更新` 字樣
- ✅ 日誌顯示 `📡 使用WebSocket实时监控订单成交`、`💓 WebSocket健康` (連接層面正常)

---

## 二、資料鏈路

```
Binance User Data Stream (ORDER_TRADE_UPDATE)
   ↓ [斷鏈點 ❌]
BinanceWebSocket._handle_user_messages
   ↓
_process_user_message → _handle_order_update
   ↓
self._user_subscriptions['order'](data)  ← callback
   ↓
GridEngineImpl._on_order_update
   ↓
GridCoordinator._on_order_filled
   ↓
PositionTrackerImpl.record_filled_order  ← 統計唯一更新點
   ↓
get_statistics() → terminal_ui / web_ui
```

斷鏈位置在最上層：**WebSocket 已連線但永遠收不到任何 `ORDER_TRADE_UPDATE` 訊息**，因此後續所有節點皆無資料，統計與歷史一直為初始值。

---

## 三、根因分析（按致命性排序）

### 🔴 根因 #1：User Data Stream WebSocket URL 拼接錯誤（致命）

**檔案**：`core/adapters/exchanges/adapters/binance_base.py:53`
```python
DEFAULT_WS_URL = "wss://fstream.binance.com/ws"  # 期货WebSocket
```

**檔案**：`core/adapters/exchanges/adapters/binance_websocket.py:229`
```python
ws_url = f"{self.ws_url}/ws/{self.listen_key}"
```

**實際生成 URL**：
```
wss://fstream.binance.com/ws/ws/{listenKey}   ← 雙重 /ws/ ❌
```

**Binance 期貨 User Data Stream 正確端點**：
```
wss://fstream.binance.com/ws/{listenKey}
```

**為何看起來「連接成功」**：錯誤路徑下伺服器仍接受 WebSocket Upgrade（不立即關閉），但因路徑無對應 listenKey 訂閱，**永不會推送任何 `ORDER_TRADE_UPDATE` / `ACCOUNT_UPDATE`**。TCP 層 ping/pong 仍能往返，所以 `_heartbeat_loop` 永遠成功，連接「看起來健康」但應用層完全死寂。

### 🟠 根因 #2：`ws_only_mode: true` 阻斷所有 REST fallback

**檔案**：`config/grid/binance-neutral-perp-sol-usdc-100-160.yaml`
```yaml
ws_only_mode: true
```

該旗標在 `grid_engine_impl.py` 中關閉了：
- L1552 `sync_pending_orders()` — 批量下單後同步
- L1458 `_smart_monitor_loop()` — REST 備援輪詢
- L1485 `_check_pending_orders()` — 指數退避輪詢

結果：**WS 死寂 + ws_only_mode = 永遠不會檢測到成交**。`_active_window_refresh_loop` 雖能補單，但其補單路徑不會經過 `_on_order_filled` → `record_filled_order`，所以統計永遠不增。

### 🟡 根因 #3：智能監控的健康檢查只看 TCP 心跳，無法偵測應用層死寂

**檔案**：`core/services/grid/implementations/grid_engine_impl.py:1426-1454`

對 `binance` 走 `_last_heartbeat`（由 `_heartbeat_loop` 每 30s `await ws.ping()` 更新）。TCP ping 永遠成功 → `heartbeat_age` 永遠 < `_ws_timeout_threshold` → **永不切回 REST 模式**。

### 🟡 根因 #4：BinanceAdapter 無 `_ws_connected` 屬性

**檔案**：`grid_engine_impl.py:1392-1393`
```python
if hasattr(self.exchange, '_ws_connected'):
    ws_connected = self.exchange._ws_connected
```

BinanceAdapter 只有 `_websocket._user_connected`，因此 `hasattr` 對 binance **永遠返回 False**，斷線檢測被無聲跳過。

---

## 四、修復方案

### 修復 A（必做・最小修改・立刻見效）

**檔案**：`core/adapters/exchanges/adapters/binance_websocket.py`
**行號**：229

```python
# 修改前
ws_url = f"{self.ws_url}/ws/{self.listen_key}"

# 修改後（self.ws_url 已含 /ws）
ws_url = f"{self.ws_url}/{self.listen_key}"
```

> 套用後 Binance 永續的 ORDER_TRADE_UPDATE 立即會推到 `_on_order_update` → coordinator → tracker，UI 統計、PnL、最近成交歷史會即時更新。

### 修復 B（建議・可靠性增強）：應用層死寂偵測

在 `grid_engine_impl._smart_monitor_loop` 的 binance/通用分支中，**追加「有掛單但 N 秒無 ORDER_TRADE_UPDATE 訊息」的死寂判定**。即使 `ws_only_mode=true`，當死寂超過閾值時也允許單次 REST 兜底（`_check_pending_orders()`）並上報告警。建議：
- 新增 config 欄位 `ws_silence_max_seconds`（預設 180）
- 死寂判定條件：`pending_orders 不為空` AND `time_since_last_message > ws_silence_max_seconds`
- 動作：呼叫 `_check_pending_orders()` 一次（不轉換模式），並 `logger.warning`

### 修復 C（建議・統一介面）：補上 `_ws_connected` 屬性別名

**檔案**：`core/adapters/exchanges/adapters/binance.py`

```python
@property
def _ws_connected(self) -> bool:
    """供 grid_engine_impl 智能監控檢測用戶數據流連線狀態"""
    return self._websocket.is_user_connected
```

讓 `grid_engine_impl.py:1392` 的斷線檢測對 binance 也生效，避免 listenKey 過期重連窗口期被忽略。

### 修復 D（驗證用・選做）：啟動自檢

`subscribe_user_data` 完成後，啟動一個 30 秒的一次性 watchdog：若 30 秒內仍無 user stream 訊息，重新獲取 listenKey 並重連一次。修復 A 後此情況不應出現，但屬可靠性兜底。

---

## 五、驗證步驟

1. 套用修復 A 後重啟程式
2. 觀察日誌應出現以下任一行：
   - `📨 收到WebSocket订单更新`
   - `✅ [成交] BUY/SELL <amount>@<price>`
3. 檢查 `logs/core.services.grid.coordinator.grid_coordinator.log` 出現 `_on_order_filled` 處理訊息
4. UI 中 `filled_buy_count` / `filled_sell_count` 開始遞增
5. 第一輪反手單成交後 `realized_pnl` 出現非零值
6. 「最近成交訂單」表格出現條目（取自 `tracker.trade_history`）

---

## 六、影響範圍

- ✅ Binance 永續所有網格策略（`exchange: binance`）
- ❌ Lighter / Hyperliquid 不受影響（SDK 內建 user stream，無此 URL 拼接邏輯）
- ❌ Backpack 不受影響（使用 `wss://ws.backpack.exchange` + JSON `subscribe` 訂閱，無 listenKey 路徑模式）
- ❌ Binance 現貨／市場行情流不受影響（不經過 `_connect_user_stream`）

---

## 七、相關檔案清單（後續修復者參考）

| 檔案 | 角色 | 關鍵符號／行號 |
|---|---|---|
| `core/adapters/exchanges/adapters/binance_base.py` | URL 常數定義 | `DEFAULT_WS_URL` (L53) |
| `core/adapters/exchanges/adapters/binance_websocket.py` | User Stream 連線 | `_connect_user_stream` (L216), `_get_listen_key` (L253), `_handle_user_messages` (L388), `_process_user_message` (L475), `_handle_order_update` (L651), `subscribe_user_data` (L968) |
| `core/adapters/exchanges/adapters/binance.py` | Adapter 對外介面 | `subscribe_user_data` (L329), `is_user_stream_connected` (L457) |
| `core/services/grid/implementations/grid_engine_impl.py` | 引擎 & 智能監控 | `_on_order_update` (L1671), `sync_pending_orders` (L1539), `_smart_monitor_loop` (L1376), `_try_restore_websocket` (L1513) |
| `core/services/grid/coordinator/grid_coordinator.py` | 協調器 | `_on_order_filled`, `_on_order_update_from_ws` |
| `core/services/grid/implementations/position_tracker_impl.py` | 統計唯一寫入點 | `record_filled_order`, `get_statistics` |
| `core/services/grid/terminal_ui.py` | UI 渲染 | 使用 `GridStatistics` |
| `config/grid/binance-neutral-perp-sol-usdc-100-160.yaml` | 觸發配置 | `ws_only_mode: true` |

---

## 八、給後續 AI 的執行建議

**最短路徑**（強烈建議先做這個）：
1. 套用「修復 A」一行修改
2. 重啟並驗證 5 分鐘內 UI 統計出現非零值
3. 若驗證通過，再評估是否實施 B/C/D

**完整路徑**（避免未來 listenKey 失效再次靜默）：
1. A → B → C → D 全部實施
2. 為 `binance_websocket._connect_user_stream` 補單元測試（mock listenKey 取得 + 驗證 URL 字串）
3. 為 `_smart_monitor_loop` 死寂偵測新增整合測試

**禁止行為**：
- ❌ 不要把 `DEFAULT_WS_URL` 改成 `wss://fstream.binance.com`（會破壞 `_connect_futures_stream` L189 對 `DEFAULT_WS_URL` 的直接使用，市場流是直接連 `/ws` 根路徑）
- ❌ 不要在 ws_only_mode 下無條件啟用 REST 輪詢（會抵消用戶設置 ws_only_mode 的初衷：節省 API 權重）

---

**報告結束**
