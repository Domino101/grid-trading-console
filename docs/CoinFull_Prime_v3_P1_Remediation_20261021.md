# CoinFull Prime v3 P1 修復工作紀錄（2026-10-21）

- 基線：`fa90734`（VERSION 0.3.13）
- 範圍：C-1、C-6、C-7、C-2、C-3、C-4、C-5
- 未連線交易所；未進行 live 驗證

## 1. 修復內容

### C-1 止損與換倉共用互斥鎖

檔案：`core/services/coinfull_prime/service.py`

- `check_and_trigger_stop_losses()` 先檢查 `_rebalance_lock`；若換倉正在執行，直接跳過本次止損檢查，下一輪 5 秒 tick 重查。
- 以 `asyncio.wait_for(lock.acquire(), timeout=0.05)` 取得鎖，避免在換倉剛開始時排隊等待。
- 取得鎖後呼叫 `_check_and_trigger_stop_losses_locked()`，finally 釋放鎖。
- 避免同一標的被換倉單與 reduceOnly 止損單同時處理。

### C-6 配置缺失 fail-closed + v3 預設

檔案：`core/services/coinfull_prime/service.py`、`core/services/coinfull_prime/quant_engine.py`

- `load_config()` 在以下情況拋出 `RuntimeError`，拒絕啟動：
  - 檔案不存在
  - YAML 解析失敗
  - 檔案為空或不是 dict
- 不再把 `self.config` 設為 `{}` 後用程式預設值交易。
- `CoinFullPrimeQuant` 預設改為 v3：
  - weights：momentum 0.60 / funding 0.40 / flow 0 / liquidation 0
  - `hedge_mode=benchmark`
  - `momentum_mode=blend`，blend 預設 0/0.5/0.3/0.2
  - `funding_mode=smooth`、`settlements=9`
  - `top_n=80`、`long_count=5`、`short_count=1`
  - `min_24h_volume_usdt=50M`、`max_7d_atr_pct=0.20`
  - `annual_max_long_pct=60`、`annual_min_short_pct=-40`
  - `overheat_penalty=True`

### C-7 加碼前保證金上限

檔案：`core/services/coinfull_prime/service.py`

- 新增 `_cap_scale_by_free_margin()`。
- 只在 `scale>1.0` 且非 `force_flatten` 時執行。
- 以 USDT `free` 餘額的 95% 為預算；`required = capital * leverage * scale`。
- 餘額不足時將 scale 壓到 `budget / base_notional`，最低 1.0x。
- adapter 缺失、餘額讀取失敗、找不到 USDT 餘額時，一律降回 1.0x。

### C-2 Opt D fail-closed

檔案：`core/services/coinfull_prime/quant_engine.py`、`core/services/coinfull_prime/service.py`

- `fetch_features()` 每次重跑先清空 `latest_prices` / `quote_volumes` / `atr_pct_map`，避免抓取失敗時沿用上一輪舊資料。
- `compute_ranks()`：
  - 成交量資料全域缺漏 → `status=failed`
  - ATR 資料全域缺漏 → `status=failed`
  - 個別 symbol ATR 缺值 → 直接排除
  - 回補階段不再放寬 ATR
  - 無合格多頭 → `status=failed`，不建立只有空頭的裸對沖
  - 無合格空頭/對沖 → `status=failed`，不建立只有多頭的裸方向倉
- `trigger_once()`：
  - `rank_result.status != "success"` 時回 `ranking_failed` 並 hold
  - 不把空名單交給 `execute_rebalance()`，避免 `calculate_diff` 把現有部位全平

### C-3 restricted_symbols 配置

檔案：`config/coinfull_prime/strategy_config.yaml`、`core/services/coinfull_prime/quant_engine.py`

- 新增 `universe.restricted_symbols: []`，語意為精確 symbol 人工黑名單（大小寫不敏感）。
- `restricted_tags` 僅保留為舊配置別名。
- 不再宣稱有交易所 MEME/AI_AGENT tag 過濾；交易所沒有穩定 tag 資料源，需人工維護清單與審核理由。

### C-4 資金費率 Carry 與 equity snapshot

檔案：`core/services/coinfull_prime/service.py`、`web_ui/backend/routes/coinfull_prime.py`

