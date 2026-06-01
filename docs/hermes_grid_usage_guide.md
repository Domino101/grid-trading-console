# 🤖 Hermes Grid Trading API & Client 整合使用指引 v1.0

本文件提供開發人員與 AI Agent (Hermes) 完整的系統整合指南，協助快速部署、控制與監控量化網格交易系統。

---

## 📌 系統簡介

**Hermes Grid API** 是一套專為外部 AI 代理設計的超低延遲、高安全、零 API 權重消耗的網格生命週期控制平台。它為 AI 提供了安全網關，確保 AI 不需要直接接觸交易所私鑰，全流程透過本地安全 REST 接口完成。

---

## 🧠 Hermes Agent 認知決策與工具使用 SOP (AI-Facing Skill Specification)

作為一個擁有主動決策能力的 AI Agent (Hermes)，當你載入此文檔作為 **Skill（技能擴充觸手）** 時，你必須嚴格遵循以下認知決策樹與操作限制，以確保資金安全與系統穩定：

```
                [ AI 決策起點：分析市場機會 ]
                             │
                             ▼
                 Step 1: 查詢系統健康狀態
               GET /api/v1/system/health
                             │
            ┌────────────────┴────────────────┐
   [熔斷開啟 / can_accept=False]       [系統正常且有額度]
            │                                 │
            ▼                                 ▼
      【❌ 暫停操作】                 Step 2: 查詢當前網格摘要
       等待 15 分鐘                 GET /api/v1/grids/summary
                                              │
                                              ▼
                                    Step 3: 避免重複交易對
                                 若 symbol 已運行，跳過或停止舊格
                                              │
                                              ▼
                                    Step 4: 查詢交易對精度限制
                                  GET /api/v1/market/info/{symbol}
                                              │
                                              ▼
                                    Step 5: 資金算力校驗
                                 總投入 >= GRID_MIN_INVESTMENT (10 USDC)
                                              │
                                              ▼
                                    Step 6: 發送交易指令 (寫操作)
                                 POST /api/v1/grids (帶入詳細元數據)
```

### 📋 AI 認知決策六大步驟 (Standard Operating Procedure)

#### Step 1: 預檢系統健康 (Pre-flight Health Scan)
*   **指令**：發送 `GET /api/v1/system/health`。
*   **決策邏輯**：
    *   若 `circuit_breaker.is_open` 為 `true`：**立刻暫停所有操作**！此時交易所正在實施限流或 IP Ban，任何請求都會加劇 Ban 的時長。
    *   若 `can_accept_new_grid` 為 `false` 或 `running_grids` $\ge 5$：系統已達最大併發負載，**拒絕建立新網格**。
    *   如果健康狀態為 `degraded`，說明引擎進程未啟動，請輸出明顯日誌提醒使用者。

#### Step 2: 分析現有佈局 (Analyze Deployments)
*   **指令**：發送 `GET /api/v1/grids/summary`。
*   **決策邏輯**：
    *   檢查現有網格的實時盈虧（`realized_pnl`、`unrealized_pnl`）。
    *   如果某個活躍網格的 `total_pnl` 低於風控閥值（例如 $-100.0$ USDC），應自動發起 **Step 6** 的優雅停止 (`POST /grids/{id}/stop`)，防止虧損擴大。

#### Step 3: 重複標的排重 (Deduplication Check)
*   **決策邏輯**：
    *   比對 `GET /api/v1/grids` 列表。
    *   針對同一個交易對標的（例如 `SOL/USDC:USDC`），**嚴禁同時運行多個活躍網格**。如果決定更換區間或重啟，你必須**先優雅停止並歸檔**舊網格 (`DELETE /api/v1/grids/{grid_id}`)，再建立新網格。

#### Step 4: 交易精度與上下界對齊 (Precision Alignment)
*   **指令**：發送 `GET /api/v1/market/info/{symbol}`。
*   **決策邏輯**：
    *   讀取 `price_precision` 與 `quantity_precision`。
    *   你在建立網格時產生的價格界限（`lower_price`、`upper_price`）、間隔（`grid_interval`）以及每格數量（`order_amount`），**必須精確截斷至上述小數位數**，防止交易所返回 `Invalid Order Size` 或 `Precision Error`。

#### Step 5: 投資額硬限制計算 (Min Investment Math)
*   **決策邏輯**：
    *   在發送建倉請求前，你必須在內部執行以下公式計算總投資額：
        $$\text{投資額} = \text{order\_amount} \times \left( \frac{\text{upper\_price} - \text{lower\_price}}{\text{grid\_interval}} \right) \times \text{lower\_price}$$
    *   確保該估算值 $\ge 10.0$ USDC (或 `GRID_MIN_INVESTMENT` 的設定值)。若未達標準，系統安全防護牆將直接攔截你的請求並判定為 `rejected`。

