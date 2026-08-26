"""
DCA 機器人資料模型

包含設定、執行狀態（含安全單槽位）、訂單與統計的核心資料結構。
"""

from .dca_config import (
    AveragingMethod,
    DcaConfig,
    DcaConfigError,
    DcaDirection,
    DcaMode,
    EntryConfig,
    EntryMode,
)
from .dca_order import (
    DcaOrder,
    DcaOrderRole,
    DcaOrderStatus,
    bot_id_hash8,
    build_client_order_id,
)
from .dca_state import (
    DcaState,
    DcaStatus,
    InvalidDcaTransition,
    SafetyOrderSlot,
    SlotStatus,
)
from .dca_statistics import DcaStatistics

__all__ = [
    "AveragingMethod",
    "DcaConfig",
    "DcaConfigError",
    "DcaDirection",
    "DcaMode",
    "EntryConfig",
    "EntryMode",
    "DcaOrder",
    "DcaOrderRole",
    "DcaOrderStatus",
    "bot_id_hash8",
    "build_client_order_id",
    "DcaState",
    "DcaStatus",
    "InvalidDcaTransition",
    "SafetyOrderSlot",
    "SlotStatus",
    "DcaStatistics",
]
