# Phase 2 生產化資料層稽核報告（PostgreSQL + Redis）

> 日期：2026-08-31｜分支：`arena/01a056af-grid-trading-console`
> 受稽物件：commit `fdcf6f5`（feat(data): Phase 2 production data layer）
> 開發計畫：`docs/data_layer_resilience_plan_zh-TW.md` §5（Phase 2，P2-1～P2-6）
> 部署文件：`docs/phase2_production_ops_zh-TW.md`
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §2/§5（五段式）
> 稽核方式：**靜態逐檔審查 + 真實基礎設施實證**（本沙箱實際部署 PostgreSQL 16.2（`grid:grid@127.0.0.1:5432/grid`）與 Redis 6.2.14（`127.0.0.1:6379`），依 Phase 2 Gate 三項準則逐條重跑）

---

## 0. 總判定

| Phase 2 Gate 準則（計畫 §5） | 判定 | 摘要 |
|---|---|---|
| Phase 1 全綠且不退化 | ✅ **PASS** | SQLite 預設模式，`scripts/ci.sh` 同模組清單 **384/384 OK**（本沙箱實跑，與報告宣稱一致） |
| PG 上重跑全部 DB 測試全綠 | ❌ **FAIL（as shipped）** | **F1（P0）**：照 `.env.example` 設定 `postgresql://` URL 直接崩潰；**F2（P1）**：無 PG 測試隔離，模組級重跑 5 個模組失敗；**F3（P1）**：PG 模式 `_connect()` 仍讀原生 SQLite |
| Redis 停機演練：服務不中斷、資料不丟失 | ✅ **PASS**（附警語） | 讀寫自動降級回 DB、資料零損失、Redis 恢復後讀路徑自癒；但事件訂閱不自癒（F5）、TTL env 未接線（F4） |

> **結論：Phase 2 Gate 未通過（as shipped）。** 阻斷項 F1 修復並補 PG 隔離測試（F2）後即可複審；P2-1/P2-2 的核心抽象、DDL 移植、facts 優先遷移與冪等性、Redis 降級語意**實證正確**，品質良好，問題集中在「驅動接線、測試隔離、事件路徑自癒」三類。

---

## 1. 範圍與風險

**稽核範圍**（對應計畫 §5）：

| 子任務 | 實作檔案 | 稽核深度 |
|---|---|---|
| P2-1 SQLAlchemy + Alembic + LedgerStore 抽象 | `core/infrastructure/database/ledger_store.py`（860 行）、`ledger_repository.py`（facade）、`migrations/`、`alembic.ini` | 逐行 + 雙後端實跑 |
| P2-2 PostgreSQL 真相源 + 遷移 | `PostgresLedgerStore`、`scripts/migration/sqlite_to_postgres.py`、`requirements.txt`（psycopg v3） | 逐行 + 真實 PG 實跑（建表/測試/遷移/冪等） |
| P2-3 Redis 熱快取 | `redis_support.py::RedisStateCache`、`grid_state_store.py`（write-through/read-through） | 逐行 + 實機演練（含停機/恢復） |
| P2-4 Redis Pub/Sub → WebSocket | `redis_support.py::RedisEventBus`、`web_ui/backend/main.py`（`/ws/grid-events`）、`control_plane.py` | 逐行 + 實機 pub/sub 延遲量測 + 前端接線檢查 |
| P2-5 Redis 領導者選舉 | `redis_support.py::RedisLeaderElection`、`run_multiple_grids.py`（grid 323–330、DCA 553–559） | 逐行 + 雙引擎互斥實證 + 停機行為 |
| P2-6 組態與維運 | `.env.example`、`docs/phase2_production_ops_zh-TW.md`、`scripts/ci.sh`、`start_web_ui.sh`、`scripts/deployment/crypto-webui.service` | 對照實際行為逐項驗證 |

**涉及 mode / 資料來源**：本階段為資料層，不涉策略與下單語意；LIVE 維持關閉。資料來源真相序（ADR-4：交易所 > 本地 facts > 快照）未變動。

