#!/usr/bin/env bash
# Local / CI entrypoint matching docs/github-actions-ci.yml.
# Copy that file to .github/workflows/ci.yml when the GitHub App has workflows permission.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python -m unittest \
  tests.test_lifecycle_and_safety \
  tests.test_paper_adapter \
  tests.test_command_repository \
  tests.test_runtime_repository \
  tests.test_ledger_repository \
  tests.test_database_guard \
  tests.test_database_migrator_and_writer \
  tests.test_monotonic_guard \
  tests.test_grid_fill_ledger \
  tests.test_bot_identity_stability \
  tests.test_backfill_bot_ids \
  tests.test_identity_audit \
  tests.test_async_writer_full_save \
  tests.test_phase1_gate_repro \
  tests.test_paper_grid_lifecycle_e2e \
  tests.test_frontend_format_contract \
  tests.test_dashboard_snapshot \
  tests.test_web_ui_http_contract \
  tests.test_dca_ladder \
  tests.test_dca_indicators \
  tests.test_dca_config_and_state \
  tests.test_dca_executor \
  tests.test_dca_coordinator \
  tests.test_dca_config_loader \
  tests.test_dca_ohlcv_cache \
  tests.test_dca_paper_e2e \
  tests.test_dca_yaml_generator \
  tests.test_dca_http_contract \
  tests.test_dca_snapshot \
  tests.test_dca_frontend_contract \
  tests.test_dca_kline_repository \
  tests.test_dca_replay_exchange \
  tests.test_dca_replay_driver \
  tests.test_dca_backtest_metrics \
  tests.test_dca_backtest_sweep \
  tests.test_dca_backtest_http_contract \
  tests.test_dca_chart_backtest_e2e \
  tests.test_backend_import_contract

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
