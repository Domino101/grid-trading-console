"""
CoinFull Prime 風險閘門 (Regime Gate)

實作 docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md 的觸發規則：

* T1 風險關閉停單：`pts == 0` → 平掉所有受管部位並停止開新倉
  （flatten 保守版 0×；soft 積極版 0.35×）
* T2 崩跌日延後換倉：BTC 最近收盤日報酬 ≤ -5% → 換倉延後 24 小時
* T5 極端擁擠保護：資金費率百分位 > 90 禁止加碼；> 95 且 pts ≤ 1 → 減半
* 加碼（04 號規格書）：pts == 2 且資金費率百分位 45–80 → 1.15×（上限 1.2×）

設計原則（與稽核報告 §10.2 一致）：
* 決策層 (`decide()`) 為純函數：輸入三個元件量測值，輸出 RegimeDecision，
  完全離線可測、不依賴網路。
* 資料層 (`collect()`) 負責自幣安合約 API 抓取日線 K 線與 8 小時資金費率，
  產出 decide() 所需的 inputs dict。
* 資料缺漏一律採「保守」處理（該元件計 0 分 + degraded 標記 + reasons 說明）。
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from .models import RegimeDecision

logger = logging.getLogger("CoinFullPrime.Regime")

BASE_URL = "https://fapi.binance.com"
BTC_SYMBOL = "BTCUSDT"


def _to_float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / Inf
            return default
        return f
    except (TypeError, ValueError):
        return default


class RegimeFilter:
    """風險閘門：pts 計算、停手/減倉/加碼決策、狀態落地"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        cfg = self.config.get("regime_gate", {}) or {}

        self.enabled: bool = bool(cfg.get("enabled", True))
        self.log_only: bool = bool(cfg.get("log_only", False))
        self.mode: str = str(cfg.get("mode", "flatten")).lower()  # flatten | soft

        # 三個元件的門檻
        self.trend_ma_days: int = int(cfg.get("trend_ma_days", 50))
        self.breadth_threshold_pct: float = float(cfg.get("breadth_threshold_pct", 30.0))
        self.funding_percentile_days: int = int(cfg.get("funding_percentile_days", 252))
        self.funding_threshold_pct: float = float(cfg.get("funding_threshold_pct", 45.0))

        # soft 模式風險關閉时的部分倉位
        self.risk_off_scale: float = float(cfg.get("risk_off_scale", 0.35))

        # 加碼條件（pts == 2 且資金費率百分位在加碼區間）
        su = cfg.get("scale_up", {}) or {}
        self.scale_up_enabled: bool = bool(su.get("enabled", True))
        self.scale_up_min_pctile: float = float(su.get("min_pctile", 45.0))
        self.scale_up_max_pctile: float = float(su.get("max_pctile", 80.0))
        self.scale_up_multiplier: float = float(su.get("multiplier", 1.15))
        self.scale_up_max_multiplier: float = float(su.get("max_multiplier", 1.2))

        # T2 崩跌日延後
        cd = cfg.get("crash_defer", {}) or {}
        self.crash_defer_enabled: bool = bool(cd.get("enabled", True))
        self.crash_defer_pct: float = float(cd.get("btc_ret_1d_pct", -5.0))
        self.crash_defer_max: int = int(cd.get("max_defers", 2))

        # T5 極端擁擠保護
        ec = cfg.get("extreme_crowding", {}) or {}
        self.crowd_no_scaleup_pctile: float = float(ec.get("no_scale_up_above_pctile", 90.0))
        self.crowd_halve_pctile: float = float(ec.get("halve_above_pctile", 95.0))

        if self.mode not in ("flatten", "soft"):
            logger.warning(f"regime_gate.mode 未支援: {self.mode}，退回 flatten")
            self.mode = "flatten"

    # ------------------------------------------------------------------
    # 決策層（純函數，離線可測）
    # ------------------------------------------------------------------

    def decide(
        self,
        btc_above_ma50: Optional[bool] = None,
        breadth_pct: Optional[float] = None,
        funding_pctile: Optional[float] = None,
        btc_ret_1d_pct: Optional[float] = None,
        btc_close: Optional[float] = None,
        btc_ma50: Optional[float] = None,
    ) -> RegimeDecision:
        """依三個元件量測值計算 pts 並產出決策。

        任一元件為 None（資料缺漏）時該元件保守計 0 分，並標記 degraded。
        """
        reasons: List[str] = []
        degraded = False

        # --- 元件 1：趨勢（BTC vs 50 日 SMA）---
        if btc_above_ma50 is None:
            trend = 0
            degraded = True
            reasons.append("趨勢元件資料缺漏（BTC 50 日均線無法計算）→ 保守計 0")
        else:
            trend = 1 if btc_above_ma50 else 0

        # --- 元件 2：廣度（宇宙站上 50 日 SMA 比例）---
        if breadth_pct is None:
            breadth = 0
            degraded = True
            reasons.append("廣度元件資料缺漏（宇宙 50 日均線覆蓋不足）→ 保守計 0")
        elif breadth_pct > self.breadth_threshold_pct:
            breadth = 1
            reasons.append(f"廣度 {breadth_pct:.1f}% > 門檻 {self.breadth_threshold_pct:.0f}%")
        else:
            breadth = 0
            reasons.append(f"廣度 {breadth_pct:.1f}% ≤ 門檻 {self.breadth_threshold_pct:.0f}%")

        # --- 元件 3：情緒/擁擠（全市場資金費率 252 日百分位）---
        if funding_pctile is None:
            funding = 0
            degraded = True
            reasons.append("資金費率元件資料缺漏（歷史不足）→ 保守計 0")
        elif funding_pctile > self.funding_threshold_pct:
            funding = 1
            reasons.append(f"資金費率百分位 {funding_pctile:.1f} > 門檻 {self.funding_threshold_pct:.0f}")
        else:
            funding = 0
            reasons.append(f"資金費率百分位 {funding_pctile:.1f} ≤ 門檻 {self.funding_threshold_pct:.0f}")

        pts = trend + breadth + funding

        # --- 規模決策（04 號規格書倉位對照表）---
        if pts == 0:
            # T1 風險關閉
            if self.mode == "flatten":
                scale = 0.0
                force_flatten = True
                allow_new_entries = False
                reasons.append("T1 風險關閉 (pts=0, flatten 保守版) → 平倉並停止下單")
            else:
                scale = self.risk_off_scale
                force_flatten = False
                allow_new_entries = True
                reasons.append(f"T1 風險關閉 (pts=0, soft 積極版) → 縮至 {self.risk_off_scale}×")
        else:
            scale = 1.0
            force_flatten = False
            allow_new_entries = True
            # 加碼：pts==2 且資金費率百分位在 [45, 80]（T5：>90 一律禁止加碼，天然被上界擋住）
            if (
                self.scale_up_enabled
                and pts == 2
                and funding_pctile is not None
                and self.scale_up_min_pctile <= funding_pctile <= self.scale_up_max_pctile
            ):
                scale = min(self.scale_up_multiplier, self.scale_up_max_multiplier)
                reasons.append(
                    f"加碼條件成立 (pts=2 且資金費率百分位 {funding_pctile:.1f} 落在 "
                    f"{self.scale_up_min_pctile:.0f}~{self.scale_up_max_pctile:.0f}) → {scale}×"
                )

        # --- T5 極端擁擠保護：>95 且 pts≤1 → 減半（不與加碼同時成立）---
        if funding_pctile is not None and funding_pctile > self.crowd_halve_pctile and pts <= 1:
            scale = min(scale, 0.5)
            reasons.append(f"T5 極端擁擠 (百分位 {funding_pctile:.1f} > {self.crowd_halve_pctile:.0f} 且 pts≤1) → 降至 0.5×")

        # --- T2 崩跌日延後 ---
        crash_defer = False
        if self.crash_defer_enabled and btc_ret_1d_pct is not None and btc_ret_1d_pct <= self.crash_defer_pct:
            crash_defer = True
            reasons.append(f"T2 崩跌日 (BTC 收盤日 {btc_ret_1d_pct:+.2f}% ≤ {self.crash_defer_pct:+.1f}%) → 換倉延後 24 小時")

        return RegimeDecision(
            enabled=self.enabled,
            log_only=self.log_only,
            mode=self.mode,
            trend_flag=trend,
            breadth_flag=breadth,
            funding_flag=funding,
            pts=pts,
            btc_close=btc_close,
            btc_ma50=btc_ma50,
            breadth_pct=breadth_pct,
            funding_pctile=funding_pctile,
            btc_ret_1d_pct=btc_ret_1d_pct,
            scale=round(scale, 4),
            allow_new_entries=allow_new_entries,
            force_flatten=force_flatten,
            crash_defer=crash_defer,
            degraded=degraded,
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # 資料層（網路抓取，僅用於 decide() 的輸入）
    # ------------------------------------------------------------------

    async def collect(
        self,
        session: aiohttp.ClientSession,
        universe_symbols: List[str],
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> Dict[str, Optional[float]]:
        """抓取閘門所需的三個元件量測值。

        回傳 dict：btc_above_ma50 / breadth_pct / funding_pctile / btc_ret_1d_pct
        （另附 btc_close / btc_ma50 供稽核展示）。
        各元件失敗時對應欄位為 None（decide() 會保守處理）。
        """
        to = timeout or aiohttp.ClientTimeout(total=15)

        btc_closes = await self._fetch_daily_closes(session, BTC_SYMBOL, limit=self.trend_ma_days + 10, timeout=to)
        btc_above_ma50: Optional[bool] = None
        btc_close: Optional[float] = None
        btc_ma50: Optional[float] = None
        btc_ret_1d_pct: Optional[float] = None
        if btc_closes and len(btc_closes) >= self.trend_ma_days:
            last = btc_closes[-1]
            ma = sum(btc_closes[-self.trend_ma_days:]) / self.trend_ma_days
            btc_close = last
            btc_ma50 = ma
            btc_above_ma50 = last > ma
            if len(btc_closes) >= 2 and btc_closes[-2] > 0:
                btc_ret_1d_pct = (btc_closes[-1] / btc_closes[-2] - 1.0) * 100.0

        breadth_pct = await self._compute_breadth(session, universe_symbols, to)

        funding_pctile = await self._compute_funding_percentile(session, universe_symbols, to)

        return {
            "btc_above_ma50": btc_above_ma50,
            "btc_close": btc_close,
            "btc_ma50": btc_ma50,
            "breadth_pct": breadth_pct,
            "funding_pctile": funding_pctile,
            "btc_ret_1d_pct": btc_ret_1d_pct,
        }

    async def collect_auto(
        self,
        universe_symbols: List[str],
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> Dict[str, Optional[float]]:
        """自建 session 的 collect 便利包裝"""
        async with aiohttp.ClientSession() as session:
            return await self.collect(session, universe_symbols, timeout=timeout)

    # ---------------- 內部抓取工具 ----------------

    @staticmethod
    async def _fetch_daily_closes(
        session: aiohttp.ClientSession,
        symbol: str,
        limit: int = 70,
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> List[float]:
        """抓取日線收盤價（剔除尚未收盤的進行中 K 線），最舊 → 最新"""
        url = f"{BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": "1d", "limit": min(max(limit, 2), 500)}
        try:
            async with session.get(url, params=params, timeout=timeout or aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        now_ms = time.time() * 1000
        closes: List[float] = []
        for k in data:
            try:
                if int(k[6]) > now_ms:  # close_time 未來 = 進行中 K 線，剔除
                    continue
                closes.append(float(k[4]))
            except (TypeError, ValueError, IndexError):
                continue
        return closes

    async def _compute_breadth(
        self,
        session: aiohttp.ClientSession,
        universe_symbols: List[str],
        to: aiohttp.ClientTimeout,
    ) -> Optional[float]:
        """宇宙中站上 50 日 SMA 的比例（%）。覆蓋率 < 50% 視為缺漏 (None)。"""
        syms = [s for s in (universe_symbols or []) if s]
        if not syms:
            return None

        sem = asyncio.Semaphore(10)

        async def one(symbol: str) -> Optional[bool]:
            async with sem:
                closes = await self._fetch_daily_closes(
                    session, symbol, limit=self.trend_ma_days + 10, timeout=to
                )
            if len(closes) < self.trend_ma_days:
                return None
            ma = sum(closes[-self.trend_ma_days:]) / self.trend_ma_days
            return closes[-1] > ma

        results = await asyncio.gather(*[one(s) for s in syms])
        valid = [r for r in results if r is not None]
        if len(valid) < max(5, int(len(syms) * 0.5)):
            logger.warning(
                f"🟡 廣度覆蓋不足：{len(valid)}/{len(syms)} 檔有效 50 日均線 → 該元件保守計 0"
            )
            return None
        above = sum(1 for r in valid if r)
        return above / len(valid) * 100.0

    async def _compute_funding_percentile(
        self,
        session: aiohttp.ClientSession,
        universe_symbols: List[str],
        to: aiohttp.ClientTimeout,
    ) -> Optional[float]:
        """全市場（宇宙）資金費率當前值在過去 N 日的百分位 (0~100)。

        定義與研究重建一致：
        * 每檔幣的「日值」= 該日 3 次 8 小時結算之和；
        * 「市場日值」= 當日有資料幣種的橫截面平均；
        * 當前值 = 每檔幣最近 3 次結算之和的橫截面平均（與完整日值同量綱，不受結算時點影響）；
        * 百分位 = 過去 funding_percentile_days 個市場日值中 ≤ 當前值的比例 × 100。
        """
        syms = [s for s in (universe_symbols or []) if s]
        if not syms:
            return None

        need_days = self.funding_percentile_days
        limit_settle = min(need_days * 3 + 10, 1000)  # 8 小時結算 = 每日 3 次

        sem = asyncio.Semaphore(10)

        async def one(symbol: str) -> Optional[List[Tuple[int, float]]]:
            url = f"{BASE_URL}/fapi/v1/fundingRate"
            async with sem:
                try:
                    async with session.get(
                        url,
                        params={"symbol": symbol, "limit": limit_settle},
                        timeout=to,
                    ) as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.json()
                except Exception:
                    return None
            if not isinstance(data, list) or not data:
                return None
            pairs: List[Tuple[int, float]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                ts = _to_float(item.get("fundingTime"))
                rate = _to_float(item.get("fundingRate"))
                if ts is None or rate is None:
                    continue
                pairs.append((int(ts), rate))
            pairs.sort(key=lambda x: x[0])
            return pairs or None

        hist = await asyncio.gather(*[one(s) for s in syms])
        valid_hist = [h for h in hist if h and len(h) >= 9]  # 至少 3 日結算才可用
        if len(valid_hist) < max(5, int(len(syms) * 0.5)):
            logger.warning(
                f"🟡 資金費率歷史覆蓋不足：{len(valid_hist)}/{len(syms)} 檔有效 → 該元件保守計 0"
            )
            return None

        # 每檔：trailing-3 結算和序列（ts → 值）
        per_symbol: List[Dict[int, float]] = []
        for pairs in valid_hist:
            t3: Dict[int, float] = {}
            for i, (ts, rate) in enumerate(pairs):
                window = pairs[max(0, i - 2): i + 1]
                t3[ts] = sum(r for _, r in window)
            per_symbol.append(t3)

        # 市場日值序列：以每日 16:00 UTC 為錨點（當日最後一次結算），取當日 16:00 前最近結算
        now = datetime.now(timezone.utc)
        anchor_days = need_days + 3
        market_daily: Dict[int, List[float]] = {}  # date(YYYYMMDD) -> per-symbol values
        for day_off in range(anchor_days, 0, -1):
            anchor_dt = (now - timedelta(days=day_off)).replace(hour=16, minute=0, second=0, microsecond=0)
            anchor_ms = int(anchor_dt.timestamp() * 1000)
            values: List[float] = []
            for t3 in per_symbol:
                best_ts, best_val = None, None
                for ts, val in t3.items():
                    if ts <= anchor_ms and (best_ts is None or ts > best_ts):
                        best_ts, best_val = ts, val
                if best_ts is not None and (anchor_ms - best_ts) <= 90 * 3600 * 1000:
                    values.append(best_val)
            if values:
                market_daily[anchor_dt.date()] = values

        dates = sorted(market_daily.keys())
        if len(dates) < min(100, need_days):
            logger.warning(f"🟡 市場資金費率日序列僅 {len(dates)} 天（<100）→ 該元件保守計 0")
            return None

        # 當前值：每檔最近 3 次結算和的橫截面平均
        current_values: List[float] = []
        for t3 in per_symbol:
            last_ts = max(t3.keys())
            if (time.time() * 1000 - last_ts) <= 90 * 3600 * 1000:  # 3 日內有結算才算
                current_values.append(t3[last_ts])
        if not current_values:
            return None
        current = sum(current_values) / len(current_values)

        # 過去 need_days 個市場日值（排除當日）的百分位
        window_days = dates[-need_days - 1:-1] if len(dates) > 1 else []
        window = [sum(market_daily[d]) / len(market_daily[d]) for d in window_days]
        if not window:
            return None
        below = sum(1 for v in window if v <= current)
        return below / len(window) * 100.0

    # ------------------------------------------------------------------
    # 一站式（service 層呼叫）
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        session: aiohttp.ClientSession,
        universe_symbols: List[str],
        timeout: Optional[aiohttp.ClientTimeout] = None,
    ) -> RegimeDecision:
        """collect + decide 一站式"""
        inputs = await self.collect(session, universe_symbols, timeout=timeout)
        return self.decide(
            btc_above_ma50=inputs.get("btc_above_ma50"),
            breadth_pct=inputs.get("breadth_pct"),
            funding_pctile=inputs.get("funding_pctile"),
            btc_ret_1d_pct=inputs.get("btc_ret_1d_pct"),
            btc_close=inputs.get("btc_close"),
            btc_ma50=inputs.get("btc_ma50"),
        )
