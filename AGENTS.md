## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Project Snapshot (截至 2026-09-01)

- Python 3.11+ / Node.js 22+；後端 FastAPI in `web_ui/backend/main.py`（port 8001），前端 React 19 + Vite in `web_ui/frontend/`。
- 引擎入口：`run_multiple_grids.py`（多網格 + DCA 共用監督者）；執行與 Web UI 共用 `data/`、`config/`。
- 資料層：預設 SQLite `data/web_ui_runtime.sqlite3`（交易/快照/事件 canonical ledger）+ `data/grid_audit.db`（操作稽核）；`DATABASE_URL` 可切 PostgreSQL（Phase 2），Redis 為可拋棄快取/事件/領導者選舉層。
- 資料庫 schema 由 SQLAlchemy + `core/infrastructure/database/ledger_store.py` 定義，並由 `migrations/versions/`（0001_initial_schema、0002_bot_events）納管。**既有 SQLite 不可跑 `alembic upgrade head`**，僅全新 DB 可跑。
- 核心 ledger 表：`bots`、`trade_fills`、`bot_positions`、`bot_snapshots`、`bot_snapshot_history`、`bot_identity_events`、`bot_events`；`grid_runtime_*` 為 legacy 相容表（仍雙寫）。
- API：`/api/v1/system|grids|dca|dashboard|commands|market|grid-backtest`，另有 `/ws/grid-events`、`/ws/logs/{pid}`。
- 新功能「策略回測」：`/api/v1/grid-backtest/{run,stress,sweep}`（網格資金感知模擬）+ 既有 `/api/v1/dca/backtest`（DCA 單向/雙向 + 指標）；左側「網格回測」分頁統一收斂。
- 測試規範：Python 走 `unittest`（`./scripts/ci.sh` 或 `python3 -m unittest ...`；pytest 亦可），前端 `npm test` + `npm run lint` + `npm run build`。

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


