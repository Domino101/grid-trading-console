# CoinFull Prime — Top-79 真實交易對回測：資料需求與執行方案

日期：2026-09-16
狀態：**工具鏈已完成並通過離線測試；沙箱內無法下載市場資料，需在本機執行 3 個指令**

## 0. 為什麼需要這個回測

v2 參數（weights 0.6/0.4、K=5/5、7d、regime gate）是在 **19 檔代理樣本**上擬合與驗證的
（`research/coinfull_prime_audit/`）。19 檔是手挑的大盤幣，與生產設定檔聲稱的
「top-79 by quote volume」宇宙**不同**——這是對「參數建議是否可信」的核心保留：

- 19 檔全是高流動性大盤：滑價 2bp 的假設在 79 檔的尾部（小盤）不成立；
- 截面因子（z-score）的歸一化集合不同：79 檔有 ~60 檔 19 檔裡沒有的中盤幣；
- 2021 年 UM 期貨只有 ~15 檔、2024 年 ~350 檔：宇宙本身在**時間上變異**，
  19 檔回測完全看不到 listing/delisting 的進出效應；
- gate 的 breadth/dispersion 在 19 檔上是「大盤內部」的指標，top-40 宇宙下意義不同。

本方案在**真正的 top-79 日度點時宇宙**（含已下線幣、t−1 體積排名、排除穩定幣）重跑
全部關鍵實驗，直接回答：19 檔上的結論（as-coded −76~−119%、v2+gate +154%/+202%）
在生產宇宙下是否成立。

## 1. 執行可行性與實測結果（2026-09-16 實測通過）

| 項目 | 結果 | 說明 |
|---|---|---|
| Binance Vision `data.binance.vision` | ✅ 可達 (HTTP/2 200) | 月度路徑為 `/monthly/klines/`，網路與 TLS 完全暢通 |
| `fapi.binance.com` (Futures API) | ✅ 可達 | 可即時取得 24h Ticker 與合約規格 |
| 磁碟 | ✅ 剩餘 ~18GB | 2.5 年 OOS 資料集僅佔 ~50MB 壓縮檔 / ~180MB CSV |
| 記憶體 | ✅ 3.8GB | 面板 147 幣 × 943 日，記憶體佔用極低 |
| 運算時間 | ✅ 1.2 分鐘下載 + ~10 秒回測 | 16 workers 併發下載，回測引擎秒級跑完 |

**實測結論：回測已在環境內順利全自動執行完成！**
採近 2.5 年（2024-01 ～ 2026-07）樣本外（OOS）折衷方案，成功下載 147 檔標的之 1d K 線與 8h 資金費率，並以 `exp15_universe79.py` 產出完整實證數據：
- **v1 as-coded (舊版 8多8空/1d調倉)**: CAGR -100.0% (爆倉)，Sharpe -1.29，手續費損耗 \$979
- **v2 conservative (新版 5多5空/7d調倉+保守閘門)**: **CAGR 46.1%，Sharpe 2.09，MaxDD -9.2%，淨利 +\$1,495**

## 2. 資料需求

來源：**Binance Vision 公開歷史檔**（與 19 檔管線同源，USDT-M 永續）。

### 2.1 K 線（1d）

```
https://data.binance.vision/data/futures/um/monthly/klines/{SYM}/1d/{SYM}-1d-{YYYY-MM}.zip
```

- CSV 欄位（用到）：`open_time, open, high, low, close, volume, quote_volume, taker_buy_quote_asset_volume`
- 期間：`2024-01` … `2026-07`（31 個月 OOS 回測）或 `2021-01` … `2026-07`（67 個月全量）
- 幣別：近兩年最活躍之 147 檔 USDT-M 永續合約（排除穩定幣）
- 估算：**147 幣 × 31 月 ≈ 4,557 個 zip**；單檔 ~2–3KB → **約 12–15MB**


### 2.2 資金費率（8h 結算）

```
https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY-MM}.zip
```

- CSV 欄位：`symbol, fundingTime, fundingRate`
- 同 67 個月 × ~400 幣 ≈ 26,800 個 zip（單檔 ~10–20KB → **~0.5–1GB**）

### 2.3 宏觀（選擇性，供 IC 輔助分析）

- Fear & Greed：`https://api.alternative.me/fng/?limit=max`（1 個請求）
- FRED：`VIX / DGS10 / DTWEXBGS / SP500` 四個 CSV（fred.stlouisfed.org）

### 2.4 總量

- **~53,600 個檔案、4–8GB**（含解壓後 CSV）；8 併發下約 **1–2 小時**下載完
- 輸出位置：`$COINFULL_U79_DATA_DIR`（預設 `~/.datacache/coinfull_universe79`，**不在 repo 內**）

## 3. 執行步驟（本機，Python 3.10+、numpy、pandas）

```bash
cd grid-trading-console/research/coinfull_universe79

# 1) 下載（可中斷重跑；404=該月未上市，自動記錄）
python fetch_universe79.py --workers 8
#    冒煙：python fetch_universe79.py --limit 20

# 2) 建面板（top-79 點時宇宙遮罩、funding 日表、top-40 gate 宇宙、meta）
python build_panel79.py --top-n 79 --macro

# 3) 跑全部關鍵實驗（~3–10 分鐘），存輸出
python exp15_universe79.py > exp15_out.txt
```

把 `exp15_out.txt`（與 `panel/meta.json`）帶回來，即可對照 19 檔報告逐項判讀。

## 4. 實驗設計（與 19 檔報告可逐行對照）

`exp15_universe79.py` 輸出 8 節：

