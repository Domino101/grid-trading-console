import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
macro = E.load_macro()
cols = list(close.columns)
dates = close.index

# ---------------------------------------------------------------- regime state variables (all lagged-safe: value at t uses <= t)
btc = close["BTCUSDT"]
mkt = close.pct_change(1).mean(axis=1)                     # equal-weight market daily return
r = pd.DataFrame(index=dates)
r["btc_ret30"] = btc.pct_change(30) * 100
r["btc_ret7"] = btc.pct_change(7) * 100
r["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
r["btc_vol14"] = btc.pct_change(1).rolling(14).std() * np.sqrt(365) * 100
r["btc_vol_pct"] = r["btc_vol14"].rolling(180).rank(pct=True) * 100
r["disp7"] = close.pct_change(7).std(axis=1) * 100          # cross-sectional dispersion
r["disp_pct"] = r["disp7"].rolling(180).rank(pct=True) * 100
r["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
r["mkt_fund"] = fund.mean(axis=1) * 100                     # avg daily funding across universe (%)
r["mkt_fund_pct"] = fund.mean(axis=1).rolling(180).rank(pct=True) * 100
r["fund_spread"] = np.sort(fund.values, axis=1)[:, -5:].mean(axis=1) * 100 - np.sort(fund.values, axis=1)[:, :5].mean(axis=1) * 100
r["mkt_ret30"] = mkt.rolling(30).sum() * 100
r["mkt_vol14"] = mkt.rolling(14).std() * np.sqrt(365) * 100
r = r.join(macro[["fng", "vix"]]).ffill()
print("=== regime variable summary ===")
print(r.describe().round(1).T.to_string())

# ---------------------------------------------------------------- baseline + candidate strategies
BASE_ASCODED = dict(long_count=3, short_count=3, weights={"mom": .35, "flow": .35, "fund": .2, "liq": 0},
                    momentum_mode="mom1", flow_mode="pressure", funding_mode="fund")
V2 = dict(long_count=5, short_count=5, rebalance_days=7, weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
r_as = E.run_backtest(close, fund, f, E.BT(**BASE_ASCODED))
r_v2 = E.run_backtest(close, fund, f, E.BT(**V2))
ret_as = r_as["frame"]["net_pct"]
ret_v2 = r_v2["frame"]["net_pct"]
print("\n=== baseline stats ===")
for nm, rr in [("as-coded K3 1d", r_as), ("v2 K5 7d", r_v2)]:
    m = E.perf(rr["frame"]); print(f"{nm:16s} cagr={m['cagr_pct']:.1f}% sharpe={m['sharpe']:.2f} dd={m['max_dd_pct']:.1f}% turn={m['turnover']:.2f}")

# ---------------------------------------------------------------- regime conditioning
def bucket_table(ret: pd.Series, reg: pd.DataFrame, n_q=4, label=""):
    rows = []
    for col in reg.columns:
        x = reg[col].reindex(ret.index)
        m = x.notna() & ret.notna()
        if m.sum() < 200: continue
        try:
            q = pd.qcut(x[m], n_q, labels=False, duplicates="drop")
        except Exception:
            continue
        for b in sorted(pd.unique(q.dropna())):
            rr = ret[m][q == b]
            sd = rr.std()
            rows.append({"var": col, "bucket": int(b), "n": len(rr),
                         "lo": x[m][q == b].min(), "hi": x[m][q == b].max(),
                         "mean_bp": rr.mean() * 1e4, "ann_pct": rr.mean() * 365 * 100,
                         "hit": (rr > 0).mean() * 100,
                         "sharpe": rr.mean() / sd * np.sqrt(365) if sd > 0 else 0})
    return pd.DataFrame(rows)

print("\n=== v2 strategy returns by regime quartile (2021-2026 in-sample) ===")
bt = bucket_table(ret_v2, r[["btc_ret30", "btc_ma50_dev", "btc_vol14", "btc_vol_pct", "disp7", "disp_pct",
                             "breadth50", "mkt_fund_pct", "mkt_vol14", "fng", "vix"]])
print(bt.round(2).to_string(index=False))

print("\n=== as-coded strategy returns by regime quartile ===")
bt2 = bucket_table(ret_as, r[["btc_ret30", "btc_ma50_dev", "btc_vol_pct", "disp_pct", "breadth50", "mkt_fund_pct", "fng", "vix"]])
print(bt2.round(2).to_string(index=False))
