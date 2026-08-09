---
name: dashboard-enhancement
description: "Dashboard frontend and backend development for crypto-trading-open. Use when adding new API endpoints, modifying the web UI, creating data visualizations, or enhancing monitoring capabilities for trading bots (Grid Trading, Volume Maker, Price Alerts). Also use when modifying web_ui/app.py or web_ui templates."
---

# Dashboard Enhancement Skill

## When to Activate
- Adding or modifying API endpoints in `web_ui/app.py`
- Modifying frontend views or templates in `web_ui/templates/`
- Enhancing monitoring, control, or log display capabilities
- Testing Web UI rendering or API responses

## Web UI Architecture

### Backend (`web_ui/app.py`)
- **Framework**: Flask / FastAPI
- **Control Script**: `start_web_ui.sh`
- **Main Responsibilities**:
  - Expose API endpoints for process management (`run_grid_trading.py`, `run_volume_maker.py`, etc.)
  - Render real-time market tickers, grid parameters, and active positions
  - Provide system status, log streaming, and circuit breaker status

### Frontend (`web_ui/templates/`, `web_ui/static/`)
- Responsive CSS / JS dashboard layout
- Auto-refresh or WebSocket updates for market data and bot state

## Development Guidelines

1. **Adding New Endpoints**: Edit `web_ui/app.py` to register routes. Return JSON or rendered HTML templates.
2. **Updating UI Components**: Edit files in `web_ui/templates/` or `web_ui/static/`.
3. **Security**: Never expose API keys or secret tokens in HTML templates or public JSON responses.
4. **Verification**: Run `bash start_web_ui.sh` or check `curl http://localhost:<port>/api/...`.

