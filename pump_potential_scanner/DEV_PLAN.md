# 幣安拉盤潛力幣信號系統 — 開發計畫

> **狀態**:草案 v0.1
> **建立日期**:2026-06-27
> **對標專案**:`grid_volatility_scanner/`(現有)
> **對標 Bot**:Telegram `neopepper_bot`(截圖)

---

## 一、對標定位

| 項目 | 既有 `grid_volatility_scanner` | 本計畫 `pump_potential_scanner` |
|---|---|---|
| 用途 | 找網格套利高 APR 標的 | 找潛在拉盤幣,提前 0–30 分鐘預警 |
| 輸出 | Rich 終端排行榜 + 排序 | 三色分級(🟠🟢🔴)+ Telegram 推播 |
| 資料源 | 交易所 ticker + kline | 訂單簿 + aggTrade + OI/Funding + 鏈上 + 強平 |
| 是否下單 | 否,純分析 | 否,純信號輸出(與 grid bot 解耦) |
| 執行方式 | `pm2-grid-vol-scanner` | `pm2-pump-scanner`(同模式) |
| 對應 Telegram Bot | — | `neopepper_bot` |

---

## 二、目錄結構

完全復刻 `grid_volatility_scanner/` 的分層,便於日後維護與橫向對照。

```
pump_potential_scanner/
├── pump_scanner.py            # 主掃描器 + 三階段狀態機
├── run_scanner.py             # CLI 入口 + Telegram 推播整合
├── config/
│   ├── pump_config.yaml       # 三階段閾值、token universe、過濾規則
│   └── smart_money.yaml       # 鯨魚 / market maker 地址清單
├── core/
│   ├── smart_money_monitor.py # 階段 1:鏈上聰明錢佈局
│   ├── orderbook_cluster.py   # 階段 1:大單聚類 + 異常撤單
│   ├── oi_funding_monitor.py  # 階段 1:OI/Funding 背離
│   ├── cvd_tracker.py         # 階段 2:CVD 累計買入 Delta
│   ├── volume_spike.py        # 階段 2:1m 量能暴增 (>=10x)
│   ├── liquidation_cascade.py # 階段 3:強平瀑布
│   └── signal_scorer.py       # 三階段評分聚合 + 升級/降級狀態機
├── models/
│   ├── pump_signal.py         # 統一信號資料模型(階段、score、metric)
│   └── token_state.py         # 每個幣的狀態(目前階段、進入時間、metrics)
└── ui/
    └── pump_ui.py             # Rich Live 三色分級表
```

---

## 三、三階段偵測設計

### 🟠 階段 1:潛在拉盤(準備期) — 價格未動,底層異常

價格可能根本還沒動,甚至還在盤整,但底層數據已出現異常。

| 偵測項 | 資料源 | 評分邏輯 |
|---|---|---|
| **鏈上聰明錢佈局** | GMGN Track API + Binance hot wallet 觀察 | smart wallet 淨流入 ≥ 5% 流通量 +0.7 |
| **大單掛單聚類** | Binance Spot `<sym>@depth20@1000ms` | bid 側前 5 檔占比 > 60% 且賣方大單撤銷 +0.65 |
| **OI / Funding 背離** | `/fapi/v1/openInterest` + `/fapi/v1/fundingRate`(WS) | OI 1h 漲 > 8% 且 \|funding\| < 0.01% +0.6 |

**升級條件**:任一偵測評分 ≥ 0.6 即升 🟠。
**去重**:前三名不重複;每幣最多保留 1 個階段 1 標記 30 分鐘。

### 🟢 階段 2:啟動拉盤(爆發初期) — 動能啟動

| 偵測項 | 資料源 | 評分邏輯 |
|---|---|---|
| **CVD 垂直折角** | `<sym>@aggTrade`(以 `buyer_is_maker` 反推) | rolling 1m CVD 斜率突變 > 5σ +1 |
| **Volume Spike** | `<sym>@kline_1m` | 1m vol / 24h avg-per-min ≥ 10x 且漲幅 > 0.5% +1 |

**升級條件**:任一觸發即從 🟠 升 🟢。
**冷卻**:同一 token 進入 🟢 後啟動 30 秒冷卻,避免重複打點。

### 🔴 階段 3:拉飛衝刺(高潮期) — 漲幅 30%+ 動能延續

通常已進入「高危」階段(如 $HEI、$G 漲幅 ≥ 30%),此時監控追漲動能延續與空頭爆倉的連鎖反應。