**風險立場**：
- PG 為「真相源」承諾（ADR-3/ADR-5）——任何「文件說用 PG、實際沒用 PG」的分歧皆以 **P0** 論（錯誤的帳本位置＝不可恢復風險）。
- Redis 為可拋棄層（ADR-2）——Redis 相關缺陷不阻斷放行，但「靜默失效」仍記 P2。

---

## 2. 檢核結果

### 2.1 子任務驗收（P2-1～P2-6）

| # | 檢核項 | 等級 | 結果 | 證據（本沙箱實證） |
|---|---|---|---|---|
| P2-1-a | `LedgerStore` Protocol 定義（write_fill/rehydrate/save_snapshot/OCC/load_by_bot_id/history） | P1 | **PASS** | `ledger_store.py` L84–L106 完整 Protocol；`LedgerRepository` facade 逐方法委派，對外介面不變 |
| P2-1-b | `SqliteLedgerStore` 改 SQLAlchemy Core、schema 語意保留 | P1 | **PASS** | Core Table 定義鏡像 `schema.sql`（7 表 + 7 索引）；測試 `test_ledger_repository`/`test_runtime_repository` 雙模式全綠 |
| P2-1-c | Alembic 納管（schema.sql → migration 001） | P1 | **PASS** | `migrations/versions/0001_initial_schema.py`；`alembic upgrade head` 於 SQLite 與 PG 均實跑成功；PG 端 `\d trade_fills` 確認 `numeric(38,18)`（ADR-5）+ 索引齊全 |
| P2-2-a | DDL 方言移植（`ON CONFLICT`、`NUMERIC`、連線池） | P1 | **PASS** | `_upsert_insert`/`_ignore_insert` 方言分支；`pool_size/pool_pre_ping` 接 env |
| P2-2-b | `DATABASE_URL` 切換、未設定回落 SQLite | P0 | **FAIL（F1）** | 見 §2.2 F1：文件 URL 崩潰；`+psycopg` URL 被 `is_postgres_url()` 拒絕並**靜默回落 SQLite** |
| P2-2-c | 同一組恢復/冪等/單調測試在 PG 全綠 | P1 | **FAIL（F2/F3）** | 見 §3.2 矩陣：模組級 5 模組失敗；逐測試隔離後僅 F3 真失敗 |
| P2-2-d | `sqlite_to_postgres.py` facts 優先、冪等可重跑 | P1 | **PASS** | 實證：種子 3 bots/30 fills/3 positions → 遷移成功；**重跑後 fills 仍 30、無重複**；facts 先於 snapshots 寫入；dry-run 可用。附帶 F8（version/審計事件未隨遷） |
| P2-3 | Redis 熱快取 SETEX + read-through + DB fallback | P1 | **PASS**（附 F4） | 實機演練：write-through 雙 key（`grid:state:{bot_id}`、`grid:state:path:{b64}`）；模擬 API 重啟 read-through 命中；Redis 停機讀寫自動降級、**資料零損失**、恢復後正常 |
| P2-4 | Pub/Sub 事件推送（engine → Redis → WebSocket） | P2 | **PASS**（附 F5/F6） | 實機：`publish_runtime` → Redis → FastAPI → WS client 收到 `runtime` 事件，**延遲 83ms**（< 500ms 門檻）；壞 token 4401 拒絕。但前端零接線（F6）、Redis 重啟後訂閱不自癒（F5） |
| P2-5 | SETNX 領導者選舉（防雙開） | P1 | **PASS**（附 F7） | 實機：engine-A `acquire()`=True → engine-B=False（互斥）；release 後 B 接管；grid/DCA 啟動入口均已接入且 `finally` 釋放。Redis 停機時 fail-open（F7 討論） |
| P2-6 | `.env.example` / 部署維運文件 / 雙環境啟動 | P2 | **PARTIAL** | `.env.example` §3 已含 6 個新變數、ops 文件存在；但 **F4**（兩個 TTL env 無效）、**F9**（`rich` 未列依賴）、**F10**（雙環境啟動驗收無演練證據） |

