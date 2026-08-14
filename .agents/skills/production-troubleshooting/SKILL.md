---
name: production-troubleshooting
description: "Production troubleshooting playbook for grid-trading-console. Use when trading services crash, hang, encounter exchange API rate limits/IP bans, produce unexpected orders, or when Web UI is unreachable."
---

# Production Troubleshooting Skill

## When to Activate
- Trading script (`run_multiple_grids.py`, `run_grid_trading.py`, `run_volume_maker.py`, etc.) crashes or hangs
- Exchange API issues (Binance IP ban HTTP 418/429, OKX authorization errors, Hyperliquid/Lighter connection drops)
- PM2 services failing or Web UI unreachable (`start_web_ui.sh`)

## Emergency Response Checklist
1. **Check Process Alive**: `pgrep -fa python` or `pm2 status`
2. **Check Web UI**: `curl -I http://localhost:8001` or check `logs/web_ui.log`
3. **Check Logs**: `tail -n 50 logs/web_ui.log` or `tail -n 50 logs/pm2-multi-grid.log`
4. **Check IP Ban**: `curl -s -w "\nHTTP: %{http_code}\n" "https://api.binance.com/api/v3/ping"`

## Common Decision Trees

### Binance IP Ban (HTTP 418 / 429)
- Symptom: Binance adapter calls return HTTP 418 or 429.
- Action: Consult `diagnosis_binance_ip_ban_and_apikey_20260503.md`. Switch IP/proxy or reduce polling frequency.

### Process Crashed / Restart Failed
- Check logs for missing module or invalid syntax.
- Test script manually under venv: `/root/grid-trading-console/venv/bin/python3 run_multiple_grids.py --help`.

### Web UI Offline (Port 8001)
- Check port availability: `lsof -i :8001` or `netstat -tlpn | grep 8001`.
- Restart Web UI: `bash start_web_ui.sh` or run manually via `web_ui/backend/main.py`.
