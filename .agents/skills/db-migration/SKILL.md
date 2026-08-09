---
name: db-migration
description: "Database migration procedures for crypto-trading-open (SQLite crypto_trading.db / PostgreSQL). Use when modifying schema, adding tables, migrating trade state, or resolving database lock issues."
---

# Database Migration Skill

## When to Activate
- User requests schema changes or new tables
- Before running ALTER/CREATE/DROP statements on trade/position databases
- When migrating from SQLite to PostgreSQL or updating schema definitions

## Database Infrastructure
`crypto-trading-open` uses SQLite (`crypto_trading.db`) for local storage or PostgreSQL for production deployments.

### Core Tables
| Table | Purpose |
|---|---|
| `grid_orders` / `orders` | Order placement history, status, fill prices |
| `positions` | Active positions, entry prices, realized/unrealized PnL |
| `market_ticks` / `prices` | Historical price data and scan results |
| `config_state` | Dynamic runtime configurations |

## Migration Protocol
1. **Backup**: Create a backup of `crypto_trading.db` (`cp crypto_trading.db crypto_trading_backup.db`) before modifying schema.
2. **Schema Update**: Update schema definitions in the corresponding database manager module.
3. **Execution & Test**: Run migration script or ALTER TABLE statements using parameterized queries.
4. **Verification**: Verify table structure with `.schema` in SQLite or `\d` in PostgreSQL.

