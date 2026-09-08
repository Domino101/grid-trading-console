# CoinFull Prime（市場中性策略）本地端自動化開發計畫書

## 一、 專案概述與核心精神

### 1.1 策略理念

**CoinFull Prime** 是一套跨幣種截面多空市場中性量化策略（Cross-Sectional Market Neutral Alpha Strategy）。

* **操作原則**：每日在流動性最高、市值前 79 名的主流加密貨幣合約（排除穩定幣）中，依據多維度量化因子進行綜合評分。  
* **對沖機制**：等權重買入綜合得分最高的 8 個最強幣種（多頭籃子，各佔 12.5%），同時等權重賣出綜合得分最低的 8 個最弱幣種（空頭籃子，各佔 12.5%）。  
* **收益來源**：完全對沖整體加密市場的系統性漲跌（Market Beta ≈ 0），純粹賺取強勢資產相較於弱勢資產的價差擴大收益（Alpha）。

### 1.2 本地直連架構（選擇一）優勢

* **零外部中介**：不依賴 GitHub Actions、GCS 或外部伺服器，所有計算均在本地記憶體完成。  
* **零網路暴露風險**：無須開啟路由器端口、無須反向代理或 DDNS，徹底杜絕外部攻擊面。  
* **零延遲與高可靠度**：排程直接在本地喚醒，獲取公開數據後毫秒級送入換倉引擎，避免雲端上傳或授權 Token 過期風險。

---

## 二、 系統架構與流程圖

                 \[本地定時排程器 (Cron / APScheduler)\]

                         │ (每日 UTC 00:00)

                         ▼

             【CoinFull Prime 核心量化引擎】

                         │

        ┌────────────────┼────────────────┐

        ▼                ▼                ▼

  \[幣安行情與動能\]    \[資金費率介面\]    \[Taker買賣流/大戶比\]

  (Ticker & Klines) (PremiumIndex) (Futures Market Data)

        │                │                │

        └────────────────┼────────────────┘

                         ▼

          【特徵工程：去極值 (Winsorization)】

                         ▼

          【橫截面標準化 (Cross-Sectional Z-Score)】

                         ▼

          【四維信號加權打分 (Score \= Σ w\_i \* Z\_i)】

                         ▼

        【生成 Top 8 Long \+ Bottom 8 Short 清單】

                         │ (記憶體即時傳遞)

                         ▼

                【本地換倉引擎 (Rebalancer)】

                         │ (計算目標權重與差額訂單)

                         ▼

                  \[幣安合約下單 API\]

---

## 三、 四大客觀信號定義與數學模型

為杜絕社群推文造假與非結構化文本雜訊，全策略 100% 採用交易所撮合系統與合約微觀結構之真實數值：

### 3.1 信號矩陣與權重

| \# | 信號名稱 | 權重 | 指標計算邏輯 | 方向 | 數據源 (全公開免Key) |
| :---- | :---- | :---: | :---- | :---: | :---- |
| 1 | **價格動能 (Momentum)** | **35%** | 結合短期 (7D) 與中期 (30D) 漲跌幅： `Mom = 0.6 * R_7d + 0.4 * R_30d` | 正向 | `GET /fapi/v1/ticker/24hr` `GET /fapi/v1/klines` |
| 2 | **主動買賣流 (Taker Flow)** | **35%** | 主動市價吃單額比率： `Flow = TakerBuyVol / TakerSellVol` | 正向 | `GET /futures/data/takerlongshortRatio` |
| 3 | **資金費率反向 (Funding Rate)** | **20%** | 當前及近 24 小時資金費率均值： `Signal_fund = -1.0 * FundingRate` | **反向** | `GET /fapi/v1/premiumIndex` |
| 4 | **大戶多空博弈 (Top Trader Ratio)** | **10%** | 頂級交易者帳戶多空比： `Signal_liq = TopLongShortRatio` | 正向 | `GET /futures/data/topLongShortAccountRatio` |

### 3.2 統計標準化流程

1. **標的池（Universe 79）篩選**： 每日自 USDT 永續合約中，依 24 小時報價成交額（Quote Volume）排序前 79 名，自動排除法幣抵押與算法穩定幣。  
2. **縮尾處理（Winsorization）**： 對各特徵截面實施雙側 2.5% 截斷，消除特定極端幣種對均值與標準差的干擾： $$X\_{\\text{clipped}} \= \\max(\\min(X, Q\_{0.975}), Q\_{0.025})$$  
3. **橫截面 Z-Score 計算**： $$Z\_{i, k} \= \\frac{X\_{i, k} \- \\mu\_k}{\\sigma\_k}$$  
4. **加權總分合成**： $$\\text{Score}*i \= 0.35 Z*{i, \\text{Mom}} \+ 0.35 Z\_{i, \\text{Flow}} \+ 0.20 Z\_{i, \\text{Funding}} \+ 0.10 Z\_{i, \\text{Liq}}$$  
5. **多空分配**：  
   * 做多名單：綜合得分降冪排序第 1 至 8 名（各分配多頭名義資金 12.5%）。  
   * 做空名單：綜合得分降冪排序第 72 至 79 名（各分配空頭名義資金 12.5%）。