- 新增 `sync_funding_income()`：從 ccxt `fetch_income("USDT", since, limit, {"incomeType": "FUNDING_FEE"})` 取得資金費率現金流。
- 每筆以 `tranId` 作為穩定 `fill_id`（`funding:{account}:{tranId}`），寫入 `trade_fills`：
  - `side="funding"`
  - `role="funding"`
  - `price=0`、`quantity=0`
  - `realized_pnl=income`
- `record_fill()` 會同步 emit `fill` 事件，讓 Carry 可被 `get_fills` 加總與事件溯源回放。
- 同步 cursor 寫入 `bot_runtime_state.funding_sync_cursor`；每次預設回溯 7 天，重跑為冪等 no-op。
- `trigger_once()` 在取得 adapter 後同步 funding；paper 自動跳過。
- `_persist_run_snapshot()` 新增：
  - `funding_income_usdt`：由 canonical fills 的 `role=funding` 加總。
  - `account.equity_usdt` / `account.free_margin_usdt`：由 adapter USDT 餘額補寫。
- 新增 `POST /api/v1/coinfull-prime/funding/sync` 手動同步端點。
- 前端 `CoinFullPrimePage.tsx` 與 `api.ts`：funding 列顯示「資金費率 / 資金費率 Carry」，價格與數量顯示「—」，不再被誤認為一般買賣成交。

### C-5 持倉 reconciliation

檔案：`core/services/coinfull_prime/service.py`、`web_ui/backend/routes/coinfull_prime.py`

- 新增 `reconcile_positions()`，比對交易所持倉與 `bot_baskets`：
  - `missing_on_exchange`：帳本 active 有數量，但交易所沒有對應部位。
  - `quantity_mismatch`：兩邊都有，但 signed quantity 差異超過 1%（至少 1e-8）。
  - `unmanaged_on_exchange`：交易所有部位但不在本策略籃子，也未受外部機器人保護。
  - `protected_on_exchange`：交易所部位屬於外部網格/DCA，預期存在。
- 只寫 `reconcile` 事件與 critical log，**不自動平倉**，避免對帳誤判造成資金損失。
- `trigger_once()` 在換倉鎖釋放後執行，timeout 30 秒；paper 與無 adapter 時跳過。
- 新增 `POST /api/v1/coinfull-prime/reconcile` 手動對帳端點。

## 2. 回歸測試

新增 `tests/test_coinfull_prime_p1_safety.py`（18 項）：

| 類別 | 測試 |
|---|---|
| C-1 | `test_stop_loss_skips_when_rebalance_lock_held`、`test_stop_loss_releases_lock_when_no_positions` |
| C-6 | `test_missing_config_refuses_start`、`test_empty_config_refuses_start`、`test_quant_v3_defaults` |
| C-7 | sufficient / insufficient / intermediate / missing adapter / balance failure / force_flatten 共 6 項 |
| C-2 | volume 缺漏、ATR 缺漏、個別 ATR 缺值、回補不放寬、ranking_failed hold |
| C-3 | restricted_symbols 過濾、production config 有此欄位 |

新增 `tests/test_coinfull_prime_p1b_safety.py`（7 項）：

| 類別 | 測試 |
|---|---|
| C-4 | funding 入帳與冪等、Carry 加總、equity/free margin helper |
| C-5 | missing/mismatch/unmanaged/protected 對帳、paper/無 adapter/API 失敗跳過 |

既有測試調整：

- `tests/test_coinfull_prime_quant.py`：v2 basket 測試顯式指定 `hedge.mode=basket`、`min_24h_volume_usdt=0`、`max_7d_atr_pct=0`，避免被 v3 預設影響。
- `tests/test_unified_ledger_accounting.py`：`make_service()` 寫入最小合法 YAML，配合 C-6 fail-closed。

## 3. 執行證據

