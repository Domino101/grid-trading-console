"""P4-1 統一記帳層整合測試。

驗證 grid / DCA / CoinFull Prime 三個策略是否真的共用 **同一套資料庫記帳**：
* 唯一寫入路徑（StrategyLedger → LedgerRepository → create_ledger_store）
* 冪等性（fill_id / dedupe_key / 籃子 upsert）
* 籃子均價法已實現損益（CoinFull Prime 的損益可由 DB 獨立重算）
* 檔案型帳本降為鏡像 + 一次性冪等回填
* 記帳失敗為「可觀測降級」而非靜默失敗
* 操作稽核日誌 (grid_audit.db) 自我修復

全部離線：以合成資料與假適配器驅動，不觸網、不寫入 repo/data。
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import (
    BasketEntry,
    BotRecord,
    RegimeDecisionRecord,
    TradeFill,
)
from core.infrastructure.database.strategy_ledger import StrategyLedger, contract_for


def _rank_item(symbol: str, side: str, score: float, rank: int, notional: float = 250.0):
    from core.services.coinfull_prime.models import RankedItem, SignalZScores

    return RankedItem(
        symbol=symbol,
        score=score,
        rank=rank,
        signals=SignalZScores(),
        side=side,
        target_notional=notional,
    )


def _rank_result(long_symbols, short_symbols, notional: float = 250.0):
    from core.services.coinfull_prime.models import UniverseRankResult

    return UniverseRankResult(
        universe_size=len(long_symbols) + len(short_symbols),
        long_list=[_rank_item(s, "long", 2.0 - i * 0.1, i + 1, notional) for i, s in enumerate(long_symbols)],
        short_list=[_rank_item(s, "short", -2.0 + i * 0.1, i + len(long_symbols) + 1, notional) for i, s in enumerate(short_symbols)],
        all_rankings=[],
    )


class _FakeOrderResult:
    def __init__(self, order_id: str, price: float, qty: float):
        self.id = order_id
        self.average = price
        self.price = price
        self.filled = qty
        self.client_id = f"cfp_client_{order_id}"
        self.fee = {"cost": 0.1, "currency": "USDT"}


class _FakeAdapter:
    """最小交易所適配器：name 含 'paper' 以通過合約市場預檢（免觸網）。"""

    def __init__(self, positions=None, prices=None):
        self.name = "paper_fake"
        self.exchange_name = "paper_fake"
        self._positions = positions or []
        self._prices = prices or {}
        self.sent = []

    async def get_positions(self):
        return self._positions

    async def get_ticker(self, symbol):
        price = self._prices.get(symbol)
        return SimpleNamespace(last_price=price) if price else None

    async def create_order(self, symbol, side, order_type, amount, params=None):
        self.sent.append({"symbol": symbol, "side": str(side), "amount": float(amount), "params": params or {}})
        order_id = f"oid{len(self.sent)}"
        return _FakeOrderResult(order_id, self._prices.get(symbol, 100.0), float(amount))


class UnifiedLedgerTestCase(unittest.TestCase):
    """提供「每個測試一套獨立 canonical ledger」的隔離基底。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.root / "ledger.sqlite3")

        self.repo = LedgerRepository(self.db_path)
        self.ledger = StrategyLedger(repository=self.repo)
        # 讓全域 StrategyLedger.instance()（策略程式碼使用的路徑）解析到本測試的 repo
        StrategyLedger.reset_instance()
        LedgerRepository._instance = self.repo
        self.addCleanup(self._reset_singletons)
        self.addCleanup(self.tmp.cleanup)

    def _reset_singletons(self):
        LedgerRepository.reset_instance()
        StrategyLedger.reset_instance()

    def make_service(self):
        from core.services.coinfull_prime.service import CoinFullPrimeService

        # C-6 之後 load_config 對缺失檔案 fail-closed；測試提供最小合法配置。
        config_path = self.root / "strategy_config.yaml"
        if not config_path.exists():
            config_path.write_text(
                "portfolio:\n"
                "  total_capital_usdt: 1000.0\n"
                "  leverage: 1.0\n"
                "  dry_run: true\n",
                encoding="utf-8",
            )
        service = CoinFullPrimeService(config_path=str(config_path))
        # 鏡像檔一律寫進測試 data 目錄，避免污染 repo/data
        service._get_data_dir = lambda: self.data_dir  # type: ignore[method-assign]
        service._get_scheduler_state_file = lambda: self.data_dir / "coinfull_prime_scheduler.json"  # type: ignore[method-assign]
        service.config = {"portfolio": {"total_capital_usdt": 1000.0, "leverage": 1.0, "dry_run": True}}
        from core.services.coinfull_prime.rebalancer import PortfolioRebalancer

        service.rebalancer = PortfolioRebalancer(service.config)
        return service


