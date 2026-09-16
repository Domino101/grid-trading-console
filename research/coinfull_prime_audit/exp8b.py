"""exp8b: robustness of the timing findings - weekday phase on IS and OOS separately,
cadence 7 vs 14, and the combined recommended rules."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
BT = E2.Backtester(close, op, high, low, vol, fund, f)
IS = ("2021-01-01", "2023-12-31"); OOS = ("2024-01-01", "2026-07-31")

btc = close["BTCUSDT"]
R = pd.DataFrame(index=close.index)
R["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
R["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["mkt_fund_pct"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R = R.ffill()
pts = ((R["btc_ma50_dev"] > 0).astype(int) + (R["breadth50"] > 30).astype(int) + (R["mkt_fund_pct"] > 45).astype(int))
ro = (pts == 0)
gate_off = pd.Series(np.where(pts == 0, 0.0, 1.0), index=close.index)
ret1 = btc.pct_change(1)
allow_crash = (ret1 > -0.05).astype(bool)
on = ~ro
ge1 = pd.Series(np.where(on & on.shift(1, fill_value=False), 1.0, 0.0), index=close.index)
ge1 = ge1.where(close.index >= close.index[60], 1.0)

V2 = dict(long_count=5, short_count=5, rebalance_days=7,
          weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
GATE = {**V2, "gate": gate_off}

def run(cfg):
    return E.perf(BT.run(E2.CFG(**cfg))["frame"])

def row(tag, cfg):
    m = run(cfg)
    print(f"{tag:40s} cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} dd={m['max_dd_pct']:6.1f}% "
          f"tot={m['total_pct']:7.1f}% turn={m['turnover']:.3f}")
    return m

print("############ 相位檢驗：IS (2021-04 起) ############")
base_is = pd.Timestamp("2021-04-01")
for i in range(7):
    s = (base_is + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
    row(f"IS  phase{i} ({close.index[max(60, close.index.searchsorted(pd.Timestamp(s)))].day_name()[:3]})",
        {**GATE, "start": s, "end": IS[1]})

print("\n############ 相位檢驗：OOS (2024-01 起) ############")
base_oos = pd.Timestamp("2024-01-01")
for i in range(7):
    s = (base_oos + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
    wd = pd.Timestamp(s).day_name()[:3]
    row(f"OOS phase{i} ({wd})", {**GATE, "start": s, "end": OOS[1]})

print("\n############ 週期 7 vs 14 日（IS / OOS）############")
for reb in (7, 14):
    for nm, (s, e) in [("FULL", (None, None)), ("IS", IS), ("OOS", OOS)]:
        cfg = {**GATE, "rebalance_days": reb, "start": (base_is + pd.Timedelta(days=0)).strftime("%Y-%m-%d") if s is None else s, "end": e} if e else {**GATE, "rebalance_days": reb, "start": (base_is).strftime("%Y-%m-%d")}
        row(f"reb={reb:2d} {nm}", cfg)

print("\n############ 建議組合（相位=週四, 7日, gate, 崩跌延後）############")
combo = {**GATE, "rebalance_allow": allow_crash, "start": "2021-04-01"}
row("combo FULL", combo)
row("combo IS", {**combo, "end": IS[1]})
row("combo OOS", {**GATE, "rebalance_allow": allow_crash, "start": "2024-01-01", "end": OOS[1]})
print("\n-- 加上「延遲一日再進場」--")
row("combo+delay-entry FULL", {**GATE, "gate": gate_off * ge1, "rebalance_allow": allow_crash, "start": "2021-04-01"})
row("combo+delay-entry OOS", {**GATE, "gate": gate_off * ge1, "rebalance_allow": allow_crash, "start": "2024-01-01", "end": OOS[1]})
print("\n(完成)")
