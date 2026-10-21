"""
CoinFull Prime P1-A 回歸測試 (2026-10-21)

C-1 止損與換倉共用互斥鎖
C-6 配置缺失 fail-closed；CoinFullPrimeQuant v3 預設
C-7 加碼前以可用保證金封頂 scale
"""

import asyncio
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import yaml

from core.services.coinfull_prime.models import (
    RawFeature,
    RegimeDecision,
    UniverseRankResult,
)
from core.services.coinfull_prime.quant_engine import CoinFullPrimeQuant
from core.services.coinfull_prime.regime_filter import RegimeFilter
from core.services.coinfull_prime.service import CoinFullPrimeService


class _BalanceAdapter:
    def __init__(self, free):
        self.free = free

    async def get_balances(self):
        return [SimpleNamespace(
            currency="USDT",
            free=Decimal(str(self.free)),
            total=Decimal(str(self.free)),
        )]


class _BrokenBalanceAdapter:
    async def get_balances(self):
        raise RuntimeError("balance endpoint down")


class TestConfigFailClosed(unittest.TestCase):
    def test_missing_config_refuses_start(self):
        service = CoinFullPrimeService(config_path="/tmp/coinfull_prime_missing_config.yaml")
        with self.assertRaises(RuntimeError):
            asyncio.run(service.initialize())

    def test_empty_config_refuses_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.yaml"
            path.write_text("", encoding="utf-8")
            service = CoinFullPrimeService(config_path=str(path))
            with self.assertRaises(RuntimeError):
                asyncio.run(service.initialize())

    def test_quant_v3_defaults(self):
        quant = CoinFullPrimeQuant()
        self.assertEqual(quant.weights["momentum"], 0.60)
        self.assertEqual(quant.weights["funding"], 0.40)
        self.assertEqual(quant.weights["flow"], 0.0)
        self.assertEqual(quant.weights["liquidation"], 0.0)
        self.assertEqual(quant.hedge_mode, "benchmark")
        self.assertEqual(quant.momentum_mode, "blend")
        self.assertEqual(quant.funding_mode, "smooth")
        self.assertEqual(quant.momentum_blend, {"r1d": 0.0, "r7d": 0.5, "r14d": 0.3, "r30d": 0.2})
        self.assertTrue(quant.overheat_penalty)
        self.assertEqual(quant.top_n, 80)
        self.assertEqual(quant.long_count, 5)
        self.assertEqual(quant.short_count, 1)
        self.assertEqual(quant.min_24h_volume_usdt, 50_000_000.0)
        self.assertEqual(quant.max_7d_atr_pct, 0.20)
        self.assertEqual(quant.annual_max_long_pct, 60.0)
        self.assertEqual(quant.annual_min_short_pct, -40.0)


class TestStopLossLock(unittest.TestCase):
    def test_stop_loss_skips_when_rebalance_lock_held(self):
        service = CoinFullPrimeService()
        service.load_basket_entries = MagicMock(return_value=[])

        async def run():
            await service._rebalance_lock.acquire()
            try:
                return await service.check_and_trigger_stop_losses()
            finally:
                service._rebalance_lock.release()

        self.assertEqual(asyncio.run(run()), [])
        service.load_basket_entries.assert_not_called()

    def test_stop_loss_releases_lock_when_no_positions(self):
        service = CoinFullPrimeService()
        service.load_basket_entries = MagicMock(return_value=[])
        self.assertEqual(asyncio.run(service.check_and_trigger_stop_losses()), [])
        self.assertFalse(service._rebalance_lock.locked())


class TestScaleMarginCap(unittest.TestCase):
    def setUp(self):
        self.service = CoinFullPrimeService()
        self.service.rebalancer = MagicMock()
        self.service.rebalancer.leverage = 1.0
        self.decision = RegimeDecision(scale=1.15, force_flatten=False)

    def _cap(self, adapter, capital=1000.0):
        return asyncio.run(self.service._cap_scale_by_free_margin(
            adapter=adapter,
            account_id="paper",
            effective_capital=capital,
            decision=self.decision,
        ))

    def test_sufficient_free_margin_keeps_scale(self):
        result = self._cap(_BalanceAdapter(free=2000))
        self.assertAlmostEqual(result.scale, 1.15)

    def test_insufficient_free_margin_caps_to_one(self):
        result = self._cap(_BalanceAdapter(free=1000))
        self.assertAlmostEqual(result.scale, 1.0)

    def test_intermediate_free_margin_caps_fractionally(self):
        result = self._cap(_BalanceAdapter(free=1100))
        self.assertAlmostEqual(result.scale, 1.045, places=4)

    def test_missing_adapter_caps_to_one(self):
        result = self._cap(None)
        self.assertAlmostEqual(result.scale, 1.0)

    def test_balance_failure_caps_to_one(self):
        result = self._cap(_BrokenBalanceAdapter())
        self.assertAlmostEqual(result.scale, 1.0)

    def test_force_flatten_is_not_capped(self):
        decision = RegimeDecision(scale=1.15, force_flatten=True)
        result = asyncio.run(self.service._cap_scale_by_free_margin(
            adapter=None,
            account_id="paper",
            effective_capital=1000.0,
            decision=decision,
        ))
        self.assertAlmostEqual(result.scale, 1.15)


