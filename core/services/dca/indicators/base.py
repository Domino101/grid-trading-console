"""
技術指標共用基礎定義（§4.3.0 資料管線）

- IndicatorResult：所有指標評估的統一回傳型別
- drop_unclosed：運算約定 1 —— 最後一根未收線 K 棒一律丟棄
- 暖機期語意：warmup_need = 產生首個有效值所需的 K 棒數；
  insufficient_data=True 時 triggered 恆為 False（運算約定 5）。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union

Number = Union[int, float]


@dataclass(frozen=True)
class IndicatorResult:
    """
    單一指標在「最後一根已收線 K 棒」上的評估結果。

    value             主數值（完整精度；UI 顯示時才四捨五入到 2 位，運算約定 7）
    triggered         該方向的入場條件是否成立（資料不足時恆 False）
    insufficient_data 是否因暖機資料不足而無法計算
    warmup_have       目前已有的有效 K 棒數（上限 warmup_need）
    warmup_need       產生首個有效值所需的 K 棒數
    as_of             用於計算的最後一根已收線 K 棒索引（0-based，相對輸入序列）
    details           指標附屬數值（如 MACD line/signal/hist、BB 三軌與 %B）
    """
    value: Optional[float]
    triggered: bool
    insufficient_data: bool
    warmup_have: int
    warmup_need: int
    as_of: Optional[int]
    details: Dict[str, Optional[float]] = field(default_factory=dict)


def drop_unclosed(values: Sequence[Number], last_bar_closed: bool = True) -> List[float]:
    """
    運算約定 1：只使用已收線 K 棒。

    last_bar_closed=False 時丟棄序列末端（未收線）那一根；
    回傳全新的 float list，副作用為零。
    """
    out = [float(v) for v in values]
    if not last_bar_closed and out:
        out.pop()
    return out


def first_valid_index(series: Sequence[Optional[float]]) -> Optional[int]:
    """序列中首個非 None 值的索引；全 None 時回傳 None。"""
    for i, value in enumerate(series):
        if value is not None:
            return i
    return None


def validate_direction(direction: str) -> str:
    """方向僅接受 'long' / 'short'；其餘拋明確異常。"""
    if direction not in ("long", "short"):
        raise ValueError(f"無效的方向: {direction!r}，合法值: long, short")
    return direction
