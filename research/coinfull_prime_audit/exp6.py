import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

pd.set_option("display.width", 260); pd.set_option("display.max_columns", 60)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
macro = E.load_macro()
BT = E2.Backtester(close, op, high, low, vol, fund, f)
IS = ("2021-01-01", "2023-12-31"); OOS = ("2024-01-01", "2026-07-31")

# regime state
btc = close["BTCUSDT"]; mkt = close.pct_change(1).mean(axis=1)
R = pd.DataFrame(index=close.index)
R["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
R["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["mkt_fund_pct"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R["btc_vol14"] = btc.pct_change(1).rolling(14).std() * np.sqrt(365) * 100
R["mkt_move1"] = mkt.abs() * 100
R = R.ffill()
pts = ((R["btc_ma50_dev"] > 0).astype(int) + (R["breadth50"] > 30).astype(int) + (R["mkt_fund_pct"] > 45).astype(int))
gate_off = pd.Series(np.where(pts == 0, 0.0, 1.0), index=close.index)

# volatility-spike gates (defer new orders after a violent day)
def volgate(thr):
    return pd.Series(np.where(R["mkt_move1"].shift(1) > thr, 0.0, 1.0), index=close.index).fillna(1.0)

V2 = dict(long_count=5, short_count=5, rebalance_days=7, weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
REC = {**V2, "gate": gate_off}
AS_DAILY = dict(long_count=3, short_count=3, weights={"mom": .35, "flow": .35, "fund": .2, "liq": 0},
                momentum_mode="mom1", flow_mode="pressure", funding_mode="fund", rebalance_days=1)

print("############ A. walk-forward: 1y rolling windows (fee 5bp+2bp) ############")
hdr = f"{'window':14s} {'current(daily,coded)':>22s} {'v2+gate':>22s}"
print(hdr)
for y0 in range(2021, 2026):
    s, e = f"{y0}-01-01", f"{y0}-12-31"
    if pd.Timestamp(e) > close.index[-1]:
        e = str(close.index[-1].date())
    a = BT.run(E2.CFG(**{**AS_DAILY, "start": s, "end": e})); b = BT.run(E2.CFG(**{**REC, "start": s, "end": e}))
    ma, mb = E.perf(a["frame"]), E.perf(b["frame"])
    print(f"{s[:4]:>6s}-{e[:4]:<7s} {ma['cagr_pct']:>9.1f}% sh={ma['sharpe']:>5.2f} dd={ma['max_dd_pct']:>6.1f}% "
          f"{mb['cagr_pct']:>9.1f}% sh={mb['sharpe']:>5.2f} dd={mb['max_dd_pct']:>6.1f}%")
s, e = "2026-01-01", str(close.index[-1].date())
a = BT.run(E2.CFG(**{**AS_DAILY, "start": s, "end": e})); b = BT.run(E2.CFG(**{**REC, "start": s, "end": e}))
ma, mb = E.perf(a["frame"]), E.perf(b["frame"])
print(f"2026YTD({e[:10]}) {ma['cagr_pct']:>9.1f}% sh={ma['sharpe']:>5.2f} dd={ma['max_dd_pct']:>6.1f}% "
      f"{mb['cagr_pct']:>9.1f}% sh={mb['sharpe']:>5.2f} dd={mb['max_dd_pct']:>6.1f}%")

print("\n############ B. parameter sensitivity of v2+gate (FULL / OOS) ############")
rows = []
for K in [3, 4, 5, 6, 8]:
    for rb in [3, 5, 7, 10]:
        for wm in [0.4, 0.6, 0.8]:
            cfg = {**V2, "long_count": K, "short_count": K, "rebalance_days": rb,
                   "weights": {"mom": wm, "flow": 0.0, "fund": 1 - wm, "liq": 0}, "gate": gate_off}
            full = E.perf(BT.run(E2.CFG(**cfg))["frame"])
            oos = E.perf(BT.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
            rows.append({"K": K, "reb": rb, "w_mom": wm, "full_cagr": full["cagr_pct"], "full_sharpe": full["sharpe"],
                         "full_dd": full["max_dd_pct"], "oos_cagr": oos["cagr_pct"], "oos_sharpe": oos["sharpe"],
                         "oos_dd": oos["max_dd_pct"]})
S = pd.DataFrame(rows)
print(S.round(2).to_string(index=False))
print("\n--- summary by parameter (mean OOS Sharpe) ---")
for c in ["K", "reb", "w_mom"]:
    print(S.groupby(c)[["full_sharpe", "oos_sharpe", "oos_cagr", "oos_dd"]].mean().round(2).to_string())

print("\n############ C. market-neutrality diagnostics ############")
for nm, cfg in [("current(coded daily)", AS_DAILY), ("v2+gate", REC)]:
    r = BT.run(E2.CFG(**cfg))["frame"]
    x = mkt.reindex(r.index).fillna(0).values
    y = r["net_pct"].values
    beta = np.cov(y, x)[0, 1] / np.var(x)
    corr = np.corrcoef(y, x)[0, 1]
    up = y[x > 0].mean() * 1e4; dn = y[x < 0].mean() * 1e4
    print(f"{nm:20s} beta={beta:6.3f} corr={corr:6.3f}  avg_bp(up_days)={up:7.2f} avg_bp(down_days)={dn:7.2f}  "
          f"gross={r['gross'].sum():8.0f} fees={r['fees'].sum():7.0f} funding={r['funding'].sum():7.0f}")

print("\n############ D. order-avoidance triggers ############")
for nm, g in [("none", None), ("risk-off(pts=0) freeze-flatten", gate_off),
              ("vol spike >5% mkt (defer)", volgate(5.0)), ("vol spike >7% mkt (defer)", volgate(7.0)),
              ("both", (gate_off * volgate(5.0)))]:
    cfg = {**V2}
    if g is not None:
        cfg["gate"] = g
    full = E.perf(BT.run(E2.CFG(**cfg))["frame"])
    oos = E.perf(BT.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"{nm:32s} FULL cagr={full['cagr_pct']:6.1f}% sh={full['sharpe']:5.2f} dd={full['max_dd_pct']:6.1f}% turn={full['turnover']:.2f} | "
          f"OOS cagr={oos['cagr_pct']:6.1f}% sh={oos['sharpe']:5.2f} dd={oos['max_dd_pct']:6.1f}%")

print("\n############ E. gate semantics: hold vs flatten during risk-off ############")
for act, desc in [("freeze", "FREEZE: no trades at all in risk-off"),
                  ("scale", "SCALE: cut gross to 0 (liquidate) in risk-off")]:
    cfg = {**V2, "gate": gate_off, "gate_action": act}
    full = E.perf(BT.run(E2.CFG(**cfg))["frame"])
    oos = E.perf(BT.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"{desc:46s} FULL cagr={full['cagr_pct']:6.1f}% sh={full['sharpe']:5.2f} dd={full['max_dd_pct']:6.1f}% fee=${full['fees']:.0f} | "
          f"OOS cagr={oos['cagr_pct']:6.1f}% sh={oos['sharpe']:5.2f} dd={oos['max_dd_pct']:6.1f}%")

print("\n############ F. recommended config: yearly detail ############")
r = BT.run(E2.CFG(**REC))
print(E.yearly(r["frame"]).round(1).to_string())
print(E.perf(r["frame"]))
r2 = BT.run(E2.CFG(**AS_DAILY))
print("\n-- current as-coded yearly --")
print(E.yearly(r2["frame"]).round(1).to_string())
