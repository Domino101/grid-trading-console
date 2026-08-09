---
name: code-quality
description: "Mandatory code quality checks for crypto-trading-open. Run after every code change. Also serves as over-engineering review: use when user says 'review for over-engineering', 'what can we delete', 'is this over-engineered', 'simplify review', 'ponytail review', 'ponytail audit', 'audit for bloat', 'find bloat', '過度工程', '簡化審查'."
---

# Code Quality Skill

## When to Activate
- After ANY code modification (MANDATORY)
- Before committing code
- When user requests code review
- When user asks for over-engineering review or bloat audit

## Pre-Commit Checklist
- [ ] Run `black .` for formatting if configured
- [ ] Run `flake8 core/ web_ui/ --max-line-length=120` or python linter
- [ ] Run `python3 -m pytest` to ensure test suite passes
- [ ] No `print()` statements in production code (use `logging`)
- [ ] No hardcoded credentials or API keys in code or config
- [ ] Type hints and docstrings present on public module methods

## Strategy & Core Services Validation
- [ ] Services in `core/services/` (`grid`, `volume_maker`, `arbitrage_monitor`, `price_alert`) handle lifecycle cleanly
- [ ] Configuration parsed via `config/` dataclasses/dicts
- [ ] Correct handling of exchange symbols (e.g. `BTC/USDT` vs `BTCUSDT`)

## Risk & Safety Validation
- [ ] All order placement checks enforce `paper_trading` toggle
- [ ] Min notional, price range boundaries, and stop-loss / risk limits enforced before calling exchange adapters
- [ ] Error logging and graceful recovery on exchange API failures

## Database & State Validation
- [ ] All database queries (SQLite `crypto_trading.db` / PostgreSQL) use parameterized placeholders (`?` or `%s`)
- [ ] Proper error handling and connection teardown

## Exchange Adapters Validation
- [ ] Exchange adapters under `core/adapters/exchanges/adapters/` adhere to adapter interfaces
- [ ] Order IDs, prices, and quantities validated and cast correctly
- [ ] WebSocket connections handle disconnections and reconnect gracefully

## Fail-Fast Rules
If any check fails:
1. Stop and report specific failure
2. Fix and re-run full checklist
3. Only declare complete when ALL pass

---

## Over-Engineering Review (ponytail-review)

Review diffs for unnecessary complexity. One line per finding: location, what
to cut, what replaces it. The diff's best outcome is getting shorter.

### Format

`L<line>: <tag> <what>. <replacement>.`, or `<file>:L<line>: ...` for
multi-file diffs.

### Tags

- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

### Examples

❌ "This EmailValidator class might be more complex than necessary, have you
considered whether all these validation rules are needed at this stage?"

✅ `L12-38: stdlib: 27-line validator class. "@" in email, 1 line, real validation is the confirmation mail.`

✅ `L4: native: moment.js imported for one format call. Intl.DateTimeFormat, 0 deps.`

✅ `repo.py:L88: yagni: AbstractRepository with one implementation. Inline it until a second one exists.`

✅ `L52-71: delete: retry wrapper around an idempotent local call. Nothing replaces it.`

✅ `L30-44: shrink: manual loop builds dict. dict(zip(keys, values)), 1 line.`

### Scoring

End with the only metric that matters: `net: -<N> lines possible.`

If there is nothing to cut, say `Lean already. Ship.` and stop.

---

## Repo-Wide Bloat Audit (ponytail-audit)

Like the over-engineering review above, but scans the entire codebase instead
of a diff: a ranked list of what to delete, simplify, or replace with
stdlib/native equivalents.

### Hunt

Deps the stdlib or platform already ships, single-implementation interfaces,
factories with one product, wrappers that only delegate, files exporting one
thing, dead flags and config, hand-rolled stdlib.

### Output

One line per finding, ranked biggest cut first:
`<tag> <what to cut>. <replacement>. [path]`

End with `net: -<N> lines, -<M> deps possible.` Nothing to cut: `Lean already. Ship.`

---

## Boundaries

Over-engineering review and bloat audit scope: complexity only. Correctness
bugs, security holes, and performance are explicitly out of scope — route them
to a normal review pass, not this one. A single smoke test or `assert`-based
self-check is the ponytail minimum, not bloat — never flag it for deletion.
Lists findings, applies nothing. "stop ponytail-review" or "normal mode" to
revert to verbose review style.

