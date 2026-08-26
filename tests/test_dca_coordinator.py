"""
DcaCoordinator 狀態機測試（Phase 2 稽核 2.1–2.21）

全程 FakeExchange 驅動，不接真實交易所（允許手工觸發 Close/Stop 由 stop() 模擬）。
每個測試對應計畫 §6 稽核標準的一項斷言。
"""

import asyncio
import time
import unittest
from decimal import Decimal as D

from core.services.dca.coordinator import DcaCoordinator, EntryMonitor
from core.services.dca.entry.evaluator import EntryVerdict
from core.services.dca.execution import DcaOrderExecutor
from core.services.dca.ladder import (
    quantize_price, quantize_quantity, take_profit_price,
)
from core.services.dca.models.dca_config import DcaConfig, EntryConfig, EntryMode
from core.services.dca.models.dca_state import DcaStatus, SlotStatus
from tests.services.dca.fake_exchange import FakeExchange

BOT_ID = "bot-phase2-test"
SYMBOL = "ETH/USDC:USDC"
E0 = D("2500")
BASE_QTY = D("20") / E0   # 0.008（precision 6 下不縮水）


class StubEntryMonitor:
    """可程式化的入場監控 stub：逐方向控制條件是否成立。"""

    def __init__(self):
        self.matched = {"long": False, "short": False}
        self.calls = []

    async def evaluate(self, direction):
        self.calls.append(direction)
        ok = self.matched.get(direction, False)
        return EntryVerdict(
            should_enter=ok, mode=EntryMode.INDICATOR, direction=direction,
            consecutive_true_bars=1 if ok else 0, confirm_bars_needed=1,
        )


def make_config(**kw) -> DcaConfig:
    base = dict(
        exchange="binance", symbol=SYMBOL,
        base_order_amount=D("20"), direction="long",
        max_safety_orders=3, price_deviation_percent=D("0.8"),
        price_deviation_multiplier=D("1.2"), order_size_multiplier=D("1.0"),
        active_safety_orders_window=2, take_profit_percent=D("1.0"),
        repeat_after_take_profit=False, cooldown_seconds=0,
        quantity_precision=6, price_decimals=2,
        monitoring_interval=5,
    )
    base.update(kw)
    return DcaConfig(**base)


def build_coordinator(config, exchange, entry_monitor=None, min_notional=D("0"),
                      circuit_probe=None, time_fn=None):
    executor = DcaOrderExecutor(
        config, BOT_ID, exchange,
        min_notional=min_notional, circuit_probe=circuit_probe,
    )
    published = []
    kw = {}
    if time_fn is not None:
        kw["time_fn"] = time_fn
    coord = DcaCoordinator(
        config, BOT_ID, executor,
        entry_monitor=entry_monitor,
        lifecycle_publisher=published.append,
        **kw,
    )
    return coord, executor, published


async def drive_until(coord, target: DcaStatus, max_steps: int = 30) -> DcaStatus:
    """逐步推進直到進入目標狀態（上限保護防無窮迴圈）。"""
    steps = 0
    while coord.state.status is not target and steps < max_steps:
        await coord.step()
        steps += 1
    return coord.state.status


def safety_creates(fake: FakeExchange) -> list:
    return [c for c in fake.calls if c[0] == "create" and "_so" in str(c[1])]


def tp_creates(fake: FakeExchange) -> list:
    return [c for c in fake.calls if c[0] == "create" and str(c[1]).endswith("_tp")]


def open_with_suffix(fake: FakeExchange, suffix: str) -> list:
    return [r for r in fake.open_order_records() if r.client_id.endswith(suffix)]


def event_names(coord) -> set:
    return {name for name, _ in coord.events}


class ArmedInvariantTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.1：armed 期間交易所掛單數必為 0（INV-1）。"""

    async def test_inv1_armed_never_places_orders(self):
        # 全實管線：真實 EntryMonitor + 真實指標（PriceChange 門檻 -99% 絕不成立）
        from core.services.dca.indicators.config import PriceChangeConfig
        config = make_config(entry=EntryConfig(
            mode=EntryMode.INDICATOR,
            indicators=(PriceChangeConfig(lookback_bars=24,
                                          long_threshold_percent=-99.0,
                                          short_threshold_percent=99.0),),
        ))

        async def flat_closes(symbol, timeframe, limit):
            return [100.0] * limit

        monitor = EntryMonitor(config, flat_closes)
        coord, ex, _ = build_coordinator(config, FakeExchange(E0), monitor)
        coord.begin()
        for _ in range(20):
            await coord.step()
            self.assertIs(coord.state.status, DcaStatus.ARMED)
            self.assertEqual(coord.build_statistics().active_safety_orders, 0)
        fake: FakeExchange = ex._exchange  # 執行器持有的正是測試注入的 fake
        self.assertEqual(fake.create_call_count(), 0)
        self.assertEqual(fake.open_order_records(), [])


class TriggerFlowTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.2 / 2.3：觸發後才下基礎單；成交後掛窗口 + 恰好 1 張 TP。"""

    async def test_trigger_places_exactly_one_base_next_round(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, ex, _ = build_coordinator(config, fake)
        coord.begin()
        await coord.step()  # armed → triggering（不得下有單）
        self.assertIs(coord.state.status, DcaStatus.TRIGGERING)
        self.assertEqual(fake.create_call_count(), 0)

        await coord.step()  # 下基礎單：本輪恰好 1 次 create
        self.assertEqual(fake.create_call_count(), 1)
        call = fake.calls[0]
        self.assertTrue(str(call[1]).endswith("_long_base"))
        self.assertEqual(call[4], quantize_quantity(BASE_QTY, 6))  # 0.008
        self.assertEqual(call[5], D("2500.00"))

        status = await drive_until(coord, DcaStatus.AVERAGING)
        self.assertIs(status, DcaStatus.AVERAGING)
        self.assertEqual(coord.state.entry_price, E0)

    async def test_default_window_plus_one_tp(self):
        # 預設窗口語意：min(window=5, N=20) + 1 TP = 6 張掛單
        config = make_config(max_safety_orders=20, active_safety_orders_window=5)
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING), DcaStatus.AVERAGING)

        open_orders = fake.open_order_records()
        self.assertEqual(len(open_orders), 6)
        self.assertEqual(len(open_with_suffix(fake, "_tp")), 1)
        safety_suffixes = sorted(r.client_id.rsplit("_", 1)[1]
                                 for r in open_orders if "_so" in r.client_id)
        self.assertEqual(safety_suffixes, ["so01", "so02", "so03", "so04", "so05"])
        stats = coord.build_statistics(last_price=E0)
        self.assertEqual(stats.take_profit_orders, 1)
        self.assertEqual(stats.active_safety_orders, 5)


class WindowReplenishTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.3b / 2.3c：活躍窗口遞補與急跌穿透重對齊。"""

    async def _enter_averaging(self, config, fake):
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        return coord

    async def test_window_replenishes_after_each_fill(self):
        config = make_config(max_safety_orders=20, active_safety_orders_window=5)
        fake = FakeExchange(E0)
        coord = await self._enter_averaging(config, fake)
        slots = {s.index: s for s in coord.state.safety_slots}

        for filled_count in (1, 2, 3):
            # 掛價 = quantize 後值；市價 ≤ 掛價才成交（恰好填第 i 層）
            fake.set_last_price(quantize_price(slots[filled_count].planned_price, 2))
            await coord.step()
            self.assertEqual(coord.state.filled_safety_count(), filled_count)
            self.assertEqual(len(open_with_suffix(fake, "_tp")), 1)
            active = [s for s in coord.state.safety_slots
                      if s.status is SlotStatus.ACTIVE]
            self.assertEqual(len(active), 5)
            self.assertEqual(coord.build_statistics().active_safety_orders, 5)
        # 前 3 張已成交、遞補到 so08（5 原始 + 3 遞補）
        self.assertEqual(len(safety_creates(fake)), 8)
        self.assertEqual(slots[1].status, SlotStatus.FILLED)

    async def test_flash_crash_realigned_below_market(self):
        config = make_config(max_safety_orders=20, active_safety_orders_window=5)
        fake = FakeExchange(E0)
        coord = await self._enter_averaging(config, fake)

        fake.set_last_price("1000")   # 單棒穿透 ≥5 層
        await coord.step()

        self.assertIn("window_realigned", event_names(coord))
        self.assertEqual(coord.state.filled_safety_count(), 5)
        active = [s for s in coord.state.safety_slots
                  if s.status is SlotStatus.ACTIVE]
        self.assertEqual(len(active), 5)
        for slot in active:  # 全部重對齊到現價下方
            self.assertLess(slot.actual_price, D("1000"))
        # 配額語意：跳過層仍計入 → 累計下單安全單次數 = 10，不超發
        self.assertEqual(len(safety_creates(fake)), 10)
        unique_slots = {str(c[1]) for c in safety_creates(fake)}
        self.assertEqual(len(unique_slots), 10)


class TakeProfitInvariantTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.4 / 2.5 / 2.6：TP 唯一性、原子重掛、失敗降級。"""

    async def _enter_averaging(self, config, fake):
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        return coord, published

    async def test_inv2_single_tp_at_formula_price(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _ = await self._enter_averaging(config, fake)
        tp_records = open_with_suffix(fake, "_tp")
        self.assertEqual(len(tp_records), 1)
        expected = quantize_price(take_profit_price(E0, D("1.0"), "long"), 2)
        self.assertEqual(tp_records[0].price, expected)  # 2525.00
        self.assertEqual(coord.build_statistics().take_profit_orders, 1)

    async def test_tp_replaced_atomically_after_safety_fill(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _ = await self._enter_averaging(config, fake)
        old_tp_id = open_with_suffix(fake, "_tp")[0].id

        fake.set_last_price("2470")   # 填 so01
        await coord.step()

        create_tp_idx = next(
            i for i, c in enumerate(fake.calls)
            if c[0] == "create" and str(c[1]).endswith("_tp")
            and i > 1  # 首張 TP 之後
        )
        cancel_idx = next(i for i, c in enumerate(fake.calls)
                          if c[0] == "cancel" and c[1] == old_tp_id)
        self.assertLess(cancel_idx, create_tp_idx)   # 嚴格 cancel-then-place

        new_tp = open_with_suffix(fake, "_tp")
        self.assertEqual(len(new_tp), 1)
        expected = quantize_price(
            take_profit_price(coord.state.average_cost, D("1.0"), "long"), 2
        )
        self.assertEqual(new_tp[0].price, expected)

    async def test_tp_place_failure_degrades_then_recovers(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, published = await self._enter_averaging(config, fake)

        fake.fail_next_creates = 1      # 下一次 create（TP 重掛）失敗；cancel 成功
        fake.set_last_price("2470")
        await coord.step()

        self.assertIs(coord.state.status, DcaStatus.DEGRADED)
        self.assertIn(DcaStatus.DEGRADED.value, published)
        self.assertNotIn("running", published)   # degraded 期間不得回報正常
        self.assertEqual(open_with_suffix(fake, "_tp"), [])   # 確實無 TP

        await coord.step()  # 下一輪自動重試
        self.assertIs(coord.state.status, DcaStatus.AVERAGING)
        self.assertEqual(len(open_with_suffix(fake, "_tp")), 1)
        expected = quantize_price(
            take_profit_price(coord.state.average_cost, D("1.0"), "long"), 2
        )
        self.assertEqual(open_with_suffix(fake, "_tp")[0].price, expected)


class QuotaAndCapTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.7 / 2.8 / 2.15：不超發安全單、曝險上限、最小名目略過。"""

    async def test_inv3_never_places_more_than_n_safeties(self):
        config = make_config(max_safety_orders=3, active_safety_orders_window=2)
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)

        p1 = coord.state.safety_slots[0].planned_price
        p3 = coord.state.safety_slots[2].planned_price
        fake.set_last_price(p1)                    # 填 so01 → 遞補 so03
        await coord.step()
        fake.set_last_price(p3)                    # 填 so02、so03 → 階梯用盡
        await coord.step()
        for _ in range(3):                          # 再多壓也絕不發第 4 張
            fake.set_last_price(D("100"))
            await coord.step()
        self.assertEqual(len(safety_creates(fake)), 3)
        distinct = {str(c[1]) for c in safety_creates(fake)}
        self.assertEqual(len(distinct), 3)

    async def test_position_cap_stops_averaging_with_event(self):
        config = make_config(max_position_notional=D("45"),
                             max_safety_orders=3, active_safety_orders_window=2)
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        # 基礎 20 + so01 20 = 40 ≤ 45 → 放行；下一張 60 > 45 → 阻擋
        self.assertEqual(len(safety_creates(fake)), 1)
        self.assertIn("position_cap_reached", event_names(coord))

        fake.set_last_price("2470")
        for _ in range(3):
            await coord.step()
        self.assertEqual(len(safety_creates(fake)), 1)   # 依然不超發
        cap_events = [e for e in coord.events if e[0] == "position_cap_reached"]
        self.assertEqual(len(cap_events), 1)             # 事件只記一次

    async def test_safety_below_min_notional_skipped_not_sent(self):
        config = make_config(max_safety_orders=3, active_safety_orders_window=2)
        fake = FakeExchange(E0)
        coord, executor, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        baseline = len(safety_creates(fake))

        # 白箱模擬：交易所調高最小名目（後續安全單 notional 20 < 25 一律被拒）
        executor._min_notional = D("25")
        fake.set_last_price("2470")
        await coord.step()

        self.assertEqual(len(safety_creates(fake)), baseline)   # 未送交易所
        self.assertIn("safety_below_min_notional", event_names(coord))
        skipped = [s for s in coord.state.safety_slots
                   if s.status is SlotStatus.SKIPPED]
        self.assertGreaterEqual(len(skipped), 1)


class RoundCompletionTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.9 / 2.10 / 2.11：TP 成交收輪、循環語意、max_cycles。"""

    async def test_tp_fill_completes_round_and_clears_all_orders(self):
        config = make_config(repeat_after_take_profit=False)
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)

        fake.set_last_price("2530")   # 填 TP 2525
        self.assertIs(await drive_until(coord, DcaStatus.STOPPED), DcaStatus.STOPPED)
        self.assertEqual(fake.open_order_records(), [])     # 殘單全撤
        self.assertEqual(published[-1], DcaStatus.STOPPED.value)
        self.assertGreater(coord.build_statistics().realized_profit, D("0"))

    async def test_repeat_cycle_cooling_then_rearmed_direction_free(self):
        config = make_config(repeat_after_take_profit=True, cooldown_seconds=0)
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        fake.set_last_price("2530")
        self.assertIs(await drive_until(coord, DcaStatus.ARMED), DcaStatus.ARMED)

        self.assertIn(DcaStatus.COOLING.value, published)
        self.assertIsNone(coord.state.active_direction)     # 回到雙向同時評估
        self.assertEqual(coord.state.cycle_count, 1)
        self.assertEqual(fake.open_order_records(), [])     # 無殘留掛單

    async def test_repeat_disabled_stops_after_completed(self):
        config = make_config(repeat_after_take_profit=False)
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        fake.set_last_price("2530")
        self.assertIs(await drive_until(coord, DcaStatus.STOPPED), DcaStatus.STOPPED)
        self.assertNotIn(DcaStatus.COOLING.value, published)

    async def test_max_cycles_stops_after_nth_round(self):
        config = make_config(repeat_after_take_profit=True, cooldown_seconds=0,
                             max_cycles=2)
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.begin()

        for round_no in (1, 2):
            self.assertIs(await drive_until(coord, DcaStatus.AVERAGING, 60),
                          DcaStatus.AVERAGING)
            fake.set_last_price("2530")          # 止盈出輪
            fake.set_last_price(E0)              # 回到原點供下一輪（成交已於 fake 落實）
            await coord.step()                   # 偵測 TP 成交 → completed
            self.assertIs(coord.state.status, DcaStatus.COMPLETED)
            if round_no == 1:
                self.assertIs(await drive_until(coord, DcaStatus.COOLING, 10),
                              DcaStatus.COOLING)
                self.assertIs(await drive_until(coord, DcaStatus.ARMED, 10),
                              DcaStatus.ARMED)   # 冷卻 0 → 立刻回到待命
            else:
                await coord.step()               # max_cycles 達成 → stopped

        self.assertIs(coord.state.status, DcaStatus.STOPPED)
        self.assertEqual(coord.state.cycle_count, 2)
        self.assertIn(DcaStatus.COOLING.value, published)  # 第一輪有冷卻


class StopSemanticsTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.12 / 2.13 / 2.19：停止撤單留倉、冪等、task 無洩漏。"""

    async def test_stop_cancels_orders_keeps_position_publishes_stopped(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        position_before = coord.state.position_quantity

        await coord.stop()
        self.assertIs(coord.state.status, DcaStatus.STOPPED)
        self.assertEqual(fake.open_order_records(), [])
        self.assertGreaterEqual(fake.cancel_call_count(), 3)   # TP + 2 安全單
        self.assertEqual(coord.state.position_quantity, position_before)
        market_creates = [c for c in fake.calls if c[0] == "create" and c[3] == "market"]
        self.assertEqual(market_creates, [])                    # 撤單不平倉
        self.assertEqual(published[-1], DcaStatus.STOPPED.value)

    async def test_stop_is_idempotent(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        await coord.stop()
        cancels_after_first = fake.cancel_call_count()
        await coord.stop()
        await coord.stop()
        self.assertEqual(fake.cancel_call_count(), cancels_after_first)
        self.assertIs(coord.state.status, DcaStatus.STOPPED)

    async def test_start_tasks_are_joined_on_stop(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(config, fake)
        coord.start()
        self.assertEqual(published[0], DcaStatus.ARMED.value)
        self.assertEqual(len(coord._tasks), 1)
        self.assertTrue(any(not t.done() for t in coord._tasks))

        await asyncio.sleep(0)   # 讓迴圈起步即可（立即停）
        await coord.stop()
        self.assertTrue(all(t.done() for t in coord._tasks))
        live = [t for t in coord._tasks if not t.done()]
        self.assertEqual(live, [])
        self.assertEqual(coord._tasks, set())                  # 協調器持有 task 數歸 0


class CircuitGuardTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.14：熔斷中 triggering 延後而非失敗；關閉後自動續行。"""

    async def test_triggering_defers_under_circuit_then_proceeds(self):
        flag = [True]
        config = make_config()
        fake = FakeExchange(E0)
        coord, _, published = build_coordinator(
            config, fake, circuit_probe=lambda: flag[0])
        coord.begin()
        await coord.step()                # armed → triggering（armed 不下單）
        self.assertIs(coord.state.status, DcaStatus.TRIGGERING)

        for _ in range(3):                # 熔斷持續：不下單、不失敗
            await coord.step()
            self.assertIs(coord.state.status, DcaStatus.TRIGGERING)
        self.assertEqual(fake.create_call_count(), 0)
        self.assertNotIn(DcaStatus.FAILED.value, published)

        flag[0] = False                   # 熔斷關閉 → 自動續行
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        self.assertGreaterEqual(fake.create_call_count(), 1)


class FillDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.16：同一 order_id 由 WS 與 REST 各回報一次，業務邏輯只執行一次。"""

    async def test_ws_and_rest_duplicate_fill_applied_once(self):
        config = make_config()
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake)
        coord.begin()
        await coord.step()                # → triggering
        await coord.step()                # 下基礎單（fake 立即在市價成交）
        base_cid = fake.calls[0][1]

        # 手動模擬 WS 推送（交易所真實推送由 WsFillListener 於 Phase 3 接上）
        self.assertTrue(await coord.handle_ws_fill(base_cid, E0, BASE_QTY))
        self.assertFalse(await coord.handle_ws_fill(base_cid, E0, BASE_QTY))
        self.assertEqual(coord.state.position_quantity, BASE_QTY)   # 只加一次

        # REST 對帳同一 order_id → 去重：部位不得再增加
        await coord.step()
        self.assertEqual(coord.state.position_quantity, BASE_QTY)
        self.assertEqual(coord.state.average_cost, E0)


class DirectionLockEngineTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.17 / 2.17b：both 單向互斥、tiebreak 事件、跨輪換向。"""

    async def test_both_directions_matched_locks_by_tiebreak(self):
        config = make_config(direction="both", both_tiebreak="long_first")
        stub = StubEntryMonitor()
        stub.matched = {"long": True, "short": True}
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake, stub)
        coord.begin()
        await coord.step()

        self.assertIs(coord.state.status, DcaStatus.TRIGGERING)
        self.assertEqual(coord.state.active_direction, "long")
        self.assertIn("both_directions_matched", event_names(coord))
        tiebreak_events = [p for n, p in coord.events
                           if n == "both_directions_matched"]
        self.assertEqual(tiebreak_events[0]["tiebreak"], "long_first")

        # 鎖定期間（雙向仍同時成立）所有掛單都屬 long leg，無反向單
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        for _ in range(3):
            await coord.step()
        legs = {str(c[1]).split("_")[2] for c in fake.calls if c[0] == "create"}
        self.assertEqual(legs, {"long"})

    async def test_both_can_reverse_direction_next_round(self):
        config = make_config(direction="both", repeat_after_take_profit=True,
                             cooldown_seconds=0)
        stub = StubEntryMonitor()
        stub.matched = {"long": True, "short": False}
        fake = FakeExchange(E0)
        coord, _, _ = build_coordinator(config, fake, stub)
        coord.begin()

        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING),
                      DcaStatus.AVERAGING)
        self.assertEqual(coord.state.active_direction, "long")
        fake.set_last_price("2530")                       # 多單止盈收輪
        self.assertIs(await drive_until(coord, DcaStatus.ARMED), DcaStatus.ARMED)
        self.assertIsNone(coord.state.active_direction)
        self.assertEqual(fake.open_order_records(), [])   # 無殘留掛單（稽核 2.9）

        # 第二輪：僅 short 條件成立 → 換向進場
        fake.set_last_price("2500")
        stub.matched = {"long": False, "short": True}
        self.assertIs(await drive_until(coord, DcaStatus.AVERAGING, 60),
                      DcaStatus.AVERAGING)
        self.assertEqual(coord.state.active_direction, "short")
        round2_creates = [c for _, c, _, _, _, _ in
                          [(c[0], c[1], c[2], c[3], c[4], c[5])
                           for c in fake.calls if c[0] == "create"
                           and str(c[1]).split("_")[2] == "short"]]
        self.assertGreaterEqual(len(round2_creates), 1)
        self.assertTrue(any(str(c[1]).endswith("_short_base")
                            for c in fake.calls if c[0] == "create"))


