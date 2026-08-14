# Worklog — 2026-07-06 三網格啟動鏈崩潰修復

## 摘要

`python3 run_multiple_grids.py` 同時啟動三個中性對沖網格(ETH/USDC、COAI/USDT、SOL/USDC)時,
觸發多個層級的語法/介面/屬性 bug,沿啟動鏈逐步浮現,共修復 **7 個獨立 bug**。
最終三個 grid 全部「🎉 网格协调器启动成功」,進程 2890408 持續運行。

| 階段 | 觸發錯誤 | 修復 |
|------|---------|------|
| 1 | `SyntaxError` (直譯器層) | 修字串閉合 |
| 2 | `FileNotFoundError` (logger 層) | 修檔名非法字元 |
| 3 | `missing positional arguments` (子模組建構層) | 補 4 個子模組建構簽名 |
| 4 | `'XxxMonitor' object has no attribute 'check_*'` (監控迴圈層) | 補 3 個 wrapper 方法 |
| 5 | `'GridResetManager' object has no attribute 'initialize_grid_orders'` (重置層) | 補 `initialize_grid_orders` 方法 |
| 6 | `'GridCoordinator' object has no attribute 'manager'` (coordinator 屬性層) | 補 `capital_protection_manager` / `take_profit_manager` / `engine.initialize` |
| 7 | `'GridConfig' object has no attribute 'monitoring_interval'` (config 屬性層) | 補 `monitoring_interval` 預設 |

---

## 觸發情境

嘗試以單一 `run_multiple_grids.py` 進程同時承載三個 grid config:
- `config/grid/binance-neutral-perp-eth-usdc-1800-3160.yaml`
- `config/grid/binance-neutral-perp-COAI-USDT-0.2-0.6.yaml`
- `config/grid/binance-neutral-perp-sol-usdc-100-160.yaml`

推測來源:近期對 `core/services/grid/coordinator/` 進行了重構(模組拆分、簽名變更、屬性改為 Optional),
但 `grid_coordinator.py` 的呼叫端、監控迴圈、首次啟動流程未同步更新,留下大量 dangling references。

---

## Bug #1 — `run_multiple_grids.py` line 434 字串未閉合

### 原因
`KeyboardInterrupt` 例外處理中,`print("` 開頭的字串被換行切斷到下一行,
Python 直譯器把 `print("` 視為「未結束的字串字面值」。

### 症狀
```
File "/root/crypto-trading-open/run_multiple_grids.py", line 434
    print("
          ^
SyntaxError: unterminated string literal (detected at line 434)
```

### 修法
合併為一行並加上 `\n` 換行符:
```python
# run_multiple_grids.py:430-434
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 收到鍵盤終止訊號，正在安全退出多網格系統...")
```

### 影響
- 啟動前就 SyntaxError,連交易所連線都沒進到。
- 修完後才進入後續階段。

---

## Bug #2 — Log 檔名含有路徑分隔符號

### 原因
`grid_coordinator.py:93` 把 `self.config.symbol`(perpetual 格式 `ETH/USDC:USDC`)直接傳入
`get_logger(f"GridCoordinator.{self.config.symbol}")`,而 `core/logging/logger.py:73` 又把
logger name 直接當檔名拼接:
```python
log_file = os.path.join(self.config.log_dir, f"{self.name}.log")
```
`/` 被作業系統解讀為路徑分隔符號,嘗試寫入不存在的子目錄 `ETH/` 而崩潰。

### 症狀
```
[Errno 2] No such file or directory:
'/root/crypto-trading-open/logs/GridCoordinator.ETH/USDC:USDC.log'
```

### 修法
在 coordinator 將 symbol 中的非法字元替換為 `_`:
```python
# grid_coordinator.py:93-96
safe_symbol = self.config.symbol.replace("/", "_").replace(":", "_")
self.logger = get_logger(f"GridCoordinator.{safe_symbol}")
```
新檔名範例:`GridCoordinator.ETH_USDC_USDC.log`

### 影響
- 之前單一 grid(只有 ETH)沒問題(剛好 `ETH/USDC:USDC` 也是這個字串),
  但日後若新增其他 symbol 會立即重現。
- 屬於「沒觸發但永遠會壞」的 latent bug。

---

## Bug #3 — `GridResetManager` 缺少 5 個必要參數

