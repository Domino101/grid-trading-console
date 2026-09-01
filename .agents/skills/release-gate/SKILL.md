---
name: release-gate
description: "Release audit verification and sign-off checklist generator for grid-trading-console. Verifies test suite pass rate, code quality, dependency cleanliness, and generates a formatted Markdown Release Audit Checklist before merging to main."
---

# Release Gate Skill (發布驗收與門檻審核)

## When to Activate
- User asks for release audit / release check / 發布審核 / 準備上線 / release gate / 合併至 main
- Before merging any feature or arena branch into `main`

## Quality Gates Checklist
1. **Python Test Suite**: Run `./scripts/ci.sh` (Python uses `unittest`; `python3 -m pytest` also works) and verify 100% pass rate. Include the new regressions: `tests/services/grid/backtest/*`, `tests/test_grid_backtest_http_contract.py`, `tests/services/grid/test_coordinator_stats_and_fills.py`.
2. **Frontend Build & Lint**: Run `cd web_ui/frontend && npm test && npm run lint && npm run build` and ensure TypeScript compilation clean.
3. **AI Code Review (OCR)**: Run `ocr review --audience agent -b "Release Audit"` or `ocr delegate preview` to verify zero Critical/High line-level defects in Git diff.
4. **Trade Safety**: Verify `paper_trading` default protection and risk bounds.
5. **No Secrets/Hardcoded Credentials**: Verify no raw API keys or Web3 private keys in commit diff.
6. **Live Smoke Test**: Verify Web UI (Port 8001) and PM2 engines start cleanly; run a read-only smoke of `/api/v1/grid-backtest/run` if the backtest endpoints changed.
