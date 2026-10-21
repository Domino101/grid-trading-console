"""
CoinFull Prime 組合換倉 Diffing 與執行單元測試
"""

import unittest
import asyncio
from core.services.coinfull_prime.models import (
    UniverseRankResult,
    RankedItem,
    SignalZScores,
)
from core.services.coinfull_prime.rebalancer import PortfolioRebalancer


class TestCoinFullPrimeRebalancer(unittest.TestCase):

    def setUp(self):
        self.config = {
            "portfolio": {
                "total_capital_usdt": 1000.0,
                "leverage": 1.0,
                "dry_run": True
            }
        }
        self.rebalancer = PortfolioRebalancer(self.config)

        # 構造標準 2 多 2 空排程結果
        sig = SignalZScores()
        self.rank_result = UniverseRankResult(
            universe_size=4,
            long_list=[
                RankedItem(symbol="BTCUSDT", score=2.0, rank=1, signals=sig, side="long", target_notional=250.0),
                RankedItem(symbol="ETHUSDT", score=1.5, rank=2, signals=sig, side="long", target_notional=250.0)
            ],
            short_list=[
                RankedItem(symbol="SOLUSDT", score=-1.5, rank=3, signals=sig, side="short", target_notional=250.0),
                RankedItem(symbol="DOGEUSDT", score=-2.0, rank=4, signals=sig, side="short", target_notional=250.0)
            ],
            all_rankings=[]
        )

        self.prices = {
            "BTCUSDT": 50000.0,
            "ETHUSDT": 2500.0,
            "SOLUSDT": 100.0,
            "DOGEUSDT": 0.1,
            "OLDUSDT": 10.0,
        }

    def test_calculate_diff_initial_empty_positions(self):
        # 初始無持倉
        current_positions = {}
        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=self.prices,
            rank_result=self.rank_result
        )

        # 應產生 4 筆開倉單 (2 多 + 2 空)，無平倉單
        self.assertEqual(len(closed_syms), 0)
        self.assertEqual(len(opened_syms), 4)
        self.assertEqual(len(orders), 4)

        # 檢查 BTCUSDT (做多 250 USDT / 50000 = 0.005 顆)
        btc_order = next(o for o in orders if o.symbol == "BTCUSDT")
        self.assertEqual(btc_order.side, "buy")
        self.assertAlmostEqual(btc_order.amount, 0.005, places=5)
        self.assertEqual(btc_order.reduce_only, False)

        # 檢查 DOGEUSDT (做空 250 USDT / 0.1 = 2500 顆)
        doge_order = next(o for o in orders if o.symbol == "DOGEUSDT")
        self.assertEqual(doge_order.side, "sell")
        self.assertAlmostEqual(doge_order.amount, 2500.0, places=1)
        self.assertEqual(doge_order.reduce_only, False)

    def test_calculate_diff_with_old_positions_exit(self):
        # 假定先前持有 OLDUSDT (多頭 10 顆 = 100 USDT)
        current_positions = {
            "OLDUSDT": 10.0,
            "BTCUSDT": 0.005 # 已持有正確的 BTC 多單
        }

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=self.prices,
            rank_result=self.rank_result
        )

        # OLDUSDT 不在名單內 -> 必須平倉 (SELL 10 顆, reduce_only=True)
        self.assertIn("OLDUSDT", closed_syms)
        old_order = next(o for o in orders if o.symbol == "OLDUSDT")
        self.assertEqual(old_order.side, "sell")
        self.assertTrue(old_order.reduce_only)
        self.assertEqual(old_order.reason, "exit_old")

        # BTCUSDT 倉位已滿足目標 -> 不應重複產生訂單 (省手續費)
        btc_orders = [o for o in orders if o.symbol == "BTCUSDT"]
        self.assertEqual(len(btc_orders), 0)

    def test_calculate_diff_with_ccxt_slash_format_symbols(self):
        # 模擬幣安等交易所 CCXT 回傳格式：帶斜線與冒號（例如 BTC/USDT:USDT）
        current_positions = {
            "OLD/USDT:USDT": 10.0,
            "BTC/USDT:USDT": 0.005
        }

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=self.prices,
            rank_result=self.rank_result
        )

        # 1. 舊持倉 OLD 必須被識別並平倉（符號自動正規化為 OLDUSDT）
        self.assertIn("OLDUSDT", closed_syms)
        old_order = next(o for o in orders if o.symbol == "OLDUSDT")
        self.assertEqual(old_order.side, "sell")
        self.assertTrue(old_order.reduce_only)
        self.assertEqual(old_order.reason, "exit_old")

        # 2. BTC/USDT:USDT 應被正規化對齊，識別為已達目標，不重複下單
        btc_orders = [o for o in orders if o.symbol == "BTCUSDT"]
        self.assertEqual(len(btc_orders), 0)

        # 3. 新入榜標的 (ETH, SOL, DOGE) 應產生開倉單
        self.assertIn("ETHUSDT", opened_syms)
        self.assertIn("SOLUSDT", opened_syms)
        self.assertIn("DOGEUSDT", opened_syms)

    def test_execute_rebalance_dry_run(self):
        async def run_test():
            report = await self.rebalancer.execute_rebalance(
                rank_result=self.rank_result,
                exchange_adapter=None,
                dry_run=True,
                prices_override=self.prices
            )
            self.assertEqual(report.status, "completed_dry_run")
            self.assertEqual(len(report.orders), 4)
            for o in report.orders:
                self.assertEqual(o.status, "simulated")

        asyncio.run(run_test())


    def test_find_exchange_market(self):
        class MockAdapter:
            name = "binance"
            _market_info = {
                "BTC/USDT:USDT": {"id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "linear": True, "active": True},
                "CFG/USDT": {"id": "CFGUSDT", "symbol": "CFG/USDT", "spot": True, "linear": False, "active": True}
            }

        adapter = MockAdapter()
        btc_m = PortfolioRebalancer._find_exchange_market(adapter, "BTCUSDT")
        self.assertIsNotNone(btc_m)
        self.assertTrue(btc_m.get("linear"))

        # CFGUSDT 是現貨非合約 -> 應返回 None
        cfg_m = PortfolioRebalancer._find_exchange_market(adapter, "CFGUSDT")
        self.assertIsNone(cfg_m)

    def test_quantize_order_amount(self):
        # 測試整數步長合約（如 SPCXUSDT step=1.0, min=1.0）
        market_int = {
            "precision": {"amount": 1.0},
            "limits": {"amount": {"min": 1.0}}
        }
        # 0.45 張 -> 應被判定低於最小下單量
        amt, skip_reason = PortfolioRebalancer._quantize_order_amount(0.45, market_int)
        self.assertEqual(amt, 0.0)
        self.assertIn("低於交易所最小合約下單量", skip_reason)

        # 2.85 張 -> 應裁切為 2.0 張
        amt, skip_reason = PortfolioRebalancer._quantize_order_amount(2.85, market_int)
        self.assertEqual(amt, 2.0)
        self.assertIsNone(skip_reason)

        # 測試小數位步長合約（如 ORCAUSDT step=0.1, min=0.1）
        market_dec = {
            "precision": {"amount": 0.1},
            "limits": {"amount": {"min": 0.1}}
        }
        amt, skip_reason = PortfolioRebalancer._quantize_order_amount(41.06, market_dec)
        self.assertEqual(amt, 41.0)
        self.assertIsNone(skip_reason)


    def test_calculate_diff_with_anti_interference_and_protected_bots(self):
        # 模擬帳戶現有持倉：包含外部網格部位 (SOLUSDC)、外部DCA部位 (ETHUSDC)、專屬舊持倉 (OLDUSDT) 與未登記持倉 (UNMANAGED)
        current_positions = {
            "SOLUSDC": 12.5,
            "ETHUSDC": 0.45,
            "OLDUSDT": 10.0,
            "UNMANAGED": 5.0
        }
        prices = dict(self.prices)
        prices["SOLUSDC"] = 130.0
        prices["UNMANAGED"] = 20.0

        # 設定保護名單與專屬持倉籃子
        protected_symbols = {"SOLUSDC", "ETHUSDC"}
        managed_symbols = {"OLDUSDT"}

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=prices,
            rank_result=self.rank_result,
            managed_symbols=managed_symbols,
            protected_symbols=protected_symbols
        )

        # 斷言：外部機器人標的 (SOLUSDC, ETHUSDC) 與未納管部位 (UNMANAGED) 絕對不得被平倉
        self.assertNotIn("SOLUSDC", closed_syms)
        self.assertNotIn("ETHUSDC", closed_syms)
        self.assertNotIn("UNMANAGED", closed_syms)

        # 斷言：僅有登記在 CoinFull Prime 專屬籃子內且跌出名單的 OLDUSDT 被平倉
        self.assertIn("OLDUSDT", closed_syms)

        # 斷言：入榜新標的正常開倉
        self.assertIn("BTCUSDT", opened_syms)

    def test_execute_rebalance_records_ledger_fills_and_events(self):
        """驗證 execute_rebalance 自動登記 bot 身分並將 fills 與 events 寫入 Canonical Ledger

        P4-1：讀寫同源 —— 一律經 StrategyLedger（統一記帳門面），並額外驗證
        canonical fill 事件與換倉事件的冪等 dedupe_key。
        """
        async def run_test():
            from core.infrastructure.database.strategy_ledger import StrategyLedger
            report = await self.rebalancer.execute_rebalance(
                rank_result=self.rank_result,
                exchange_adapter=None,
                dry_run=True,
                prices_override=self.prices,
                account_id="paper"
            )
            self.assertEqual(report.status, "completed_dry_run")
            self.assertEqual(len(report.orders), 4)

            store = StrategyLedger.instance()

            # 驗證 bots 表有登記身分
            bot = store.get_bot("coinfull_prime:paper")
            self.assertIsNotNone(bot)
            self.assertEqual(bot.strategy_type, "coinfull_prime")

            # 驗證 trade_fills 表有寫入成交流水
            fills = store.get_fills("coinfull_prime:paper", limit=100)
            self.assertGreaterEqual(len(fills), 4)

            # 驗證 bot_events 表有寫入換倉事件（含冪等 dedupe_key）與 canonical fill 事件
            events = store.get_events("coinfull_prime:paper", limit=100)
            rebalance_events = [e for e in events if e.event_type == "daily_rebalance"]
            self.assertGreaterEqual(len(rebalance_events), 1)
            self.assertIn("orders", rebalance_events[-1].payload)
            self.assertTrue(rebalance_events[-1].dedupe_key)
            self.assertIn("batch_id", rebalance_events[-1].payload)
            fill_events = [e for e in events if e.event_type == "fill"]
            self.assertGreaterEqual(len(fill_events), len(fills))

        asyncio.run(run_test())

    def test_paper_adapter_rebalance_execution_not_skipped(self):
        """驗證使用 PaperExchangeAdapter (dry_run=False) 時不會被誤判跳過"""
        from core.adapters.exchanges.adapters.paper import create_paper_adapter

        paper_config = {
            "grid_system": {
                "symbol": "BTC/USDT",
                "exchange": "paper",
                "account_mode": "paper"
            }
        }
        adapter = create_paper_adapter(paper_config)
        self.assertTrue(PortfolioRebalancer._is_paper_adapter(adapter))
        market = PortfolioRebalancer._find_exchange_market(adapter, "龙虾USDT")
        self.assertIsNotNone(market)
        self.assertTrue(market.get("linear"))

        async def run_exec():
            report = await self.rebalancer.execute_rebalance(
                rank_result=self.rank_result,
                exchange_adapter=adapter,
                dry_run=False,
                prices_override=self.prices,
                account_id="paper"
            )
            self.assertEqual(report.status, "completed")
            # 確保 4 筆訂單皆被正常成交，無任何訂單被 skipped
            for ord_spec in report.orders:
                self.assertEqual(ord_spec.status, "filled", f"Order {ord_spec.symbol} was {ord_spec.status}: {ord_spec.error}")

        asyncio.run(run_exec())

    def test_execute_stop_loss_settles_and_records(self):
        """驗證盤中單幣硬止損（市價 reduceOnly 平倉）的均價法結算與記帳完整性"""
        from core.infrastructure.database.strategy_ledger import StrategyLedger
        from core.infrastructure.database.models import BasketEntry
        import time

        ledger = StrategyLedger.instance()
        bot_id = "coinfull_prime:paper"
        account_id = "paper"

        # 登記身分
        ledger.ensure_bot(
            bot_id=bot_id,
            strategy_type="coinfull_prime",
            symbol="BASKET_16",
            exchange=account_id,
            account_mode="paper",
            status="running",
        )

        # 設置初始多頭部位：買入 100 顆 CRASHCOIN，進場價 10.0 (名義 1000 USDT)
        ledger.save_basket(
            bot_id=bot_id,
            account_id=account_id,
            entries=[
                BasketEntry(
                    bot_id=bot_id,
                    account_id=account_id,
                    symbol="CRASHCOIN",
                    side="long",
                    quantity=100.0,
                    entry_price=10.0,
                    notional_usdt=1000.0,
                    status="active",
                    updated_at=time.time(),
                )
            ],
            close_missing=False
        )

        async def run_stop_loss():
            from decimal import Decimal
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, MagicMock

            # 觸發止損：市價暴跌至 8.5 (-15.0%，觸發 -12% 硬止損)
            adapter = MagicMock()
            adapter.name = "binance_testnet"
            adapter._market_info = {}
            adapter.create_order = AsyncMock(return_value=SimpleNamespace(
                filled=Decimal("100"),
                average=8.5,
                price=8.5,
                id="SLTEST123",
                fee={"cost": 0.0, "currency": "USDT"},
            ))
            res = await self.rebalancer.execute_stop_loss(
                symbol="CRASHCOIN",
                account_id=account_id,
                current_price=8.5,
                exchange_adapter=adapter,
                dry_run=False,
                reason="intra_week_stop_loss"
            )

            self.assertEqual(res["status"], "filled")
            self.assertEqual(res["symbol"], "CRASHCOIN")
            self.assertEqual(res["entry_price"], 10.0)
            self.assertEqual(res["exit_price"], 8.5)
            self.assertAlmostEqual(res["pnl_pct"], -0.15, places=2)
            # 均價法已實現損益: (8.5 - 10.0) * 100 = -150.0 USDT
            self.assertAlmostEqual(res["realized_pnl"], -150.0, places=2)

            # 檢驗籃子中該標的部位已清空並結算
            entries = ledger.load_basket(bot_id, account_id=account_id, active_only=True)
            crash_entries = [e for e in entries if e.symbol == "CRASHCOIN"]
            self.assertEqual(len(crash_entries), 0) # active_only 應不再包含已平倉標的

            # 檢驗 trade_fills 表有記錄 stop_loss 流水
            fills = ledger.get_fills(bot_id, limit=20)
            sl_fills = [f for f in fills if f.role == "stop_loss"]
            self.assertGreaterEqual(len(sl_fills), 1)
            self.assertEqual(sl_fills[-1].side, "sell")
            self.assertEqual(sl_fills[-1].exchange_order_id, "SLTEST123")
            self.assertAlmostEqual(sl_fills[-1].realized_pnl, -150.0, places=2)

            # 檢驗 bot_events 表有記錄 stop_loss 事件
            events = ledger.get_events(bot_id, limit=20)
            sl_events = [e for e in events if e.event_type == "stop_loss"]
            self.assertGreaterEqual(len(sl_events), 1)
            self.assertEqual(sl_events[-1].payload.get("symbol"), "CRASHCOIN")

        asyncio.run(run_stop_loss())

    def test_rebalance_sets_leverage_on_adapter(self):
        """測試實盤換倉開倉單下單前，必須呼叫 exchange_adapter.set_leverage 以防 code -2027"""
        from unittest.mock import AsyncMock, MagicMock
        from decimal import Decimal

        mock_adapter = MagicMock()
        mock_adapter.set_leverage = AsyncMock(return_value={"ret": "ok"})
        mock_adapter.get_positions = AsyncMock(return_value=[])
        mock_adapter.get_ticker = AsyncMock(return_value=MagicMock(last_price=10.0))
        mock_adapter.create_order = AsyncMock(return_value=MagicMock(
            id="test_ord_1",
            price=10.0,
            average=10.0,
            filled=10.0,
            fee={"cost": 0.05, "currency": "USDT"}
        ))
        mock_adapter._is_paper = False
        mock_adapter._market_info = {
            "TESTUSDT": {"id": "TESTUSDT", "linear": True, "precision": {"amount": 2}, "limits": {"amount": {"min": 0.1, "step": 0.01}}}
        }

        sig = SignalZScores()
        rank_res = UniverseRankResult(
            universe_size=1,
            long_list=[
                RankedItem(symbol="TESTUSDT", score=2.0, rank=1, signals=sig, side="long", target_notional=100.0)
            ],
            short_list=[],
            all_rankings=[]
        )

        async def run_rebalance():
            report = await self.rebalancer.execute_rebalance(
                rank_result=rank_res,
                exchange_adapter=mock_adapter,
                dry_run=False,
                capital_override=100.0,
                leverage_override=1.0,
                account_id="binance_live"
            )
            # 必須呼叫 set_leverage 設定為 1x
            mock_adapter.set_leverage.assert_awaited_with("TESTUSDT", 1)
            # 必須下單成功
            self.assertEqual(len(report.orders), 1)
            self.assertEqual(report.orders[0].status, "filled")

        asyncio.run(run_rebalance())

    def test_calculate_diff_with_position_reversal(self):
        """測試反向翻倉 (如持有多單但目標為做空) 必須正確拆分為先平後開兩筆訂單，避免帶超出部位的 reduceOnly 委託"""
        sig = SignalZScores()
        rank_res = UniverseRankResult(
            universe_size=1,
            long_list=[],
            short_list=[
                RankedItem(symbol="BTCUSDT", score=-2.0, rank=1, signals=sig, side="short", target_notional=100.0)
            ],
            all_rankings=[]
        )
        # 當前持有 0.001 顆多單 (價值 50 USDT)
        current_positions = {"BTCUSDT": 0.001}
        prices = {"BTCUSDT": 50000.0}

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=prices,
            rank_result=rank_res
        )

        # 必須拆為兩筆訂單：
        # 1. 平舊多倉: side=sell, amount=0.001, reduce_only=True, reason=flip_close
        # 2. 開新空倉: side=sell, amount=0.002 (100U / 50000), reduce_only=False, reason=flip_open
        self.assertEqual(len(orders), 2)
        close_ord = orders[0]
        self.assertEqual(close_ord.side, "sell")
        self.assertEqual(close_ord.amount, 0.001)
        self.assertTrue(close_ord.reduce_only)
        self.assertEqual(close_ord.reason, "flip_close")

        open_ord = orders[1]
        self.assertEqual(open_ord.side, "sell")
        self.assertEqual(open_ord.amount, 0.002)
        self.assertFalse(open_ord.reduce_only)
        self.assertEqual(open_ord.reason, "flip_open")

    def test_execute_rebalance_status_evaluation(self):
        """測試實盤換倉時若部分訂單下單異常，report.status 必須準確標記為 partial 或 failed，不能盲目標記 completed"""
        from unittest.mock import AsyncMock, MagicMock

        mock_adapter = MagicMock()
        mock_adapter.set_leverage = AsyncMock(return_value={"ret": "ok"})
        mock_adapter.get_positions = AsyncMock(return_value=[])
        mock_adapter.get_ticker = AsyncMock(return_value=MagicMock(last_price=10.0))
        # 模擬下單拋出異常
        mock_adapter.create_order = AsyncMock(side_effect=Exception("APIError -2027: Margin insufficient"))
        mock_adapter._is_paper = False
        mock_adapter._market_info = {
            "TESTUSDT": {"id": "TESTUSDT", "linear": True, "precision": {"amount": 2}, "limits": {"amount": {"min": 0.1, "step": 0.01}}}
        }

        sig = SignalZScores()
        rank_res = UniverseRankResult(
            universe_size=1,
            long_list=[
                RankedItem(symbol="TESTUSDT", score=2.0, rank=1, signals=sig, side="long", target_notional=100.0)
            ],
            short_list=[],
            all_rankings=[]
        )

        async def run_rebalance():
            report = await self.rebalancer.execute_rebalance(
                rank_result=rank_res,
                exchange_adapter=mock_adapter,
                dry_run=False,
                capital_override=100.0,
                leverage_override=1.0,
                account_id="binance_live"
            )
            # 訂單失敗時 status 必須為 failed 且 error 必須包含錯誤細節
            self.assertEqual(report.status, "failed")
            self.assertIn("Margin insufficient", str(report.error))

        asyncio.run(run_rebalance())


if __name__ == "__main__":
    unittest.main()


