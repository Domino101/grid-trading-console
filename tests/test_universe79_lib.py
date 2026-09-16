# -*- coding: utf-8 -*-
"""Offline tests for the CoinFull Prime universe-79 toolchain (no network).

Covers:
  * month/URL planning
  * point-in-time top-79 universe mask (t-1 rule, stablecoin exclusion, first day)
  * volume-tiered slippage (point-in-time, monotone in volume rank)
  * tiered fee adjustment of an engine2 result (exact delta arithmetic)
  * end-to-end on a synthetic raw tree: fetch plan -> build_panel79 -> exp15 --quick
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT79 = os.path.join(os.path.dirname(HERE), "research", "coinfull_universe79")
sys.path.insert(0, ROOT79)

import universe79_lib as L  # noqa: E402


# --------------------------------------------------------------------------- planning
def test_month_range():
    m = L.month_range("2021-01", "2021-03")
    assert m == ["2021-01", "2021-02", "2021-03"]
    m = L.month_range("2025-12", "2026-01")
    assert m == ["2025-12", "2026-01"]
    assert len(L.month_range()) == 67  # 2021-01 .. 2026-07 inclusive


def test_urls():
    assert L.klines_url("BTCUSDT", "2021-01") == \
        "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1d/BTCUSDT-1d-2021-01.zip"
    assert L.funding_url("ETHUSDT", "2023-05") == \
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/ETHUSDT/ETHUSDT-fundingRate-2023-05.zip"


def test_plan_tasks_counts_and_paths(tmp_path):
    tasks = L.plan_tasks(["BTCUSDT", "ETHUSDT"], ["2021-01", "2021-02"], str(tmp_path))
    assert len(tasks) == 8
    kinds = {t["kind"] for t in tasks}
    assert kinds == {"klines", "funding"}
    assert all(os.path.isabs(t["zip"]) and os.path.isabs(t["csv"]) for t in tasks)
    kt = [t for t in tasks if t["kind"] == "klines"][0]
    assert kt["url"].endswith("BTCUSDT-1d-2021-01.zip") or kt["url"].endswith("ETHUSDT-1d-2021-01.zip")
    with pytest.raises(ValueError):
        L.plan_tasks(["A"], ["2021-01"], str(tmp_path), kinds=("nope",))


# --------------------------------------------------------------------------- universe mask
def _qv(n_syms=10, n_days=40, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n_days, freq="D")
    cols = [f"S{i:02d}USDT" for i in range(n_syms)]
    v = rng.lognormal(10, 0.5, size=(n_days, n_syms))
    # give each symbol a stable size tier
    v *= np.linspace(0.1, 10.0, n_syms)
    return pd.DataFrame(v, index=idx, columns=cols)


def test_mask_uses_t_minus_1():
    # deterministic volumes: S05 > S04 > S03 > S02 > S01 > S00 (constant over time)
    idx = pd.date_range("2023-01-01", periods=10, freq="D")
    cols = [f"S{i:02d}USDT" for i in range(6)]
    qv = pd.DataFrame(np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], (10, 1)), index=idx, columns=cols)
    mask, size = L.build_universe_mask(qv, top_n=4, exclude=())
    # day t membership must equal top-4 of day t-1 volume
    d1 = idx[5]
    assert (mask.loc[d1] == qv.shift(1).loc[d1].rank(ascending=False, method="first").le(4)).all()
    assert set(mask.columns[mask.loc[d1]]) == {"S05USDT", "S04USDT", "S03USDT", "S02USDT"}

    # knock S03's volume below S01 ONLY on day t: mask on day t unchanged, day t+1 swaps
    qv2 = qv.copy()
    qv2.loc[idx[5], "S03USDT"] = 0.5
    m2, _ = L.build_universe_mask(qv2, top_n=4, exclude=())
    assert (m2.loc[idx[5]] == mask.loc[idx[5]]).all()               # day t unaffected by day-t volume
    assert "S03USDT" not in set(m2.columns[m2.loc[idx[6]]])           # day t+1: S03 out ...
    assert "S01USDT" in set(m2.columns[m2.loc[idx[6]]])              # ... S01 in
    assert mask.iloc[0].sum() == 0                                    # first day: no t-1


def test_mask_excludes_stablecoins_and_caps():
    qv = _qv(n_syms=12)
    stab = "S00USDT"  # pretend it's the biggest — still must be excluded
    qv[stab] = 1e12
    L.STABLECOINS  # touch
    mask, size = L.build_universe_mask(qv, top_n=4, exclude={stab})
    assert not mask[stab].any()
    assert size.iloc[-1] == 4
    assert mask.iloc[0].sum() == 0  # first day: no t-1
    assert size.max() <= 4


def test_mask_top_n_series():
    qv = _qv()
    vals = np.tile([2.0, 3.0, 4.0, 5.0], len(qv) // 4 + 1)[: len(qv)]
    cap = pd.Series(vals, index=qv.index)
    mask, size = L.build_universe_mask(qv, top_n=4, exclude=(), top_n_series=cap)
    # cap pattern by day: idx%4 -> 0:2, 1:3, 2:4, 3:5
    assert size.iloc[1] == 3
    assert size.iloc[26] == 4
    assert size.iloc[-1] == 5


def test_apply_universe_masks_only_named_cells():
    qv = _qv()
    mask, _ = L.build_universe_mask(qv, top_n=4, exclude=())
    (mc,) = L.apply_universe(qv, mask=mask)
    m = mask.reindex(index=mc.index, columns=mc.columns).values
    vals = mc.values
    assert np.isfinite(vals[m]).all()          # inside universe: untouched
    assert np.isnan(vals[~m]).all()            # outside universe: NaN


# --------------------------------------------------------------------------- slippage tiers
def test_tier_slippage_point_in_time_and_ordering():
    n_days = 60
    idx = pd.date_range("2023-01-01", periods=n_days, freq="D")
    cols = [f"S{i:02d}USDT" for i in range(10)]
    qv = pd.DataFrame(1.0, index=idx, columns=cols)
    for i, c in enumerate(cols):
        qv[c] *= (i + 1)  # fixed volume ordering S09 biggest
    mask = pd.DataFrame(True, index=idx, columns=cols)
    t = 40
    slip = L.tier_slippage(qv, mask, t=t, lookback=30)
    # highest volume -> cheapest
    assert slip[cols[9]] == 2.0
    assert slip[cols[0]] == 8.0
    assert (slip.values >= 2.0).all() and (slip.values <= 8.0).all()
    # look-ahead: changing FUTURE volume must not change today's slip
    qv2 = qv.copy()
    qv2.iloc[t + 1:, 0] *= 1000  # S00 becomes the biggest from tomorrow on
    slip2 = L.tier_slippage(qv2, mask, t=t, lookback=30)
    assert (slip2 == slip).all()


def test_tier_slippage_non_universe_zero():
    idx = pd.date_range("2023-01-01", periods=40, freq="D")
    cols = ["AUSDT", "BUSDT"]
    qv = pd.DataFrame([[100.0, 10.0]] * 40, index=idx, columns=cols)
    mask = pd.DataFrame(False, index=idx, columns=cols)
    mask["AUSDT"] = True
    slip = L.tier_slippage(qv, mask, t=35)
    assert slip["BUSDT"] == 0.0
    assert slip["AUSDT"] > 0


# --------------------------------------------------------------------------- fee adjustment
def _mini_engine2_result():
    """Tiny but REAL engine2 run (2 symbols, 120 days) to test tiered_fee_adjust on real positions."""
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "research", "coinfull_prime_audit"))
    import engine as E
    import engine2 as E2
    n = 120
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(1)
    close = pd.DataFrame({
        "AAAUSDT": 100 * np.cumprod(1 + rng.normal(0, 0.02, n)),
        "BBBUSDT": 10 * np.cumprod(1 + rng.normal(0, 0.02, n)),
    }, index=idx)
    op = hi = lo = vol = close.copy()
    fund = pd.DataFrame(0.0001 * rng.normal(size=(n, 2)), index=idx, columns=close.columns)
    f = E.factor_panel(close, op, hi, lo, vol, fund)
    f["fund"] = -fund
    f["fund_smooth"] = -fund.rolling(3).mean()
    bt = E2.Backtester(close, op, hi, lo, vol, fund, f)
    r = bt.run(E2.CFG(long_count=1, short_count=1, rebalance_days=3), collect_positions=True)
    assert len(r["frame"]) > 50, "engine2 run produced too few rows"
    return r, close


def test_tiered_fee_adjust_exact_delta():
    r, close = _mini_engine2_result()
    frame = r["frame"]
    qv = close * 1000  # volume proportional to price
    mask = pd.DataFrame(True, index=qv.index, columns=qv.columns)
    adj, delta = L.tiered_fee_adjust(frame, r["positions"], qv, mask,
                                     fee_bps=5.0, slip_base_bps=2.0, capital=1000.0)
    # hand-recompute the delta from the positions
    n = len(frame)
    cols = list(qv.columns)
    W = np.zeros((n, 2))
    for t, dp in enumerate(r["positions"]):
        for i, c in enumerate(cols):
            W[t, i] = dp.get(c, 0.0)
    dw = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, 2))))
    roll = qv.rolling(30, min_periods=15).mean().shift(1).values
    dt2q = {d: i for i, d in enumerate(qv.index)}
    expect = 0.0
    for t in range(n):
        qidx = dt2q[frame.index[t]]  # frame starts at warmup -> map dates, not row numbers
        row = roll[qidx]
        if not np.isfinite(row).all():
            continue  # matches the not-enough-history guard (delta 0)
        order = np.argsort(row)
        rank = np.empty(2)
        rank[order] = [1, 2]
        pct = rank / 2
        # top (pct=1.0) >= 0.7 -> 2bp; bottom (pct=0.5) >= 0.3 -> 4bp
        slip = np.where(pct >= 0.7, 2.0, np.where(pct >= 0.3, 4.0, 8.0))
        expect += float(np.sum(dw[t] * (slip - 2.0))) / 1e4 * 1000.0
    assert abs(delta - expect) < 1e-6
    # frame consistency: net' = net - delta, delta = fees - fees'
    np.testing.assert_allclose(adj["net"].values,
                               frame["net"].values - (frame["fees"].values - adj["fees"].values),
                               atol=1e-9)
    np.testing.assert_allclose(adj["equity"].values, 1000.0 + adj["net"].cumsum().values, atol=1e-9)
    # with 2 symbols (pct 0.5 / 1.0): one at 2bp, one at 8bp -> mixed delta sign is fine, but
    # the traded one each day is the top-ranked (highest vol) => slip 2bp == base => delta >= 0 only if
    # lower-vol symbol trades; here assert simply that equity changed consistently:
    assert adj["equity"].iloc[-1] == pytest.approx(1000.0 + adj["net"].sum(), rel=1e-9)


def test_tiered_fee_adjust_no_trades_zero_delta():
    idx = pd.date_range("2023-01-01", periods=10, freq="D")
    cols = ["AUSDT", "BUSDT"]
    frame = pd.DataFrame({
        "date": idx, "gross": 0.0, "fees": 0.0, "funding": 0.0, "net": 0.0,
        "turnover": 0.0, "gross_exposure": 0.0, "net_exposure": 0.0, "scale": 1.0,
        "n_long": 0, "n_short": 0,
    }, index=idx)
    frame["net_pct"] = 0.0
    frame["equity"] = 1000.0
    qv = pd.DataFrame([[100.0, 10.0]] * 10, index=idx, columns=cols)
    mask = pd.DataFrame(True, index=idx, columns=cols)
    positions = [{c: 0.0 for c in cols} for _ in range(10)]
    adj, delta = L.tiered_fee_adjust(frame, positions, qv, mask, fee_bps=5.0, slip_base_bps=2.0)
    assert delta == 0.0
    np.testing.assert_array_equal(adj["net"].values, frame["net"].values)


# --------------------------------------------------------------------------- end-to-end (synthetic raw tree)
KLINES_HEADER = ("open_time,open,high,low,close,volume,close_time,quote_volume,trades,"
                 "taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore")
FUNDING_HEADER = "symbol,fundingTime,fundingRate"


def _make_raw_tree(raw_dir: str, n_syms: int = 8) -> None:
    """Four calendar months per symbol (Jan-Apr 2024, 121 days): klines + funding, Vision format."""
    rng = np.random.default_rng(42)
    month_starts = [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
                    pd.Timestamp("2024-03-01"), pd.Timestamp("2024-04-01")]
    months = list(zip(month_starts, month_starts[1:] + [pd.Timestamp("2024-05-01")]))
    for s in range(n_syms):
        sym = f"SYM{s}USDT"
        for (a, b) in months:
            month = a.strftime("%Y-%m")
            days = []
            t = a
            while t < b:
                o = 10 + s + rng.normal(0, 0.1)
                c = o * (1 + rng.normal(0, 0.01))
                h, l = max(o, c) * 1.01, min(o, c) * 0.99
                v = rng.lognormal(6, 0.3) * (s + 1)
                ot = int(t.timestamp() * 1000)
                days.append(f"{ot},{o:.4f},{h:.4f},{l:.4f},{c:.4f},{v:.4f},{ot+86399999},{v*c:.4f},100,{v/2:.4f},{v*c/2:.4f},0")
                t = t + pd.Timedelta(days=1)
            kdir = os.path.join(raw_dir, "klines", sym)
            os.makedirs(kdir, exist_ok=True)
            with open(os.path.join(kdir, f"{sym}-1d-{month}.csv"), "w") as fh:
                fh.write(KLINES_HEADER + "\n" + "\n".join(days) + "\n")
            fdir = os.path.join(raw_dir, "funding", sym)
            os.makedirs(fdir, exist_ok=True)
            frows = []
            t = a
            while t < b:
                for hh in (0, 8, 16):
                    frows.append(f"{sym},{int((t + pd.Timedelta(hours=hh)).timestamp()*1000)},{rng.normal(0.0001, 0.0002):.8f}")
                t = t + pd.Timedelta(days=1)
            with open(os.path.join(fdir, f"{sym}-fundingRate-{month}.csv"), "w") as fh:
                fh.write(FUNDING_HEADER + "\n" + "\n".join(frows) + "\n")


def test_end_to_end_synthetic(tmp_path, monkeypatch):
    root = str(tmp_path / "u79")
    _make_raw_tree(os.path.join(root, "raw"))
    monkeypatch.setenv("COINFULL_U79_DATA_DIR", root)

    # builder
    r1 = subprocess.run(
        [sys.executable, os.path.join(ROOT79, "build_panel79.py"), "--top-n", "5"],
        capture_output=True, text=True, cwd=ROOT79, timeout=300)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    pdir = os.path.join(root, "panel")
    for fn in ["close.csv", "fund_daily.csv", "universe_mask.csv", "universe_size.csv",
               "top40_mask.csv", "quote_volume.csv", "meta.json"]:
        assert os.path.isfile(os.path.join(pdir, fn)), fn
    mask = pd.read_csv(os.path.join(pdir, "universe_mask.csv"), index_col=0)
    assert mask.shape[1] == 8
    assert (mask.iloc[1:] <= 5).all().all()

    # runner (quick = packs FULL + costs only)
    r2 = subprocess.run(
        [sys.executable, os.path.join(ROOT79, "exp15_universe79.py"), "--quick"],
        capture_output=True, text=True, cwd=ROOT79, timeout=600)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    out = r2.stdout
    for section in ["0. DATA", "1. PACKS", "5. COST SENSITIVITY"]:
        assert section in out, section
    # the as-coded pack must have run (it trades daily)
    assert "as-coded" in out
