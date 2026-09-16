"""exp9: exact momentum/funding form used by the recommended config (score = 0.6*z(M) + 0.4*z(F))."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
BT = E2.Backtester(close, op, high, low, vol, fund, f)
OOS = ("2024-01-01", "2026-07-31")

btc = close["BTCUSDT"]
R = pd.DataFrame(index=close.index)
R["d"] = (btc / btc.rolling(50).mean() - 1) * 100
R["b"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["f"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R = R.ffill()
pts = ((R["d"] > 0).astype(int) + (R["b"] > 30).astype(int) + (R["f"] > 45).astype(int))
gate = pd.Series(np.where(pts == 0, 0.0, 1.0), index=close.index)

class BTS(E2.Backtester):
    def __init__(self, *a, score=None, **k):
        super().__init__(*a, **k); self._ov = score
    def _score(self, cfg):
        return self._ov if self._ov is not None else super()._score(cfg)

def build(mom, fund_df=None, wmom=0.6, wfund=0.4):
    fund_df = f["fund_smooth"] if fund_df is None else fund_df
    M, Fd = mom.values, fund_df.values
    arr = np.zeros(M.shape)
    for i in range(len(M)):
        arr[i] = wmom * E.winsor_z(M[i]) + wfund * E.winsor_z(Fd[i])
    return pd.DataFrame(arr, index=mom.index, columns=mom.columns)

V2C = dict(long_count=5, short_count=5, rebalance_days=7, gate=gate,
           weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
           momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")

def rep(tag, score_df):
    bt = BTS(close, op, high, low, vol, fund, f, score=score_df)
    a = E.perf(bt.run(E2.CFG(**V2C))["frame"])
    b = E.perf(bt.run(E2.CFG(**{**V2C, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"{tag:34s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% | "
          f"OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f} dd={b['max_dd_pct']:6.1f}%")

print("=== 動能因子形式 (搭 0.4 資金費率) ===")
mv = {"mom1(1d)": f["mom1"], "mom3(3d)": f["mom3"], "mom7(7d)": f["mom7"], "mom30(30d)": f["mom30"],
      "composite .6/.4 (7/30)": f["composite"], "blend .5/.3/.2 (1/7/30)": f["blend"],
      "0.3/0.7 (7/30)": 0.3 * f["mom7"] + 0.7 * f["mom30"],
      "0.4/0.6 (7/30)": 0.4 * f["mom7"] + 0.6 * f["mom30"]}
for nm, mf in mv.items():
    rep(nm, build(mf))
print("\n=== 資金費率形式 (搭 blend) ===")
rep("fund_smooth (-3d avg) 建議", build(f["blend"]))
rep("fund raw (-1 snapshot)", build(f["blend"], fund_df=f["fund"]))
rep("fund 7d avg", build(f["blend"], fund_df=-fund.rolling(7).mean()))
rep("fund 14d avg", build(f["blend"], fund_df=-fund.rolling(14).mean()))
print("\n=== 動能/資金權重 ===")
for wm in (0.4, 0.5, 0.6, 0.7, 0.8):
    rep(f"w_mom={wm:.1f}", build(f["blend"], wmom=wm, wfund=round(1 - wm, 1)))
print("\n(完成)")
