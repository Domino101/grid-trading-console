# Phase 2 修復工作日誌（對應稽核報告 2026-08-31）

> 日期：2026-08-31｜分支：`arena/01a056af-grid-trading-console`
> 前置文件：`docs/phase2_audit_report_20260831_zh-TW.md`（F1–F10 缺陷清單與實證）
> 範圍：修復 Phase 2 生產化資料層稽核發現之全部可修項目（F1–F9；F10 補實證），並複審 Phase 2 Gate

---

## 0. 修復總覽

| ID | 等級 | 狀態 | 修復方式（一句話） |
|---|---|---|---|
| F1 | P0 | ✅ 已修復＋實證 | `postgresql://` URL 自動正規化為 `postgresql+psycopg://`；`is_postgres_url()` 接受驅動限定形式 |
| F2 | P1 | ✅ 已修復＋實證 | 非預設 ledger 路徑 → 獨立 PG schema（`ledger_<sha1[:12]>`）；ci.sh 加 `PG_TEST_URL` leg；env 敏感測試補 env 衛生 |
| F3 | P1 | ✅ 已修復＋實證 | PG 模式 `_connect()`/`_ensure_schema(conn)` 改為 raise；測試改走方言無關的 store 讀取 |
| F4 | P2 | ✅ 已修復＋實證 | `RedisStateCache`/`RedisLeaderElection` 未傳 ttl 時讀取 `GRID_REDIS_STATE_TTL`/`GRID_REDIS_LEADER_TTL` |
| F5 | P2 | ✅ 已修復＋實證 | Pub/Sub 訂閱指數退避自動重連；訂閱 handle 提供 `close()`；API 關閉不再洩漏 traceback |
| F6 | P2 | ✅ 已修復＋實證 | 前端 `services/events.ts` 接入 `/ws/grid-events`；Dashboard 事件驅動刷新；TopBar 顯示「即時/輪詢」 |
| F7 | P2 | ✅ 已修復＋實證 | fail-open 升級 ERROR log；續約/釋放改 Lua 原子；renew 迴圈追蹤失敗、喪失所有權即報錯並嘗試重取 |
| F8 | P3 | ✅ 已修復＋實證 | OCC insert 保留來源 version；遷移搬運 identity events 並去重 |
| F9 | P3 | ✅ 已修復＋實證 | requirements.txt 補 `rich==13.7.1` |
| F10 | P3 | ✅ 補實證 | 雙環境（有/無 PG+Redis）`/healthz` OK、引擎熱待命 OK、log 無 traceback |

**複審後 Gate 結果**：SQLite 391/391（含 7 個新測試）｜PG 38 模組全綠（自我隔離、零外部 fixture）｜PG live factory 測試 12/12｜Redis 演練 15/15｜前端 98 tests + lint 0 errors（僅存 1 個基準線既有 warning）+ build 通過。**Phase 2 Gate：通過。**

---

## 1. 逐項修復細節

### F1（P0）PostgreSQL URL 驅動正規化

**檔案**：`core/infrastructure/database/ledger_store.py`、`migrations/env.py`

- `is_postgres_url()`：接受 `postgres://`、`postgresql://`、`postgresql+<driver>://`（原本 `+psycopg` 形式被誤判為非 PG → 靜默回落 SQLite）。
- 新增 `normalize_postgres_url()`：bare URL 以 regex 改寫為 `postgresql+psycopg://`（repo 唯一 pin 的驅動），並記 INFO log；驅動限定 URL 原樣保留。
- `PostgresLedgerStore.__init__` 與 `create_ledger_store()` 皆經正規化；`migrations/env.py` 在設定 `sqlalchemy.url` 前同步正規化。
- **實證**：解除安裝 psycopg2-binary 後（模擬乾淨環境），以 `.env.example` 原文 URL `postgresql://grid:grid@127.0.0.1:5432/grid` 建立 store → `PostgresLedgerStore`，寫入/讀取成功；`alembic upgrade head` 對 PG 直接成功。

### F2（P1）PG 測試隔離

**檔案**：`core/infrastructure/database/ledger_store.py`、`core/infrastructure/database/guard.py`、`scripts/ci.sh`、`tests/test_phase2_ledger_orchestration.py`

