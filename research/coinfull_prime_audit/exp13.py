# -*- coding: utf-8 -*-
"""exp13: 現行（as-coded）設定在不同 K 的重建績效 + 單純改 7 日換倉的效果。

目的：證明「現行設定虧錢」的結論不是特定 K 造成的假象。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import engine as E
import engine2 as E2

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")
bt = E2.Backtester(close, op, high, low, vol, fund, f)

ASCODED = dict(weights={"mom": .35, "flow": .35, "fund": .20, "liq": .10},
               momentum_mode="mom1", flow_mode="takerproxy", funding_mode="fund")
V2 = dict(weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")


def line(tag, cfg):
    mf = E.perf(bt.run(E2.CFG(**cfg))["frame"])
    mi = E.perf(bt.run(E2.CFG(**{**cfg, "start": IS[0], "end": IS[1]}))["frame"])
    mo = E.perf(bt.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"{tag:34s} FULL tot={mf['total_pct']:8.1f}% cagr={mf['cagr_pct']:7.1f}% sh={mf['sharpe']:5.2f} "
          f"turn={mf['turnover']:.2f} fee=${mf['fees']:6.0f} gross=${mf['gross']:7.0f} | "
          f"IS tot={mi['total_pct']:7.1f}% | OOS tot={mo['total_pct']:7.1f}% cagr={mo['cagr_pct']:6.1f}% sh={mo['sharpe']:5.2f}")


print("############ A. 現行因子、每日換倉（cron 每天 00:00）############")
for K in (2, 3, 4, 5, 6, 8):
    line(f"as-coded K={K} 1d", {**ASCODED, "long_count": K, "short_count": K, "rebalance_days": 1})

print("\n############ B. 只把換倉改成 7 日（不動因子）############")
for K in (2, 3, 4, 5, 6, 8):
    line(f"as-coded K={K} 7d", {**ASCODED, "long_count": K, "short_count": K, "rebalance_days": 7})

print("\n############ C. 現行因子、每日、但成本=0（檢驗毛 alpha）############")
for K in (3, 5, 8):
    line(f"as-coded K={K} 1d 0bp", {**ASCODED, "long_count": K, "short_count": K, "rebalance_days": 1,
                                     "fee_bps": 0.0, "slip_bps": 0.0})

print("\n############ D. 對照：v2 因子、7 日 ############")
for K in (3, 4, 5, 6, 8):
    line(f"v2 K={K} 7d", {**V2, "long_count": K, "short_count": K, "rebalance_days": 7})

print("\n(完成)")
