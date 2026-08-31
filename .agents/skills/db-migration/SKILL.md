---
name: db-migration
description: "Database migration procedures for grid-trading-console (SQLite data/web_ui_runtime.sqlite3, optional PostgreSQL DATABASE_URL, and data/grid_audit.db). Use when modifying schema, adding tables, migrating runtime/audit state, or resolving database lock issues."
---

# Database Migration Skill

## When to Activate
- User requests schema changes or new tables
- Before running ALTER/CREATE/DROP statements on runtime or audit databases
- When modifying SQLite/PostgreSQL schema definitions or resolving WAL/lock issues
- **When upgrading a live machine from the pre-v0.2.0-beta codebase (legacy JSON snapshot schema) to the current Phase 1-3 branch — follow `docs/live_upgrade_playbook_zh-TW.md` instead of improvising.**

## Database Infrastructure
`grid-trading-console` defaults to SQLite (`data/web_ui_runtime.sqlite3`); setting
`DATABASE_URL=postgresql://...` switches the ledger store to PostgreSQL (Phase 2).

### Core Databases & Tables (v0.2.0-beta schema)
| Database | Table | Purpose |
|---|---|---|
| `data/web_ui_runtime.sqlite3` | `bots` | Stable bot identity (bot_id PK, decoupled from filename) |
| `data/web_ui_runtime.sqlite3` | `trade_fills` | Append-only immutable fill ledger |
| `data/web_ui_runtime.sqlite3` | `bot_positions` | OCC-guarded position/counters projection |
| `data/web_ui_runtime.sqlite3` | `bot_snapshots` / `bot_snapshot_history` | Latest per-bot runtime snapshot + history |
| `data/web_ui_runtime.sqlite3` | `bot_identity_events` | Identity audit (created/config_linked/renamed) |
| `data/web_ui_runtime.sqlite3` | `bot_events` | Phase 3 append-only event stream (seq per bot) |
| `data/web_ui_runtime.sqlite3` | `grid_runtime_snapshots` / `grid_runtime_history` | Legacy compat tables (still dual-written; old readers keep working) |
| `data/web_ui_runtime.sqlite3` | `grid_commands` / `grid_command_events` / `grid_events` | Command pipeline (unchanged) |
| `data/web_ui_runtime.sqlite3` | `dca_klines` / `audit_log` | Chart cache / audit trail (unchanged) |
| `data/grid_audit.db` | `audit_log` | Operator action audit trail and security events |

## Migration Protocol
1. **Backup**: Always backup before any schema migration:
   ```bash
   sqlite3 data/web_ui_runtime.sqlite3 ".backup data/web_ui_runtime_backup_$(date +%s).sqlite3"
   cp data/grid_audit.db data/grid_audit_backup_$(date +%s).sqlite3
   ```
2. **Existing SQLite DB: NEVER run `alembic upgrade head`.** Migration 0001 re-creates
   the legacy compat tables and fails with `table grid_runtime_snapshots already exists`.
   The application auto-creates missing tables via SQLAlchemy `create_all(checkfirst)`
   on startup; run the explicit backfill for legacy JSON snapshots:
   ```bash
   python3 -c "from core.infrastructure.database.migrator import DatabaseMigrator; print(DatabaseMigrator.migrate())"
   ```
   Alembic is for a **fresh** database only (typically fresh PostgreSQL in Phase 2).
3. **Legacy → relational conversion** is idempotent and facts-first:
   `scripts/migration/backfill_bot_ids.py` (YAML `metadata.bot_id`) →
   `DatabaseMigrator.migrate()` (SQLite in place) →
   `scripts/migration/sqlite_to_postgres.py` (optional SQLite→PostgreSQL).
4. **Verification**: check tables and counts:
   ```bash
   sqlite3 data/web_ui_runtime.sqlite3 ".tables"
   sqlite3 data/web_ui_runtime.sqlite3 "SELECT count(*) FROM bots; SELECT bot_id,completed_cycles,version FROM bot_positions;"
   ```
5. **Service Restart & Smoke Test**: If Web UI is running, restart backend and verify
   live endpoints (`/healthz`, dashboard values unchanged — no counter resets).

## Related
- Live-machine upgrade runbook (near-zero downtime, verification gates, rollback):
  `docs/live_upgrade_playbook_zh-TW.md`
- Phase 2 ops: `docs/phase2_production_ops_zh-TW.md`; Phase 3 DR: `docs/phase3_disaster_recovery_zh-TW.md`
