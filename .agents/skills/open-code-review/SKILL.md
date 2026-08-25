---
name: open-code-review
description: "AI-powered code review on Git changes using the `ocr` CLI from alibaba/open-code-review. Use when user requests code review, PR review, diff audit, commit check, line-level code verification, or security/concurrency/bug inspection. Performs precise line-level review and can delegate to host AI agent."
---

# Open Code Review (ocr) Skill

AI-powered code review skill for `grid-trading-console`. Invokes Alibaba's `open-code-review` CLI (`ocr`) to parse Git diffs, filter unsupported files, bundle related changes, and perform line-level precision code quality & security reviews.

## When to Activate
- User says: "review my code", "review changes", "ocr review", "代碼審查", "檢查 diff", "PR review", "審查提交"
- Before merging feature branches or completing major refactoring
- As part of `release-gate` and `code-quality` checks

## Common Workflows

### 1. Workspace Mode (Review uncommitted & staged changes)
Review all staged, unstaged, and untracked changes in the working copy:
```bash
# Preview what files will be reviewed
ocr review --preview

# Run review with agent output format and background context
ocr review --audience agent -b "Reviewing active working copy changes for bugs and concurrency issues"
```

### 2. Branch & PR Comparison Mode
Review changes between two git refs (e.g. `main` vs current feature branch):
```bash
ocr review --audience agent -b "PR review for feature branch" --from main --to HEAD
```

### 3. Single Commit Mode
Review a specific commit against its parent:
```bash
ocr review --audience agent -b "Review commit fixes" --commit <commit_hash>
```

### 4. Full Directory Scan (`ocr scan`)
Review entire files in a directory when there is no Git diff:
```bash
ocr scan --path core/services/grid/implementations/
```

### 5. Delegation Mode (`ocr delegate`)
Use OCR's deterministic file selection & ruleset while delegating the LLM review pass to the host agent:
```bash
# Step 1: Preview reviewable files and ref metadata
ocr delegate preview --format json

# Step 2: Extract file specific review rules
ocr delegate rule --format json core/services/grid/coordinator/grid_coordinator.py
```

## Review Severity & Categorization
When reporting review findings, group by severity:
- **Critical / High**: Immediate security vulnerabilities, data race/lock issues, unhandled order execution failures, null pointer crashes.
- **Medium**: Performance bottlenecks, missing validation, inefficient SQL/array queries.
- **Low**: Style nitpicks or minor documentation improvements (optional).