### 2.2 缺陷清單（依稽核標準分級）

#### F1（P0｜阻斷）PostgreSQL 真相源照文件無法啟用；備援 URL 語法被靜默忽略

- **實證**（沙箱逐一重現）：
  1. `DATABASE_URL=postgresql://grid:grid@127.0.0.1:5432/grid`（`.env.example` L72、ops 文件 §2/§3 原文）→ `PostgresLedgerStore.__init__` → `create_engine()` → SQLAlchemy 預設 psycopg2 方言 → `ModuleNotFoundError: No module named 'psycopg2'`。`requirements.txt` 僅 pin `psycopg[binary]==3.1.19`（v3），**乾淨安裝下必崩**。
  2. 改用 v3 語法 `postgresql+psycopg://...` → `is_postgres_url()`（`ledger_store.py` L253）不認得 → **工廠靜默建立 `SqliteLedgerStore`**。操作者以為真相源是 PG，實際帳本寫進本機 SQLite——正是稽核標準 A 項「誤導資料來源」的 P0 情境。
  3. `alembic upgrade head` 對 PG 同因失敗（`engine_from_config` 解析 `postgresql://` → psycopg2）。稽核為繼續取得其餘實證，沙箱**額外安裝 psycopg2-binary**（非 repo 依賴）後，Alembic/PG store/全套測試方可運行。
- **影響**：Phase 2 Gate 第二準則在乾淨環境**不可能通過**；部署文件 §2/§3/§5 全部無法照做。
- **建議修法（擇一，均需補測試）**：
  - A（建議）：`create_ledger_store()`/`PostgresLedgerStore` 內部將 `postgresql://` 正規化為 `postgresql+psycopg://`（SQLAlchemy URL），並讓 `is_postgres_url()` 同時接受兩種形式；`migrations/env.py` 同步正規化。
  - B：`requirements.txt` 改 pin `psycopg2-binary`，文件不變。
  - 無論何者：新增「`create_ledger_store()` 在 `DATABASE_URL` 為 postgres 形式時必須回傳 `PostgresLedgerStore`」的單元測試（此測試若在，F1 不會發生）。

#### F2（P1）PG 模式無測試隔離 →「PG 上重跑全部 DB 測試」模組級必失敗

- **實證**：`DATABASE_URL` 指向 PG 時，`create_ledger_store()` 忽略呼叫端傳入的 `database_path`，所有測試共享同一 PG schema：
  - 模組級（每模組重置 schema）：38 模組中 **5 個 PG 特有失敗**——`test_monotonic_guard`（2 例）、`test_identity_audit`（1 例）、`test_dashboard_snapshot`（1 例）、`test_dca_http_contract`（1 例）、`test_phase2_ledger_orchestration`（3 例）。
  - 逐**測試**重置 schema 後：除 F3 一例外**全部通過**（含 `test_monotonic_guard` 5/5、`test_dashboard_snapshot`、`test_dca_http_contract`）→ 證明是**跨測試污染**而非產品邏輯錯誤。
  - `test_phase2_ledger_orchestration` 3 例為環境敏感測試：斷言「預設=SQLite」「`PostgresLedgerStore(None)` 必須 raise」，未清除 `DATABASE_URL` 即斷言 → 帶 env 跑必失敗。
- **對應計畫落差**：P2-2 驗收「`ci.sh` 增加 PG fixture（Docker/Testcontainers）」被標記為可選而未做，但 Gate 準則「PG 上重跑全部 DB 測試全綠」依賴它。
- **建議修法**：PG fixture（每測試/每模組 schema reset 或 per-test database/schema）；`test_phase2_ledger_orchestration` 進場先 `os.environ.pop("DATABASE_URL", None)` 還原。

#### F3（P1）PG 模式下 `_connect()`／`_ensure_schema()` 仍開啟原生 SQLite（雙來源分歧）

