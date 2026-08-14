# 網格交易策略配置目錄 (`config/grid/`)

本目錄包含網格交易系統的標準策略設定範本（YAML 格式）。

---

## 1. 檔案命名規範

策略檔案命名採用四段式標準結構：
```text
<交易所名稱>-<多空方向>-<市場類型>-<幣種交易對>.yaml
```

### 命名範例：
* `binance-long-perp-sol-usdc.yaml`：幣安 SOL/USDC 永續做多網格
* `binance-neutral-perp-eth-usdc-usdc-1680-3300.yaml`：幣安 ETH/USDC 永續中性震盪網格
* `lighter-long-perp-btc.yaml`：Lighter DEX BTC 永續做多網格
* `hyperliquid-long-spot-btc.yaml`：Hyperliquid BTC 現貨做多網格
* `backpack-long-perp-hype.yaml`：Backpack HYPE 永續做多網格

---

## 2. 標準策略 YAML 欄位定義

每個策略檔案定義核心 `grid_system` 物件：

```yaml
grid_system:
  # 基礎設定
  symbol: "SOL/USDC:USDC"           # 交易對符號（CCXT 標準格式）
  exchange: "binance"              # 交易所 (binance / lighter / hyperliquid / backpack / okx)
  grid_type: "long"                # 策略方向: long (做多) / short (做空) / neutral (中性)
  account_mode: "live"             # 模式: live (實盤) 或 paper (模擬)
  
  # 價格區間與網格密度
  lower_price: 100.0               # 價格下限
  upper_price: 160.0               # 價格上限
  grid_interval: 3.0               # 網格間距 (或透過 grid_count 推算)
  order_amount: 0.1                # 每格下單基礎幣數量
  
  # 槓桿與保證金
  leverage: 5                      # 槓桿倍數 (1-125)
  margin_mode: "cross"             # cross (全倉) / isolated (逐倉)
  
  # 精度與保護
  price_precision: 2               # 價格小數位數
  quantity_precision: 4            # 數量小數位數
  min_notional: 5.0                # 最小名義下單價值 (USDT/USDC)
```

---

## 3. 實盤 (LIVE) 與模擬 (PAPER) 模式說明

* **PAPER 模式**：讀取交易所即時公開行情，但在本機記憶體中進行虛擬撮合與虛擬資金計算，完全不消耗真實資產。
* **LIVE 模式**：直接向交易所下單真實撮合。
  * **前置安全要求**：伺服器環境變數必須設定 `GRID_LIVE_TRADING_ENABLED=1`（位於專案根目錄 `.env`）。

---

## 4. 目錄結構

* `defaults.yaml`：全域預設安全參數（速率保護、滑點控制、重試機制）。
* `archive/`：已結束或過期之一次性測試與歷史實驗策略檔案封存區。
