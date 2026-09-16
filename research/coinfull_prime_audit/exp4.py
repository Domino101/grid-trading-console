import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
macro = E.load_macro()
bt = E2.Backtester(close, op, high, low, vol, fund, f)

IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")

# ------------------------------------------------------------------ regime state (trailing, no look-ahead)
btc = close["BTCUSDT"]
mkt = close.pct_change(1).mean(axis=1)
R = pd.DataFrame(index=close.index)
R["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
R["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["mkt_fund"] = fund.mean(axis=1) * 100
R["mkt_fund_pct"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R["btc_vol14"] = btc.pct_change(1).rolling(14).std() * np.sqrt(365) * 100
R["disp7"] = close.pct_change(7).std(axis=1) * 100
R["disp_pct"] = R["disp7"].rolling(252).rank(pct=True) * 100
R["fng"] = macro["fng"]
R = R.ffill()
print("=== regime state occupation (trend / breadth / funding-points) ===")
state = pd.DataFrame(index=close.index)
state["s_trend"] = (R["btc_ma50_dev"] > 0).astype(int)
state["s_breadth"] = (R["breadth50"] > 30).astype(int)
state["s_fund"] = (R["mkt_fund_pct"] > 45).astype(int)
state["pts"] = state[["s_trend", "s_breadth", "s_fund"]].sum(axis=1)

V2 = dict(long_count=5, short_count=5, rebalance_days=7, weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
AS = dict(long_count=3, short_count=3, weights={"mom": .35, "flow": .35, "fund": .2, "liq": 0},
          momentum_mode="mom1", flow_mode="pressure", funding_mode="fund")
SINGLE = dict(long_count=5, short_count=5, rebalance_days=1, weights={"mom": 1.0, "flow": 0.0, "fund": 0.0, "liq": 0},
              momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")

def cond_report(cfgkw, tag):
    r = bt.run(E2.CFG(**cfgkw), collect_positions=False)
    ret = r["frame"]["net_pct"]
    rows = []
    for p in range(4):
        m = state["pts"] == p
        mm = m.reindex(ret.index).fillna(False).astype(bool)
        rr = ret[mm].dropna()
        rows.append({"points": p, "n": len(rr), "share_pct": len(rr) / len(ret) * 100,
                     "mean_bp": rr.mean() * 1e4, "ann_pct": rr.mean() * 365 * 100,
                     "sharpe": rr.mean() / rr.std() * np.sqrt(365) if rr.std() > 0 else 0,
                     "hit_pct": (rr > 0).mean() * 100})
    print(f"\n--- {tag}: strategy daily P&L by regime points (0=worst..3=best) ---")
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    return pd.DataFrame(rows), r

print("\n############ IN-SAMPLE DESIGN (2021-2023) ############")
res_is, r_is = cond_report({**V2, **{"start": IS[0], "end": IS[1]}}, "v2 K5 7d")
res_as, _ = cond_report({**AS, **{"start": IS[0], "end": IS[1]}}, "as-coded K3 1d")
print("\n############ OUT-OF-SAMPLE (2024-2026.07) ############")
res_oos, r_oos = cond_report({**V2, **{"start": OOS[0], "end": OOS[1]}}, "v2 K5 7d")
res_as_oos, _ = cond_report({**AS, **{"start": OOS[0], "end": OOS[1]}}, "as-coded K3 1d")

# ------------------------------------------------------------------ gate construction
GATE_MAP = {0: 0.0, 1: 0.40, 2: 0.75, 3: 1.0}
gate = state["pts"].map(GATE_MAP)
gate_lin = (0.25 + 0.25 * state["pts"]).clip(0, 1)          # 0.25 / 0.5 / 0.75 / 1.0
gate_binary = (state["pts"] >= 2).astype(float)             # only block the worst half
gate_hard = (state["pts"] >= 3).astype(float) * 1.0 + (state["pts"] == 2) * 0.6 + (state["pts"] == 1) * 0.2

print("\n=== gate variants: state share ===")
for nm, g in [("gate_map", gate), ("gate_lin", gate_lin), ("gate_binary", gate_binary), ("gate_hard", gate_hard)]:
    print(f"{nm:12s} avg={g.mean():.2f}  share0={100*(g==0).mean():.1f}%  share<1={100*(g<1).mean():.1f}%")

# ------------------------------------------------------------------ overlay comparison
print("\n=== OVERLAY LADDER (full sample 2021-2026.07; fee 5bp + 2bp) ===")
def run_pair(tag, base_cfg, overlay: dict):
    print(f"\n## {tag}")
    full = bt.run(E2.CFG(**{**base_cfg, **overlay}))
    E2.summary(f"  FULL      {tag}", full)
    for label, (s, e) in [("IS 21-23", IS), ("OOS 24-26", OOS)]:
        rr = bt.run(E2.CFG(**{**base_cfg, **overlay, "start": s, "end": e}))
        E2.summary(f"  {label:9s} {tag}", rr)
    return full

print("\n--- A. as-coded baseline ---")
run_pair("as-coded K3 daily", AS, {})
print("\n--- B. v2 signal/frequency ---")
run_pair("v2 K5 7d", V2, {})
print("\n--- C. v2 + rank hysteresis(2) + no-trade band 1.5% ---")
run_pair("v2 + hysteresis + band", V2, {"rank_buffer": 2, "no_trade_band": 0.015})
print("\n--- D. v2 + hyst + band + regime gate (map 0/.4/.75/1) ---")
run_pair("v2 + hyst + band + gate", V2, {"rank_buffer": 2, "no_trade_band": 0.015, "gate": gate})
print("\n--- E. v2 + hyst + band + gate + vol target 22% ---")
run_pair("v2 + hyst + band + gate + volT", V2, {"rank_buffer": 2, "no_trade_band": 0.015, "gate": gate,
                                                 "vol_target": 0.22, "vol_lookback": 14})
print("\n--- F. E + drawdown throttle(12% -> 0.5) ---")
run_pair("v2 + all overlays + DDthrottle", V2, {"rank_buffer": 2, "no_trade_band": 0.015, "gate": gate,
                                                "vol_target": 0.22, "vol_lookback": 14,
                                                "dd_throttle": (0.12, 0.5), "dd_recovery": 0.05})

print("\n--- G. single-factor momentum blend daily (low-turnover signal) + overlays ---")
run_pair("mom-only K5 daily + overlays", SINGLE, {"rank_buffer": 2, "no_trade_band": 0.015, "gate": gate,
                                                  "vol_target": 0.22})

print("\n--- H. gate variants on top of (hyst+band) ---")
for nm, g in [("no gate", None), ("gate_map", gate), ("gate_lin", gate_lin),
              ("gate_binary", gate_binary), ("gate_hard", gate_hard)]:
    ov = {"rank_buffer": 2, "no_trade_band": 0.015}
    if g is not None:
        ov["gate"] = g
    r = bt.run(E2.CFG(**{**V2, **ov}))
    E2.summary(f"  {nm}", r)
