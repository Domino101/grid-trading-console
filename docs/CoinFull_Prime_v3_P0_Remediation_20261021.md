# CoinFull Prime v3 P0 修復工作紀錄（2026-10-21）

- 基線：`fa90734`（VERSION 0.3.13）
- 依據：[CoinFull_Prime_v3_Current_Implementation_Review_20261021.md](CoinFull_Prime_v3_Current_Implementation_Review_20261021.md) 的 A-1～A-5
- 範圍：止損結算、籃子名單同步、degraded 換倉、對沖腿保護、止損生命週期
- 後續：P1-A（C-1/C-6/C-7）與 C-2/C-3 已於同日完成，見 [P1 Remediation Worklog](CoinFull_Prime_v3_P1_Remediation_20261021.md)
- 未連線交易所；未進行 live 驗證

## 1. 修復內容

### A-1 止損 dry-run / adapter 缺失不得改帳本

檔案：`core/services/coinfull_prime/rebalancer.py`

- `execute_stop_loss()` 在 `dry_run=True` 時只回傳 `status="simulated"`，不呼叫 `apply_basket_fill()`、不寫 `trade_fills`、不寫 `stop_loss` 事件。
- `exchange_adapter is None` 時回傳 `status="failed"`，籃子保持 active。
- 只有交易所回報 `filled > 0` 才結算；部分成交只結算 `filled`，剩餘數量留在籃子給下一輪守護。
- `fill_id` 優先使用交易所 order id；fallback 才用秒級時間戳。`exchange_order_id` 同步寫入 `trade_fills`。
- 事件新增 `requested_quantity`、`remaining_quantity`、`exchange_order_id`。

### A-2 名單同步不得關閉未成功平倉的成分

檔案：`core/services/coinfull_prime/service.py`、`core/services/coinfull_prime/rebalancer.py`

- `save_active_basket()` 改用 `close_missing=False`；平倉結算唯一由 `apply_basket_fill()` 擁有。
- `trigger_once()` 只在 `status == "completed"` 時同步新名單；`partial/failed/skipped/hedge_blocked/degraded_hold` 一律不同步。
- 新增 `_should_sync_basket()` 作為單一判斷點。
- 只登記「已持有或本次 entry 成交」的目標，避免 quantity=0 的 active 幽靈成分。
- `execute_rebalance()` 的「部分成交 + 部分跳過」改判 `partial`，不再誤標 `completed`。

### A-3 degraded 資料缺漏時 hold

檔案：`core/services/coinfull_prime/service.py`

- `RegimeDecision.degraded=True` 時，`trigger_once()` 直接回傳 `status="degraded_hold"`。
- 不建立新倉、不因不完整的 `pts=0` 全平；保留現有部位與對沖狀態。
- 仍寫入 regime history 與 run snapshot，並在 log 標明缺漏原因。

### A-4 對沖腿被外部機器人保護時整輪 abort

檔案：`core/services/coinfull_prime/rebalancer.py`、`core/services/coinfull_prime/service.py`

- 抽出 `_protected_sets()` / `_is_protected_symbol()` / `_matches_protected()`。
- `execute_rebalance()` 在建立任何多頭前檢查 `short_list`；若任一對沖標的在 `protected_symbols`，回傳 `status="hedge_blocked"`，不產生任何訂單。
- `_execute_rebalance_with_retry()` 遇到 `hedge_blocked` 不重試，寫 critical log，等待人工解除衝突。
- `force_flatten=True` 不受此限制（只平倉、不開新倉）。

### A-5 止損守護與排程解耦

檔案：`core/services/coinfull_prime/service.py`

- 新增 `_stop_loss_running`、`_has_active_long_positions()`、`_start_stop_loss_daemon()`。
- `_intra_week_stop_loss_loop()` 改用 `_stop_loss_running`，不再依賴排程的 `_is_running`。
- `stop_scheduler(disable=True)` 在仍有 active 多頭時保留守護；`disable=False`（程序關閉）或已無多頭才取消。
- `restore_scheduler()` 在排程停用但仍有 active 多頭時，啟動獨立守護。
- `get_status().risk_control.stop_loss_running` 暴露守護狀態。

## 2. 回歸測試

新增 `tests/test_coinfull_prime_p0_safety.py`（12 項；後續加入 2 項 mirror 復活回歸），並已加入 `scripts/ci.sh` 的 `TEST_MODULES`，CI 會直接跑：

| 測試 | 證明的契約 |
|---|---|
| `test_dry_run_does_not_mutate_basket_or_ledger` | dry-run 不呼叫 apply/record/event，籃子 quantity 不變 |
| `test_missing_adapter_does_not_mutate_basket` | adapter 缺失回 failed，籃子 active |
| `test_partial_fill_settles_only_filled_quantity` | filled=40/100 只結算 40，剩 60 active |
| `test_save_active_basket_never_closes_missing_components` | save_basket 收到 close_missing=False |
| `test_sync_only_on_fully_completed_rebalance` | 只有 completed 才同步名單 |
| `test_degraded_data_holds_instead_of_rebalancing` | degraded 不呼叫 execute_rebalance、不抓 features |
| `test_protected_hedge_aborts_before_opening_longs` | BTC 受保護時 status=hedge_blocked、0 訂單 |
| `test_stop_scheduler_keeps_guard_when_active_longs` | 停止排程但有多頭 → 不 cancel 守護 |
| `test_stop_scheduler_cancels_guard_when_no_longs` | 無多頭 → cancel 守護 |
| `test_shutdown_always_cancels_guard` | 程序關閉 → 一律 cancel |

