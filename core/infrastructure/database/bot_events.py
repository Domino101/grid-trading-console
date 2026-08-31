"""P3-1: non-blocking bot event emitter.

Trading and the request path must never fail because event capture failed, so
every emission is best-effort: exceptions are logged at WARNING and swallowed.
"""

from __future__ import annotations

from typing import Optional

from core.logging import get_logger

logger = get_logger(__name__)


def emit_bot_event(
    bot_id: str,
    event_type: str,
    payload: Optional[dict] = None,
    created_at: Optional[float] = None,
    dedupe_key: Optional[str] = None,
) -> Optional[int]:
    """Best-effort append to the canonical bot_events stream. Returns seq or None."""
    if not bot_id:
        return None
    try:
        from core.infrastructure.database.ledger_repository import LedgerRepository

        return LedgerRepository.instance().append_event(
            bot_id, event_type, payload=payload, created_at=created_at, dedupe_key=dedupe_key
        )
    except Exception as exc:  # 事件擷取絕不阻斷交易（同 P1-3 帳本語意）
        logger.warning(f"寫入 bot_events 失敗 (略過，不影響交易): bot_id={bot_id} type={event_type}: {exc}")
        return None


def emit_fill_event(fill) -> Optional[int]:
    """Emit a ``fill`` event from a TradeFill (idempotent by fill_id)."""
    from core.infrastructure.database.event_fold import fill_event_payload

    return emit_bot_event(
        fill.bot_id,
        "fill",
        payload=fill_event_payload(fill),
        created_at=float(fill.executed_at),
        dedupe_key=f"fill:{fill.fill_id}",
    )


def emit_reset_checkpoint(
    bot_id: str,
    reset_type: str,
    checkpoint: Optional[dict] = None,
    config_path: Optional[str] = None,
    created_at: Optional[float] = None,
) -> Optional[int]:
    """Emit a reset/take_profit/stop_loss checkpoint (P3-1 + P1-4 audit).

    ``reset_type`` mapping: 止盈 -> take_profit; 本金保護/止損 -> stop_loss;
    其他（價格脫離/手動/移動網格） -> reset.
    """
    text = (reset_type or "").lower()
    if "止盈" in reset_type or "take_profit" in text or "take-profit" in text:
        event_type = "take_profit"
    elif ("止損" in reset_type) or ("止损" in reset_type) or ("本金" in reset_type) or ("stop_loss" in text):
        event_type = "stop_loss"
    else:
        event_type = "reset"
    payload = {"reset_type": reset_type, "checkpoint": checkpoint or {}}
    if config_path:
        payload["config_path"] = config_path
    return emit_bot_event(bot_id, event_type, payload=payload, created_at=created_at)
