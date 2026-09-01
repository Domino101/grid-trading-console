# Project-Specific Rules for AI Agents

These rules are automatically applied by the AI agent when working in this project.

## rule-01-trade-safety
- `paper_trading` MUST remain `true` unless user explicitly confirms switching to live trading
- All trade orders MUST pass risk gates (min notional, price limits, position size) before reaching exchange adapters in `core/adapters/exchanges/`
- Never bypass or modify risk gate logic without explicit user approval
- Before declaring any trade-related task complete, verify position tracking, order states, and error handling

## rule-02-code-structure
- New trading services and strategies go in `core/services/` (e.g. `grid/`, `dca/`, `volume_maker/`, `arbitrage_monitor/`, `price_alert/`, and `*/backtest/` for backtest logic)
- Exchange-specific adapters go in `core/adapters/exchanges/adapters/` (e.g. `binance.py`, `okx.py`, `hyperliquid.py`, `lighter.py`, `edgex.py`, `backpack.py`, `o1exchange.py`, `paper.py`)
- Web UI backend is FastAPI (`web_ui/backend/main.py` on port 8001; routes under `web_ui/backend/routes/`), frontend is React 19 SPA (`web_ui/frontend/`)
- Configuration goes in `config/` or environment variables in `.env` — never hardcoded credentials
- Grid/DCA runtime state uses the relational ledger (SQLite `data/web_ui_runtime.sqlite3` or PostgreSQL via `DATABASE_URL`); operator audit uses `data/grid_audit.db`

## rule-03-logging
- Use `logging` module — NEVER `print()` for any runtime output in core services
- Log level: INFO for trade events, DEBUG for scanning/orderbook, ERROR for failures
- All log messages include: timestamp, service_name, exchange, symbol, action, result

## rule-04-database
- Default is SQLite `data/web_ui_runtime.sqlite3` (canonical ledger: `bots/trade_fills/bot_positions/bot_snapshots/bot_events/...`) + `data/grid_audit.db` (operator audit). `DATABASE_URL` switches the ledger store to PostgreSQL (Phase 2); Redis is an optional ephemeral cache/event/leader layer.
- All database queries (SQLite or PostgreSQL) MUST use parameterized queries — no string concatenation.
- Schema is owned by SQLAlchemy (`core/infrastructure/database/ledger_store.py`) and Alembic (`migrations/versions/`. **Existing SQLite DB: NEVER run `alembic upgrade head`**; run `DatabaseMigrator.migrate()` for legacy-json backfill. Alembic only for a fresh DB (usually fresh PostgreSQL).
- Schema changes require backup → dry run → execute → verify workflow.

## rule-05-restart-after-changes
- After any change under `core/`, `web_ui/`, `run_*.py`, `config/`, or `requirements.txt`, ALWAYS remind the user that a service restart is required for the change to take effect — do not assume hot-reload.
- State the exact restart command at the end of the response (e.g. `python3 run_multiple_grids.py`, `cd web_ui/backend && python main.py`, `bash start_web_ui.sh` (local only), or `pm2 restart ecosystem.config.js` / `systemctl restart crypto-webui` (production)), and list which file(s) triggered the requirement.
- Use repo-relative commands or `$PROJECT_ROOT`; never hardcode an absolute path like `/root/grid-trading-console/...`.
- Edits that do NOT require restart: files under `tests/`, `docs/`, `.agents/`, top-level `*.md` reports, and `tools/` one-off scripts.

## rule-06-bot-audit
- When asked to perform an audit or check if the bot is trading normally:
  1. Inspect running processes using `pgrep -fa python` or `pm2 status` to verify `run_*.py` services and Web UI are active.
  2. Check active exchange API connectivity and configuration in `config/` and `.env`.
  3. Query local database / trade state for recent execution results, identifying any rejected or cancelled orders.
  4. Check log files in `logs/` or `tmp_output.log` to trace order IDs or exchange errors.
  5. Present findings clearly with status, active exchanges, recent trade count, and recommendations.

## rule-07-ponytail (lazy senior dev mode)

Before writing any code, stop at the first rung of the YAGNI ladder that holds:

1. **Does this need to be built at all?** Speculative need = skip it, say so in one line.
2. **Stdlib does it?** Use it.
3. **Native platform feature covers it?** DB constraint over app code, CSS over JS, `input type="date"` over a picker lib.
4. **Already-installed dependency solves it?** Use it. Never add a new dep for what a few lines can do.
5. **Can it be one line?** One line.
6. **Only then:** the minimum code that works.

Rules:
- No unrequested abstractions: no interface with one implementation, no factory for one product, no config for a value that never changes
- No boilerplate or scaffolding "for later" — later can scaffold for itself
- Deletion over addition. Boring over clever. Fewest files possible. Shortest working diff wins
- Mark deliberate simplifications with a `ponytail:` comment naming the ceiling and upgrade path: `# ponytail: global lock, per-account locks if throughput matters`
- Two stdlib options, same size? Take the one correct on edge cases

**Ponytail does NOT override rules 01–06.** Never simplify away:
- Trade safety (rule-01), risk engine gates, `paper_trading` protection
- Input validation at trust boundaries, error handling that prevents data loss
- Security measures, parameterized queries (rule-04)
- Logging standards (rule-03)
- Anything the user explicitly requested

Non-trivial logic (a branch, a loop, a parser, a money/security path) leaves ONE runnable check behind — an `assert`-based self-check or one small `test_*.py`. No frameworks, no fixtures. Trivial one-liners need no test.

## rule-08-git-branching
- `main` branch MUST remain stable, 100% test-passing, and deploy-ready at all times.
- NEVER develop directly on `main` for non-trivial features, refactoring, or structural audits.
- All work MUST occur on topic branches (`arena/<topic>` or `feature/<topic>`).
- Before merging any branch into `main`, verify unit tests pass (`./scripts/ci.sh`; Python uses `python3 -m unittest ...` or `python3 -m pytest`).
- Merges to `main` SHOULD use `--no-ff` (`git merge --no-ff <branch>`) to maintain a clean release graph.
- When a feature touches DB/API/trading state, also update `.agents/` guidance + `README.md`/`CHANGELOG.md` in the same change set so agent instructions stay accurate.


