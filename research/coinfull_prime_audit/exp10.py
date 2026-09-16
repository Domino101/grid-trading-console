# -*- coding: utf-8 -*-
"""exp10: 自動規避下單時機（gate 全表）與倉位增減（scale/tilt）實測。

用法: python3 exp10.py
產出: 標準輸出（可 tee 到 exp10_out.txt）
"""
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


# --------------------------------------------------------------------------- helpers
class BTS(E2.Backtester):
    """Backtester 加上 precomputed score 覆寫。"""

    def __init__(self, *a, score=None, **k):
        super().__init__(*a, **k)
        self._ov = score

    def _score(self, cfg):
        return self._ov if self._ov is not None else super()._score(cfg)


def build(mom, fund_df=None, wmom=0.6, wfund=0.4, extra=None, extra_w=0.0):
    fund_df = f["fund_smooth"] if fund_df is None else fund_df
    M, Fd = mom.values, fund_df.values
    X = extra.values if isinstance(extra, pd.DataFrame) else None
    arr = np.zeros(M.shape)
    for i in range(len(M)):
        z = wmom * E.winsor_z(M[i]) + wfund * E.winsor_z(Fd[i])
        if X is not None:
            z = z + extra_w * E.winsor_z(X[i])
        arr[i] = z
    return pd.DataFrame(arr, index=mom.index, columns=mom.columns)


