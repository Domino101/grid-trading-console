# -*- coding: utf-8 -*-
"""exp12: 最終建議包（保守版 / 積極版）完整績效表：FULL / IS / OOS、逐年、曝險天數、成本歸因。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import engine as E
import engine2 as E2

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 40)

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
btc = close["BTCUSDT"]
IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")

# ---- regime inputs (all computable in real time)
vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
disp7 = close.pct_change(7).std(axis=1) * 100.0
mkt_fund = fund.mean(axis=1)
fund_pct = mkt_fund.rolling(252, min_periods=126).rank(pct=True) * 100.0
breadth50 = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
ro = (pts == 0)
vol_q90 = vol14.rolling(252, min_periods=126).quantile(0.90)
disp_q95 = disp7.rolling(252, min_periods=126).quantile(0.95)

print("門檻值（2026-07-31 當下）: vol14_q90=%.1f%%  disp7_q95=%.2f%%" % (vol_q90.iloc[-1], disp_q95.iloc[-1]))
print("最新一日狀態: vol14=%.1f%%  disp7=%.2f%%  pts=%d" % (vol14.iloc[-1], disp7.iloc[-1], pts.iloc[-1]))


def gate_conservative():
    g = pd.Series(1.0, index=close.index)
    g[ro.fillna(False) | (vol14 > vol_q90).fillna(False) | (disp7 > disp_q95).fillna(False)] = 0.0
    return g


def gate_aggressive():
    g = pd.Series(np.where(pts == 2, 1.15, 1.0), index=close.index)
    g[ro.fillna(False) | (vol14 > vol_q90).fillna(False) | (disp7 > disp_q95).fillna(False)] = 0.0
    return g


CFG = dict(long_count=5, short_count=5, rebalance_days=7,
           weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
           momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth",
           no_trade_band=0.01)

bt = E2.Backtester(close, op, high, low, vol, fund, f)

PACKS = {
    "0. 現行（as-coded: 1d mom + flow, K=8, 1d, 5bp+2bp）": dict(
        long_count=8, short_count=8, rebalance_days=1,
        weights={"mom": .35, "flow": .35, "fund": .20, "liq": .10},
        momentum_mode="mom1", flow_mode="takerproxy", funding_mode="fund", no_trade_band=0.0),
    "1. v2 因子（無閘門）": dict(CFG),
    "2. v2 + T1（pts=0→0）": dict(CFG, gate=pd.Series(np.where(ro, 0.0, 1.0), index=close.index)),
    "3. v2 + T1 + 波動停手（vol>q90→0）": dict(
        CFG, gate=pd.Series(np.where(ro | (vol14 > vol_q90).fillna(False), 0.0, 1.0), index=close.index)),
    "4. v2 + T1 + 離散停手（disp>q95→0）": dict(
        CFG, gate=pd.Series(np.where(ro | (disp7 > disp_q95).fillna(False), 0.0, 1.0), index=close.index)),
    "5. 【保守版】v2 + T1 + 波動 + 離散": dict(CFG, gate=gate_conservative()),
    "6. 【積極版】保守版 + pts=2 加碼 1.15×": dict(CFG, gate=gate_aggressive()),
}

print("\n############ 最終績效表（本金 $1,000；費率 5bp + 滑價 2bp）############")
rows = []
for name, cfg in PACKS.items():
    m_full = E.perf(bt.run(E2.CFG(**cfg))["frame"])
    m_is = E.perf(bt.run(E2.CFG(**{**cfg, "start": IS[0], "end": IS[1]}))["frame"])
    m_oos = E.perf(bt.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"\n{name}")
    for tag, m in (("FULL", m_full), ("IS  ", m_is), ("OOS ", m_oos)):
        print(f"  {tag}  cagr={m['cagr_pct']:6.1f}%  vol={m['vol_pct']:5.1f}%  sharpe={m['sharpe']:5.2f}  "
              f"maxdd={m['max_dd_pct']:7.1f}%  calmar={m['calmar']:5.2f}  hit={m['hit_pct']:4.1f}%  "
              f"turn={m['turnover']:.3f}  fee=${m['fees']:6.0f}  fund=${m['funding']:6.0f}  net=${m['net']:7.0f}")
    rows.append((name, m_full, m_is, m_oos))

print("\n############ 逐年績效（保守版 vs 積極版 vs 現行）############")
for name in ("0. 現行（as-coded: 1d mom + flow, K=8, 1d, 5bp+2bp）",
             "5. 【保守版】v2 + T1 + 波動 + 離散",
             "6. 【積極版】保守版 + pts=2 加碼 1.15×"):
    cfg = PACKS[name]
    r = bt.run(E2.CFG(**cfg))["frame"]
    y = E.yearly(r)
    print(f"\n{name}")
    print(y.round(2).to_string())

print("\n############ 曝險與停手統計（保守版 / 積極版）############")
idx = close.index
for name in ("5. 【保守版】v2 + T1 + 波動 + 離散", "6. 【積極版】保守版 + pts=2 加碼 1.15×"):
    g = PACKS[name]["gate"]
    flat = (g == 0)
    print(f"\n{name}: 完全空手天數 {int(flat.sum())} / {len(g)} = {flat.mean()*100:.1f}%")
    print("   逐年停手天數:", {str(y): int(flat[idx.year == y].sum()) for y in range(2021, 2027)})
    for lab, mask in (("pts=0", ro), ("vol>q90", (vol14 > vol_q90).fillna(False)), ("disp>q95", (disp7 > disp_q95).fillna(False))):
        print(f"   {lab:9s}: {int(mask.sum()):4d} 天 ({mask.mean()*100:4.1f}%)  平均每次持續 "
              f"{mask.sum()/max(1,(mask.astype(int).diff()==1).sum()):.1f} 天")

print("\n############ 日報酬分配（保守版）############")
r = bt.run(E2.CFG(**PACKS["5. 【保守版】v2 + T1 + 波動 + 離散"]))["frame"]
d = r["net_pct"] if "net_pct" in r.columns else r["net"] / 1000 * 100
print("  最差 10 日: ", np.round(np.sort(d.values)[:10], 2))
print("  單日 <= -4% 天數:", int((d <= -4).sum()), " >= +4% 天數:", int((d >= 4).sum()))
print("  日報酬 std=%.3f%%  skew=%.2f" % (d.std(), d.skew()))

print("\n############ 資金費率歸因（保守版）############")
frame = r
print("  累計 funding P&L = $%.0f / 累計 fee = $%.0f / 累計 gross = $%.0f"
      % (frame["funding"].sum(), -frame["fees"].sum(), frame["gross"].sum()))

print("\n(完成)")
