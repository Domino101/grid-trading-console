# 🩺 Binance API 連線失常診斷報告

> **事故日期**：2026-05-03
> **影響交易對**：`binance/SOL/USDC:USDC`
> **症狀關鍵字**：`-2015 Invalid API-key`、`-1003 Way too many requests`、`418 I'm a teapot`、`IP banned until`
> **出口 IP**：`36.224.21.191`（中華電信 HiNet 動態 IP，台灣）
> **報告角色**：Diagnosis & Assessment Skill（零寫入權限）
> **資料來源**：`logs/core.services.grid.implementations.grid_engine_impl.log`（26802 行，截至 13:09）

---

## 1. 專案全局狀態

* **核心交易功能停擺**：自 `2026-05-03 12:04:12` 起，網格引擎進入「下單全失敗 + IP 週期性被 BAN」惡性循環，連續 50+ 分鐘 `create_order` 0 成功率。
* **WebSocket 震盪**：每 2~3 分鐘 `❌ WebSocket连接断开 → 🔄 尝试恢复` 反覆觸發；REST 模式 fallback 又被 418 擊中。
* **邏輯漂移警告**：
  * `_execute_with_retry`（`binance_rest.py:81`）已內建 BAN 智慧退避；但上層 `_execute_batch_orders` 重試器（`grid_engine_impl.py:618`）**未感知 BAN/-2015 狀態**，繼續對 13 筆訂單執行 `max_retries` 輪重試 → 反向加劇 BAN（log 26656「🔄 第2次重试: 13个订单」即為證據）。
  * `_active_window_refresh_loop`、`balance_monitor._balance_monitor_loop` 與 `position_monitor` **不共享** BAN 冷卻狀態 → 解封後第一筆請求即觸發新一輪 BAN。

---

## 2. BUG 檢測矩陣

| # | 錯誤位置/模組 | 分類 | 根因分析 | 嚴重度 |
|---|---|---|---|---|
| 1 | Binance API Key（外部設定） | **用戶可修復** | `36.224.x.x` 為中華電信動態 IP，IP 變更後 API Key 白名單失效 → 所有簽名請求回 `-2015`。**本次事故 Root Cause**。 | High |
| 2 | `core/services/grid/implementations/grid_engine_impl.py` `_execute_batch_orders` 重試器（line ~618-660） | **模型可恢復** | 重試器未檢查上次失敗是否屬於「永久性錯誤 (-2015)」或「冷卻中錯誤 (-1003)」。在 -2015 條件下重試永不會成功，反而轟炸已被 BAN 的 IP。 | High |
| 3 | `core/adapters/exchanges/adapters/binance_rest.py` `_execute_with_retry`（line 95-115） | **模型可恢復** | BAN 識別邏輯只認 `'banned until'` / `'429'`，**未將 `-2015 Invalid API-key` 視為 fail-fast 條件**。-2015 與 -1003 都不可即時恢復，應同等熔斷。 | Med |
| 4 | `core/services/grid/implementations/grid_engine_impl.py` `_active_window_refresh_loop`（line 2112-2129） | **瞬時錯誤被放大** | 60s 週期呼叫 `get_open_orders`，**未檢查 `_ban_cooldown_until`**。`PositionMonitor` 已維護該狀態，但本 loop 不共用 → BAN 解除後立即重新觸發。 | Med |
| 5 | `core/services/grid/coordinator/balance_monitor.py` `_balance_monitor_loop`（line 93-106） | **瞬時錯誤被放大** | 同樣未共享 BAN 冷卻狀態；異常後僅 `await asyncio.sleep(self._update_interval)` 就再次嘗試。 | Med |
| 6 | WebSocket 自動恢復邏輯 | **意外錯誤** | WS 斷線後 REST 模式持續觸發限流，重連與限流交替震盪。本質為 #1 #2 衍生症狀。 | Low |

---

## 3. Harness 工程與架構升級建議

### 3.1 工具層 (Tool Layer)
* **新增全域 `ApiHealthGuard` 單例**：集中管理 `_ban_cooldown_until`、`_api_key_invalid_until`、`_consecutive_failures`，所有 REST 站點呼叫前必先 query。
  * 掛接點：`PositionMonitor` / `BalanceMonitor` / `_active_window_refresh_loop` / `_execute_batch_orders` 重試器 / `binance_rest._execute_with_retry`。
* **`_execute_with_retry` 錯誤分類升級**：將 `is_rate_limit` 拆為三類，不同類別觸發不同冷卻策略：
  * `is_api_key_invalid (-2015)` → **熔斷整個交易引擎**，發出 user-actionable 通知，等待人工確認 IP 白名單後手動 resume。
  * `is_banned (-1003)` → 解析 `banned until` 時間戳寫入 `_ban_cooldown_until`，暫停所有 REST。
  * `is_rate_limit (純 429)` → 指數退避。

### 3.2 護欄層 (Guardrails)
* `_execute_batch_orders` 進入重試前，檢查最後失敗類型：
  ```python
  if last_error_code in ('-2015', '-1003'):
      logger.error("永久性 / 冷卻中錯誤，跳過重試")
      break
  ```
