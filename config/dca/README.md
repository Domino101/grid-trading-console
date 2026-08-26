# DCA 機器人設定目錄 (`config/dca/`)

本目錄存放 DCA（Dollar-Cost Averaging）策略的 YAML 設定檔，與 `config/grid/`
完全隔離（§4.1 命名空間）。

---

## 1. 檔案命名規範

```text
dca-{exchange}-{direction}-{symbol}-{yyyymmddHHMMSS}.yaml
```

* `dca-` 前綴保證 `bot_id = Path(config_path).stem` 與網格 `grid_id` 永不碰撞。
* 範例：`dca-binance-long-ETH/USDC:USDC-20260826000000.yaml`

## 2. YAML 欄位

所有欄位以 `dca:` 區段包覆。金額與費率使用字串數值以保留 `Decimal` 精度：

```yaml
dca:
  exchange: binance          # binance / lighter / backpack / hyperliquid
  symbol: ETH/USDC:USDC
  direction: long            # long / short / both
  account_mode: paper        # 必填：paper / live（缺漏視為 unknown，拒絕載入）
  leverage: 10
  margin_mode: isolated
  quantity_precision: null   # null 時執行器依交易所自動填入
  price_decimals: null
  fee_rate: "0.0004"
  base_order_amount: "20"    # Amount per trade
  amount_unit: quote         # quote(USD) / base(幣)
  dca_mode: order            # order / position
  averaging_method: fixed_value  # fixed_value / fixed_coin
  max_safety_orders: 20      # Max DCA orders
  price_deviation_percent: "0.8"
  price_deviation_multiplier: "1.2"
  order_size_multiplier: "1.0"
  take_profit_percent: "1.0"
  take_profit_order_type: limit
  stop_loss_percent: null
  max_position_notional: null
  repeat_after_take_profit: true
  cooldown_seconds: 300
  max_cycles: null
  both_tiebreak: long_first
  active_safety_orders_window: 5
  monitoring_interval: 15
  indicator_refresh_interval: 60
  order_health_check_interval: 300

  entry:
    mode: indicator           # immediate / indicator
    logic: all                # all(AND) / any(OR)
    timeframe: 15m            # 所有指標共用
    fetch_limit: 300
    price_source: close       # close / hlc3 / ohlc4
    confirm_bars: 1
    max_wait_seconds: null
    indicators:
      - type: rsi
        period: 14
        oversold: 30
        overbought: 70
        condition: level
```

完整欄位語意見 `docs/dca_bot_implementation_plan_zh-TW.md` §4.2–§4.5。

## 3. 控制檔

監督者讀取 `config/active_dca.json`（`{config_path: bool}`）熱插拔啟動/停止
DCA 機器人，語意與 `active_grids.json` 對稱。該檔已被 `.gitignore` 排除。
