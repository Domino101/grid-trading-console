---
name: db-migration
description: "Database migration procedures for grid-trading-console (SQLite data/web_ui_runtime.sqlite3 and data/grid_audit.db). Use when modifying schema, adding tables, migrating runtime/audit state, or resolving database lock issues."
---

# Database Migration Skill

## When to Activate
- User requests schema changes or new tables
- Before running ALTER/CREATE/DROP statements on runtime or audit databases
- When modifying SQLite schema definitions or resolving WAL/lock issues

## Database Infrastructure
`grid-trading-console` uses SQLite databases stored in `data/` for local and production runtime state.

### Core Databases & Tables
| Database | Table | Purpose |
|---|---|---|
| `data/web_ui_runtime.sqlite3` | `grid_runtime_snapshots` | Latest active grid state, metrics, and price levels |
| `data/web_ui_runtime.sqlite3` | `grid_runtime_history` | Historical execution snapshots for charting |
| `data/web_ui_runtime.sqlite3` | `grid_commands` | Hot-plug control commands sent via Web UI |
| `data/web_ui_runtime.sqlite3` | `grid_events` | Runtime lifecycle & trade event log |
| `data/web_ui_runtime.sqlite3` | `grid_command_events` | Command execution trace events |
| `data/grid_audit.db` | `audit_log` | Operator action audit trail and security events |

## Migration Protocol
1. **Backup**: Always backup SQLite databases before any schema migration:
   ```bash
   cp data/web_ui_runtime.sqlite3 data/web_ui_runtime_backup_$(date +%s).sqlite3
   cp data/grid_audit.db data/grid_audit_backup_$(date +%s).sqlite3
   ```
2. **Schema Update**: Update repository modules (`web_ui/backend/runtime_repository.py`, `web_ui/backend/command_repository.py`, `web_ui/backend/audit.py`).
3. **Execution & Test**: Run migration script or ALTER TABLE statements using parameterized queries.
4. **Verification**: Verify table structure with sqlite3:
   ```bash
   sqlite3 data/web_ui_runtime.sqlite3 ".schema"
   ```
5. **Service Restart & Smoke Test**: If Web UI is running, restart backend and verify live endpoints.
