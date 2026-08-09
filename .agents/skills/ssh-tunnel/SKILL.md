---
name: ssh-tunnel
description: "Guidelines and instructions to start/restart crypto-trading-open services (run_grid_trading.py, run_volume_maker.py, start_web_ui.sh), check status, and set up SSH port-forwarding to access Web UI. Triggered by '開啟機器人', '重啟機器人', '啟動機器人', '開啟交易', '開啟網格交易'."
---

# SSH Tunnel & Web UI Management Skill

## When to Activate
- Triggered by user phrases such as: **"開啟機器人"**, **"重啟機器人"**, **"啟動機器人"**, **"開啟網格交易"**, **"啟動刷量"**, **"開啟介面"**.
- Starting, stopping, or restarting background services or the Web UI (`start_web_ui.sh`).

## Critical Constraints for the AI Agent
> [!IMPORTANT]
> **DO NOT** execute the local SSH tunnel port-forwarding command (`ssh -L ...`) on the server terminal.
> The SSH tunnel command is a client-side command that must be run on the user's local machine.

## Service Commands (Server-Side)
- **Start Web UI**: `bash start_web_ui.sh`
- **Start Grid Trading**: `python3 run_grid_trading.py`
- **Start Volume Maker**: `python3 run_volume_maker.py`
- **PM2 Control**: `pm2 start ecosystem.config.js` / `pm2 restart all` / `pm2 status`

## Client-Side SSH Tunnel Command
Remind the user to run in their local terminal:
```bash
ssh -L 8000:localhost:8000 <user>@<server_ip>
```
Then visit `http://localhost:8000` in their browser.

