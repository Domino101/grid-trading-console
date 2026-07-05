# 网格波动率扫描器诊断报告

**项目路径**: `/root/crypto-trading-open/grid_volatility_scanner/`
**稽核日期**: 2026-06-13
**稽核范围**: 全部源文件、配置、文档、运行脚本
**测试执行**: 82 项断言全部通过（0 失败 / 0 警告）

---

## 一、测试结果总览

| 测试模块 | 用例 | 通过 | 失败 | 警告 |
|---------|------|------|------|------|
| VirtualGrid 网格参数计算 | 6 | 6 | 0 | 0 |
| VirtualGrid 价格穿越与状态机 | 8 | 8 | 0 | 0 |
| VirtualGrid 多次循环检测 | 2 | 2 | 0 | 0 |
| APRCalculator 公式正确性 | 5 | 5 | 0 | 0 |
| APR 滚动窗口 | 3 | 3 | 0 | 0 |
| SimulationResult 评级 | 6 | 6 | 0 | 0 |
| CycleDetector | 6 | 6 | 0 | 0 |
| APRAlertManager | 6 | 6 | 0 | 0 |
| BTC 符号识别 | 8 | 8 | 0 | 0 |
| S 级持续时间追踪 | 5 | 5 | 0 | 0 |
| 配置文件加载 | 5 | 5 | 0 | 0 |
| 边界与异常 | 4 | 4 | 0 | 0 |
| 成交量格式化 | 4 | 4 | 0 | 0 |
| ScannerUI 初始化 | 7 | 7 | 0 | 0 |
| Scanner 模拟初始化 | 7 | 7 | 0 | 0 |
| **合计** | **82** | **82** | **0** | **0** |

测试脚本已保存为 `grid_volatility_scanner/audit_test.py`，可通过以下命令复跑：

```bash
cd /root/crypto-trading-open
source venv/bin/activate
python grid_volatility_scanner/audit_test.py
```

---

## 二、模块功能验证

### 2.1 ✅ VirtualGrid（虚拟网格）—— 核心正确

- **网格参数计算**：上下边界、网格数量、网格线数全部正确
  - 例：BTC $100k, width 2%, interval 0.5% → 4 格 (5 条线) ✓
- **整格挂单初始化**：买单 $99.5k, 卖单 $100.5k（不是半格）✓
- **状态机穿越逻辑**：下跌触发买单、上涨触发卖单、挂单对侧下移/上移一格 ✓
- **循环计数**：`min(buy_crosses, sell_crosses)` 准确，5 次震荡→5 次循环 ✓
- **APR 滚动窗口**：5 分钟窗口外事件被清理，启动 1 分钟后可计算 ✓
- **S 级追踪**：进入/持续/降级 3 状态切换正常 ✓

### 2.2 ✅ APRCalculator —— 公式与文档一致

- README 示例 (interval=0.5%, width=10%, cycles=10) → **2172.48%** ✓
- 边界处理：手续费 > 间距 → APR=0；循环=0 → APR=0 ✓
- 总本金计算：$10 × 格数 ✓
- 单次循环净收益公式正确 ✓

### 2.3 ✅ SimulationResult —— 评级与评分

- S/A/B/C/D 五档阈值 (500/300/150/50) 全部正确
- 评分上下限强制约束到 [0, 100]
- 成交量格式化 K/M 单位正确

### 2.4 ✅ APRAlertManager —— 报警机制

- 阈值触发、冷却期、最大次数、状态重置全部正常

### 2.5 ✅ ScannerUI —— Rich 终端 UI

- 实例化、日志 handler、stats/results 更新、layout 创建、stop 全部通过

### 2.6 ✅ GridVolatilityScanner —— 整体编排

- 配置加载、BTC/ETH 默认配置正确读取
- 稳定币 (USDT-USD) 被过滤
- BTC 置顶 + 其他按 APR 排序逻辑正确

---

## 三、发现的问题与风险

> 以下问题均**不影响当前 82 项功能测试通过**，但属于代码异味、配置隐患或文档不一致，需要在后续版本修正。

### 🔴 P0 — 高优先级（功能缺失 / 死代码）

#### 问题 1：`volume_24h_usdc` 与 `price_change_24h_percent` 永远为 0

