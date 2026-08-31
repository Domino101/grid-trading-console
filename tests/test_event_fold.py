"""P3-1: event fold — replay consistency property tests.

Gate 準則：「事件重放與直寫投影一致（屬性測試通過）」。
屬性 1：純 fill 序列 → fold(events) == rehydrate(fills)（兩條事實路徑等價）。
屬性 2：含 reset 檢查點的隨機序列 → fold == 直寫模擬（同一語意逐步套用）。
"""

import os
import random
import tempfile
import time
import unittest

from core.infrastructure.database.event_fold import (
    build_timeline,
    events_from_fills,
    fold_timeline,
    rebuild_position,
)
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotEvent, BotRecord, TradeFill


def _fill(bot_id, i, side, price, qty, fee=0.0, pnl=0.0, role="grid", ts=None):
    return BotEvent(
        bot_id=bot_id,
        seq=0,
        event_type="fill",
        payload={
            "fill_id": f"f-{i}",
            "client_order_id": f"c-{i}",
            "side": side,
            "role": role,
            "price": price,
            "quantity": qty,
            "fee": fee,
            "realized_pnl": pnl,
            "executed_at": ts or (1756600000.0 + i),
        },
        created_at=ts or (1756600000.0 + i),
        dedupe_key=f"fill:f-{i}",
    )


class TestFoldEqualsRehydrate(unittest.TestCase):
    """屬性 1：fold 與既有 rehydrate_position_from_fills 在 fill 序列上等價。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = LedgerRepository(os.path.join(self.tmp.name, "fold.sqlite3"))

    def test_random_fill_sequences_fold_equals_rehydrate(self):
        rng = random.Random(20260831)
        for trial in range(50):
            bot_id = f"prop-{trial}"
            self.repo.register_or_update_bot(BotRecord(
                bot_id=bot_id, strategy_type="grid", symbol="X/USDC", exchange="binance",
                account_mode="paper", status="running", created_at=time.time(),
            ))
            fills = []
            for i in range(rng.randint(1, 60)):
                fill = TradeFill(
                    fill_id=f"{bot_id}-f-{i}", bot_id=bot_id, client_order_id=f"c-{i}",
                    side=rng.choice(["buy", "sell"]), role=rng.choice(["grid", "grid", "tp"]),
                    price=round(rng.uniform(50, 150), 4), quantity=round(rng.uniform(0.01, 2), 4),
                    fee=round(rng.uniform(0, 0.5), 4), realized_pnl=round(rng.uniform(-2, 2), 4),
                    executed_at=1756600000.0 + i,
                )
                self.repo.record_fill(fill)
                fills.append(fill)
            timeline = build_timeline(self.repo.store, bot_id)
            folded = fold_timeline(bot_id, timeline)
            rehydrated = self.repo.store.rehydrate_position_from_fills(bot_id)
            for field in ("quantity", "completed_cycles", "realized_profit", "total_fees"):
                self.assertAlmostEqual(
                    float(getattr(folded, field)), float(getattr(rehydrated, field)),
                    places=6, msg=f"trial={trial} field={field}",
                )


class TestFoldWithResets(unittest.TestCase):
    """屬性 2：含 reset 檢查點 → fold 與直寫模擬一致。"""

    def _direct_write_sim(self, timeline):
        """直寫模擬：與 fold 相同語意的命令式版本（獨立實作以互相驗證）。"""
        position = 0.0
        cost = 0.0
        realized = 0.0
        fees = 0.0
        cycles = 0
        for e in timeline:
            if e.event_type == "fill":
                p = e.payload
                fees += float(p.get("fee") or 0)
                realized += float(p.get("realized_pnl") or 0)
                if p.get("role") == "tp":
                    cycles += 1
                q = float(p.get("quantity") or 0)
                price = float(p.get("price") or 0)
                if p.get("side") == "buy":
                    position += q
                    cost += price * q
                else:
                    position -= q
                    cost -= price * q
            elif e.event_type in ("reset", "take_profit", "stop_loss"):
                position = 0.0
                cost = 0.0
                cp = e.payload.get("checkpoint") or {}
                cycles = int(cp.get("cycles", 0) or 0)
                realized = float(cp.get("realized", 0) or 0)
                fees = float(cp.get("fees", 0) or 0)
        return position, realized, fees, cycles

    def test_random_lifecycle_sequences_fold_matches_direct_write(self):
        rng = random.Random(31)
        for trial in range(50):
            bot_id = f"life-{trial}"
            timeline = []
            i = 0
            for _ in range(rng.randint(1, 30)):
                action = rng.random()
                if action < 0.65:
                    timeline.append(_fill(bot_id, i, rng.choice(["buy", "sell"]),
                                          round(rng.uniform(1, 200), 3), round(rng.uniform(0.01, 1), 3),
                                          fee=round(rng.uniform(0, 0.2), 4),
                                          pnl=round(rng.uniform(-1, 1), 4),
                                          role=rng.choice(["grid", "tp"]), ts=1756600000.0 + i))
                elif action < 0.9:
                    timeline.append(BotEvent(
                        bot_id=bot_id, seq=0, event_type="state_transition",
                        payload={"status": "running"}, created_at=1756600000.0 + i,
                    ))
                else:
                    cp = {"cycles": rng.randint(0, 3), "realized": round(rng.uniform(0, 5), 3),
                          "fees": round(rng.uniform(0, 1), 3)}
                    timeline.append(BotEvent(
                        bot_id=bot_id, seq=0,
                        event_type=rng.choice(["reset", "take_profit", "stop_loss"]),
                        payload={"reset_type": "止盈", "checkpoint": cp},
                        created_at=1756600000.0 + i,
                    ))
                i += 1
            timeline.sort(key=lambda e: e.created_at)
            folded = fold_timeline(bot_id, timeline)
            pos, realized, fees, cycles = self._direct_write_sim(timeline)
            self.assertAlmostEqual(folded.quantity, max(0.0, pos), places=6, msg=f"trial={trial}")
            self.assertAlmostEqual(folded.realized_profit, realized, places=6, msg=f"trial={trial}")
            self.assertAlmostEqual(folded.total_fees, fees, places=6, msg=f"trial={trial}")
            self.assertEqual(folded.completed_cycles, cycles, msg=f"trial={trial}")

    def test_legacy_fallback_merges_fills_without_fill_events(self):
        """舊資料（只有 trade_fills、無 fill 事件）→ timeline 以 fills 合成。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = LedgerRepository(os.path.join(tmp.name, "legacy.sqlite3"))
        repo.register_or_update_bot(BotRecord(
            bot_id="legacy-1", strategy_type="grid", symbol="X/USDC", exchange="binance",
            account_mode="paper", status="running", created_at=time.time(),
        ))
        for i in range(5):
            repo.record_fill(TradeFill(
                fill_id=f"lg-{i}", bot_id="legacy-1", client_order_id=f"c-{i}",
                side="buy" if i % 2 == 0 else "sell", role="grid",
                price=100 + i, quantity=0.5, fee=0.01, realized_pnl=1.0 if i % 2 else 0.0,
                executed_at=1756600000.0 + i,
            ))
        rebuilt = rebuild_position(repo.store, "legacy-1")
        rehydrated = repo.store.rehydrate_position_from_fills("legacy-1")
        self.assertAlmostEqual(rebuilt.quantity, rehydrated.quantity, places=7)
        self.assertAlmostEqual(rebuilt.realized_profit, rehydrated.realized_profit, places=7)


if __name__ == "__main__":
    unittest.main()
