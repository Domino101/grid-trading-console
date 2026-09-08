"""
CoinFull Prime 核心量化引擎 (純客觀截面矩陣計算)
"""

import asyncio
import logging
from typing import Dict, List, Optional, Set
import numpy as np
import aiohttp

from .models import (
    RawFeature,
    SignalZScores,
    RankedItem,
    UniverseRankResult,
    RawMetrics,
    ParameterEvaluations
)

logger = logging.getLogger("CoinFullPrime.Quant")

DEFAULT_STABLES: Set[str] = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", 
    "EURUSDT", "BUSDUSDT", "AEURUSDT", "USDEUSDT", "BFDUSDUSDT"
}

DEFAULT_WEIGHTS = {
    "momentum": 0.35,
    "flow": 0.35,
    "funding": 0.20,
    "liquidation": 0.10
}


class CoinFullPrimeQuant:
    """CoinFull Prime 截面多空量化計算引擎 (非同步、高效、零副作用)"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.weights = self.config.get("weights", DEFAULT_WEIGHTS)
        self.base_url = "https://fapi.binance.com"
        self.stables = set(self.config.get("universe", {}).get("exclude_stables", DEFAULT_STABLES))
        self.top_n = self.config.get("universe", {}).get("top_n", 79)
        self.long_count = self.config.get("portfolio", {}).get("long_count", 8)
        self.short_count = self.config.get("portfolio", {}).get("short_count", 8)
        self.total_capital = float(self.config.get("portfolio", {}).get("total_capital_usdt", 1000.0))
        self.leverage = float(self.config.get("portfolio", {}).get("leverage", 1.0))

    async def fetch_universe(
        self,
        session: aiohttp.ClientSession,
        allowed_symbols: Optional[Set[str]] = None,
        excluded_symbols: Optional[Set[str]] = None
    ) -> List[str]:
        """非同步獲取 24h 報價成交額前 top_n 幣種（排除穩定幣，可指定支援標的白名單與外部機器人黑名單）"""
        url = f"{self.base_url}/fapi/v1/ticker/24hr"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()

        clean_allowed = {s.upper().replace("/", "").replace(":", "") for s in allowed_symbols} if allowed_symbols else None
        
        # 建立避碰排除清單 (包含完整代號與基礎幣種，例如 SOLUSDC -> 排除 SOLUSDC 與 SOL)
        clean_excluded = set()
        clean_base_excluded = set()
        if excluded_symbols:
            for s in excluded_symbols:
                norm = str(s).upper().replace("/", "").replace(":", "").replace("-", "").replace("_", "").strip()
                clean_excluded.add(norm)
                for quote in ["USDT", "USDC", "BUSD", "USD"]:
                    if norm.endswith(quote) and len(norm) > len(quote):
                        clean_base_excluded.add(norm[:-len(quote)])

        def _is_excluded(sym: str) -> bool:
            if not sym:
                return True
            s = sym.upper()
            if s in clean_excluded:
                return True
            base = s[:-4] if s.endswith("USDT") else s
            if base in clean_base_excluded or base in clean_excluded:
                return True
            return False

        usdt_pairs = [
            item for item in data 
            if isinstance(item, dict)
            and item.get("symbol", "").endswith("USDT") 
            and item.get("symbol") not in self.stables
            and not _is_excluded(item.get("symbol", ""))
            and (not clean_allowed or item.get("symbol", "").upper() in clean_allowed)
        ]
        # 依 quoteVolume (24h USDT 成交額) 降冪排序
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        universe = [item["symbol"] for item in usdt_pairs[:self.top_n]]
        filter_msg = []
        if clean_allowed:
            filter_msg.append(f"白名單: {len(clean_allowed)} 幣")
        if clean_excluded or clean_base_excluded:
            filter_msg.append(f"外部機器人避碰排除: {len(clean_excluded | clean_base_excluded)} 幣")
        filter_str = f" ({', '.join(filter_msg)})" if filter_msg else ""
        logger.info(f"成功獲取流動性前 {len(universe)} 名標的{filter_str} (Top 1: {universe[0] if universe else 'N/A'})")
        return universe

    async def fetch_features(self, session: aiohttp.ClientSession, symbols: List[str]) -> List[RawFeature]:
        """非同步批次獲取 79 幣的客觀合約微觀數據"""
        # 1. 資金費率全市場批次
        funding_map: Dict[str, float] = {}
        try:
            async with session.get(f"{self.base_url}/fapi/v1/premiumIndex", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                premium_data = await resp.json()
                if isinstance(premium_data, list):
                    funding_map = {
                        item["symbol"]: float(item.get("lastFundingRate", 0.0)) 
                        for item in premium_data if "symbol" in item
                    }
        except Exception as e:
            logger.warning(f"獲取資金費率批次失敗，將使用中性值: {e}")

        # 2. 24h 動能與市價全市場批次
        mom_map: Dict[str, float] = {}
        price_map: Dict[str, float] = {}
        try:
            async with session.get(f"{self.base_url}/fapi/v1/ticker/24hr", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ticker_data = await resp.json()
                if isinstance(ticker_data, list):
                    for item in ticker_data:
                        if isinstance(item, dict) and "symbol" in item:
                            sym = item["symbol"]
                            mom_map[sym] = float(item.get("priceChangePercent", 0.0))
                            if item.get("lastPrice"):
                                price_map[sym] = float(item.get("lastPrice", 0.0))
            self.latest_prices = price_map
        except Exception as e:
            logger.warning(f"獲取動能批次失敗，將使用中性值: {e}")

        # 3 & 4. 併發抓取單幣 Taker 買賣流和大戶多空比 (控頻 Semaphore=10)
        sem = asyncio.Semaphore(10)

        async def fetch_single_symbol(symbol: str) -> RawFeature:
            flow_val = 1.0
            liq_val = 1.0
            async with sem:
                # Taker Long/Short Ratio
                try:
                    url_flow = f"{self.base_url}/futures/data/takerlongshortRatio?symbol={symbol}&period=1d&limit=1"
                    async with session.get(url_flow, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        flow_json = await resp.json()
                        if isinstance(flow_json, list) and flow_json:
                            flow_val = float(flow_json[0].get("buySellRatio", 1.0))
                except Exception:
                    flow_val = 1.0

                # Top Trader Long/Short Account Ratio
                try:
                    url_liq = f"{self.base_url}/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1d&limit=1"
                    async with session.get(url_liq, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        liq_json = await resp.json()
                        if isinstance(liq_json, list) and liq_json:
                            liq_val = float(liq_json[0].get("longShortRatio", 1.0))
                except Exception:
                    liq_val = 1.0

            raw_funding = funding_map.get(symbol, 0.0)
            # 資金費率作為反向因子：資金費率高代表多頭擁擠 -> 乘以 -1.0
            neg_funding = -1.0 * raw_funding

            return RawFeature(
                symbol=symbol,
                momentum=mom_map.get(symbol, 0.0),
                flow=flow_val,
                funding=neg_funding,
                liquidation=liq_val
            )

        tasks = [fetch_single_symbol(s) for s in symbols]
        raw_features = await asyncio.gather(*tasks)
        return list(raw_features)

    @staticmethod
    def winsorize_and_zscore(series: List[float], limits=(0.025, 0.975)) -> np.ndarray:
        """雙側去極值並計算橫截面 Z-Score"""
        arr = np.array(series, dtype=float)
        # 排除 NaN / Inf
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        if len(arr) <= 1:
            return np.zeros_like(arr)

        lower = np.quantile(arr, limits[0])
        upper = np.quantile(arr, limits[1])
        clipped = np.clip(arr, lower, upper)
        std = np.std(clipped)
        if std < 1e-12:
            return np.zeros_like(clipped)
        return (clipped - np.mean(clipped)) / std

    def compute_ranks(
        self,
        raw_features: List[RawFeature],
        capital_override: Optional[float] = None,
        leverage_override: Optional[float] = None
    ) -> UniverseRankResult:
        """計算橫截面加權總分並決定多空籃子清單（支援動態本金與槓桿覆蓋）"""
        if not raw_features:
            return UniverseRankResult(status="failed", message="無可用特徵數據")

        n = len(raw_features)
        z_mom = self.winsorize_and_zscore([x.momentum for x in raw_features])
        z_flow = self.winsorize_and_zscore([x.flow for x in raw_features])
        z_fund = self.winsorize_and_zscore([x.funding for x in raw_features])
        z_liq = self.winsorize_and_zscore([x.liquidation for x in raw_features])

        ranked_items: List[RankedItem] = []
        w_mom = self.weights.get("momentum", 0.35)
        w_flow = self.weights.get("flow", 0.35)
        w_fund = self.weights.get("funding", 0.20)
        w_liq = self.weights.get("liquidation", 0.10)

        prices = getattr(self, "latest_prices", {})

        for i, item in enumerate(raw_features):
            score = (
                w_mom * z_mom[i] +
                w_flow * z_flow[i] +
                w_fund * z_fund[i] +
                w_liq * z_liq[i]
            )
            signals = SignalZScores(
                mom_z=round(float(z_mom[i]), 2),
                flow_z=round(float(z_flow[i]), 2),
                fund_z=round(float(z_fund[i]), 2),
                liq_z=round(float(z_liq[i]), 2)
            )

            # 原始指標參數
            sym_price = prices.get(item.symbol, 0.0)
            real_fund_rate = -1.0 * item.funding # 轉回真實資金費率
            raw_metrics = RawMetrics(
                price=sym_price,
                price_change_pct=round(item.momentum, 2),
                taker_flow_ratio=round(item.flow, 3),
                funding_rate_pct=round(real_fund_rate * 100, 4),
                top_trader_ratio=round(item.liquidation, 3)
            )

            # 各分項中文語意評價
            # 1. 動能評價
            if z_mom[i] >= 1.5:
                mom_eval = "動能極強 🔥"
            elif z_mom[i] >= 0.7:
                mom_eval = "強勢上漲 📈"
            elif z_mom[i] >= -0.7:
                mom_eval = "震盪中性 ⚖️"
            elif z_mom[i] >= -1.5:
                mom_eval = "偏弱走跌 📉"
            else:
                mom_eval = "動能極弱 ❄️"

            # 2. 買賣流評價
            if z_flow[i] >= 1.5:
                flow_eval = "大戶爆買 🟢"
            elif z_flow[i] >= 0.7:
                flow_eval = "主動偏多 🔼"
            elif z_flow[i] >= -0.7:
                flow_eval = "買賣均衡 ⚖️"
            elif z_flow[i] >= -1.5:
                flow_eval = "主動偏空 🔽"
            else:
                flow_eval = "拋售沉重 🔴"

            # 3. 資金費率評價
            if real_fund_rate >= 0.0003:
                fund_eval = "嚴重過熱(看空時機) ⚠️"
            elif real_fund_rate >= 0.0001:
                fund_eval = "費率偏熱 ♨️"
            elif real_fund_rate >= -0.0001:
                fund_eval = "費率正常 ⚖️"
            elif real_fund_rate >= -0.0003:
                fund_eval = "費率偏冷 ❄️"
            else:
                fund_eval = "深度貼水(反轉看多) 💎"

            # 4. 大戶比評價
            if z_liq[i] >= 1.5:
                liq_eval = "大戶極度看多 🐂"
            elif z_liq[i] >= 0.7:
                liq_eval = "大戶偏多 🔼"
            elif z_liq[i] >= -0.7:
                liq_eval = "大戶持平 ⚖️"
            elif z_liq[i] >= -1.5:
                liq_eval = "大戶偏空 🔽"
            else:
                liq_eval = "大戶極度看空 🐻"

            evaluations = ParameterEvaluations(
                momentum_eval=mom_eval,
                flow_eval=flow_eval,
                funding_eval=fund_eval,
                liquidation_eval=liq_eval,
                overall_eval="觀望"
            )

            ranked_items.append(
                RankedItem(
                    symbol=item.symbol,
                    score=round(float(score), 4),
                    rank=0,  # 排序後賦予
                    signals=signals,
                    raw_params=raw_metrics,
                    evaluations=evaluations
                )
            )

        # 降冪排序（得分最高在前）
        ranked_items.sort(key=lambda x: x.score, reverse=True)

        for idx, item in enumerate(ranked_items):
            item.rank = idx + 1

        # 多頭權重配置 (各分配 50% 總多頭資金)
        effective_capital = float(capital_override) if capital_override is not None and float(capital_override) > 0 else self.total_capital
        effective_leverage = float(leverage_override) if leverage_override is not None and float(leverage_override) > 0 else self.leverage

        long_weight_each = 1.0 / max(self.long_count, 1)
        long_notional_each = (effective_capital * effective_leverage * 0.5) * long_weight_each

        for item in ranked_items[:self.long_count]:
            item.side = "long"
            item.target_weight = round(long_weight_each, 4)
            item.target_notional = round(long_notional_each, 2)
            item.evaluations.overall_eval = f"強烈做多 (Top {item.rank}) 🚀"

        # 空頭權重配置 (各分配 50% 總空頭資金)
        short_weight_each = 1.0 / max(self.short_count, 1)
        short_notional_each = (effective_capital * effective_leverage * 0.5) * short_weight_each

        for idx, item in enumerate(ranked_items[-self.short_count:]):
            item.side = "short"
            item.target_weight = round(short_weight_each, 4)
            item.target_notional = round(short_notional_each, 2)
            item.evaluations.overall_eval = f"強烈做空 (Bottom {idx + 1}) 🔻"

        # 中間標的評估
        for item in ranked_items[self.long_count:-self.short_count]:
            if item.score >= 0.4:
                item.evaluations.overall_eval = "偏多候選 ↗️"
            elif item.score <= -0.4:
                item.evaluations.overall_eval = "偏空候選 ↘️"
            else:
                item.evaluations.overall_eval = "中性觀望 ⚪"

        long_list = ranked_items[:self.long_count]
        short_list = ranked_items[-self.short_count:]

        return UniverseRankResult(
            version="1.0",
            universe_size=n,
            prices=getattr(self, "latest_prices", {}),
            long_list=long_list,
            short_list=short_list,
            all_rankings=ranked_items,
            status="success"
        )

    async def run_daily_pipeline(
        self,
        allowed_symbols: Optional[Set[str]] = None,
        excluded_symbols: Optional[Set[str]] = None,
        capital_override: Optional[float] = None,
        leverage_override: Optional[float] = None
    ) -> UniverseRankResult:
        """完整執行一日數據拉取與算分管線（支援白名單、避碰排除黑名單與本金/槓桿覆蓋）"""
        logger.info("開始執行 CoinFull Prime 每日量化算分管線...")
        async with aiohttp.ClientSession() as session:
            universe = await self.fetch_universe(
                session,
                allowed_symbols=allowed_symbols,
                excluded_symbols=excluded_symbols
            )
            if not universe:
                return UniverseRankResult(status="failed", message="無法獲取 Universe 79 標的")
            features = await self.fetch_features(session, universe)
            result = self.compute_ranks(features, capital_override=capital_override, leverage_override=leverage_override)

        if result.status == "success" and result.long_list and result.short_list:
            logger.info(
                f"CoinFull Prime 算分完成! Top 1 多頭: {result.long_list[0].symbol} ({result.long_list[0].score}), "
                f"Top 1 空頭: {result.short_list[-1].symbol} ({result.short_list[-1].score})"
            )
        return result