- **實證**：`test_identity_audit.test_no_unknown_placeholder_rows` 單獨跑（乾淨 PG schema）仍失敗——`repo._connect()` 回傳 SQLite 連線，`no such table: bots`（寫入在 PG、檢查讀 SQLite）。`ledger_repository.py` L59–L82 的向下相容路徑在 PG 模式是**幽靈資料源**。
- **現況範圍**：全倉庫掃描確認生產碼目前**無** `_connect()` 呼叫者（僅測試/稽核工具面），故降為 P1 而非 P0；但該 API 是公開面，任何操作者/審計腳本照 Phase 1 慣例使用即讀到錯誤來源。
- **建議修法**：PG 模式下 `_connect()`/`_ensure_schema()` 直接 `raise LedgerStoreError("PostgreSQL backend: use repo.store / SQLAlchemy")`，並把該測試改查 `store` 或於 PG 模式 skip。

#### F4（P2）`GRID_REDIS_STATE_TTL`／`GRID_REDIS_LEADER_TTL` 文件宣稱生效，實際未接線

- **實證**：`GRID_REDIS_STATE_TTL=60` + `GRID_REDIS_LEADER_TTL=15` 實測 key TTL = **300 / 30**（預設值）。`grid_state_store.py` L24 `RedisStateCache()`、`run_multiple_grids.py` L325/L554 `RedisLeaderElection(bot_id)` 均未讀 env。`.env.example` L84–L88 與 ops 文件 §2/§4 的宣稱不成立。
- **建議**：兩處建立物件時讀 env（`int(os.getenv(...))`），並加一個「env 值反映到實際 TTL」的測試。

#### F5（P2）Redis 重啟後事件訂閱不自癒；API 關閉時訂閱執行緒印未處理 traceback

- **實證**：Drill I——訂閱後重啟 Redis，重啟前事件可收、**重啟後事件永遠收不到**（`grid-redis-events` 執行緒隨連線死亡，無重連）。另 API shutdown 時 `_stop_redis_subscriber()` 的 `pubsub.close()` 觸發 listen 執行緒 `ValueError: I/O operation on closed file` 未處理例外（實測可重現）；`join(timeout=2)` 也可能收不掉屍。
- **影響**：讀路徑降級正確，但 `/ws/grid-events` 推送在 Redis 重啟後**靜默停擺**（前端若未來接線，會呈現假死）；與 ops 文件「Redis 停機自動降級…服務不會中斷」的承諾在事件面不符。
- **建議**：subscribe 迴圈包 try/except + 退避重連（重新 `get_redis()`）；`reset_redis_client()` 時一併關閉舊 pubsub；關閉流程先 `unsubscribe()` 再 `close()`。

#### F6（P2）P2-4 前端零接線：`/ws/grid-events` 沒有任何 UI 消費者

- **實證**：`grep -r "grid-events" web_ui/frontend/src/` → 0 命中。後端 WS 端點 + Redis 橋接已可用（83ms 實測），但前端仍純輪詢。計畫 P2-4 驗收「UI 收到事件延遲 < 500ms」的 **UI** 面未成立。
- **建議**：前端接 `/ws/grid-events` 作 dashboard 增量更新（保語意：斷線後以 snapshot 全量追補，符合「斷線後由 facts 重建」）。

#### F7（P2）領導者選舉 fail-open 與非原子續約/釋放

- **實證**：Redis 停機時 `acquire()` 經 `safe_redis(_do, True)` 回 True（防雙開靜默失效，僅 WARNING log）；Redis 恢復後，停機期間啟動的引擎**不會重新驗權** → 理論雙領導。`renew()`＝GET+EXPIRE、`release()`＝GET+DELETE 皆非原子（競態窗口可讓他人 key 被續約/刪除）。
- **定性**：fail-open 符合 ADR-2（Redis 可拋棄、不擋單機啟動）+ 進程鎖/pgrep 掃描仍在（`run_multiple_grids.py` L102–L155），單機場景可接受；但「靜默」不符稽核標準可觀測性要求。
- **建議**：Redis 設定但不可達時升級 ERROR log + 計數指標；恢復後於 renew 失敗 N 次時重驗所有權；續約/釋放改 Lua（`if GET==me then EXPIRE/DEL`）。