* `_active_window_refresh_loop` 與 `_balance_monitor_loop` wrap：
  ```python
  if api_health_guard.is_circuit_open():
      await asyncio.sleep(refresh_interval)
      continue
  ```
* **新增 IP 漂移偵測**：啟動時與每小時 ping `https://api.ipify.org`，若 IP 變動則寫入 ALERT log，提示用戶更新 Binance API Key 白名單。

### 3.3 長期記憶更新（建議納入 `AGENTS.md` / `CLAUDE.md`）
* **R-API-001**：所有外部 REST 呼叫站點（balance/position/order/active_window）必須統一引用 `ApiHealthGuard`，禁止繞過。新增任何輪詢任務前，必須在 PR 中聲明已掛接 guard。
* **R-API-002**：對 Binance 而言，`-2015` 與 `-1003` 視為「不可即時恢復」錯誤，重試器一律 `break`，不得進入 `max_retries` 迴圈。
* **R-DEPLOY-001**：使用 IP 白名單的交易所 API Key 必須在動態 IP 環境下搭配 DDNS 或固定出口 VPS。生產部署 checklist 須包含「IP 白名單 vs 當前出口 IP」比對步驟。

---

## 4. 後續行動指南 (Actionable Remediation Plan)

### 4.1 立即動作（人類操作，10 分鐘內）
1. **登入 Binance API 管理頁面**：將當前出口 IP `36.224.21.191` 加入該 API Key 的白名單；或暫時關閉 IP 限制（不建議長期使用）。
2. **暫停網格引擎**：避免持續轟炸已被 BAN 的 IP，加速冷卻。最後一筆 BAN 解封時間戳 `1777792854425` ≈ `2026-05-03 13:20:54 UTC`。
3. **驗證**：
   * `curl -X GET "https://fapi.binance.com/fapi/v1/ping"` 確認 IP 已可通行。
   * 用 `fetch_balance` 驗證 API Key 已可簽名。

### 4.2 修復智能體 Prompt（給 Remediation Agent）
> **任務**：在 `core/services/grid/implementations/grid_engine_impl.py` 的 `_execute_batch_orders` 重試迴圈（約 line 618-660）以及 `_active_window_refresh_loop`（line 2112）中，加入「最近一次失敗錯誤碼短路」邏輯：
> 1. 在類別內維護 `self._last_api_error_code: Optional[str]` 與 `self._api_circuit_until: float`。
> 2. 在每次 `create_order` 失敗時，從 exception 的 `str(e)` 解析 `code:-XXXX` 並寫入 `_last_api_error_code`。
> 3. 進入下一輪重試前，若 `_last_api_error_code in ('-2015',)` 則 `break` 並 `logger.critical("API Key/IP 無效，請檢查白名單，已停止重試")`；若為 `'-1003'` 則計算 `banned until` 剩餘秒數寫入 `_api_circuit_until`，並 `await asyncio.sleep(剩餘秒數)`。
> 4. `_active_window_refresh_loop` 與 `balance_monitor._balance_monitor_loop` 在進入主體前檢查 `time.time() < self._api_circuit_until`，若是則直接跳過該輪。
> 5. **不要修改** `binance_rest._execute_with_retry` 既有的退避邏輯，只新增第二層短路。
> 6. 撰寫單元測試 `tests/services/grid/test_batch_retry_circuit_breaker.py`，模擬 -2015 和 -1003 異常，斷言重試器在第 1 次後即停止。

### 4.3 修改命名空間
* **主要修改**：`core/services/grid/implementations/grid_engine_impl.py`
* **次要修改**：`core/services/grid/coordinator/balance_monitor.py`、`core/services/grid/coordinator/position_monitor.py`（合併 `_ban_cooldown_until` 至共享 guard）
* **新增（可選）**：`core/adapters/exchanges/api_health_guard.py`
* **測試**：`tests/services/grid/test_batch_retry_circuit_breaker.py`

---

## 5. 附錄：關鍵日誌證據

| Log 行號 | 時間 | 事件 |
|---|---|---|
| 26501 | 12:04:12 | 首次 `-2015 Invalid API-key`（IP 白名單失效起點）|
| 26531 | 12:07:49 | 首次 `-1003 banned until 1777784981763`（限流升級為 BAN）|
| 26656 | 12:39:01 | `🔄 第2次重试: 13个订单`（驗證重試器未短路）|
| 26699 | 12:45:46 | 第二輪 `-1003`，BAN until `1777783557504` |
| 26750 | 13:06:57 | `获取当前价格失败: 418 I'm a teapot`，BAN until `1777792854425` |
| 26797 | 13:08:02 | 活躍窗口刷新降級使用本地 pending |

---

> **零寫入確認**：本報告全程僅以 read-only 模式產出，未修改任何程式碼或執行破壞性指令。
> **後續授權需求**：執行第 4.2 節修復計畫前，請明確授權「修改 grid_engine_impl.py 等檔案」。
