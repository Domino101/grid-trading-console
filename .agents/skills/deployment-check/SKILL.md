---
name: deployment-check
description: "Deployment validation checklist, post-deploy health check, and emergency rollback protocol for grid-trading-console. Use when deploying, restarting services (PM2 / background Python scripts), or handling emergency rollback."
---

# Deployment Check & Emergency Rollback Skill

## When to Activate
- User asks to deploy, restart, or check production status
- Restarting services via PM2 (`ecosystem.config.js`) or `start_web_ui.sh`
- Verifying production health or handling emergency rollback

## Pre-Deployment Checklist
- [ ] `paper_trading: true` confirmed for initial deployment / test runs
- [ ] Environment variables set in `.env` (`GRID_API_TOKEN`, exchange API keys/secrets)
- [ ] Code syntax clean and unit tests pass (`python3 -m pytest`)
- [ ] Config files in `config/` validated
- [ ] Frontend build succeeds (`cd web_ui/frontend && npm run build`)

## Deployment Steps
Services are managed via PM2 (`ecosystem.config.js`) or background Python scripts:
```bash
# Start/Restart via PM2
pm2 start ecosystem.config.js
pm2 restart all
pm2 status

# Start Web UI (Port 8001)
bash start_web_ui.sh

# Run specific strategy service
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
3. **Revert Git Commit**:
   ```bash
   git checkout main
   git revert HEAD --no-edit
   ```
4. **Restart Services in Safe (Paper) Mode**:
   ```bash
   pm2 start ecosystem.config.js
   ```
