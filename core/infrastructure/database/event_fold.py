"""P3-1: Event-sourcing fold — rebuild projections from the bot_events stream.

Canonical order (ADR-3/計畫 §6):
1. If the bot has ``fill`` events in ``bot_events``, the event stream is the
   canonical timeline (fills included as events).
2. Legacy bots without fill events fall back to ``trade_fills`` merged with
   lifecycle events by timestamp.

Fold semantics mirror ``rehydrate_position_from_fills`` (fill math) plus
lifecycle checkpoints:
* ``reset`` / ``take_profit`` / ``stop_loss`` events carry an optional
  ``checkpoint`` dict with absolute counters (cycles/buy/sell/fees/realized)
  captured from the tracker *after* the runtime reset, plus implicit position
  zeroing (grid resets are flat-with-drawdown restarts).  Checkpoints make
  replay == direct-write by construction.
* Other event types (bot_created / bot_renamed / order_placed /
  order_cancelled / state_transition / reconcile) do not affect the position
  projection and are carried for audit/replay context.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from core.infrastructure.database.models import BotEvent, BotPosition, TradeFill

# Event types that mutate the position projection during a fold.
POSITION_EVENT_TYPES = {"fill", "reset", "take_profit", "stop_loss"}


def fill_event_payload(fill: TradeFill) -> dict:
    """Canonical payload for a ``fill`` event (mirrors the TradeFill row)."""
    return {
        "fill_id": fill.fill_id,
        "client_order_id": fill.client_order_id,
        "exchange_order_id": fill.exchange_order_id,
        "side": fill.side,
        "role": fill.role,
        "price": float(fill.price),
        "quantity": float(fill.quantity),
        "fee": float(fill.fee or 0.0),
        "realized_pnl": float(fill.realized_pnl or 0.0),
        "executed_at": float(fill.executed_at),
    }


def events_from_fills(fills: Iterable[TradeFill]) -> List[BotEvent]:
    """Synthesize ``fill`` events from trade_fills rows (legacy fallback)."""
    events: List[BotEvent] = []
    for f in fills:
        events.append(
            BotEvent(
                bot_id=f.bot_id,
                seq=0,  # synthetic; ordering resolved by created_at
                event_type="fill",
                payload=fill_event_payload(f),
                created_at=float(f.executed_at),
                dedupe_key=f"fill:{f.fill_id}",
            )
        )
    return events


def build_timeline(store, bot_id: str) -> List[BotEvent]:
    """Canonical replay timeline for one bot (events-first, fills fallback)."""
    events = store.get_events(bot_id)
    has_fill_events = any(e.event_type == "fill" for e in events)
    if not has_fill_events:
        merged = list(events) + events_from_fills(store.get_fills(bot_id, limit=1_000_000))
        merged.sort(key=lambda e: (e.created_at, e.seq))
        return merged
    return sorted(events, key=lambda e: e.seq)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fold_timeline(bot_id: str, timeline: List[BotEvent]) -> BotPosition:
    """Fold a replay timeline into the position projection.

    The fill math is intentionally identical to
    ``SQLAlchemyLedgerStore.rehydrate_position_from_fills`` so a replay of the
    same trades reproduces the direct-write projection exactly (P3 Gate).
    """
    net_qty = 0.0
    total_cost = 0.0
    realized_profit = 0.0
    total_fees = 0.0
    cycles = 0
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    last_reset_type: Optional[str] = None
    reset_count = 0

    for event in timeline:
        etype = event.event_type
        if etype == "fill":
            payload = event.payload
            side = str(payload.get("side", "")).lower()
            price = _num(payload.get("price"))
            qty = _num(payload.get("quantity"))
            total_fees += _num(payload.get("fee"))
            realized_profit += _num(payload.get("realized_pnl"))
            if str(payload.get("role", "")).lower() == "tp":
                cycles += 1
            if side == "buy":
                net_qty += qty
                total_cost += price * qty
                if entry_price is None:
                    entry_price = price if price else None
                    direction = "long"
            elif side == "sell":
                net_qty -= qty
                total_cost -= price * qty
                if entry_price is None:
                    entry_price = price if price else None
                    direction = "short"
        elif etype in {"reset", "take_profit", "stop_loss"}:
            # Legal zeroing (P1-4 semantics): grid resets restart flat; the
            # checkpoint records the authoritative post-reset counters so the
            # fold matches the direct-write projection.
            net_qty = 0.0
            total_cost = 0.0
            entry_price = None
            checkpoint = event.payload.get("checkpoint") if isinstance(event.payload, dict) else None
            if isinstance(checkpoint, dict):
                cycles = int(_num(checkpoint.get("cycles")))
                realized_profit = _num(checkpoint.get("realized"))
                total_fees = _num(checkpoint.get("fees"))
            else:
                cycles = 0
            last_reset_type = str(event.payload.get("reset_type", etype)) if isinstance(event.payload, dict) else etype
            reset_count += 1

    avg_cost = (total_cost / net_qty) if net_qty > 0.00000001 else None
    return BotPosition(
        bot_id=bot_id,
        active_direction=direction,
        quantity=max(0.0, net_qty),
        average_cost=avg_cost,
        total_cost=max(0.0, total_cost),
        entry_price=entry_price,
        completed_cycles=cycles,
        realized_profit=realized_profit,
        total_fees=total_fees,
        updated_at=timeline[-1].created_at if timeline else 0.0,
    )


def rebuild_position(store, bot_id: str) -> BotPosition:
    """Rebuild the projection from the canonical facts (events + fills)."""
    return fold_timeline(bot_id, build_timeline(store, bot_id))


def diff_projection(a: BotPosition, b: BotPosition, epsilon: float = 1e-6) -> Dict[str, Tuple[float, float]]:
    """Field-level diff between two projections (for replay/DR reports)."""
    fields = ("quantity", "completed_cycles", "realized_profit", "total_fees")
    diffs: Dict[str, Tuple[float, float]] = {}
    for name in fields:
        va = float(getattr(a, name) or 0)
        vb = float(getattr(b, name) or 0)
        if abs(va - vb) > epsilon:
            diffs[name] = (va, vb)
    return diffs
