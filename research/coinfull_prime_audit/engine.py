"""
CoinFull Prime audit engine — real-data backtest core.

Data: Binance Vision monthly klines (1d) + monthly fundingRate (8h) for 19 USDT-M perps
(2021-01-01 .. 2026-07-31). Re-implements the mechanics of
`core/services/coinfull_prime/{quant_engine,rebalancer}.py`:

    score = w_mom*Z(mom) + w_flow*Z(flow) + w_fund*Z(-funding) + w_liq*Z(liq)
    -> long top-K / short bottom-K, equal notional, 50% of capital per side,

and charges realistic costs (taker fee + slippage + funding settlement).
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 資料目錄解析順序：環境變數 COINFULL_DATA_DIR → ./research/amm/data → ~/.datacache/amm/data
_env_dir = os.environ.get("COINFULL_DATA_DIR")
_repo_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "amm", "data"))
_home_dir = os.path.expanduser("~/.datacache/amm/data")
DATA_DIR = os.path.abspath(_env_dir or (_repo_dir if os.path.isdir(_repo_dir) else _home_dir))
DEFAULT_LIMITS = (0.025, 0.975)


# ----------------------------------------------------------------------------- data
def load_panel(data_dir: str = DATA_DIR):
    frames = {}
    for f in sorted(glob.glob(os.path.join(data_dir, "ohlcv", "*_1d.csv"))):
        sym = os.path.basename(f).replace("_1d.csv", "").replace("_", "")
        d = pd.read_csv(f)
        d["dt"] = pd.to_datetime(d["timestamp_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
        frames[sym] = d.drop_duplicates("dt").set_index("dt").sort_index()

    close = pd.DataFrame({k: v["close"] for k, v in frames.items()})
    op = pd.DataFrame({k: v["open"] for k, v in frames.items()})
    high = pd.DataFrame({k: v["high"] for k, v in frames.items()})
    low = pd.DataFrame({k: v["low"] for k, v in frames.items()})
    vol = pd.DataFrame({k: v["volume"] for k, v in frames.items()})

    fund = pd.read_csv(os.path.join(data_dir, "derivatives", "funding_all.csv"))
    fund["dt"] = pd.to_datetime(fund["timestamp_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    fund_daily = fund.groupby(["dt", "symbol"])["funding_rate"].sum().unstack().reindex(close.index)

    keep = [c for c in close.columns if c in fund_daily.columns]
    close, op, high, low, vol = (x[keep] for x in (close, op, high, low, vol))
    fund_daily = fund_daily[keep].ffill(limit=2)
    return close, op, high, low, vol, fund_daily


def load_macro(data_dir: str = DATA_DIR) -> pd.DataFrame:
    fg = pd.read_csv(os.path.join(data_dir, "macro", "fear_greed_daily.csv"))
    fg["dt"] = pd.to_datetime(fg["date"]).dt.normalize()
    fg = fg.set_index("dt")[["value"]].rename(columns={"value": "fng"})
    fr = pd.read_csv(os.path.join(data_dir, "macro", "fred_daily.csv"))
    fr["dt"] = pd.to_datetime(fr["date"]).dt.normalize()
    fr = fr.set_index("dt")
    cols = [c for c in ["vix", "us_10y_yield_pct", "trade_weighted_usd_index", "sp500_index"] if c in fr.columns]
    return fg.join(fr[cols], how="outer").sort_index().ffill().reindex(fg.index.union(fr.index)).ffill()


# ------------------------------------------------------------------------- statistics
def winsor_z(x: np.ndarray, limits: Tuple[float, float] = DEFAULT_LIMITS) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    finite = np.isfinite(x)
    if finite.sum() <= 1:
        return np.zeros_like(x)
    lo, hi = np.quantile(x[finite], limits)
    c = np.clip(x, lo, hi)
    mu, sd = np.nanmean(c), np.nanstd(c)
    if sd < 1e-12:
        return np.zeros_like(x)
    return (c - mu) / sd


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 5:
        return np.nan
    ra, rb = pd.Series(a[m]).rank().values, pd.Series(b[m]).rank().values
    if np.std(ra) < 1e-12 or np.std(rb) < 1e-12:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


# ------------------------------------------------------------------------- factors
def factor_panel(close, op, high, low, vol, fund) -> Dict[str, pd.DataFrame]:
    f: Dict[str, pd.DataFrame] = {}
    f["mom1"] = close.pct_change(1) * 100.0
    f["mom3"] = close.pct_change(3) * 100.0
    f["mom7"] = close.pct_change(7) * 100.0
    f["mom30"] = close.pct_change(30) * 100.0
    f["composite"] = 0.6 * f["mom7"] + 0.4 * f["mom30"]
    f["blend"] = 0.5 * f["mom1"] + 0.3 * f["mom7"] + 0.2 * f["mom30"]
    f["rev3"] = -close.pct_change(3) * 100.0

    rng = (high - low).replace(0, np.nan)
    f["pressure"] = ((close - op) / rng).clip(-1, 1)
    vs = vol / vol.rolling(20).mean()
    f["takerproxy"] = f["pressure"] * vs.clip(0.2, 3.0)
    f["none"] = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    f["fund"] = -fund.fillna(0.0)
    f["fund_smooth"] = -fund.rolling(3).mean()
    f["liq"] = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    return f


# ------------------------------------------------------------------------- engine
@dataclass
class BT:
    long_count: int = 3
    short_count: int = 3
    capital: float = 1000.0
    leverage: float = 1.0
    weights: Dict[str, float] = field(default_factory=lambda: {"mom": 0.35, "flow": 0.35, "fund": 0.20, "liq": 0.10})
    momentum_mode: str = "mom1"
    flow_mode: str = "pressure"
    use_funding: bool = True
    funding_mode: str = "fund_smooth"
    use_liq: bool = False
    fee_bps: float = 5.0
    slip_bps: float = 2.0
    rebalance_days: int = 1
    min_order_notional: float = 5.0
    warmup: int = 35
    start: Optional[str] = None
    end: Optional[str] = None
    gross_scale: Optional[pd.Series] = None
    equity_compounding: bool = False
    vol_target_ann: Optional[float] = None
    vol_lookback: int = 14
    momentum_lag: int = 0


def _zscore_series(f: Dict[str, pd.DataFrame], cfg: BT, t: int, cols) -> pd.Series:
    w = dict(cfg.weights)
    mom = f[cfg.momentum_mode].iloc[t].values
    zm = winsor_z(mom)
    if cfg.flow_mode == "none":
        zf = np.zeros_like(zm); w["flow"] = 0.0
    else:
        zf = winsor_z(f[cfg.flow_mode].iloc[t].values); w["flow"] = cfg.weights.get("flow", 0.0)
    if cfg.use_funding:
        zfu = winsor_z(f[cfg.funding_mode].iloc[t].values)
    else:
        zfu = np.zeros_like(zm); w["fund"] = 0.0
    if cfg.use_liq:
        zl = winsor_z(f["liq"].iloc[t].values)
    else:
        zl = np.zeros_like(zm); w["liq"] = 0.0
    tot = sum(w.values()) or 1.0
    s = (w["mom"] * zm + w["flow"] * zf + w["fund"] * zfu + w["liq"] * zl) / tot
    return pd.Series(s, index=cols)


_SCORE_CACHE: Dict[tuple, pd.DataFrame] = {}


def score_matrix(f: Dict[str, pd.DataFrame], cfg: BT, cols) -> pd.DataFrame:
    """Cache the per-day cross-sectional composite score (rows=date, cols=symbol)."""
    key = (cfg.momentum_mode, cfg.flow_mode, cfg.funding_mode, cfg.use_funding, cfg.use_liq,
           tuple(sorted(cfg.weights.items())))
    if key in _SCORE_CACHE:
        return _SCORE_CACHE[key]
    arr = np.zeros((len(f[cfg.momentum_mode]), len(cols)))
    for t in range(len(arr)):
        arr[t] = _zscore_series(f, cfg, t, cols).values
    out = pd.DataFrame(arr, index=f[cfg.momentum_mode].index, columns=cols)
    _SCORE_CACHE[key] = out
    return out


def run_backtest(close, fund, f, cfg: BT, scores: Optional[pd.DataFrame] = None,
                 keep_positions: bool = False) -> Dict[str, object]:
    if scores is None:
        scores = score_matrix(f, cfg, list(close.columns))
    cols = list(close.columns)
    dates = close.index
    fwd = close.pct_change(1).shift(-1)     # r_{t -> t+1}

    first = max(cfg.warmup, int(np.searchsorted(dates, pd.Timestamp(cfg.start))) if cfg.start else cfg.warmup)
    last = len(dates) - 2
    if cfg.end:
        last = min(last, int(np.searchsorted(dates, pd.Timestamp(cfg.end))) - 1)
    cost = (cfg.fee_bps + cfg.slip_bps) / 1e4

    prev_w = np.zeros(len(cols))
    _positions: List[dict] = []
    equity = cfg.capital
    rows: List[dict] = []
    pending: Optional[np.ndarray] = None
    pending_days = 0

    for t in range(first, last):
        rebal = (pending is None) or ((t - first) % cfg.rebalance_days == 0)
        target_w = np.zeros(len(cols))
        if rebal:
            ti = t - cfg.momentum_lag
            if ti >= cfg.warmup:
                s = scores.iloc[ti]
                valid = close.iloc[t].notna().values & fwd.iloc[t].notna().values & s.notna().values
                s = s[valid]
                k_long = min(cfg.long_count, len(s) // 2)
                k_short = min(cfg.short_count, len(s) // 2)
                if k_long > 0 and k_short > 0:
                    order = s.sort_values(ascending=False)
                    for sym in order.index[:k_long]:
                        target_w[cols.index(sym)] += 0.5 * cfg.leverage / k_long
                    for sym in order.index[-k_short:]:
                        target_w[cols.index(sym)] -= 0.5 * cfg.leverage / k_short
            pending = target_w
        else:
            target_w = pending

        scale = 1.0
        if cfg.gross_scale is not None:
            key = dates[t - cfg.momentum_lag]
            if key in cfg.gross_scale.index:
                scale *= float(cfg.gross_scale.loc[key])
        if cfg.vol_target_ann is not None:
            scale *= _vol_scale(close, f, cfg, t)
        tw = target_w * scale

        cap = equity if cfg.equity_compounding else cfg.capital
        desired = tw * cap
        held = prev_w * cap
        delta = desired - held
        traded = np.where(np.abs(delta) >= cfg.min_order_notional, delta, 0.0)
        eff_notional = held + traded
        eff_w = eff_notional / cap if cap else np.zeros_like(eff_notional)

        r = np.nan_to_num(fwd.iloc[t].values, nan=0.0)
        gross = float(np.sum(eff_notional * r))
        fees = float(np.sum(np.abs(traded)) * cost)
        fd = np.nan_to_num(fund.iloc[t + 1].values, nan=0.0)
        funding = float(-np.sum(eff_notional * fd))

        pnl = gross - fees + funding
        equity += pnl
        prev_w = eff_w
        rows.append({
            "date": dates[t], "gross": gross, "fees": fees, "funding": funding, "net": pnl,
            "turnover": float(np.sum(np.abs(traded)) / cfg.capital),
            "gross_exposure": float(np.sum(np.abs(eff_notional)) / cfg.capital),
            "net_exposure": float(np.sum(eff_notional) / cfg.capital),
            "n_long": int(np.sum(eff_notional > 1)), "n_short": int(np.sum(eff_notional < -1)),
            "equity": equity,
        })
        if keep_positions:
            _positions.append({cols[i]: eff_notional[i] / cap for i in range(len(cols)) if abs(eff_notional[i]) > 1e-9})

    res = pd.DataFrame(rows).set_index("date")
    for c in ["gross", "fees", "funding", "net"]:
        res[c + "_pct"] = res[c] / cfg.capital
    res["equity"] = cfg.capital + res["net"].cumsum()
    out = {"frame": res, "cfg": cfg, "scores": scores}
    if keep_positions:
        out["positions"] = _positions
    return out


def _vol_scale(close, f, cfg: BT, t: int) -> float:
    rets = close.pct_change(1).iloc[max(0, t - cfg.vol_lookback):t + 1].values.ravel()
    rets = rets[np.isfinite(rets)]
    if len(rets) < 10:
        return 1.0
    sig = np.std(rets) * np.sqrt(365)
    if sig < 1e-6:
        return 1.0
    return float(min(2.0, cfg.vol_target_ann / sig))


# ------------------------------------------------------------------------- metrics
def perf(res: pd.DataFrame, capital: float = 1000.0) -> Dict[str, float]:
    r = res["net_pct"].values
    n = len(r)
    if n == 0:
        return {}
    ann = 365.0  # daily observations
    eq = capital + np.cumsum(res["net"].values)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    sd = np.std(r, ddof=1) if n > 1 else 0.0
    down = np.std(np.clip(r, -np.inf, 0), ddof=1) if n > 1 else 0.0
    span_days = max((res.index[-1] - res.index[0]).days, 1)
    cagr = (eq[-1] / capital) ** (365.0 / span_days) - 1 if eq[-1] > 0 else -1.0
    return {
        "days": n,
        "total_pct": (eq[-1] / capital - 1) * 100,
        "cagr_pct": cagr * 100,
        "vol_pct": sd * np.sqrt(ann) * 100,
        "sharpe": (np.mean(r) * ann) / (sd * np.sqrt(ann)) if sd > 0 else 0.0,
        "sortino": (np.mean(r) * ann) / (down * np.sqrt(ann)) if down > 0 else 0.0,
        "max_dd_pct": dd.min() * 100,
        "calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "hit_pct": float((r > 0).mean() * 100),
        "avg_bp": float(np.mean(r) * 1e4),
        "median_bp": float(np.median(r) * 1e4),
        "worst_pct": float(np.min(r) * 100),
        "best_pct": float(np.max(r) * 100),
        "gross": float(res["gross"].sum()),
        "fees": float(res["fees"].sum()),
        "funding": float(res["funding"].sum()),
        "net": float(res["net"].sum()),
        "turnover": float(res["turnover"].mean()),
    }


def yearly(res: pd.DataFrame) -> pd.DataFrame:
    out = []
    for y, g in res.groupby(res.index.year):
        r = g["net_pct"].values
        sd = np.std(r, ddof=1) if len(r) > 1 else 0.0
        out.append({
            "year": y, "days": len(g), "net_pct": r.sum() * 100,
            "gross_pct": g["gross_pct"].sum() * 100,
            "fees_pct": g["fees_pct"].sum() * 100,
            "funding_pct": g["funding_pct"].sum() * 100,
            "vol_pct": sd * np.sqrt(365) * 100,
            "sharpe": (np.mean(r) * 365) / (sd * np.sqrt(365)) if sd > 0 else 0.0,
            "hit_pct": float((r > 0).mean() * 100),
            "maxdd_pct": _maxdd(r) * 100,
        })
    return pd.DataFrame(out).set_index("year")


def _maxdd(r: np.ndarray) -> float:
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    return float((eq - peak).min())


def ic_table(f: Dict[str, pd.DataFrame], close: pd.DataFrame, start: Optional[str] = None,
             end: Optional[str] = None, horizon: int = 1) -> pd.DataFrame:
    ret = close.pct_change(horizon).shift(-horizon)
    rows = []
    for name, df in f.items():
        ics = []
        for t in range(len(close) - horizon):
            d = close.index[t]
            if start and d < pd.Timestamp(start):
                continue
            if end and d > pd.Timestamp(end):
                continue
            v = spearman(df.iloc[t].values, ret.iloc[t].values)
            if np.isfinite(v):
                ics.append(v)
        ics = np.array(ics)
        if not len(ics):
            continue
        rows.append({"factor": name, "mean_ic": ics.mean(), "ic_std": ics.std(),
                     "ic_ir": ics.mean() / ics.std() if ics.std() > 0 else np.nan,
                     "t_stat": ics.mean() / (ics.std() / np.sqrt(len(ics))) if ics.std() > 0 else np.nan,
                     "pos_rate": float((ics > 0).mean()), "n": len(ics)})
    return pd.DataFrame(rows).set_index("factor").sort_values("mean_ic", ascending=False)
