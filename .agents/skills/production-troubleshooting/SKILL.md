---
name: production-troubleshooting
description: "Production troubleshooting playbook for crypto-trading-open. Use when trading services crash, hang, encounter exchange API rate limits/IP bans, produce unexpected orders, or when Web UI is unreachable."
---

# Production Troubleshooting Skill

## When to Activate
- Trading script (`run_grid_trading.py`, `run_volume_maker.py`, etc.) crashes or hangs
- Exchange API issues (Binance IP ban HTTP 418/429, OKX authorization errors)
- PM2 services failing or Web UI unreachable (`start_web_ui.sh`)

## Emergency Response Checklist
1. **Check Process Alive**: `pgrep -fa python` or `pm2 status`
2. **Check Web UI**: `curl http://localhost:8000` or check `start_web_ui.sh`
3. **Check Logs**: `tail -n 50 logs/*.log` or `tail -n 50 tmp_output.log`
4. **Check IP Ban**: `curl -s -w "\nHTTP: %{http_code}\n" "https://api.binance.com/api/v3/ping"`

## Common Decision Trees

### Binance IP Ban (HTTP 418 / 429)
- Symptom: Binance adapter calls return HTTP 418 or 429.
- Action: Consult `diagnosis_binance_ip_ban_and_apikey_20260503.md`. Switch IP/proxy or reduce polling frequency.

### Process Crashed / Restart Failed
- Check logs for missing module or invalid syntax.
- Test script manually: `python3 run_grid_trading.py --help` or run under venv: `venv/bin/python3 run_grid_trading.py`.

### Web UI Offline
- Check port availability: `netstat -tlpn | grep python` or `lsof -i :8000`.
- Restart Web UI: `bash start_web_ui.sh`.

