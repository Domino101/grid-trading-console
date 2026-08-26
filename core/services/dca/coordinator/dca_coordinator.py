"""
DcaCoordinator —— DCA 主狀態機驅動器（計畫 §6 Phase 2 核心交付）

主迴圈語意（armed → triggering → averaging → completed 循環）：
- ARMED：零交易所掛單（INV-1）；逐輪評估入場條件（locked DirectionLock）
- TRIGGERING：下 1 張基礎單；熔斷中延後而非失敗（稽核 2.14）；
  成交後建階梯 → 掛 TP（先）+ 活躍窗口安全單（後）
- AVERAGING：成交漏斗（WS 優先 + REST 對帳，order_id 去重）→
  安全單成交 ⇒ 原子重掛 TP（cancel-then-place）+ 窗口遞補／急跌重對齊；
  TP 成交 ⇒ 撤殘單 → COMPLETED
- DEGRADED：TP cancel 成功但 place 失敗 → 持續重試，期間絕不回報正常（稽核 2.6）
- COMPLETED → COOLING（release 鎖定）→ ARMED（循環）；或 max_cycles / repeat=false → STOPPED

工程約束：
- 本類全部非同步；內部只使用 asyncio.sleep（稽核 2.20：禁用同步阻塞 sleep）
- start() 建立的背景 task 全部註冊 self._tasks，stop() 逐一 cancel + await（稽核 2.19）
- stop() 撤單不平倉、冪等（稽核 2.12/2.13）；pause()/resume() 必須 async def（網格教訓）
"""

import asyncio
import time
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..interfaces.dca_executor import IDcaOrderExecutor, OutcomeKind
from ..ladder import build_ladder, stop_loss_price
from ..models.dca_config import DcaConfig
from ..models.dca_order import DcaOrder, DcaOrderRole, DcaOrderStatus
from ..models.dca_state import DcaState, DcaStatus, SlotStatus
from ..models.dca_statistics import DcaStatistics
from .direction_lock import DirectionLock
from .entry_monitor import EntryMonitor, OhlcvProvider
from .take_profit_manager import TakeProfitManager, TpReplaceError
from core.logging import get_logger

logger = get_logger(__name__)

LifecyclePublisher = Callable[[str], None]
EventHandler = Callable[[str, dict], None]


