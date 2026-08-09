---
name: stability-check
description: "Stability, performance bottleneck, and resource monitoring checks for crypto-trading-open. Detects memory/thread leaks, file descriptor exhaustion, system sluggishness, IDE server lag, high CPU load, server latency, and untracked files. 處理系統變慢、變頓、性能瓶頸、IDE 卡頓、連線延遲、伺服器耗能過高、未忽略檔案清理、CPU/記憶體效能優化與排查。"
---

# Stability Check & Resource Monitoring Skill

This skill provides guidelines and tools for monitoring the resource footprint of crypto-trading-open to detect memory leaks, thread leaks, high CPU load, or IDE/server slowness.

## When to Activate
- When running stability tests or verifying long-running bot instances (`run_grid_trading.py`, `run_volume_maker.py`, etc.)
- Inspecting system load, CPU/RAM usage, or memory leaks
- Troubleshooting IDE server lag, high untracked file counts, or high CPU utilization

## Key Resource Checks

### 1. Process CPU & Memory Usage
```bash
# Top CPU/RAM Python processes
ps aux | grep python | sort -k3 -rn | head -10

# Memory & thread status for PID
cat /proc/<PID>/status | grep -E "VmRSS|VmHWM|Threads"
```

### 2. Untracked Files Cleanup
High numbers of untracked files can slow down git and IDE watchers:
```bash
# Check untracked files count
git status -s | grep "^??" | wc -l

# Clean temporary log/output files if safe
rm -f tmp_output.log out.txt
```


