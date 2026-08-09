# Saved Prompt Workflows

Trigger these with `/` in the AI agent chat panel.

## /risk-audit-full
Run a complete risk audit across all trading services (Grid Trading, Volume Maker, Arbitrage Monitor, Price Alert). Check current exposure, daily PnL, circuit breaker status, and exchange orderbook state. Produce a markdown report with PASS/FAIL status.

## /paper-to-live-prep
Validate readiness for switching from paper trading to live trading:
1. Run full risk audit across configured exchanges (Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack)
2. Verify all environment variables set (API keys, secret keys, Web3 private keys)
3. Confirm paper trading execution stability
4. Check database consistency and error log trends
5. Produce go/no-go recommendation

## /strategy-performance
Analyze the performance of all enabled trading strategies:
1. Query positions and trade execution logs for recent activity
2. Calculate win rate, avg PnL, grid capture rate per strategy
3. Compare against configured strategy thresholds
4. Flag underperforming symbols or grid ranges
5. Suggest parameter adjustments

## /deploy-gcp
Deploy latest code to GCP production or PM2 runner:
1. Verify clean git working tree and passed tests
2. Run deployment checklist
3. Restart PM2 processes (`pm2 restart ecosystem.config.js`) or background services
4. Verify Web UI health endpoint (`start_web_ui.sh`)
5. Monitor logs for 5 minutes
6. Report deployment status

## /code-review-changes
Review the most recent git diff:
1. Check for hardcoded credentials or unmasked API keys
2. Verify exchange adapter and strategy interface compliance
3. Check type hints and docstrings
4. Validate risk checks not bypassed
5. Check logging uses logging module
6. Produce review report with issues and suggestions

## /bot-audit
Audit the bot's current runtime status, active trading mode, and recent order placement/fill health:
1. Verify if `run_*.py` processes and Web UI are active (`pgrep -fa python` or `pm2 status`).
2. Check the active configuration in `config/` or `.env`.
3. Check recent order executions and position statuses across active exchanges.
4. Cross-reference order status with logs (`logs/` or `tmp_output.log`) to diagnose system or API issues.
5. Present a structured Markdown report along with clear, actionable recommendations.

## /feature-start
Initialize a new topic branch for feature development or refactoring:
1. Ensure working tree is clean (`git status`).
2. Checkout latest `main` and pull updates (`git checkout main && git pull origin main`).
3. Create new topic branch (`git checkout -b arena/<topic_name>`).
4. Confirm test baseline (`python3 -m pytest -q`).

## /release-merge-prep
Prepare a feature/arena branch for merging into `main`:
1. Run automated release gate checks and pytest suite (`python3 -m pytest`).
2. Inspect financial safety red lines (Risk limits, Exchange WS circuit breaker, Min notional checks).
3. Generate standard Markdown Release Audit Sign-off Checklist.
4. Provide git merge commands (`git merge --no-ff`).


