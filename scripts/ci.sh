#!/usr/bin/env bash
# Local / CI entrypoint matching docs/github-actions-ci.yml.
# Copy that file to .github/workflows/ci.yml when the GitHub App has workflows permission.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TEST_MODULES=(
  tests.test_lifecycle_and_safety
  tests.test_paper_adapter
  tests.test_command_repository
  tests.test_runtime_repository
  tests.test_ledger_repository
  tests.test_database_guard
  tests.test_database_migrator_and_writer
  tests.test_monotonic_guard
  tests.test_grid_fill_ledger
  tests.test_bot_identity_stability
  tests.test_backfill_bot_ids
  tests.test_identity_audit
  tests.test_async_writer_full_save
  tests.test_phase1_gate_repro
  tests.test_paper_grid_lifecycle_e2e
  tests.test_frontend_format_contract
  tests.test_dashboard_snapshot
  tests.test_web_ui_http_contract
  tests.test_dca_ladder
  tests.test_dca_indicators
  tests.test_dca_config_and_state
  tests.test_dca_executor
  tests.test_dca_coordinator
  tests.test_dca_config_loader
  tests.test_dca_ohlcv_cache
  tests.test_dca_paper_e2e
  tests.test_dca_yaml_generator
  tests.test_dca_http_contract
  tests.test_dca_snapshot
  tests.test_dca_frontend_contract
  tests.test_dca_kline_repository
  tests.test_dca_replay_exchange
  tests.test_dca_replay_driver
  tests.test_dca_backtest_metrics
  tests.test_dca_backtest_sweep
  tests.test_dca_backtest_http_contract
  tests.test_dca_chart_backtest_e2e
  tests.test_phase2_ledger_orchestration
  tests.test_bot_event_stream
  tests.test_event_fold
  tests.test_grid_reconcile
  tests.test_replay_rebuild_tools
  tests.test_backend_import_contract
  tests.test_grid_backtest_http_contract
  tests.services.grid.test_coordinator_stats_and_fills
  tests.services.grid.test_batch_retry_circuit_breaker
  tests.services.grid.test_fifo_arbitrage_and_window_protection
  tests.services.grid.test_grid_profit_and_stream_fix
  tests.services.grid.test_three_pillar_profit_isolation
  tests.services.grid.backtest.test_allocation
  tests.services.grid.backtest.test_risk
  tests.services.grid.backtest.test_simulator
  tests.services.grid.backtest.test_stress
  tests.services.dca.test_dca_snapshot_recovery
  tests.test_coinfull_prime_quant
  tests.test_coinfull_prime_rebalance
  tests.test_coinfull_prime_regime
  tests.test_coinfull_prime_p0_safety
  tests.test_coinfull_prime_p1_safety
  tests.test_coinfull_prime_p1b_safety
  tests.test_unified_ledger_accounting
  tests.test_ledger_consistency_audit
  tests.test_ledger_legacy_repair
  tests.test_accounting_path_policy
)

PYTHON="${PYTHON_BIN:-$(command -v python3 || command -v python)}"

# Public-repo hygiene gate (secrets / binaries / tracked-but-ignored files).
# Rationale: docs/PUBLIC_RELEASE_AUDIT_20261021.md
bash "$ROOT/scripts/public_repo_guard.sh"

"$PYTHON" -m unittest "${TEST_MODULES[@]}"

if command -v pytest >/dev/null 2>&1; then
  pytest tests/test_universe79_lib.py -q
fi

# ── Optional PostgreSQL leg (Phase 2 Gate: rerun the suite against PG). ────
# Enable with e.g. PG_TEST_URL=postgresql://grid:grid@localhost:5432/grid_test
# (docker: docker run -d -p 5432:5432 -e POSTGRES_USER=grid -e POSTGRES_PASSWORD=grid -e POSTGRES_DB=grid_test postgres:16)
# Ledger paths map to per-path PG schemas (audit F2), so tests stay isolated
# without external fixtures.
if [[ -n "${PG_TEST_URL:-}" ]]; then
  echo "== PostgreSQL leg: ${PG_TEST_URL%%\?*} =="
  # TEST_PG_URL enables the live-PG factory/isolation regressions inside
  # tests.test_phase2_ledger_orchestration (TestPhase2PostgresFactory).
  GRID_ENV=test DATABASE_URL="$PG_TEST_URL" TEST_PG_URL="$PG_TEST_URL" "$PYTHON" -m unittest "${TEST_MODULES[@]}"
fi

if [[ "${SKIP_FRONTEND:-}" == "1" ]]; then
  exit 0
fi

cd "$ROOT/web_ui/frontend"
if [[ ! -d node_modules ]]; then
  npm ci
fi
npm test
npm run lint
npm run build
