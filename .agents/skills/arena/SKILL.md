---
name: arena
description: "Spawn N parallel candidates at the same task in isolated worktrees, pick a base, and graft the strongest parts of the losers into it. Use for /arena, 'arena this', or exploring complex strategy/algorithm designs."
---

# Arena (並行方案競爭與精華嫁接)

當面對非瑣碎（Non-trivial）的原型探索、交易演算法設計（例如切單演算法、網格間距分佈、動態停損模型）或架構重構時，單一嘗試容易鎖死在錯誤的形狀上。透過 Arena 開啟多方案並行對決。

---

## 執行流程 (6 大階段)

### Phase A: Frame (定義任務契約與評分規約)
1. 定義產出物（Artifact）。
2. 列出 3-6 個具體可評分的規約（Rubric），作為後續評分的客觀工具。
3. 為每個候選方案分配獨立輸出路徑（優先使用獨立 `git worktree`，或 `/tmp/arena-<slug>/candidate-<n>/`），遵循「先隔離再序列化」原則。

### Phase B: Fan out (平行發散執行)
- 同時啟動多個獨立 Subagent（或背景任務），輸入相同的任務規格與規約。
- 每個候選方案實作完成後，附上一段簡短說明：選擇了什麼方案、考慮過哪些替代方案、以及為何放棄。

### Phase C: Cross-judge (交叉裁決)
- 由獨立的審查角色（Cross-judge）根據 Phase A 的 Rubric，逐項評分各候選方案，並給出推薦作為基準（Base）的理由。

### Phase D: Pick a base (挑選基準)
- 閱讀所有候選方案。
- 依規約各維度比對，選擇未來最容易維護、且最不容易破壞系統不變式的方案作為 Base。
- 若兩者旗鼓相當，優先選擇公開介面更小、更乾淨的方案（遵循 Laziness Protocol）。

### Phase E: Graft (精華嫁接)
- 重新檢視落敗的候選方案，挑選出真正值得移植的 1-2 個亮點（如更優雅的邊界處理、更周全的型別定義）。
- **人工親手將亮點嫁接進 Base**，確保整體維持單一清晰的心智模型，絕不機械式拼貼。

### Phase F: Verify (驗證)
- 對最終融合產物執行真實驗證（`prove-it-works`）。跑測試、確認邊界與回測數據。