class TestUnifiedLedgerFacade(UnifiedLedgerTestCase):
    """統一記帳門面本身的契約。"""

    def test_backend_is_selected_by_factory_not_hardcoded(self):
        from core.infrastructure.database.ledger_store import SqliteLedgerStore

        self.assertEqual(self.ledger.backend, "sqlite")
        self.assertIsInstance(self.ledger.store, SqliteLedgerStore)
        self.assertEqual(os.path.abspath(self.ledger.store.database_path), os.path.abspath(self.db_path))

    def test_record_fill_writes_canonical_fill_event(self):
        self.ledger.ensure_bot("bot-a", "grid", "BTC/USDT", "binance", account_mode="paper")
        fill = TradeFill("f1", "bot-a", "c1", "buy", "grid", 100.0, 1.0, time.time())
        self.assertTrue(self.ledger.record_fill(fill))

        self.assertEqual(len(self.ledger.get_fills("bot-a")), 1)
        events = self.ledger.get_events("bot-a")
        fill_events = [e for e in events if e.event_type == "fill"]
        self.assertEqual(len(fill_events), 1)
        self.assertEqual(fill_events[0].dedupe_key, "fill:f1")

    def test_fill_event_lands_in_injected_store_not_global_singleton(self):
        """P4-1 修復：fill 事件必須寫進「注入的 store」，而非全域單例解析出的另一個 DB。

        舊行為：facade 呼叫 ``bot_events.emit_fill_event()``，該 helper 解析全域
        ``LedgerRepository.instance()``。當 facade 被注入自訂 store（稽核 CLI ``--db``、
        RuntimeSnapshotRepository 自訂路徑、測試隔離）時，事件會落到另一個資料庫，
        形成 ``trade_fills`` 有資料、``bot_events`` 缺事件的 L5 事件溯源缺口。
        """
        decoy_repo = LedgerRepository(str(self.root / "decoy.sqlite3"))
        LedgerRepository._instance = decoy_repo  # 全域單例指向另一個 DB

        isolated = StrategyLedger(repository=LedgerRepository(self.db_path))
        isolated.ensure_bot("bot-iso", "coinfull_prime", "BTCUSDT", "binance", account_mode="paper")
        self.assertTrue(
            isolated.record_fill(TradeFill("f-iso", "bot-iso", "c-iso", "buy", "cfp_long", 100.0, 1.0, time.time()))
        )

        own_events = [e for e in isolated.get_events("bot-iso") if e.event_type == "fill"]
        self.assertEqual(len(own_events), 1, "fill 事件必須與 trade_fills 落在同一個 DB")
        self.assertEqual(own_events[0].dedupe_key, "fill:f-iso")

        decoy = StrategyLedger(repository=decoy_repo)
        self.assertEqual(
            [e for e in decoy.get_events("bot-iso") if e.event_type == "fill"],
            [],
            "事件不得外洩到全域單例解析出的另一個 DB",
        )
        self.assertFalse(isolated.health()["degraded"], msg=str(isolated.health()))

    def test_record_fill_is_idempotent_by_fill_id(self):
        self.ledger.ensure_bot("bot-a", "dca", "ETH/USDT", "binance", account_mode="paper")
        fill = TradeFill("f-dup", "bot-a", "c1", "buy", "base", 100.0, 1.0, time.time())
        self.ledger.record_fill(fill)
        self.ledger.record_fill(fill)
        self.assertEqual(len(self.ledger.get_fills("bot-a")), 1)

    def test_accounting_failures_are_observable_not_silent(self):
        class _Boom:
            def record_fill(self, _fill):
                raise RuntimeError("disk full")

        broken = StrategyLedger(repository=SimpleNamespace(store=None, record_fill=_Boom().record_fill,
                                                           register_or_update_bot=lambda *_a, **_k: None))
        fill = TradeFill("f-x", "bot-x", "c", "buy", "grid", 1.0, 1.0, time.time())
        # 不拋出例外（交易優先），但必須可觀測
        broken.record_fill(fill)
        health = broken.health()
        self.assertTrue(health["degraded"])
        self.assertEqual(health["writes_failed"], 1)
        self.assertIn("disk full", health["last_error"])

    def test_basket_average_cost_realized_pnl(self):
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.assertEqual(self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0), 0.0)
        self.assertAlmostEqual(self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "sell", 110.0, 1.0, reduce_only=True), 10.0)
        self.assertAlmostEqual(self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "sell", 90.0, 1.0, reduce_only=True), -10.0)
        entries = self.ledger.load_basket(bot, account_id="paper", active_only=False)
        closed = [e for e in entries if e.symbol == "BTCUSDT"]
        self.assertEqual(closed[0].status, "closed")
        self.assertAlmostEqual(closed[0].realized_pnl, 0.0)  # +10 -10 = 0（淨損益可由 DB 重算）

    def test_basket_short_side_pnl_sign(self):
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "ETHUSDT", "short", 200.0, 1.0)
        pnl = self.ledger.apply_basket_fill(bot, "paper", "ETHUSDT", "buy", 180.0, 1.0, reduce_only=True)
        self.assertAlmostEqual(pnl, 20.0)  # 做空跌價 → 獲利

    def test_basket_membership_sync_preserves_settled_pnl(self):
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0)
        # 名單同步（quantity=0）不得洗掉既有持倉數量與均價
        self.ledger.save_basket(
            bot, "paper",
            [BasketEntry(bot, "paper", "BTCUSDT", "long", 0.0, None), BasketEntry(bot, "paper", "ETHUSDT", "short", 1.0, 50.0)],
            close_missing=True,
        )
        entries = {e.symbol: e for e in self.ledger.load_basket(bot, account_id="paper")}
        self.assertAlmostEqual(entries["BTCUSDT"].quantity, 2.0)
        self.assertAlmostEqual(entries["BTCUSDT"].entry_price, 100.0)
        self.assertEqual(entries["ETHUSDT"].side, "short")

    def test_runtime_state_and_regime_idempotency(self):
        self.ledger.set_state("coinfull_prime:scheduler", "scheduler", {"account_id": "paper", "dry_run": True})
        self.assertEqual(self.ledger.get_state("coinfull_prime:scheduler", "scheduler")["account_id"], "paper")

        key = "regime:paper:2026-09-16T00:00:00+00:00"
        first = self.ledger.record_regime_decision(
            RegimeDecisionRecord("coinfull_prime:paper", time.time(), pts=2, dedupe_key=key)
        )
        second = self.ledger.record_regime_decision(
            RegimeDecisionRecord("coinfull_prime:paper", time.time(), pts=0, dedupe_key=key)
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger.load_regime_decisions(bot_id="coinfull_prime:paper")), 1)

    def test_strategy_contract_is_strategy_aware(self):
        self.assertEqual(contract_for("coinfull_prime")["basket_store"], "bot_baskets")
        self.assertIsNone(contract_for("coinfull_prime")["position_store"])
        self.assertEqual(contract_for("grid")["position_store"], "bot_positions")
        self.assertEqual(contract_for("dca")["position_store"], "bot_positions")

    def test_facade_accepts_raw_ledger_store_injection(self):
        """P4-1 修復：注入 raw LedgerStore（稽核 CLI ``--db`` 路徑）不得讓記帳靜默失敗。

        舊行為：``StrategyLedger.store`` 直接取 ``repo.store``，注入
        ``SqliteLedgerStore`` 時拋 ``AttributeError`` 並被寫入守門吞掉
        → 籃子/事件全部寫不進去，卻只在 log 留下一行 ERROR。
        """
        from core.infrastructure.database.ledger_store import SqliteLedgerStore

        ledger = StrategyLedger(repository=SqliteLedgerStore(database_path=str(self.root / "raw.sqlite3")))
        self.assertEqual(ledger.backend, "sqlite")
        self.assertTrue(
            ledger.ensure_bot("bot-raw", "coinfull_prime", "BTCUSDT", "binance", account_mode="paper")
        )
        touched = ledger.save_basket(
            "bot-raw",
            "paper",
            [BasketEntry(bot_id="bot-raw", account_id="paper", symbol="BTCUSDT", side="long", quantity=1.0, entry_price=100.0)],
        )
        self.assertEqual(touched, 1)
        self.assertEqual(len(ledger.load_basket("bot-raw", "paper")), 1)
        self.assertFalse(ledger.health()["degraded"], msg=str(ledger.health()))

    def test_save_basket_coerces_legacy_dict_entries(self):
        """P4-1 修復：檔案型帳本的 dict（含 ``avg_price`` / ``qty`` 舊鍵）必須被正規化。

        舊行為：dict 直送 store → ``AttributeError: 'dict' object has no attribute
        'symbol'`` → 記帳靜默失敗（回填/鏡像路徑最常見）。
        """
        bot = "coinfull_prime:paper"
        touched = self.ledger.save_basket(
            bot,
            "paper",
            [
                {"symbol": "ETHUSDT", "side": "short", "qty": 3.0, "avg_price": 50.0, "status": "active"},
                {"symbol": "", "side": "long"},  # 無標的 → 忽略，不得中斷整批
            ],
        )
        self.assertEqual(touched, 1)
        rows = self.ledger.load_basket(bot, "paper")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].symbol, "ETHUSDT")
        self.assertEqual(rows[0].side, "short")
        self.assertAlmostEqual(rows[0].quantity, 3.0)
        self.assertAlmostEqual(rows[0].entry_price or 0.0, 50.0)
        self.assertFalse(self.ledger.health()["degraded"], msg=str(self.ledger.health()))


