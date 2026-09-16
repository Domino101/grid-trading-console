# -*- coding: utf-8 -*-
"""exp11: 最佳規避規則組合、穩健性（門檻擾動）、以及「降低下單次數」的成本控制規則。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import engine as E
import engine2 as E2

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
btc = close["BTCUSDT"]
IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")

V2 = dict(long_count=5, short_count=5, rebalance_days=7,
          weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")

vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
disp7 = close.pct_change(7).std(axis=1) * 100.0
mkt_fund = fund.mean(axis=1)
fund_pct = mkt_fund.rolling(252, min_periods=126).rank(pct=True) * 100.0
breadth50 = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
ro = (pts == 0)

MASK = {}


def q(series, p):
    key = (id(series), p)
    if key not in MASK:
        MASK[key] = series.rolling(252, min_periods=126).quantile(p)
    return MASK[key]


def mk_gate(parts):
    """parts: list of (mask_series(bool), scale_when_true, scale_when_false)"""
    s = pd.Series(1.0, index=close.index)
    for mask, tv, fv in parts:
        s = s * pd.Series(np.where(mask.fillna(False).values, tv, fv), index=close.index)
    return s


def rep(tag, cfg, score=None):
    bt = E2.Backtester(close, op, high, low, vol, fund, f)
    r = bt.run(E2.CFG(**cfg))
    mf = E.perf(r["frame"])
    r2 = bt.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))
    mo = E.perf(r2["frame"])
    print(f"{tag:46s} FULL cagr={mf['cagr_pct']:6.1f}% sh={mf['sharpe']:5.2f} dd={mf['max_dd_pct']:7.1f}% "
          f"turn={mf['turnover']:.3f} fee=${mf['fees']:5.0f} | OOS cagr={mo['cagr_pct']:6.1f}% sh={mo['sharpe']:5.2f} "
          f"dd={mo['max_dd_pct']:7.1f}% turn={mo['turnover']:.3f}")
    return mf, mo


print("############ A. T1 + 波動/分散 規避規則的組合 ############")
t1 = mk_gate([(ro, 0.0, 1.0)])
for pv in (0.80, 0.85, 0.90, 0.95):
    for act in (0.0, 0.5):
        g = mk_gate([(ro, 0.0, 1.0), (vol14 > q(vol14, pv), act, 1.0)])
        rep(f"T1 + vol14>q{int(pv*100)} → {act}", {**V2, "gate": g})
for pd_ in (0.90, 0.95, 0.98):
    for act in (0.0, 0.5):
        g = mk_gate([(ro, 0.0, 1.0), (disp7 > q(disp7, pd_), act, 1.0)])
        rep(f"T1 + disp7>q{int(pd_*100)} → {act}", {**V2, "gate": g})

print("\n-- 兩者合併 --")
for pv, pd_ in ((0.90, 0.95), (0.85, 0.95), (0.90, 0.90)):
    for act in (0.0, 0.5):
        g = mk_gate([(ro, 0.0, 1.0), (vol14 > q(vol14, pv), act, 1.0), (disp7 > q(disp7, pd_), act, 1.0)])
        rep(f"T1 + vol>q{int(pv*100)} + disp>q{int(pd_*100)} → {act}", {**V2, "gate": g})

print("\n############ B. 軟閘門版本 + 規避組合 ############")
for base_tag, g0 in (("T1(0/1)", t1),
                     ("軟 0.35/1/1.15", mk_gate([(ro, 0.35, 1.0), (pts == 2, 1.15, 1.0)])),
                     ("軟 0.0/1/1.15", mk_gate([(ro, 0.0, 1.0), (pts == 2, 1.15, 1.0)]))):
    g = g0 * mk_gate([(vol14 > q(vol14, 0.90), 0.0, 1.0), (disp7 > q(disp7, 0.95), 0.5, 1.0)])
    rep(f"{base_tag} + vol>q90→0 + disp>q95→0.5", {**V2, "gate": g})

print("\n############ C. 降低下單次數（成本控制）: rank buffer / no-trade band ############")
g_best = t1 * mk_gate([(vol14 > q(vol14, 0.90), 0.0, 1.0), (disp7 > q(disp7, 0.95), 0.5, 1.0)])
rep("基準：無緩衝", {**V2, "gate": g_best})
for buf in (1, 2, 3):
    rep(f"rank_buffer={buf}", {**V2, "gate": g_best, "rank_buffer": buf})
for band in (0.005, 0.01, 0.02):
    rep(f"no_trade_band={band*100:.1f}%", {**V2, "gate": g_best, "no_trade_band": band})
for buf, band in ((2, 0.01), (2, 0.02), (3, 0.02)):
    rep(f"buffer={buf} + band={band*100:.0f}%", {**V2, "gate": g_best, "rank_buffer": buf, "no_trade_band": band})
for z in (0.5, 1.0):
    rep(f"min_entry_z={z}", {**V2, "gate": g_best, "min_entry_z": z})

print("\n############ D. 最終候選（K / 週期 穩健性）############")
for reb in (5, 7, 10, 14):
    for K in (4, 5, 6):
        rep(f"final reb={reb} K={K}", {**V2, "gate": g_best, "rebalance_days": reb,
                                       "long_count": K, "short_count": K})

print("\n############ E. 資金費率極端者的進場過濾（per-name）############")
for mf_ in (0.05, 0.10, 0.15):
    rep(f"max_funding_cost={mf_}%/day", {**V2, "gate": g_best, "max_funding_cost_pct": mf_})

print("\n(完成)")
