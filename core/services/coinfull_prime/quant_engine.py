"""
CoinFull Prime 核心量化引擎 (純客觀截面矩陣計算)
"""

import asyncio
import logging
import time
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

# 內建常見 TradFi（美股/韓股/指數 ETF/大宗商品）標的集合，作為網路異常或離線回測的兜底名單
DEFAULT_FALLBACK_TRADFI_SYMBOLS: Set[str] = {
    # 美股與全球個股
    "NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "AMZNUSDT", "GOOGLUSDT", "METAUSDT",
    "MSFTUSDT", "PLTRUSDT", "AMDUSDT", "INTCUSDT", "COINUSDT", "HOODUSDT",
    "MSTRUSDT", "MRVLUSDT", "DELLUSDT", "ORCLUSDT", "AVGOUSDT", "QCOMUSDT",
    "ARMUSDT", "MUUSDT", "RKLBUSDT", "AAOIUSDT", "SNDKUSDT", "SKHYNIXUSDT",
    "SKHYUSDT", "SAMSUNGUSDT", "BULLAUSDT", "CRCLUSDT", "SNXXUSDT",
    # 槓桿與指數 ETF
    "SPYUSDT", "QQQUSDT", "TQQQUSDT", "SQQQUSDT", "SOXLUSDT", "SOXSUSDT",
    "SPCXUSDT", "EWYUSDT", "EWJUSDT", "KORUUSDT",
    # 貴金屬與大宗商品（含 RWA 黃金代幣）
    "XAUUSDT", "XAGUSDT", "PAXGUSDT", "XAUTUSDT", "XAUMUSDT",
    "CLUSDT", "BZUSDT", "NATGASUSDT", "COPPERUSDT", "XPTUSDT", "XPDUSDT",
    # 傳統金融與指數代號
    "BTCDOMUSDT", "DEFIUSDT"
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
        self.exclude_tradfi = bool(self.config.get("universe", {}).get("exclude_tradfi", True))
        self._tradfi_cache: Optional[Set[str]] = None
        self._tradfi_cache_time: float = 0.0
        self.top_n = self.config.get("universe", {}).get("top_n", 79)
        self.long_count = self.config.get("portfolio", {}).get("long_count", 8)
        self.short_count = self.config.get("portfolio", {}).get("short_count", 8)
        self.total_capital = float(self.config.get("portfolio", {}).get("total_capital_usdt", 1000.0))
        self.leverage = float(self.config.get("portfolio", {}).get("leverage", 1.0))

        # 因子形式（v3.0 優化：docs/CoinFull_Prime_v3_Engineering_Spec_and_Optimization_Master.md）
        # momentum.mode: "day" = 24h 漲跌幅; "blend" = 多時距混合 (r7d, r14d, r30d)
        momentum_cfg = self.config.get("momentum", {}) or {}
        self.momentum_mode = str(momentum_cfg.get("mode", "day")).lower()
        blend_cfg = momentum_cfg.get("blend", {}) or {}
        self.momentum_blend = {
            "r1d": float(blend_cfg.get("r1d", 0.0 if "r14d" in blend_cfg else 0.5)),
            "r7d": float(blend_cfg.get("r7d", 0.3)),
            "r14d": float(blend_cfg.get("r14d", 0.0)),
            "r30d": float(blend_cfg.get("r30d", 0.2)),
        }
        self.overheat_penalty = bool(momentum_cfg.get("overheat_penalty", False))

        # funding.mode: "spot" = 單點快照; "smooth" = 最近 N 次 8 小時結算平均
        funding_cfg = self.config.get("funding", {}) or {}
        self.funding_mode = str(funding_cfg.get("mode", "spot")).lower()
        self.funding_settlements = int(funding_cfg.get("settlements", 3))
        self.annual_max_long_pct = float(funding_cfg.get("annual_max_long_pct", 0.0))
        self.annual_min_short_pct = float(funding_cfg.get("annual_min_short_pct", 0.0))

        # 非對稱空頭對沖配置 (v3.0 Opt C)
        hedge_cfg = self.config.get("hedge", {}) or {}
        self.hedge_mode = str(hedge_cfg.get("mode", "basket")).lower()
        self.short_hedge_symbol = str(hedge_cfg.get("symbol", "BTCUSDT"))
        self.short_hedge_weight = float(hedge_cfg.get("weight", 0.50))
        self.short_dual_weights = hedge_cfg.get("dual_weights", {"BTCUSDT": 0.25, "ETHUSDT": 0.25})

        # 質量與流動性初篩門檻 (v3.0 Opt D)
        univ_cfg = self.config.get("universe", {}) or {}
        self.min_24h_volume_usdt = float(univ_cfg.get("min_24h_volume_usdt", 0.0))
        self.max_7d_atr_pct = float(univ_cfg.get("max_7d_atr_pct", 0.0))

        self.latest_prices: Dict[str, float] = {}
        self.quote_volumes: Dict[str, float] = {}

        if self.momentum_mode not in ("day", "blend"):
            logger.warning(f"momentum.mode 未支援: {self.momentum_mode}，退回 day")
            self.momentum_mode = "day"
        if self.funding_mode not in ("spot", "smooth"):
            logger.warning(f"funding.mode 未支援: {self.funding_mode}，退回 spot")
            self.funding_mode = "spot"

    async def fetch_tradfi_symbols(self, session: aiohttp.ClientSession) -> Set[str]:
        """
        獲取交易所非加密 (TradFi) 合約清單（快取 1 小時）。
        包含：美股、韓股、指數 ETF、大宗商品、外匯等。
        """
        now = time.time()
        if self._tradfi_cache is not None and (now - self._tradfi_cache_time) < 3600:
            return self._tradfi_cache

        tradfi_symbols = set(DEFAULT_FALLBACK_TRADFI_SYMBOLS)
        try:
            url = f"{self.base_url}/fapi/v1/exchangeInfo"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    symbols = data.get("symbols", []) if isinstance(data, dict) else []
                    for s in symbols:
                        sym = s.get("symbol", "")
                        contract_type = str(s.get("contractType", "")).upper()
                        underlying_type = str(s.get("underlyingType", "")).upper()
                        subtypes = [str(x).upper() for x in s.get("underlyingSubType", [])]

                        # 官方判定標準：
                        # 1. contractType 為 TRADIFI_PERPETUAL
                        # 2. underlyingType 不為 COIN (如 EQUITY, KR_EQUITY, INDEX, COMMODITY, PREMARKET 等)
                        # 3. underlyingSubType 含 TRADFI
                        if (
                            contract_type == "TRADIFI_PERPETUAL"
                            or (underlying_type and underlying_type != "COIN")
                            or "TRADFI" in subtypes
                        ):
                            tradfi_symbols.add(sym)
                    self._tradfi_cache = tradfi_symbols
                    self._tradfi_cache_time = now
                    logger.info(f"成功更新 TradFi 非加密合約清單: 共 {len(tradfi_symbols)} 隻")
                    return self._tradfi_cache
        except Exception as ex:
            logger.warning(f"獲取 exchangeInfo TradFi 清單失敗，退回使用內建兜底名單 ({len(tradfi_symbols)} 隻): {ex}")

        self._tradfi_cache = tradfi_symbols
        self._tradfi_cache_time = now
        return self._tradfi_cache

    async def fetch_universe(
        self,
        session: aiohttp.ClientSession,
        allowed_symbols: Optional[Set[str]] = None,
        excluded_symbols: Optional[Set[str]] = None,
        exclude_tradfi: Optional[bool] = None
    ) -> List[str]:
        """非同步獲取 24h 報價成交額前 top_n 幣種（排除穩定幣與 TradFi，可指定支援標的白名單與外部機器人黑名單）"""
        url = f"{self.base_url}/fapi/v1/ticker/24hr"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()

        should_exclude_tradfi = self.exclude_tradfi if exclude_tradfi is None else bool(exclude_tradfi)
        tradfi_symbols: Set[str] = set()
        if should_exclude_tradfi:
            tradfi_symbols = await self.fetch_tradfi_symbols(session)

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
            if should_exclude_tradfi and s in tradfi_symbols:
                return True
            if s in clean_excluded:
                return True
            base = s[:-4] if s.endswith("USDT") else s
            if base in clean_base_excluded or base in clean_excluded:
                return True
            if should_exclude_tradfi and f"{base}USDT" in tradfi_symbols:
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
        if should_exclude_tradfi:
            filter_msg.append(f"排除 TradFi: {len(tradfi_symbols)} 標的")
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
        quote_vol_map: Dict[str, float] = {}
        try:
            async with session.get(f"{self.base_url}/fapi/v1/ticker/24hr", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ticker_data = await resp.json()
                if isinstance(ticker_data, list):
                    for item in ticker_data:
                        if isinstance(item, dict) and "symbol" in item:
                            sym = item["symbol"]
                            mom_map[sym] = float(item.get("priceChangePercent", 0.0))
                            quote_vol_map[sym] = float(item.get("quoteVolume", 0.0))
                            if item.get("lastPrice"):
                                price_map[sym] = float(item.get("lastPrice", 0.0))
            self.latest_prices = price_map
            self.quote_volumes = quote_vol_map
        except Exception as e:
            logger.warning(f"獲取動能批次失敗，將使用中性值: {e}")

        # 3 & 4. 併發抓取單幣 Taker 買賣流和大戶多空比 (控頻 Semaphore=10)
        # v2：權重為 0 的因子不再呼叫對應 API（省 2×N 次請求）；
        #     momentum=blend 改抓日線 K 線、funding=smooth 改抓最近 N 次結算
        sem = asyncio.Semaphore(10)

        w_flow = float(self.weights.get("flow", 0.0) or 0.0)
        w_liq = float(self.weights.get("liquidation", 0.0) or 0.0)
        need_flow = w_flow > 0
        need_liq = w_liq > 0
        need_klines = self.momentum_mode == "blend"
        need_funding_hist = self.funding_mode == "smooth"

        async def fetch_single_symbol(symbol: str) -> RawFeature:
            flow_val = 1.0
            liq_val = 1.0
            async with sem:
                # Taker Long/Short Ratio（flow 權重 > 0 才抓取）
                if need_flow:
                    try:
                        url_flow = f"{self.base_url}/futures/data/takerlongshortRatio?symbol={symbol}&period=1d&limit=1"
                        async with session.get(url_flow, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            flow_json = await resp.json()
                            if isinstance(flow_json, list) and flow_json:
                                flow_val = float(flow_json[0].get("buySellRatio", 1.0))
                    except Exception:
                        flow_val = 1.0

                # Top Trader Long/Short Account Ratio（liquidation 權重 > 0 才抓取）
                if need_liq:
                    try:
                        url_liq = f"{self.base_url}/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1d&limit=1"
                        async with session.get(url_liq, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            liq_json = await resp.json()
                            if isinstance(liq_json, list) and liq_json:
                                liq_val = float(liq_json[0].get("longShortRatio", 1.0))
                    except Exception:
                        liq_val = 1.0

                # 動能：blend 模式改以日線 K 線計算多時距混合 (v3.0: 支援 7d, 14d, 30d 與防夾懲罰)
                mom_val = mom_map.get(symbol, 0.0)
                if need_klines:
                    try:
                        url_kl = f"{self.base_url}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=35"
                        async with session.get(url_kl, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                            kl = await resp.json()
                        closes = [float(k[4]) for k in kl if isinstance(k, list) and len(k) > 6]
                        # 剔除尚未收盤的進行中 K 線（close_time 在未來）
                        now_ms = time.time() * 1000
                        if closes and kl and isinstance(kl[-1], list) and int(kl[-1][6]) > now_ms:
                            closes = closes[:-1]
                        if len(closes) >= 31 and closes[-2] > 0 and closes[-8] > 0 and closes[-31] > 0:
                            r1 = closes[-1] / closes[-2] - 1.0
                            r7 = closes[-1] / closes[-8] - 1.0
                            r14 = closes[-1] / closes[-15] - 1.0 if len(closes) >= 15 and closes[-15] > 0 else r7
                            r30 = closes[-1] / closes[-31] - 1.0
                            b = self.momentum_blend
                            raw_mom = (
                                b.get("r1d", 0.0) * r1 +
                                b.get("r7d", 0.0) * r7 +
                                b.get("r14d", 0.0) * r14 +
                                b.get("r30d", 0.0) * r30
                            )
                            # 短線過熱防夾懲罰 (Overheating Penalty)
                            if self.overheat_penalty and r7 > 0 and r1 > 0.65 * r7:
                                raw_mom *= 0.5
                            # 以 % 表示（與 24h priceChangePercent 同量綱）
                            mom_val = 100.0 * raw_mom
                    except Exception as e:
                        logger.debug(f"{symbol} 日線動能抓取失敗，退回 24h 漲跌幅: {e}")

                # 資金費率：smooth 模式改以最近 N 次 8 小時結算平均
                raw_funding = funding_map.get(symbol, 0.0)
                if need_funding_hist:
                    try:
                        url_fr = (
                            f"{self.base_url}/fapi/v1/fundingRate?symbol={symbol}"
                            f"&limit={max(3, self.funding_settlements)}"
                        )
                        async with session.get(url_fr, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            fr = await resp.json()
                        if isinstance(fr, list) and fr:
                            rates = [
                                float(x.get("fundingRate", 0.0))
                                for x in fr
                                if isinstance(x, dict) and x.get("fundingRate") is not None
                            ]
                            if rates:
                                raw_funding = sum(rates[-self.funding_settlements:]) / min(
                                    len(rates), self.funding_settlements
                                )
                    except Exception as e:
                        logger.debug(f"{symbol} 資金費率歷史抓取失敗，退回單點快照: {e}")

            # 資金費率作為反向因子：資金費率高代表多頭擁擠 -> 乘以 -1.0
            neg_funding = -1.0 * raw_funding

            return RawFeature(
                symbol=symbol,
                momentum=mom_val,
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

        # 多頭與空頭名額配置
        effective_capital = float(capital_override) if capital_override is not None and float(capital_override) > 0 else self.total_capital
        effective_leverage = float(leverage_override) if leverage_override is not None and float(leverage_override) > 0 else self.leverage

        # 確定對沖排除標的 (避免做多對沖標的造成自成交)
        hedge_symbols_to_exclude: Set[str] = set()
        if self.hedge_mode == "benchmark":
            hedge_symbols_to_exclude.add(self.short_hedge_symbol)
        elif self.hedge_mode == "dual_benchmark":
            hedge_symbols_to_exclude.update(self.short_dual_weights.keys())

        # 1. 篩選多頭候選 (初篩門檻：成交額與資費過熱)
        long_candidates: List[RankedItem] = []
        for item in ranked_items:
            if item.symbol in hedge_symbols_to_exclude:
                continue
            # 質量初篩：24h 成交額門檻
            if self.min_24h_volume_usdt > 0 and getattr(self, "quote_volumes", None):
                vol = self.quote_volumes.get(item.symbol, 0.0)
                if vol < self.min_24h_volume_usdt:
                    continue
            # 資費過熱過濾：年化資費上限
            if self.annual_max_long_pct > 0 and hasattr(item, "raw_params") and item.raw_params:
                ann_funding = item.raw_params.funding_rate_pct * 3 * 365
                if ann_funding > self.annual_max_long_pct:
                    continue
            long_candidates.append(item)
            if len(long_candidates) >= self.long_count:
                break

        # 若初篩後標的不足 long_count，回退補足排名前列標的（避免空名單）
        if len(long_candidates) < self.long_count:
            for item in ranked_items:
                if item.symbol in hedge_symbols_to_exclude or item in long_candidates:
                    continue
                long_candidates.append(item)
                if len(long_candidates) >= self.long_count:
                    break

        long_list = long_candidates
        long_weight_each = 1.0 / max(self.long_count, 1)
        long_notional_each = (effective_capital * effective_leverage * 0.5) * long_weight_each

        for item in long_list:
            item.side = "long"
            item.target_weight = round(long_weight_each, 4)
            item.target_notional = round(long_notional_each, 2)
            item.evaluations.overall_eval = f"強烈做多 (Top {item.rank}) 🚀"

        # 2. 確定空頭名單 (支援 benchmark 基準對沖 / dual_benchmark / basket 倒序小幣)
        short_list: List[RankedItem] = []
        if self.hedge_mode == "benchmark":
            hedge_sym = self.short_hedge_symbol
            sym_price = prices.get(hedge_sym, 0.0)
            existing = next((x for x in ranked_items if x.symbol == hedge_sym), None)
            if existing:
                hedge_item = RankedItem(
                    symbol=hedge_sym,
                    score=existing.score,
                    rank=existing.rank,
                    signals=existing.signals,
                    raw_params=existing.raw_params,
                    evaluations=ParameterEvaluations(
                        momentum_eval=existing.evaluations.momentum_eval,
                        flow_eval=existing.evaluations.flow_eval,
                        funding_eval=existing.evaluations.funding_eval,
                        liquidation_eval=existing.evaluations.liquidation_eval,
                        overall_eval=f"基準指數對沖做空 ({hedge_sym} {int(self.short_hedge_weight*100)}%) 🛡️"
                    )
                )
            else:
                hedge_item = RankedItem(
                    symbol=hedge_sym,
                    score=0.0,
                    rank=0,
                    signals=SignalZScores(),
                    raw_params=RawMetrics(price=sym_price),
                    evaluations=ParameterEvaluations(
                        overall_eval=f"基準指數對沖做空 ({hedge_sym} {int(self.short_hedge_weight*100)}%) 🛡️"
                    )
                )
            hedge_item.side = "short"
            hedge_item.target_weight = round(self.short_hedge_weight, 4)
            hedge_item.target_notional = round(effective_capital * effective_leverage * self.short_hedge_weight, 2)
            short_list = [hedge_item]

        elif self.hedge_mode == "dual_benchmark":
            for h_sym, h_weight in self.short_dual_weights.items():
                sym_price = prices.get(h_sym, 0.0)
                existing = next((x for x in ranked_items if x.symbol == h_sym), None)
                if existing:
                    h_item = RankedItem(
                        symbol=h_sym,
                        score=existing.score,
                        rank=existing.rank,
                        signals=existing.signals,
                        raw_params=existing.raw_params,
                        evaluations=ParameterEvaluations(
                            overall_eval=f"雙基準對沖做空 ({h_sym} {int(float(h_weight)*100)}%) 🛡️"
                        )
                    )
                else:
                    h_item = RankedItem(
                        symbol=h_sym,
                        score=0.0,
                        rank=0,
                        signals=SignalZScores(),
                        raw_params=RawMetrics(price=sym_price),
                        evaluations=ParameterEvaluations(
                            overall_eval=f"雙基準對沖做空 ({h_sym} {int(float(h_weight)*100)}%) 🛡️"
                        )
                    )
                h_item.side = "short"
                h_item.target_weight = round(float(h_weight), 4)
                h_item.target_notional = round(effective_capital * effective_leverage * float(h_weight), 2)
                short_list.append(h_item)

        else:
            # "basket" 模式 (倒序選取得分最低的小幣籃子)
            short_weight_each = 1.0 / max(self.short_count, 1)
            short_notional_each = (effective_capital * effective_leverage * 0.5) * short_weight_each
            for idx, item in enumerate(ranked_items[-self.short_count:]):
                item.side = "short"
                item.target_weight = round(short_weight_each, 4)
                item.target_notional = round(short_notional_each, 2)
                item.evaluations.overall_eval = f"強烈做空 (Bottom {idx + 1}) 🔻"
                short_list.append(item)

        # 3. 中間標的評估
        long_symbols = {x.symbol for x in long_list}
        short_symbols = {x.symbol for x in short_list}
        for item in ranked_items:
            if item.symbol in long_symbols or item.symbol in short_symbols:
                continue
            if item.score >= 0.4:
                item.evaluations.overall_eval = "偏多候選 ↗️"
            elif item.score <= -0.4:
                item.evaluations.overall_eval = "偏空候選 ↘️"
            else:
                item.evaluations.overall_eval = "中性觀望 ⚪"

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
        leverage_override: Optional[float] = None,
        exclude_tradfi: Optional[bool] = None
    ) -> UniverseRankResult:
        """完整執行一日數據拉取與算分管線（支援白名單、避碰排除黑名單、TradFi 過濾與本金/槓桿覆蓋）"""
        logger.info("開始執行 CoinFull Prime 每日量化算分管線...")
        async with aiohttp.ClientSession() as session:
            universe = await self.fetch_universe(
                session,
                allowed_symbols=allowed_symbols,
                excluded_symbols=excluded_symbols,
                exclude_tradfi=exclude_tradfi
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