class TestCoinFullPrimeUnifiedAccounting(UnifiedLedgerTestCase):
    """CoinFull Prime 記帳孤島是否已收斂至 canonical ledger。"""

    def _rebalancer(self):
        from core.services.coinfull_prime.rebalancer import PortfolioRebalancer

        return PortfolioRebalancer({"portfolio": {"total_capital_usdt": 1000.0, "leverage": 1.0, "dry_run": True}})

    def test_dry_run_records_fills_and_idempotent_event(self):
        rebalancer = self._rebalancer()
        prices = {"BTCUSDT": 100.0, "ETHUSDT": 50.0, "SOLUSDT": 20.0, "DOGEUSDT": 0.1}
        rank = _rank_result(["BTCUSDT", "ETHUSDT"], ["SOLUSDT", "DOGEUSDT"])

        async def run():
            return await rebalancer.execute_rebalance(
                rank_result=rank,
                exchange_adapter=None,
                dry_run=True,
                prices_override=prices,
                account_id="paper",
                batch_id="20260916T000000Z",
            )

        report = asyncio.run(run())
        self.assertEqual(report.status, "completed_dry_run")
        bot = "coinfull_prime:paper"
        fills = self.ledger.get_fills(bot, limit=100)
        self.assertEqual(len(fills), 4)
        self.assertTrue(all(f.role.startswith("simulated_") for f in fills))
        events = [e for e in self.ledger.get_events(bot) if e.event_type == "daily_rebalance"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].dedupe_key, "rebalance:paper:20260916T000000Z:dry_run")
        self.assertEqual(events[0].payload["batch_id"], "20260916T000000Z")
        self.assertEqual(report.batch_id, "20260916T000000Z")

        # 同批重跑 → 冪等（fills 不重複、事件不重複）
        asyncio.run(run())
        self.assertEqual(len(self.ledger.get_fills(bot, limit=100)), 4)
        self.assertEqual(len([e for e in self.ledger.get_events(bot) if e.event_type == "daily_rebalance"]), 1)

    def test_dry_run_does_not_mutate_basket(self):
        rebalancer = self._rebalancer()
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0)
        before = {(e.symbol, e.quantity) for e in self.ledger.load_basket(bot, account_id="paper")}

        prices = {"BTCUSDT": 100.0, "ETHUSDT": 50.0, "SOLUSDT": 20.0, "DOGEUSDT": 0.1}
        asyncio.run(
            rebalancer.execute_rebalance(
                rank_result=_rank_result(["ETHUSDT", "DOGEUSDT"], ["SOLUSDT", "BTCUSDT"]),
                exchange_adapter=None,
                dry_run=True,
                prices_override=prices,
                account_id="paper",
                managed_symbols={"BTCUSDT"},
                batch_id="b1",
            )
        )
        after = {(e.symbol, e.quantity) for e in self.ledger.load_basket(bot, account_id="paper")}
        self.assertEqual(before, after)

    def test_dry_run_estimates_realized_pnl_from_basket(self):
        rebalancer = self._rebalancer()
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0)

        prices = {"BTCUSDT": 110.0, "ETHUSDT": 50.0}
        # Dry-Run 仍需交易所持倉才能產生平倉單（持倉來自 adapter，非帳本）
        adapter = _FakeAdapter(
            positions=[SimpleNamespace(symbol="BTCUSDT", size=Decimal("2"), side="long")],
            prices=prices,
        )
        report = asyncio.run(
            rebalancer.execute_rebalance(
                rank_result=_rank_result(["ETHUSDT"], []),
                exchange_adapter=adapter,
                dry_run=True,
                prices_override=prices,
                account_id="paper",
                managed_symbols={"BTCUSDT"},
                batch_id="b2",
            )
        )
        self.assertEqual(adapter.sent, [])  # Dry-Run 絕不送單
        exit_fills = [f for f in self.ledger.get_fills(bot, limit=100) if f.role == "simulated_exit"]
        self.assertEqual(len(exit_fills), 1)
        self.assertAlmostEqual(exit_fills[0].realized_pnl, 20.0)  # (110-100) * 2
        events = [e for e in self.ledger.get_events(bot) if e.event_type == "daily_rebalance"]
        self.assertAlmostEqual(events[-1].payload["estimated_realized_pnl"], 20.0)
        self.assertEqual(report.status, "completed_dry_run")

    def test_live_rebalance_updates_basket_and_realized_pnl(self):
        rebalancer = self._rebalancer()
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0)

        adapter = _FakeAdapter(
            positions=[SimpleNamespace(symbol="BTCUSDT", size=Decimal("2"), side="long")],
            prices={"BTCUSDT": 110.0, "ETHUSDT": 50.0},
        )
        report = asyncio.run(
            rebalancer.execute_rebalance(
                rank_result=_rank_result(["ETHUSDT"], []),
                exchange_adapter=adapter,
                dry_run=False,
                prices_override={"BTCUSDT": 110.0, "ETHUSDT": 50.0},
                account_id="paper",
                managed_symbols={"BTCUSDT"},
                batch_id="live-1",
            )
        )
        self.assertEqual(report.status, "completed")
        fills = {f.role: f for f in self.ledger.get_fills(bot, limit=100)}
        self.assertIn("rebalance_exit", fills)
        self.assertAlmostEqual(fills["rebalance_exit"].realized_pnl, 20.0)
        self.assertAlmostEqual(fills["rebalance_exit"].fee, 0.1)
        self.assertAlmostEqual(report.realized_pnl_usdt, 20.0)
        self.assertAlmostEqual(report.fees_usdt, 0.2, places=6)

        entries = {e.symbol: e for e in self.ledger.load_basket(bot, account_id="paper", active_only=False)}
        self.assertEqual(entries["BTCUSDT"].status, "closed")
        self.assertAlmostEqual(entries["BTCUSDT"].realized_pnl, 20.0)
        self.assertEqual(entries["ETHUSDT"].status, "active")
        self.assertGreater(entries["ETHUSDT"].quantity, 0.0)

        events = [e for e in self.ledger.get_events(bot) if e.event_type == "daily_rebalance"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].dedupe_key, "rebalance:paper:live-1:live")
        self.assertAlmostEqual(events[0].payload["realized_pnl_usdt"], 20.0)
        # 平倉單必帶 reduceOnly（先平後開、絕不被閘門阻擋）
        self.assertTrue(any(o["params"].get("reduceOnly") for o in adapter.sent))

    def test_force_flatten_exit_is_recorded_with_pnl(self):
        rebalancer = self._rebalancer()
        bot = "coinfull_prime:binance_testnet"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "binance_testnet", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "binance_testnet", "BTCUSDT", "long", 100.0, 1.0)
        adapter = _FakeAdapter(
            positions=[SimpleNamespace(symbol="BTCUSDT", size=Decimal("1"), side="long")],
            prices={"BTCUSDT": 95.0},
        )
        asyncio.run(
            rebalancer.execute_rebalance(
                rank_result=_rank_result(["ETHUSDT"], []),
                exchange_adapter=adapter,
                dry_run=False,
                prices_override={"BTCUSDT": 95.0, "ETHUSDT": 50.0},
                account_id="binance_testnet",
                managed_symbols={"BTCUSDT"},
                force_flatten=True,
                allow_new_entries=False,
                gross_scale=0.0,
                gate_pts=0,
                batch_id="flatten-1",
            )
        )
        exits = [f for f in self.ledger.get_fills(bot, limit=100) if f.role == "rebalance_exit"]
        self.assertEqual(len(exits), 1)
        self.assertAlmostEqual(exits[0].realized_pnl, -5.0)
        # 測試網不得被標記為 live 資金
        self.assertEqual(self.ledger.get_bot(bot).account_mode, "paper")

    def test_basket_db_is_source_of_truth_over_mirror_file(self):
        service = self.make_service()
        service.save_active_basket("paper", ["BTCUSDT", "ETHUSDT"], sides={"BTCUSDT": "long", "ETHUSDT": "short"})

        mirror = self.data_dir / "coinfull_prime_active_basket.json"
        self.assertTrue(mirror.exists())
        mirror_data = json.loads(mirror.read_text(encoding="utf-8"))
        self.assertEqual(mirror_data["paper"]["managed_symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(mirror_data["paper"]["source"], "canonical_ledger:bot_baskets")
        self.assertEqual(mirror_data["paper"]["entries"][0]["side"], "long")

        # 竄改鏡像檔 → 讀取仍以 DB 為準（證明檔案已非真相源）
        mirror.write_text(json.dumps({"paper": {"managed_symbols": ["HACKUSDT"], "count": 1}}), encoding="utf-8")
        self.assertEqual(service.load_active_basket("paper"), {"BTCUSDT", "ETHUSDT"})

    def test_legacy_side_file_is_backfilled_once(self):
        legacy = {
            "binance_testnet": {
                "managed_symbols": ["SOLUSDT", "DOGEUSDT"],
                "count": 2,
                "updated_at": "2026-09-15T00:00:00+00:00",
            }
        }
        (self.data_dir / "coinfull_prime_active_basket.json").write_text(json.dumps(legacy), encoding="utf-8")
        service = self.make_service()
        self.assertEqual(service.load_active_basket("binance_testnet"), {"SOLUSDT", "DOGEUSDT"})
        entries = self.ledger.load_basket("coinfull_prime:binance_testnet", account_id="binance_testnet")
        self.assertEqual({e.symbol for e in entries}, {"SOLUSDT", "DOGEUSDT"})

    def test_mirror_file_restores_full_basket_details(self):
        """0.3.6 鏡像檔（含 entries 明細）必須可無損還原回 DB —— 災復路徑。

        DB 為唯一真相源，但鏡像不得只是「名單」：當 DB 遺失/重建時，
        回填必須能還原 side / quantity / entry_price / realized_pnl，
        否則均價法損益基準會歸零（等於把歷史損益抹掉）。
        """
        from core.infrastructure.database.ledger_backfill import backfill_coinfull_prime_side_files

        mirror = {
            "paper": {
                "managed_symbols": ["BTCUSDT", "ETHUSDT"],
                "count": 2,
                "source": "canonical_ledger:bot_baskets",
                "entries": [
                    {"symbol": "BTCUSDT", "side": "long", "quantity": 2.0, "entry_price": 100.0,
                     "notional_usdt": 200.0, "realized_pnl": 12.5, "status": "active"},
                    {"symbol": "ETHUSDT", "side": "short", "quantity": 3.0, "entry_price": 50.0,
                     "notional_usdt": 150.0, "realized_pnl": -4.0, "status": "active"},
                ],
                "updated_at": "2026-09-16T00:05:00+00:00",
            }
        }
        (self.data_dir / "coinfull_prime_active_basket.json").write_text(json.dumps(mirror), encoding="utf-8")

        stats = backfill_coinfull_prime_side_files(data_dir=self.data_dir, ledger=self.ledger)
        self.assertEqual(stats["baskets"]["accounts"], 1)
        self.assertEqual(stats["baskets"]["symbols"], 2)
        self.assertEqual(stats["errors"], [])

        rows = {e.symbol: e for e in self.ledger.load_basket("coinfull_prime:paper", account_id="paper")}
        self.assertEqual(set(rows), {"BTCUSDT", "ETHUSDT"})
        self.assertEqual(rows["BTCUSDT"].side, "long")
        self.assertAlmostEqual(rows["BTCUSDT"].quantity, 2.0)
        self.assertAlmostEqual(rows["BTCUSDT"].entry_price or 0.0, 100.0)
        self.assertAlmostEqual(rows["BTCUSDT"].realized_pnl, 12.5)
        self.assertEqual(rows["ETHUSDT"].side, "short")
        self.assertAlmostEqual(rows["ETHUSDT"].realized_pnl, -4.0)

        # 冪等：重跑不得複製或洗掉已還原的明細
        backfill_coinfull_prime_side_files(data_dir=self.data_dir, ledger=self.ledger)
        rows2 = {e.symbol: e for e in self.ledger.load_basket("coinfull_prime:paper", account_id="paper")}
        self.assertEqual(set(rows2), {"BTCUSDT", "ETHUSDT"})
        self.assertAlmostEqual(rows2["BTCUSDT"].realized_pnl, 12.5)

    def test_regime_history_persisted_to_ledger_and_mirror(self):
        from core.services.coinfull_prime.models import RegimeDecision

        service = self.make_service()
        decision = RegimeDecision(timestamp="2026-09-16T00:05:00+00:00", pts=2, scale=1.0, mode="flatten",
                                  allow_new_entries=True, force_flatten=False, reasons=["trend ok"])
        service.append_regime_history(decision, "paper", note="test")
        service.append_regime_history(decision, "paper", note="test-rerun")  # 同批冪等

        rows = self.ledger.load_regime_decisions(bot_id="coinfull_prime:paper")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pts, 2)
        events = [e for e in self.ledger.get_events("coinfull_prime:paper") if e.event_type == "regime_gate"]
        self.assertEqual(len(events), 1)
        history = service.load_regime_history(account_id="paper")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "canonical_ledger:bot_regime_decisions")
        mirror = (self.data_dir / "coinfull_prime_regime_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(mirror), 2)  # 鏡像為 append-only 日誌，DB 才是去重真相源

    def test_scheduler_state_roundtrip_through_ledger(self):
        service = self.make_service()
        service.selected_account = "binance_testnet"
        service.scheduler_dry_run = True
        service.scheduler_capital = 500.0
        service._defer_count = 1
        service._pending_defer_until = "2026-09-17T00:05:00+00:00"
        service.save_scheduler_state()

        from core.infrastructure.database.ledger_backfill import SCHEDULER_BOT_ID

        state = self.ledger.get_state(SCHEDULER_BOT_ID, "scheduler")
        self.assertEqual(state["account_id"], "binance_testnet")
        self.assertEqual(state["defer_count"], 1)
        self.assertTrue((self.data_dir / "coinfull_prime_scheduler.json").exists())

        # 新實例（模擬重啟）→ 從 canonical ledger 恢復
        revived = self.make_service()
        self.assertEqual(revived.selected_account, "binance_testnet")
        self.assertEqual(revived._pending_defer_until, "2026-09-17T00:05:00+00:00")

    def test_run_snapshot_written_to_canonical_snapshot_tables(self):
        from core.services.coinfull_prime.models import RegimeDecision, RebalanceReport

        service = self.make_service()
        bot = "coinfull_prime:paper"
        self.ledger.ensure_bot(bot, "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.apply_basket_fill(bot, "paper", "BTCUSDT", "long", 100.0, 2.0)

        decision = RegimeDecision(pts=3, scale=1.0)
        report = RebalanceReport(dry_run=True, exchange="paper", status="completed_dry_run", batch_id="snap-1")
        service._persist_run_snapshot("paper", decision, report)

        snapshot = self.ledger.load_snapshot(bot)
        self.assertIsNotNone(snapshot)
        payload, _ts = snapshot
        self.assertEqual(payload["snapshot_kind"], "run")
        self.assertEqual(payload["batch_id"], "snap-1")
        self.assertEqual(payload["basket"]["position_count"], 1)
        self.assertAlmostEqual(payload["basket"]["gross_notional_usdt"], 200.0)
        self.assertEqual(len(self.ledger.load_history(bot, limit=10)), 1)
        mirror_lines = (self.data_dir / "coinfull_prime_run_snapshots.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(mirror_lines), 1)
        self.assertNotIn("_monotonic_bypass", json.loads(mirror_lines[0]))

    def test_external_bot_symbols_come_from_ledger(self):
        self.ledger.ensure_bot("grid-bot", "grid", "SOL/USDC:USDC", "binance", account_mode="live", status="running")
        self.ledger.ensure_bot("dca-bot", "dca", "ETH/USDT", "binance", account_mode="paper", status="running")
        self.ledger.ensure_bot("dead-bot", "grid", "XRP/USDT", "binance", account_mode="live", status="stopped")
        self.ledger.ensure_bot("coinfull_prime:paper", "coinfull_prime", "BASKET_16", "paper", account_mode="paper")

        service = self.make_service()
        protected = service.get_external_bot_symbols()
        self.assertIn("SOLUSDC", protected)
        self.assertIn("ETHUSDT", protected)
        self.assertNotIn("XRPUSDT", protected)          # 已停止的機器人不再列入避碰
        self.assertNotIn("BASKET16", protected)         # 自身籃子不算外部保護標的

    def test_only_active_account_identity_is_registered(self):
        service = self.make_service()
        service.selected_account = "paper"
        asyncio.run(service.initialize())
        bots = {b.bot_id for b in self.ledger.list_bots()}
        self.assertIn("coinfull_prime:paper", bots)
        # 未使用的環境不再產生幽靈機器人身分（稽核 L9）
        self.assertNotIn("coinfull_prime:binance_live", bots)


class TestGridAndDcaShareTheSamePort(UnifiedLedgerTestCase):
    """grid / DCA 的 heartbeat 記帳是否與 CoinFull Prime 走同一條路徑。"""

    def _runtime_repo(self):
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        RuntimeSnapshotRepository.reset_instance()
        self.addCleanup(RuntimeSnapshotRepository.reset_instance)
        return RuntimeSnapshotRepository(self.db_path)

    def test_dca_heartbeat_fill_goes_through_unified_facade(self):
        repo = self._runtime_repo()
        repo.save(
            "config/dca/test-bot.yaml",
            {
                "bot_id": "dca-test-bot",
                "strategy_type": "dca",
                "symbol": "ETH/USDT",
                "exchange": "binance",
                "account_mode": "paper",
                "status": "running",
                "fills": [
                    {
                        "fill_id": "dca-f1",
                        "client_order_id": "dca_c1_long_base",
                        "side": "buy",
                        "role": "base",
                        "price": 100.0,
                        "quantity": 1.0,
                        "fee": 0.02,
                        "executed_at": time.time(),
                    }
                ],
                "position": {"quantity": 1.0, "average_cost": 100.0, "total_cost": 100.0},
                "profit": {"realized": 0.0, "fees": 0.02},
            },
        )
        fills = self.repo.get_fills("dca-test-bot", limit=10)
        self.assertEqual(len(fills), 1)
        events = [e for e in self.repo.get_events("dca-test-bot") if e.event_type == "fill"]
        self.assertEqual(len(events), 1)
        health = repo._accounting.health()
        self.assertFalse(health["degraded"])
        self.assertGreaterEqual(health["writes_ok"], 2)

    def test_grid_heartbeat_and_cfp_share_backend(self):
        repo = self._runtime_repo()
        repo.save(
            "config/grid/test-grid.yaml",
            {
                "bot_id": "grid-test-bot",
                "strategy_type": "grid",
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "account_mode": "paper",
                "status": "running",
                "fills": [
                    {"fill_id": "g1", "client_order_id": "g1", "side": "buy", "role": "grid",
                     "price": 100.0, "quantity": 0.1, "executed_at": time.time()}
                ],
            },
        )
        self.ledger.ensure_bot("coinfull_prime:paper", "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        grid_bot = self.repo.get_bot("grid-test-bot")
        cfp_bot = self.repo.get_bot("coinfull_prime:paper")
        self.assertIsNotNone(grid_bot)
        self.assertIsNotNone(cfp_bot)
        # 同一張 bots 表、同一個 DB 檔 → 記帳已統一
        self.assertEqual(os.path.abspath(self.repo.store.database_path), os.path.abspath(self.db_path))


class TestAuditLoggerSelfHealing(unittest.TestCase):
    """操作稽核日誌（data/grid_audit.db）的可用性 —— 「稽核功能」本身。"""

    def setUp(self):
        from web_ui.backend.audit import AuditLogger

        AuditLogger.reset_instances()
        self.addCleanup(AuditLogger.reset_instances)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_instances_are_keyed_by_project_root(self):
        from web_ui.backend.audit import AuditLogger

        root_a = os.path.join(self.tmp.name, "a")
        root_b = os.path.join(self.tmp.name, "b")
        inst_a = AuditLogger.get_instance(root_a)
        inst_b = AuditLogger.get_instance(root_b)
        self.assertIsNot(inst_a, inst_b)
        self.assertEqual(inst_a.db_path, os.path.join(root_a, "data", "grid_audit.db"))
        self.assertEqual(inst_b.db_path, os.path.join(root_b, "data", "grid_audit.db"))
        self.assertIs(AuditLogger.get_instance(root_a), inst_a)

    def test_write_succeeds_and_is_queryable(self):
        from web_ui.backend.audit import AuditLogger

        inst = AuditLogger.get_instance(os.path.join(self.tmp.name, "c"))
        inst.log("dashboard", "create_grid", "grid-1", {"symbol": "BTC/USDT"}, "success", reason="test")
        logs = inst.get_logs(limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "create_grid")
        self.assertEqual(inst.total_write_failures(), 0)

    def test_self_heals_after_db_file_deleted(self):
        from web_ui.backend.audit import AuditLogger

        root = os.path.join(self.tmp.name, "d")
        inst = AuditLogger.get_instance(root)
        inst.log("dashboard", "start", "grid-1", None, "success")
        os.remove(inst.db_path)  # 模擬檔案被移除/輪替
        inst.log("dashboard", "stop", "grid-1", None, "success")
        logs = inst.get_logs(limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(inst.total_write_failures(), 0)  # 自我修復 → 無靜默失敗
        self.assertTrue(inst.health()["schema_ok"])


if __name__ == "__main__":
    unittest.main()
