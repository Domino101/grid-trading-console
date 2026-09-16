import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
print("panel:", close.shape, close.index.min().date(), close.index.max().date())

def show(name, res, capital=1000.0):
    m = E.perf(res["frame"], capital)
    print(f"{name:46s} tot={m['total_pct']:7.1f}%  cagr={m['cagr_pct']:6.1f}%  vol={m['vol_pct']:6.1f}%  "
          f"sharpe={m['sharpe']:5.2f}  maxdd={m['max_dd_pct']:6.1f}%  hit={m['hit_pct']:4.0f}%  "
          f"gross=${m['gross']:8.0f} fee=${m['fees']:7.0f} fund=${m['funding']:7.0f} net=${m['net']:8.0f} turn={m['turnover']:.2f}")
    return m

BASE = dict(long_count=3, short_count=3, weights={"mom": 0.35, "flow": 0.35, "fund": 0.20, "liq": 0.10})

print("\n=== A. AS-CODED (1d momentum + bar pressure flow + 1d funding), K=3 ===")
res = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode="mom1", flow_mode="pressure",
                                         funding_mode="fund", use_funding=True))
m0 = show("as-coded K3", res)
print(E.yearly(res["frame"]).round(1).to_string())

print("\n=== B. factor ablations (K=3) ===")
for mom in ["mom1", "mom3", "mom7", "mom30", "composite", "blend", "rev3"]:
    r = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode=mom, flow_mode="pressure", funding_mode="fund"))
    show(f"score: mom={mom} + flow + fund", r)

for fm in ["none", "pressure", "takerproxy"]:
    r = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode="mom1", flow_mode=fm, funding_mode="fund"))
    show(f"score: mom1 + flow={fm} + fund", r)

for uf in [True, False]:
    for fmm in (["fund", "fund_smooth"] if uf else ["fund"]):
        r = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode="mom1", flow_mode="pressure",
                                                funding_mode=fmm, use_funding=uf))
        show(f"score: mom1 + pressure + funding={'off' if not uf else fmm}", r)

print("\n=== C. single-factor portfolio (K=3) ===")
for mom in ["mom1", "mom7", "mom30", "composite", "blend", "rev3"]:
    cfg = E.BT(long_count=3, short_count=3, weights={"mom": 1.0, "flow": 0.0, "fund": 0.0, "liq": 0.0},
               momentum_mode=mom, flow_mode="none", funding_mode="fund", use_funding=False)
    show(f"pure momentum={mom}", E.run_backtest(close, fund, f, cfg))
cfg = E.BT(long_count=3, short_count=3, weights={"mom": 0.0, "flow": 0.0, "fund": 1.0, "liq": 0.0},
           momentum_mode="mom1", flow_mode="none", funding_mode="fund_smooth", use_funding=True)
show("pure funding (3d mean, reverse)", E.run_backtest(close, fund, f, cfg))
cfg = E.BT(long_count=3, short_count=3, weights={"mom": 0.0, "flow": 1.0, "fund": 0.0, "liq": 0.0},
           momentum_mode="mom1", flow_mode="pressure", use_funding=False)
show("pure flow (bar pressure)", E.run_backtest(close, fund, f, cfg))

print("\n=== D. basket width K (as-coded score) ===")
for k in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
    r = E.run_backtest(close, fund, f, E.BT(weights=BASE["weights"], long_count=k, short_count=k, momentum_mode="mom1",
                                            flow_mode="pressure", funding_mode="fund"))
    show(f"K={k} each side (of {close.shape[1]})", r)

print("\n=== E. cost sensitivity (as-coded, K=3) ===")
for fee, slip in [(0, 0), (2, 1), (5, 2), (5, 5), (10, 5)]:
    r = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode="mom1", flow_mode="pressure",
                                            funding_mode="fund", fee_bps=fee, slip_bps=slip))
    show(f"fee={fee}bp slip={slip}bp", r)

print("\n=== F. rebalance frequency (as-coded, K=3) ===")
for rb in [1, 2, 3, 5, 7, 14]:
    r = E.run_backtest(close, fund, f, E.BT(**BASE, momentum_mode="mom1", flow_mode="pressure",
                                            funding_mode="fund", rebalance_days=rb))
    show(f"rebalance every {rb}d", r)

print("\n=== G. information coefficient (whole sample, 1d horizon) ===")
print(E.ic_table(f, close).round(4).to_string())

print("\n=== H. annual breakdown of as-coded ===")
print(E.yearly(res["frame"]).round(1).to_string())
