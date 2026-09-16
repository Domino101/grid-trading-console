import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E

pd.set_option("display.width", 240); pd.set_option("display.max_columns", 40)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
cols = list(close.columns)
dates = close.index

# ---------------------------------------------------------------- 1. horizon / tail analysis
print("=== 1. IC by horizon (cross-sectional Spearman) ===")
tab = {}
for h in [1, 3, 7, 14, 21]:
    t = E.ic_table(f, close, horizon=h)
    tab[f"IC{h}d"] = t["mean_ic"]
print(pd.DataFrame(tab).round(4).to_string())

print("\n=== 2. Portfolio spread by rank bucket (basket size K, 1d holding, zero cost) ===")
fwd1 = close.pct_change(1).shift(-1)
rows = []
for name in ["mom1", "mom3", "mom7", "mom30", "composite", "blend", "fund", "fund_smooth", "pressure", "takerproxy", "rev3"]:
    for K in [2, 3, 5]:
        sp = []
        for t in range(35, len(close) - 1):
            s = f[name].iloc[t].dropna()
            r = fwd1.iloc[t].reindex(s.index)
            m = r.notna()
            if m.sum() < 2 * K:
                continue
            s, r = s[m], r[m]
            o = s.sort_values(ascending=False).index
            sp.append(r[o[:K]].mean() - r[o[-K:]].mean())
        sp = np.array(sp)
        rows.append({"factor": name, "K": K, "mean_spread_bp": sp.mean() * 1e4,
                     "ann_spread_pct": sp.mean() * 365 * 100, "sharpe": sp.mean() / sp.std() * np.sqrt(365),
                     "hit_pct": (sp > 0).mean() * 100, "turnover_proxy": np.nan})
print(pd.DataFrame(rows).pivot(index="factor", columns="K", values=["mean_spread_bp", "sharpe"]).round(2).to_string())

# ---------------------------------------------------------------- 3. turnover decomposition
print("\n=== 3. turnover decomposition (as-coded, K=3, daily) ===")
res = E.run_backtest(close, fund, f, E.BT(long_count=3, short_count=3, weights={"mom": .35, "flow": .35, "fund": .2, "liq": .1},
                                          momentum_mode="mom1", flow_mode="pressure", funding_mode="fund"), keep_positions=True)
pos = res["positions"]
name_to = []; drift_to = []
prev = {}
for p in pos:
    keys_prev, keys_new = set(prev), set(p)
    nt = sum(abs(p[k]) for k in keys_new - keys_prev) + sum(abs(prev[k]) for k in keys_prev - keys_new)
    dt = sum(abs(p[k] - prev[k]) for k in keys_new & keys_prev)
    name_to.append(nt); drift_to.append(dt); prev = p
print(f"avg name turnover/day={np.mean(name_to):.3f}  drift turnover/day={np.mean(drift_to):.3f}  "
      f"total={np.mean(name_to)+np.mean(drift_to):.3f}  (annual fee drag at 7bp = {(np.mean(name_to)+np.mean(drift_to))*0.0007*365*100:.1f}%/yr)")
print(f"avg holding period ~ {1/max(np.mean(name_to),1e-9)*1:.1f} days per name swap")

# name survival: how long does a selected name stay in the basket?
for K in [2, 3, 8]:
    s = E.score_matrix(f, E.BT(long_count=K, short_count=K, weights={"mom": .35, "flow": .35, "fund": .2, "liq": .1},
                               momentum_mode="mom1", flow_mode="pressure", funding_mode="fund"), cols)
    lasts = []
    for side in ["long", "short"]:
        membership = {}
        cur = {}
        for t in range(35, len(close) - 1):
            sv = s.iloc[t].dropna()
            if len(sv) < 2 * K: continue
            o = sv.sort_values(ascending=False).index
            picked = set(o[:K]) if side == "long" else set(o[-K:])
            for sym in picked:
                cur[sym] = cur.get(sym, 0) + 1
            for sym in list(cur):
                if sym not in picked:
                    lasts.append(cur.pop(sym))
    print(f"K={K}: avg holding duration {np.mean(lasts):.1f} days, median {np.median(lasts):.0f}, days<2: {(np.array(lasts)<2).mean()*100:.0f}%")

