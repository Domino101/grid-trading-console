"""共享 OHLCV 快取測試（Phase 3 稽核 3.6–3.9、3.11）。"""

import asyncio
import unittest
from types import SimpleNamespace

from core.services.dca.market_data.ohlcv_cache import OhlcvCache


def _bar(close: float, synthetic: bool = True):
    return SimpleNamespace(close=close, raw_data={"synthetic": synthetic})


class CountingFetcher:
    def __init__(self, bars=None, fail_after: int = 0, fail: bool = False):
        self.calls = 0
        self.bars = bars or [_bar(100 + i, synthetic=False) for i in range(30)]
        self.fail_after = fail_after
        self.fail = fail

    async def __call__(self, exchange, symbol, timeframe, limit):
        self.calls += 1
        if self.fail and self.calls > self.fail_after:
            raise RuntimeError("injected ohlcv failure")
        return self.bars


class OhlcvCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_within_ttl(self):
        """稽核 3.6：同試一個 TTL 內多次取得 → 底層只呼叫 1 次。"""
        fetcher = CountingFetcher()
        cache = OhlcvCache(fetcher)
        snapshots = []
        for _ in range(5):
            snapshots.append(await cache.get("binance", "ETH/USDC:USDC", "15m", 100))
        self.assertEqual(fetcher.calls, 1)
        for s in snapshots:
            self.assertIsNotNone(s)

    async def test_stampede_protection(self):
        """稽核 3.7：5 個同時 cache miss → 底層仍只呼叫 1 次。"""
        fetcher = CountingFetcher()
        cache = OhlcvCache(fetcher)
        results = await asyncio.gather(*[
            cache.get("binance", "ETH/USDC:USDC", "15m", 100) for _ in range(5)
        ])
        self.assertEqual(fetcher.calls, 1)
        self.assertEqual(sum(1 for r in results if r is not None), 5)

    async def test_ttl_aligned_to_timeframe(self):
        """稽核 3.8：15m 的 TTL ≤ 60s；過期後重新拉取。"""
        clock = [0.0]
        fetcher = CountingFetcher()
        cache = OhlcvCache(fetcher, now_fn=lambda: clock[0])

        await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertEqual(fetcher.calls, 1)
        clock[0] = 30.0
        await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertEqual(fetcher.calls, 1)   # 30s 內命中
        clock[0] = 61.0
        await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertEqual(fetcher.calls, 2)   # 過期後重拉

    async def test_failure_without_cache_returns_none(self):
        """稽核 3.9：交易所拋例外且無快取 → 回傳 None。"""
        fetcher = CountingFetcher(fail_after=0, fail=True)
        cache = OhlcvCache(fetcher)
        result = await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertIsNone(result)

    async def test_failure_with_previous_cache_returns_stale(self):
        """稽核 3.9：有前一份快取時，交易所失敗回傳上一份並標記 stale=True。"""
        clock = [0.0]
        fetcher = CountingFetcher(fail_after=1, fail=True)
        cache = OhlcvCache(fetcher, now_fn=lambda: clock[0])
        first = await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertIsNotNone(first)
        self.assertFalse(first.stale)
        clock[0] = 61.0   # 過期，下一次 get 會嘗試 refresh 並失敗
        second = await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertIsNotNone(second)
        self.assertTrue(second.stale)
        self.assertEqual(second.closes, first.closes)

    async def test_drop_last_bar_and_synthetic_flag(self):
        """運算約定 1（丟棄未收線尾棒）與合成標記（稽核 3.11）。"""
        fetcher = CountingFetcher(
            [_bar(100), _bar(101), _bar(102), _bar(103), _bar(104)],
        )
        cache = OhlcvCache(fetcher)
        snapshot = await cache.get("binance", "ETH/USDC:USDC", "15m", 100)
        self.assertEqual(snapshot.closes, [100.0, 101.0, 102.0, 103.0])
        self.assertTrue(snapshot.synthetic)

    async def test_provider_returns_bounded_closes(self):
        """provider 回傳 closes（EntryMonitor 介面），且限制長度。"""
        fetcher = CountingFetcher(
            [_bar(100), _bar(101), _bar(102), _bar(103), _bar(104)],
        )
        cache = OhlcvCache(fetcher)
        provider = cache.provider("binance")
        closes = await provider("ETH/USDC:USDC", "15m", 3)
        self.assertEqual(closes, [101.0, 102.0, 103.0])  # 丟尾棒存 4 根，取末尾 3
        self.assertEqual(fetcher.calls, 1)


if __name__ == "__main__":
    unittest.main()
