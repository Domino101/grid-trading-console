---
name: deployment-check
description: "Deployment validation checklist, post-deploy health check, and emergency rollback protocol for grid-trading-console. Use when deploying, restarting services (PM2 / background Python scripts), or handling emergency rollback."
---

# Deployment Check & Emergency Rollback Skill

## When to Activate
- User asks to deploy, restart, or check production status
- Restarting services via PM2 (`ecosystem.config.js`) / systemd (`scripts/deployment/crypto-webui.service`), or using `start_web_ui.sh` for local only
- Verifying production health or handling emergency rollback

## Pre-Deployment Checklist
- [ ] `paper_trading` protection confirmed; `GRID_LIVE_TRADING_ENABLED` left unset/`0` for test runs
- [ ] Environment variables set in `.env` (`GRID_API_TOKEN`, exchange API keys/secrets, optional `DATABASE_URL`/`REDIS_URL`)
- [ ] Code syntax clean and unit tests pass (`./scripts/ci.sh`, or `python3 -m unittest ...`)
- [ ] Config files in `config/` validated
- [ ] Frontend build succeeds (`cd web_ui/frontend && npm run build`)

## Deployment Steps
- **Local/experimental**: `bash start_web_ui.sh` (nohup, not for production).
- **Production Web UI**: systemd unit `scripts/deployment/crypto-webui.service` (`systemctl restart crypto-webui`).
- **Engines**: PM2 (`ecosystem.config.js`) or background Python scripts; PM2 only runs `multi-grid-engine` (optionally the volatility scanner) — do not double-run a single `run_grid_trading.py` against the same YAML.
```bash
# Engines via PM2
pm2 start ecosystem.config.js
pm2 restart all
pm2 status

# Web UI (local only)
bash start_web_ui.sh

# Run strategy service (for ad-hoc testing)
python3 run_multiple_grids.py
python3 run_grid_trading.py
python3 run_volume_maker.py
```

## Post-Deployment Verification
- [ ] `pgrep -fa python` or `pm2 status` shows running services
- [ ] Web UI accessible (`curl -I http://localhost:8001` or `/api/v1/system/health`)
- [ ] Check logs (`tail -n 50 logs/web_ui.log`, `tail -n 50 logs/pm2-multi-grid.log`)
- [ ] No API authentication or IP ban errors (Binance/OKX HTTP 401/418/429)

## Emergency Rollback Sequence
1. **Stop Services**:
   ```bash
   pm2 stop all  # or pkill -f run_
   ```
2. **Database Backup**:
   ```bash
   cp data/web_ui_runtime.sqlite3 data/web_ui_runtime_rollback_$(date +%F_%H%M%S).sqlite3
   ```
3. **Revert Git Commit** (stay on the current release branch; do not force `main` if this is a topic branch):
   ```bash
   git checkout <release_or_topic_branch>
   git revert HEAD --no-edit
   ```
4. **Restart Services in Safe (Paper) Mode**:
   ```bash
   pm2 start ecosystem.config.js
   systemctl restart crypto-webui   # if production systemd is in use
   ```
