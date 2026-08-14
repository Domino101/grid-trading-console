---
name: dashboard-enhancement
description: "Dashboard frontend and backend development for grid-trading-console. Use when adding new API endpoints, modifying the web UI, creating data visualizations, or enhancing monitoring capabilities for trading bots (Grid Trading, Volume Maker, Price Alerts). Also use when modifying web_ui/backend or web_ui/frontend."
---

# Dashboard Enhancement Skill

## When to Activate
- Adding or modifying API endpoints in `web_ui/backend/` (FastAPI routes, schemas, lifecycle, repositories)
- Modifying frontend views or components in `web_ui/frontend/` (React 19 + TypeScript + Vite + Vanilla CSS)
- Enhancing grid monitoring, hot-plug control, or log display capabilities
- Testing Web UI rendering or API responses

## Web UI Architecture

### Backend (`web_ui/backend/`)
- **Framework**: FastAPI + Uvicorn
- **Entry point**: `web_ui/backend/main.py` (Port 8001)
- **Authentication**: Bearer token (`GRID_API_TOKEN` in `.env`)
- **Main Responsibilities**:
  - Expose authenticated `/api/v1/` REST & WebSocket endpoints (system, grids, dashboard, market, commands)
  - Process & configuration management (`active_grids.json`, hot-reload YAMLs)
  - Market data inspection & precision validation (`/api/v1/market/info/{symbol}`)
  - System status, audit logs (`data/grid_audit.db`), and circuit breaker safety guards
  - Runtime SQLite state persistence (`data/web_ui_runtime.sqlite3`)

### Frontend (`web_ui/frontend/`)
- **Stack**: React 19 + TypeScript + Vite + Vanilla CSS (Glassmorphism design tokens) + Recharts + Lucide-react
- Single-page application built into `web_ui/frontend/dist/` and served by FastAPI backend static mount

## Mandatory Post-Modification Workflow (強制重啟與驗證標準)

任何對 `web_ui/backend` 或 `web_ui/frontend` 的修改，**必須在最後重啟並完成即時驗證**：

1. **若修改了前端 (`web_ui/frontend`)**：
   ```bash
   cd /root/grid-trading-console/web_ui/frontend
   npm run build
   ```
2. **重啟後端服務 (`web_ui/backend`)**：
   ```bash
   # 終止舊進程
   pkill -f "python.*main.py" || true
   sleep 1
   
   # 在背景啟動新進程
   cd /root/grid-trading-console/web_ui/backend
   nohup /root/grid-trading-console/venv/bin/python3 main.py > ../../logs/web_ui.log 2>&1 &
   ```
3. **執行運行態即時驗證 (Live Smoke Test)**：
   - 檢查監聽埠口：`lsof -i :8001`
   - 發送即時 API 請求驗證：
     ```bash
     TOKEN=$(grep GRID_API_TOKEN /root/grid-trading-console/.env | cut -d '=' -f2)
     curl -s "http://127.0.0.1:8001/api/v1/system/health" -H "Authorization: Bearer $TOKEN"
     ```
   - 檢查最新日誌：`tail -n 20 /root/grid-trading-console/logs/web_ui.log`
4. **確認即時端點返回正常後，方可宣告修改完成**。
