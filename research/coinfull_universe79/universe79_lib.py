# -*- coding: utf-8 -*-
"""CoinFull Prime — top-79 live-universe backtest: shared pure helpers.

Everything here is network-free and unit-testable:
  * month/URL planning for the Binance Vision fetcher
  * the daily point-in-time top-79 universe mask (t-1 quote volume)
  * volume-tiered slippage schedule (point-in-time, no look-ahead)
  * post-hoc tiered-fee adjustment of an engine2 result

Look-ahead rules (see docs/CoinFull_Universe79_Backtest_Plan.md):
  * universe membership on day t uses ONLY day t-1's quote volume
  * tier slippage on day t uses the trailing-30d avg quote volume through t-1
  * factors/scores use closes <= t; execution at t close; returns t -> t+1
  * funding charged on positions held through the t+1 settlement (engine convention)
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- constants
# Production v1 config (config/coinfull_prime/strategy_config.yaml) exclude list,
# plus USD1USDT (World Liberty Financial, listed 2025) and USDEUSDT kept as in prod.
STABLECOINS: frozenset = frozenset({
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "EURUSDT",
    "BUSDUSDT", "AEURUSDT", "USDEUSDT", "BFDUSDUSDT", "USD1USDT",
})

VISION_BASE = "https://data.binance.vision/data/futures/um"
START_MONTH = "2021-01"
END_MONTH = "2026-07"
TOP_N = 79
GATE_TOP_N = 40          # production gate uses the top-40-by-volume universe

# default data root (outside the repo; gitignored anyway)
_DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".datacache", "coinfull_universe79")
DATA_ROOT = os.environ.get("COINFULL_U79_DATA_DIR") or _DEFAULT_ROOT


def data_root() -> str:
    return os.environ.get("COINFULL_U79_DATA_DIR") or _DEFAULT_ROOT


# ---------------------------------------------------------------- months / urls
def month_range(start: str = START_MONTH, end: str = END_MONTH) -> List[str]:
    """Inclusive list of 'YYYY-MM' strings from start to end."""
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out, y, m = [], y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def klines_url(sym: str, month: str) -> str:
    return f"{VISION_BASE}/daily/klines/{sym}/1d/{sym}-1d-{month}.zip"


def funding_url(sym: str, month: str) -> str:
    return f"{VISION_BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{month}.zip"


def symbols_listing_url() -> str:
    """Directory listing of every USDT-M perp that ever had Vision data (incl. delisted)."""
    return f"{VISION_BASE}/daily/klines/"


def plan_tasks(symbols: Sequence[str],
               months: Sequence[str],
               root: str,
               kinds: Tuple[str, ...] = ("klines", "funding")) -> List[Dict[str, str]]:
    """Pure task planner for the fetcher: one task per (kind, symbol, month)."""
    tasks = []
    for sym in symbols:
        for month in months:
            for kind in kinds:
                if kind == "klines":
                    url, ext_dir = klines_url(sym, month), "klines"
                elif kind == "funding":
                    url, ext_dir = funding_url(sym, month), "funding"
                else:
                    raise ValueError(kind)
                d = os.path.join(root, "raw", ext_dir, sym)
                tasks.append({
                    "kind": kind, "symbol": sym, "month": month, "url": url,
                    "zip": os.path.join(d, f"{os.path.basename(url)}"),
                    "csv": os.path.join(d, f"{os.path.basename(url)[:-4]}.csv"),
                })
    return tasks


# ---------------------------------------------------------------- universe mask
def build_universe_mask(quote_volume: pd.DataFrame,
                        top_n: int = TOP_N,
                        exclude: Sequence[str] = STABLECOINS,
                        top_n_series: Optional[pd.Series] = None) -> Tuple[pd.DataFrame, pd.Series]:
    """Daily tradable-universe mask, point-in-time.

    mask[t, s] = True  <=>  s is among the top `top_n` non-stable symbols by
    quote volume on day t-1 (the last fully settled day before t).

    Returns (mask, size) where size[t] = number of symbols in the universe on day t.
    The first row is always all-False (no t-1 available).
    """
    qv = quote_volume.copy()
    for c in exclude:
        if c in qv.columns:
            qv[c] = np.nan
    qv_prev = qv.shift(1)                                  # <- t-1, no look-ahead
    ranks = qv_prev.rank(axis=1, ascending=False, method="first")
    if top_n_series is not None:
        cap_arr = top_n_series.reindex(ranks.index).fillna(float(top_n)).values[:, None]
    else:
        cap_arr = np.full((len(ranks), 1), float(top_n))
    mask = (ranks.values <= cap_arr) & qv_prev.notna().values
    mask = pd.DataFrame(mask, index=qv.index, columns=qv.columns)
    size = mask.sum(axis=1)
    mask.iloc[0] = False
    return mask, size


def apply_universe(*dfs: pd.DataFrame, mask: pd.DataFrame) -> List[pd.DataFrame]:
    """Mask panels to the tradable universe (non-universe cells -> NaN)."""
    out = []
    for df in dfs:
        m = mask.reindex(index=df.index, columns=df.columns)
        out.append(df.where(m))
    return out


# ---------------------------------------------------------------- cost tiers
def _slip_from_row(row: np.ndarray, univ: np.ndarray,
                   tiers: Sequence[Tuple[float, float]] = ((0.70, 2.0), (0.30, 4.0), (0.0, 8.0))) -> np.ndarray:
    """Fast per-row slippage from a precomputed mean-volume row and universe row."""
    valid = univ & np.isfinite(row)
    out = np.zeros(len(row))
    if valid.sum() == 0:
        return out
    vals = row[valid]
    # rank pct within the universe (1.0 = highest volume)
    order = np.argsort(vals, kind="mergesort")
    rank_pos = np.empty(len(vals), dtype=float)
    rank_pos[order] = np.arange(1, len(vals) + 1)
    pct = rank_pos / len(vals)
    slip = np.full(len(vals), float(tiers[-1][1]))
    for thr, bps in reversed(tiers):          # lower thresholds override
        slip[pct >= thr] = bps
    out[valid] = slip
    return out


def tier_slippage(qv: pd.DataFrame,
                  mask: pd.DataFrame,
                  t: int,
                  lookback: int = 30,
                  min_periods: int = 15,
                  tiers: Sequence[Tuple[float, float]] = ((0.70, 2.0), (0.30, 4.0), (0.0, 8.0))) -> pd.Series:
    """Per-symbol slippage (bps) on day index t, point-in-time (one-shot/test form).

    Ranks each universe member by trailing-`lookback`-day mean quote volume
    computed through t-1 (inclusive); the cheapest tier applies to the highest-volume
    names. Default schedule: top-30% by volume -> 2bp, next 40% -> 4bp, bottom 30% -> 8bp.
    Non-universe symbols get 0.0 (they are never traded).
    """
    roll = qv.rolling(lookback, min_periods=min_periods).mean().shift(1)  # through t-1
    row = roll.iloc[t].values.astype(float)
    univ = mask.reindex(index=qv.index, columns=qv.columns).iloc[t].values.astype(bool)
    return pd.Series(_slip_from_row(row, univ, tiers), index=qv.columns)


def tiered_fee_adjust(frame: pd.DataFrame,
                      positions: List[dict],
                      qv: pd.DataFrame,
                      mask: pd.DataFrame,
                      fee_bps: float,
                      slip_base_bps: float,
                      capital: float = 1000.0,
                      lookback: int = 30,
                      tiers: Sequence[Tuple[float, float]] = ((0.70, 2.0), (0.30, 4.0), (0.0, 8.0)),
                      ) -> Tuple[pd.DataFrame, float]:
    """Reprice an engine2 result's fees with volume-tiered slippage.

    `positions` is the per-day {symbol: signed weight} list from
    Backtester.run(..., collect_positions=True).  traded_t[i] = |w_t[i]-w_{t-1}[i]|,
    which reproduces the engine's |traded_w| exactly (w_t = w_{t-1} + traded_w_t).

    Returns (adjusted_frame, total_delta) where total_delta = tiered_fees - flat_fees
    (a positive delta *reduces* net PnL).
    """
    dates = frame.index
    n = len(dates)
    cols = list(qv.columns)
    idx = {c: i for i, c in enumerate(cols)}
    W = np.zeros((n, len(cols)))
    for t, daypos in enumerate(positions):
        for sym, wv in daypos.items():
            if sym in idx and wv is not None and np.isfinite(wv):
                W[t, idx[sym]] = float(wv)
    dw = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, len(cols)))))  # |traded_w| per day

    roll = qv.rolling(lookback, min_periods=15).mean().shift(1).values  # through t-1
    m = mask.reindex(index=qv.index, columns=qv.columns)
    mask_vals = m.values
    date_to_qidx = {d: i for i, d in enumerate(qv.index)}
    delta = np.zeros(n)
    for t in range(n):
        d = dates[t]
        qidx = date_to_qidx.get(d)
        if qidx is None or qidx >= len(roll):
            continue
        univ = mask_vals[qidx]
        if not univ.any():
            continue
        if not np.isfinite(roll[qidx][univ]).any():
            continue  # not enough volume history yet -> keep base slippage (delta 0)
        slip = _slip_from_row(roll[qidx], univ, tiers)
        delta[t] = float(np.sum(dw[t] * (slip - slip_base_bps))) / 1e4 * capital

    out = frame.copy()
    out["fees"] = out["fees"] - delta
    out["net"] = out["net"] - delta
    out["net_pct"] = out["net"] / capital
    out["equity"] = capital + out["net"].cumsum()
    return out, float(delta.sum())


# ---------------------------------------------------------------- data loading
def load_panel79(root: str) -> Dict[str, object]:
    """Load the panel produced by build_panel79.py. Pure read, no computation."""
    p = os.path.join(root, "panel")

    def _read(name):
        df = pd.read_csv(os.path.join(p, name), index_col=0)
        df.index = pd.to_datetime(df.index)
        return df

    close = _read("close.csv")
    fund = _read("fund_daily.csv")
    mask = _read("universe_mask.csv").astype(bool)
    size = _read("universe_size.csv").squeeze()
    try:
        qv = _read("quote_volume.csv")
    except FileNotFoundError:
        qv = close  # fallback: never used for the headline runs
    return {"close": close, "fund": fund, "mask": mask, "size": size, "qv": qv,
            "dates": close.index, "symbols": list(close.columns)}