### 原因
`grid_coordinator.py:109` 呼叫 `GridResetManager(self)`,但其 `__init__` 簽名需要 6 個:
```python
# grid_reset_manager.py:29
def __init__(self, coordinator, config, state, engine, tracker, strategy):
```

### 症狀
```
GridResetManager.__init__() missing 5 required positional arguments:
'config', 'state', 'engine', 'tracker', and 'strategy'
```

### 修法
傳入完整 6 個參數:
```python
# grid_coordinator.py:109-111
self.reset_manager = GridResetManager(
    self, self.config, self.state, self.engine, self.tracker, self.strategy
)
```

---

## Bug #4 — 4 個子模組建構簽名錯誤

### 原因
`grid_coordinator.py` 內多個子模組呼叫只傳了 `self`,但各自需要不同參數:

| 子模組 | 正確簽名 |
|--------|---------|
| `PositionMonitor(engine, tracker, config, coordinator)` | 4 個必要參數 |
| `BalanceMonitor(engine, config, coordinator, update_interval=60)` | 3 必 + 1 選 |
| `StopLossMonitor(engine, config, coordinator)` | 3 個必要參數 |
| `ScalpingOperations(coordinator, scalping_manager, engine, state, tracker, strategy, config)` | 7 個必要參數 |

### 症狀
```
PositionMonitor.__init__() missing 3 required positional arguments:
'tracker', 'config', and 'coordinator'
```

### 修法
1. 4 個子模組改用正確簽名建立
2. `scalping_manager` **無條件建立**(`grid_reset_manager` 與 `balance_monitor` 會直接呼叫
   `scalping_manager.xxx()` 而無 None 保護,line 414, 512, 263-265, 329-331, 341, 370 等)
3. `scalping_ops` **僅在 `is_scalping_enabled()` 時建立**(`scalping_ops` 內部 line 150, 155, 186
   等對 `self.scalping_manager` 沒有 None 保護,僅在 `is_scalping_enabled` 才會被 coordinator 觸發)

```python
# grid_coordinator.py:108-130
self.reset_manager = GridResetManager(
    self, self.config, self.state, self.engine, self.tracker, self.strategy
)
self.position_monitor = PositionMonitor(self.engine, self.tracker, self.config, self)
self.balance_monitor = BalanceMonitor(self.engine, self.config, self)
self.stop_loss_monitor = StopLossMonitor(self.engine, self.config, self)

# 剝頭皮管理器(無條件建立,內部僅在啟用時做事)
self.scalping_manager = ScalpingManager(self.config)

# 剝頭皮操作(僅在啟用時建立)
self.scalping_ops: Optional[ScalpingOperations] = None
if self.config.is_scalping_enabled():
    self.scalping_ops = ScalpingOperations(
        self, self.scalping_manager, self.engine, self.state,
        self.tracker, self.strategy, self.config
    )
```

---

## Bug #5 — `balance_monitor.check_initial_balance()` 不存在

### 原因
`BalanceMonitor` 重構後,原本的 `check_initial_balance` 被改名為 `update_balance()`(涵蓋電路斷路器保護、
WebSocket 緩存優先、風控初始化等邏輯,line 161/250 內部會呼叫 `_initialize_managers_capital`)。

### 症狀
```
'BalanceMonitor' object has no attribute 'check_initial_balance'
```

### 修法
`grid_coordinator.py` 改呼叫實際存在的方法:
```python
# grid_coordinator.py:142
# 1. 检查账户余额与仓位
await self.balance_monitor.update_balance()
```

---

## Bug #6 — 監控迴圈呼叫 3 個不存在的 `check_*` 方法

### 原因
`grid_coordinator.py:357-368` 的 `_run_monitoring_loop` 呼叫:
- `position_monitor.check_positions()`
- `balance_monitor.check_balance_risk()`
- `stop_loss_monitor.check_stop_loss()`

但 3 個子模組都沒有這些公開方法(內部對應邏輯分別是 `_query_and_update_position`、
`update_balance`、`_check_stop_loss_condition`,且命名為 private)。

### 症狀
```
'PositionMonitor' object has no attribute 'check_positions'
'BalanceMonitor' object has no attribute 'check_balance_risk'
'StopLossMonitor' object has no attribute 'check_stop_loss'
```

