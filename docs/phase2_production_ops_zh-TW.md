# Phase 2 生產化部署與維運（PostgreSQL + Redis）

> 對應計畫：`docs/data_layer_resilience_plan_zh-TW.md` §5（Phase 2）
> 分支：`arena/01a05696-grid-trading-console`
> 稽核與修復記錄：`docs/phase2_audit_report_20260831_zh-TW.md`、`docs/phase2_fixes_worklog_20260831.md`（2026-08-31 Gate 已通過）

---

## 1. 模式摘要

| 模式 | 觸發條件 | 效果 |
|---|---|---|
| 單機 SQLite | 未設定 `DATABASE_URL`（預設） | 零外部依賴，行為與 Phase 1 完全相同 |
| PostgreSQL 真相源 | 設定 `DATABASE_URL=postgresql://...` | LedgerStore 工廠改以 `PostgresLedgerStore` 持久化 |
| Redis 熱快取/事件/領導者 | 設定 `REDIS_URL=redis://...` | 增加熱快取、WebSocket 事件推送、防雙開；Redis 停機自動降級回 DB |

---

## 2. 環境變數

```bash
# .env（參考 .env.example）
DATABASE_URL=postgresql://grid:grid@localhost:5432/grid
REDIS_URL=redis://localhost:6379/0

GRID_PG_POOL_SIZE=5
GRID_PG_MAX_OVERFLOW=10
GRID_REDIS_STATE_TTL=300
GRID_REDIS_LEADER_TTL=30
GRID_DB_ASYNC_WRITE=1
```

## 3. PostgreSQL 建置

```bash
psql -U postgres -c "CREATE ROLE grid LOGIN PASSWORD 'grid';"
psql -U postgres -c "CREATE DATABASE grid OWNER grid;"
```

> **驅動說明（稽核 F1 修復後）**：repo 僅 pin psycopg **v3**（`psycopg[binary]`）。
> `postgresql://…` 與 `postgres://…` URL 會由系統自動正規化為
> `postgresql+psycopg://…`；亦接受直接寫 `postgresql+psycopg://…`。
> **不要**另行安裝 psycopg2。

### Alembic 初始化（首次）

```bash
alembic upgrade head
```

### SQLite → PostgreSQL 一次性遷移

```bash
python scripts/migration/sqlite_to_postgres.py \
  --sqlite data/web_ui_runtime.sqlite3 \
  --postgres-url "postgresql://grid:grid@localhost:5432/grid"
```

遷移是 **facts 優先**：bots → trade_fills → positions → snapshots，並具冪等性，可安全重跑。
遷移完成後將 `.env` 的 `DATABASE_URL` 指向 PG，重啟 Web API / 引擎。

## 4. Redis 快取與事件

- 熱快取 key：`grid:state:{bot_id}`、`grid:state:path:{base64(config_path)}`，TTL = `GRID_REDIS_STATE_TTL`（已實際接線，稽核 F4 修復）。
- 事件 channel：`grid:events`，Web API 於 `/ws/grid-events` 訂閱並推送前端；斷線後仍可由 DB facts 重建。
  - 前端已接入：`runtime`/`lifecycle` 事件觸發即時快照刷新（150ms debounce），頂欄顯示「即時/輪詢」連線狀態（稽核 F6 修復）。
  - 訂閱自動重連（指數退避、上限 30s）：**Redis 重啟後事件推送自動恢復，無需重啟 API**（稽核 F5 修復）。
- 領導者選舉 key：`grid:leader:{bot_id}`，租約 = `GRID_REDIS_LEADER_TTL`（已實際接線）；續約/釋放為 Lua 原子操作（稽核 F7 修復）。Redis 不可達時 fail-open 放行（防雙開僅剩進程鎖防護），並記 **ERROR** log。
- 未設 `REDIS_URL` 時自動關閉，允許單機啟動。

> **Redis 不是事實來源**。所有 Redis 讀寫都有 DB fallback；即使 Redis 整個停機，服務不會中斷，資料也不會丟失。

## 5. 備份 / 災難恢復

1. PostgreSQL 啟用 WAL 歸檔 + 每日 base backup（`pg_basebackup` / 雲端 PITR）。
2. 資料原則：`facts（trade_fills + bot_positions）` 必須在 `bot_snapshots`（投影）之前備份。
3. 若投影遺失：只需由 facts 重放/重建（`rehydrate_position_from_fills`），不必從交易所回補。

## 6. 監控指標

| 指標 | 來源 |
|---|---|
| `grid.bots_running` / `grid.bots_stale` | `/api/v1/...` dashboard read model |
| Redis 命中/降級 log（WARNING） | `RedisStateCache` / `RedisEventBus` |
| 領導者衝突 log（ERROR） | `RedisLeaderElection` |
| PostgreSQL 連線池使用率 | `GRID_PG_POOL_SIZE/GRID_PG_MAX_OVERFLOW` |

## 7. 回滾

任何子任務均可 `git revert`；Phase 2 以 env 開關控制：將 `DATABASE_URL` / `REDIS_URL` 設空即可回到 Phase 1 的 SQLite 直連模式。

## 8. 部署驗證證據（2026-08-31 稽核沙箱實證）

| 項目 | 結果 |
|---|---|
| SQLite 模式（無 DATABASE_URL/REDIS_URL）CI 回歸 | 391/391 OK |
| PG 16.2 模式 CI 同模組清單（`PG_TEST_URL` leg） | 38 模組全綠（測試以 per-path schema 自隔離） |
| `alembic upgrade head`（SQLite / PG） | 雙後端成功 |
| sqlite→PG 遷移（facts 優先）＋ 冪等重跑 | fills 不重複、OCC version 保留、identity events 去重搬運 |
| Redis 停機演練（殺掉 Redis → 讀寫 → 重啟） | 15/15 檢查通過：資料零損失、自動降級、讀路徑與事件訂閱自癒 |
| 事件延遲（engine → Redis → WebSocket） | 83–110ms（門檻 <500ms） |
| 雙環境啟動（有/無 PG+Redis 的 `/healthz`、引擎熱待命） | 皆正常，log 無 traceback |