class TestOptDFailClosed(unittest.TestCase):
    def _quant(self, **universe):
        cfg = {
            "portfolio": {"long_count": 2, "short_count": 1, "total_capital_usdt": 1000.0, "leverage": 1.0},
            "hedge": {"mode": "benchmark", "symbol": "BTCUSDT", "weight": 0.50},
            "universe": {"min_24h_volume_usdt": 0.0, "max_7d_atr_pct": 0.0},
        }
        cfg["universe"].update(universe)
        return CoinFullPrimeQuant(cfg)

    @staticmethod
    def _features():
        return [
            RawFeature(symbol="BTCUSDT", momentum=20.0, funding=0.01),
            RawFeature(symbol="AAAUSDT", momentum=10.0, funding=0.0),
            RawFeature(symbol="BBBUSDT", momentum=5.0, funding=0.0),
            RawFeature(symbol="CCCUSDT", momentum=1.0, funding=0.0),
        ]

    def test_missing_volume_data_fails_closed(self):
        quant = self._quant(min_24h_volume_usdt=50_000_000.0)
        quant.quote_volumes = {}
        result = quant.compute_ranks(self._features())
        self.assertEqual(result.status, "failed")
        self.assertIn("volume", result.message)

    def test_missing_atr_data_fails_closed(self):
        quant = self._quant(max_7d_atr_pct=0.20)
        quant.atr_pct_map = {}
        result = quant.compute_ranks(self._features())
        self.assertEqual(result.status, "failed")
        self.assertIn("ATR", result.message)

    def test_missing_atr_symbol_is_excluded(self):
        quant = self._quant(max_7d_atr_pct=0.20)
        quant.quote_volumes = {s: 100_000_000.0 for s in ("BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT")}
        quant.atr_pct_map = {"AAAUSDT": 0.10, "BBBUSDT": 0.05}  # CCC 缺值
        result = quant.compute_ranks(self._features())
        self.assertEqual(result.status, "success")
        self.assertNotIn("CCCUSDT", [x.symbol for x in result.long_list])

    def test_fallback_does_not_relax_atr(self):
        quant = self._quant(max_7d_atr_pct=0.20)
        quant.quote_volumes = {s: 100_000_000.0 for s in ("BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT")}
        quant.atr_pct_map = {"AAAUSDT": 0.10, "BBBUSDT": 0.50, "CCCUSDT": 0.0}
        result = quant.compute_ranks(self._features())
        self.assertEqual(result.status, "success")
        long_symbols = [x.symbol for x in result.long_list]
        self.assertIn("AAAUSDT", long_symbols)
        self.assertNotIn("BBBUSDT", long_symbols)  # ATR 0.50 不得因回補被放寬納入

    def test_restricted_symbols_filters(self):
        quant = self._quant()
        quant.restricted_tags = {"AAAUSDT"}
        result = quant.compute_ranks(self._features())
        self.assertEqual(result.status, "success")
        self.assertNotIn("AAAUSDT", [x.symbol for x in result.long_list])

    def test_production_config_has_restricted_symbols(self):
        cfg = yaml.safe_load(Path("config/coinfull_prime/strategy_config.yaml").read_text(encoding="utf-8"))
        self.assertIn("restricted_symbols", cfg["universe"])


class TestRankingFailureHold(unittest.TestCase):
    def test_ranking_failure_does_not_liquidate_positions(self):
        service = CoinFullPrimeService()
        service.quant_engine = MagicMock()
        service.quant_engine.fetch_universe = AsyncMock(return_value=["BTCUSDT", "AAAUSDT"])
        service.quant_engine.fetch_features = AsyncMock(return_value=[])
        service.quant_engine.compute_ranks = MagicMock(return_value=UniverseRankResult(
            status="failed", message="no eligible long candidates after Opt D filters"
        ))
        service.rebalancer = MagicMock()
        service.rebalancer.total_capital = 1000.0
        service.rebalancer.leverage = 1.0
        service.rebalancer.dry_run = True
        service.rebalancer.execute_rebalance = AsyncMock()
        service.regime_filter = RegimeFilter({"regime_gate": {"mode": "flatten"}})
        service.regime_filter.collect = AsyncMock(return_value={
            "btc_above_ma50": True,
            "breadth_pct": 60.0,
            "funding_pctile": 60.0,
            "btc_ret_1d_pct": 1.0,
            "btc_close": 100000.0,
            "btc_ma50": 90000.0,
        })
        service.get_adapter = AsyncMock(return_value=None)
        service.load_active_basket = MagicMock(return_value={"OLDUSDT"})
        service.get_external_bot_symbols = MagicMock(return_value=set())
        service.append_regime_history = MagicMock()
        service._persist_run_snapshot = MagicMock()
        service.exclude_tradfi = True
        service.selected_account = "paper"

        _, report = asyncio.run(service.trigger_once(dry_run=True, account_id="paper"))

        self.assertEqual(report.status, "ranking_failed")
        self.assertEqual(report.orders, [])
        service.rebalancer.execute_rebalance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
