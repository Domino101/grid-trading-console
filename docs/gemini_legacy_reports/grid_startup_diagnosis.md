# 🔍 網格啟動診斷報告

## 問題 1：命令語法錯誤 (Exit Code 127)

你執行的命令格式錯誤：

```diff
- args: "config/grid/binance-neutral-perp-sol-usdc-100-160.yaml ..."
+ python3 run_multiple_grids.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml
```

系統沒有 `args:` 這個指令，正確的啟動方式是直接用 `python3` 執行腳本。

---

## 問題 2：啟動後無訂單掛出（核心問題）

用正確命令啟動後，腳本**成功連接了交易所**，但最終 **0 個訂單被掛出**。日誌顯示兩個連續的問題：

### 2a. 中性網格初始化產生 0 個訂單

```
grid_strategy_impl - 中性网格：初始不生成订单（等待价格触发或手动下单）
grid_coordinator - 初始订单方向分布: 买0 / 卖0，当前价格 $85.51
```

**原因**：[grid_strategy_impl.py:124-139](file:///root/nofx_recovered/crypto-trading-open/core/services/grid/implementations/grid_strategy_impl.py#L124-L139) 中，`GridType.NEUTRAL` 的初始化邏輯**故意返回空列表**（不掛任何初始訂單），設計上是等待價格波動後由協調器動態掛單。

### 2b. 已有 200 掛單上限被觸發

```
grid_engine_impl - WARNING - 跳過批量下單：該交易對已達200掛單上限
```

**原因**：[grid_engine_impl.py:638-643](file:///root/nofx_recovered/crypto-trading-open/core/services/grid/implementations/grid_engine_impl.py#L638-L643) 中，`_allowed_orders_for_symbol()` 查詢 Binance 發現 SOL/USDC:USDC 交易對**已經有 200 個掛單**，所以即使後續動態掛單也會被攔截。

> [!WARNING]
> 你的 Binance 帳戶上 `SOL/USDC:USDC` 這個交易對**已經有 200 個現存掛單**（可能是之前的網格殘留）。新網格無法再掛任何訂單。

---

## 🔧 解決方案

### 方案 A：先清除舊掛單再啟動（推薦）

在啟動新網格前，先取消 SOL/USDC:USDC 上的所有舊掛單：

```bash
# 使用 Python 腳本清除舊掛單
python3 -c "
import asyncio
from run_grid_trading import create_exchange_adapter
import yaml

async def cleanup():
    with open('config/grid/binance-neutral-perp-sol-usdc-100-160.yaml') as f:
        cfg = yaml.safe_load(f)
    adapter = await create_exchange_adapter(cfg)
    orders = await adapter.get_open_orders('SOL/USDC:USDC')
    print(f'找到 {len(orders)} 個掛單')
    for o in orders:
        await adapter.cancel_order(str(o.id), 'SOL/USDC:USDC')
    print('全部清除完畢')
    await adapter.disconnect()

asyncio.run(cleanup())
"
```

然後再啟動網格：

```bash
python3 run_multiple_grids.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml
```

### 方案 B：使用單網格啟動器（包含更多初始化步驟）

```bash
python3 run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml --no-ui
```

---

## 📝 其他發現

| 項目 | 狀態 |
|------|------|
| API Key 認證 | ✅ 正常（從 .env 讀取） |
| WebSocket 連接 | ✅ 正常（用戶流 + 行情流） |
| 價格數據 | ✅ 正常（當前價 $85.51） |
| 持倉 | ⚠️ 無持倉 |
| 掛單數量 | ❌ 已達 200 上限 |
| 中性網格邏輯 | ℹ️ 設計上初始不掛單 |
