"""
DirectionLock —— direction=both 的單方向互斥鎖定（決策 D-2，§4.4，INV-6）

語意（凍結）：
- armed 狀態下同時評估多空兩套入場條件；先成立者鎖定本輪。
- 鎖定期間（triggering/averaging）另一方向完全忽略：不評估、不下單。
- 同一根 K 棒雙向皆成立 → 依 both_tiebreak 決定，記錄 both_directions_matched。
- 本輪結束（completed → cooling）解除鎖定，下一輪可換向（稽核 2.17b）。
"""

from typing import Callable, Optional

from ..models.dca_config import DcaDirection


class DirectionLock:
    """一台 bot 的本輪方向互斥鎖。"""

    def __init__(self, direction: DcaDirection, both_tiebreak: str = "long_first",
                 event_handler: Optional[Callable[[str, dict], None]] = None):
        self._configured = direction
        self._tiebreak = both_tiebreak
        self._emit = event_handler or (lambda name, payload: None)
        self.active: Optional[str] = None  # "long" / "short" / None

    @property
    def configured(self) -> DcaDirection:
        return self._configured

    def candidate_directions(self):
        """armed 評估時要檢查的方向清單（fixed 方向只評估自己）。"""
        if self._configured is DcaDirection.LONG:
            return ("long",)
        if self._configured is DcaDirection.SHORT:
            return ("short",)
        return ("long", "short")

    def acquire(self, long_matched: bool, short_matched: bool) -> Optional[str]:
        """
        嘗試鎖定本輪方向。回傳鎖定方向；雙向皆未成立或已鎖定時回傳 None。
        """
        if self.active is not None:
            return None  # INV-6：鎖定期間不得換向

        fixed = self._configured
        if fixed is DcaDirection.LONG:
            short_matched = False
        elif fixed is DcaDirection.SHORT:
            long_matched = False

        if long_matched and short_matched:
            winner = "long" if self._tiebreak == "long_first" else "short"
            self._emit("both_directions_matched", {
                "active_direction": winner,
                "tiebreak": self._tiebreak,
            })
            self.active = winner
            return winner
        if long_matched:
            self.active = "long"
            return "long"
        if short_matched:
            self.active = "short"
            return "short"
        return None

    def ignore_reverse_signals(self) -> bool:
        """鎖定期間反向訊號一律忽略（稽核 2.17：斷言無反向單的語意來源）。"""
        return self.active is not None

    def release(self) -> None:
        """本輪結束解除鎖定。"""
        self.active = None
