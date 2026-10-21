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

## Public Repository Hygiene (公開 repo 衛生守則，2026-10-21)

本 repo 已準備公開。完整審查與每項決策的原因見 `docs/PUBLIC_RELEASE_AUDIT_20261021.md`，以下為不可違反的摘要：

- **憑證**：`config/exchanges/` 只追蹤 `*.example.yaml`；真實檔 `<exchange>_config.yaml`、`.env`、`.env.*`、`.grid_api_token`、`.secrets/` 皆被 `.gitignore`，**不得**改名回來或 `git add -f`。`.env.example` 的任何 `*_KEY/*_SECRET/*_TOKEN` 必須是明顯佔位符。
- **二進位**：`*.zip/*.exe/*.dll/*.bat/*.cmd` 等被 `.gitignore` 封鎖。原因：本專案源自被植入木馬的上游 fork（`HamaX83/crypto-trading-open`），`core/` 內曾藏兩個 LuaJIT loader 惡意 zip（已剔除）。從上游同步任何內容前先掃描。
- **生成物**：`graphify-out/2026-*/`、`graphify-out/cache/`、`config/grid/archived/`、`data/`、`logs/` 不進版本控制；只有 `graphify-out/graph.json|graph.html|GRAPH_REPORT.md|manifest.json` 是刻意追蹤的知識圖譜主體。
- **守門**：`scripts/public_repo_guard.sh`（G1–G6）已接入 `scripts/ci.sh`。守門失敗時**修正違規，不是放寬規則**；確屬誤判才在腳本白名單內補註原因。
- **個資**：新文件不得出現真實對外 IP、`/root/<user>` 實路徑、帳戶餘額；改用 `<REDACTED_*>` / `<PROJECT_ROOT>`。
- **授權**：目前無 LICENSE（保留所有權利），且上游亦無授權條款；加 LICENSE 前先讀審查報告 F-06。
- **歷史**：若需再次清除歷史中的敏感內容，沿用 `scripts/purge_history.sh` 的流程（全新 bare clone → filter-repo → 驗證 → force-push → 所有 clone `reset --hard`）。

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
  3. **Stage 3 (Lead Judgment & 4-Tier Taxonomy)**: Synthesize findings into the 4-tier decision taxonomy (`interrogate` standard):
     - **Act on (阻斷發布項)**: 影響正確性、資金安全、或記帳不變式的致命缺陷，必須修復才能合併/上線。
     - **Consider (值得評估項)**: 具備技術合理性，但需權衡當前重構成本與維護效益。
     - **Noted (列入觀察項)**: 技術上成立但非迫切，屬於低影響或過早優化。
     - **Dismissed (已駁回項)**: 誤報、無理挑剔或未考慮業務脈絡，附上具體駁回理由。
  4. **Output Discipline (Unslop Standard)**: 報告輸出嚴格遵守 `unslop` 規範，結論先行，去除 AI 客套話、裝飾性 Emoji 與空泛形容詞，直陳事實與行號。

## Mistake Classes & Structural Enforcement Table (錯誤類別與結構守門對照表)

Rule:
- **Zero-Tolerance for Repeated Agent Mistakes**: 依照 `correct` 技能規範，所有重複發生的錯誤必須提升至最高可能層級（架構 > 型別/Lint > 測試 > 規則）消滅，並在此表持續登記強制守門機制：

