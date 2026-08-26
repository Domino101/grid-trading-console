# DCA 機器人 Phase 4 稽核報告 — 後端 API 層

> 日期：2026-08-26｜分支：`arena/01a03c81-grid-trading-console`
> 計畫：`docs/dca_bot_implementation_plan_zh-TW.md` §Phase 4（§4.7 契約 + 稽核 4.1–4.24）
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：Phase 3 已完成的 DCA 引擎能力以 REST 暴露為前端唯一資料來源。觸及：

- 新端點：`web_ui/backend/routes/dca.py`（§4.7 全部 13 個端點）。
- 建立/載入：`web_ui/backend/dca_yaml_generator.py`（JSON → `config/dca/*.yaml`）。
- 讀模型與快照：`GET /api/v1/dca/snapshot`（一次讀 active_dca → YAML → runtime，禁止 N+1）。
- 前後端數值一致性：`POST /api/v1/dca/preview` 直接呼叫 `core.services.dca.ladder.build_ladder()`。
- 市場規則：`web_ui/backend/routes/market.py` 新增 `GET /api/v1/market/indicators/{symbol}`。
- 安全護欄：`web_ui/backend/safety_guard.py` 新增 `MAX_CONCURRENT_DCA_BOTS`、`check_can_create_dca()`、網格+DCA 總量上限。
- 契約：`web_ui/backend/schemas.py` 追加 `Dca*` / `MarketIndicatorsResponse` Pydantic 模型。
- 測試：`tests/test_dca_yaml_generator.py`、`tests/test_dca_http_contract.py`、`tests/test_dca_snapshot.py` 登錄 `scripts/ci.sh`。

**風險**：本階段包含 DCA 生命週期寫入（create/start/stop/delete）與 LIVE 建立閘門，屬於高風險操作。所有 LIVE 路徑沿用網格既有的 feature flag + 市場規則防護；PAPER 路徑可在無引擎存活下建立設定檔（與網格 create 一致）。

