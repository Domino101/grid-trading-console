# -*- coding: utf-8 -*-
"""exp15: CoinFull Prime — full top-79 live-universe backtest.

Re-runs the headline experiments of the 19-coin audit (exp12 recipe, engine2) on the
true production universe: daily top-79 by t-1 quote volume (stablecoins excluded),
point-in-time, with delisted symbols handled via NaN masking.

Sections:
  0. data summary
  1. packs: as-coded v1 / v2 / v2+T1 / +vol / +disp / conservative / aggressive (FULL/IS/OOS)
  2. rebalance-frequency scan (v2 + conservative gate)
  3. weekday-phase scan (weekly, v2 + conservative gate)
  4. basket-width K scan
  5. cost sensitivity: flat fee/slip grid + volume-tiered slippage
  6. gate-state performance (by gate value and by pts)
  7. 19-coin vs 79-coin comparison

Usage:  python exp15_universe79.py [--quick]
        --quick runs packs (FULL only) + costs (smoke / subsample)
Redirect stdout to keep a copy:  python exp15_universe79.py > exp15_out.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "coinfull_prime_audit"))
import universe79_lib as L  # noqa: E402
import engine as E  # noqa: E402
import engine2 as E2  # noqa: E402

IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")

# 19-coin reference numbers (research/coinfull_prime_audit/exp12_out.txt, FULL;
# capital $1,000, fee 5bp + slip 2bp)
REF19 = {
    "0. as-coded v1 (K=8, 1d, mom+flow+fund)": dict(cagr=-100.0, sharpe=-1.21, dd=-133.3, turn=1.038, fee=1435, net=-1188),
    "1. v2 no gate": dict(cagr=16.9, sharpe=1.02, dd=-18.3, turn=0.140, fee=194, net=1332),
    "2. v2 + T1": dict(cagr=18.8, sharpe=1.41, dd=-15.3, turn=0.113, fee=156, net=1541),
    "5. conservative": dict(cagr=20.8, sharpe=1.86, dd=-8.6, turn=0.109, fee=151, net=1776),
    "6. aggressive": dict(cagr=24.8, sharpe=1.75, dd=-12.3, turn=0.162, fee=225, net=2322),
}


def build_gates(close: pd.DataFrame, fund: pd.DataFrame, m40: pd.DataFrame) -> Dict[str, pd.Series]:
    """Production-faithful regime gate on the top-40-by-volume universe (same math as exp12)."""
    c = close.where(m40)
    f = fund.where(m40)
    btc_col = "BTCUSDT" if "BTCUSDT" in close.columns else close.mean().abs().idxmax()
    btc = c[btc_col] if m40[btc_col].notna().any() else close[btc_col]
    vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
    disp7 = c.pct_change(7).std(axis=1) * 100.0
    mkt_fund = f.mean(axis=1)
    fund_pct = mkt_fund.rolling(252, min_periods=126).rank(pct=True) * 100.0
    breadth50 = (c > c.rolling(50).mean()).mean(axis=1) * 100.0
    btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
    pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
    ro = (pts == 0)
    vol_q90 = vol14.rolling(252, min_periods=126).quantile(0.90)
    disp_q95 = disp7.rolling(252, min_periods=126).quantile(0.95)
    vol_off = (vol14 > vol_q90).fillna(False)
    disp_off = (disp7 > disp_q95).fillna(False)

    out = {
        "T1": pd.Series(np.where(ro.fillna(False), 0.0, 1.0), index=close.index),
        "T1+vol": pd.Series(np.where(ro.fillna(False) | vol_off, 0.0, 1.0), index=close.index),
        "T1+disp": pd.Series(np.where(ro.fillna(False) | disp_off, 0.0, 1.0), index=close.index),
        "cons": pd.Series(np.where(ro.fillna(False) | vol_off | disp_off, 0.0, 1.0), index=close.index),
        "agg": pd.Series(np.where(ro.fillna(False) | vol_off | disp_off, 0.0,
                                  np.where(pts == 2, 1.15, 1.0)), index=close.index),
    }
    out["_pts"] = pts.fillna(0)
    return out


def show(name: str, m: dict, tag: str = "FULL") -> None:
    if not np.isfinite(m.get("cagr_pct", np.nan)):
        print(f"  {tag}  {name:38s} (period not in dataset)")
        return
    print(f"  {tag}  {name:38s} cagr={m['cagr_pct']:7.1f}%  vol={m['vol_pct']:5.1f}%  sharpe={m['sharpe']:5.2f}  "
          f"maxdd={m['max_dd_pct']:7.1f}%  calmar={m['calmar']:5.2f}  hit={m['hit_pct']:4.1f}%  "
          f"turn={m['turnover']:.3f}  fee=${m['fees']:6.0f}  fund=${m['funding']:6.0f}  net=${m['net']:7.0f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    root = L.data_root()
    P = L.load_panel79(root)
    close, fund, mask, size, qv = P["close"], P["fund"], P["mask"], P["size"], P["qv"]
    dates = P["dates"]

    # --- extra OHLCV panels (optional; needed for the takerproxy flow factor)
    pdir = os.path.join(root, "panel")

    def _opt(name):
        try:
            df = pd.read_csv(os.path.join(pdir, name), index_col=0)
            df.index = pd.to_datetime(df.index)
            return df
        except FileNotFoundError:
            return None

    op, hi, lo, vol = _opt("open.csv"), _opt("high.csv"), _opt("low.csv"), _opt("volume.csv")

    # universe-masked inputs (non-universe -> NaN): selection, cross-sectional z-scores
    # and returns all stay strictly inside the tradable set; delisted symbols drop out
    # automatically when their bars end (px/fwd -> NaN -> excluded, positions close).
    mc = close.where(mask)
    mo = op.where(mask) if op is not None else mc
    mh = hi.where(mask) if hi is not None else mc
    ml = lo.where(mask) if lo is not None else mc
    mv = vol.where(mask) if vol is not None else mc
    mf = fund.where(mask)

    f = E.factor_panel(mc, mo, mh, ml, mv, mf)
    # engine.py's factor_panel fills funding gaps with 0 — that would leak ~300
    # non-universe symbols into the cross-sectional z-score. Re-derive the funding
    # factors with NaN outside the universe (winsor_z skips NaN, universe-only z).
    f["fund"] = -mf
    f["fund_smooth"] = -mf.rolling(3).mean()
    bt = E2.Backtester(mc, mo, mh, ml, mv, mf, f)

    # top-40 mask for the gate (production regime universe)
    m40f = _opt("top40_mask.csv")
    m40 = m40f.astype(bool) if m40f is not None else L.build_universe_mask(qv, top_n=L.GATE_TOP_N,
                                                                          exclude=L.STABLECOINS)[0]
    gates = build_gates(close, fund, m40)
    pts = gates["_pts"]

    meta_p = os.path.join(pdir, "meta.json")
    meta = json.loads(open(meta_p).read()) if os.path.isfile(meta_p) else {}

    def run_pack(cfg: dict, collect: bool = False, start=None, end=None) -> "tuple[dict, dict]":
        if end and pd.Timestamp(end) < dates[0]:
            return {"cagr_pct": np.nan, "vol_pct": np.nan, "sharpe": np.nan, "max_dd_pct": np.nan,
                    "calmar": np.nan, "hit_pct": np.nan, "turnover": np.nan, "fees": 0.0, "funding": 0.0, "net": 0.0}, {}
        c = {**cfg, "start": start, "end": end}
        r = bt.run(E2.CFG(**c), collect_positions=collect)
        return E.perf(r["frame"], 1000.0), r

    def _fmt(m: dict, w: int = 7) -> str:
        if not np.isfinite(m.get("cagr_pct", np.nan)):
            return f"{'N/A':>{w}}   N/A"
        return f"{m['cagr_pct']:{w}.1f}% {m['sharpe']:5.2f}"


    # ---------------------------------------------------------------- 0. data
    print("=" * 158)
    print(f"0. DATA  symbols={meta.get('n_symbols', close.shape[1])}  days={len(dates)} "
          f"({dates[0].date()} .. {dates[-1].date()})  top_n={meta.get('top_n', L.TOP_N)}  "
          f"stablecoins_excluded={len(L.STABLECOINS)}")
    us = size.groupby(size.index.year).agg(["min", "median", "max"]).round(1)
    print(us.to_string())

    # ---------------------------------------------------------------- 1. packs
    V2 = dict(long_count=5, short_count=5, rebalance_days=7,
              weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
              momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth",
              no_trade_band=0.01)
    PACKS = [
        ("0. as-coded v1 (K=8, 1d, mom+flow+fund)",
         dict(long_count=8, short_count=8, rebalance_days=1,
              weights={"mom": .35, "flow": .35, "fund": .20, "liq": .10},
              momentum_mode="mom1", flow_mode="takerproxy", funding_mode="fund", no_trade_band=0.0)),
        ("1. v2 no gate", dict(V2)),
        ("2. v2 + T1", dict(V2, gate=gates["T1"])),
        ("3. v2 + T1 + vol", dict(V2, gate=gates["T1+vol"])),
        ("4. v2 + T1 + disp", dict(V2, gate=gates["T1+disp"])),
        ("5. conservative", dict(V2, gate=gates["cons"])),
        ("6. aggressive", dict(V2, gate=gates["agg"])),
    ]
    cons_run = None
    print("\n" + "=" * 158)
    print("1. PACKS (capital $1,000; fee 5bp + slip 2bp; gate universe = top-40 by t-1 volume)")
    for name, cfg in PACKS:
        m_full, r_full = run_pack(cfg, collect=name.startswith("5."))
        show(name, m_full, "FULL")
        if name.startswith("5."):
            cons_run = r_full
        if not args.quick:
            m_is, _ = run_pack(cfg, start=IS[0], end=IS[1])
            m_oos, _ = run_pack(cfg, start=OOS[0], end=OOS[1])
            show("", m_is, "IS  ")
            show("", m_oos, "OOS ")

    # ---------------------------------------------------------------- 2. freq scan
    if not args.quick:
        print("\n" + "=" * 158)
        print("2. REBALANCE-FREQUENCY SCAN (v2 + conservative gate)")
        print(f"  {'reb':>4} | {'FULL cagr':>9} {'sh':>6} {'dd':>7} | {'IS cagr':>8} {'sh':>6} | {'OOS cagr':>8} {'sh':>6} {'dd':>7}")
        for rb in [1, 2, 3, 4, 5, 6, 7, 8, 10, 14, 28]:
            cfg = {**V2, "rebalance_days": rb, "gate": gates["cons"]}
            m_f, _ = run_pack(cfg)
            m_i, _ = run_pack(cfg, start=IS[0], end=IS[1])
            m_o, _ = run_pack(cfg, start=OOS[0], end=OOS[1])
            print(f"  {rb:>4} | {m_f['cagr_pct']:8.1f}% {m_f['sharpe']:6.2f} {m_f['max_dd_pct']:7.1f} | "
                  f"{_fmt(m_i, 7)} | {_fmt(m_o, 7)} {m_o['max_dd_pct']:7.1f}")

    # ---------------------------------------------------------------- 3. weekday phase
    if not args.quick:
        print("\n" + "=" * 158)
        print("3. WEEKDAY-PHASE SCAN (weekly rebalance on weekday w; production v2 = Thursday=3)")
        wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for w in range(7):
            allow = pd.Series(dates.weekday == w, index=dates)
            cfg = {**V2, "rebalance_days": 7, "gate": gates["cons"], "rebalance_allow": allow}
            m_f, _ = run_pack(cfg)
            m_i, _ = run_pack(cfg, start=IS[0], end=IS[1])
            m_o, _ = run_pack(cfg, start=OOS[0], end=OOS[1])
            mark = " <- prod" if w == 3 else ""
            print(f"  {wd_names[w]:>3}{mark:8s} FULL cagr={m_f['cagr_pct']:7.1f}% sh={m_f['sharpe']:5.2f} "
                  f"dd={m_f['max_dd_pct']:6.1f}% | IS {_fmt(m_i, 7)} | OOS {_fmt(m_o, 7)}")

    # ---------------------------------------------------------------- 4. K scan
    if not args.quick:
        print("\n" + "=" * 158)
        print("4. BASKET-WIDTH K SCAN (v2 + conservative gate, 7d)")
        for k in [3, 4, 5, 6, 8]:
            cfg = {**V2, "long_count": k, "short_count": k, "gate": gates["cons"]}
            m_f, _ = run_pack(cfg)
            m_i, _ = run_pack(cfg, start=IS[0], end=IS[1])
            m_o, _ = run_pack(cfg, start=OOS[0], end=OOS[1])
            print(f"  K={k}  FULL cagr={m_f['cagr_pct']:7.1f}% sh={m_f['sharpe']:5.2f} dd={m_f['max_dd_pct']:6.1f}% "
                  f"turn={m_f['turnover']:.3f} | IS {_fmt(m_i, 7)} | OOS {_fmt(m_o, 7)}")


    # ---------------------------------------------------------------- 5. costs
    print("\n" + "=" * 158)
    print("5. COST SENSITIVITY (v2 + conservative gate, FULL)")
    for fee, slip in [(3.0, 1.0), (5.0, 2.0), (10.0, 5.0), (15.0, 8.0)]:
        m, _ = run_pack({**V2, "fee_bps": fee, "slip_bps": slip, "gate": gates["cons"]})
        print(f"  fee={fee:4.0f}bp slip={slip:4.0f}bp  cagr={m['cagr_pct']:7.1f}%  sh={m['sharpe']:5.2f}  "
              f"dd={m['max_dd_pct']:6.1f}%  net=${m['net']:7.0f}  fee=${m['fees']:6.0f}")
    if cons_run is not None and cons_run.get("positions") is not None:
        adj, delta = L.tiered_fee_adjust(cons_run["frame"], cons_run["positions"], qv, mask,
                                         fee_bps=5.0, slip_base_bps=2.0, capital=1000.0)
        m = E.perf(adj, 1000.0)
        m0 = E.perf(cons_run["frame"], 1000.0)
        print(f"  tiered slippage (2/4/8bp by 30d vol rank)  cagr={m['cagr_pct']:7.1f}%  sh={m['sharpe']:5.2f}  "
              f"dd={m['max_dd_pct']:6.1f}%  net=${m['net']:7.0f}  (Δfee vs flat-2bp: ${delta:+.0f}; "
              f"flat baseline net=${m0['net']:.0f})")

    # ---------------------------------------------------------------- 6. gate state
    if not args.quick and cons_run is not None:
        cf_ = cons_run["frame"]
        print("\n" + "=" * 158)
        print("6. GATE-STATE PERFORMANCE (conservative pack, FULL)")
        g = cf_["scale"]  # no vol-target in this pack => scale == gate value
        print("  by gate value:")
        for gv in sorted(g.unique()):
            sel = cf_[g == gv]
            r = sel["net_pct"].values
            print(f"    gate={gv:4.2f}  days={len(sel):4d} ({len(sel)/len(cf_)*100:4.1f}%)  "
                  f"ann={r.mean()*365*100:7.1f}%  mean_daily={r.mean()*1e4:6.2f}bp")
        print("  by pts (0..3):")
        p = pts.reindex(cf_.index).fillna(0).astype(int)
        for pv in sorted(p.unique()):
            sel = cf_[p == pv]
            r = sel["net_pct"].values
            if len(r):
                print(f"    pts={pv}  days={len(sel):4d}  ann={r.mean()*365*100:7.1f}%  "
                      f"mean_daily={r.mean()*1e4:6.2f}bp")
            else:
                print(f"    pts={pv}  days=0")

    # ---------------------------------------------------------------- 7. comparison
    print("\n" + "=" * 158)
    print("7. 19-COIN (proxy) vs 79-COIN (true universe) — FULL, fee 5bp + slip 2bp")
    print(f"  {'pack':38s} | {'19c cagr':>9} {'19c sh':>7} | {'79c cagr':>9} {'79c sh':>7}")
    pcfg = {name: cfg for name, cfg in PACKS}
    for name, ref in REF19.items():
        m, _ = run_pack(pcfg[name])
        print(f"  {name:38s} | {ref['cagr']:8.1f}% {ref['sharpe']:7.2f} | {m['cagr_pct']:8.1f}% {m['sharpe']:7.2f}")

    print("\n" + "=" * 158)
    print("caveats: universe = top-79 by t-1 quote volume (stablecoins excluded); gate computed on top-40;")
    print("funding day = UTC date of fundingTime (19-coin convention); delisted names auto-close at last bar;")
    print("first ~6 months of gated packs: fund_pct warm-up forces gate off (same as the 19-coin run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
