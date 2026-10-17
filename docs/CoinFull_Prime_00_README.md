# CoinFull Prime 稽核與優化文件包

*日期：2026-09-16｜資料區間：2021-01-01 ～ 2026-07-31（19 檔 USDT 永續合約日線 + 8 小時資金費率）*

## 文件清單

| # | 文件 | 內容 | 適用對象 |
|---|---|---|---|
| **0** | **[稽核報告（主檔）](./CoinFull_Prime_Audit_Report_2026-09-16.md)** | 程式實作稽核、目前獲利能力、因子研究、建議配置、**執行時機**、**變盤因應（規避 vs 反向）**、倉位時機、優化路線圖 | 全部 |
| 1 | [獲利優化與降低虧損計畫](./CoinFull_Prime_01_Profit_Optimization_Plan.md) | 三階段優化、參數表、時程、監控指標、已排除方法 | 決策者 / 工程 |
| 2 | [最適用情境手冊](./CoinFull_Prime_02_Regime_Playbook.md) | 風險分數 pts、各狀態績效、即時快照、狀態轉換原則 | 交易 / 風控 |
| 3 | [自動規避下單時機規格書](./CoinFull_Prime_03_NoTrade_Trigger_Spec.md) | 6 條觸發規則（T1–T6）、每日檢查流程、實作位置 | 工程 |
| 4 | [倉位增減時機規格書](./CoinFull_Prime_04_Position_Sizing_Timing.md) | 倉位對照表、加倉/減倉條件、公式、檢核表 | 交易 / 工程 |
| 5 | [75天回測與策略規範說明書 (v2.1)](./CoinFull_Prime_75d_Backtest_and_Strategy_Spec.md) | 幣安合約 58 天實測逐筆成交還原、多空雙腿歸因、基線基準 | 交易 / 工程 |
| 6 | [量化策略深度稽核與優化指南 (v3.0)](./CoinFull_Prime_Strategy_Audit_and_Optimization_Report.md) | 統計倖存與空頭腿失血機理診斷、五大優化升級藍圖 (評級 C+) | 量化 / 風控 |
| 7 | [v3.0 策略優化實證回測報告](./CoinFull_Prime_v3_Comparative_Backtest_Report.md) | 歷史同週期逐筆模擬比對、開單內容變更對照、績效翻倍實證 (評級 A-) | 決策者 / 量化 |

## 三個關鍵數字

| 現行設定 | 建議設定 |
|---|---|
| −76.4%｜年化 −23.4%｜Sharpe −0.42 | **+154.1%｜年化 18.8%｜Sharpe 1.41（樣本外 25.1% / 1.94）** |

原因：每日換倉讓週轉達 1.59 倍/日、年費用約 40% 本金；改為「每週四 UTC 00:05 換倉 + 重建因子 + 風險閘門」後成本降至 ~2.9%/年。

## 最重要的三個行動

1. **換倉頻率 1 日 → 7 日**（固定在週四 UTC 00:05）。
2. **因子重建**：動能改 `0.5×R1d + 0.3×R7d + 0.2×R30d`、資金費率取 −3 日平均、**移除 taker 買賣壓力與大戶比**。
3. **加入風險閘門**：`pts = 0` → **平倉並停止下單**（樣本外 Sharpe 0.84 → 1.94）。遇到變盤**不要反向下單**。

## 實作與重現狀態（2026-09-16）

* **風險閘門已實作**：`core/services/coinfull_prime/regime_filter.py`，由 `service.py` 於換倉前呼叫、`rebalancer.py` 套用（`gross_scale / allow_new_entries / force_flatten`），查詢端點 `GET /api/v1/coinfull-prime/regime`。
* **設定檔已更新**：`config/coinfull_prime/strategy_config.yaml`（5/5 檔、權重 0.6/0.4、`momentum.mode=blend`、`funding.mode=smooth`、`rebalance_days=7`、`execution_weekday=3`(週四)、`risk_overlay.*`）。
* **測試**：`python3 -m unittest tests.test_coinfull_prime_quant tests.test_coinfull_prime_rebalance tests.test_coinfull_prime_regime` → **21 項通過**。
* **尚待實作**：`quant_engine.py` 的新因子形式（多時距動能、資金費率平滑、移除 flow/大戶比）、排程改為每週四 00:05、每日權益/PnL 落地（詳見主報告 §10.2 實作狀態表）。

## 重要提醒

* 本 repo 內**沒有任何實盤或模擬盤成交紀錄**（預設 `exchange: paper`、`dry_run: true`，且無資料庫/狀態檔），因此**所有績效都是歷史重建**，不是實盤結果。
* 重建使用 19 檔主流幣；策略實際上線的宇宙是 top-79，結果可能不同，上線前建議用實際宇宙重跑。
* 重現腳本與完整實驗輸出（每個表格都可逐項比對）：[`research/coinfull_prime_audit/`](../research/coinfull_prime_audit/)（`engine.py`、`engine2.py`、`exp1.py`～`exp14.py`、`live_okx.py`、`exp*_out.txt`）。
