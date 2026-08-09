# Graphify 使用手冊

這份手冊是針對 `crypto-trading-open` 這個 repo 的實戰版說明，不是 Graphify 官方泛用安裝文件。重點放在這個專案最常見的三種需求：

1. 快速理解 grid 交易主流程
2. 追 adapter / service / runtime entry point 的關聯
3. 改完碼後，用最低成本更新知識圖譜

## 目前專案狀態

本 repo 已完成下列配置：

- 全域已安裝 `graphify`
- 專案 skill 已安裝在 `.trae/skills/graphify/`
- 專案規則已寫入 `AGENTS.md`
- git hooks 已安裝，會在 commit / checkout 後更新圖譜
- 已生成 `graphify-out/`

目前可直接使用的輸出檔：

- `graphify-out/graph.json`
- `graphify-out/GRAPH_REPORT.md`
- `graphify-out/graph.html`

## 先記住一件重要的事

**目前這個 repo 的現有圖譜是用 `--code-only` 建的。**

這代表：

- `Python` 程式碼結構、類別、方法、import 關係的查詢效果很好
- `config/grid/*.yaml`、`docs/*.md`、圖片等內容**不一定完整進圖**
- 如果你要查設定檔、設計文件、工作日誌之間的語意關聯，應該改跑完整抽取，而不是只靠目前這份 code-only graph

## 這個 repo 最適合怎麼用 Graphify

### 1. 看主流程入口

這個 repo 的 runtime 入口主要集中在：

- `run_grid_trading.py`
- `run_multiple_grids.py`
- `run_arbitrage_monitor.py`
- `run_volume_maker.py`
- `run_lighter_volume_maker.py`
- `run_price_alert.py`

當你接手一個 bug 或要理解執行流時，先查入口，再往下追 service。

建議指令：

```bash
graphify query "What are the main runtime entry points and grid trading services in this repository?" --budget 1200
graphify explain "GridEngineImpl"
```

### 2. 追 grid 核心服務

這個 repo 的 grid 核心通常會落在：

- `core/services/grid/implementations/grid_engine_impl.py`
- `core/services/grid/implementations/order_health_checker.py`
- `core/services/grid/implementations/grid_strategy_impl.py`
- `core/services/grid/coordinator/grid_coordinator.py`

如果你在查：

- 為什麼掛單被取消
- 為什麼健康檢查補單/去重失效
- grid state 是怎麼從 config 走到 runtime

優先用：

```bash
graphify explain "GridEngineImpl"
graphify explain "core_services_grid_implementations_order_health_checker_orderhealthchecker"
graphify affected "ExchangeInterface" --depth 2
```

說明：

- `explain` 適合看單一核心類別的上下游
- `affected` 適合做「改這個 interface 會波及誰」的逆向影響分析

### 3. 追交易所 adapter 與共用介面

這個 repo 的交易所抽象層是 Graphify 很容易看清楚的一塊，因為 class / import 結構很明確。

重點路徑：

- `core/adapters/exchanges/interface.py`
- `core/adapters/exchanges/factory.py`
- `core/adapters/exchanges/manager.py`
- `core/adapters/exchanges/adapters/*.py`

常用查法：

```bash
graphify explain "ExchangeInterface"
graphify affected "ExchangeInterface" --depth 2
graphify god-nodes --top 15
```

在這個 repo，`ExchangeInterface`、`GridEngineImpl`、`OrderData`、`TickerData` 都是高連接度節點，適合作為導覽起點。

### 4. 改完碼後更新圖譜

日常開發時，**優先用增量更新**：

```bash
graphify update .
```

這一步成本低，適合：

- 改 Python code
- 新增/刪除 method
- 調整 import / class 關係
- 修補 grid engine、health checker、adapter

如果你只是修 `core/services/grid/` 或 `core/adapters/exchanges/`，通常 `graphify update .` 就夠了。

## 建議工作流

### 工作流 A：接手一個 grid 問題

