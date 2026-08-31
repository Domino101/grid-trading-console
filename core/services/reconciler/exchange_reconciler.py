"""Exchange-as-Truth Dual-Stage Reconciler and Self-Healing Engine.

Verifies local bot state against exchange real truth (PositionRisk + Open Orders + MyTrades).
Automatically rehydrates missing states and enforces Take-Profit (TP) order protection.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)


class ExchangeReconciler:
    """Three-way state reconciler: Local Ledger <-> Exchange Positions <-> Open Orders."""

    @classmethod
    async def reconcile_dca(cls, coordinator: Any) -> Dict[str, Any]:
        """Perform exchange-as-truth reconciliation for DCA bot.

        Guarantees that any open position on the exchange is rehydrated and protected by TP.
        """
        bot_id = getattr(coordinator, "_bot_id", "unknown")
        symbol = getattr(coordinator._config, "symbol", "")
        executor = getattr(coordinator, "_executor", None)
        if not executor:
            return {"status": "skipped", "reason": "no_executor"}

        result = {
            "bot_id": bot_id,
            "symbol": symbol,
            "rehydrated": False,
            "tp_deployed": False,
            "orders_taken": 0,
        }

        try:
            # 1. First take over any active orders matching the bot namespace
            taken = await coordinator.reconcile_startup_orders()
            result["orders_taken"] = taken

            # 2. Query Exchange Position Truth
            exchange_pos_qty = Decimal("0")
            exchange_avg_cost = Decimal("0")

            # Try executor method or underlying exchange adapter
            exchange = getattr(executor, "_exchange", None)
            if exchange and hasattr(exchange, "get_positions"):
                try:
                    positions = await exchange.get_positions([symbol])
                    if positions:
                        for p in positions:
                            sym = getattr(p, "symbol", "")
                            if sym == symbol or sym.replace("/", "").replace(":", "") == symbol.replace("/", "").replace(":", ""):
                                raw_amt = getattr(p, "amount", getattr(p, "size", 0))
                                exchange_pos_qty = abs(Decimal(str(raw_amt or 0)))
                                raw_entry = getattr(p, "entry_price", 0)
                                if raw_entry:
                                    exchange_avg_cost = Decimal(str(raw_entry))
                                break
                except Exception as e:
                    logger.warning(f"⚠️ [Reconciler {symbol}] 查詢交易所倉位警告: {e}")

            # 3. Detect discrepancy: Exchange has position but Coordinator is empty or missing TP
            local_qty = coordinator.state.position_quantity
            if exchange_pos_qty > Decimal("0") and local_qty == Decimal("0"):
                logger.warning(
                    f"🚨 [Reconciler {symbol}] 檢測到狀態不一致！交易所存在持倉 {exchange_pos_qty} 但本地為 0。啟動自癒回補！",
                    extra={"bot_id": bot_id, "symbol": symbol, "exchange_qty": str(exchange_pos_qty)},
                )

                # Rehydrate average cost
                if exchange_avg_cost <= Decimal("0"):
                    try:
                        exchange_avg_cost = await executor.last_price()
                    except Exception:
                        exchange_avg_cost = Decimal("0")

                coordinator.state.position_quantity = exchange_pos_qty
                coordinator.state.total_cost = exchange_pos_qty * (exchange_avg_cost or Decimal("0"))
                if not coordinator.state.entry_price and exchange_avg_cost > 0:
                    coordinator.state.entry_price = exchange_avg_cost

                # Transition to AVERAGING
                from core.services.dca.models import DcaStatus
                coordinator.state.status = DcaStatus.AVERAGING
                result["rehydrated"] = True

            # 4. Critical TP Protection Check: If position > 0, make sure Take-Profit order is deployed
            if coordinator.state.position_quantity > Decimal("0"):
                has_active_tp = any(
                    getattr(o, "role", None) and str(getattr(o.role, "value", o.role)) == "tp"
                    and getattr(o, "is_active_on_exchange", lambda: True)()
                    for o in coordinator._active.values()
                )
                if not has_active_tp or not coordinator._tp_client_id:
                    logger.warning(
                        f"🛡️ [Reconciler {symbol}] 持倉中且無在世 TP 單，即刻執行 Take-Profit 原子補掛！",
                        extra={"bot_id": bot_id, "symbol": symbol, "qty": str(coordinator.state.position_quantity)},
                    )
                    from core.services.dca.coordinator.take_profit_manager import TakeProfitManager
                    direction = coordinator.state.active_direction or getattr(coordinator._config, "direction", "long")
                    if direction not in ("long", "short"):
                        direction = "long"
                    coordinator.state.active_direction = direction
                    if not coordinator._tp_manager:
                        coordinator._tp_manager = TakeProfitManager(executor, direction)

                    await coordinator._attempt_tp_replace()
                    result["tp_deployed"] = True

            result["status"] = "success"
            return result

        except Exception as exc:
            logger.error(
                f"❌ [Reconciler {symbol}] DCA 雙軌對帳自癒異常: {exc}",
                extra={"bot_id": bot_id, "symbol": symbol, "error": str(exc)},
                exc_info=True,
            )
            result["status"] = "error"
            result["error"] = str(exc)
            return result

    @classmethod
    async def reconcile_grid(cls, coordinator: Any) -> Dict[str, Any]:
        """Perform exchange-as-truth three-way reconciliation for Grid bot (P3-2).

        比對：本地 facts（虛擬持倉/掛單）↔ 交易所 positions ↔ 交易所 open orders。
        自癒情境（計畫 §6 P3-2 驗收）：
        * A「本地持倉=0、交易所持倉>0」→ 以交易所真值回補本地虛擬持倉並記審計。
        * B「本地有掛單/TP、交易所無」→ 將本地殘單標記取消並記審計，
          既有健康檢查/補單機制後續重建格位掛單。
        每一項修復都寫入 ``reconcile`` 事件（bot_events 審計軌跡）。
        """
        bot_id = getattr(coordinator, "bot_id", None) or getattr(coordinator, "grid_id", None) \
            or str(getattr(coordinator.config, "symbol", "unknown"))
        symbol = str(getattr(coordinator.config, "symbol", ""))
        result: Dict[str, Any] = {
            "bot_id": bot_id,
            "symbol": symbol,
            "status": "success",
            "rehydrated": False,
            "stale_local_orders": 0,
            "exchange_only_orders": 0,
            "audits": 0,
        }

        def _audit(action: str, detail: Dict[str, Any]) -> None:
            try:
                from core.infrastructure.database.bot_events import emit_bot_event

                emit_bot_event(
                    str(bot_id),
                    "reconcile",
                    payload={"action": action, "symbol": symbol, **detail},
                    dedupe_key=f"reconcile:{action}:{symbol}:{detail.get('order_id') or int(__import__('time').time())}",
                )
                result["audits"] += 1
            except Exception:
                pass  # 審計失敗不影響對帳修復

        try:
            engine = getattr(coordinator, "engine", None)
            tracker = getattr(coordinator, "tracker", None)
            exchange = getattr(engine, "exchange", None)
            if exchange is None or tracker is None:
                result["status"] = "skipped"
                result["reason"] = "no_exchange_or_tracker"
                return result

            # ── 1. 持倉三路比對（交易所為真相源，ADR-4）─────────────────
            exchange_qty: Optional[Decimal] = None
            exchange_entry: Optional[Decimal] = None
            try:
                positions = await exchange.get_positions([symbol])
                norm_symbol = symbol.replace("/", "").replace(":", "")
                for p in positions or []:
                    psym = str(getattr(p, "symbol", ""))
                    if psym == symbol or psym.replace("/", "").replace(":", "") == norm_symbol:
                        raw = getattr(p, "size", getattr(p, "amount", 0))
                        exchange_qty = abs(Decimal(str(raw or 0)))
                        entry = getattr(p, "entry_price", None)
                        exchange_entry = Decimal(str(entry)) if entry else None
                        break
            except Exception as exc:
                logger.warning(f"⚠️ [Reconciler {symbol}] 查詢交易所持倉失敗（略過持倉比對）: {exc}")

            if exchange_qty is not None:
                local_qty = Decimal(str(getattr(tracker, "virtual_position", 0) or 0))
                if exchange_qty > Decimal("0") and local_qty == Decimal("0"):
                    # 情境 A：交易所持倉、本地為 0 → 回補並記審計
                    entry = exchange_entry if exchange_entry and exchange_entry > 0 else None
                    if entry is None:
                        try:
                            entry = await engine.get_current_price()
                        except Exception:
                            entry = Decimal("0")
                    tracker.sync_recovered_position(exchange_qty, entry or Decimal("0"))
                    state = getattr(coordinator, "state", None)
                    if state is not None:
                        state.current_position = exchange_qty
                    result["rehydrated"] = True
                    logger.warning(
                        f"🚨 [Reconciler {symbol}] 本地持倉=0、交易所持倉={exchange_qty}，"
                        f"已回補本地虛擬持倉（entry={entry}）",
                        extra={"bot_id": str(bot_id), "symbol": symbol},
                    )
                    _audit("position_rehydrated", {
                        "before_local": str(local_qty),
                        "exchange_qty": str(exchange_qty),
                        "entry_price": str(entry or 0),
                    })
                elif exchange_qty == Decimal("0") and local_qty > Decimal("0"):
                    # 本地幽靈持倉（交易所已平/被手動平倉）→ 以交易所為準歸零並記審計
                    tracker.sync_recovered_position(Decimal("0"), Decimal("0"))
                    state = getattr(coordinator, "state", None)
                    if state is not None:
                        state.current_position = Decimal("0")
                    result["rehydrated"] = True
                    logger.warning(
                        f"🚨 [Reconciler {symbol}] 本地虛擬持倉={local_qty} 但交易所為 0，已按交易所真值歸零",
                        extra={"bot_id": str(bot_id), "symbol": symbol},
                    )
                    _audit("position_zeroed_to_exchange", {
                        "before_local": str(local_qty),
                        "exchange_qty": "0",
                    })

            # ── 2. 掛單三路比對（情境 B：本地有單、交易所無）─────────────
            exchange_order_ids: Optional[set] = None
            try:
                open_orders = await exchange.get_open_orders(symbol)
                exchange_order_ids = set()
                for o in open_orders or []:
                    for key in (getattr(o, "id", None), getattr(o, "client_id", None)):
                        if key:
                            exchange_order_ids.add(str(key))
            except Exception as exc:
                logger.warning(f"⚠️ [Reconciler {symbol}] 查詢交易所掛單失敗（略過掛單比對）: {exc}")

            if exchange_order_ids is not None:
                pending = []
                try:
                    pending = list(engine.get_pending_orders() or [])
                except Exception:
                    pending = []
                local_ids = set()
                for order in pending:
                    for key in (getattr(order, "order_id", None), getattr(order, "client_id", None)):
                        if key:
                            local_ids.add(str(key))
                for order in pending:
                    oid = str(getattr(order, "order_id", "") or "")
                    cid = str(getattr(order, "client_id", "") or "")
                    on_exchange = (oid in exchange_order_ids) or (cid in exchange_order_ids)
                    if not on_exchange and order.is_pending():
                        # 情境 B：本地掛單（含 TP 相關單）交易所已無 → 本地標記取消 + 審計
                        order.mark_cancelled()
                        result["stale_local_orders"] += 1
                        _audit("stale_local_order_cancelled", {
                            "order_id": oid or cid,
                            "side": str(getattr(getattr(order, "side", None), "value", "")),
                            "price": str(getattr(order, "price", "")),
                            "grid_id": getattr(order, "grid_id", None),
                        })
                exchange_only = len(exchange_order_ids - local_ids)
                result["exchange_only_orders"] = exchange_only
                if exchange_only:
                    _audit("exchange_only_orders_observed", {"count": exchange_only})

            # ── 3. 既有恢復路徑（計數/盈虧 facts 重建）──────────────────
            await coordinator.recover_old_orders()
            return result
        except Exception as e:
            logger.error(f"❌ [Reconciler {symbol}] Grid 對帳異常: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)
            return result