- `EnvironmentGuard.default_database_path()`：抽出生產預設路徑常數。
- `create_ledger_store()`：`DATABASE_URL` 為 PG 時，非預設的 `database_path`（＝測試/工具的暫存路徑）映射至 `ledger_<sha1(abspath)[:12]>` schema，以 `options=-csearch_path=<schema>,public` 掛到 engine；`public` 留給生產與 Alembic。語意與「SQLite 每路徑一檔」完全一致。
- `PostgresLedgerStore` 新增 `schema` 參數，init 時 `CREATE SCHEMA IF NOT EXISTS`。
- `scripts/ci.sh`：模組清單改為陣列；新增可選 PG leg（`PG_TEST_URL` 未設即跳過，註解附 docker run 範例）。
- `tests/test_phase2_ledger_orchestration.py`：setUp/tearDown 清 `DATABASE_URL`（原本殘留 env 使 3 個「預設=SQLite」斷言在 PG 環境必失敗）。
- **實證**：**不做事前 schema 重置**，PG 模式模組級 38/38 全綠（稽核時為 19 失敗）；live-PG factory 測試（`TEST_PG_URL` gate）12/12。

### F3（P1）關閉 PG 模式的原生 SQLite 後門

**檔案**：`core/infrastructure/database/ledger_repository.py`、`tests/test_identity_audit.py`

- `_connect()` 與 `_ensure_schema(connection)` 在非 SQLite store 時 raise `LedgerStoreError`（中文訊息指明改用 `repo.store`），杜絕「寫 PG、查 SQLite」的雙來源分歧。
- `test_no_unknown_placeholder_rows` 改用 `repo.get_all_bots()`（方言無關），保留原斷言意圖。
- **實證**：PG 套件中 identity_audit 模組全綠；`test_raw_sqlite_connect_rejected_in_postgres_mode` 迴歸測試通過。

### F4（P2）TTL 環境變數接線

**檔案**：`core/infrastructure/database/redis_support.py`

- 新增 `env_int(name, default)`（含非整數告警）。
- `RedisStateCache(ttl=None)` / `RedisLeaderElection(ttl=None)` 未明確傳值時讀 env（300/30 為預設）；引擎與 state store 的既有呼叫端（無參數）自動生效。
- **實證**：`GRID_REDIS_STATE_TTL=60`/`GRID_REDIS_LEADER_TTL=15` 實測 key TTL = 60/15（修復前為 300/30）；單元測試 `test_ttl_env_vars_are_wired`（含 explicit 值覆寫、garbage 值回落預設）。

### F5（P2）Pub/Sub 重連與乾淨關閉

**檔案**：`core/infrastructure/database/redis_support.py`、`web_ui/backend/main.py`

- `RedisEventBus.subscribe()` 重寫：listener 迴圈全程 try/except；斷線後指數退避（1s→30s cap）重連（`reset_redis_client()` 後重新 `get_redis()` + resubscribe）；重連成功記 INFO。
- 訂閱 handle 新增 `close()`（先停迴圈再關 socket）；`main._stop_redis_subscriber()` 優先呼叫 `close()`，join 前不再對已關閉 socket 的 listener 產生未處理例外。
- **實證**：Drill I——訂閱存活下重啟 Redis，重啟後事件持續收到（修復前為永久靜默）；TestClient shutdown 無 traceback（修復前必有 `Exception in thread grid-redis-events`）。

### F6（P2）前端接入事件推送

**檔案**：`web_ui/frontend/src/services/events.ts`（新）、`Dashboard.tsx`、`TopBar.tsx`、`tests/events.test.ts`（新）、`package.json`

- `connectGridEvents()`：同源相對 URL `ws(s)://<host>/ws/grid-events?token=...`；斷線指數退避重連；`isRefreshableEvent()` 純函式判定 `runtime`/`lifecycle`。
- `Dashboard`：事件觸發 150ms debounce 的 `fetchSnapshot()`（併發保護沿用 `inFlight`）；WS（重）連上時排程一次對帳刷新；輪詢（15s + backoff、tab hidden 降頻）保持不變、仍為權威來源。
- `TopBar`：新增「事件: 即時/輪詢」遙測點（`Radio` icon + tooltip），不只靠顏色傳達（有文字標籤）。
- **實證**：`npm test` 98/98（含 3 個新案例）、`npm run lint` 0 errors（1 個 warning 為基準線既有、非本次檔案）、`npm run build` 通過；後端橋接 Drill G 實測事件延遲 110ms。

