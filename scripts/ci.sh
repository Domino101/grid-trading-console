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
  tests.test_paper_grid_lifecycle_e2e \
  tests.test_frontend_format_contract \
  tests.test_dashboard_snapshot \
  tests.test_web_ui_http_contract

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