### 修法
在 3 個子模組各加一個 1 行的 public wrapper,coordinator 端不動:
```python
# position_monitor.py:110-112
async def check_positions(self) -> bool:
    """單次查詢並更新持倉（給 coordinator 監控迴圈呼叫）"""
    return await self._query_and_update_position()

# balance_monitor.py:66-68
async def check_balance_risk(self):
    """單次更新餘額並執行風控初始化（給 coordinator 監控迴圈呼叫）"""
    await self.update_balance()

# stop_loss_monitor.py:77-79
async def check_stop_loss(self):
    """單次檢查止損條件（給 coordinator 監控迴圈呼叫）"""
    await self._check_stop_loss_condition()
```

---

## Bug #7 — `initialize_grid_orders` 缺失 + 多個 coordinator 屬性缺失

這是一個複合 bug,沿 `start()` 流程連續觸發:

### 7a. `GridResetManager.initialize_grid_orders` 不存在

**原因**:重構後該方法未實作,但 `grid_coordinator.py:142-143` 仍呼叫它。

**症狀**:
```
'GridResetManager' object has no attribute 'initialize_grid_orders'
```

**修法**:在 `grid_reset_manager.py` 新增 `initialize_grid_orders` 方法(輕量版,只做
「engine.initialize + 網格層級初始化 + 策略生成 + 批次下單」,不做取消訂單/重置餘額等重置專用邏輯):
```python
# grid_reset_manager.py:52-97
async def initialize_grid_orders(self):
    """初始化首批網格訂單(首次啟動用,與 _generic_reset_workflow 不同)"""
    try:
        # 1. 初始化執行引擎(設定 self.config 等內部狀態)
        await self.engine.initialize(self.config)

        # 2. 價格移動網格(若啟用):更新價格區間
        if self.config.is_follow_mode():
            current_price = await self.engine.get_current_price()
            old_range = (self.config.lower_price, self.config.upper_price)
            self.config.update_price_range_for_follow_mode(current_price)
            self.logger.info(
                f"📋 價格移動網格:價格區間 "
                f"[${old_range[0]:,.2f}, ${old_range[1]:,.2f}] → "
                f"[${self.config.lower_price:,.2f}, ${self.config.upper_price:,.2f}]"
            )

        # 3. 初始化網格層級
        self.state.initialize_grid_levels(
            self.config.grid_count,
            self.config.get_grid_price
        )
        self.logger.info(f"✅ 網格層級初始化完成,共 {self.config.grid_count} 個")

        # 4. 策略生成初始訂單
        initial_orders = self.strategy.initialize(self.config)
        self.logger.info(f"📋 策略生成 {len(initial_orders)} 個初始訂單")

        # 5. 引擎批次下單
        placed_orders = await self.engine.place_batch_orders(initial_orders)
        self.logger.info(
            f"✅ 成功掛出 {len(placed_orders)}/{len(initial_orders)} 個初始訂單"
        )
        return placed_orders
    except Exception as e:
        self.logger.error(f"❌ 初始化網格訂單失敗: {e}", exc_info=True)
        raise
```

### 7b. `GridEngineImpl.config = None` 未初始化

**原因**:`grid_engine_impl.py:169` 預設 `self.config = None`,
備份檔中由 coordinator `initialize()` 流程第一步 `await self.engine.initialize(self.config)` 設定,
但當前 `start()` 流程未呼叫。

**症狀**:
```
'NoneType' object has no attribute 'symbol'
(發生在 get_current_price 內 self.exchange.get_ticker(self.config.symbol))
🚨 价格获取网络故障！连续失败20次 (阈值=15次)
```

**修法**:在 `initialize_grid_orders` 開頭呼叫 `await self.engine.initialize(self.config)`
(已包含在 7a 的修法中)。

### 7c. `capital_protection_manager` 與 `take_profit_manager` 缺失

**原因**:`grid_reset_manager.py:415-416, 505-506` 與 `balance_monitor.py:257-263, 361-369`
會直接呼叫 `self.coordinator.capital_protection_manager` / `take_profit_manager`,
但當前 coordinator 從未建立這兩個屬性。