**位置**：
- [models/virtual_grid.py#L101-L103](file:///root/crypto-trading-open/grid_volatility_scanner/models/virtual_grid.py#L101-L103)（字段定义）
- [scanner.py](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py) `_on_ticker_update` 与 `_price_update_callback`

**问题描述**：
- `VirtualGrid` 声明了 24h 成交量与价格变化字段
- UI 表格专门有一列「24h量」并参与评分微调（[simulation_result.py#L88-L92](file:///root/crypto-trading-open/grid_volatility_scanner/models/simulation_result.py#L88-L92)）
- 但整个 `scanner.py` 没有任何代码从 TickerData / REST API 把这两个值写回 grid
- **实际效果**：UI 上 24h 量永远显示 `$0`，且 `volume_millions < 0.5` 恒为真，所有代币都被扣 10 分

**建议**：
- 在 `_on_ticker_update` 中读取 `ticker.quoteVolume`（CCXT 风格）或 Lighter 对应字段
- 一次性 batch 获取并存入 grid（避免每个 tick 都查 REST）
- 在 SimulationResult.calculate_rating 中，对 volume=0 跳过扣分（防止 0 分惩罚）

#### 问题 2：`min_24h_volume_usdc` 配置项形同虚设

**位置**：
- [config/market_config.yaml#L80](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L80)（配置项）
- [scanner.py#L152](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py#L152)（默认值）

**问题描述**：
- README 与配置文件均说明"低于此值不扫描"
- 但 `_get_all_markets` 与 `_create_virtual_grids` 中**完全没有**读取或过滤该字段
- **实际效果**：低流动性代币一样会被订阅，占用 WebSocket 资源

**建议**：
- 在订阅前过滤（先 batch 获取 tickers，按 `quoteVolume >= 阈值` 过滤）
- 列入「待办：pre-create 模式重新启用时一并修复」

#### 问题 3：`top_n_results` 配置项未被使用

**位置**：
- [config/market_config.yaml#L93](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L93)（配置项）
- [ui/scanner_ui.py#L268](file:///root/crypto-trading-open/grid_volatility_scanner/ui/scanner_ui.py#L268)（硬编码 `[:50]`）

**建议**：把 `[:50]` 改为 `[: self.scanner_config.get('top_n_results', 50)]`，或经由 `update_results` 传入参数

---

### 🟠 P1 — 中优先级（代码异味 / 不一致）

#### 问题 4：`ticker.last` vs `ticker.last_price` 字段名不一致

**位置**：
- [scanner.py#L568](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py#L568) 使用 `ticker.last`（**生产路径**）
- [scanner.py#L224, L291, L675](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py#L224) 使用 `ticker.last_price`（**未使用路径**）

**风险**：
- 如果 Lighter TickerData 实际字段是 `last_price` 而非 `last`，生产路径上的 `_on_ticker_update` 会在 `if not ticker or not ticker.last` 判定时直接 `return`，**所有价格更新都会被静默丢弃**，不会抛错也不会写日志（DEBUG 级别）
- 单元测试中无法发现，因为测试都是同步构造 Decimal 直接传入

**建议**：
- 统一字段名为 `last_price`（与未使用路径一致）
- 在 `_on_ticker_update` 入口添加 `logger.warning` 当 ticker 字段缺失时，便于定位

#### 问题 5：`PriceMonitor` 与 `CycleDetector` 是死代码

**位置**：
- [core/price_monitor.py](file:///root/crypto-trading-open/grid_volatility_scanner/core/price_monitor.py)：明确写着「⚠️ 当前为占位实现」
- [core/cycle_detector.py](file:///root/crypto-trading-open/grid_volatility_scanner/core/cycle_detector.py)：`detect_cross` 函数过于简化，与 VirtualGrid 状态机重复
- [__init__.py#L20-L21](file:///root/crypto-trading-open/grid_volatility_scanner/__init__.py#L20-L21)：仍对外导出

**建议**：
- 删除 `PriceMonitor`（或迁移到 `tools/` 目录作为示例）
- 把 `CycleDetector` 标记为 deprecated（@deprecated 装饰器），保留以备新交易所接入

#### 问题 6：`_monitor_prices` 是未启用的轮询实现

**位置**：[scanner.py#L665-L684](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py#L665-L684)

**问题描述**：
- 使用 `get_ticker` REST API 每秒轮询，**生产中并未调用**
- 注释自承"实际应该集成WebSocket实时推送"
- 误导新读者以为这是主路径

**建议**：在 `scan()` 入口处明确选择 polling vs websocket 模式，或直接删除

#### 问题 7：报警阈值 `apr_alert_threshold: 1500.0` 几乎不可能触发

**位置**：[config/market_config.yaml#L96](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L96)

**问题描述**：
- 1500% 的 APR 阈值在真实网格场景下几乎不可能达到
- 报警功能实际上变成了「摆设」

**建议**：默认值改为 `300.0`（A级门槛）或 `500.0`（S级门槛），同时提供文档说明如何按交易所/币种定制

#### 问题 8：扫描器代码职责过重

**位置**：[scanner.py](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py)（共 997 行）

**问题描述**：
- `_create_virtual_grids` 一个方法接近 250 行，承担了：市场过滤、symbol 映射、批量订阅、统计、错误处理、状态记录
- `_on_ticker_update` 中又重新实现了 `_create_single_virtual_grid` 内部的 config-type 检测逻辑

**建议**：
- 拆分出 `MarketFilter`（过滤稳定币/外汇）、`SubscriptionManager`（分批订阅）、`SymbolMapper`（映射规则）
- 消除 `_on_ticker_update` 与 `_create_single_virtual_grid` 之间的重复 config-type 判断

---

### 🟡 P2 — 低优先级（改进建议）

#### 问题 9：`_get_grid_index` 注释与实现不一致

**位置**：[models/virtual_grid.py#L199-L213](file:///root/crypto-trading-open/grid_volatility_scanner/models/virtual_grid.py#L199-L213)

**问题描述**：注释写「二分查找」，实际是线性遍历

**建议**：改为 `bisect.bisect_right` 实现真正的 O(log n) 查找，或修正注释

#### 问题 10：`grid_lines` 字段未被业务使用

**位置**：[models/virtual_grid.py#L74](file:///root/crypto-trading-open/grid_volatility_scanner/models/virtual_grid.py#L74)

**问题描述**：在 `__post_init__` 中生成 `grid_lines`（共 `grid_count + 1` 条），但 `update_price` 用的是 `pending_buy_price/pending_sell_price` 状态机，`_get_grid_index` 也仅用于日志

**建议**：
- 移除非必要的 `grid_lines`（如果不需要调试网格可视化）
- 或在 UI 中可视化展示 grid_lines（增强调试能力）

#### 问题 11：默认配置与 README 示例不一致

**位置**：
- [config/market_config.yaml#L4-L5](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L4-L5)：default `width=50.0, interval=0.1`
- [README.md#L18-L25](file:///root/crypto-trading-open/grid_volatility_scanner/README.md#L18-L25)：示例 `BTC: width=2.0, interval=0.5`

**问题描述**：
- 默认配置下，500 个网格 × 10 USDC = 5000 USDC 的总本金（与小资金用户预期不符）
- README 与实际配置脱节

**建议**：默认改为 `width=10.0, interval=0.5`（20 格 × 10 USDC = 200 USDC），README 与实际保持一致

#### 问题 12：S 级持续时间在 UI 静止时不会更新

**位置**：[models/virtual_grid.py#L593-L601](file:///root/crypto-trading-open/grid_volatility_scanner/models/virtual_grid.py#L593-L601)

**问题描述**：`get_s_rating_duration_seconds` 是惰性计算，但只在 `update_rating` 时被 UI 调用。如果 APR 不再变化，UI 刷新期间 S 级计时会卡住

**建议**：
- 在 `ScannerUI.create_rankings_table` 中调用 `get_s_rating_duration_seconds()` 而非读缓存字段
- 或在 `_update_ui_loop` 每秒独立刷新 S 级时间显示

#### 问题 13：重复订阅同一 symbol 风险

**位置**：[scanner.py#L490-L500](file:///root/crypto-trading-open/grid_volatility_scanner/scanner.py#L490-L500)

**问题描述**：分批订阅中，若 `subscribe_ticker` 内部对同一 symbol 重复调用，会出现同一 symbol 注册多个回调

**建议**：在订阅前对 `subscribed_symbols` 做 `set()` 去重检查

#### 问题 14：测试覆盖不足

**位置**：`grid_volatility_scanner/test_scanner.py`

**问题描述**：原 `test_scanner.py` 仅覆盖 3 个基础场景；本次新增的 `audit_test.py` 覆盖 82 项，建议：
- 把 `audit_test.py` 重构为 `pytest` 风格（使用 `assert`，配合 `pytest-asyncio`）
- 加入 CI：`.github/workflows/ci.yml` 或 `pytest.ini`
- 覆盖率目标 ≥ 80%（目前未测量）

---

## 四、性能与可维护性观察

| 维度 | 现状 | 建议 |
|------|------|------|
| 启动时间 | 因 WebSocket 订阅分批 (20/批 × 95+ symbol) 约 5-10 秒 | 可并行订阅（但需控制连接数） |
| UI 刷新 | 2 Hz，500ms 一次，每次都重算全部 grid | 缓存 layout，仅刷新数据行 |
| 内存占用 | 95+ grid × O(cycle_events) = 几百 KB | 足够小，可忽略 |
| 日志体积 | BTC 详细日志每次价格变化都写 INFO | 增加采样（每 N 次成交写一次） |
| 并发安全 | `self.virtual_grids` 字典在多协程下读写 | 加 `asyncio.Lock` 或切换到线程局部 |

---

## 五、文档与配置同步

| 不一致点 | 位置 | 建议 |
|---------|------|------|
| `min_cycles_to_display: 5` in README | [README.md#L203](file:///root/crypto-trading-open/grid_volatility_scanner/README.md#L203) | 实际配置为 0；同步为 0 或在配置中改为 5 |
| `top_n_results: 50` 未生效 | [config/market_config.yaml#L93](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L93) | 在 UI 中读取 |
| `min_24h_volume_usdc: 100000` 未生效 | [config/market_config.yaml#L80](file:///root/crypto-trading-open/grid_volatility_scanner/config/market_config.yaml#L80) | 实现过滤逻辑 |
| `volume_24h_usdc` UI 显示 $0 | [ui/scanner_ui.py#L230](file:///root/crypto-trading-open/grid_volatility_scanner/ui/scanner_ui.py#L230) | 接入真实数据 |
| `README_FIXED.md` 路径错误 | [README_FIXED.md#L75](file:///root/crypto-trading-open/grid_volatility_scanner/README_FIXED.md#L75) | `/Volumes/T7/crypto-trading` 是 macOS 路径 |

---

## 六、后续改版方向建议

### 短期（1 周内）
1. **修复 24h 成交量为空**（P0）：接入 Lighter TickerData 的 quoteVolume 字段
2. **统一 `ticker.last` 字段名**（P1）：避免生产环境静默失败
3. **调整报警阈值为 300%**（P1）：让报警功能可用
4. **同步 README 与默认配置**（P2）：消除文档与实现脱节

### 中期（2-4 周）
5. **拆分 scanner.py**：将 997 行 monolith 重构为多个职责单一的服务
6. **删除死代码**：移除 `PriceMonitor`、标记 `CycleDetector` deprecated
7. **接入 pytest**：将 `audit_test.py` 迁移为标准测试，纳入 CI
8. **实现 `min_24h_volume_usdc` 过滤**：低流动性代币预过滤

### 长期（1-2 月）
9. **多交易所适配器统一**：当前 Lighter 特殊处理，未来抽象出 `IExchangeAdapter` 接口
10. **APR 算法扩展**：增加考虑资金费率、滑点、撤单率等多因子模型
11. **持久化与历史回放**：扫描结果存入 SQLite，支持 T-7d 复盘对比
12. **Web 管理后台**：与现有 `web_ui` 集成，提供远程查看与告警

---

## 七、附录：复跑测试

```bash
# 激活环境
cd /root/crypto-trading-open
source venv/bin/activate

# 复跑原 test_scanner.py
python grid_volatility_scanner/test_scanner.py

# 复跑本次稽核测试 (82 项)
python grid_volatility_scanner/audit_test.py
```

测试输出样例（截取）：

```
================================================================================
📋 测试 4: APRCalculator 计算正确性
================================================================================
  ✅ PASS: README示例APR - 2172.48% ≈ 2172.48%
  ✅ PASS: 手续费>利润 → APR=0 - 0.003% < 0.004% 手续费 → APR=0
  ✅ PASS: 循环=0 → APR=0 - 无循环时APR=0
  ✅ PASS: 总本金计算 - 10 USDC × 20格 = 200 USDC
  ✅ PASS: 单次循环收益 - $0.04960

================================================================================
📋 测试 2: VirtualGrid 价格穿越与状态机
================================================================================
  ✅ PASS: 下跌触发买单 - 价格2980 < 买单2985 → 买入成交
  ✅ PASS: 完整循环计数 - 完成1次循环: min(1买入,1卖出)=1
```

---

**报告结束。** 如需对任何条目展开更深入的分析或制定修复计划，请告知。
