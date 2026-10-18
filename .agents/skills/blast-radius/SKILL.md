---
name: blast-radius
description: "Find what a change could break somewhere else before it ships, beyond the diff, and prove the one fact it's safe because of by running real code instead of writing it up. Use for 'blast radius of X', 'what could this break', '版本對比', '變更稽核', '變更影響分析', or reviewing any diff touching trading, risk, or ledger code."
---

# Blast Radius (變更爆炸半徑與運行態安全驗證)

Find what a change breaks somewhere else, before it ships. Use for "blast radius of X", "what could this break", "版本對比", "變更影響", or reviewing any diff touching trading core, risk checks, or ledger state.

Listing the callers is not the job. The agent can grep or run `graphify path` in seconds. The job is the breakage grep won't show you.

## Don't Trust Your Own Writeup (嚴禁空言推論)

A blast-radius writeup that sounds right is worthless. It reads as convincing whether or not it's true. So don't hand back a purely narrative writeup. Find the one or two facts the whole safety depends on and prove them by running code.

### 5-Level Certainty Ladder (確信度階梯)

For each fact the change's safety depends on, climb as high up this list as possible:

1. **You said so**: Worthless on its own.
2. **You pointed at the line**: A real `file:line`, or the external library/schema source.
3. **You showed the bad case can't happen**: You walked the failure step by step and proved the execution path does not reach.
4. **You ran it**: A script or test that calls the real code and fails loud if you're wrong (e.g. running Python script or contract test).
5. **You reproduced it in the running app**: Live instance verification against running service (`curl` / logs / DB inspection).

## Execution Steps

1. **Read the change**:
   - Inspect `git diff <base>...<head>` (or staged diff).
   - Identify added/modified/deleted symbols, and what runs differently underneath (including side-effects, DB schema, or wire format).
2. **Find the one fact it's safe because of**:
   - Most risky-looking changes are safe because of a single foundational fact (e.g., "this accounting write only executes when `dedupe_key` does not exist in `bot_events`", or "this adapter function is only invoked in paper trading").
   - Find that linchpin fact. If it holds, most hypothetical risks are cleared at once.
3. **Look where grep stops**:
   - Trace cross-boundary implications: JSON payloads across REST/WebSocket, SQLite/PostgreSQL schema columns, enum values across frontend React and FastAPI backend.
   - Trace asynchronous lifecycle: asyncio task cancellation, reconnect storms, DB transaction boundaries, and thread locks.
4. **Be honest about each risk**:
   - Grade probability and blast radius cost (especially trade safety, account balance, or ledger corruption).
   - Separate verified risks from checked-and-cleared items. Never make up callers or assumptions.
5. **Prove the one fact**:
   - Write a short verification script or run an isolated test that exercises the code and records the exact output.
6. **For high-risk changes (core/services/grid, strategy_ledger, trade_fills)**:
   - Run adversarial checks (`interrogate` 4-tier judgment).

## Deliverable Format

- **What it does**: What changed, highlighting subtle or non-obvious side-effects.
- **The one fact it's safe because of**: State the linchpin fact, report its Certainty Level (Level 1–5), and paste the code/test proof. If unproven, mark `UNPROVEN`.
- **Risks**: Each names how it breaks, `file:line`, likelihood & severity, and how to verify.
- **Cleared**: What was checked and why it is safe.
- **Before you merge**: The cheapest test or reproduction script that catches real regression.
