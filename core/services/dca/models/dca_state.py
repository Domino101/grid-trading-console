"""
DCA 機器人執行狀態（§4.6 凍結狀態機）

生命週期（決策 D-2 / D-3）：
    draft ──start──► armed ──條件成立──► triggering ──基礎單成交──► averaging
    averaging ──止盈/止損成交──► completed ──► cooling ──冷卻結束──► armed（循環）
    completed ──max_cycles / repeat=false──► stopped

不變式相關欄位：
- active_direction：本輪鎖定方向（§4.4 單方向互斥）；armed/cooling 時必為 None
- transition_to() 遇到非法轉移拋 InvalidDcaTransition（INV-5），不得靜默修正
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .dca_order import DcaOrder


class DcaStatus(Enum):
    """DCA 生命週期狀態（§4.6 表格與徽章語意）"""
    DRAFT = "draft"              # 已建立，尚未啟動
    ARMED = "armed"              # 🟡 監控中（0 掛單，INV-1）
    TRIGGERING = "triggering"    # 🔵 進場中（基礎單下單流程內）
    AVERAGING = "averaging"      # 🟢 攤平中（安全單 + 恰好 1 筆 TP，INV-2）
    COMPLETED = "completed"      # ⚪ 本輪完成
    COOLING = "cooling"          # 🕐 冷卻中（循環模式過渡，active_direction 已解除）
    STOPPING = "stopping"        # 🟠 停止中
    STOPPED = "stopped"          # ⚫ 已停止（預設不平倉）
    FAILED = "failed"            # 🔴 失敗（需人工介入）
    DEGRADED = "degraded"        # 🟣 降級（Phase 2 稽核 2.6：TP 無法重掛等暫時性故障，持續重試，期間不得回報正常）


class InvalidDcaTransition(Exception):
    """非法狀態轉移（INV-5）：呼叫方應捕捉並將機器人轉入 failed。"""

    def __init__(self, current: DcaStatus, target: DcaStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"非法的 DCA 狀態轉移: {current.value} -> {target.value}（詳見計畫 §4.6 狀態機圖）"
        )


# §4.6 狀態機圖的合法轉移表（唯一真相來源）
LEGAL_TRANSITIONS: Dict[DcaStatus, Set[DcaStatus]] = {
    DcaStatus.DRAFT: {DcaStatus.ARMED, DcaStatus.FAILED},
    DcaStatus.ARMED: {DcaStatus.TRIGGERING, DcaStatus.STOPPING, DcaStatus.FAILED},
    DcaStatus.TRIGGERING: {
        DcaStatus.AVERAGING,   # 基礎單成交
        DcaStatus.ARMED,       # 條件在成交前失效（order 撤回，回到待命）
        DcaStatus.STOPPING,
        DcaStatus.FAILED,      # 下單失敗
    },
    DcaStatus.AVERAGING: {
        DcaStatus.AVERAGING,   # 安全單成交 → 重掛 TP + 遞補窗口（自迴圈）
        DcaStatus.COMPLETED,   # 止盈 / 止損成交
        DcaStatus.DEGRADED,    # TP cancel 成功但 place 失敗（稽核 2.6）
        DcaStatus.STOPPING,
        DcaStatus.FAILED,
    },
    DcaStatus.DEGRADED: {
        DcaStatus.AVERAGING,   # 下一輪重試成功 → 恢復（稽核 2.6）
        DcaStatus.DEGRADED,    # 持續重試（自迴圈）
        DcaStatus.STOPPING,
        DcaStatus.FAILED,
    },
    DcaStatus.COMPLETED: {
        DcaStatus.COOLING,     # 循環（決策 D-3，預設）
        DcaStatus.STOPPED,     # repeat_after_take_profit=false 或 max_cycles 達成
        DcaStatus.FAILED,
    },
    DcaStatus.COOLING: {
        DcaStatus.ARMED,       # 冷卻結束，重新同時評估多空
        DcaStatus.STOPPING,
        DcaStatus.FAILED,
    },
    DcaStatus.STOPPING: {DcaStatus.STOPPED, DcaStatus.FAILED},
    DcaStatus.STOPPED: set(),  # terminal
    DcaStatus.FAILED: set(),   # terminal
}

# active_direction 必須為 None 的狀態（§4.4：armed/cooling 雙向同時評估）
DIRECTION_FREE_STATUSES = frozenset({
    DcaStatus.DRAFT,
    DcaStatus.ARMED,
    DcaStatus.COOLING,
    DcaStatus.STOPPED,
})


class SlotStatus(Enum):
    """安全單槽位狀態（決策 D-4 活躍窗口）"""
    WAITING = "waiting"      # 虛擬階梯中，尚未掛上交易所
    ACTIVE = "active"        # 已掛在交易所
    FILLED = "filled"        # 已成交
    SKIPPED = "skipped"      # 急跌穿透被跳過（window_realigned）


@dataclass
class SafetyOrderSlot:
    """邏輯階梯中的一個安全單槽位（交易所實際掛單由窗口遞補管理）"""
    index: int                              # 1-based 階梯層號
    planned_price: Decimal                  # 計畫掛單價（由 ladder 計算）
    planned_quantity: Decimal               # 計畫數量
    status: SlotStatus = SlotStatus.WAITING
    actual_price: Optional[Decimal] = None  # 實際掛單價（重對齊後可能不同）
    filled_price: Optional[Decimal] = None
    filled_quantity: Decimal = Decimal("0")

    def mark_active(self, price: Decimal):
        self.status = SlotStatus.ACTIVE
        self.actual_price = price

    def mark_filled(self, price: Decimal, quantity: Decimal):
        self.status = SlotStatus.FILLED
        self.filled_price = price
        self.filled_quantity = quantity

    def mark_skipped(self):
        self.status = SlotStatus.SKIPPED


@dataclass
class DcaState:
    """
    單台 DCA 機器人的執行期狀態（不進 YAML；§4.4 執行期欄位）。

    bot_id = Path(config_path).stem，與網格 grid_id 推導規則一致（§4.1）。
    """
    bot_id: str
    status: DcaStatus = DcaStatus.DRAFT
    active_direction: Optional[str] = None   # "long" / "short" / None（鎖定語意）
    cycle_count: int = 0                     # 已完成的循環輪數
    base_order_filled_at: Optional[datetime] = None
    entry_price: Optional[Decimal] = None    # 本輪基礎單成交均價
    position_quantity: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")       # Σ 已成交名目（不含手續費）
    safety_slots: List[SafetyOrderSlot] = field(default_factory=list)
    active_orders: List[DcaOrder] = field(default_factory=list)
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = None

    @property
    def average_cost(self) -> Optional[Decimal]:
        """當前攤平均價；無部位時為 None（不得回傳 0，G-5）。"""
        if self.position_quantity == 0:
            return None
        return self.total_cost / self.position_quantity

    def can_transition_to(self, target: DcaStatus) -> bool:
        return target in LEGAL_TRANSITIONS[self.status]

    def transition_to(self, target: DcaStatus):
        """
        執行狀態轉移；非法轉移拋 InvalidDcaTransition（INV-5）。

        附帶維護 active_direction 的 §4.4 鎖定語意：
        - 進入 triggering：必須已鎖定方向（否則是程式錯誤）
        - 進入 direction-free 狀態（armed/cooling/stopped）：解除鎖定
        """
        if not self.can_transition_to(target):
            raise InvalidDcaTransition(self.status, target)

        if target is DcaStatus.TRIGGERING and self.active_direction not in ("long", "short"):
            raise InvalidDcaTransition(self.status, target)

        self.status = target

        if target in DIRECTION_FREE_STATUSES:
            self.active_direction = None
        if target is DcaStatus.COMPLETED:
            self.cycle_count += 1

    def init_slots(self, plan_levels):
        """由 ladder 計畫初始化虛擬階梯槽位（index 從 1 起；level 0 是基礎單）。"""
        self.safety_slots = [
            SafetyOrderSlot(
                index=level.index,
                planned_price=level.price,
                planned_quantity=level.quantity,
            )
            for level in plan_levels
            if level.index >= 1
        ]

    def filled_safety_count(self) -> int:
        """已成交安全單數（含 SKIPPED 之外的 FILLED；INV-3 計數口徑）。"""
        return sum(1 for s in self.safety_slots if s.status is SlotStatus.FILLED)

    def __repr__(self) -> str:
        return (
            f"DcaState(bot_id={self.bot_id!r}, status={self.status.value}, "
            f"active_direction={self.active_direction!r}, cycles={self.cycle_count})"
        )
