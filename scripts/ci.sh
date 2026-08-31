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
  tests.test_backend_import_contract
)

python -m unittest "${TEST_MODULES[@]}"

# ── Optional PostgreSQL leg (Phase 2 Gate: rerun the suite against PG). ────
# Enable with e.g. PG_TEST_URL=postgresql://grid:grid@localhost:5432/grid_test
# (docker: docker run -d -p 5432:5432 -e POSTGRES_USER=grid -e POSTGRES_PASSWORD=grid -e POSTGRES_DB=grid_test postgres:16)
# Ledger paths map to per-path PG schemas (audit F2), so tests stay isolated
# without external fixtures.
if [[ -n "${PG_TEST_URL:-}" ]]; then
  echo "== PostgreSQL leg: ${PG_TEST_URL%%\?*} =="
  # TEST_PG_URL enables the live-PG factory/isolation regressions inside
  # tests.test_phase2_ledger_orchestration (TestPhase2PostgresFactory).
  GRID_ENV=test DATABASE_URL="$PG_TEST_URL" TEST_PG_URL="$PG_TEST_URL" python -m unittest "${TEST_MODULES[@]}"
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