**修法**:在 coordinator 補上 Optional 預設 + 條件建立:
```python
# grid_coordinator.py:132-140
# 本金保護管理器(可選,預設 None;下游已用 None 保護)
self.capital_protection_manager: Optional[CapitalProtectionManager] = None
if self.config.is_capital_protection_enabled():
    self.capital_protection_manager = CapitalProtectionManager(self.config)

# 止盈管理器(可選,預設 None;下游已用 None 保護)
self.take_profit_manager: Optional[TakeProfitManager] = None
if self.config.take_profit_enabled:
    self.take_profit_manager = TakeProfitManager(self.config)
```
下游的 `if self.coordinator.xxx_manager:` 已有 None 保護,所以 None 是安全的。

### 7d. `GridConfig.monitoring_interval` 缺失

**原因**:監控迴圈 `await asyncio.sleep(self.config.monitoring_interval)` 使用,
但 `GridConfig` 從未定義此屬性。

**修法**:在 `grid_config.py` 加預設值(5 秒):
```python
# grid_config.py:100-101
rest_position_query_interval: int = 1    # REST持仓查询间隔（秒，默认1秒）
monitoring_interval: int = 5             # 監控迴圈間隔（秒，coordinator 預設使用）
```

---

## 修改檔案清單

| 檔案 | 修改類型 |
|------|---------|
| `run_multiple_grids.py` | 修字串閉合 (Bug #1) |
| `core/services/grid/coordinator/grid_coordinator.py` | 多處修補 (Bug #2, #3, #4, #5, #7c) |
| `core/services/grid/coordinator/grid_reset_manager.py` | 新增 `initialize_grid_orders` (Bug #7a, #7b) |
| `core/services/grid/coordinator/position_monitor.py` | 新增 `check_positions` (Bug #6) |
| `core/services/grid/coordinator/balance_monitor.py` | 新增 `check_balance_risk` (Bug #6) |
| `core/services/grid/coordinator/stop_loss_monitor.py` | 新增 `check_stop_loss` (Bug #6) |
| `core/services/grid/models/grid_config.py` | 新增 `monitoring_interval` (Bug #7d) |

---

## 進程演變

每次啟動都因發現新 bug 而崩潰,共經歷 6 次失敗啟動 + 1 次成功啟動:

| 啟動 | PID | 觸發 bug | 結果 |
|------|-----|----------|------|
| 1 | 2671078 | SyntaxError → 修字串 | 失敗 |
| 2 | 2697003 | `GridResetManager` 缺參數 → 補簽名 | 失敗 |
| 3 | 2735282 | `PositionMonitor` 缺參數 → 4 個子模組簽名修 | 失敗 |
| 4 | 2774535 | `BalanceMonitor.check_initial_balance` 不存在 → 改名 | 失敗 |
| 5 | 2810901 | `GridResetManager.initialize_grid_orders` + `capital_protection_manager` 不存在 → 補方法/屬性 | 失敗 |
| 6 | 2846608 | `monitoring_interval` + `take_profit_manager` 不存在 → 補屬性 | 部分成功(監控迴圈小錯) |
| 7 | 2890408 | (無) | **成功,持續運行** |

---

## Bug #8 — `GridCoordinator.get_statistics` 缺失(2026-07-06 01:08 重現)

### 原因
`run_multiple_grids.py:272` 每 60 秒週期性呼叫 `coordinator.get_statistics()` 用於統計彙報
(`[STATS_JSON]` 與 `📊 [...] 當前價格: ...` 兩條日誌),
但 `grid_coordinator.py` 在重構後刪除了此方法(`.bak` 版本 line 951 還在),
導致每 60 秒觸發 `AttributeError: 'GridCoordinator' object has no attribute 'get_statistics'`。

屬於「worklog 預留待辦 #2」的真實重現 — 之前只在「已知錯誤」中標註,
未實際補上,於是 production 跑起來每分鐘一次 ERROR。

### 症狀
```
2026-07-06 01:05:14,561 - system - ERROR - ⚠️ [ETH/USDC:USDC] 獲取統計資訊失敗:
  'GridCoordinator' object has no attribute 'get_statistics'
ERROR:system:⚠️ [ETH/USDC:USDC] 獲取統計資訊失敗: ...
```
每 60 秒重複 3 次(SOL/ETH/COAI)。

### 修法
在 `grid_coordinator.py:421` `resume()` 後新增 `async def get_statistics()`,
簡化自 `.bak` 版本(當前精簡版 coordinator 不緩存 `_spot_balance` 等欄位,
故省略對應部分,僅保留呼叫端實際使用的):
```python
# grid_coordinator.py:422
async def get_statistics(self) -> GridStatistics:
    # 1. 更新當前價格(失敗僅 warning)
    current_price = Decimal('0')
    try:
        current_price = await self.engine.get_current_price()
        current_grid_id = self.config.get_grid_index_by_price(current_price)
        self.state.update_current_price(current_price, current_grid_id)
    except Exception as e:
        self.logger.warning(f"獲取當前價格失敗: {e}")

    # 2. 以 tracker.get_statistics() 為基底
    stats = self.tracker.get_statistics()

    # 3. 嘗試以 WebSocket 緩存的真實持倉覆蓋(若 engine 支援)
    try:
        if hasattr(self.engine, 'get_real_time_position'):
            position_data = await self.engine.get_real_time_position(self.config.symbol)
            ws_position = position_data.get('size', Decimal('0'))
            ws_entry_price = position_data.get('entry_price', Decimal('0'))
            has_cache = position_data.get('has_cache', False)
            if has_cache and getattr(self, '_position_ws_enabled', False):
                stats.current_position = ws_position
                stats.average_cost = ws_entry_price
                stats.position_data_source = "WebSocket缓存"
                if ws_position != 0 and current_price > 0:
                    stats.unrealized_profit = ws_position * (current_price - ws_entry_price)
    except Exception as e:
        self.logger.debug(f"獲取 WebSocket 持倉失敗,使用本地 tracker: {e}")

    # 4. 監控模式
    if hasattr(self.engine, 'get_monitoring_mode'):
        try: stats.monitoring_mode = self.engine.get_monitoring_mode()
        except Exception: pass

    # 5. 本金保護 / 6. 止盈(條件式,None-guard 已在 coordinator 完成)
    if self.capital_protection_manager:
        try:
            stats.capital_protection_enabled = True
            stats.capital_protection_active = self.capital_protection_manager.is_active()
            stats.initial_capital = self.capital_protection_manager.get_initial_capital()
            stats.capital_profit_loss = self.capital_protection_manager.get_profit_loss(
                stats.total_balance
            )
        except Exception as e:
            self.logger.debug(f"讀取本金保護狀態失敗: {e}")
    if self.take_profit_manager:
        try:
            stats.take_profit_enabled = True
            stats.take_profit_active = self.take_profit_manager.is_active()
            stats.take_profit_initial_capital = self.take_profit_manager.get_initial_capital()
            stats.take_profit_current_profit = self.take_profit_manager.get_profit_amount(stats.total_balance)
            stats.take_profit_profit_rate = self.take_profit_manager.get_profit_percentage(stats.total_balance)
            threshold = getattr(self.config, 'take_profit_percentage', Decimal('0'))
            stats.take_profit_threshold = threshold * 100
        except Exception as e:
            self.logger.debug(f"讀取止盈狀態失敗: {e}")

    return stats
```

### 驗證
重啟後,每 60 秒正確產出:
```
2026-07-06 01:22:12,234 - system - INFO - 📊 [ETH/USDC:USDC] 當前價格: 1774.255 | 持倉: 0 | 實現盈虧: 0 | 活躍掛單: 0
2026-07-06 01:22:17,237 - system - INFO - 📊 [COAI/USDT:USDT] 當前價格: 0.2928500 | 持倉: 0 | 實現盈虧: 0 | 活躍掛單: 0
2026-07-06 01:22:22,234 - system - INFO - 📊 [SOL/USDC:USDC] 當前價格: 80.9950 | 持倉: 0 | 實現盈虧: 0 | 活躍掛單: 0
```

---

## Bug #9 — WebSocket `'ClientConnection' object has no attribute 'closed'`(2026-07-06 01:08 重現)

### 原因
環境 `websockets==16.0`(asyncio client API):
- 舊版(`<10`)`WebSocketClientProtocol.closed` 為 bool 屬性
- 新版(>=10)`ClientConnection` **不再有 `closed` 屬性**,改為 `protocol.state` 枚舉
  (`State.CONNECTING / OPEN / CLOSING / CLOSED`)

`binance_websocket.py` 共 4 處直接 `self._xxx_websocket.closed`:
- line 206 `_connect_market_stream` 守衛
- line 240 `_connect_futures_stream` 守衛
- line 1203 `is_connected` property
- line 1208 `is_user_connected` property(本次報錯處)

### 症狀
```
[01:04:40] ERROR    ExchangeAdapter.binance | ❌ 订阅用户数据失败:
  'ClientConnection' object has no attribute 'closed'
2026-07-06 01:04:40,279 - core.services.grid.implementations.grid_engine_impl - INFO - 🔄 尝试恢复WebSocket监控...
[01:04:40] ERROR    ExchangeAdapter.binance | ❌ 订阅用户数据失败: 'ClientConnection' object has no attribute 'closed'
```
每 5 秒觸發一次(`is_user_connected` 被 `subscribe_user_data` 觸發 → 失敗 →
30s watchdog 重連 → 又失敗 → 無窮迴圈)。

### 修法
新增 `binance_websocket.py:18-47` 跨版本 helper:
```python
def _is_ws_closed(ws) -> bool:
    """
    跨 websockets 版本的「連線是否已關閉」檢查
    1. 優先 ws.closed(legacy,bool)
    2. 次之 ws.protocol.state 比對 State.CLOSED/CLOSING(new)
    3. 兩者皆不可用時默認 False(視為仍開啟)以避免誤觸重連風暴
    """
    if ws is None: return True
    closed_attr = getattr(ws, 'closed', None)
    if closed_attr is not None: return bool(closed_attr)
    state = getattr(getattr(ws, 'protocol', None), 'state', None)
    if state is not None:
        try:
            from websockets.protocol import State
            return state in (State.CLOSED, State.CLOSING)
        except Exception:
            name = getattr(state, 'name', str(state))
            return name in ('CLOSED', 'CLOSING')
    return False
```
並把 4 處 `not self._xxx_websocket.closed` 改為 `not _is_ws_closed(self._xxx_websocket)`。

### 驗證
重啟後 `grep -E "ERROR|closed"` 在新 log 中**無匹配**。
`ExchangeAdapter.binance` 日誌顯示 user stream 連線成功:
```
[01:21:55] INFO     ExchangeAdapter.binance | ✅ 获取listenKey成功
[01:21:55] INFO     ExchangeAdapter.binance | 📡 连接Binance用户数据流: wss://fstream.binance.com/ws/...
[01:21:56] INFO     ExchangeAdapter.binance | ✅ Binance用户数据流连接成功
[01:21:56] INFO     ExchangeAdapter.binance | ✅ Watchdog 重連成功
```
剩下的「User Stream Watchdog 30 秒未收到訊息 → 切 REST → 重連」是設計行為
(無 user stream 推送時的 fallback),非 `.closed` AttributeError,符合預期。

---

## 當前狀態(2026-07-06 01:24)

- ✅ 三個 grid 全部「🎉 网格协调器启动成功」
- ✅ `get_statistics()` 正常產出每分鐘統計(Bug #8 修)
- ✅ WebSocket `ClientConnection.closed` AttributeError 完全消失(Bug #9 修)
- ✅ User Stream Watchdog 30s 重連機制正常運作
- ⚠️ 策略生成 **0 個**初始訂單 — 已知「中性網格設計行為」,無需修

進程:`PID 3221306`(`run_multiple_grids.py`,持續運行)
Log:`/root/crypto-trading-open/logs/run_multiple_grids_20260706_012102.out`

---

## Bug #10 — 活躍窗口完全沒啟動(2026-07-06 01:38 發現)

### 用戶反饋
> 之前為了修復重複在同一價位放置多個訂單導致了之前一系列異常,實際上中性網格無初始訂單,
> 但應該要在上下幾格的地方要掛上訂單待價格經過成交才對。目前行為模式被改成什麼?

### 當前行為(錯誤)
- `defaults.yaml:23` 已設定 `active_window_each_side: 2`
- 3 個 grid 的「中性網格」啟動後:**0 個訂單** — 完全不掛
- 價格穿越時無任何訂單可成交
- 連續運行 7+ 分鐘觀察期內,active window 機制從未觸發任何補單/取消動作

### 根因
[`grid_coordinator.py:142-176`](file:///root/crypto-trading-open/core/services/grid/coordinator/grid_coordinator.py#L142-L176) 重構後的 `start()` 流程:

| 步驟 | 動作 | .bak 版本對比 |
|------|------|---------------|
| 1 | `balance_monitor.update_balance()` | 一致 |
| 2 | `recover_old_orders()`(空實作) | 一致 |
| 3 | `reset_manager.initialize_grid_orders()`(中性網格:0 訂單) | 一致 |
| 4 | `engine.subscribe_order_updates(...)` | 一致 |
| **4.5** | **`engine.start()`(啟動活躍窗口 + 價格監控)** | **❌ 漏掉** |
| 5 | `asyncio.create_task(_run_monitoring_loop())` | 一致 |

[`grid_coordinator copy.bak:804-805`](file:///root/crypto-trading-open/core/services/grid/coordinator/grid_coordinator%20copy.bak#L804-L805) 原本有:
```python
await self.initialize()
await self.engine.start()  # ← 啟動 active_window 迴圈
```

[`grid_engine_impl.py:1804-1822`](file:///root/crypto-trading-open/core/services/grid/implementations/grid_engine_impl.py#L1804-L1822) 中 `engine.start()` 才是 active window 的入口:
```python
async def start(self):
    self._running = True
    await self._start_price_monitor()           # 訂閱 ticker
    if ex_id == 'binance' and window_each_side > 0:
        asyncio.create_task(self._active_window_refresh_loop())
        self.logger.info(f"✅ 活躍窗口刷新已啟動:每90秒,現價±{window_each_side}格")
```

重構時把這一行漏掉,導致「活躍窗口迴圈」永遠沒被排程。

### 修法
在 `coordinator.start()` 補上 `await self.engine.start()`(放在 `subscribe_order_updates` 之後,`_run_monitoring_loop` 之前):
```python
# 4.5 啟動執行引擎(活躍窗口刷新 + 價格監控)
# 對中性網格至關重要:
#   預設 active_window_each_side=2(由 defaults.yaml 提供),
#   應在現價上下各 2 格掛單(BUY 在下方 / SELL 在上方),
#   等價格穿越觸發成交,再掛反手單。
# 此方法是 .bak 版本 line 805 遺留的呼叫,
# 重構時未同步補回,造成 #10:啟動後完全無活躍窗口訂單。
try:
    await self.engine.start()
    self.logger.info("执行引擎启动完成")
except Exception as e:
    self.logger.warning(f"⚠️ 执行引擎启动失败(已记录,继续运行): {e}")
```

### 重複掛單的安全保證(為何不用擔心)
[`_refresh_active_window_once:1946-1958`](file:///root/crypto-trading-open/core/services/grid/implementations/grid_engine_impl.py#L1946-L1958) 內建兩層去重:
```python
local_pending_grid_ids = {
    o.grid_id for o in self.get_pending_orders()
    if o.status == GridOrderStatus.PENDING
}
for p in missing_prices:
    if grid_id in local_pending_grid_ids:
        self.logger.warning("⚠️ 活躍窗口去重:Grid X 本地已有 PENDING 訂單,跳過重複掛單")
        continue
```
且 `targets = center ± 1..N`(不包含 center 本身),不會在現價位下單,
正是中性網格「現價不掛,等穿越觸發」的核心設計。

### 驗證(2026-07-06 01:40 重啟)
三個 grid 同時上線:
```
2026-07-06 01:40:38,455 ✅ 活躍窗口刷新已啟動:每60秒,現價±2格  (ETH)
2026-07-06 01:40:46,038 ✅ 活躍窗口刷新已啟動:每60秒,現價±2格  (COAI)
2026-07-06 01:40:54,053 ✅ 活躍窗口刷新已啟動:每60秒,現價±2格  (SOL)
```
COAI 在 01:43:38 觸發 4 筆真實成交(由價格穿越引發):
```
📊 REST轮询检测到订单成交: buy 30.000@0.28815 (Grid 42)
📊 REST轮询检测到订单成交: sell 30.000@0.29460 (Grid 45)
📊 REST轮询检测到订单成交: sell 30.000@0.29675 (Grid 46)
📊 REST轮询检测到订单成交: sell 30.000@0.29890 (Grid 47)
```
連續 17 分鐘的 active window 刷新週期皆正常運作,未發現重複掛單。
每 60 秒出現的「活躍窗口去重:Grid X 本地已有 PENDING 訂單」警告
= 反向驗證「`initialize_grid_orders` 與 `active_window` 互不打擾,去重有效」。

### 修法與 #6/#7 修法的相互影響
- #6 修法新增的 `position_monitor` / `balance_monitor` / `stop_loss_monitor` 監控迴圈:**未受影響**
- #7 修法新增的 `reset_manager.initialize_grid_orders`:**仍會先跑,中性網格下生成 0 訂單**;
  `engine.start()` 在它之後才跑,接力掛出 ±2 格的活躍窗口訂單。順序正確。
- 若使用者想要更激進的窗口(±5、±10),改 `config/grid/<symbol>.yaml` 內的
  `active_window_each_side` 即可,無需改 code。

---

## 當前狀態(2026-07-06 01:57 更新)

- ✅ 三個 grid 全部「🎉 网格协调器启动成功」
- ✅ 三個 grid 全部「活躍窗口刷新已啟動」(**Bug #10 修**)
- ✅ `get_statistics()` 每 60 秒產出統計(**Bug #8 修**)
- ✅ WebSocket `ClientConnection.closed` 不再觸發(**Bug #9 修**)
- ✅ COAI 已 4 筆成交,中性網格價格穿越觸發鏈正常運作
- ✅ 活躍窗口內建去重,連續 17 分鐘無重複掛單

進程:`PID 3462913`,已運行 17+ 分鐘,CPU 3.5% / MEM 1.5%
Log:`/root/crypto-trading-open/logs/run_multiple_grids_20260706_014029.out`

---

## 修改檔案清單(累計)

| 檔案 | 修改類型 |
|------|---------|
| `run_multiple_grids.py` | 修字串閉合 (Bug #1) |
| `core/services/grid/coordinator/grid_coordinator.py` | 多處修補 (Bug #2, #3, #4, #5, #7c, #8, **#10**) |
| `core/services/grid/coordinator/grid_reset_manager.py` | 新增 `initialize_grid_orders` (Bug #7a, #7b) |
| `core/services/grid/coordinator/position_monitor.py` | 新增 `check_positions` (Bug #6) |
| `core/services/grid/coordinator/balance_monitor.py` | 新增 `check_balance_risk` (Bug #6) |
| `core/services/grid/coordinator/stop_loss_monitor.py` | 新增 `check_stop_loss` (Bug #6) |
| `core/services/grid/models/grid_config.py` | 新增 `monitoring_interval` (Bug #7d) |
| `core/adapters/exchanges/adapters/binance_websocket.py` | 新增 `_is_ws_closed` helper,4 處替換 (Bug #9) |

---

## 經驗教訓(可記入 project_memory.md)

1. **重構子模組時,必須同步更新呼叫端的「建構簽名」、「公開介面」、「Optional 屬性」三處**,否則會留下 7 個 dangling references 沿啟動鏈逐個爆開。
2. **「Optional 屬性 + None 保護」是 coordinator 與子模組間的安全設計模式**:
   - `scalping_manager` 因下游有 None 保護,可條件建立
   - `scalping_ops` 因下游無 None 保護,需無條件建立(或補保護)
   - `capital_protection_manager` / `take_profit_manager` 同樣採「下游 None 保護 + 上游條件建立」
3. **Logger 檔名禁止直接拼接含有 `/` `:` 等路徑分隔符號的輸入**(如 `ETH/USDC:USDC`),
   即使 Linux 上 `/` 不會爆錯,也應在 `BaseLogger` 層做字元清理,作為縱深防禦。
4. **「啟動期介面缺失」是最容易連環爆發的 bug 類型**,建議對 `__init__` 與 `start()` 流程
   寫整合測試(若能容忍 Binance API 模擬),可在 CI 階段一次性抓出全部缺失的介面。
5. **worklog 待辦項若僅寫「待查」「TODO」而不實際修,production 必爆** —
   Bug #8 在前一份 worklog 末尾被列為「中優先待辦」,12 小時後在 production 重現。
   建議:worklog 寫下的待辦若 24 小時內未修,應主動 inline 進「下次開機 checklist」。
6. **跨版本第三方 API 屬性(`websockets.ClientConnection.closed`)會在環境升級後瞬間爆** —
   應在「導入/構造」階段寫一個 `getattr(ws, 'closed', None) or check_state(ws)` 的 helper,
   把所有 `xxx.closed` 集中管理,後續升級只改一處。