#### F8（P3）遷移腳本不保留 OCC `version`、不搬 `bot_identity_events`

- **實證**：`update_position_occ()` 首次寫入走 insert 分支，version 恆為 1（來源 version>1 時，遷移後首次 CAS 需重新對齊，fail-closed 安全但不連續）；PG 側 `bot_identity_events` 由遷移動作另生 `created` 事件，SQLite 時代審計軌跡出現斷點。
- **建議**：insert 分支保留來源 version；遷移補搬 identity events（或於 ops 文件聲明斷點）。

#### F9（P3）`rich` 為硬依賴（`core/services/grid/__init__.py` 無條件 import）但 requirements.txt 未 pin

- **實證**：乾淨 venv 跑 `scripts/ci.sh` 模組清單 → 多個測試 `ModuleNotFoundError: No module named 'rich'`；補裝後 384/384。requirements 僅於註解提及 rich。
- **建議**：requirements.txt 增加 `rich` pin（或在 `core/services/grid/__init__.py` 延遲/容錯 import）。

#### F10（P3）P2-6「有/無 PG+Redis 雙環境正常啟動」驗收無演練證據

- **實證**：`start_web_ui.sh`/systemd 單元在本沙箱未實跑（沙箱以 venv `.venv`、無前端 build；後端 import、384 測試、WS 橋接、guard 隔離皆實證通過）。文件面：`.env.example`、`crypto-webui.service`（`EnvironmentFile=-/opt/crypto-trading-open/.env`）完備。
- **建議**：部署時補「`DATABASE_URL`/`REDIS_URL` 清空 vs 設定」兩種啟動的健康檢查紀錄（ops 文件 §6 指標即可佐證）。

### 2.3 對照稽核標準 §3 檢核表（僅列 Phase 2 適用項）

| 標準項 | 結果 | 說明 |
|---|---|---|
| A 資料來源可見/不以 0 偽造 | ✅ | Phase 1 讀模型未動；PG/SQLite 模式 `data_status` 語意一致（test_dashboard_snapshot 雙模式綠） |
| A 金額精度（ADR-5） | ✅ | PG `trade_fills`/`bot_positions` 金額欄 `numeric(38,18)` 實查確認 |
| D 狀態不僅存 process memory | ✅ | Redis 停機演練證明 DB 為唯一真相、Redis 為純快取 |
| D 外部依賴降級 | ⚠️ | 讀路徑 ✅（實證）；事件路徑不自癒（F5） |
| D API schema/contract | ✅ | `test_web_ui_http_contract`/`test_dca_http_contract` 雙模式綠（PG 需隔離修復，見 F2） |
| F 健康檢查/可觀測 | ⚠️ | 降級有 WARNING log（實測可見）；領導者衝突 ERROR（grid/DCA 入口）；但 TTL env 失效、事件停推無告警（F4/F5） |
| G 回歸/證據 | ⚠️ | SQLite 384/384 ✅；PG 模組級矩陣未全綠（F1/F2）；前端 npm test/lint/build 本沙箱未執行 |

---

## 3. 測試證據

**稽核環境**：Python 3.11 venv（依 `requirements.txt` 核心子集安裝：sqlalchemy 2.0.23 / alembic 1.13.1 / redis 5.0.1 / psycopg[binary] 3.1.19 / fastapi / uvicorn / pandas / numpy / ccxt / eth-account / **rich** / **psycopg2-binary**※）；PostgreSQL 16.2（0.0.0.0:5432，role `grid`、db `grid`，照 ops 文件 §3 建置）；Redis 6.2.14（0.0.0.0:6379）。
（※ psycopg2-binary 為稽核追加，用於繞過 F1 以取得後續實證；repo 依賴未改。）

### 3.1 Gate 一：Phase 1 不退化（SQLite 預設模式）

```text
$ python -m unittest <scripts/ci.sh 全部 38 個 Python 模組>
Ran 384 tests in 24.8s — OK
```