class FailureTransitionTests(unittest.IsolatedAsyncioTestCase):
    """稽核 2.18（部分）：triggering 下單失敗 → failed，且 lifecycle 發布。"""

    async def test_base_order_error_transitions_to_failed(self):
        config = make_config()
        fake = FakeExchange(E0)
        fake.fail_next_creates = 1
        coord, _, published = build_coordinator(config, fake)
        coord.begin()
        await coord.step()
        self.assertIs(coord.state.status, DcaStatus.TRIGGERING)
        await coord.step()
        self.assertIs(coord.state.status, DcaStatus.FAILED)
        self.assertIn(DcaStatus.FAILED.value, published)
        self.assertIsNotNone(coord.state.last_error)


class StaticConstraintTests(unittest.TestCase):
    """稽核 2.20 / 2.21：事件迴圈不得阻塞；禁止使用 print()。"""

    def _iter_dca_sources(self):
        import core.services.dca as pkg
        from pathlib import Path
        root = Path(pkg.__path__[0])
        yield from sorted(root.rglob("*.py"))

    def test_no_blocking_sleep_in_dca_package(self):
        offenders = []
        for path in self._iter_dca_sources():
            src = path.read_text(encoding="utf-8")
            if "time.sleep" in src.replace("time.sleep_async(secs)", ""):
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_no_print_calls_in_dca_package(self):
        offenders = []
        for path in self._iter_dca_sources():
            src = path.read_text(encoding="utf-8")
            if "print(" in src:
                offenders.append(str(path))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
