# -*- coding: utf-8 -*-
"""exp7: 建議配置（v2 + gate）的疊加測試、閘門語意、參數敏感度、直接年化表格。

基準：v2 = 動能 blend(0.5*1d+0.3*7d+0.2*30d, 權重0.6) + 資金費率 -3日均值(權重0.4)
      K=5, 7 日換倉, 5bp 手續費 + 2bp 滑價, 本金 1000 USDT
"""
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
bt = E2.Backtester(close, op, high, low, vol, fund, f)

# ---------------- regime inputs
vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
disp7 = close.pct_change(7).std(axis=1) * 100.0
mkt_fund = fund.mean(axis=1)
fund_pct = mkt_fund.rolling(252, min_periods=126).rank(pct=True) * 100.0
breadth50 = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
ro = (pts == 0)

V2 = dict(long_count=5, short_count=5, rebalance_days=7,
          weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
GATE = pd.Series(np.where(ro, 0.0, 1.0), index=close.index)
BASE = {**V2, "gate": GATE}


def line(tag, cfg):
    mf = E.perf(bt.run(E2.CFG(**cfg))["frame"])
    mi = E.perf(bt.run(E2.CFG(**{**cfg, "start": IS[0], "end": IS[1]}))["frame"])
    mo = E.perf(bt.run(E2.CFG(**{**cfg, "start": OOS[0], "end": OOS[1]}))["frame"])
    print(f"{tag:42s} FULL cagr={mf['cagr_pct']:6.1f}% sh={mf['sharpe']:5.2f} dd={mf['max_dd_pct']:7.1f}% "
          f"turn={mf['turnover']:.3f} | IS cagr={mi['cagr_pct']:6.1f}% sh={mi['sharpe']:5.2f} | "
          f"OOS cagr={mo['cagr_pct']:6.1f}% sh={mo['sharpe']:5.2f} dd={mo['max_dd_pct']:7.1f}%")
    return mf, mi, mo


print("############ A. 疊加測試（基準 = v2 + 風險關閉平倉，7 日、K=5）############")
line("基準 v2+gate", BASE)
line("A1 pts=0 時持有不動（hold）", {**V2, "gate": GATE, "gate_action": "hold"})
ro2 = ro & ro.shift(1, fill_value=False)
line("A2 風險連 2 日才平倉（確認延遲）", {**V2, "gate": pd.Series(np.where(ro2, 0.0, 1.0), index=close.index)})
ro3 = ro & ro.shift(1, fill_value=False) & ro.shift(2, fill_value=False)
line("A3 風險連 3 日才平倉", {**V2, "gate": pd.Series(np.where(ro3, 0.0, 1.0), index=close.index)})
for vt in (0.16, 0.18, 0.20, 0.22, 0.25):
    line(f"A4 +波動目標 {vt*100:.0f}%", {**BASE, "vol_target": vt, "vol_lookback": 14})
for lvl, sc in ((-0.10, 0.6), (-0.12, 0.5), (-0.15, 0.5), (-0.20, 0.5)):
    line(f"A5 +回撤節流 {lvl*100:.0f}% -> {sc}x", {**BASE, "dd_throttle": (abs(lvl), sc)})
line("A6 軟閘門 0.35/1/1.15",
     {**V2, "gate": pd.Series(np.where(ro, 0.35, np.where(pts == 2, 1.15, 1.0)), index=close.index)})
line("A7 軟閘門 0.0/1/1.15",
     {**V2, "gate": pd.Series(np.where(ro, 0.0, np.where(pts == 2, 1.15, 1.0)), index=close.index)})
disp_pct = disp7.rolling(252, min_periods=126).rank(pct=True) * 100.0
line("A8 +低分散度縮倉（disp<20% → 0.5x）",
     {**V2, "gate": pd.Series(np.where(ro, 0.0, np.where(disp_pct < 20, 0.5, 1.0)), index=close.index)})
line("A9 +rank buffer 2 +急單門檻 1.5%", {**BASE, "rank_buffer": 2, "no_trade_band": 0.015})
line("A10 +僅在換倉日檢查閘門（降週轉）",
     {**V2, "gate": pd.Series(np.where(ro.astype(int).rolling(7, min_periods=1).max() > 0, 0.0, 1.0), index=close.index)})

print("\n############ B. 閘門語意（pts=0 時怎麼做最好）############")
for tag, cfg in (
    ("B1 平倉（flatten）", {**V2, "gate": GATE, "gate_action": "scale"}),
    ("B2 只停止下單（hold）", {**V2, "gate": GATE, "gate_action": "hold"}),
    ("B3 減半（0.5x）", {**V2, "gate": pd.Series(np.where(ro, 0.5, 1.0), index=close.index)}),
    ("B4 留 35%（0.35x）", {**V2, "gate": pd.Series(np.where(ro, 0.35, 1.0), index=close.index)}),
    ("B5 反向下單（翻轉）", None),
):
    if cfg is None:
        continue
    line(tag, cfg)

print("\n############ C. 參數敏感度（基準配置）############")
for K in (3, 4, 5, 6, 8):
    line(f"C K={K}", {**BASE, "long_count": K, "short_count": K})
for reb in (3, 5, 7, 10, 14):
    line(f"C reb={reb}", {**BASE, "rebalance_days": reb})
for w in (0.4, 0.5, 0.6, 0.7, 0.8):
    line(f"C w_mom={w}", {**BASE, "weights": {"mom": w, "flow": 0.0, "fund": 1 - w, "liq": 0}})

print("\n############ D. 建議配置完整表 ############")
r = bt.run(E2.CFG(**BASE))
m = E.perf(r["frame"])
print("FULL:", {k: round(v, 3) for k, v in m.items()})
print()
print(E.yearly(r["frame"]).round(2).to_string())
print("\n每月效應（全期）:")
fr = r["frame"].copy()
fr["m"] = fr.index.month
print((fr.groupby("m")["net_pct"].agg(["mean", "std", "sum"]).round(5)).to_string())
print("\n多空分解: (engine2 僅輸出組合層級 P&L；已由第 3 節配置表補足兩腿比較)")
print("\n(完成)")