| 檢查 | 結果 |
|---|---|
| `python3 -m unittest tests.test_coinfull_prime_p1_safety -v` | 18 tests OK |
| `python3 -m unittest tests.test_coinfull_prime_p1b_safety -v` | 7 tests OK |
| CoinFull Prime 全部測試（P0 + P1 + P1B + quant + rebalance + regime） | 74 tests OK |
| `SKIP_FRONTEND=1 bash scripts/ci.sh` | **602 tests OK**（skipped=2）+ pytest 12 passed，exit 0 |
| `npm test`（frontend） | 101 tests passed |
| `npm run lint`（frontend） | 0 errors（1 個既有 react-hooks warning） |
| `npm run build`（frontend） | tsc + vite build 通過 |
| `scripts/public_repo_guard.sh` | G1–G6 全部通過 |
| `py_compile` / `git diff --check` | 通過 |

Ledger audit 在 P1 完成時仍是既有歷史紅燈（L2/L5/L6/L13/L8）。後續已用 `tools/coinfull_legacy_repair.py` 清掉 CoinFull Prime 的 L6（14→0）與 L5（26 fills→0），並註冊 legacy `coinfull_prime` 身分（全域 L2 140→139；後續 CI/測試寫入會使全域計數再變動）。L2/L13/L8 的其他資料仍未清理，不能宣告 HEALTHY；詳見 [Ledger Legacy Repair](CoinFull_Prime_v3_Ledger_Legacy_Repair_20261021.md)。

## 4. Blast Radius

**C-1 安全所依賴的事實**：換倉鎖被持有時，`check_and_trigger_stop_losses()` 在載入籃子前就回傳空結果。

- Level 4：`test_stop_loss_skips_when_rebalance_lock_held` 直接持有鎖並斷言 `load_basket_entries` 未被呼叫。

**C-6 安全所依賴的事實**：配置缺失/空檔不會進入交易路徑。

- Level 4：`test_missing_config_refuses_start`、`test_empty_config_refuses_start` 直接呼叫 `initialize()` 並斷言 `RuntimeError`。
- Level 4：`test_quant_v3_defaults` 斷言裸 `CoinFullPrimeQuant()` 不再是 v2 對稱小幣空頭。

**C-7 安全所依賴的事實**：只有可用保證金足夠時才允許 `scale>1.0`。

- Level 4：6 項 `_cap_scale_by_free_margin` 測試，含 adapter 缺失與餘額讀取失敗。

**C-2 安全所依賴的事實**：成交量/ATR 缺漏或無合格多空標的時，不會把空名單交給換倉器。

- Level 4：`test_missing_volume_data_fails_closed`、`test_missing_atr_data_fails_closed`、`test_fallback_does_not_relax_atr`、`test_ranking_failure_does_not_liquidate_positions`。

**C-4 安全所依賴的事實**：資金費率只以交易所回報的 FUNDING_FEE 入帳，且 fill_id 以 tranId 去重。

- Level 4：`test_funding_income_is_recorded_idempotently` 直接跑兩次 `sync_funding_income()`，第二次 recorded=0，fills 仍 2 筆。

**C-5 安全所依賴的事實**：對帳只回報，不自動平倉。

- Level 4：`test_reconcile_reports_missing_and_mismatch` 斷言 missing/mismatch/unmanaged/protected 與 `reconcile` 事件；沒有呼叫任何平倉路徑。
- Level 5（live instance）未達成：本機沒有 8001 listener、沒有交易進程。

**仍未清除的風險**：

- `Ledger audit` 的 legacy L2/L13/L8（CoinFull Prime 的 L5/L6 已於 0.3.14 repair tool 清除）。
- 止損價格仍可能回退到上一次換倉的 `latest_prices`（P2 已記錄）。
- 資金費率同步依賴 ccxt `fetch_income`；若交易所/ccxt 不支援，會安全跳過，Carry 不會入帳。

## 5. 營運注意

- C-6 是行為變更：任何使用錯誤 `config_path` 的部署現在會啟動失敗，而不是靜默用預設值交易。這是預期行為。
- 部署後請確認 `GET /api/v1/coinfull-prime/status` 正常，且 `risk_control.stop_loss_running=true`。
- 若上線前用的是舊 `strategy_config.yaml`，請確認 `universe.restricted_symbols` 欄位存在（可為空陣列）。
- 本機目前沒有 Web UI / 交易進程，無法做 live restart/curl 驗證。
- 新增手動端點：`POST /api/v1/coinfull-prime/funding/sync` 與 `POST /api/v1/coinfull-prime/reconcile`；部署後可用來補歷史 Carry 與檢查持倉差異。
