# CoinFull Prime 0.3.14 部署與 live 驗證紀錄（2026-10-21）

- 版本：0.3.14
- 進程：PM2 `grid-web-ui`（FastAPI，0.0.0.0:8001）
- 帳號：`binance_live`
- 排程：每週四 UTC 00:05；下次 2026-10-22T00:05:00Z
- 實盤開關：`GRID_LIVE_TRADING_ENABLED=1`

## 1. 部署步驟

1. 先確認沒有第二個 Web UI/交易進程在跑，避免雙寫。
2. `pm2 restart grid-web-ui --update-env` 載入 0.3.14。
3. 啟動順序改為 `CoinFullPrimeService.initialize()` → `restore_scheduler()`：
   - config 缺失會在啟動時 fail-closed。
   - 狀態 `initialized=true`，不再等到盤中才初始化。
4. 驗證 `GET /openapi.json` 回傳 `info.version=0.3.14`。

## 2. 啟動後狀態

`GET /api/v1/coinfull-prime/status`：

- `initialized=true`
- `is_running=true`
- `scheduler_config.account_id=binance_live`
- `scheduler_config.dry_run=false`
- `next_scheduled_run_utc=2026-10-22T00:05:00Z`
- `risk_control.stop_loss_running=true`
- 日誌出現「Binance 合約實盤適配器已成功建立並連接」與「盤中動態硬止損守護已啟動」

## 3. Live 對帳發現

第一次 `POST /api/v1/coinfull-prime/reconcile?account_id=binance_live`：

- 帳本 active：QUSDT 202、SANDUSDT 338、ERAUSDT 315，另有 BTC/BTW/GTC 三個 quantity=0 幽靈成分。
- 交易所：這三個 symbol 已無部位；其餘 5 個部位都屬於外部網格/DCA（protected）。
- 判定：3 個 missing_on_exchange + 3 個 zero_quantity ghost。

## 4. 修復動作

新增 `tools/coinfull_reconcile_repair.py`（預設 dry-run，`--apply` 才寫入，SQLite 先備份）：

- 只關閉「交易所已無部位」與「quantity=0」的帳本成分。
- `close_basket_entry()` 保留 entry_price / realized_pnl，不捏造損益。
- 每筆寫入 `reconcile_close` 事件，reason 為 `missing_on_exchange` 或 `zero_quantity`。
- 不處理 quantity_mismatch；那需要人工判斷。

套用結果：

- 關閉 6 筆（QUSDT、SANDUSDT、ERAUSDT、BTCUSDT、BTWUSDT、GTCUSDT）。
- 備份：`data/backups/web_ui_runtime_pre_coinfull_reconcile_repair_20261021T131924Z.sqlite3`。
- 修復後再次 reconcile：`status=ok`、`managed_count=0`、`missing_on_exchange=[]`。

## 5. 發現並修復的 mirror 復活 bug

第一次修復後，重啟服務又把 6 個成分復活。原因有兩個：

1. `_export_basket_mirror()` 在沒有 active 成分時直接 return，沒有清掉舊鏡像。
2. `load_active_basket()` 只要 active 為空就呼叫 `_backfill_side_files()`，把 stale 鏡像重新匯入。

修正：

- `load_active_basket()` 只有「整個帳號完全沒有 basket 列」才允許鏡像回填；已有 closed 列時回傳空集合。
- `_export_basket_mirror(account_id)` 在該帳號沒有 active 成分時移除該帳號 key，保留其他帳號；鏡像可為空。
- 新增 2 項 P0 mirror 復活回歸測試，以及 `close_basket_entry` 的 legacy repair 測試。

修正後再重啟，`managed_symbols=[]`、reconcile `status=ok`，沒有再復活。

## 6. 資金費率 Carry 同步

`POST /api/v1/coinfull-prime/funding/sync?account_id=binance_live&since_days=7`：

- ccxt 4.4.30 的 binance 沒有 `fetch_income`，已加 `fetch_ledger(..., {"incomeType": "FUNDING_FEE"})` fallback。
- 首次同步：29 筆 FUNDING_FEE，淨額約 +3.92 USDT。
- 第二次同步：recorded=0，確認 fill_id 冪等。
- fill 寫入 `trade_fills`，`side=funding`、`role=funding`；snapshot 的 `funding_income_usdt` 會在下次換倉時落帳。

## 7. 最終 live 狀態

`GET /status`：

- `initialized=true`、`is_running=true`
- `managed_symbols=[]`
- `risk_control.stop_loss_running=true`
- 下次換倉 2026-10-22T00:05:00Z

`POST /reconcile`：

- `status=ok`
- `managed_count=0`
- `exchange_count=5`
- `protected_on_exchange=[SOLUSDC, BRUSDT, DOGEUSDC, UNIUSDC, XRPUSDC]`
- `missing_on_exchange=[]`、`quantity_mismatch=[]`

外部網格/DCA 的 5 個部位未被修改。

## 8. 注意事項

- 本次沒有在盤中建立新倉；下一次換倉仍由排程器在週四 UTC 00:05 執行。
- 目前 managed_symbols 為空，-12% 止損守護會在下次換倉建立新多頭後才有實際監控對象。
- Ledger audit 仍有非 CoinFull Prime 的 L2/L13/L8；CoinFull Prime 的 L5/L6 已為 0。
- 若要再次修復帳本殘留，先跑 dry-run，再 `--apply`；不要直接改 SQLite。