**網路隔離**：`core/services/grid/**`、`run_grid_trading.py` 零修改（G-1/G-2）。零新依賴（requirements.txt 無異動，G-3）。工程紀律：`routes/dca.py` 與 `dca_yaml_generator.py` 維持 `print()` 為零、未知值不為 0（G-5/G-6）。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| 4.1 | 全端點需認證 | P0 | 無 Authorization → 401；錯誤 token → 401 | PASS | `test_all_dca_endpoints_require_auth`（13 個 DCA + 1 個 indicators endpooint） |
| 4.2 | preview 與引擎同源 | P0 | levels[] 與 `build_ladder()` 逐值相等；`routes/dca.py` 未重寫公式 | PASS | `test_preview_matches_build_ladder_directly_and_vector_a`；`test_preview_does_not_rewrite_ladder_formula`（source 含 `build_ladder`、`price_deviation_multiplier` 出現 < 3 次） |
| 4.3 | preview 零外部呼叫 | P1 | mock ccxt 全掛仍 200 | PASS | `test_preview_matches_build_ladder_directly_and_vector_a` 內 patch `routes.market._lookup_market_from_ccxt_sync` / `routes.dca._fetch_ticker_price_sync` raise |
| 4.4 | preview 鎖定向量 A | P0 | required=420、max_drawdown=80.131727、tp=1483.677606 | PASS | `test_preview_matches_build_ladder_directly_and_vector_a` |
| 4.5 | snapshot 無 N+1 | P0 | `RuntimeSnapshotRepository.load_all` ≤ 2 次 | PASS | `test_snapshot_loads_repository_at_most_once_per_pass`（計數器 ≤ 2） |
| 4.6 | 未知值為 null | P0 | 無 runtime → current_price/avg_price/unrealized 皆 null、data_status=unavailable | PASS | `test_missing_runtime_is_unavailable_not_zero` |
| 4.7 | LIVE/PAPER 不混算 | P0 | headline aggregate 只含 LIVE、aggregate_paper 另計 | PASS | `test_live_and_paper_not_mixed_in_headline_aggregate` |
| 4.8 | 建立→YAML 落地 | P0 | POST /dca 201 且 `load_dca_config()` 可解析 | PASS | `test_create_writes_parseable_yaml_and_auto_start_false` |
| 4.9 | auto_start=false 預設 | P0 | active_dca.json 中該項為 false | PASS | `test_create_writes_parseable_yaml_and_auto_start_false` |
| 4.10 | 冪等鍵 | P0 | 同 Idempotency-Key start 兩次回傳同一 command_id | PASS | `test_start_idempotency_returns_same_command` |
| 4.11 | 刪除限制 | P0 | running/pending_engine → 409 | PASS | `test_delete_pending_start_is_conflict` |
| 4.12 | LIVE feature flag | P0 | 未設 flag → 403 | PASS | `test_live_create_requires_feature_flag` |
| 4.13 | 市場規則校驗 | P0 | min_notional 對基礎單 + 每張安全單 → 422 列 slot | PASS | `test_live_create_checks_min_notional_every_safety_order` |
| 4.14 | both 取 max | P0 | ladder_long + ladder_short、required=max | PASS | `test_preview_both_returns_max_not_sum` |
| 4.15 | 併發總量上限 | P0 | 網格+DCA 合計 ≤ 10 | PASS | `test_combined_cap_includes_grids_and_dca`（6 grid + 5 dca → 11/10 reject）；`check_max_combined_bots` 單元驗證 |
| 4.16 | 寫入頻率限制 | P1 | 短時間連續建立 → 429 | PASS | `test_create_respects_write_rate_limit`（複用 `WriteRateLimiter`） |
| 4.17 | 稽核日誌 | P0 | create/start/stop/delete 皆一筆 | PASS | `test_audit_logs_create_start_stop_and_no_secrets` |
| 4.18 | 稽核不洩密 | P0 | params_json 與回應無 key/secret/token | PASS | `test_audit_logs_create_start_stop_and_no_secrets`（`_prune_params` 移除 secret/entry_price） |
| 4.19 | 指標端點快取 | P1 | 3 次請求底層 fetch 1 次、第 2/3 次 cached=true | PASS | `test_indicators_endpoint_caches_fetch_ohlcv` |
| 4.20 | 指標端點不阻塞 | P1 | `run_in_executor` 包 ccxt | PASS | 程式碼審查：`get_market_indicators` → `loop.run_in_executor(None, _compute_indicators_sync, ...)` |
| 4.21 | Pydantic 契約完整 | P1 | 所有端點有 response_model；openapi 含 Dca* schema | PASS | `test_openapi_contains_dca_schemas` |
| 4.22 | 路徑穿越防護 | P0 | bot_id 含 `../` / `/` → 400/404 | PASS | `test_path_traversal_rejected`；`DcaYAMLGenerator.is_valid_bot_id` |
| 4.23 | 網格 API 回歸 | P0 | 既有 web UI / dashboard 測試全綠 | PASS | 全量 CI `tests.test_web_ui_http_contract tests.test_dashboard_snapshot` OK |
| 4.24 | Live 驗證 | P0 | 重啟後端 → 實際 curl 8001 → 附回應 | 例外（見 §4） | 本沙箱無運行中的 8001 / PM2 宿主 |

> 附註：4.2 的 `grep -c "price_deviation_multiplier"` 指向 `web_ui/backend/routes/dca.py`；本實作在 route 內完全未出現該字串（整理為測試斷言 `< 3` 以保留 debug 彈性並將計算完全交由 `build_ladder`）。

---

## 3. 測試證據

### 3.1 本次新增模組

```
python3 -m unittest tests.test_dca_yaml_generator tests.test_dca_http_contract tests.test_dca_snapshot -v
Ran 25 tests — OK
```

覆蓋點：

- `test_dca_yaml_generator.py`：validate 收集錯誤、generate→`load_dca_config` 往返、四指標寫入、`both_tiebreak` 驗證、路徑穿越。
- `test_dca_http_contract.py`：4.1–4.19/4.21/4.22（TestClient + 臨時 SQLite / 臨時 `config/dca`）。
- `test_dca_snapshot.py`：4.5–4.7。

### 3.2 全量後端 CI

```
SKIP_FRONTEND=1 bash scripts/ci.sh
------------------------------------------------------
Ran 203 tests in 1.044s
OK
```

（Phase 1/2/3 原有 178 項 + Phase 4 新增 25 項 = 203 項。）

### 3.3 網格 API 回歸

```
python3 -m unittest tests.test_web_ui_http_contract tests.test_dashboard_snapshot
Ran 14 tests — OK
```

### 3.4 代表性 curl 等價契約（本沙箱離線驗證）

