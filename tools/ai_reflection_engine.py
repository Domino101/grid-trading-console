#!/usr/bin/env python3
"""
AI 決策反思與歸因評估引擎 (AI Decision Reflection & Attribution Engine)

功能：
1. 讀取 `data/ai_memory/decisions/` 下的歷史決策快照。
2. 比對過去 N 天內的真實行情（K線最高/最低價）與機器人實際表現（成交次數、實現利潤、最大浮虧）。
3. 產生客觀的「假說 vs 現實」評估指標，供 LLM 進行多維度反思。
4. 輸出反思報告至 `data/ai_memory/reflections/` 並提煉經驗至 `lessons_learned.json`。
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# 添加根目錄
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.infrastructure.database.ledger_store import SqliteLedgerStore
from web_ui.backend.grid_state_store import GridStateStore


class ReflectionEngine:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or project_root)
        self.decisions_dir = self.base_dir / "data" / "ai_memory" / "decisions"
        self.reflections_dir = self.base_dir / "data" / "ai_memory" / "reflections"
        self.lessons_path = self.base_dir / "data" / "ai_memory" / "lessons_learned.json"
        self.db_path = str(self.base_dir / "data" / "web_ui_runtime.sqlite3")

    def list_pending_decisions(self) -> List[Path]:
        """列出所有待覆盤的決策檔案（支援 decisions/call_xxx/ 結構與根目錄 *.json）"""
        if not self.decisions_dir.exists():
            return []
        sub_files = list(self.decisions_dir.glob("call_*/*.json"))
        root_files = [p for p in self.decisions_dir.glob("*.json") if p.is_file()]
        all_files = sorted(sub_files + root_files)
        return all_files

    def get_or_create_call_dir(self, call_number: Optional[int] = None) -> Path:
        """
        獲取或建立下一次調用的決策目錄：decisions/call_{N:03d}_{YYYYMMDD_HHMMSS}/
        """
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        existing_calls = sorted([p for p in self.decisions_dir.glob("call_*") if p.is_dir()])
        if call_number is None:
            call_number = len(existing_calls) + 1
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = self.decisions_dir / f"call_{call_number:03d}_{now_str}"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def evaluate_decision(self, decision_file: Path) -> Dict[str, Any]:
        """評估單一決策的真實表現"""
        with open(decision_file, "r", encoding="utf-8") as f:
            decision = json.load(f)

        bot_id = decision.get("bot_id")
        created_at = decision.get("created_at", 0)
        symbol = decision.get("symbol")
        strategy = decision.get("strategy_type", "grid")
        hypothesis = decision.get("hypothesis", {})
        params = decision.get("parameters", {})

        # 1. 從 SQLite 讀取持倉與盈虧事實
        store = SqliteLedgerStore(self.db_path)
        pos = store.get_position(bot_id) if bot_id else None
        fills = store.get_fills(bot_id, limit=500, since=created_at) if bot_id else []

        realized_profit = pos.realized_profit if pos else 0.0
        completed_cycles = pos.completed_cycles if pos else 0
        current_quantity = pos.quantity if pos else 0.0
        avg_cost = pos.average_cost if pos else None

        # 2. 從 GridStateStore 讀取即時快照（最新價、浮動盈虧）
        runtime_map = GridStateStore.instance().get_all()
        runtime_data = {}
        for path, data in runtime_map.items():
            if data.get("symbol") == symbol:
                runtime_data = data
                break

        current_price = runtime_data.get("current_price", decision.get("market_snapshot", {}).get("price_at_decision"))
        unrealized_pnl = runtime_data.get("unrealized_pnl", 0.0)

        # 3. 計算評估指標
        elapsed_hours = round((time.time() - created_at) / 3600.0, 2)
        total_pnl = realized_profit + unrealized_pnl

        # 4. 對帳比較
        expected_cycles = hypothesis.get("expected_cycles_in_72h", 0)
        cycles_fulfillment_pct = round((completed_cycles / expected_cycles * 100.0), 1) if expected_cycles > 0 else 0.0

        invalidation_price = hypothesis.get("invalidation_price")
        is_invalidated = False
        if invalidation_price and current_price:
            if strategy == "dca" and current_price < invalidation_price:
                is_invalidated = True
            elif strategy == "grid" and current_price < invalidation_price:
                is_invalidated = True

        report = {
            "call_session": decision_file.parent.name,
            "file_path": str(decision_file.relative_to(self.base_dir)),
            "decision_id": decision.get("decision_id"),
            "bot_id": bot_id,
            "symbol": symbol,
            "strategy": strategy,
            "elapsed_hours": elapsed_hours,
            "market": {
                "initial_price": decision.get("market_snapshot", {}).get("price_at_decision"),
                "current_price": current_price,
                "price_change_pct": round(((current_price - decision.get("market_snapshot", {}).get("price_at_decision", current_price)) / decision.get("market_snapshot", {}).get("price_at_decision", 1)) * 100.0, 2) if current_price else 0.0,
                "invalidation_price": invalidation_price,
                "is_invalidated": is_invalidated
            },
            "performance": {
                "completed_cycles": completed_cycles,
                "expected_cycles": expected_cycles,
                "cycles_fulfillment_pct": cycles_fulfillment_pct,
                "total_fills_count": len(fills),
                "realized_profit_usd": round(realized_profit, 4),
                "unrealized_pnl_usd": round(unrealized_pnl, 4),
                "net_profit_usd": round(total_pnl, 4),
                "current_position_size": current_quantity,
                "average_cost": avg_cost
            },
            "hypothesis_comparison": {
                "expected_profit_usd": hypothesis.get("expected_profit_usd_estimate"),
                "actual_vs_expected_pnl": round(total_pnl - hypothesis.get("expected_profit_usd_estimate", 0.0), 4)
            }
        }
        return report

    def run_all_reflections(self) -> List[Dict[str, Any]]:
        """批次評估所有待覆盤的決策檔案"""
        results = []
        for file_path in self.list_pending_decisions():
            rep = self.evaluate_decision(file_path)
            results.append(rep)
        return results


if __name__ == "__main__":
    engine = ReflectionEngine()
    print("🔍 [AI Reflection Engine] 啟動歷史決策對帳檢驗...")
    reports = engine.run_all_reflections()
    print(f"✅ 已檢驗 {len(reports)} 個歷史決策:\n")
    print(json.dumps(reports, indent=2, ensure_ascii=False))