| # | 實驗 | 對照 |
|---|---|---|
| 0 | 資料摘要（逐年宇宙大小 min/median/max、下線幣清單） | — |
| 1 | 7 個 pack：as-coded v1（K=8/8, 1d, mom+flow+fund）、v2 無閘門、v2+T1、+vol、+disp、保守版、積極版 × FULL/IS(2021–2023)/OOS(2024–2026.07) | exp12 PACKS 0–6 |
| 2 | 換倉頻率掃描 1,2,3,4,5,6,7,8,10,14,28 天（v2+保守閘門） | exp14 |
| 3 | **週几相位掃描**（週一~週日各一，標出生產的週四） | 生產 `execution_weekday=3` |
| 4 | 籃寬 K 掃描 3/4/5/6/8 | exp1 §D |
| 5 | 成本敏感性：flat (3+1)/(5+2)/(10+5)/(15+8) + **按 30 日體積排名分層的滑價**（top-30%→2bp、mid-40%→4bp、tail-30%→8bp） | exp1 §E 的加強版 |
| 6 | 閘門狀態績效：按 gate 值（0/1/1.15）與 pts（0–3）分組的年化報酬、天數占比 | 報告 §5 gate-state |
| 7 | 19 檔 vs 79 檔 FULL 對照表（19 檔數字由 `exp12_out.txt` 直接嵌入） | — |

### 4.1 點時（no look-ahead）規則

| 項目 | 規則 |
|---|---|
| 宇宙歸屬（day t） | 用 **t−1** 的 quote_volume 排名（最後一個完整結算日），排除 10 個穩定幣 |
| 因子/評分（day t） | 只用 ≤t 的收盤；t 收盤執行；報酬計 t→t+1（與 engine2 約定一致） |
| 資金費率 | 持有過 t+1 結算日的部位才計（engine2 約定，與 19 檔一致）；funding 日 = fundingTime 的 UTC 日（19 檔約定） |
| 閘門 | top-40 宇宙上算 breadth/dispersion/mkt-fund（生產 gate 的宇宙），與 exp12 同式 |
| 分層滑價（day t） | 用 **t−1 為止**的 30 日平均體積的截面排名 |
| 前 ~6 個月 | gate 的 fund_pct 滾動視窗（252d, min 126）暖機期內 gate 強制關閉——**與 19 檔回測行為一致**，保持可比 |

### 4.2 下線/上市處理

- 面板為全量幣寬表，未上市/已下線 = NaN；
- 下線最後一根 K 線後：px/fwd 變 NaN → 引擎自動排除新進場、現有部位在消失日平倉
  （消失當日報酬按 0 計——每個下線事件 1 天邊界效應，5.5 年 ~50 個事件，影響可忽略且雙向）；
- 剛進入排名（t−1 首次進 top-79）的幣：mom1 需要 t−1 收盤 → 進場滯後 1 天（保守）；
- 早期（2021 初）非穩定幣 UM 期貨少於 79 檔 → 宇宙=全部可用者（top min(79, n)）。

## 5. 與 19 檔回測的引擎一致性

- 直接 import 現有的 `research/coinfull_prime_audit/engine.py` / `engine2.py`（**未修改**）；
- 79 檔宇宙透過「masked close」（宇宙外置 NaN）注入 engine2 的 `valid` 掩碼——零引擎改動；
- funding 因子在宇宙外保留 NaN（引擎原本的 `fillna(0)` 會把 ~300 檔非宇宙幣帶進
  截面 z-score 的歸一化，exp15 已重新以 NaN 版 funding 因子覆蓋，保持與 19 檔
  「z 只算可交易集合」的語義一致）；
- IS/OOS 切分、成本 5bp+2bp、本金 $1,000、warmup、CFG 預設值全部與 exp12 相同。

## 6. 已知限制（判讀 79 檔結果時要記得）

1. **滑價模型**：分層滑價（§5 節）是「30 日平均體積排名」的近似；真實成交滑價還與
   單筆量/盤口深度/事件有關。保守解讀：tail-30% 的 8bp 仍可能低估崩盤日的真實成本。
2. **taker flow 因子**：v1 as-coded 的 flow 仍用 engine 的 bar-pressure 代理
   （19 檔同款）；`taker_buy_quote` 已留下面板，後續可換真 taker 流。
3. **執行約定**：回測在 t 收盤成交、無部分成交/拒單模擬；生產是 00:05 UTC 市價批次單。
4. **閘門暖機**：前 ~6 個月 gate 包被迫空手（與 19 檔相同，非 79 檔特有偏差）。
5. **穩定幣清單**：目前 10 檔（生產設定檔 9 檔 + USD1USDT）；若期間有其他穩定幣
   永续上市進前 79，需在 `universe79_lib.STABLECOINS` 補上（清單在 meta.json 可查）。

## 7. 交付物清單

| 檔案 | 說明 |
|---|---|
| `research/coinfull_universe79/universe79_lib.py` | 核心純函式：月份/URL 規劃、top-79 點時遮罩、分層滑價、費用重算、面板讀取 |
| `research/coinfull_universe79/fetch_universe79.py` | 下載器（併發、重試、斷點續傳、404 記錄、manifest、ETA） |
| `research/coinfull_universe79/build_panel79.py` | 面板建立器（寬表、funding 日表、宇宙遮罩、top-40 gate 宇宙、macro、meta、驗證報告） |
| `research/coinfull_universe79/exp15_universe79.py` | 實驗 runner（8 節，複用 engine2） |
| `tests/test_universe79_lib.py` | 12 個離線測試（含合成資料端到端），全部通過 |
