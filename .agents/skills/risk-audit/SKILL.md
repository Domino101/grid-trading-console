---
name: risk-audit
description: "Comprehensive risk audit checklist for crypto-trading-open. Run before going live, after strategy modifications, or when investigating trading losses."
---

# Risk Audit Skill

## When to Activate
- User mentions risk audit, loss investigation, or live trading readiness
- Before switching `paper_trading` from true to false
- After modifying parameters for Grid Trading, Volume Maker, or Arbitrage Monitor

## Risk Audit Checklist

### 1. Safety Controls
- [ ] `paper_trading: true` verified as default state
- [ ] Emergency stop-loss triggers configured and active
- [ ] Max drawdown / daily loss threshold defined

### 2. Order & Exchange Bounds
- [ ] Min notional per exchange respected (e.g. Binance minimum $5-$10 order value)
- [ ] Price & quantity precision tick sizes validated against exchange specifications
- [ ] Max concurrent grid orders / positions capped

### 3. API & Credential Security
- [ ] No API secrets or Web3 private keys hardcoded in code or committed to git
- [ ] Read/Trade-only API key permissions enabled (Withdrawals disabled on exchange)

## Audit Output Format
Produce a report with:
- Target Exchange(s) & Trading Pair(s)
- Mode (`paper` vs `live`)
- Risk Gate Pass / Fail Matrix
- Recommendations & Action Items