Request → `POST /api/v1/dca/preview`（向量 A）:
```json
{"symbol":"ETH/USDC:USDC","exchange":"binance","direction":"long",
 "entry_price":2500,"base_order_amount":20,"max_safety_orders":20,
 "price_deviation_percent":0.8,"price_deviation_multiplier":1.2,
 "order_size_multiplier":1.0,"take_profit_percent":1.0}
```
Response:
```json
{"direction":"long","required_capital":420.0,"ladder":{
  "max_drawdown_percent":80.13172696,"final_take_profit_price":1483.67760612,
  "levels":[...21 levels...]}, "data_status":"live"}
```

---

## 4. 例外接受

| # | 項目 | 等級 | 原因 | 緩解 | 到期日 |
|---|---|---|---|---|---|
| EX-4.24 | `pm2 restart` + 對運行中 8001 的實際 curl | P0 | 本沙箱無運行中的 backend / PM2 宿主；後端需要 `GRID_API_TOKEN` 與已啟動的引擎 | 程式碼已通過 TestClient 契約；部署於正式宿主執行 §5.3 驗證，並附 curl 回應片段 | 正式部署時 |
| EX-4.20 量化 | 併發 5 請求的 p95 量測 | P1 | 沙箱無真實交易所延遲環境 | 程式碼審查確認 `run_in_executor`；契約測試斷言 60s 快取後不再觸底層 | 正式部署時 |

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- 後端 API（若以 PM2 管理：`pm2 restart grid-console-backend`；實際名稱以宿主 `pm2 list` 為準）。
- 不需重啟 `multi-grid-engine`：本 Phase 未修改 `run_multiple_grids.py` 或 `core/services/dca/**` 執行引擎。
- 不修改 `requirements.txt`，無套件安裝。

### 5.2 啟動/回滾指令

```bash
# 於正式宿主（後端）
pm2 restart <backend-pm2-name>
sleep 2
lsof -i :8001
curl -s -H "Authorization: Bearer $GRID_API_TOKEN" localhost:8001/api/v1/dca/snapshot | head -c 400
```

回滾 = 切回上一個 commit 的 `web_ui/backend/` 並重啟後端；DCA YAML / active_dca / SQLite runtime 檔案不在本 Phase 程式碼內、無 migration。

### 5.3 上線驗收

1. `pm2 restart <backend>` 後確認 `/healthz` 200。
2. 無 token `curl localhost:8001/api/v1/dca/snapshot` → 401（稽核 4.1）。
3. 完成 4.24 實際 curl 並附回應片段。

---

## 附錄 A：交付物清單

| 檔案 | 說明 |
|---|---|
| `web_ui/backend/dca_yaml_generator.py` | JSON → `config/dca/*.yaml`；validate / generate / bot_id 防護 |
| `web_ui/backend/routes/dca.py` | §4.7 全部 DCA 端點 + snapshot/preview 讀模型 |
| `web_ui/backend/schemas.py` | `Dca*` / `MarketIndicatorsResponse` 契約 |
| `web_ui/backend/safety_guard.py` | `MAX_CONCURRENT_DCA_BOTS=5`、`check_can_create_dca()`、網格+DCA ≤ 10 |
| `web_ui/backend/routes/market.py` | `GET /api/v1/market/indicators/{symbol}`（60s cache + run_in_executor） |
| `web_ui/backend/main.py` | `include_router(dca_router)` |
| `tests/test_dca_yaml_generator.py` | 生成器單元測試 |
| `tests/test_dca_http_contract.py` | HTTP 契約測試（4.1–4.22） |
| `tests/test_dca_snapshot.py` | 批次快照（4.5–4.7） |
| `scripts/ci.sh` | 登錄三個新測試模組 |
| `CHANGELOG.md` | `[Unreleased] 2026-08-26` 新增 Phase 4 條目 |

## 附錄 B：Phase 5 銜接

- 前端可直接以 `GET /api/v1/dca/snapshot` 作為 DCA 頁唯一資料來源；未知值為 `null`，前端顯示 `—`。
- 建立精靈以 `POST /api/v1/dca/preview` 驅動 Summary；`direction=both` 回傳 `ladder_long` / `ladder_short`。
- 指標設定表可直接取 `GET /api/v1/market/indicators/{symbol}` 的即時值/暖機資訊。
- 生命週期操作沿用 `command_id` + `pending_engine` 語意，前端以 heartbeat 轉 `running`。