# ---------------------------------------------------------------- 4. score variants (realistic cost)
print("\n=== 4. candidate configurations, fee 5bp + slip 2bp, K=3 ===")
def show(name, r):
    m = E.perf(r["frame"])
    print(f"{name:52s} tot={m['total_pct']:8.1f}% cagr={m['cagr_pct']:6.1f}% vol={m['vol_pct']:5.1f}% "
          f"sharpe={m['sharpe']:5.2f} sortino={m['sortino']:5.2f} dd={m['max_dd_pct']:6.1f}% hit={m['hit_pct']:4.0f}% "
          f"turn={m['turnover']:.2f} gross=${m['gross']:7.0f} fee=${m['fees']:6.0f} fund=${m['funding']:6.0f}")

variants = {
    "as-coded: 0.35mom1+0.35flow+0.20fund": dict(weights={"mom": .35, "flow": .35, "fund": .2, "liq": 0}, momentum_mode="mom1", flow_mode="pressure", funding_mode="fund"),
    "v2a: 0.5 mom_blend + 0.2 flow + 0.3 fund3d": dict(weights={"mom": .5, "flow": .2, "fund": .3, "liq": 0}, momentum_mode="blend", flow_mode="pressure", funding_mode="fund_smooth"),
    "v2b: no flow, 0.7 mom_blend + 0.3 fund3d": dict(weights={"mom": .7, "flow": 0.0, "fund": .3, "liq": 0}, momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth"),
    "v2c: no flow, 100% mom_blend": dict(weights={"mom": 1.0, "flow": 0.0, "fund": 0.0, "liq": 0}, momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth", use_funding=False),
    "v2d: no flow, 100% mom7": dict(weights={"mom": 1.0, "flow": 0.0, "fund": 0.0, "liq": 0}, momentum_mode="mom7", flow_mode="none", funding_mode="fund_smooth", use_funding=False),
    "v2e: 0.6 mom_composite + 0.4 fund3d": dict(weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0}, momentum_mode="composite", flow_mode="none", funding_mode="fund_smooth"),
    "v2f: 0.6 mom_blend + 0.4 fund3d": dict(weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0}, momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth"),
    "v2g: flow contrarian (-0.2) + mom_blend": dict(weights={"mom": .8, "flow": -0.2, "fund": .3, "liq": 0}, momentum_mode="blend", flow_mode="pressure", funding_mode="fund_smooth"),
}
for rb in [1, 3, 7]:
    print(f"-- rebalance every {rb}d")
    for name, kw in variants.items():
        r = E.run_backtest(close, fund, f, E.BT(long_count=3, short_count=3, rebalance_days=rb, **kw))
        show(f"{name} [{rb}d]", r)

print("\n=== 5. K sweep for the best-of-class (v2f, 7d rebalance) ===")
for K in [1, 2, 3, 4, 5, 6, 8]:
    r = E.run_backtest(close, fund, f, E.BT(long_count=K, short_count=K, rebalance_days=7,
                                            weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0}, momentum_mode="blend",
                                            flow_mode="none", funding_mode="fund_smooth"))
    show(f"v2f K={K} [7d]", r)

print("\n=== 6. yearly detail of v2f K=5 7d ===")
r = E.run_backtest(close, fund, f, E.BT(long_count=5, short_count=5, rebalance_days=7,
                                        weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0}, momentum_mode="blend",
                                        flow_mode="none", funding_mode="fund_smooth"))
print(E.yearly(r["frame"]).round(1).to_string())
m = E.perf(r["frame"]); print(m)
