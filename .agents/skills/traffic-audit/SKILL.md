---
name: traffic-audit
description: "Network traffic auditing, bandwidth monitoring, and cloud egress optimization for grid-trading-console. Use when investigating traffic bill spikes, slow network response, API timeout patterns, network overhead, bandwidth audits, or when the user says 網路流量 / 流量統計 / 流量監測 / 爆流量 / 網路費 / bandwidth / traffic audit / network egress / network costs."
---

# Network Traffic Audit & Bandwidth Monitoring Skill

This skill provides guidelines and tools to capture, analyze, and optimize network traffic of the grid-trading-console bot and exchange API connections (Binance, OKX, Hyperliquid, Lighter, EdgeX, Backpack, O1Exchange, Paper).

## When to Activate
- When user asks about network traffic costs, egress bandwidth, or server network usage
- When troubleshooting network latency, WebSocket disconnections, or API timeout patterns

## Diagnostic Commands
```bash
# Check tcpdump availability
sudo tcpdump --version

# Run traffic capture script if available
python3 .agents/skills/traffic-audit/scripts/traffic_stats.py --duration 60 --output scratch/traffic_report.md
```

## Common Issues & Mitigations
- **High Exchange API Traffic**: Reduce polling frequency in config or switch from REST polling to WebSocket streams.
- **High SSH / Log Traffic**: Avoid dumping massive log files directly to terminal stdout.
