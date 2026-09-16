# -*- coding: utf-8 -*-
"""exp14: 換倉週期（rebalance_days）的系統性掃描 —— 「7 日真的是最佳嗎？」

回答三個問題：
  A. 1～28 日全部試過了嗎？ → 完整掃描 1,2,3,4,5,6,7,8,9,10,11,12,14,16,18,21,28（含 FULL / IS / OOS）
  B. 7 日的優勢來自「因子 alpha」還是「成本」？ → 同掃描在 fee=0 與 gate=off 下的形狀
  C. 7 日是統計上真的比較好，還是運氣？ → (1) 換倉相位穩健性（每個週期 7 種對齊相位）
                                              (2) 對 7 日的配對區塊 bootstrap 檢定

基準：v2 因子（mom blend .6 + fund_smooth .4，移除 flow/liq）、K=5/5、5bp 手續費 + 2bp 滑價、
      單規則風險閘門 T1（pts=0 → 0x，其餘 1.0x；即 exp12 的「v2 + T1」），本金 1000 USDT。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import engine as E
import engine2 as E2

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)
rng = np.random.default_rng(20260916)

close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
btc = close["BTCUSDT"]
IS = ("2021-01-01", "2023-12-31")
OOS = ("2024-01-01", "2026-07-31")
dates = close.index

# ---------------- 閘門（與 exp11/exp12 完全相同）
vol14 = btc.pct_change().rolling(14).std() * np.sqrt(365) * 100.0
disp7 = close.pct_change(7).std(axis=1) * 100.0
mkt_fund = fund.mean(axis=1)
fund_pct = mkt_fund.rolling(252, min_periods=126).rank(pct=True) * 100.0
breadth50 = (close > close.rolling(50).mean()).mean(axis=1) * 100.0
btc_dev50 = (btc / btc.rolling(50).mean() - 1) * 100.0
pts = ((btc_dev50 > 0).astype(int) + (breadth50 > 30).astype(int) + (fund_pct > 45).astype(int))
ro = (pts == 0)
GATE = pd.Series(np.where(ro, 0.0, 1.0), index=close.index)   # T1：pts=0 → 0x

BASE = dict(long_count=5, short_count=5,
            weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
            momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth",
            gate=GATE)

CYCLES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 21, 28]


def run(reb, fee_bps=5.0, slip_bps=2.0, gate=True, start=None, end=None):
    cfg = dict(BASE, rebalance_days=reb, fee_bps=fee_bps, slip_bps=slip_bps)
    if not gate:
        cfg["gate"] = None
    if start:
        cfg["start"], cfg["end"] = start, end
    r = E2.Backtester(close, op, high, low, vol, fund, f).run(E2.CFG(**cfg))
    return r["frame"], E.perf(r["frame"])


print("=" * 110)
print("A. 換倉週期完整掃描（v2 因子 + T1 閘門 pts=0→0、K=5/5、5bp+2bp）")
print("=" * 110)
print(f"{'reb':>4} {'FULL cagr':>10} {'sh':>6} {'dd':>7} {'turn/d':>7} {'fee$':>7} {'fee%/yr':>8} | "
      f"{'IS cagr':>8} {'sh':>6} | {'OOS cagr':>9} {'sh':>6} {'dd':>7}")
rows = {}
frames = {}
for reb in CYCLES:
    fr, mf = run(reb)
    _, mi = run(reb, start=IS[0], end=IS[1])
    _, mo = run(reb, start=OOS[0], end=OOS[1])
    frames[reb] = fr
    rows[reb] = (mf, mi, mo)
    years = mf["days"] / 365.0
    print(f"{reb:>4} {mf['cagr_pct']:>9.1f}% {mf['sharpe']:>6.2f} {mf['max_dd_pct']:>6.1f}% "
          f"{mf['turnover']:>7.3f} {mf['fees']:>7.0f} {mf['fees']/1000/years*100:>7.2f}% | "
          f"{mi['cagr_pct']:>7.1f}% {mi['sharpe']:>6.2f} | "
          f"{mo['cagr_pct']:>8.1f}% {mo['sharpe']:>6.2f} {mo['max_dd_pct']:>6.1f}%")

print("\n※ 觀察重點：成本隨週期拉長而單調下降（≈ turnover/日 × 365 × 7bp），")
print("  但因子 alpha 的有效期限（IC 峰值在 7–21 日，見 exp2）也一起下降 → 存在內點最佳解。\n")

print("=" * 110)
print("B. 形狀分解：把成本拿掉（fee=0）與把閘門拿掉（gate=off）")
print("=" * 110)
print(f"{'reb':>4} | {'有成本+閘門':>22} | {'零成本+閘門（純 alpha）':>26} | {'有成本、無閘門':>22}")
print(f"{'':>4} | {'cagr':>9} {'sh':>6} {'dd':>6} | {'cagr':>11} {'sh':>6} {'dd':>6} | {'cagr':>9} {'sh':>6} {'dd':>6}")
for reb in CYCLES:
    _, m0 = run(reb)
    _, mz = run(reb, fee_bps=0.0, slip_bps=0.0)
    _, mn = run(reb, gate=False)
    print(f"{reb:>4} | {m0['cagr_pct']:>8.1f}% {m0['sharpe']:>6.2f} {m0['max_dd_pct']:>5.1f}% | "
          f"{mz['cagr_pct']:>10.1f}% {mz['sharpe']:>6.2f} {mz['max_dd_pct']:>5.1f}% | "
          f"{mn['cagr_pct']:>8.1f}% {mn['sharpe']:>6.2f} {mn['max_dd_pct']:>5.1f}%")

print("\n※ 若「零成本」下短週期明顯更好，代表 7 日的結論主要來自成本，而非 alpha 到期。\n")

print("=" * 110)
print("C1. 相位穩健性：每個週期試 7 種對齊相位（start 前後平移 0–6 日）")
print("=" * 110)
print(f"{'reb':>4} | {'FULL cagr 中位數':>16} {'min':>7} {'max':>7} {'全距':>7} | "
      f"{'OOS cagr 中位數':>16} {'min':>7} {'max':>7} {'全距':>7}")
warm = 60
for reb in [3, 4, 5, 6, 7, 8, 10, 14]:
    fc, oc = [], []
    for off in range(7):
        s = str(dates[warm + off].date())
        _, mf = run(reb, start=s)
        _, mo = run(reb, start=s, end=OOS[1]) if False else (None, None)
        # OOS 相位平移：以相同 offset 平移 OOS 起點
        o_start = dates[dates.searchsorted(pd.Timestamp(OOS[0])) + off]
        _, mo = run(reb, start=str(o_start.date()), end=OOS[1])
        fc.append(mf["cagr_pct"])
        oc.append(mo["cagr_pct"])
    fc, oc = np.array(fc), np.array(oc)
    print(f"{reb:>4} | {np.median(fc):>15.1f}% {fc.min():>6.1f}% {fc.max():>6.1f}% {fc.max()-fc.min():>6.1f}% | "
          f"{np.median(oc):>15.1f}% {oc.min():>6.1f}% {oc.max():>6.1f}% {oc.max()-oc.min():>6.1f}%")

print("\n※ 全距（max−min）若大於週期之間的差距，代表「最佳週期」其實是相位運氣。\n")

print("=" * 110)
print("D. 統計檢定：以 7 日為基準的配對區塊 bootstrap（10 日為一個區塊，2000 次重抽）")
print("=" * 110)
base_r = frames[7]["net_pct"].reindex(frames[7].index).fillna(0.0).values
n = len(base_r)
block = 10
nb = int(np.ceil(n / block))
print(f"{'reb':>4} {'平均日差(bp)':>12} {'95% CI':>22} {'P(差<=0)':>9} {'結論':>14}")
for reb in CYCLES:
    if reb == 7:
        continue
    a = frames[reb]["net_pct"].reindex(frames[7].index).fillna(0.0).values
    d = a - base_r
    boots = np.empty(2000)
    for b in range(2000):
        idx = rng.integers(0, nb, nb)
        pick = np.concatenate([np.arange(i * block, min((i + 1) * block, n)) for i in idx])
        boots[b] = d[pick].mean()
    lo, hi = np.percentile(boots, [2.5, 97.5])
    p_le0 = float((boots <= 0).mean())
    # p_le0 = P(該週期日報酬 <= 7 日)。p 很小 → 該週期顯著較差；p 很大 → 該週期顯著較好
    verdict = ("顯著較差" if p_le0 < 0.05 else ("無顯著差異" if p_le0 < 0.95 else "顯著較好（優於7日）"))
    print(f"{reb:>4} {d.mean()*1e4:>11.2f} [{lo*1e4:>9.2f}, {hi*1e4:>9.2f}] {p_le0:>9.3f} {verdict:>14}")

print("\n※ 平均日差 = 該週期日報酬 − 7 日週期日報酬（正 = 該週期較好）。")
print("  P(差<=0) 很小 → 該週期顯著優於 7 日；接近 0.5 → 與 7 日無從區分。")

print("=" * 110)
print("E. 成本結構：週期 vs 每次換倉週轉與年化成本")
print("=" * 110)
print(f"{'reb':>4} {'換倉次數':>8} {'每期週轉':>9} {'turn/d':>8} {'年成本(5+2bp)':>14} {'平均持有天數/檔':>16}")
for reb in CYCLES:
    mf = rows[reb][0]
    n_reb = mf["days"] / reb
    per_cycle = mf["turnover"] * mf["days"] / n_reb
    hold = 2.0 / max(mf["turnover"], 1e-9) / 2  # 單邊週轉 → 名義持有天數（近似）
    print(f"{reb:>4} {n_reb:>8.0f} {per_cycle:>9.3f} {mf['turnover']:>8.3f} "
          f"{mf['fees']/1000/(mf['days']/365)*100:>13.2f}% {1/max(mf['turnover'],1e-9):>15.1f}")

print("\n(完成)")
