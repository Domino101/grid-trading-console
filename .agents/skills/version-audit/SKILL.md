---
name: version-audit
description: "Version-to-version code audit and change analysis for crypto-trading-open. Performs token-efficient diff preprocessing, structured chunked analysis, and generates comprehensive audit reports comparing two git refs (branches, tags, commits)."
---

# Version Audit (版本對比稽核) Skill

## When to Activate
- 用戶說「版本對比 / 版本稽核 / compare versions / 變更報告 / what changed」
- Comparing local codebase against a remote branch (e.g. `origin/main`)
- Comparing two git tags, commits, or branches
- Generating a structured change/audit report between releases

## Workflow & Guidelines
1. **Change Manifest**: Inspect modified files by running `git diff --stat <base> <head>` or `git status`.
2. **Chunked Module Audit**: Audit changes by module (`core/services/`, `core/adapters/`, `web_ui/`, `config/`, `tests/`).
3. **Audit Dimensions**:
   - Safety & Security: credentials in diff, paper_trading toggle enforcement.
   - Logic Correctness: order quantity rounding, price precision, error recovery.
   - Dependencies: clean dependencies in `requirements.txt`.
4. **Report Generation**: Output structured Markdown change report.