| 偵測項 | 資料源 | 評分邏輯 |
|---|---|---|
| **強平瀑布** | `<sym>@forceOrder` | 連續 30s 空單爆倉金額 > $500k +1 |
| **多空比極端** | `/futures/data/globalLongShortAccountRatio` | long/short > 1.5 +0.5 |
| **動能延續 vs 主力派發** | 1m/5m 漲幅 + OI 變化 | 漲 > 30% + OI 仍上升 → 延續;OI 開始下降 → 🟡 警示 |

**自動升級**:🟢 進入後 5 分鐘自動升 🔴(對應截圖中「高危」標記)。

---

## 四、Telegram 推播格式

直接對齊 `neopepper_bot` 截圖樣式:

```
🔥拉盤潛力信號 · 06/27 05:30

🟠 潛在拉盤 · 偏多
#1 $BEL +12.4% $0.1565 買撥
#2 $HMSTR +11.9% $0.000169 買撥
...

🟢 啟動拉盤 · 偏多
#8 $BABY +11.8% $0.0140 買撥
...

🔴 拉飛衝刺 · 偏多
#18 $HUMA +18.0% $0.0260 倉中等 買撥
...

信號匯總
潛在:🟢 接連:16 持續:4 🔴 出貨:0
前3: $BEL+12.4%, $HMSTR+11.9%, $STNSR+11.1%
```

**推播觸發**:`pump_scanner.py` → `signal_scorer` 評分變化時 → `run_scanner.py` 內的 `TelegramPusher`(沿用 `neopepper_bot` token)。

---

## 五、共用元件(不重複造輪)

直接複用 `/root/crypto-trading-open` 既有模組:

| 元件 | 路徑 | 用途 |
|---|---|---|
| Binance Adapter | `core/adapters/exchanges/adapters/binance_{base,rest,websocket}.py` | WS 訂閱、REST 輪詢 |
| Logger | `core/logging/logger.py` | 沿用 `ImmediateFileHandler` |
| Cache | `core/infrastructure/cache/` | CVD rolling window(deque 或 Redis) |
| Web UI | `web_ui/backend/` | 不動,此工具純 CLI + Telegram |

---

## 六、開發里程碑

| # | 工作 | 依賴 |
|---|---|---|
| M1 | 訂單簿聚類 + OI/Funding 監聽原型(階段 1 後兩項) | binance_websocket 既有 |
| M2 | CVD tracker + Volume spike(階段 2) | binance_websocket 既有 |
| M3 | 強平瀑布 + 多空比(階段 3) | binance_websocket 既有 |
| M4 | Smart money 整合(`gmgn-track` API + 充值地址) | 需 API key |
| M5 | `signal_scorer` 三階段狀態機 + `pump_signal` / `token_state` 模型 | M1–M3 |
| M6 | `pump_ui` Rich 三色表 + `TelegramPusher` | M5 |
| M7 | pm2 部署 + 與 grid_volatility_scanner 並行驗證 | M6 |

---

## 七、風險與注意

- **Binance WS 上限**:每連線 200 streams;高頻幣優先訂閱,其他走輪詢。
- **aggTrade 高頻**:CVD rolling 採本地 `deque(maxlen=7200)`,避免 Redis 往返。
- **鏈上 hot wallet 清單**:Binance 不公開完整清單,需從鏈上瀏覽器動態維護。
- **本質是機率**:信號只推送、不下單;與 `run_multiple_grids.py` 的 ETH/USDC 網格 bot 維持解耦,避免互相汙染狀態。
- **回測驗證**:M7 前用歷史 aggTrade + forceOrder 回放,驗證 signal_scorer 對 $HEI / $G 這類已知拉盤樣本的召回率。

---

## 八、整合點

| 項目 | 路徑 / 指令 |
|---|---|
| 啟動指令 | `pump_potential_scanner/run_scanner.py --duration 86400` |
| 日誌檔 | `/root/crypto-trading-open/logs/pm2-pump-scanner.log` |
| pm2 process | `pump-scanner`(與 `grid-vol-scanner` 並行) |
| Telegram | 沿用 `neoppeper_bot`,僅需 `sendMessage` 權限的 chat_id |

---

## 九、版本紀錄

| 版本 | 日期 | 變更 |
|---|---|---|
| v0.1 | 2026-06-27 | 初版草案,對標 `grid_volatility_scanner/` 與 `neopepper_bot` 截圖 |
