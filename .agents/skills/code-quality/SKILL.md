---
name: code-quality
description: "Mandatory code quality checks for grid-trading-console. Run after every code change. Also serves as over-engineering review: use when user says 'review for over-engineering', 'what can we delete', 'is this over-engineered', 'simplify review', 'ponytail review', 'ponytail audit', 'audit for bloat', 'find bloat', '過度工程', '簡化審查'."
---

# Code Quality Skill

## When to Activate
- After ANY code modification (MANDATORY)
- Before committing code
- When user requests code review
- When user asks for over-engineering review or bloat audit

## Pre-Commit Checklist
- [ ] Run `python3 -m pytest` to ensure test suite passes
- [ ] No `print()` statements in production core services (use `logging`)
- [ ] No hardcoded credentials or API keys in code or config (use `.env`)
- [ ] Type hints and docstrings present on public module methods

## Strategy & Core Services Validation
- [ ] Services in `core/services/` (`grid`, `volume_maker`, `arbitrage_monitor`, `price_alert`) handle lifecycle cleanly
- [ ] Configuration parsed via `config/` dataclasses/dicts
- [ ] Correct handling of exchange symbols (e.g. `BTC/USDT` vs `BTCUSDT`)

## Risk & Safety Validation
- [ ] All order placement checks enforce `paper_trading` toggle
- [ ] Min notional, price range boundaries, and stop-loss / risk limits enforced before calling exchange adapters
- [ ] Error logging and graceful recovery on exchange API failures

## Database & State Validation
- [ ] All database queries (`data/web_ui_runtime.sqlite3`, `data/grid_audit.db`) use parameterized placeholders (`?`)
- [ ] Proper error handling and connection teardown

## Exchange Adapters Validation
- [ ] Exchange adapters under `core/adapters/exchanges/adapters/` adhere to adapter interfaces
- [ ] Order IDs, prices, and quantities validated and cast correctly
- [ ] WebSocket connections handle disconnections and reconnect gracefully

## Mandatory Service Restart & Live Verification (修改後重啟與運行態驗證標準)
**修改完程式碼後，絕對不可僅憑靜態測試就宣告完成！必須嚴格執行以下動作：**
1. **檢查受影響的運行中進程**：
   - 檢查 Web UI 後端：`lsof -i :8001` 或 `ps aux | grep main.py`
   - 檢查交易引擎 / PM2 進程：`pm2 list` 或 `ps aux | grep run_multiple_grids`
2. **自動重啟受影響服務**：
   - 若 Web UI 後端運行中：終止舊進程並重啟 `cd web_ui/backend && nohup /root/grid-trading-console/venv/bin/python3 main.py > ../../logs/web_ui.log 2>&1 &`
   - 若前端有修改：執行 `cd web_ui/frontend && npm run build` 重新構建靜態資源
   - 若策略/引擎修改：執行 `pm2 restart <service>` 或重啟對應策略進程
3. **執行運行態即時驗證 (Live Smoke Test)**：
   - 發送真實 HTTP/WebSocket 請求驗證（例如 `curl http://127.0.0.1:8001/api/...`）
   - 檢查即時日誌（`tail -n 30 logs/web_ui.log`）確認無未捕獲異常或崩潰
4. **宣告完成的前提條件**：
   - [ ] 相關單元/合約測試通過
   - [ ] 受影響的在線服務已重啟
   - [ ] 運行態真實端點（Live Instance）請求驗證通過且回傳符合預期

## Fail-Fast Rules
If any check fails:
1. Stop and report specific failure
2. Fix the issue before declaring task complete
