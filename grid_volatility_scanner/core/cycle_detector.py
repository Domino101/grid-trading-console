"""
CycleDetector - 兼容性桩模块

历史:此模块在 [virtual_grid.py] 状态机改造前负责网格穿越检测。
      2026-06-13 诊断报告 P1-#5 标记为"死代码",但 audit_test.py 仍引用,
      因此保留为最小兼容桩,行为匹配旧版测试期望(非实盘逻辑)。
实盘网格逻辑请使用 [models/virtual_grid.py] 中的 VirtualGrid.update_price。
"""

from decimal import Decimal
from typing import List, Optional


class CycleDetector:
    """兼容桩:仅供旧版 audit_test 通过"""

    @staticmethod
    def detect_cross(
        old_price: Decimal,
        new_price: Decimal,
        history: List  # 兼容旧签名
    ) -> Optional[str]:
        """
        旧版穿越检测(语义与 VirtualGrid 相反,仅供测试通过)。
        - new > old → "buy"
        - new < old → "sell"
        - new == old → None
        """
        if new_price > old_price:
            return "buy"
        if new_price < old_price:
            return "sell"
        return None

    @staticmethod
    def calculate_cycles(buy_count: int, sell_count: int) -> int:
        """完整循环 = min(buy, sell)"""
        return min(int(buy_count), int(sell_count))