```bash
graphify explain "GridEngineImpl"
graphify explain "core_services_grid_implementations_order_health_checker_orderhealthchecker"
graphify query "How does GridEngineImpl relate to order health checking and batch order placement?" --budget 1200
```

適用情境：

- NEUTRAL grid 掛單異常
- 批量下單 / circuit breaker 問題
- 補單、健康檢查、活躍窗口更新問題

### 工作流 B：評估改 interface 的連帶影響

```bash
graphify explain "ExchangeInterface"
graphify affected "ExchangeInterface" --depth 2
```

適用情境：

- 改 adapter method signature
- 調整 exchange data model
- 新增交易所 adapter

### 工作流 C：先找系統樞紐再深入

```bash
graphify god-nodes --top 20
graphify explain "GridEngineImpl"
graphify explain "ExchangeInterface"
```

適用情境：

- 新人接手 repo
- 要快速掌握模組中心點
- 想找最值得先讀的類別

## 目前這個 repo 查詢時要避開的坑

### 1. 同名節點歧義

這個 repo 內目前有重名節點，例如：

- `OrderHealthChecker`

因為同時存在：

- `core/services/grid/implementations/order_health_checker.py`
- `core/services/grid/implementations/order_health_checker copy.py`

所以這種查詢可能會歧義：

```bash
graphify explain "OrderHealthChecker"
```

建議改用精確 node id：

```bash
graphify explain "core_services_grid_implementations_order_health_checker_orderhealthchecker"
```

### 2. 不要對 code-only 圖譜期待 YAML 語意查詢

像這些檔案：

- `config/grid/binance-neutral-perp-*.yaml`
- `docs/hermes_grid_usage_guide.md`
- `docs/hermes_grid_phase*.md`

如果你需要的是「設定值和程式邏輯、設計文檔之間的語意關聯」，請跑完整抽取：

```bash
graphify extract .
graphify cluster-only .
```

如果環境裡有 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`，文件/圖片語意抽取會更完整；沒有也可以跑，只是非 code 語意抽取能力會受限於當前 agent 執行方式。

### 3. `query` 太寬會很容易截斷

這個 repo 節點很多，問得太大會被 budget 截掉。

例如這類問題容易太寬：

```bash
graphify query "How does everything in grid trading work?" --budget 600
```

建議做法：

- 把問題聚焦到一個 class / flow / interface
- 把 `--budget` 拉高到 `1200` 或 `2000`
- 先 `explain` 再 `query`

## 建議保留的常用命令

### 日常增量更新

```bash
graphify update .
```

### 重新生成報告與視覺化

```bash
graphify cluster-only . --no-label
```

### 查單一核心類別

```bash
graphify explain "GridEngineImpl"
graphify explain "ExchangeInterface"
```

### 查影響面

```bash
graphify affected "ExchangeInterface" --depth 2
```

### 看系統樞紐

```bash
graphify god-nodes --top 15
```

## 什麼時候應該重建完整圖譜

遇到以下情況，建議不要只跑 `update`，而是重跑完整抽取：

1. 新增大量文件、設定檔、流程說明
2. 想把 `config/grid/` 與 `docs/` 一起納入分析
3. 想產出更完整的 `GRAPH_REPORT.md`
4. 目前 `graph.json` 明顯與工作樹內容不一致

建議指令：

```bash
graphify extract .
graphify cluster-only .
```

如果只是純程式碼更新，仍然優先用：

```bash
graphify update .
```

## 在 Trae 裡怎麼配合使用

本 repo 的 `AGENTS.md` 已經定義好規則：

- codebase 問題優先查 `graphify query`
- 關聯問題用 `path` / `affected`
- 單一概念用 `explain`
- 改完 code 後跑 `graphify update .`

也就是說，在這個 repo 裡，Graphify 應該被視為**第一層導覽工具**，不是最後才拿來補充的工具。

## 一句話建議

對這個 repo，最划算的 Graphify 用法不是「把所有東西都問一遍」，而是：

**先用 `god-nodes` 和 `explain` 找中心，再用 `affected` / `query` 縮小問題面，最後在改碼後用 `graphify update .` 保持圖譜新鮮。**