調整 `tests/test_coinfull_prime_rebalance.py` 的止損結算測試：改用實盤 adapter mock（`dry_run=False`、`filled=100`、order id `SLTEST123`），不再用 dry-run 驗證結算。

## 3. 執行證據

| 檢查 | 結果 |
|---|---|
| `python3 -m unittest tests.test_coinfull_prime_p0_safety -v` | 10 tests OK |
| `python3 -m unittest tests.test_coinfull_prime_p0_safety tests.test_coinfull_prime_quant tests.test_coinfull_prime_rebalance tests.test_coinfull_prime_regime` | 47 tests OK |
| `SKIP_FRONTEND=1 bash scripts/ci.sh` | 572 tests OK（skipped=2）+ pytest `test_universe79_lib` 12 passed |
| `python3 -m unittest tests.test_accounting_path_policy` | 12 tests OK |
| `python3 -m py_compile`（changed files） | OK |
| `python3 tools/ledger_consistency_audit.py --markdown --fail-on high` | exit 1、UNHEALTHY；見下方說明 |

### Ledger audit 仍未 HEALTHY

P0 修復沒有新增 CoinFull Prime 的 L6/L13 異常；但既有資料庫仍有 5 項歷史發現：

- L2 ORPHAN_EVENTS：126 個 bot_id 有事件但未註冊 `bots`（含舊 `coinfull_prime` 無帳號後綴的 12 筆）。
- L5 FILL_EVENT_GAP：1 個 bot 有 fill 無 canonical fill 事件。
- L6 NON_IDEMPOTENT_EVENTS：14 筆舊 `daily_rebalance` 缺 `dedupe_key`（12 筆舊 `coinfull_prime` + 2 筆 `coinfull_prime:paper`），皆為 0.3.13 前歷史列；已於 0.3.14 `tools/coinfull_legacy_repair.py` 清除。
- L13 DUPLICATE_FILLS：660 組重複 `client_order_id`，非 CoinFull Prime。
- L8 CONTRACT_VIOLATION：38 個有成交的機器人缺部位/籃子/快照承載，非 CoinFull Prime。

CoinFull Prime 自身的 bots/fills/basket 覆蓋仍為正常；L13 沒有 CoinFull Prime 重複 fill。要讓 CI gate 轉綠，需另外處理歷史資料（backfill/cleanup）與其他策略的 L2/L5/L8/L13，不能靠本次 P0 宣告 HEALTHY。

## 4. Blast Radius

**這次安全所依賴的單一事實**：`execute_stop_loss()` 只有在 `not dry_run`、`exchange_adapter is not None`、且交易所回報 `filled > 0` 時，才會呼叫 `apply_basket_fill()` 與寫入 ledger。

- Certainty Level 4（跑過真實 code path）：`test_dry_run_does_not_mutate_basket_or_ledger`、`test_missing_adapter_does_not_mutate_basket`、`test_partial_fill_settles_only_filled_quantity` 直接呼叫 `execute_stop_loss()`，以 fake ledger 斷言 `apply_calls/fills/events` 為空或 quantity 正確。
- Certainty Level 4（跑過 service）：`test_degraded_data_holds_instead_of_rebalancing` 走完整 `trigger_once()`，斷言 `execute_rebalance` 未被呼叫。
- Certainty Level 4（跑過 rebalancer）：`test_protected_hedge_aborts_before_opening_longs` 走 `execute_rebalance()`，斷言 `hedge_blocked` 且 0 訂單。
- Certainty Level 5（live instance）**未達成**：本機沒有 8001 listener、沒有交易進程，無法做 live restart/curl 驗證。

**已清除的風險**：

- dry-run 止損不再清空 live 籃子。
- adapter 缺失不再製造「帳本已平倉、交易所仍有倉」的幽靈。
- 部分成交不再把剩餘部位誤標 closed。
- partial/failed 換倉不再用名單同步關閉失敗部位。
- degraded 不再在資料缺漏時換倉。
- 對沖腿被保護不再留下裸多。
- 使用者停止排程不再同時關掉 -12% 守護。

**仍未清除的風險**（P1，見原審閱報告）：

- 止損與換倉沒有共用互斥鎖（C-1）。
- ATR/成交量 fail-open（C-2）。
- RESTRICTED_TAGS 未配置（C-3）。
- 資金費率 Carry / equity 未入帳（C-4）。
- 無持倉 reconciliation（C-5）。
- 配置缺失會退回 v2 預設（C-6）。
- 1.15x 加碼的保證金檢查（C-7）。

## 5. 營運注意

本機目前沒有 Web UI / 交易進程；`data/coinfull_prime_scheduler.json` 仍顯示 `binance_live`、`enabled=true`、`is_running=true`，且 live 籃子有 3 個 `quantity>0` 的 active 成分。若本機就是生產機，部署後必須：

1. 以 systemd 或既有啟動方式重啟 Web UI/引擎，讓新 code 載入。
2. 確認 `GET /api/v1/coinfull-prime/status` 的 `risk_control.stop_loss_running=true`。
3. 確認 log 出現「止損守護已啟動」，且沒有 `degraded_hold` 或 `hedge_blocked` 的非預期狀態。
4. 以 paper/testnet 跑一次 `trigger_once(dry_run=true)`，再檢查 `bot_baskets` 未被改動。

在完成 live 重啟與驗證前，本次只能宣告「程式與測試完成」，不能宣告「生產已驗證」。
