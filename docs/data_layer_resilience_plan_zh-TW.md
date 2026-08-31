# 資料層韌性強化開發計畫（Phase 1–3）

> 建立日期：2026-08-31
> 分支：`arena/01a05649-grid-trading-console`
> 範圍：`core/infrastructure/database/`、`web_ui/backend/`（`runtime_repository`、`grid_state_store`、`command_repository`、`control_plane`、`lifecycle`、`yaml_generator`、`dca_yaml_generator`）、`run_multiple_grids.py`、`core/services/grid/`、`core/services/dca/`、`core/services/reconciler/`
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md`（P0/P1/P2/P3 等級）
> 對應盤點：commit `eb0190c`（v0.2.0-beta）

---

## 0. 執行摘要

使用者回報：**「常常因為一個小改動、重開機器人時，已開啟的網格及 DCA 機器人的交易紀錄就歸 0 了，十分脆弱。」**

經逐檔盤點確認，資料其實**沒有被刪除**，而是被「換了名字」與「讀錯來源」所拋棄：

1. 機器人身份（`bot_id` / `grid_id`）由 **YAML 檔名 stem** 推導，而檔名又內含**易變參數**（grid：`lower-upper` 價格區間；DCA：`{timestamp}` 時間戳）。小改動 → 檔名變 → 身份變 → 舊帳本被孤兒化。
2. 重啟恢復流程讀的是**會被每 heartbeat 覆寫的最新快照 JSON**（且是 `config_path` 為 key 的 legacy 表），而非**不可變成交流水 `trade_fills`**。
3. grid 的成交根本**沒有逐筆寫入 `trade_fills`**（只給 `recent_fills`），其累計計數只活在會被覆寫的快照裡。
4. 快照 upsert **沒有單調防護**，剛重啟、恢復尚未完成的引擎可用 0 值覆寫累積資料。

本計畫分三階段，每階段皆附**可證偽的驗收準則**，未通過不得進入下一階段：

| Phase | 主題 | 目標 | 新增外部元件 |
|---|---|---|---|
| **1** | 語意止血 | 修「身份漂移」＋「恢復讀錯來源」＋「單調防護」＋「非同步寫入」，**不新增任何外部依賴** | 無 |
| **2** | 生產化 | SQLAlchemy/Alembic 抽象 + PostgreSQL 真相源 + Redis 熱快取/Pub-Sub/領導者選舉 | PostgreSQL、Redis |
| **3** | 事件溯源 + 三路對帳 | 全量事件化 + 交易所真相源自癒，稽核/回放能力 | 無（架構層） |

> **核心決策（ADR-0）**：Phase 1 是 Phase 2/3 的**前置條件**。只換 PostgreSQL/Redis 而不修身份與恢復來源，歸 0 依然會發生——只是「更快地把錯亂的帳本寫進一個更好的引擎」。

---

## 1. 現況盤點（commit `eb0190c`）

### 1.1 既有儲存架構

| 層 | 位置 | 現況 |
|---|---|---|
| 儲存引擎 | `data/web_ui_runtime.sqlite3` | SQLite（WAL，`PRAGMA journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`），engine 與 Web API **雙 process 共用** |
| 正規化帳本 | `core/infrastructure/database/ledger_repository.py` | `bots` / `trade_fills`（append-only）/ `bot_positions`（OCC `version` 樂觀鎖）/ `bot_snapshots` / `bot_snapshot_history` |
| 相容層 | `web_ui/backend/runtime_repository.py` | `RuntimeSnapshotRepository` 包裝 `LedgerRepository`，同時**雙寫** legacy 表 `grid_runtime_snapshots` / `grid_runtime_history` |
| 進程快取 | `web_ui/backend/grid_state_store.py` | 記憶體 dict，`REFRESH_TTL=0.5s` 定期回讀 SQLite |
| 非同步寫 | `core/infrastructure/database/async_writer.py` | `AsyncDatabaseWriter` **已實作但主路徑未接**（全倉庫只有其自身引用 `enqueue_snapshot`） |
| 遷移引擎 | `core/infrastructure/database/migrator.py` | `DatabaseMigrator.migrate()` legacy JSON → 正規化表 |
| 環境防護 | `core/infrastructure/database/guard.py` | `EnvironmentGuard` 隔離測試與生產 DB |
| 身份推導 | `web_ui/backend/yaml_generator.py`、`dca_yaml_generator.py` | `grid_id`/`bot_id` = `Path(config_path).stem` |
| 自癒 | `core/services/reconciler/exchange_reconciler.py` | `ExchangeReconciler` 已存在，**僅 DCA 接上、且只救持倉/TP 不救歷史累計** |

### 1.2 Schema 現況（`core/infrastructure/database/schema.sql`）

- `bots(bot_id PK, strategy_type, symbol, exchange, account_mode, config_path, status, created_at, archived_at)`
- `trade_fills(fill_id PK, bot_id FK→bots, exchange_order_id, client_order_id, side, role, price, quantity, fee, fee_currency, realized_pnl, executed_at)` — append-only
- `bot_positions(bot_id PK, active_direction, quantity, average_cost, total_cost, entry_price, completed_cycles, realized_profit, total_fees, version, updated_at)` — OCC
- `bot_snapshots(bot_id PK, config_path, payload JSON, version, updated_at)` + `bot_snapshot_history`
- Legacy：`grid_runtime_snapshots(config_path PK, payload, updated_at)`、`grid_runtime_history`

> 關鍵觀察：**正規化表本來就以 `bot_id` 為 key 且結構正確**（`trade_fills` 已是 append-only、`bot_positions` 已有 OCC、`rehydrate_position_from_fills()` 也已存在），但**恢復路徑卻走 `config_path` 為 key 的 legacy 表與快照 JSON**，導致正確的資產閒置未用。

### 1.3 根因（可證偽）

| # | 根因 | 證據（檔案） | 嚴重度 |
|---|---|---|---|
| R1 | 身份綁在易變檔名 | grid 檔名 `{exchange}-{type}-perp-{symbol}-{lower}-{upper}.yaml`（`yaml_generator.py:207`）；DCA 檔名 `dca-{exchange}-{direction}-{symbol}-{timestamp}.yaml`，timestamp=`%Y%m%d%H%M%S`（`dca_yaml_generator.py:90`） | **P0** |
| R2 | 恢復讀「最新快照」而非「不可變成交流水」 | `grid_coordinator.py:478 recover_old_orders`、`dca_coordinator.py:102 recover_state_from_snapshot` 都只讀 `RuntimeSnapshotRepository.load_all()`；`rehydrate_position_from_fills()`（`ledger_repository.py:294`）從未被呼叫 | **P0** |
| R3 | grid 成交未逐筆入 `trade_fills` | `runtime_repository.py` 只抽 `stats.get("fills")`，但 grid 只給 `recent_fills`（`run_multiple_grids.py:449`）；DCA 有給 `fills`（`run_multiple_grids.py:593`）故有入表 | **P0** |
| R4 | 快照 upsert 無單調防護 | `ledger_repository.py:376 save_snapshot` 為 `ON CONFLICT DO UPDATE`，無「新值不得倒退」或「恢復完成前禁寫」防護 | P1 |
| R5 | 身份 placeholder 漂移 | `record_fill` / `update_position_occ` 在 bot 不存在時插 `strategy_type='unknown'` placeholder | P1 |
| R6 | 每 heartbeat 同步寫 4+ 條 SQL + 2 次 history + 2 次 prune | `save_snapshot` 每次呼叫做 6–8 條語句；`AsyncDatabaseWriter` 閒置 | P2 |

---

## 2. 目標與非目標

### 2.1 目標（Goal）

- **G-1**：任何「小改動」（改價格區間、重設、重開、重命名）**不得**導致已累積的交易紀錄歸 0。
- **G-2**：重啟後，grid 與 DCA 的「已實現盈虧、循環數、成交筆數、持倉、均價」**可由不可變帳本重建**，且與交易所（LIVE）對帳一致。
- **G-3**：資料層可在不變更業務邏輯下，從 SQLite 切換到 PostgreSQL（真相源）＋ Redis（快取/協調）。
- **G-4**：所有寫入路徑具備冪等與單調保護；跨 process 併發安全。

### 2.2 非目標（Non-goal，本計畫不做）

- 不改交易策略演算法、指標、回測邏輯（除非與資料層直接衝突）。
- 不新增交易所或改變下單語意。
- 不做分散式多主機高可用叢集（Phase 2 的 PG/Redis 以單主為前提；多機為後續）。
- 不把 Redis 當交易帳本的事實來源。

---

## 3. 目標架構

```text
┌─ engine (run_multiple_grids.py) ───────────────────────────────┐
│  GridCoordinator / DcaCoordinator                              │
│    └─ fill/order 事件 → LedgerWritePort.write_fill(...)        │
│    └─ heartbeat  → LedgerWritePort.write_snapshot(...)         │
│    └─ 啟動恢復 → LedgerReadPort.rehydrate(bot_id)              │
└───────────────┬───────────────────────────────────────────────┘
                │ (AsyncDatabaseWriter: queue + batch)
                ▼
