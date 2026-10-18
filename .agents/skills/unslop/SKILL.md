---
name: unslop
description: "Cut AI tells and superficial filler from writing, technical specs, and review reports. Removes artificial jargon, excessive formatting, and empty prose."
---

# Unslop (去 AI 油水與技術寫作品質標準)

本技能規範技術文件、PR 說明、Commit 訊息與審查報告的寫作風格，嚴格清除「AI 油水（AI Slop）」與虛假贅詞。

---

## 需偵測並掃除的模式 (Patterns to Detect & Eliminate)

### 1. 內容油水 (Content Slop)
- **浮誇的 -ing 結尾子句**：如「ensuring that...」、「fostering a robust...」、「highlighting the importance of...」。刪除或用具體資料源展開。
- **模糊的權威歸因**：如「業界專家認為」、「研究顯示」、「許多分析師指出」。直接指出具體文件、PR 編號或指標數據，否則刪除。

### 2. AI 詞彙庫 (AI Vocabulary)
- 避免空洞抽象詞：*additionally, crucial, delve, intricate, landscape, pivotal, showcase, tapestry, underscore, vibrant, 深度剖析, 畫龍點睛, 璀璨, 賦能*.
- 改用直接樸素的工程語言（「具備」、「含有」、「為」、「因」）。

### 3. 排版與標點過度 (Stylistic Overuse)
- **破折號（Em dash）過度使用**：盡量使用句號或逗號斷句。
- **粗體濫用（Boldface Overuse）**：不要在每個專有名詞、縮寫上都加粗體。只有關鍵結論與核心指標才加粗。
- **裝飾性 Emoji**：技術文件與審查報告中嚴禁在標題與列表塞滿 🚀🔥✨ 等裝飾性 Emoji。
- **行內重複標籤**：避免 `**Performance:** The performance is good...` 這種同義反覆。

### 4. 機器人對話贅詞 (Chatbot Fluff)
- 嚴禁出現：「Hope this helps!」、「Certainly!」、「Great question!」、「As an AI...」等諂媚或客服語句，直奔技術核心。
