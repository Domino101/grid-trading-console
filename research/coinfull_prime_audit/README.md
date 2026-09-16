# CoinFull Prime 稽核重建腳本

本目錄是 `docs/CoinFull_Prime_Audit_Report_2026-09-16.md` 與其四份子文件（`docs/CoinFull_Prime_0*.md`）的完整重現腳本。
所有績效數字都是「用真實日線 OHLCV + 8 小時資金費率」逐日重建的結果，**不是實盤績效**。

## 檔案

| 檔案 | 用途 |
|---|---|
| `engine.py` | 資料載入、因子計算（mom1/3/7/30、fund、flow…）、橫斷面 z 分數、回測引擎 v1、績效指標 |
| `engine2.py` | 回測引擎 v2：`CFG`（long/short count、rebalance_days、gate + gate_action、stops、`max_funding_cost_pct`、`vol_target`、`dd_throttle`、`rebalance_allow`、`trigger_turnover`） |
| `exp1.py` | 基準探索、年化修正、因子 IC |
| `exp2.py` | 持有期/週轉/候選數矩陣 |
| `exp3.py` | 情境分析（BTC 30 日報酬、恐懼貪婪指數） |
| `exp4.py` | 風險閘門映射（`GATE_MAP`、`gate_lin`、`gate_binary`、`gate_hard`）與各狀態報酬 |
| `exp5.py` | 現行設定修正矩陣、建議配置、軟/硬閘門、停損停利、資金費率過濾、成本層級 → **稽核報告第 3–5 節** |
| `exp6.py` | 逐年 walk-forward、60 組參數敏感度、市場中性度、規避下單觸發、閘門語意 → **第 5、7 節** |
| `exp7.py` | 平倉 vs 持有、確認延遲、波動尖峰、分散度、軟閘門 |
| `exp8.py` | **執行時機（相位/頻率/啟動條件）＋ 變盤因應（平倉/持有/反向下單）** → **第 7、8 節** |
| `exp8b.py` | 相位 IS/OOS 切分、7 vs 14 日週期、組合規則 |
| `exp9.py` | 動能與資金費率的確切形式（blend vs composite、3 日平均 vs 單點） |
| `exp10.py` | 規避規則全集：flow 方向、閘門變體、回撤節流、波動尖峰、再進場延遲、最佳組合 → **第 7、8 節** |
| `exp11.py` | 規避規則組合、軟閘門 + 規避、下單次數抑制（rank buffer / no-trade band）、最終候選 K/週期穩健性、資金費率極端值過濾 → **第 7、9 節** |
| `exp12.py` | **最終方案包**（現行 / v2 / v2+T1 / +波動 / +離散 / 保守版 / 積極版）、空手天數統計、日報酬分配、資金費率歸因 → **第 5、9、10 節** |
| `exp13.py` | 現行因子在不同 K 的重建 + 只把換倉改成 7 日的效果 → **第 3.3、5 節** |
| `exp14.py` | **換倉週期（rebalance_days）系統性掃描**：1～28 日 17 種長度 × FULL/IS/OOS、零成本／無閘門對照、換倉相位穩健性（7 種對齊）、對 7 日的配對區塊 bootstrap 檢定 → **第 7.1 節** |
| `exp1_out.txt` ～ `exp14_out.txt`（另有 `exp6_full.txt`、`live_okx_out.txt`） | 已保存的完整實驗輸出（報告中每個表格都可在這裡比對） |
| `live_okx.py` | 即時風險快照：以 OKX 日線 + 資金費率歷史計算 `pts` 與建議倉位倍數（報告 6.5 節） |

## 執行

```bash
# 資料目錄解析順序：環境變數 COINFULL_DATA_DIR → ./research/amm/data → ~/.datacache/amm/data
cd research/coinfull_prime_audit
python3 exp5.py            # 需要 numpy / pandas
python3 exp8.py            # 執行時機 + 變盤
```

資料面板：19 檔 USDT 永續合約日線（2021-01-01 ～ 2026-07-31，2,038 根）、
8 小時資金費率（113,847 筆）、總體指標（恐懼貪婪指數等）。面板與資料集**未進版控**（外部資料目錄）。

## 注意

* 重建使用 19 檔主流幣；策略實際上線宇宙為 top-79，結果可能不同。
* 手續費假設 5bp + 滑價 2bp（另做 0～10bp 敏感度）。
* 資金費率以 8 小時結算資料模擬，持有部位逐日計入。
* 期間內無爆倉/維持保證金/限倉模擬。