### 3.2 Gate 二：PG 重跑矩陣（`DATABASE_URL=postgresql://grid:grid@127.0.0.1:5432/grid`）

每模組重置 PG schema，與 SQLite 基準逐模組對比：

| 結果 | 模組 |
|---|---|
| 雙模式全綠（33） | lifecycle_and_safety、paper_adapter、command_repository、runtime_repository、**ledger_repository**、database_guard、database_migrator_and_writer、**bot_identity_stability**、backfill_bot_ids、**async_writer_full_save**、**phase1_gate_repro**、paper_grid_lifecycle_e2e、dca_ladder/indicators/config_and_state/config_loader/ohlcv_cache/paper_e2e/yaml_generator/snapshot/frontend_contract/kline_repository/replay_exchange/replay_driver/backtest_metrics/backtest_sweep/backtest_http_contract/chart_backtest_e2e |
| PG 特有失敗（5） | **monotonic_guard**（2 例，隔離後全過）、**identity_audit**（1 真實失敗=F3 + 1 污染）、**dashboard_snapshot**（1，隔離後過）、**dca_http_contract**（1，隔離後過）、**phase2_ledger_orchestration**（3，env 敏感測試） |

單測隔離複跑（每測試重置 schema）：`test_monotonic_guard` 5/5 OK、`test_dashboard_snapshot.test_missing_runtime_is_unavailable_not_zero` OK、`test_dca_http_contract.test_orders_endpoint_unavailable_without_runtime` OK、`test_created_event_recorded` OK；唯 `test_no_unknown_placeholder_rows` 持續失敗（=F3）。

### 3.3 Alembic 雙後端

```text
SQLite：alembic upgrade head → 0001_initial_schema ✓（8 表）
PG   ：alembic upgrade head → 0001_initial_schema ✓（※ 需先緩解 F1；沙箱以 psycopg2-binary 後通過）
\d trade_fills → numeric(38,18) ×5、索引 idx_trade_fills_bot_time/client_order ✓
```

### 3.4 遷移腳本（facts 優先 + 冪等）

```text
種子：3 bots / 30 fills / 3 positions / 3 snapshots（SQLite）
--dry-run → {'bots': 3, 'fills': 0, 'positions': 0, 'snapshots': 0}（未寫入）✓
第 1 次遷移 → {'bots': 3, 'fills': 30, 'positions': 3, 'snapshots': 3} ✓
第 2 次重跑 → 同計數；PG 實數：bots=3、trade_fills=30（無重複）、bot_positions=3、bot_snapshots=3 ✓
```

### 3.5 Gate 三：Redis 演練（實機，14/15 檢查通過）

```text
P2-3 write-through 雙 key（grid:state:{bot_id} + grid:state:path:{b64}）      PASS
P2-3 GRID_REDIS_STATE_TTL=60 實際生效                                        FAIL（實測 300）→ F4
P2-3 模擬 API 重啟後 read-through 命中                                        PASS
── kill Redis（停機演練）──
P2-3 get()/update() 自動降級回 DB，資料零損失                                 PASS（log 出現預期 WARNING）
P2-5 acquire() Redis 停機時放行（fail-open，設計如此）                        PASS → F7
P2-4 publish() 停機中不拋錯                                                   PASS
── 重啟 Redis ──
P2-3 讀路徑自癒（key 過期 → DB 重建快取）                                     PASS
P2-4 pub/sub → subscribe 回呼往返                                             PASS
P2-5 A 取得 / B 被拒（SETNX 互斥）                                            PASS
P2-5 release 後 B 接管                                                        PASS
── 補充 Drill I ──
Redis 重啟後既有訂閱仍可收事件                                                FAIL → F5
```

### 3.6 端到端補充