┌─ Ledger 抽象層（SQLAlchemy，Ports & Adapters）────────────────┐
│  LedgerStore 介面                                              │
│    ├─ SqliteLedgerStore（現行，預設）                          │
│    └─ PostgresLedgerStore（Phase 2）                           │
│  事實表：bots / trade_fills / bot_positions / bot_events       │
│  投影表：bot_snapshots（顯示用，可重建、可拋棄）               │
└───────────────┬───────────────────────────────────────────────┘
                │ 讀取
                ▼
┌─ Web API (FastAPI) ───────────────────────────────────────────┐
│  GridStateStore（in-process cache）                            │
│    → Redis hot cache（Phase 2，SETEX + TTL）                  │
│    → Pub/Sub 廣播 lifecycle/heartbeat（Phase 2）              │
└───────────────────────────────────────────────────────────────┘
```

**資料原則（本計畫不可違背）**：

1. **帳本唯一 key = `bot_id`**（穩定 UUID），`config_path` 僅為人類可讀路徑，可變。
2. **事實（facts）**：`trade_fills` 與 `bot_events`（append-only）＋ `bot_positions`（OCC）。
3. **投影（projection）**：`bot_snapshots` 是顯示用快取，永遠可由 facts 重建。
4. **真相源優先序**：LIVE 時 `交易所 > 本地 facts > 快照`；PAPER 時 `本地 facts > 快照`。
5. Redis 是**可拋棄快取**，其內容永遠可由 PostgreSQL/SQLite＋交易所重建。

---

## 4. Phase 1 — 語意止血（不新增外部依賴）

> 目標：在不引入 Redis/PostgreSQL 的前提下，消除「歸 0」。全部子任務完成並通過驗收後，才進入 Phase 2。

### P1-1　穩定 bot_id（R1）

- **目標**：bot 身份與檔名解耦。
- **做法**：
  1. YAML 新增 `metadata.bot_id`（建立時產生一次 UUID4），grid 與 DCA 皆然。
  2. 解析優先序：`metadata.bot_id` > 檔名 stem（legacy 相容）。
  3. **編輯既有 bot 時必須沿用舊 `bot_id`**：`yaml_generator.generate()` 與 `dca_yaml_generator.generate()` 增加 `existing_bot_id` 參數；UI 編輯流程傳入舊檔的 `bot_id`，不得重新產生。
  4. 提供一次性 backfill 腳本：掃 `config/grid/*.yaml` 與 `config/dca/*.yaml`，缺 `metadata.bot_id` 者補上（值 = 現行 stem，維持既有帳本可對接）。
- **涉及檔案**：`web_ui/backend/yaml_generator.py`、`web_ui/backend/dca_yaml_generator.py`、`core/services/dca/config_loader.py`、`core/services/grid/models/grid_config.py`、編輯路由（`web_ui/backend/routes/grids.py`、`routes/dca.py`）、`scripts/migration/backfill_bot_ids.py`（新增）。
- **驗收**：
  - 改 grid 價格區間（lower/upper）後，`grid_id`（`bot_id`）**保持不變**。
  - 重建 DCA YAML 後，`bot_id` **保持不變**（無 timestamp 漂移）。
  - 既有 YAML 經 backfill 後具備 `metadata.bot_id` 且值穩定。

### P1-2　恢復流程改讀不可變成交流水（R2）

- **目標**：重啟恢復以 `trade_fills`＋`bot_positions`（facts）為來源，快照 JSON 降級為顯示用。
- **做法**：
  1. `RuntimeSnapshotRepository` 新增 `load_by_bot_id(bot_id)` 與 `rehydrate(bot_id) -> BotPosition`（呼叫既有 `LedgerRepository.rehydrate_position_from_fills()`）。
  2. `GridCoordinator.recover_old_orders()` 改為：以穩定 `bot_id` 查 `bots` → `trade_fills` 重建 `filled_buy/sell_count`、`completed_cycles`、`realized_profit`、`total_fees`、`current_position`、`average_cost`；快照僅作 fallback。
  3. `DcaCoordinator.recover_state_from_snapshot()` 同法：`_fills` 由 `trade_fills` 重建，`cycle_count`/`_realized_profit`/`_total_fees` 由 facts 重建。
  4. 移除/隔離「以 symbol 猜測」與「以路徑後綴猜測」的脆弱 fallback，改為「找不到 bot 即建立新身份並記入審計」。
- **涉及檔案**：`core/infrastructure/database/ledger_repository.py`、`web_ui/backend/runtime_repository.py`、`core/services/grid/coordinator/grid_coordinator.py`、`core/services/dca/coordinator/dca_coordinator.py`。
- **驗收**：
  - 模擬「成交 5 筆後重啟」：grid 與 DCA 的計數/盈虧**逐項等於重啟前**。
  - 刪除 `grid_runtime_snapshots` legacy 表內容後，恢復結果**不變**（證明讀的是 facts）。

### P1-3　補齊 grid 不可變成交流水（R3）

- **目標**：grid 成交與 DCA 同等級逐筆 append。
- **做法**：
  1. `PositionTrackerImpl.record_filled_order()`（或 coordinator 成交處理點）逐筆產 `TradeFill(role='grid'|'sl'|...)` 寫入 `trade_fills`。
  2. 引擎 heartbeat 的 `stats_dict` 補上完整 `fills`（與 `recent_fills` 並存，`fills` 供寫入、`recent_fills` 供 UI 顯示），或改由 tracker 直接呼叫 `LedgerRepository.record_fill()` 以避開快照欄位膨脹。
  3. `record_fill` 維持 `INSERT OR IGNORE`（以 `fill_id` 冪等，`fill_id` = exchange fill/order id 或 `client_order_id`+hash）。
- **涉及檔案**：`core/services/grid/implementations/position_tracker_impl.py`、`core/services/grid/coordinator/grid_coordinator.py`、`run_multiple_grids.py`。
- **驗收**：
  - 模擬 3 筆 grid 成交後，`trade_fills` 存在 3 筆 role=`grid` 紀錄。
  - 重複送同一 `fill_id` 不會產生重複列（冪等）。

### P1-4　快照單調防護（R4）

- **目標**：新引擎不得用倒退值覆寫累積計數。
- **做法**：
  1. `save_snapshot` 增加 `monotonic` 檢查：若新 payload 的累計欄位（`completed_cycles`、`realized`、`filled buy/sell count`、`total_fees`）**小於**現存快照，且該 bot 非「剛 reset」，則拒絕覆寫並記 warn log。
  2. 增加 `recovered` 旗標：coordinator 完成恢復前，heartbeat 不寫入 facts 的累計欄位（只更新 `current_price` 等即時欄）。
  3. grid 的重置（止盈/本金保護/價格脫離）是**合法歸零**，需以 `reset_id`/事件標記豁免單調檢查（為 Phase 3 事件化埋點）。
- **涉及檔案**：`core/infrastructure/database/ledger_repository.py`、`web_ui/backend/runtime_repository.py`、`core/services/grid/coordinator/grid_reset_manager.py`。
- **驗收**：
  - 手動以「較低計數」的 payload 寫入，快照**保持較高值**且 log 有 warn。
  - 合法 reset 仍可歸零（不被單調防護擋死）。

### P1-5　接上 AsyncDatabaseWriter（R6）

- **目標**：heartbeat 寫入走 queue + batch，消除每 heartbeat 6–8 條語句的同步寫放大。
- **做法**：
  1. `control_plane.publish_runtime()` 改走 `AsyncDatabaseWriter.instance().enqueue_snapshot()`（保留同步 fallback，由 env `GRID_DB_ASYNC_WRITE` 控制）。
  2. 修正 `AsyncDatabaseWriter`：flush 前做 per-bot 去重（已具備）＋ 終止前 `flush_sync`（已具備）；補 flush 失敗重試佇列。
- **涉及檔案**：`core/infrastructure/database/async_writer.py`、`web_ui/backend/control_plane.py`、`run_multiple_grids.py`。
- **驗收**：
  - 壓力測試（10 bot × 30s heartbeat）SQLite 寫入次數顯著下降，無 lock 錯誤。
  - 進程終止時最後一批快照完整落盤。

### P1-6　身份 placeholder 與審計（R5）

- **目標**：不再產生無法歸屬的 placeholder；身份變更可追溯。
- **做法**：
  1. `record_fill`/`update_position_occ` 在 bot 不存在時，改為「先由呼叫方註冊 bot 再寫」，移除 `'unknown'` placeholder；無法註冊時 raise 並記 error（而非靜默）。
  2. `bots` 新增 `bot_identity_events` 審計（或複用 `bot_events`，見 Phase 3）：記錄 `created / renamed / remapped` 與 `config_path` 對應變更。
- **涉及檔案**：`core/infrastructure/database/ledger_repository.py`、`core/infrastructure/database/models.py`、`schema.sql`。
- **驗收**：
  - 不存在任何 `strategy_type='unknown'` 的 bot 列（既有資料 backfill 除外）。
  - 身份 remap 有審計軌跡。

### Phase 1 驗收總則（Gate）

- [ ] 完整回歸：`./scripts/ci.sh` 全綠。
- [ ] 新增單元/整合測試（見 §9）全綠。
- [ ] 手工重現腳本：開 1 台 grid + 1 台 DCA（PAPER）→ 產生成交 → 改 grid 區間 / 重建 DCA → 重啟引擎 → **計數與盈虧不歸 0**。

---

## 5. Phase 2 — 生產化（PostgreSQL + Redis）

> 前置：Phase 1 驗收通過。目標：多 process 併發安全、可備份/監控、讀取加速與事件推送。

### P2-1　引進 SQLAlchemy + Alembic，抽象 Ledger 介面

- 定義 `LedgerStore` Protocol（`write_fill / rehydrate / save_snapshot / save_position_occ / load_by_bot_id / history`）。
- `SqliteLedgerStore` 由現行裸 SQL 遷移為 SQLAlchemy Core（保留同一 schema 語意）。
- Alembic 納入版本控制（現有 `schema.sql` 轉為 migration 001）。
- **驗收**：`LedgerRepository` 對外介面不變，既有測試（`test_ledger_repository.py`、`test_runtime_repository.py`）全綠。

### P2-2　PostgreSQL 真相源

- DDL 移植：`INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`、`AUTOINCREMENT`→`IDENTITY`、`REAL`→`DOUBLE PRECISION`（或 NUMERIC for 金額）、`PRAGMA`→連線參數。
- 連接池（SQLAlchemy `create_engine(pool_size, max_overflow)`）＋ `psycopg`（v3）。
- 以 env `DATABASE_URL` 切換（未設定時預設 SQLite，維持單機可跑）。
- 一次性遷移腳本 `scripts/migration/sqlite_to_postgres.py`（facts 優先，投影可重建）。
- **驗收**：同一組恢復/冪等/單調測試在 PG 上全綠；`ci.sh` 增加 PG fixture（可選，Docker/Testcontainers）。

### P2-3　Redis 熱快取

- `GridStateStore` 寫入後 `SETEX grid:state:{bot_id} {ttl} {json}`；讀取 miss 才回 SQLite/PG。
- TTL 與 staleness 語意沿用現有 `STALE_THRESHOLD`。
- **驗收**：重啟 Web API 後，dashboard 快照命中 Redis，無需等待引擎 heartbeat；Redis 重啟後服務自動降級回 DB 讀（無中斷）。

### P2-4　Redis Pub/Sub 事件推送

- engine 發布 lifecycle / heartbeat / fill 事件到 `grid:events` channel；Web API 訂閱並經 WebSocket 推前端（取代部分輪詢）。
- **驗收**：UI 收到事件延遲 < 500ms（本地）；斷線重連後可追補（以 facts 重建）。

### P2-5　Redis 領導者選舉（防雙開）

- 每 bot 啟動時 `SETNX grid:leader:{bot_id} {engine_id} EX {ttl}` ＋ 續約；無法取得者拒絕啟動。
- 取代/強化現有「同一 YAML 雙開」檢查。
- **驗收**：同 bot 第二個 engine 進程無法取得領導權並退出，log 明確。

### P2-6　組態與維運

- `.env.example` 增 `DATABASE_URL`、`REDIS_URL`、`GRID_DB_ASYNC_WRITE`。
- `docs` 增部署維運（備份/PITR/監控指標）。
- **驗收**：`start_web_ui.sh` 與 systemd 單元可在有/無 PG+Redis 兩種環境正常啟動。

### Phase 2 驗收總則（Gate）

- [ ] Phase 1 全綠且不退化。
- [ ] PG 上重跑全部 DB 測試全綠。
- [ ] Redis 停機演練：服務不中斷、資料不丟失（自動降級回 DB）。

---

## 6. Phase 3 — 事件溯源 + 交易所三路對帳

> 前置：Phase 2 驗收通過。目標：任何狀態皆可重建、可回放、可稽核；LIVE 下以交易所為最終真相自癒。

### P3-1　`bot_events` append-only 事件流

- 事件型別：`bot_created / bot_renamed / order_placed / order_cancelled / fill / take_profit / stop_loss / reset / state_transition`。
- 投影（`bot_positions`、計數）改為由事件摺疊（fold）產生；保留現行直寫為優化，但以事件為 canonical。
- **驗收**：任意時點可「重放事件」重建出與直寫一致的持倉/計數（屬性測試）。

### P3-2　grid 三路對帳（補齊 `ExchangeReconciler.reconcile_grid`）

- 比對 `本地 facts ↔ 交易所 positions ↔ open orders`；偵測不一致 → 自癒回補並記審計。
- 沿用既有 DCA reconciler 模式（`core/services/reconciler/exchange_reconciler.py`）。
- **驗收**：模擬「本地持倉=0、交易所持倉>0」與「本地有 TP、交易所無」兩情境，皆自動修復並記 audit。

### P3-3　稽核與回放工具

- `scripts/tools/replay_bot.py`：以 `bot_id` 重放事件，輸出時序對帳報告。
- **驗收**：對既有資料可產出「重建 vs 現存投影」差異報告。

### P3-4　備份/災難恢復演練

- PG PITR + facts 優先恢復 SOP；文件化。
- **驗收**：刪除全部投影表後，可由 facts＋事件完整重建。

### Phase 3 驗收總則（Gate）

- [ ] 事件重放與直寫投影一致（屬性測試通過）。
- [ ] grid + DCA 三路對帳自癒皆通過演練。
- [ ] 災難恢復 SOP 演練通過。

---

## 7. 決策紀錄（ADR 摘要）

| ID | 決策 | 理由 |
|---|---|---|
| ADR-0 | Phase 1 為 Phase 2/3 前置 | 換引擎不修身份/恢復來源，歸 0 仍在 |
| ADR-1 | 帳本唯一 key = `bot_id`（穩定 UUID），`config_path` 可變 | 消除檔名漂移（R1） |
| ADR-2 | Redis 只當快取/協調，不當事實來源 | 記憶體、可 evict、單機掉電即失 |
| ADR-3 | `trade_fills`/`bot_events` 為事實；`bot_snapshots` 為可重建投影 | 恢復以 facts 為準（R2/R3） |
| ADR-4 | LIVE 真相源優先序：交易所 > 本地 facts > 快照 | 真倉正確性 |
| ADR-5 | 金額用 NUMERIC/DECIMAL（PG），不用浮點 | 會計精度 |
| ADR-6 | 單調防護以「合法 reset 事件」豁免 | 兼顧防倒退與合法歸零（R4） |

---

## 8. 風險與回滾

| 風險 | 影響 | 緩解 |
|---|---|---|
| 身份 backfill 錯誤 remap 既有帳本 | 歷史對接錯亂 | backfill 前先備份 DB；`config_path→bot_id` 映射表人工核對；remap 寫審計 |
| 恢復改讀 facts 後，legacy 資料不含 facts（grid 舊成交） | 舊帳本重建不全 | 一次性 migrator：把 `grid_runtime_snapshots` 既有計數轉為「合成起始 fill」，並標 `role='legacy_reconstituted'` |
| PG 遷移 SQL 方言差異 | 資料錯誤 | 以 Alembic migration + 屬性測試雙保險；遷移後跑雙軌對比 |
| Redis 停機 | 讀取延遲/功能降級 | 全部 Redis 讀寫有 DB fallback；不讓 Redis 成單點 |
| 單調防護誤擋合法操作 | 無法歸零/更新 | 以事件旗標豁免；warn log 可查 |

**回滾策略**：每子任務獨立 commit；任一 Gate 失敗，`git revert` 該子任務即可。Phase 2 以 env 開關（`DATABASE_URL`/`REDIS_URL` 未設定 = 舊 SQLite 直連模式），可隨時回退。

---

## 9. 測試計畫

| 層級 | 內容 | 對應 |
|---|---|---|
| 單元 | `LedgerRepository`：冪等 fill、OCC、單調防護、rehydrate | `tests/test_ledger_repository.py`（擴充） |
| 單元 | `RuntimeSnapshotRepository`：`load_by_bot_id`、`rehydrate` | `tests/test_runtime_repository.py`（擴充） |
| 單元 | 身份穩定：改區間/重建不變 bot_id | 新增 `tests/test_bot_identity_stability.py` |
| 整合 | grid/DCA 恢復：成交後重啟不歸 0（快照表清空仍可重建） | `tests/services/grid/`、`tests/services/dca/`（擴充） |
| 整合 | grid 成交逐筆入 `trade_fills` | 新增 `tests/test_grid_fill_ledger.py` |
| 回歸 | `./scripts/ci.sh` 全綠（含 Phase 2 PG fixture） | 既有 CI |
| 演練 | 歸 0 重現腳本（§4 Phase 1 驗收） | 手動/腳本化 |

---

## 10. 里程碑

| 里程碑 | 內容 | 建議順序 |
|---|---|---|
| M1 | P1-1 → P1-2 → P1-3 → P1-4 → P1-6 → P1-5（Phase 1 Gate） | 先身份、後恢復來源、再單調、最後非同步 |
| M2 | P2-1 → P2-2 → P2-3/4/5 → P2-6（Phase 2 Gate） | 先抽象與 PG，再 Redis |
| M3 | P3-1 → P3-2 → P3-3 → P3-4（Phase 3 Gate） | 事件化 → 對帳 → 稽核 → 災備 |

> 每 Phase 皆依「單一 AI Agent 對話可完成」的工作量切分；每子任務附**獨立、可執行、可證偽的稽核標準**，未通過不得進入下一子任務/Phase。

---

## 11. 名詞對照

| 名詞 | 定義 |
|---|---|
| facts | 不可變成交流水（`trade_fills`、`bot_events`）＋ OCC 持倉 |
| projection | 由 facts 重建的顯示用資料（`bot_snapshots`） |
| bot_id | 穩定業務身份（UUID），帳本唯一 key |
| rehydrate | 由 facts 重建持倉/計數/盈虧 |
| 單調防護 | 防止新值倒退覆寫累積計數的機制 |
| 三路對帳 | 本地 facts ↔ 交易所 positions ↔ open orders 比對自癒 |
