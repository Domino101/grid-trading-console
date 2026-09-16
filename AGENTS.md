## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Project Snapshot (截至 2026-09-16)

- Python 3.11+ / Node.js 22+；後端 FastAPI in `web_ui/backend/main.py`（port 8001），前端 React 19 + Vite in `web_ui/frontend/`。
- 引擎入口：`run_multiple_grids.py`（多網格 + DCA 共用監督者）；執行與 Web UI 共用 `data/`、`config/`。
- 資料層：預設 SQLite `data/web_ui_runtime.sqlite3`（交易/快照/事件 canonical ledger）+ `data/grid_audit.db`（操作稽核）；`DATABASE_URL` 可切 PostgreSQL（Phase 2），Redis 為可拋棄快取/事件/領導者選舉層。
- **統一記帳（0.3.6 / P4-1）**：Grid、DCA、CoinFull Prime 與 API 層一律經**單一記帳埠** `core/infrastructure/database/strategy_ledger.py::StrategyLedger`（→ `LedgerRepository` → `create_ledger_store()`）；後端由工廠依 `DATABASE_URL` 解析。**DB 為唯一真相源**，CoinFull Prime 的 JSON/JSONL 檔僅為匯出鏡像（`source: canonical_ledger:*`），並支援冪等回填。
- 資料庫 schema 由 SQLAlchemy + `core/infrastructure/database/ledger_store.py` 定義，並由 `migrations/versions/`（0001_initial_schema、0002_bot_events、0003_unified_strategy_accounting）納管。**既有 SQLite 不可跑 `alembic upgrade head`**，僅全新 DB 可跑；既有庫的新表由 `ledger_store.initialize()`（`create_all`）自動補齊。
- 核心 ledger 表：`bots`、`trade_fills`、`bot_positions`、`bot_snapshots`、`bot_snapshot_history`、`bot_identity_events`、`bot_events`；**P4-1 新增** `bot_baskets`（多標的受管籃子，均價法 `realized_pnl`）、`bot_regime_decisions`（風險閘門決策）、`bot_runtime_state`（策略運行 KV，如排程狀態）；`grid_runtime_*` 為 legacy 相容表（仍雙寫）。
- 記帳一致性稽核：引擎 `core/infrastructure/database/ledger_audit.py`（L1–L15 不變式）、CLI `tools/ledger_consistency_audit.py`（`--markdown/--json/--backfill/--export-mirror/--db/--fail-on`）、API `GET /api/v1/system/ledger-audit`。
- API：`/api/v1/system|grids|dca|dashboard|commands|market|grid-backtest|coinfull-prime`，另有 `/ws/grid-events`、`/ws/logs/{pid}`。
- 新功能「策略回測」：`/api/v1/grid-backtest/{run,stress,sweep}`（網格資金感知模擬）+ 既有 `/api/v1/dca/backtest`（DCA 單向/雙向 + 指標）；左側「網格回測」分頁統一收斂。
- 測試規範：Python 走 `unittest`（`./scripts/ci.sh` 或 `python3 -m unittest ...`；pytest 亦可），前端 `npm test` + `npm run lint` + `npm run build`。

## Unified Accounting Path Policy (統一記帳路徑政策，0.3.6 / P4-1)

Rule:
- **單一記帳埠**：任何策略（grid / dca / coinfull_prime）、API 路由或引擎入口要寫/讀記帳資料，**只能**透過 `StrategyLedger`（`core/infrastructure/database/strategy_ledger.py`）。
  **禁止**在策略層/API 層直接 `SqliteLedgerStore()`、`PostgresLedgerStore()` 或 `sqlite3.connect()`——這會在 `DATABASE_URL`（PostgreSQL）模式下寫進本地 SQLite 的**幽靈資料源**，讓 canonical ledger 看不到資料。
  唯一允許的低階連線點是 `web_ui/backend/lifecycle.py::connect_sqlite()`（已套 `EnvironmentGuard`），列於 `ledger_audit.POLICY_ALLOWLIST`。
- **DB 為唯一真相源**：不得新增「檔案型帳本」作為真相源。若需檔案（外部工具/人工檢視），一律由 DB **匯出鏡像**（`export_ledger_to_side_files()`，鏡像需標注 `source: canonical_ledger:*`），並可經 `backfill_coinfull_prime_side_files()` **冪等**回填；回填一律 `close_missing=False`（非破壞性）。
- **冪等鍵**：`trade_fills.fill_id` 必為穩定業務鍵（交易所 order id / 命名契約，**禁用毫秒時間戳**）；可重跑事件（`daily_rebalance`、`weekly_rebalance`、`manual_rebalance`、`regime_gate`）**必帶** `dedupe_key`。
- **結算權歸屬**：籃子型策略的 `status`/`quantity`/`realized_pnl` 結算**只**由 `apply_basket_fill()`（均價法）擁有；「名單同步」不得覆寫已結算成分，且必須跳過 `reduce_only`（平倉）單，否則會產生幽靈持倉。
- **記帳失敗不得阻斷交易**，但**必須可觀測**：經 `StrategyLedger.health()`（`writes_failed` / `errors_by_op` / `degraded`）與稽核 L12 暴露；禁止以 `except: pass` 吞掉記帳例外。
- **改動後必跑**：`python3 tools/ledger_consistency_audit.py --markdown --fail-on high`（或 `GET /api/v1/system/ledger-audit`）應為 HEALTHY；
  `tests/test_accounting_path_policy.py` 會在 CI 靜態擋下路徑政策回歸（含 `schema.sql` ↔ Alembic `0003` parity）。
- **網格核心守門**：`tests/_grid_guard.py::GRID_DIFF_ALLOWLIST` 記錄「已授權的網格核心改動」（檔案 + 交付期 + 理由）。
  改動 `core/services/grid/` 或 `run_grid_trading.py` 時，必須在該允許清單登記理由，否則 6 個 DCA 交付期的零變更守門測試會失敗。

## Service Restart & Live Verification (修改後重啟與運行驗證標準)

Rule:
- **Mandatory Restart & Verification**: Whenever any code or configuration affecting active services (Web UI backend, React frontend, Trading Engines, PM2 services) is modified, you MUST:
  1. Check if the target service is running in the background (`lsof -i :8001`, `pm2 list`, `ps aux`).
  2. If the service is running, restart it cleanly to load the latest code/build. For the Web UI use repo-relative commands, never hardcode an absolute checkout path (e.g. `bash start_web_ui.sh`, or `cd web_ui/backend && python main.py`); production uses systemd (`scripts/deployment/crypto-webui.service`), not `start_web_ui.sh`.
  3. Execute a live verification request (e.g. `curl` to the running port, check logs) against the actual running instance.
  4. Only declare the task complete after verifying that the live restarted instance returns the expected result.

## Dual-Stage Audit Standard (雙軌稽核自動化標準)

Rule:
- **Automatic Audit Workflow**: Whenever the user requests to "audit", "review", "審查", "稽核" any module or codebase logic:
  1. **Stage 1 (Macro Architecture)**: Execute `graphify query "<target>"` (or `graphify explain` / `graphify path`) first to understand module topology, caller chains, and impact radius.
  2. **Stage 2 (Micro Line-level Inspection)**: Execute `ocr review --audience agent -b "<context>"` (if git diff exists) or `ocr scan --path <dir>` (for static directory audit) to perform line-level precision defect, concurrency, and safety audits.
  3. Combine both outputs into a structured audit report.