V2 = dict(long_count=5, short_count=5, rebalance_days=7,
          weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
S_BASE = build(f["blend"])


def run(cfg, score=None, tag=None, show=False):
    bt = BTS(close, op, high, low, vol, fund, f, score=score)
    r = bt.run(E2.CFG(**cfg))
    m = E.perf(r["frame"])
    if show:
        print(f"{tag:44s} cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} "
              f"dd={m['max_dd_pct']:7.1f}% turn={m['turnover']:.3f} vol={m['vol_pct']:.1f}%")
    return m, r


def rep(tag, cfg, score=None, start=IS[1]):
    mf, _ = run(cfg, score)
    mo, _ = run({**cfg, "start": OOS[0], "end": OOS[1]}, score)
    print(f"{tag:44s} FULL cagr={mf['cagr_pct']:6.1f}% sh={mf['sharpe']:5.2f} dd={mf['max_dd_pct']:7.1f}% "
          f"| OOS cagr={mo['cagr_pct']:6.1f}% sh={mo['sharpe']:5.2f} dd={mo['max_dd_pct']:7.1f}%")
    return mf, mo


# --------------------------------------------------------------------------- regime vars
def pct_rank(s, win=252, minp=126):
    return s.rolling(win, min_periods=minp).rank(pct=True) * 100.0


vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
disp7 = close.pct_change(7).std(axis=1) * 100.0
mkt_fund = fund.mean(axis=1)
fund_pct = pct_rank(mkt_fund)
breadth50 = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
r1 = btc.pct_change(1)

pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
ro = (pts == 0)


def const(v):
    return pd.Series(v, index=close.index)


def soft(a0, a2):
    return pd.Series(np.select([pts == 0, pts == 2], [a0, a2], 1.0), index=close.index)


print("############ 0. 基準（v2, gate=off）與單因子方向 ############")
rep("v2 無閘門（基準）", V2, S_BASE)

# --------------------------------------------------------------------------- A
print("\n############ A. flow 因子方向（gate=off）############")
for nm, fl in [("pressure", f["pressure"]), ("takerproxy", f["takerproxy"])]:
    for w, tag in [(1.0, "as-coded(+0.35)"), (0.35, "弱版(+0.12)"), (-0.35, "反向(-0.35)"), (-1.0, "強反向(-1.0)")]:
        rep(f"flow={nm} {tag}", V2, build(f["blend"], extra=fl, extra_w=w), start=IS[1])

# --------------------------------------------------------------------------- B
print("\n############ B. T1 風險關閉閘門變體 ############")
gates = {
    "B1 pts=0 → 0 (建議)": soft(0.0, 1.0),
    "B2 pts<=1 → 0": pd.Series(np.where(pts <= 1, 0.0, 1.0), index=close.index),
    "B3 pts<=2 → 0": pd.Series(np.where(pts <= 2, 0.0, 1.0), index=close.index),
    "B4 軟 0.35 / 1 / 1.15": soft(0.35, 1.15),
    "B5 軟 0.25 / 1 / 1.25": soft(0.25, 1.25),
    "B6 軟 0.5 / 1 / 1.0": soft(0.5, 1.0),
    "B7 軟 0.0 / 1 / 1.15": soft(0.0, 1.15),
    "B8 僅換倉日檢查 pts=0": None,  # 下面另外處理
}
for tag, g in gates.items():
    if g is None:
        continue
    rep(tag, {**V2, "gate": g}, S_BASE)

# B8: 只在換倉日更新風險狀態（其餘時間沿用上次判定）
idx = np.arange(len(close))
first = 60
mask = ((idx - first) % 7 == 0)
pts_rb = pd.Series(np.where(mask, pts.values, np.nan), index=close.index).ffill()
rep("B8 僅換倉日檢查 pts=0", {**V2, "gate": pd.Series(np.where(pts_rb == 0, 0.0, 1.0), index=close.index)}, S_BASE)
# B9: 每日檢查，但只檔新倉（不強制平倉）——用 hold 動作模擬
rep("B9 每日檢查、只擋新倉(hold)", {**V2, "gate": soft(0.0, 1.0), "gate_action": "hold"}, S_BASE)

# --------------------------------------------------------------------------- C
print("\n############ C. 回撤節流（iteration-based，含 T1）############")


def dd_throttle_run(triggers, tag, base=None, score=None, iters=8):
    base = dict(V2) if base is None else dict(base)
    ns = pd.Series(1.0, index=close.index)
    bt = BTS(close, op, high, low, vol, fund, f, score=score)
    r = None
    for _ in range(iters):
        r = bt.run(E2.CFG(**{**base, "gate": ns}))
        eq = r["frame"]["equity"].values
        peak = np.maximum.accumulate(eq)
        dd = eq / peak - 1.0
        new = np.ones(len(eq))
        for lvl, s in sorted(triggers, key=lambda x: x[0], reverse=True):
            new = np.where(dd <= -lvl, np.minimum(new, s), new)
        ns2 = pd.Series(new, index=r["frame"].index).reindex(close.index).fillna(1.0)
        if float((ns2 - ns).abs().max()) < 1e-09:
            ns = ns2
            break
        ns = ns2
    m = E.perf(r["frame"])
    mo, _ = run({**V2, "gate": ns, "start": OOS[0], "end": OOS[1]}, score)
    print(f"{tag:44s} FULL cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} dd={m['max_dd_pct']:7.1f}% "
          f"| OOS cagr={mo['cagr_pct']:6.1f}% sh={mo['sharpe']:5.2f} dd={mo['max_dd_pct']:7.1f}%")
    return m


dd_throttle_run([(0.06, 0.5), (0.09, 0.0)], "C1 軟 0.35/1/1.15 + DD(−6%→.5, −9%→0)", {**V2, "gate": soft(0.35, 1.15)}, S_BASE)
dd_throttle_run([(0.10, 0.5), (0.15, 0.0)], "C2 軟 0.35/1/1.15 + DD(−10%→.5, −15%→0)", {**V2, "gate": soft(0.35, 1.15)}, S_BASE)
dd_throttle_run([(0.12, 0.5), (0.18, 0.5)], "C3 軟 0.35/1/1.15 + DD(−12%→.5, −18%→.5)", {**V2, "gate": soft(0.35, 1.15)}, S_BASE)
dd_throttle_run([(0.12, 0.5)], "C4 T1 + DD(−12%→.5)", {**V2, "gate": soft(0.0, 1.0)}, S_BASE)

# --------------------------------------------------------------------------- D
print("\n############ D. 其他規避規則（單獨疊在 T1 上；T1=基準 gate=pts0→0）############")
q90v = vol14.rolling(252, min_periods=126).quantile(0.90)
q95v = vol14.rolling(252, min_periods=126).quantile(0.95)
q20d = disp7.rolling(252, min_periods=126).quantile(0.20)
q95d = disp7.rolling(252, min_periods=126).quantile(0.95)
g_t1 = soft(0.0, 1.0)

extra_gates = {
    "D1 T1 + vol14>q90 → 0.5": g_t1 * pd.Series(np.where(vol14 > q90v, 0.5, 1.0), index=close.index),
    "D2 T1 + vol14>q90 → 0": g_t1 * pd.Series(np.where(vol14 > q90v, 0.0, 1.0), index=close.index),
    "D3 T1 + vol14>q95 → 0.5": g_t1 * pd.Series(np.where(vol14 > q95v, 0.5, 1.0), index=close.index),
    "D4 T1 + disp7<q20 → 0.5": g_t1 * pd.Series(np.where(disp7 < q20d, 0.5, 1.0), index=close.index),
    "D5 T1 + disp7>q95 → 0.5": g_t1 * pd.Series(np.where(disp7 > q95d, 0.5, 1.0), index=close.index),
    "D6 T1 + fund_pct<45 → 0": pd.Series(np.where(ro | (fund_pct < 45), 0.0, 1.0), index=close.index),
    "D7 T1 + fund_pct<45 → 0.5": pd.Series(np.where(ro, 0.0, np.where(fund_pct < 45, 0.5, 1.0)), index=close.index),
    "D8 T1 + fund_pct>90 → 0.5": pd.Series(np.where(ro | (fund_pct > 90), 0.0, 1.0), index=close.index),
    "D9 T1 + breadth<30 → 0": pd.Series(np.where(ro | (breadth50 < 30), 0.0, 1.0), index=close.index),
    "D10 T1 + breadth>80 → 1.15": pd.Series(np.where(ro, 0.0, np.where(breadth50 > 80, 1.15, 1.0)), index=close.index),
    "D11 T1 + BTC 1d<=-5% → 0（當日）": g_t1 * pd.Series(np.where(r1 <= -0.05, 0.0, 1.0), index=close.index),
    "D12 T1 + BTC 1d>=+5% → 0（當日）": g_t1 * pd.Series(np.where(r1 >= 0.05, 0.0, 1.0), index=close.index),
    "D13 T1 + vol<q90 且 disp>q20": g_t1 * pd.Series(np.where((vol14 < q90v) & (disp7 > q20d), 1.0, 0.5), index=close.index),
}
for tag, g in extra_gates.items():
    rep(tag, {**V2, "gate": g}, S_BASE)

# --------------------------------------------------------------------------- E
print("\n############ E. 最佳組合 + 週期/K ###########")
BEST = {**V2, "gate": soft(0.35, 1.15)}
for reb in (5, 7, 10):
    for K in (4, 5, 6):
        rep(f"軟0.35/1.15 reb={reb} K={K}", {**BEST, "rebalance_days": reb, "long_count": K, "short_count": K}, S_BASE)

print("\n-- 週期/倉位敏感度（軟閘門固定）--")
for reb in (3, 5, 7, 10, 14):
    rep(f"reb={reb}", {**BEST, "rebalance_days": reb}, S_BASE)
for K in (3, 4, 6, 8):
    rep(f"K={K}", {**BEST, "long_count": K, "short_count": K}, S_BASE)

# --------------------------------------------------------------------------- F
print("\n############ F. 風險結束後的再進場延遲（T3）############")


def entry_delay(gate, days=1):
    on = (gate > 0).values
    out = on.copy()
    for i in range(1, len(on)):
        if on[i] and not all(on[max(0, i - days):i]):
            out[i] = False
    return pd.Series(np.where(out, gate.values, 0.0), index=close.index)


for d in (1, 2, 3):
    rep(f"T1 + 再進場延遲 {d} 日", {**V2, "gate": entry_delay(g_t1, d)}, S_BASE)
    rep(f"軟閘門 + 再進場延遲 {d} 日", {**V2, "gate": entry_delay(soft(0.35, 1.15), d)}, S_BASE)

print("\n(完成)")