#### Step 6: 填寫高保真決策審計元數據 (Decisions & Meta Logging)
*   **決策邏輯**：
    *   你具備寫操作頻率限制：**60 秒內最多只能發起 3 次寫入**（Create、Start、Stop、Delete）。為防範觸發 `429 Too Many Requests`，兩次寫操作之間請**主動間隔至少 20 秒**。
    *   在發送寫操作（如 `POST /api/v1/grids`）時，你**必須**在 `metadata` 中帶入真實決策理由，這將被持久化儲存於 SQLite 審計表中供人類複查：
        *   `source`: 必須設定為 `"hermes"`。
        *   `reason`: 必須寫入明確的量化依據（例如：`"SOL 30天年化波動率 48% 處於歷史高點，且價格於 130-150 箱體震盪，適合 neutral 部署"`）。
        *   `confidence`: 你的模型置信度分數，介於 `0.0` 至 `1.0` 之間的浮點數。

---

## ⚙️ 快速開始與環境配置

### 1. 配置 API 安全 Token
系統強制使用 **Bearer Token** 進行接口鑑權。請在專案根目錄的 [`.env`](file:///root/crypto-trading-open/.env) 檔案中加入強隨機 Token（建議 64 位 Hex 字元）：

```env
# web_ui/backend/.env 或 專案根目錄 .env
GRID_API_TOKEN=your_secure_random_64_character_hex_token_here
GRID_MIN_INVESTMENT=10.0
```

> [!NOTE]
> 如果啟動時未在環境變數或 `.env` 偵測到 `GRID_API_TOKEN`，API 引擎會自動生成一個**臨時一次性 Token** 並於日誌中警告。為了生產環境穩定，請務必顯式配置。

### 2. 啟動 FastAPI 服務端
在專案根目錄下，執行啟動腳本開啟後端服務（預設監聽連接埠 `8001`）：

```bash
bash start_web_ui.sh
```

啟動後，您可直接在瀏覽器訪問 API 自動生成交互文檔：
*   **Swagger UI (交互文檔)**: [http://localhost:8001/docs](http://localhost:8001/docs)
*   **Redoc 文檔**: [http://localhost:8001/redoc](http://localhost:8001/redoc)

---

## 🔒 認證機制 (Authentication)

所有向網格 API 發送的 HTTP 請求，都必須在 Headers 中攜帶 Bearer Token：

```http
Authorization: Bearer <your_GRID_API_TOKEN>
```

---

## 🌐 API 接口清單參考

所有的 API 路由均以 `/api/v1` 為前置首碼，保證與現有 Web UI 路由隔離共存：

### 1. 生命週期管理 (Grid Lifecycle)
*   `POST /api/v1/grids` — **建立新網格**
    *   接收完整的 JSON 參數結構（支援 30+ 高級參數），進行安全驗證與最小投資額檢查後，在 `config/grid/` 生成標準 YAML 配置文件。
    *   若 `auto_start` 設為 `true`（預設），將自動註冊至啟動名單。
*   `POST /api/v1/grids/{grid_id}/start` — **啟動網格**
    *   啟動已存在的網格配置文件，網格引擎將在 ~5 秒內偵測到並在交易所發起掛單。
*   `POST /api/v1/grids/{grid_id}/stop` — **停止網格**
    *   優雅停止運行中的網格，撤銷交易所所有掛單，保持持倉。
*   `DELETE /api/v1/grids/{grid_id}` — **停止並歸檔網格**
    *   停止運行並從啟動名單中永久移除，且將其 YAML 配置文件移至 `config/grid/archived/` 歸檔。
*   `GET /api/v1/grids` — **獲取網格列表**
    *   列出所有已註冊網格的基礎狀態與參數。

### 2. 系統健康與統計監控 (Health & Stats)
*   `GET /api/v1/system/health` — **系統健康與熔斷狀態**
    *   *API 權重消耗: 0*
    *   回傳全局狀態（熔斷器是否開啟、限速器當前窗口消耗、引擎進程 PID、運行中網格數）。
    *   AI Agent 在發起任何建倉或啟動操作前，**必須先調用此接口**確認 `can_accept_new_grid` 是否為 `true`。
*   `GET /api/v1/grids/summary` — **全局 PnL 與統計聚合**
    *   *API 權重消耗: 0*
    *   返回所有網格的即時 PnL 匯總（已實現、未實現、總盈虧）以及 Bot 明細。
*   `GET /api/v1/grids/{grid_id}/stats` — **單網格即時統計明細**
    *   *API 權重消耗: 0*
    *   獲取即時價格、掛單詳情、當前持倉均價與數量、已成交買賣單數與循環數、資金利用率、持倉保證金等。

### 3. 行情精度與審計日誌 (Market & Auditing)
*   `GET /api/v1/market/info/{symbol}` — **交易對精度與最小量**
    *   *API 權重消耗: ≤10 (首次)*
    *   返回交易對價格精度、數量精度、最小下單金額與合約大小（快取時間 60 秒，避免消耗權重）。
*   `GET /api/v1/system/audit` — **SQLite 操作審計歷史**
    *   *API 權重消耗: 0*
    *   可分頁或篩選獲取 Hermes 或手動操作的完整 SQLite 審計歷史，便於追溯 AI 決策動機。

---

## 🐍 Python 客戶端整合

我們提供了標準且嚴格對齊規範的連接組件，直接放置於 [`tools/hermes_grid_client.py`](file:///root/crypto-trading-open/tools/hermes_grid_client.py)。您可以直接匯入使用：

### 1. 使用 `HermesGridClient` 操作 API
```python
from tools.hermes_grid_client import HermesGridClient
import asyncio

async def main():
    # 初始化連接器（將自動嘗試從環境變數載入 GRID_API_TOKEN）
    client = HermesGridClient(base_url="http://localhost:8001")
    
    # 1. 檢查系統是否正常
    if await client.is_system_ready():
        print("系統狀態健康，可接受新網格！")
        
        # 2. 一鍵建立並啟用 SOL 中性網格
        res = await client.create_neutral_grid(
            symbol="SOL/USDC:USDC",
            lower=120.0,
            upper=180.0,
            interval=0.5,
            amount=0.1
        )
        print(f"網格建立成功: {res['grid_id']}")
        
        # 3. 獲取全局盈虧統計
        summary = await client.get_pnl_summary()
        print(f"全局總盈虧: {summary['total_realized_pnl']} USDC")

asyncio.run(main())
```

### 2. 繼承 `HermesGridController` 實作自動化監控
```python
import asyncio
from tools.hermes_grid_client import HermesGridController, HermesDecision

class MySmartHermesAgent(HermesGridController):
    """
    繼承標準控制器，實現 AI 自動分析決策
    """
    
    async def ai_analyze(self, summary: dict) -> list:
        decisions = []
        active_grids = summary.get("active_grids", 0)
        
        # 範例規則：若當前運行中網格小於 2，自動發現機會建倉
        if active_grids < 2:
            decisions.append(
                HermesDecision(
                    action="create",
                    params={
                        "symbol": "SOL/USDC:USDC",
                        "lower": 130.0,
                        "upper": 170.0,
                        "interval": 0.5,
                        "amount": 0.1
                    }
                )
            )
            
        # 範例規則：若某網格虧損過大，發起風控停止
        for grid in summary.get("grids", []):
            if grid.get("total_pnl", 0) < -100.0 and grid.get("status") == "running":
                decisions.append(
                    HermesDecision(
                        action="stop",
                        grid_id=grid.get("grid_id")
                    )
                )
                
        return decisions

async def start_agent():
    # 啟動智能代理
    agent = MySmartHermesAgent(grid_api_url="http://localhost:8001", api_token="your_token")
    # 這將進入計畫書 §4.2 規定的標準雙定時監控循環 (Degraded 休眠 15 分鐘，Healthy 休眠 30 分鐘)
    await agent.monitor_loop()

asyncio.run(start_agent())
```

---

## 🛡️ 安全護欄限制說明 (Safety Guards)

為防範外部 AI 失控操作，網格系統設有高強度的硬性護欄：

1.  **最大網格併發數限制**: 系統同時運行的活躍網格數上限為 **5 個**。超出將拒絕建立或啟動。
2.  **寫操作頻率限制 (Rate Limit)**: 同一來源（例如 `source: "hermes"`）在 **60 秒內最多只能發起 3 次寫入操作**（Create, Start, Stop, Delete）。超出將即刻返回 `429 Too Many Requests` 並記入審計表。
3.  **最小投資額校驗**: 建立網格時，系統會自動評估總投入資金：
    $$\text{總投入} = \text{單格下單量} \times \text{網格總數} \times \text{網格底價}$$
    若評估金額低於 **`GRID_MIN_INVESTMENT`** (預設 `10.0` USDC)，將會被系統攔截拒絕。
4.  **重複交易對防範**: 針對同一個交易對字元 (Symbol)，系統不允許同時運行多個活躍網格配置。

---

## 🧪 整合測試與示範腳本

專案中內嵌了豐富的腳本用於驗證和測試整合：

### 1. 執行集成測試套件
測試套件會在進程內模擬真實認證與路由分發，確保代碼健全度：
```bash
# 執行連接器與控制器集成測試
python3 -m unittest tests/test_hermes_client.py

# 執行全量 47 項 API 端點單元測試
python3 tests/test_hermes_api.py
```

### 2. 運行交互式示範腳本
直接運行示範腳本，可自動在實體後端連接與仿真沙盒模式間切換：
```bash
python3 tools/hermes_grid_demo.py
```