### F7（P2）領導者選舉強化

**檔案**：`core/infrastructure/database/redis_support.py`

- `acquire()`：Redis 已設定但不可達 → **ERROR** log（fail-open 語意保留，ADR-2 + 進程鎖仍在）；操作例外亦 ERROR。
- `renew()`/`release()`：改 Lua compare-and-act（`GET==me` 才 `EXPIRE`/`DEL`），消除競態窗口；`renew()` 回傳所有權 bool。
- `_renew_loop()`：連續 3 次續約失敗記 ERROR（雙領導風險警示）；所有權喪失時嘗試 SETNX 重取，成功記 WARNING、持續喪失記 ERROR（一次）。
- **實證**：演練 15/15（A/B 互斥、release 後 B 接管、Redis 停機 fail-open + ERROR log 可見）。

### F8（P3）遷移保真度

**檔案**：`core/infrastructure/database/ledger_store.py`、`scripts/migration/sqlite_to_postgres.py`

- `update_position_occ()` insert 分支：`version = max(1, position.version)`（來源 version 隨遷移保留；既有呼叫端皆 version=1，行為不變）。
- 遷移腳本：搬運 `bot_identity_events`，以 `(event_type, recorded_at, config_path, old, new)` 簽名去重（`register_or_update_bot` 的合成 `created` 不重複）；stats 新增 `identity_events` 計數。
- **實證**：種子 version=7 → PG version=7；`renamed` 事件遷移、created 無重複；二次重跑 fills=10 不重複、identity_events=0。

### F9（P3）rich 補 pin

`requirements.txt` 基礎設施區塊新增 `rich==13.7.1`。**實證**：乾淨 venv 只補 rich 後 SQLite 全套 391/391。

### F10（P3）雙環境啟動實證

- Web API：`/healthz` 在（A）純 SQLite、（B）PG+Redis 兩種 env 下皆回 `{"status":"ok"}`，log 無 traceback。
- 引擎：`run_multiple_grids.py` 熱待命在 PG+Redis env 正常進入運行狀態，無錯誤。

---

## 2. 測試與證據清單

| 證據 | 指令/腳本 | 結果 |
|---|---|---|
| SQLite 回歸 | `scripts/ci.sh` 同 38 模組（SKIP_FRONTEND） | **391 tests OK**（384 舊 + 7 新，2 個 PG live 測試 skip） |
| PG 模組級（Gate 二） | `DATABASE_URL=postgresql://…` 逐模組，無外部 fixture | **38/38 OK** |
| PG live factory 測試 | `TEST_PG_URL=postgresql://…` | **12/12 OK** |
| Redis 演練（Gate 三） | 演練腳本（write/read-through、TTL、停機降級、pubsub、領導者互斥、恢復自癒） | **15/15 PASS** |
| Redis 重啟自癒 | Drill I（訂閱存活 × Redis 重啟） | 修復前 False → **修復後 True** |
| 遷移冪等 + 保真 | 種子 1 bot/version=7/renamed/10 fills × 重跑 | version=7 保留、events 去重、fills 不重複 |
| WS 橋接 + 關閉 | Drill F/G/H | async×PG 落盤 ✓、事件 110ms ✓、4401 ✓、shutdown 乾淨 ✓ |
| 雙環境啟動 | `/healthz` × 2 模式 + 引擎熱待命 | 皆 OK |
| 前端 | `npm test` / `npm run lint` / `npm run build` | 98/98、0 errors（1 既有 warning）、build ✓ |

---

## 3. 已知剩餘事項

1. **PG 遷移重跑會使 OCC version +1**（值不變，僅計數前進；CAS 語意不受影響）——可接受，不在本輪處理。
2. `GridCommandRepository`（生命週期命令）仍為 SQLite 直寫（engine/API 兩進程同一路徑，無分歧）——Phase 3 事件化時一併遷移。
3. `alembic` 對「非預設 ledger 路徑的隔離 schema」不感知（測試隔離用 schema 由 store 自行 `create_all`）——生產 `public` schema 與 Alembic 對齊，行為正確；已於程式註解說明。
4. 前端僅 Dashboard 接入事件刷新；DCA 詳情頁等高頻視圖仍為輪詢——後續迭代可複用 `services/events.ts`。
