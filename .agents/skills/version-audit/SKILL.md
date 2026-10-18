---
name: version-audit
description: "Version-to-version code audit and change analysis for grid-trading-console. Upgraded to and superseded by blast-radius: performs token-efficient diff analysis and proves safety via executable runtime checks."
---

# Version Audit (已升級為 Blast Radius 變更安全審查)

> [!IMPORTANT]
> 本技能已升級並全面對齊 `blast-radius`。進行跨版本對比或變更審核時，請直接遵循 [`blast-radius`](../blast-radius/SKILL.md) 規範，強制以真實可執行的運行證據（Certainty Ladder Level 4-5）證明變更安全性。

## When to Activate
- 用戶說「版本對比 / 版本稽核 / compare versions / 變更報告 / what changed / 變更影響」
- 比較分支差異（如 `git diff main...HEAD`）或發布前審核

## Core Procedure
1. 執行 `git diff --stat <base> <head>` 取得模組變更清單。
2. 呼叫並遵循 [`blast-radius`](../blast-radius/SKILL.md) 流程：
   - 找出變更安全所仰賴的核心事實（Linchpin Safety Fact）。
   - 執行程式碼或測試腳本，產出等級 4 以上的確信度證明。
   - 輸出標準審查報告（What it does / Linchpin Fact / Confirmed Risks / Cleared Items）。
