---
name: log-analysis
description: "Unified log analysis and troubleshooting for crypto-trading-open. Use when analyzing bot logs, searching for error patterns, performance hotspots, order flow traces, or diagnosing issues from log files (logs/, tmp_output.log, out.txt, PM2 logs)."
---

# Log Analysis Skill

## When to Activate
- User asks to check logs or analyze runtime errors
- Investigating order flow, API errors, or exchange rate limits
- Generating log-based status reports

## Log Locations
- **`logs/`**: Standard logging directory (`logs/bot.log` or service-specific logs)
- **`tmp_output.log` / `out.txt`**: One-off execution or script outputs
- **PM2 Logs**: `~/.pm2/logs/` (`pm2 logs`)

## Essential Log Commands

```bash
# Recent errors
grep -i "error" logs/*.log tmp_output.log out.txt | tail -50

# Exchange connectivity / API errors
grep -E "Binance|OKX|Hyperliquid|Lighter|EdgeX|Backpack" logs/*.log | grep -i "error" | tail -30

# Order execution & fills
grep -E "order|fill|trade" logs/*.log | tail -40

# PM2 logs
pm2 logs --lines 50
```

