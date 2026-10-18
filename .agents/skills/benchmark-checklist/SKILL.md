---
name: benchmark-checklist
description: "Vet performance claims, speedups, regressions, or backtest results before reporting or acting on them. Answers the 7 performance questions with real execution evidence. Use for backtest analysis, strategy optimization, or API latency measurements."
---

# Benchmark Checklist (基準測試與回測效能 7 問)

當你產出任何效能數字、回測指標（如 Sharpe 比率、最大回撤、勝率）、延遲改善宣稱、或在不同演算法間做選擇時，**必須以運行證據回答以下 7 個問題**，嚴禁以靜態推論作為答案。

---

## 執行測試前的準備
- **明確寫出預期宣稱**：例如「在 60,000 根 K 線數據集下，撮合引擎 p50 耗時縮短 30%」或「在歷史回測中收益提升 15%」。
- **檢查測試腳本**：確認它測量了什麼、計數了什麼、以及忽略了什麼。
- **檢查系統負載**：使用 `uptime` 與 `nproc` 確認機器是否忙碌，避免噪聲干擾結果。

---

## 效能與回測 7 問 (The 7 Questions)

1. **Why not double? (瓶頸在哪裡？)**
   - 指出限制效能的瓶頸（Limiter）。透過 `top`, `py-spy`, `strace` 或 cProfile 定位熱點。若更動沒有帶來數字變化，瓶頸能解釋為什麼，先找出瓶頸再下結論。
2. **Was it tuned? (配置是否對等與調優？)**
   - 雙方是否都以生產環境等級的配置運行？（例如是否開啟了除錯日誌？連線池是否建立？交易手續費率與滑價是否一致？）若一方使用預設未調優配置，你比較的只是配置差異而非實作優勢。
3. **Did it break limits? (是否突破物理與數學極限？)**
   - 計算理論極限。若刪除一個僅佔總執行時間 10% 的模組，整體效能最多提升 ~11%。若數字好得不真實，很可能是測量到了快取、空操作（No-op）或邏輯短路。
4. **Did it error? (是否有錯誤被吞掉？)**
   - 失敗與拒絕通常非常快，而超時與重試非常慢。確認錯誤計數為零，且回傳的訂單/計算結果是正確的，而不只是「有回傳」。
5. **Does it reproduce? (是否能穩定重現？)**
   - 至少交替執行 5 次（A, B, A, B...），排除快取預熱與漂移偏差。回報中位數（Median）與範圍（Range）。若差異小於波動範圍，視為無顯著差異。
6. **Does it matter? (端到端是否有感？)**
   - 除了微基準測試（Micro-benchmark），更要測量終端使用者或交易循環等待的端到端耗時。佔整個請求 1% 的輔助函式即使快上 10 倍，整體也頂多快 1%。
7. **Did it even happen? (工作是否真的被執行了？)**
   - 確認被測量的核心工作確實在計時區間內執行：請求真正打到了後端？資料真正寫入了資料庫？還是非同步操作未被 await、生成器未被遍歷就提前結束？

---

## 報告規範
- **結論先行**：明確給出結論：Faster（更快）、Slower（更慢）、No Measurable Difference（無顯著差異）或 Inconclusive（無法下定論）。
- **數據與瓶頸並列**：附上單位、測試輪數、分佈區間與限制瓶頸（例如：`p50: 42ms -> 31ms, 5 次中位數, 區間 30-33ms, 瓶頸受限於 SQLite 序列化`）。
- **回測專屬宣告**：標記是否包含滑價（Slippage）、Maker/Taker 手續費率、以及是否排除前視偏差（Look-ahead bias）。
