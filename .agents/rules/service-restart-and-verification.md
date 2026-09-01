## Service Restart & Live Verification (修改後重啟與運行驗證標準)

This rule is mandatory across all coding, bugfix, and feature addition sessions in this repository.

### Rule Requirements
Whenever code or configuration affecting active runtime services (Web UI backend, frontend SPA, trading engines, scanner services, PM2 processes) is modified:

1. **Process Inspection**:
   - Check if the affected service is currently active:
     - Web UI backend: `lsof -i :8001` or `pgrep -fa "main.py"`
     - PM2 / Trading Engine: `pm2 list` or `pgrep -fa "run_multiple_grids.py"`
2. **Clean Restart**:
   - Web UI (use repo-relative commands; `/root/...` is NOT this checkout):
     ```bash
     pkill -f "python.*main.py" || true
     sleep 1
     cd "$(git rev-parse --show-toplevel)"   # project root
     cd web_ui/backend && python main.py > ../../logs/web_ui.log 2>&1 &
     # local convenience: bash start_web_ui.sh
     # production: systemctl restart crypto-webui
     ```
   - Frontend SPA (if UI files changed):
     ```bash
     cd web_ui/frontend && npm run build
     ```
   - PM2 / Engines:
     ```bash
     pm2 restart <app_name>
     ```
3. **Live Instance Smoke Testing**:
   - Issue a live HTTP request to the running service port (e.g. `curl http://127.0.0.1:8001/api/...` with auth token).
   - Verify that the live response returns HTTP 200 and reflects the fix.
   - Inspect the tail of the log file for runtime exceptions.
4. **Completion Gate**:
   - NEVER declare the task complete until the live restarted service is verified to be healthy and returning expected data.
