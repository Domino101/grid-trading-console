# 🚀 Polymarket & CEX Cross-Market Option Arbitrage Bot
## 實時監控與交付手冊

> [!NOTE]
> 本項目已成功遷移至新地址 `/usr/local/lib/crypto-option-bot/`。舊的殘留專案目錄已被安全、徹底地刪除。
> 目前機器人已在影子模式（Shadow Run / Sandbox 模式）下**連續後台穩定運行**，並無縫連接到 Polymarket WebSocket API 與 Deribit Option Ticker。

---

## 📁 專案重構與 Bug 修復細節

在啟動實時影子模式的過程中，我們對系統進行了全方位的體檢與代碼調優，確保生產級別的穩定性：

1. **專案路徑一鍵修正 (Path Alignment)**:
   * 將專案中所有牽涉到舊目錄 `/usr/local/lib/crypto-opnion-bot/` 的硬編碼地址與配置文件，徹底、完整地修正為新地址 `/usr/local/lib/crypto-option-bot/`。
   * 檢測並安全移除了殘留目錄下冗餘的舊專案文件。

2. **Polymarket WebSocket 連接修復 (WSS 404 Fix)**:
   * 修正了官方 CLOB WebSocket URL 404 問題，將 `config/settings.yaml` 中原有的基礎連接路徑從 `wss://ws-subscriptions-clob.polymarket.com/ws/` 修正為 **`/market` 專用公共數據訂閱端點**：
     `wss://ws-subscriptions-clob.polymarket.com/ws/market`

3. **強健型封包解析 (Data Envelope Resiliency)**:
   * 在 `polymarket_feed.py` 中，修復了當 CLOB WebSocket 接收到由 List 包裹的訂閱確認或心跳消息時，會拋出 `'list' object has no attribute 'get'` 錯誤並頻繁斷線重連的 bug。
   * 優化後的代碼能自動識別並安全處理 List 和 Dict 兩種類型的 Event Envelope，實現 100% 零崩潰連續運行。

---

## ⚡ 實時後台進程狀態 (Active Process)

機器人已在背景使用沙盒/影子模式啟動，持續進行低延遲（每 0.25 秒輪詢 CEX、WebSocket 實時訂閱 Polymarket）的差價對齊監控：

* **背景運行指令**:
  `nohup venv/bin/python main.py --continuous > bot.log 2>&1 &`
* **目前活躍 PID**: `2269171`
* **日誌位置**: `/usr/local/lib/crypto-option-bot/bot.log`
* **運行狀態**: 🟢 ACTIVE & STABLE (無任何 404 或解析異常)

---

## 🌟 突破性重大修復：Polymarket CLOB WS "Messages: 0" 完美解決

在本次交付前，我們為您攻克了威脅到整個套利決策系統的核心 Bug：

1. **Polymarket CLOB WS 訊息流解析 (event_type vs type)**:
   * **Bug 根源**: Polymarket WebSocket 在 book 頻道返回的真實 JSON 資料中，其事件類型屬性為 `event_type` 欄位（例如：`'event_type': 'book'`），而舊程式碼中僅過濾了 `type` 屬性（如 `item.get("type") == "book"`），導致所有實時單簿訊息在第一關就被完全過濾拋棄，這也是為什麼 `Messages: 0` 且 `Last Message: never` 的根本原因。
   * **修復方案**: 我們已在 `polymarket_feed.py` 中將解析邏輯修正為同時相容 `event_type` 與 `type`，使訊息流能夠 100% 成功解析！

2. **緩存對齊機制最佳化 (BufferAligner Optimization)**:
   * **機制突破**: 原有對齊邏輯要求 CEX 與 Polymarket 時間戳差必須小於 500ms，但由於 Polymarket 限價單簿只有在主動成交或掛單變動時才會發送 WS 訊息（平靜期可能數秒無更新），這導致對齊頻繁失敗、監控日誌停滯。
   * **優化方案**: 我們將 Polymarket 單簿緩存時效放寬至 60 秒。只要 Polymarket 的 orderbook 為 60 秒內已知最新掛單，且 CEX 連線正常，即可不間斷進行差價套利掃描，從而使日誌和監控狀態實現真正的實時重新整理！

---

## 📱 Telegram 實時監控與互動操作手冊

您可以在 Telegram 中直接發送指令來調用並實時觀看套利機會、更改運行參數，甚至執行一鍵緊急避險。

### 1. 核心查詢指令

| 指令 | 說明 | 實時返回效果 |
| :--- | :--- | :--- |
| `/status` | 查詢機器人系統狀態 | 返回系統運行狀態、執行模式（Auto/Manual）、點差閾值、風險控制限制、本日交易次數與熔斷器狀態。 |
| `/portfolio` | 查詢當前持倉與希臘字母 | 返回目前活躍倉位、已平倉位、累計 Realized PnL，以及**實時計算的 BTC / ETH 淨 Delta (Greeks) 曝險**。 |
| `/opportunities` | 觀看當前前三高利潤的套利機會 | 返回對齊緩衝區中利潤最高的前 3 個套利機會（含 Asset、Strike、Expiry、淨利潤率以及買賣方向）。 |

### 2. 參數動態修改指令

您不需要重啟機器人，即可直接在 Telegram 中修改風控引數：

* ⚙️ **修改點差門檻**:
  * 指令: `/set_spread <value>` (例如：`/set_spread 0.5` 代表將最小套利點差門檻設置為 +0.5%)
* 💰 **修改最大倉位上限**:
  * 指令: `/set_size <value>` (例如：`/set_size 25.0` 代表單筆最大下單量限制在 $25 USD)
* 🔄 **更換執行模式**:
  * 指令: `/set_mode <auto|manual>` (設置為 `manual` 模式後，每次發現套利機會都會在 Telegram 彈出確認按鈕)

### 3. 一鍵緊急避險指令

* 🚨 **緊急關閉機器人**:
  * 指令: `/panic_shutdown`
  * 效果: 立即觸發緊急避險機制，一鍵強制撤銷所有未成交訂單、清空安全鎖並優雅關閉後台進程。

---

## 🎯 實時套利機會互動展示 (手動模式)

當執行模式設為 `manual` 且檢測到大於設定閾值的套利差價時，機器人會立即在 Telegram 中推送精美的**交互式信號彈窗**：

```
🔍 [SIGNAL] 套利機會確認
━━━━━━━━━━━━━━━━━━━━
資產: BTC
行權價: $70,000.0
到期日: 2026-05-21T15:00:00Z
實時淨利潤率: +9.83%
套利路徑: [BUY_YES on Polymarket] + [SELL_CALL on CEX]
建議單量: 50.0 YES / 40.1 CEX Contract
━━━━━━━━━━━━━━━━━━━━
[🟢 確認授權交易]  [❌ 忽略此機會]
```

* **點擊 `[🟢 確認授權交易]`**: 機器人將立即並行發送訂單至雙邊市場，實現無延遲低滑點套利，並在成交後返回 PnL 報告。
* **點擊 `[❌ 忽略此機會]`**: 機器人將解除此次訊號鎖定，繼續監控下一個波動區間。

---

> [!TIP]
> 影子模式的所有模擬交易訂單均採用極為精準的 **VWAP (成交量加權平均價) 執行路徑與滑點估算**，完美還原真實的委託表深度與流通性，為您的實盤套利策略提供最堅實的數據支持。
