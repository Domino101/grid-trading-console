"""
DCA 機器人（Dollar-Cost Averaging Bot）子系統

依據 docs/dca_bot_implementation_plan_zh-TW.md 實作。

Phase 1 交付範圍（純函式核心，零外部呼叫 / 零檔案存取 / 零新依賴）：
- models/      DcaConfig / DcaState / DcaOrder / DcaStatistics（§4.2、§4.6）
- indicators/  RSI / MACD / Bollinger / Price Change（§4.3，純 stdlib）
- entry/       EntryEvaluator（immediate / indicator，all/any + confirm_bars）
- ladder.py    安全單階梯數學（§4.5，全 Decimal，凍結公式 + 鎖定向量）

設計硬性規則：
- 金額一律 Decimal（浮點僅允許出現在顯示序列化與技術指標數值）
- 本套件不得進行任何網路、檔案、資料庫或事件迴圈操作
"""

from .models import (
    AveragingMethod,
    DcaConfig,
    DcaConfigError,
    DcaDirection,
    DcaMode,
    DcaOrder,
    DcaOrderRole,
    DcaState,
    DcaStatistics,
    DcaStatus,
    EntryMode,
    InvalidDcaTransition,
    SafetyOrderSlot,
)
from .ladder import LadderLevel, LadderPlan, build_ladder, required_capital, take_profit_price

__all__ = [
    "AveragingMethod",
    "DcaConfig",
    "DcaConfigError",
    "DcaDirection",
    "DcaMode",
    "DcaOrder",
    "DcaOrderRole",
    "DcaState",
    "DcaStatistics",
    "DcaStatus",
    "EntryMode",
    "InvalidDcaTransition",
    "SafetyOrderSlot",
    "LadderLevel",
    "LadderPlan",
    "build_ladder",
    "required_capital",
    "take_profit_price",
]