class DcaCoordinator:
    """一台 DCA bot 的生命週期協調器。"""

    def __init__(
        self,
        config: DcaConfig,
        bot_id: str,
        executor: IDcaOrderExecutor,
        entry_monitor: Optional[EntryMonitor] = None,
        ohlcv_provider: Optional[OhlcvProvider] = None,
        lifecycle_publisher: Optional[LifecyclePublisher] = None,
        event_handler: Optional[EventHandler] = None,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._bot_id = bot_id
        self._executor = executor
        self._time_fn = time_fn
        self._publish: LifecyclePublisher = lifecycle_publisher or (lambda status: None)
        self.events: List[Tuple[str, dict]] = []
        self._event_handler = event_handler

        self._entry_monitor = entry_monitor or EntryMonitor(config, ohlcv_provider)
        self._lock = DirectionLock(config.direction, config.both_tiebreak,
                                   event_handler=self._emit)

        self.state = DcaState(bot_id=bot_id)

        # ── 執行期追蹤 ──
        self._active: Dict[str, DcaOrder] = {}          # client_order_id → 在世掛單
        self._processed_fill_ids: Set[str] = set()      # 成交去重（WS + REST 雙路）
        self._base_order: Optional[DcaOrder] = None
        self._tp_manager: Optional[TakeProfitManager] = None
        self._tp_client_id: Optional[str] = None

        # ── 步內旗標（成交漏斗 → 狀態步驟的交接）──
        self._flag_base_filled = False
        self._flag_safety_filled = False
        self._flag_round_finished = False
        self._pending_tp_replace = False
        self._cap_event_emitted = False

        self._cooldown_until: Optional[float] = None
        self._realized_profit = Decimal("0")
        self._total_fees = Decimal("0")

        # ── 成交歷史（Phase 7 詳情工作台「成交」tab；累積跨輪，不隨 round reset 清空）──
        self._fills: List[dict] = []

        # ── task 生命週期 ──
        self._tasks: Set[asyncio.Task] = set()
        self._running = False
        self._paused = False
        self._stop_started = False

    # ══════════════════ 公用 API ══════════════════

    def begin(self) -> None:
        """DRAFT → ARMED（不建背景迴圈；供逐步測試或外部驅動）。"""
        if self.state.status is DcaStatus.DRAFT:
            self._transition(DcaStatus.ARMED)

    def start(self) -> None:
        """開始監控：DRAFT → ARMED 並建立背景監控迴圈（task 註冊至 self._tasks）。"""
        self.begin()
        if self._running:
            return
        self._running = True
        task = asyncio.create_task(
            self._monitor_loop(), name=f"dca-monitor-{self._bot_id[:12]}"
        )
        self._tasks.add(task)
        logger.info(
            "DCA 協調器已啟動",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "coordinator_start"},
        )

    async def pause(self) -> None:
        """暫停輪詢（必須為 async def —— 網格早期把控制面寫成同步工具的教訓）。"""
        self._paused = True
        logger.info("DCA 已暫停", extra={"bot_id": self._bot_id, "action": "pause"})

    async def resume(self) -> None:
        self._paused = False
        logger.info("DCA 已恢復", extra={"bot_id": self._bot_id, "action": "resume"})

    async def stop(self) -> None:
        """
        停止語意（§B 結束按鈕）：取消全部掛單、**不平倉**、轉 stopped。
        冪等：第二次起直接返回，cancel 不重複（稽核 2.13）。
        """
        if self._stop_started:
            return
        self._stop_started = True
        self._running = False

        # 1) 停掉本協調器擁有的背景 task（逐一 cancel 並 await，防熱插拔洩漏）
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        for task in list(self._tasks):
            if task is current:
                continue
            with suppress(asyncio.CancelledError):
                await task
        self._tasks = {t for t in self._tasks if t is current}

        # 2) 撤單（保留倉位）
        await self._cancel_all_tracked()

        # 3) 狀態收尾與 lifecycle 發布
        status = self.state.status
        if status is DcaStatus.DRAFT:
            self.state.status = DcaStatus.STOPPED
            self._publish(DcaStatus.STOPPED.value)
        elif status not in (DcaStatus.STOPPED, DcaStatus.FAILED, DcaStatus.STOPPING):
            if self.state.can_transition_to(DcaStatus.STOPPING):
                self._transition(DcaStatus.STOPPING)
            elif self.state.status is not DcaStatus.STOPPING:
                # COMPLETED 等無 STOPPING 出口：直接合法轉 STOPPED
                self._transition(DcaStatus.STOPPED)
                return
            if self.state.status is DcaStatus.STOPPING:
                self._transition(DcaStatus.STOPPED)
        elif status is DcaStatus.STOPPING:
            self._transition(DcaStatus.STOPPED)

        logger.info(
            "DCA 已停止（撤單完成，倉位保留）",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "stop", "result": "stopped",
                   "position_quantity": str(self.state.position_quantity)},
        )

    async def handle_ws_fill(self, client_order_id: str,
                             price, quantity) -> bool:
        """
        WS 成交推送入口（Phase 3 由 WsFillListener 呼叫；Phase 2 測試由
        FakeExchange 模擬）。與 REST 對帳共用 _on_order_filled 漏斗，
        同一 order_id 業務邏輯只執行一次（稽核 2.16）。
        """
        return await self._fill_funnel(client_order_id, price, quantity)

    async def step(self) -> None:
        """推進一輪狀態機（測試與監控迴圈共用）。"""
        status = self.state.status
        if status is DcaStatus.ARMED:
            await self._step_armed()
        elif status is DcaStatus.TRIGGERING:
            await self._step_triggering()
        elif status is DcaStatus.AVERAGING:
            await self._step_averaging()
        elif status is DcaStatus.DEGRADED:
            await self._step_degraded()
        elif status is DcaStatus.COMPLETED:
            await self._step_completed()
        elif status is DcaStatus.COOLING:
            await self._step_cooling()

    # ══════════════════ 監控迴圈 ══════════════════

    async def _monitor_loop(self) -> None:
        interval = max(int(self._config.monitoring_interval), 1)
        # 終止條件用 while self._running（網格教訓：不得用 while not self._paused ——
        # 暫停或競態時會讓 task 悄悄死亡/失控）
        while self._running and not self._stop_started:
            if self._paused:
                await asyncio.sleep(interval)
                continue
            try:
                await self.step()
            except Exception as exc:  # R-1：單機器人例外只能讓自己死亡
                self._fail(exc)
            await asyncio.sleep(interval)

    # ══════════════════ 各狀態步驟 ══════════════════

    async def _step_armed(self) -> None:
        candidates = self._lock.candidate_directions()
        matched = {}
        for direction in candidates:
            try:
                verdict = await self._entry_monitor.evaluate(direction)
            except Exception as exc:
                self._fail(exc)
                return
            matched[direction] = verdict.should_enter
        chosen = self._lock.acquire(
            long_matched=matched.get("long", False),
            short_matched=matched.get("short", False),
        )
        if chosen is None:
            return  # 條件不成立：留在 armed，交易所零掛單（INV-1）
        self.state.active_direction = chosen
        logger.info(
            "入場條件成立，鎖定方向",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "trigger", "result": chosen},
        )
        self._transition(DcaStatus.TRIGGERING)

    async def _step_triggering(self) -> None:
        direction = self.state.active_direction
        if self._base_order is None:
            entry_price = await self._executor.last_price()
            v0 = self._config.base_order_amount
            if self._config.amount_unit == "base":
                v0 = v0 * entry_price
            qty = v0 / entry_price
            side = "buy" if direction == "long" else "sell"
            outcome = await self._executor.place_limit(
                DcaOrderRole.BASE, side, entry_price, qty, leg=direction,
            )
            if outcome.kind is OutcomeKind.DEFERRED_CIRCUIT_OPEN:
                logger.warning(
                    "熔斷中，triggering 延後（不下單、不失敗）",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "place_base", "result": "deferred_circuit_open"},
                )
                return  # 稽核 2.14：留在 triggering 待下輪
            if outcome.kind is not OutcomeKind.PLACED:
                self._fail(RuntimeError(f"基礎單下單失敗: {outcome.reason}"))
                return
            self._base_order = outcome.order
            self._active[outcome.order.client_order_id] = outcome.order
            return

        await self._reconcile()
        if self._flag_base_filled:
            self._flag_base_filled = False
            await self._enter_averaging()

    async def _step_averaging(self) -> None:
        await self._reconcile()
        if self._flag_round_finished:
            await self._finish_round()
            return

        # 安全單成交 → 原子重掛 TP（cancel-then-place）；失敗 → degraded
        if (self._flag_safety_filled or self._pending_tp_replace) \
                and self.state.position_quantity > 0:
            self._flag_safety_filled = False
            await self._attempt_tp_replace()
            if self.state.status is DcaStatus.DEGRADED:
                return

        await self._check_stop_loss()
        if self._flag_round_finished:
            await self._finish_round()
            return

        await self._replenish_window()

    async def _step_degraded(self) -> None:
        await self._reconcile()
        if self._flag_round_finished:
            await self._finish_round()
            return
        # TP 重掛持續重試（稽核 2.6：不可靜默留在無 TP 狀態）
        await self._attempt_tp_replace()

    async def _step_completed(self) -> None:
        max_cycles = self._config.max_cycles
        done_for_good = (not self._config.repeat_after_take_profit) or (
            max_cycles is not None and self.state.cycle_count >= max_cycles
        )
        if done_for_good:
            self._transition(DcaStatus.STOPPED)
            return
        self._cooldown_until = self._time_fn() + self._config.cooldown_seconds
        self._transition(DcaStatus.COOLING)

    async def _step_cooling(self) -> None:
        if self._cooldown_until is None or self._time_fn() >= self._cooldown_until:
            self._reset_round_state()
            self._transition(DcaStatus.ARMED)

    # ══════════════════ 基礎單成交 → 進入攤平 ══════════════════

    async def _enter_averaging(self) -> None:
        direction = self.state.active_direction
        entry_price = self.state.entry_price
        plan = build_ladder(self._config, entry_price, direction)
        self.state.init_slots(plan.levels)
        self.state.base_order_filled_at = datetime.now(timezone.utc)

        # TP 先行，倉位即刻受 INV-2 保護
        self._tp_manager = TakeProfitManager(self._executor, direction)
        try:
            tp = await self._tp_manager.place(
                self.state.average_cost, self._config.take_profit_percent,
                self.state.position_quantity,
            )
            self._tp_client_id = tp.client_order_id
            self._active[tp.client_order_id] = tp
        except TpReplaceError:
            self._pending_tp_replace = True
            logger.warning(
                "首次 TP 掛單失敗，進入 averaging 後轉 degraded 重試",
                extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                       "action": "place_tp", "result": "error"},
            )

        self._transition(DcaStatus.AVERAGING)
        await self._replenish_window()

    # ══════════════════ 窗口遞補 / 急跌重對齊 / 曝險上限 ══════════════════

    def _active_safety_count(self) -> int:
        return sum(1 for slot in self.state.safety_slots
                   if slot.status is SlotStatus.ACTIVE)

    async def _replenish_window(self) -> None:
        window = self._config.active_safety_orders_window
        direction = self.state.active_direction
        side = "buy" if direction == "long" else "sell"

        while self._active_safety_count() < window:
            slot = next((s for s in self.state.safety_slots
                         if s.status is SlotStatus.WAITING), None)
            if slot is None:
                return  # INV-3：階梯槽位用盡即停（不多發第 N+1 張）

            # —— 曝險上限（INV-4 / 稽核 2.8）：觸及即停止再攤平，槽位保留 WAITING ——
            # 口徑：已成交成本 + 已掛在交易所的安全單名目 + 本張計畫名目
            cap = self._config.max_position_notional
            planned_notional = slot.planned_price * slot.planned_quantity
            open_notional = sum(
                (s.actual_price or s.planned_price) * s.planned_quantity
                for s in self.state.safety_slots
                if s.status is SlotStatus.ACTIVE
            )
            projected = self.state.total_cost + open_notional + planned_notional
            if cap is not None and projected > cap:
                if not self._cap_event_emitted:
                    self._cap_event_emitted = True
                    self._emit("position_cap_reached", {
                        "total_cost": str(self.state.total_cost),
                        "open_notional": str(open_notional),
                        "planned_notional": str(planned_notional),
                        "max_position_notional": str(cap),
                    })
                logger.warning(
                    "曝險上限觸及，停止再攤平",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "safety_blocked", "result": "position_cap_reached"},
                )
                return

            # —— 急跌穿透窗口 → 以現價重新對齊（稽核 2.3c）——
            mark = await self._executor.last_price()
            price_above_market = (
                (direction == "long" and slot.planned_price > mark)
                or (direction == "short" and slot.planned_price < mark)
            )
            if price_above_market:
                await self._realign_remaining_slots(mark)
                continue  # 槽位計畫價已更新，重取下一個 WAITING 槽

            outcome = await self._executor.place_limit(
                DcaOrderRole.SAFETY, side,
                slot.planned_price, slot.planned_quantity,
                slot_index=slot.index, leg=direction,
            )
            if outcome.kind is OutcomeKind.PLACED:
                slot.mark_active(outcome.order.price)
                self._active[outcome.order.client_order_id] = outcome.order
            elif outcome.kind is OutcomeKind.REJECTED_MIN_NOTIONAL:
                slot.mark_skipped()
                self._emit("safety_below_min_notional", {
                    "slot_index": slot.index,
                    "reason": outcome.reason or "",
                })
                logger.warning(
                    "安全單低於最小名目被略過（未送交易所）",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "place_safety", "result": "rejected_min_notional",
                           "slot_index": slot.index},
                )
                continue
            elif outcome.kind is OutcomeKind.DEFERRED_CIRCUIT_OPEN:
                return  # 熔斷中：下輪再補
            else:
                self._fail(RuntimeError(f"安全單下單失敗: {outcome.reason}"))
                return

    async def _realign_remaining_slots(self, mark_price: Decimal) -> None:
        """
        急跌穿透：把剩餘 WAITING 槽位的計畫價／量以現價為錨重新計算。

        配額語意（稽核 2.3c）：已成交／已掛上的槽位維持原狀並**依然計入**
        max_safety_orders 配額；剩餘槽位數 = N − 已消耗，重新配價後逐槽遞補，
        總掛單次數絕不超過 N（INV-3）。
        """
        waiting = [s for s in self.state.safety_slots
                   if s.status is SlotStatus.WAITING]
        if not waiting:
            return
        sub_config = replace(self._config, max_safety_orders=len(waiting))
        plan = build_ladder(sub_config, mark_price, self.state.active_direction)
        sub_levels = {level.index: level for level in plan.levels if level.index >= 1}
        for offset, slot in enumerate(waiting, start=1):
            level = sub_levels[offset]
            slot.planned_price = level.price
            slot.planned_quantity = level.quantity
        self._emit("window_realigned", {
            "anchor_price": str(mark_price),
            "realigned_slots": len(waiting),
            "active_direction": self.state.active_direction,
        })
        logger.warning(
            "急跌穿透窗口，以現價重新對齊剩餘安全單",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "window_realigned",
                   "anchor_price": str(mark_price),
                   "realigned_slots": len(waiting)},
        )

    # ══════════════════ TP 原子重掛 ══════════════════

    async def _attempt_tp_replace(self) -> None:
        avg = self.state.average_cost
        if avg is None or self.state.position_quantity <= 0:
            return
        old_id = self._tp_client_id
        try:
            new_tp = await self._tp_manager.replace(
                avg, self._config.take_profit_percent,
                self.state.position_quantity,
            )
        except TpReplaceError as exc:
            self._pending_tp_replace = True
            if exc.tp_missing:
                # 舊 TP 已撤、新 TP 未掛 → 無 TP 中：轉 degraded 持續重試
                if old_id:
                    self._active.pop(old_id, None)
                self._tp_client_id = None
                logger.warning(
                    "TP 重掛失敗且當前無 TP，轉 degraded 待重試",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "tp_replace", "result": "degraded",
                           "error": str(exc)},
                )
                if self.state.status is not DcaStatus.DEGRADED:
                    self._transition(DcaStatus.DEGRADED)
            else:
                logger.warning(
                    "TP 重掛失敗，舊 TP 仍在保護中，下輪重試",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "tp_replace", "result": "retry_next_cycle"},
                )
            return
        if old_id and old_id != new_tp.client_order_id:
            self._active.pop(old_id, None)
        self._tp_client_id = new_tp.client_order_id
        self._active[new_tp.client_order_id] = new_tp
        self._pending_tp_replace = False
        # 稽核 7.3：安全單成交後 TP 隨新均價重掛，事件時間線記錄 tp_replaced
        self._emit("tp_replaced", {
            "avg_price": str(avg),
            "tp_price": str(new_tp.price),
            "position_quantity": str(self.state.position_quantity),
            "filled_safety_orders": self.state.filled_safety_count(),
        })
        if self.state.status is DcaStatus.DEGRADED:
            self._transition(DcaStatus.AVERAGING)

    # ══════════════════ 止損監控（價格觸發 → 市價平倉） ══════════════════

    async def _check_stop_loss(self) -> None:
        sl_percent = self._config.stop_loss_percent
        if sl_percent is None or self.state.position_quantity <= 0:
            return
        direction = self.state.active_direction
        sl_price = stop_loss_price(
            self.state.average_cost, sl_percent, direction
        )
        mark = await self._executor.last_price()
        breached = (direction == "long" and mark <= sl_price) or (
            direction == "short" and mark >= sl_price
        )
        if not breached:
            return
        self._emit("stop_loss_triggered", {
            "mark_price": str(mark), "stop_price": str(sl_price),
        })
        logger.warning(
            "止損觸發，市價平倉",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "stop_loss", "result": str(mark)},
        )
        await self._cancel_all_tracked()
        close_side = "sell" if direction == "long" else "buy"
        outcome = await self._executor.place_market(
            DcaOrderRole.STOP_LOSS, close_side,
            self.state.position_quantity, leg=direction,
        )
        if outcome.kind is OutcomeKind.PLACED:
            self._realize_profit(mark, self.state.position_quantity,
                                 DcaOrderRole.STOP_LOSS)
            # 市價平倉完成：引擎持倉同步歸零（§4.6 completed 持倉 0）
            self.state.position_quantity = Decimal("0")
            self._flag_round_finished = True
        else:
            self._fail(RuntimeError(f"止損平倉失敗: {outcome.reason}"))

    # ══════════════════ 成交漏斗（WS + REST 雙路去重） ══════════════════

    async def _fill_funnel(self, client_order_id: str, price, quantity) -> bool:
        """統一成交入口；order_id（此處以 client_order_id 為鍵）去重。"""
        if client_order_id in self._processed_fill_ids:
            logger.info(
                "成交事件重複，已忽略（去重）",
                extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                       "action": "fill_duplicate",
                       "client_order_id": client_order_id},
            )
            return False
        order = self._active.get(client_order_id)
        if order is None:
            return False  # 不屬於本輪追蹤（例如已處理或外部單）
        self._processed_fill_ids.add(client_order_id)
        fill_price = Decimal(str(price))
        fill_qty = Decimal(str(quantity))
        order.mark_filled(fill_price, fill_qty)
        self._active.pop(client_order_id, None)
        self._apply_fill_effects(order, fill_price, fill_qty)
        return True

    def _apply_fill_effects(self, order: DcaOrder,
                            price: Decimal, qty: Decimal) -> None:
        role = order.role
        # 成交歷史紀錄（Phase 7）：含手續費與成交後累積均價，供詳情「成交」tab
        fee = price * qty * self._config.fee_rate
        self._fills.append({
            "client_order_id": order.client_order_id,
            "role": role.value,
            "side": order.side,
            "slot_index": order.slot_index,
            "price": float(price),
            "quantity": float(qty),
            "notional": float(price * qty),
            "fee": float(fee),
            "occurred_at": datetime.now(timezone.utc).timestamp(),
            # 成交套用後的累積均價（TP 平倉後倉位歸零 → None）
            "cumulative_avg_price": None,
        })
        if role is DcaOrderRole.TAKE_PROFIT:
            self._realize_profit(price, qty, role)
            # TP 全數平倉：交易所端持倉已歸零，引擎狀態同步為 0（§4.6 completed 持倉 0）
            self.state.position_quantity = Decimal("0")
            self._flag_round_finished = True
            return
        # 倉位與成本一律以正數量累計；損益符號於實現/未實現損益計算時依方向處理
        self.state.position_quantity += qty
        self.state.total_cost += price * qty
        self._total_fees += price * qty * self._config.fee_rate
        if role is DcaOrderRole.BASE:
            self.state.entry_price = price
            self._flag_base_filled = True
        elif role is DcaOrderRole.SAFETY:
            for slot in self.state.safety_slots:
                if slot.index == order.slot_index and slot.status is SlotStatus.ACTIVE:
                    slot.mark_filled(price, qty)
                    break
            self._flag_safety_filled = True
        # 回填此筆成交後的累積均價（供詳情「成交」tab 顯示均價變化）
        avg = self.state.average_cost
        if self._fills:
            self._fills[-1]["cumulative_avg_price"] = float(avg) if avg is not None else None

    def _realize_profit(self, price: Decimal, qty: Decimal,
                        role: DcaOrderRole) -> None:
        avg = self.state.average_cost or price
        spread = (price - avg) * qty
        if self.state.active_direction == "short":
            spread = -spread
        self._realized_profit += spread
        self._total_fees += price * qty * self._config.fee_rate
        logger.info(
            "本輪實現盈虧入帳",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "realized_pnl", "role": role.value,
                   "result": str(spread)},
        )

    async def _reconcile(self) -> None:
        """REST 對帳（backup 路徑）：把交易所已成交但 WS 未推送的單補進漏斗。"""
        try:
            open_orders = await self._executor.fetch_open_orders()
        except Exception as exc:
            logger.warning(
                "open orders 對帳失敗（下輪再試）",
                extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                       "action": "reconcile", "result": "error", "error": str(exc)},
            )
            return
        open_ids = {o.client_order_id for o in open_orders}
        for client_id in list(self._active.keys()):
            if client_id in open_ids:
                continue
            latest = await self._executor.fetch_order(client_id)
            if latest is not None and latest.status is DcaOrderStatus.FILLED:
                price = latest.average_fill_price or latest.price
                qty = latest.filled_quantity or latest.quantity
                await self._fill_funnel(client_id, price, qty)
            elif latest is None or latest.status in (
                DcaOrderStatus.CANCELLED, DcaOrderStatus.REJECTED
            ):
                # 外部撤單/拒單：移出追蹤（TP 被外部撤單屬 P0 風險 → degraded 重掛）
                logger.warning(
                    "追蹤中的訂單在交易所消失，移出追蹤",
                    extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                           "action": "reconcile_missing",
                           "client_order_id": client_id},
                )
                order = self._active.pop(client_id)
                if order.role is DcaOrderRole.TAKE_PROFIT \
                        and self.state.status in (DcaStatus.AVERAGING, DcaStatus.DEGRADED):
                    self._tp_client_id = None
                    self._pending_tp_replace = True

    # ══════════════════ 收輪 / 停止 ══════════════════

    async def _finish_round(self) -> None:
        self._flag_round_finished = False
        await self._cancel_all_tracked()   # 撤殘餘安全單（稽核 2.9：掛單數歸 0）
        self._transition(DcaStatus.COMPLETED)

    async def _cancel_all_tracked(self) -> None:
        for client_id, order in list(self._active.items()):
            if order.is_active_on_exchange():
                await self._executor.cancel(order)
            self._active.pop(client_id, None)

    def _reset_round_state(self) -> None:
        """COOLING → ARMED 前的倫位/階梯重置（稽核 2.17b：無殘留掛單、可換向）。"""
        self._active.clear()
        self._processed_fill_ids.clear()
        self._base_order = None
        self._tp_manager = None
        self._tp_client_id = None
        self._flag_base_filled = False
        self._flag_safety_filled = False
        self._flag_round_finished = False
        self._pending_tp_replace = False
        self._cap_event_emitted = False
        self._cooldown_until = None
        self.state.entry_price = None
        self.state.position_quantity = Decimal("0")
        self.state.total_cost = Decimal("0")
        self.state.safety_slots = []
        self._lock.release()

    # ══════════════════ 工具 ══════════════════

    def _transition(self, target: DcaStatus) -> None:
        self.state.transition_to(target)
        self.state.updated_at = datetime.now(timezone.utc)
        self._publish(target.value)

    def _emit(self, name: str, payload: dict) -> None:
        payload = {
            "bot_id": self._bot_id, "symbol": self._config.symbol, **payload,
        }
        self.events.append((name, payload))
        if self._event_handler is not None:
            self._event_handler(name, payload)

    def _fail(self, exc: Exception) -> None:
        """任何未捕捉的例外只能讓自己死亡（R-1），絕不向上蔓延。"""
        logger.error(
            "DCA 發生未預期錯誤，轉 failed",
            extra={"symbol": self._config.symbol, "bot_id": self._bot_id,
                   "action": "fatal", "result": "failed", "error": str(exc)},
        )
        self.state.last_error = str(exc)
        try:
            self._transition(DcaStatus.FAILED)
        except Exception:
            self.state.status = DcaStatus.FAILED
            self._publish(DcaStatus.FAILED.value)

    # ══════════════════ 統計（INV 稽核計數口徑） ══════════════════

    def _next_safety_price(self, state: DcaState) -> Optional[Decimal]:
        """
        下一張將成交的安全單價（Phase 5 卡片「下一張安全單價格」）。

        - long：取未成交槽位中**最高**價（最接近現價、最先成交）
        - short：取未成交槽位中**最低**價
        - 無鎖定方向或無未成交槽位 → None（不得回傳 0，G-5）
        """
        if state.active_direction not in ("long", "short"):
            return None
        pending = [
            (s.actual_price or s.planned_price)
            for s in state.safety_slots
            if s.status in (SlotStatus.ACTIVE, SlotStatus.WAITING)
        ]
        if not pending:
            return None
        return max(pending) if state.active_direction == "long" else min(pending)

    def build_statistics(self, last_price: Optional[Decimal] = None) -> DcaStatistics:
        state = self.state
        active_tp = sum(
            1 for o in self._active.values()
            if o.role is DcaOrderRole.TAKE_PROFIT and o.is_active_on_exchange()
        )
        stats = DcaStatistics(
            bot_id=self._bot_id,
            symbol=self._config.symbol,
            status=state.status.value,
            active_direction=state.active_direction,
            current_price=last_price,
            position_quantity=state.position_quantity,
            average_cost=state.average_cost,
            total_cost=state.total_cost,
            entry_price=state.entry_price,
            next_safety_order_price=self._next_safety_price(state),
            max_safety_orders=self._config.max_safety_orders,
            filled_safety_orders=state.filled_safety_count(),
            active_safety_orders=self._active_safety_count(),
            take_profit_orders=active_tp,
            cycle_count=state.cycle_count,
            realized_profit=self._realized_profit,
            total_fees=self._total_fees,
        )
        if last_price is not None and state.position_quantity > 0 and state.average_cost:
            spread = (last_price - state.average_cost) * state.position_quantity
            if state.active_direction == "short":
                spread = -spread
            stats.unrealized_profit = spread
        return stats

    # ══════════════════ 詳情工作台快照（Phase 7）══════════════════

    def open_orders_snapshot(self) -> List[dict]:
        """當前在世掛單的顯示層快照（詳情「訂單」tab）。

        僅回傳交易所端仍活躍（PLACED/PARTIAL）的追蹤單，含 role 標記。
        """
        rows: List[dict] = []
        for order in self._active.values():
            if not order.is_active_on_exchange():
                continue
            rows.append({
                "client_order_id": order.client_order_id,
                "order_id": order.order_id,
                "role": order.role.value,
                "side": order.side,
                "price": float(order.price),
                "quantity": float(order.quantity),
                "notional": float(order.price * order.quantity),
                "slot_index": order.slot_index,
                "status": order.status.value,
            })
        rows.sort(key=lambda r: (r["role"], r.get("slot_index") or 0))
        return rows

    def fills_snapshot(self) -> List[dict]:
        """歷史成交的顯示層快照（詳情「成交」tab）；累積跨輪。"""
        return list(self._fills)

    def ladder_slots_snapshot(self) -> List[dict]:
        """當前虛擬階梯槽位狀態（詳情「階梯」tab）。

        index 0 為基礎單，其後為安全單槽位；狀態三態：已成交/掛單中/未觸及。
        """
        rows: List[dict] = []
        base_filled = self._base_order is not None and self._base_order.is_filled()
        if self._base_order is not None:
            rows.append({
                "index": 0,
                "role": "base",
                "status": "filled" if base_filled else "active",
                "planned_price": float(self._base_order.price),
                "planned_quantity": float(self._base_order.quantity),
                "filled_price": float(self._base_order.average_fill_price)
                if self._base_order.average_fill_price is not None else None,
                "filled_quantity": float(self._base_order.filled_quantity)
                if self._base_order.filled_quantity else None,
            })
        for slot in self.state.safety_slots:
            if slot.status is SlotStatus.FILLED:
                status = "filled"
            elif slot.status is SlotStatus.ACTIVE:
                status = "active"
            elif slot.status is SlotStatus.SKIPPED:
                status = "skipped"
            else:
                status = "waiting"
            rows.append({
                "index": slot.index,
                "role": "safety",
                "status": status,
                "planned_price": float(slot.planned_price),
                "planned_quantity": float(slot.planned_quantity),
                "actual_price": float(slot.actual_price)
                if slot.actual_price is not None else None,
                "filled_price": float(slot.filled_price)
                if slot.filled_price is not None else None,
                "filled_quantity": float(slot.filled_quantity)
                if slot.filled_quantity else None,
            })
        return rows
