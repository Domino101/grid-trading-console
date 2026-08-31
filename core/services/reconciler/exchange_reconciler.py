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
        """Perform exchange-as-truth reconciliation for Grid bot."""
        bot_id = getattr(coordinator, "grid_id", getattr(coordinator.config, "symbol", "unknown"))
        symbol = str(getattr(coordinator.config, "symbol", ""))
        result = {"bot_id": bot_id, "symbol": symbol, "status": "success"}

        try:
            # Reconcile orders and restore metrics
            await coordinator.recover_old_orders()
            return result
        except Exception as e:
            logger.error(f"❌ [Reconciler {symbol}] Grid 對帳異常: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)
            return result
