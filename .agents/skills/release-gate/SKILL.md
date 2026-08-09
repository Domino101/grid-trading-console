---
name: release-gate
description: "Release audit verification and sign-off checklist generator for crypto-trading-open. Verifies test suite pass rate, code quality, dependency cleanliness, and generates a formatted Markdown Release Audit Checklist before merging to main."
---

# Release Gate Skill (發布驗收與門檻審核)

## When to Activate
- User asks for release audit / release check / 發布審核 / 準備上線 / release gate / 合併至 main
- Before merging any feature or arena branch into `main`

## Quality Gates Checklist
1. **Pytest Suite**: Run `python3 -m pytest` and verify 100% pass rate.
2. **Trade Safety**: Verify `paper_trading` default protection and risk bounds.
3. **No Secrets/Hardcoded Credentials**: Verify no raw API keys or Web3 private keys in commit diff.
4. **Git Branching**: Ensure merge uses `git merge --no-ff`.

