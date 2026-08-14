---
name: log-analysis
description: "Unified log analysis and troubleshooting for grid-trading-console. Use when analyzing bot logs, searching for error patterns, performance hotspots, order flow traces, or diagnosing issues from log files (logs/web_ui.log, logs/pm2-multi-grid.log, logs/grid_*.log, tmp_output.log, out.txt, PM2 logs)."
---

# Log Analysis Skill

## When to Activate
- User asks to check logs or analyze runtime errors
- Investigating order flow, API errors, or exchange rate limits
- Generating log-based status reports

## Log Locations
- **`logs/web_ui.log`**: Web UI FastAPI backend logs
- **`logs/pm2-multi-grid.log` & `logs/pm2-multi-grid-error.log`**: Multi-Grid PM2 engine logs
- **`logs/grid_*.log`**: Individual grid execution logs
- **`tmp_output.log` / `out.txt`**: One-off execution or diagnostic outputs
- **PM2 Logs**: `pm2 logs` or `~/.pm2/logs/`

## Essential Log Commands

```bash
# Check Web UI backend log
tail -n 50 logs/web_ui.log

# Check PM2 Multi-grid engine logs
tail -n 50 logs/pm2-multi-grid.log
tail -n 50 logs/pm2-multi-grid-error.log

# Recent errors across all logs
grep -i "error" logs/*.log tmp_output.log 2>/dev/null | tail -50

# Exchange connectivity / API errors
grep -E "Binance|OKX|Hyperliquid|Lighter|EdgeX|Backpack|O1Exchange" logs/*.log 2>/dev/null | grep -i "error" | tail -30

# Order execution & fills
grep -E "order|fill|trade" logs/*.log 2>/dev/null | tail -40
```