---

## 四、 本地專案模組設計與程式碼規範

### 4.1 專案目錄配置

trading\_bot/

├── config/

│   └── strategy\_config.yaml         \# 策略配置（權重、標的數、槓桿比例）

├── core/

│   ├── services/

│   │   ├── signals/

│   │   │   ├── \_\_init\_\_.py

│   │   │   ├── coinfull\_prime.py    \# 核心量化信號計算模組

│   │   │   └── runner.py            \# 定時觸發器與排程協調器

│   │   └── rebalancer/

│   │       ├── \_\_init\_\_.py

│   │       └── engine.py            \# 換倉執行器 (計算差額單與 TWAP 執行)

│   └── exchange/

│       └── binance\_client.py        \# 幣安私有下單與帳戶查詢介面

├── data/

│   └── signal\_history/              \# 本地 JSON 歷史存檔 (供回測與對帳)

└── main.py                          \# 機器人主入口

### 4.2 核心量化模組實作 (`coinfull_prime.py`)

import logging

from datetime import datetime, timezone

import numpy as np

import requests

logger \= logging.getLogger("CoinFullPrime")

class CoinFullPrimeQuant:

    def \_\_init\_\_(self, config=None):

        self.weights \= config or {

            "momentum": 0.35,

            "flow": 0.35,

            "funding": 0.20,

            "liquidation": 0.10

        }

        self.base\_url \= "https://fapi.binance.com"

        self.stables \= {

            "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", 

            "EURUSDT", "BUSDUSDT", "AEURUSDT"

        }

    def fetch\_universe(self, top\_n=79):

        """獲取流動性前 Top 79 幣種"""

        url \= f"{self.base\_url}/fapi/v1/ticker/24hr"

        resp \= requests.get(url, timeout=10).json()

        usdt\_pairs \= \[

            item for item in resp 

            if item\['symbol'\].endswith('USDT') and item\['symbol'\] not in self.stables

        \]

        usdt\_pairs.sort(key=lambda x: float(x\['quoteVolume'\]), reverse=True)

        return \[item\['symbol'\] for item in usdt\_pairs\[:top\_n\]\]

    def fetch\_features(self, symbols):

        """批次獲取交易所合約微觀結構數據"""

        \# 1\. 資金費率

        premium\_data \= requests.get(f"{self.base\_url}/fapi/v1/premiumIndex", timeout=10).json()

        funding\_map \= {item\['symbol'\]: float(item.get('lastFundingRate', 0)) for item in premium\_data}

        

        \# 2\. 24h 動能

        ticker\_data \= requests.get(f"{self.base\_url}/fapi/v1/ticker/24hr", timeout=10).json()

        mom\_map \= {item\['symbol'\]: float(item.get('priceChangePercent', 0)) for item in ticker\_data}

        features \= \[\]

        for s in symbols:

            \# 3\. 主動買賣資金流 (Taker Buy/Sell)

            flow\_val \= 1.0

            try:

                flow\_resp \= requests.get(

                    f"{self.base\_url}/futures/data/takerlongshortRatio?symbol={s}\&period=1d\&limit=1",

                    timeout=5

                ).json()

                if flow\_resp and isinstance(flow\_resp, list):

                    flow\_val \= float(flow\_resp\[0\].get('buySellRatio', 1.0))

            except Exception:

                flow\_val \= 1.0

            \# 4\. 大戶多空帳戶比

            liq\_val \= 1.0

            try:

                liq\_resp \= requests.get(

                    f"{self.base\_url}/futures/data/topLongShortAccountRatio?symbol={s}\&period=1d\&limit=1",

                    timeout=5

                ).json()

                if liq\_resp and isinstance(liq\_resp, list):

                    liq\_val \= float(liq\_resp\[0\].get('longShortRatio', 1.0))

            except Exception:

                liq\_val \= 1.0

            features.append({

                "symbol": s,

                "momentum": mom\_map.get(s, 0.0),

                "flow": flow\_val,

                "funding": \-1.0 \* funding\_map.get(s, 0.0), \# 反向處理

                "liquidation": liq\_val

            })

        return features

    @staticmethod

    def \_winsorize\_and\_zscore(series, limits=(0.025, 0.975)):

        arr \= np.array(series, dtype=float)

        lower \= np.quantile(arr, limits\[0\])

        upper \= np.quantile(arr, limits\[1\])

        clipped \= np.clip(arr, lower, upper)

        std \= np.std(clipped)

        return np.zeros\_like(clipped) if std \== 0 else (clipped \- np.mean(clipped)) / std

    def compute\_ranks(self, raw\_data):

        """橫截面合成打分"""

        n \= len(raw\_data)

        z\_mom \= self.\_winsorize\_and\_zscore(\[x\['momentum'\] for x in raw\_data\])

        z\_flow \= self.\_winsorize\_and\_zscore(\[x\['flow'\] for x in raw\_data\])

        z\_fund \= self.\_winsorize\_and\_zscore(\[x\['funding'\] for x in raw\_data\])

        z\_liq \= self.\_winsorize\_and\_zscore(\[x\['liquidation'\] for x in raw\_data\])

        for i in range(n):

            score \= (

                self.weights\['momentum'\] \* z\_mom\[i\] \+

                self.weights\['flow'\] \* z\_flow\[i\] \+

                self.weights\['funding'\] \* z\_fund\[i\] \+

                self.weights\['liquidation'\] \* z\_liq\[i\]

            )

            raw\_data\[i\]\['score'\] \= round(float(score), 4\)

            raw\_data\[i\]\['signals'\] \= {

                "mom\_z": round(float(z\_mom\[i\]), 2),

                "flow\_z": round(float(z\_flow\[i\]), 2),

                "fund\_z": round(float(z\_fund\[i\]), 2),

                "liq\_z": round(float(z\_liq\[i\]), 2\)

            }

        raw\_data.sort(key=lambda x: x\['score'\], reverse=True)

        for idx, item in enumerate(raw\_data):

            item\['rank'\] \= idx \+ 1

        return {

            "version": "1.0",

            "timestamp": datetime.now(timezone.utc).isoformat(),

            "strategy": "coinfull\_prime\_local",

            "data": {

                "long\_list": raw\_data\[:8\],

                "short\_list": raw\_data\[-8:\]

            },

            "status": "success"

        }

    def run\_daily\_pipeline(self):

        logger.info("啟動 CoinFull Prime 日常信號運算...")

        universe \= self.fetch\_universe(79)

        features \= self.fetch\_features(universe)

        result \= self.compute\_ranks(features)

        logger.info(f"計算完成。Top 1 多頭: {result\['data'\]\['long\_list'\]\[0\]\['symbol'\]}, Top 1 空頭: {result\['data'\]\['short\_list'\]\[-1\]\['symbol'\]}")

        return result