```text
Drill F：引擎路徑（AsyncDatabaseWriter 開啟）× PG：publish_runtime ×5 → 批量去重落 PG，
         bots 註冊 ✓、bot_snapshots.cycles=4（最新值）✓
Drill G：engine publish_runtime → Redis → FastAPI lifespan → /ws/grid-events → client 收到
         {"event":"runtime",...}，延遲 83ms（門檻 <500ms）✓
Drill H：/ws/grid-events?token=錯誤 → 4401 拒絕 ✓
```

---

## 4. 例外接受（建議追蹤表）

| ID | 等級 | 阻斷？ | 處置建議 | 建議時程 |
|---|---|---|---|---|
| F1 | **P0** | **是（Phase 2 Gate）** | URL 正規化（`+psycopg`）或改 pin psycopg2-binary；補「PG 形式必得 PostgresLedgerStore」測試 | 修復後即可複審 Gate 二 |
| F2 | P1 | 是（Gate 二） | ci.sh/測試加 PG fixture（schema reset）；orchestration 測試清 env | 與 F1 同批 |
| F3 | P1 | 否（無生產呼叫者） | PG 模式 `_connect()` raise；測試改查 store | 下一迭代 |
| F4 | P2 | 否 | TTL env 接線 + 測試 | 下一迭代 |
| F5 | P2 | 否 | 事件訂閱重連 + 關閉流程清理 | 下一迭代（前端接 WS 前必做） |
| F6 | P2 | 否 | 前端接 `/ws/grid-events` | 下一迭代 |
| F7 | P2 | 否 | ERROR 升級 + 恢復後重驗權 + Lua 原子化 | 下一迭代 |
| F8/F9/F10 | P3 | 否 | 見 §2.2 | backlog |

---

## 5. 部署／回滾建議

1. **現階段（未修 F1 前）**：**不得**以「照 ops 文件設定 `DATABASE_URL`」的方式部署 PG 模式——會直接崩潰或靜默回落 SQLite。單機 SQLite 模式（不設 `DATABASE_URL`/`REDIS_URL`）已實證 384/384 穩定，可繼續運行。
2. **F1/F2 修復後的 PG 部署順序**（依 ops 文件 §3，本沙箱已預演）：`CREATE ROLE/DB` → `alembic upgrade head` → `sqlite_to_postgres.py`（facts 優先、冪等，重跑安全）→ 切 `DATABASE_URL` → 重啟 Web API/引擎 → 以 §6 指標驗證。
3. **回滾**：實證成立——`DATABASE_URL`/`REDIS_URL` 清空即回 Phase 1 SQLite 模式（384/384）；SQLite 原檔不被 PG 模式破壞（工廠只加不減，PG 模式不寫 SQLite 主檔，唯 `_connect()` 幽靈路徑見 F3）。
4. **Redis 停機演練結論**：可放行——資料零損失、服務不中斷、讀路徑自癒；附條件：事件推送在 Redis 重啟後需重啟 API（F5 修復前，列入部署手冊注意事項）。

---

## 附錄：與開發報告（Progress Tracker）宣稱的對照

| 報告宣稱 | 稽核結果 |
|---|---|
| P2-1「已實作，LedgerRepository 改 facade，介面不變」 | ✅ 屬實（雙模式測試全綠） |
| P2-2「已實作；**PG 實證待部署後驗收**」 | ⚠️ 屬實但低估：一經實證即暴露 F1（P0），「待驗收」實為「不可用」 |
| P2-3「已實作（write-through/read-through）」 | ✅ 屬實（含停機降級；TTL env 宣稱不實＝F4） |
| P2-4「已實作（RedisEventBus + /ws/grid-events）」 | ⚠️ API 層屬實（83ms）；UI 面未接線（F6）、重啟不自癒（F5） |
| P2-5「已實作，grid/DCA 啟動入口接入」 | ✅ 屬實（互斥實證通過；fail-open 取捨見 F7） |
| P2-6「已實作（.env.example／部署維運摘要）」 | ⚠️ 文件屬實；TTL 變數無效（F4）、雙環境啟動無證據（F10） |
| Phase 2 Gate「🗓️ 待部署環境驗證」 | 本報告已完成該驗證：**未通過**，需先修 F1/F2 |