| 錯誤類別 (Mistake Class) | 歷史痛點與症狀 | 最高守門層級 | 具體強制守門機制 (Enforcement Mechanism) |
|---|---|:---:|---|
| **MC-1: 反向翻倉 `reduce_only` 越界** | 現有多頭反向翻空為基準對沖時，單筆委託量超過持倉且帶 `reduce_only=True` 引發幣安拒單 `-2022` | 架構 (Level 1) + 測試 (Level 3) | `PortfolioRebalancer.calculate_diff` 自動拆單為 Phase 1 `flip_close`（平舊倉）與 Phase 2 `flip_open`（開新反向倉）；`test_calculate_diff_with_position_reversal` 回歸守門。 |
| **MC-2: 小幣預設槓桿超標拒單** | 幣安對低流動性小幣限制最高 5x/10x 初始槓桿，帳戶預設 20x 導致可開名義額為 0，報錯 `-2027` | 架構 (Level 1) + 測試 (Level 3) | 實盤送單前由 Rebalancer 主動呼叫 `set_leverage(symbol, target_leverage=1)` 預設安全槓桿；`test_execute_rebalance_leverage_preset` 守門。 |
| **MC-3: 換倉管線並發下單翻倍** | 使用者連點或定時排程與手動換倉重疊，多協程併發下單導致保證金穿倉 | 架構 (Level 1) + 邊界 (Level 2) | `CoinFullPrimeService` 實作非同步互斥鎖 `asyncio.Lock()`；FastAPI 邊界捕獲衝突並回傳 `409 Conflict` 阻斷。 |
| **MC-4: Dry-Run 模擬污染正式帳本** | 模擬運行調用 `save_active_basket` 覆寫並關閉實盤 `bot_baskets` | 架構 (Level 1) + 測試 (Level 3) | 嚴格隔離：Dry-Run 模式下短路所有 `StrategyLedger.save_active_basket` 調用；單元測試驗證帳本隔離。 |
| **MC-5: 避碰過寬誤鎖 BTC 對沖** | 掃描已歸檔或非幣安歷史機器人，誤將主權對沖幣 `BTC` 排除下單 | 架構 (Level 1) | 嚴格收斂掃描作用域：僅掃描目標交易所 (`binance`) 且處於活躍目錄 (`active`) 的現役機器人。 |
| **MC-6: 繞過統一記帳直連 SQLite** | 策略或 API 層直接 `sqlite3.connect()`，PostgreSQL 生產模式產生幽靈資料源 | 靜態 AST 檢查 (Level 2) | `tests/test_accounting_path_policy.py` 在 CI 靜態掃描所有連線與 import，違法直連直接爆紅。 |
| **MC-7: 隨意更動網格核心** | 修改其他業務時隨意更動 `core/services/grid/`，導致相依策略崩潰 | CI 守門測試 (Level 3) | `tests/_grid_guard.py::GRID_DIFF_ALLOWLIST` 白名單；未授權更動直接擋下 CI。 |
| **MC-8: 停止事件/名單同步越權改帳** | 止損在 dry-run 或 adapter 缺失時仍 `apply_basket_fill`；名單同步 `close_missing=True` 把平倉失敗部位標 closed，形成孤兒倉 | 架構 (Level 1) + 測試 (Level 3) | 止損僅在交易所 `filled>0` 且非 dry-run 時結算；`save_active_basket` 固定 `close_missing=False`；partial/failed 不同步名單；`tests.test_coinfull_prime_p0_safety` 守門。 |
| **MC-9: 資料/配置缺漏 fail-open** | ATR/成交量抓取失敗仍放行；配置檔缺失退回 v2 預設；`compute_ranks` failed 的空名單被 `execute_rebalance` 當成全平 | 架構 (Level 1) + 測試 (Level 3) | Opt D 缺值 fail-closed；`load_config` 缺失/空檔 raise；`ranking_failed` 直接 hold；`tests.test_coinfull_prime_p1_safety` 守門。 |
| **MC-10: 鏡像回填復活已平倉成分** | active basket 為空時 `load_active_basket` 無條件回填 stale 鏡像，把已對帳關閉的成分復活成幽靈持倉 | 架構 (Level 1) + 測試 (Level 3) | 只有整個帳號完全沒有 basket 列才允許回填；`_export_basket_mirror` 在無 active 時移除帳號 key；`tests.test_coinfull_prime_p0_safety::TestBasketMirrorResurrection` 守門。 |

## Version Single Source (版本單一來源，2026-10-21)

Rule:
- 根目錄 `VERSION` 是唯一版本來源；**禁止**在 JSX、FastAPI 或頁面標題硬編版本號。
- 前端由 Vite `define` 注入 `__APP_VERSION__`；後端由 `core.version.__version__` 讀取。
- 升版：`python3 scripts/sync_version.py --set X.Y.Z`；只改 VERSION 後執行 `python3 scripts/sync_version.py`。
- CI 執行 `python3 scripts/sync_version.py --check`；`tests/test_version_consistency.py` 會擋下 package.json、package-lock、backend 與 frontend 的版本漂移。