---

## 五、 換倉執行與風控設計

### 5.1 換倉邏輯

1. **權重平滑過渡**： 每日計算出目標 8 多 8 空後，比對當前現有持倉：  
   * 續留在名單中的標的：調整倉位至目標金額（12.5%）。  
   * 移出名單的標的：市價或 TWAP 平倉。  
   * 新入選的標的：開倉至目標權重。  
2. **總槓桿限制**：  
   * 建議多空總槓桿控制在 1.0x ～ 1.5x（多頭 0.5x~~0.75x，空頭 0.5x~~0.75x），避免 Altcoin 波動劇烈導致保證金率過低。  
3. **滑點保護**：  
   * 下單採 TWAP（分批分時間掛單）或設定最大市價滑點容忍度（如 0.2%），防止小幣流動性衝擊。

### 5.2 異常防呆機制

* **API 降級防護**：若個別小幣種之 Taker Flow 獲取超時，預設填補中性值 1.0（Z-score ≈ 0），不阻斷整體管線。  
* **熔斷保護（Circuit Breaker）**：若全市場出現系統性流動性抽離（Top 79 幣種 24h 總成交量下降超過 60%），當日暫停換倉，維持現有避險倉位。

---

## 六、 實施時程與驗證節點

* **階段一：本地模組整合與數據驗證（第 1 天）**  
  * 將 `coinfull_prime.py` 放入本地專案，單元測試 API 連線與 79 幣矩陣計算效能（確保 5 秒內完成）。  
* **階段二：換倉模擬運算（第 2～3 天）**  
  * 串接本地 `runner.py` 與 `rebalancer`，執行 Dry-Run（日誌模擬下單），驗證目標權重與多空對稱性。  
* **階段三：實盤小資金測試（第 4～10 天）**  
  * 以最小名義本金實盤運行 7 天，檢驗資金費率收支、滑點摩擦與市場中性 Beta 是否維持在 0 附近。  
* **階段四：正式上線與自動監控（第 11 天起）**  
  * 開啟 24/7 自動排程運作，建立本地日誌異常報警。

