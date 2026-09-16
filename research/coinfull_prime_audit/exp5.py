import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
macro = E.load_macro()
BT = E2.Backtester(close, op, high, low, vol, fund, f)
IS = ("2021-01-01", "2023-12-31"); OOS = ("2024-01-01", "2026-07-31")

# --------------------------------------------------- regime state + gates
btc = close["BTCUSDT"]
R = pd.DataFrame(index=close.index)
R["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
R["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["mkt_fund_pct"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R["disp_pct"] = (close.pct_change(7).std(axis=1)).rolling(252).rank(pct=True) * 100
R["btc_vol14"] = btc.pct_change(1).rolling(14).std() * np.sqrt(365) * 100
R = R.ffill()
pts = ((R["btc_ma50_dev"] > 0).astype(int) + (R["breadth50"] > 30).astype(int) + (R["mkt_fund_pct"] > 45).astype(int))
gate_off = pd.Series(np.where(pts == 0, 0.0, 1.0), index=close.index)         # block only full risk-off
gate_soft = pd.Series(np.where(pts == 0, 0.35, np.where(pts == 2, 1.15, 1.0)), index=close.index)
print("state share: pts0=%.1f%% pts1=%.1f%% pts2=%.1f%% pts3=%.1f%%" % tuple(
    100 * (pts == i).mean() for i in range(4)))

AS_DAILY = dict(long_count=3, short_count=3, weights={"mom": .35, "flow": .35, "fund": .2, "liq": 0},
                momentum_mode="mom1", flow_mode="pressure", funding_mode="fund", rebalance_days=1)
V2 = dict(long_count=5, short_count=5, rebalance_days=7, weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")

def pair(tag, cfg, quiet=False):
    full = BT.run(E2.CFG(**cfg))
    m_full = E2.summary(f"FULL  {tag}", full)
    out = {"full": m_full}
    for lb, (s, e) in [("IS", IS), ("OOS", OOS)]:
        rr = BT.run(E2.CFG(**{**cfg, "start": s, "end": e}))
        out[lb] = E.perf(rr["frame"])
        if not quiet:
            E2.summary(f"  {lb:3s} {tag}", rr)
    return out

print("\n############ 1. immediate fixes to the CURRENT daily implementation ############")
pair("as-coded daily (baseline)", AS_DAILY)
pair("as-coded daily + hysteresis2", {**AS_DAILY, "rank_buffer": 2})
pair("as-coded daily + band 1.5%", {**AS_DAILY, "no_trade_band": 0.015})
pair("as-coded daily + hyst2 + band1.5%", {**AS_DAILY, "rank_buffer": 2, "no_trade_band": 0.015})
pair("as-coded daily + hyst2 + band1.5% + gate(off only)", {**AS_DAILY, "rank_buffer": 2, "no_trade_band": 0.015, "gate": gate_off})
print()
pair("as-coded 7d (freq fix only)", {**AS_DAILY, "rebalance_days": 7})
pair("as-coded 7d + gate(off only)", {**AS_DAILY, "rebalance_days": 7, "gate": gate_off})

print("\n############ 1b. gate actions on the current daily implementation ############")
pair("as-coded daily +hyst+band + gateFREEZE", {**AS_DAILY, "rank_buffer": 2, "no_trade_band": 0.015, "gate": gate_off, "gate_action": "freeze"})
pair("as-coded daily +hyst+band + gateSCALE", {**AS_DAILY, "rank_buffer": 2, "no_trade_band": 0.015, "gate": gate_off, "gate_action": "scale"})

print("\n############ 1c. gate actions on v2 ############")
pair("v2+gateFREEZE", {**V2, "gate": gate_off, "gate_action": "freeze"})
pair("v2+gateSCALE", {**V2, "gate": gate_off, "gate_action": "scale"})

print("\n############ 2. recommended v2 family ############")
pair("v2 K5 7d (baseline v2)", V2)
pair("v2 + gate(off only)", {**V2, "gate": gate_off})
pair("v2 + gate(soft: .35/1/1.15)", {**V2, "gate": gate_soft})
pair("v2 + gate(off) + hyst1", {**V2, "gate": gate_off, "rank_buffer": 1})
pair("v2 + gate(off) + band1%", {**V2, "gate": gate_off, "no_trade_band": 0.01})
pair("v2 K8 7d + gate(off)", {**V2, "long_count": 8, "short_count": 8, "gate": gate_off})

# --------------------------------------------------- 3. per-name risk controls
print("\n############ 3. per-name stop-loss / take-profit (v2 + gate) ############")
for sl, tp in [(None, None), (0.12, None), (0.20, None), (None, 0.25), (0.12, 0.25)]:
    pair(f"v2+gate sl={sl} tp={tp}", {**V2, "gate": gate_off, "stop_loss_pct": sl, "take_profit_pct": tp})

print("\n############ 4. funding-cost entry filter (skip crowded legs) ############")
for fc in [None, 0.06, 0.10, 0.15]:
    pair(f"v2+gate maxfund={fc}%/day", {**V2, "gate": gate_off, "max_funding_cost_pct": fc})

print("\n############ 5. cost tiers for the recommended config ############")
for fee, slip in [(5, 2), (4, 1.5), (2, 1), (1, 0.5)]:
    r = BT.run(E2.CFG(**{**V2, "gate": gate_off, "fee_bps": fee, "slip_bps": slip}))
    E2.summary(f"v2+gate fee={fee}bp slip={slip}bp", r)
