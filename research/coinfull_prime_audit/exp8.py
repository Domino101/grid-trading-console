"""exp8: (1) execution TIMING (launch weekday / phase, condition-gated launch, cadence)
        (2) 變盤 handling: avoid trading vs REVERSE the book when regime flips."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine as E, engine2 as E2

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 60)
close, op, high, low, vol, fund = E.load_panel()
f = E.factor_panel(close, op, high, low, vol, fund)
BT = E2.Backtester(close, op, high, low, vol, fund, f)
COLS = list(close.columns)
IS = ("2021-01-01", "2023-12-31"); OOS = ("2024-01-01", "2026-07-31")

# ---------------------------------------------------------------- regime state
btc = close["BTCUSDT"]
R = pd.DataFrame(index=close.index)
R["btc_ma50_dev"] = (btc / btc.rolling(50).mean() - 1) * 100
R["breadth50"] = (close > close.rolling(50).mean()).mean(axis=1) * 100
R["mkt_fund_pct"] = fund.mean(axis=1).rolling(252).rank(pct=True) * 100
R = R.ffill()
pts = ((R["btc_ma50_dev"] > 0).astype(int) + (R["breadth50"] > 30).astype(int) + (R["mkt_fund_pct"] > 45).astype(int))
ro = (pts == 0)                                    # risk-off flag
ret1 = btc.pct_change(1)
gate_off = pd.Series(np.where(pts == 0, 0.0, 1.0), index=close.index)

V2 = dict(long_count=5, short_count=5, rebalance_days=7,
          weights={"mom": .6, "flow": 0.0, "fund": .4, "liq": 0},
          momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth")
GATE = {**V2, "gate": gate_off}

def run(tag, cfg, stats=False):
    m = E.perf(BT.run(E2.CFG(**cfg))["frame"])
    if stats:
        print(f"{tag:52s} tot={m['total_pct']:7.1f}% cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} "
              f"dd={m['max_dd_pct']:6.1f}% turn={m['turnover']:.3f} fee=${m['fees']:6.0f} vol={m['vol_pct']:.1f}%")
    return m

def pair(tag, cfg):
    a = run(f"FULL {tag}", cfg); b = run(f"  OOS {tag}", {**cfg, "start": OOS[0], "end": OOS[1]})
    print(f"{tag:40s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% | "
          f"OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f} dd={b['max_dd_pct']:6.1f}%")
    return a, b

n_ro_starts = int((ro.astype(int).diff() == 1).sum())
print(f"risk-off episodes: {n_ro_starts}  ({n_ro_starts / (len(pts) / 365):.1f}/yr); risk-off share={ro.mean() * 100:.1f}%")
print("state share: pts0=%.1f%% pts1=%.1f%% pts2=%.1f%% pts3=%.1f%%" % tuple(100 * (pts == i).mean() for i in range(4)))

# ================================================================= 1. TIMING
print("\n############ 1a. 啟動日 / 換倉相位（v2+gate, 每 7 日換倉）############")
base = pd.Timestamp("2021-04-01")
phase_rows = []
for i in range(7):
    s = base + pd.Timedelta(days=i)
    idx = max(60, int(close.index.searchsorted(s)))
    m = run(f"phase{i}", {**GATE, "start": s.strftime("%Y-%m-%d")})
    wd = close.index[idx].day_name()[:3]
    print(f"  相位 {i}: 首次換倉 {close.index[idx].date()} ({wd})  cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} "
          f"dd={m['max_dd_pct']:6.1f}% tot={m['total_pct']:7.1f}%")
    phase_rows.append((i, wd, m))
best = max(phase_rows, key=lambda x: x[2]["sharpe"]); worst = min(phase_rows, key=lambda x: x[2]["sharpe"])
print(f"  → 最佳相位 {best[0]} ({best[1]}) sh={best[2]['sharpe']:.2f}；最差相位 {worst[0]} ({worst[1]}) sh={worst[2]['sharpe']:.2f}")
print("  OOS 檢驗：")
for i in range(7):
    s = (base + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
    m = run(f"phase{i}", {**GATE, "start": s, "end": OOS[1]})
    print(f"    OOS 相位 {i} ({phase_rows[i][1]}) cagr={m['cagr_pct']:6.1f}% sh={m['sharpe']:5.2f} dd={m['max_dd_pct']:6.1f}%")

print("\n############ 1b. 換倉週期（v2+gate）############")
for reb in [1, 3, 5, 7, 10, 14]:
    a = run(f"reb{reb}", {**GATE, "rebalance_days": reb})
    b = run(f"reb{reb}", {**GATE, "rebalance_days": reb, "start": OOS[0], "end": OOS[1]})
    print(f"  rebalance={reb:2d}日  FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% "
          f"turn={a['turnover']:.3f} fee=${a['fees']:5.0f} | OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f}")

print("\n############ 1c. 啟動後等待條件才進場 ############")
for cond, nm in [(1, "pts>=1"), (2, "pts>=2"), (3, "pts=3")]:
    sel = np.where((pts >= cond).values & (close.index >= base))[0]
    s = close.index[sel[0]].strftime("%Y-%m-%d")
    a = run(f"wait-{nm}", {**GATE, "start": s})
    b = run(f"wait-{nm}-nogate", {**V2, "start": s})
    print(f"  等到 {nm} 才啟動 (first={s})  gate版: cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} | "
          f"無gate版: cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f}")

print("\n############ 1d. 換倉條件過濾（只在籃子真的變了才下單）############")
for t in [None, 0.15, 0.25, 0.35]:
    a = run(f"turn{t}", {**GATE, "trigger_turnover": t})
    b = run(f"turn{t}", {**GATE, "trigger_turnover": t, "start": OOS[0], "end": OOS[1]})
    print(f"  turnover門檻={str(t):5s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} turn={a['turnover']:.3f} "
          f"| OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f}")

# ============================================================ 2. 變盤 / reversal
print("\n############ 2. 變盤因應：平倉 vs 持有 vs 反向 ############")
score = E.score_matrix(f, E.BT(momentum_mode="blend", flow_mode="none", funding_mode="fund_smooth",
                               use_funding=True, weights=V2["weights"]), COLS)
sign_map = pd.Series(np.where(ro, -1.0, 1.0), index=close.index)
score_flip = score.mul(sign_map, axis=0)

class BTScore(E2.Backtester):
    def __init__(self, *a, score=None, **k):
        super().__init__(*a, **k); self._ov = score
    def _score(self, cfg):
        return self._ov if self._ov is not None else super()._score(cfg)

bt_score = BTScore(close, op, high, low, vol, fund, f, score=score)

def run_s(tag, cfg, score_obj):
    global BT
    old = BT; BT = score_obj
    m = E.perf(BT.run(E2.CFG(**cfg))["frame"])
    BT = old
    return m

pair("A. 風險期立即平倉（v2+gate，基準）", GATE)
pair("B. 風險期持有不動（不交易）", {**V2, "gate": gate_off, "gate_action": "hold"})

# inverted book at rebalance days while risk-off
a = run_s("flip", V2, bt_score); b = run_s("flip", {**V2, "start": OOS[0], "end": OOS[1]}, bt_score)
print(f"{'C. 風險期反向下單（換倉日翻轉）':40s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% | "
      f"OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f} dd={b['max_dd_pct']:6.1f}%")

# confirmation delays
gd1 = pd.Series(np.where(ro & ro.shift(1, fill_value=False), 0.0, 1.0), index=close.index)
gd2 = pd.Series(np.where(ro & ro.shift(1, fill_value=False) & ro.shift(2, fill_value=False), 0.0, 1.0), index=close.index)
on = ~ro
ge1 = pd.Series(np.where(on & on.shift(1, fill_value=False), 1.0, 0.0), index=close.index)
ge1 = ge1.where(close.index >= close.index[60], 1.0)
pair("D. 延遲1日才平倉（等確認）", {**V2, "gate": gd1})
pair("E. 延遲2日才平倉（等確認）", {**V2, "gate": gd2})
pair("F. 延遲1日才重新進場", {**V2, "gate": ge1})

# trend-alignment purity filter (momentum 7d/30d must agree)
align = (np.sign(f["mom7"]) == np.sign(f["mom30"]))
bt_align = BTScore(close, op, high, low, vol, fund, f, score=score.where(align, 0.0))
a = run_s("align", GATE, bt_align); b = run_s("align", {**GATE, "start": OOS[0], "end": OOS[1]}, bt_align)
print(f"{'G. 順勢一致性過濾（7d/30d 同向）':40s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% | "
      f"OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f} dd={b['max_dd_pct']:6.1f}%")
a = run_s("align-nogate", V2, bt_align); b = run_s("align-nogate", {**V2, "start": OOS[0], "end": OOS[1]}, bt_align)
print(f"{'G2. 順勢一致性過濾（無gate）':40s} FULL cagr={a['cagr_pct']:6.1f}% sh={a['sharpe']:5.2f} dd={a['max_dd_pct']:6.1f}% | "
      f"OOS cagr={b['cagr_pct']:6.1f}% sh={b['sharpe']:5.2f} dd={b['max_dd_pct']:6.1f}%")

# crash-day deferral of the rebalance
allow_crash = (ret1 > -0.05).astype(bool)
allow_move = (ret1.abs() < 0.05).astype(bool)
pair("H. BTC崩跌日(<=-5%)延後換倉", {**GATE, "rebalance_allow": allow_crash})
pair("I. BTC大波動日(|move|>=5%)延後換倉", {**GATE, "rebalance_allow": allow_move})
pair("J. 延遲平倉+崩跌延後（合併）", {**V2, "gate": gd1, "rebalance_allow": allow_move})
print("\n(完成)")
