"""
Engine v2: adds rank hysteresis, no-trade bands, regime gate overlay, vol targeting,
drawdown throttle and per-name funding-cost filters - the machinery proposed by the audit.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import numpy as np, pandas as pd
import engine as E


@dataclass
class CFG:
    long_count: int = 5
    short_count: int = 5
    capital: float = 1000.0
    leverage: float = 1.0
    fee_bps: float = 5.0
    slip_bps: float = 2.0
    rebalance_days: int = 7
    warmup: int = 60
    momentum_mode: str = "blend"
    flow_mode: str = "none"
    funding_mode: str = "fund_smooth"
    weights: Dict[str, float] = field(default_factory=lambda: {"mom": .6, "flow": 0.0, "fund": .4, "liq": 0})
    # --- new machinery
    rank_buffer: int = 0            # hysteresis: exit only when rank falls out of K+buffer
    no_trade_band: float = 0.0      # min |dweight| (fraction of capital) to justify a trade
    min_entry_z: float = 0.0        # only enter names whose cross-sectional |z| >= threshold
    stop_loss_pct: Optional[float] = None    # per-name stop (adverse move since entry)
    take_profit_pct: Optional[float] = None
    max_funding_cost_pct: Optional[float] = None  # skip entry if daily funding cost > x% (long pays +)
    gate: Optional[pd.Series] = None             # regime gate: 0..N exposure multiplier
    gate_action: str = "scale"                   # scale | freeze (freeze=new entries blocked, g==0 flattens)
    gate_reduce_only: bool = True                # when gate<1 (scale mode), only allow risk-reducing trades
    vol_target: Optional[float] = None
    vol_lookback: int = 14
    dd_throttle: Optional[Tuple[float, float]] = None   # (dd_level, scale) e.g. (0.12, 0.5)
    dd_recovery: float = 0.05
    start: Optional[str] = None
    end: Optional[str] = None
    exec_lag: int = 0               # 0 = trade at close of signal day
    rebalance_allow: Optional[pd.Series] = None   # False = defer rebalance to next allowed day
    trigger_turnover: Optional[float] = None      # skip rebalance unless |dw|/2 >= threshold


class Backtester:
    def __init__(self, close, op, high, low, vol, fund, f):
        self.close, self.fund, self.f = close, fund, f
        self.cols = list(close.columns)
        self.dates = close.index
        self.fwd = close.pct_change(1).shift(-1)
        self.pnl_hist: list = []

    def _score(self, cfg: CFG) -> pd.DataFrame:
        bt = E.BT(momentum_mode=cfg.momentum_mode, flow_mode=cfg.flow_mode, funding_mode=cfg.funding_mode,
                  use_funding=cfg.weights.get("fund", 0) != 0, weights=cfg.weights)
        return E.score_matrix(self.f, bt, self.cols)

    def run(self, cfg: CFG, collect_positions: bool = False) -> dict:
        score = self._score(cfg)
        cols, dates = self.cols, self.dates
        fwd = self.fwd
        first = max(cfg.warmup, int(np.searchsorted(dates, pd.Timestamp(cfg.start))) if cfg.start else cfg.warmup)
        last = len(dates) - 2
        if cfg.end:
            last = min(last, int(np.searchsorted(dates, pd.Timestamp(cfg.end))) - 1)
        cost = (cfg.fee_bps + cfg.slip_bps) / 1e4

        w = np.zeros(len(cols))                 # current weights (signed fraction of capital)
        entry_px = np.full(len(cols), np.nan)
        entry_w = np.zeros(len(cols))
        equity = cfg.capital
        peak_eq = equity
        dd = 0.0
        rows, poslog = [], []
        self.pnl_hist = []
        pending: Optional[np.ndarray] = None
        pnl_hist: list = []
        deferred = False

        for t in range(first, last):
            periodic = (pending is None) or ((t - first) % cfg.rebalance_days == 0) or cfg.rebalance_days == 1
            allow = True
            if cfg.rebalance_allow is not None and dates[t] in cfg.rebalance_allow.index:
                allow = bool(cfg.rebalance_allow.loc[dates[t]])
            rebal = (periodic or deferred) and allow
            if rebal:
                deferred = False
            if periodic and not allow:
                deferred = True
            px = self.close.iloc[t].values
            target = w.copy()

            # ---------------- signal / basket construction
            if rebal:
                s = score.iloc[t].values
                valid = np.isfinite(px) & np.isfinite(fwd.iloc[t].values) & np.isfinite(s)
                sv = np.where(valid, s, np.nan)
                order = np.argsort(-np.nan_to_num(sv, nan=-1e9))
                ranked = [i for i in order if valid[i]]
                K, KB = cfg.long_count, cfg.long_count + cfg.rank_buffer
                new_w = np.zeros(len(cols))
                # --- keep existing positions that are still inside the hysteresis band
                for i in np.where(np.abs(w) > 1e-9)[0]:
                    if not valid[i]:
                        continue
                    rk = ranked.index(i) + 1
                    keep = (rk <= KB) if w[i] > 0 else (rk > len(ranked) - KB)
                    if keep:
                        new_w[i] = np.sign(w[i]) * 0.5 * cfg.leverage / K
                # --- fill the remaining slots from the top/bottom of the ranking
                longs = [i for i in ranked if sv[i] > 0][:K]
                shorts = [i for i in ranked[::-1] if sv[i] < 0][:K]
                if cfg.min_entry_z > 0:
                    longs = [i for i in longs if sv[i] >= cfg.min_entry_z]
                    shorts = [i for i in shorts if sv[i] <= -cfg.min_entry_z]
                for i in longs:
                    if new_w[i] <= 0:
                        if cfg.max_funding_cost_pct is not None and np.isfinite(self.fund.iloc[t].values[i]) \
                                and self.fund.iloc[t].values[i] * 100 > cfg.max_funding_cost_pct:
                            continue
                        new_w[i] = 0.5 * cfg.leverage / K
                for i in shorts:
                    if new_w[i] >= 0:
                        if cfg.max_funding_cost_pct is not None and np.isfinite(self.fund.iloc[t].values[i]) \
                                and -self.fund.iloc[t].values[i] * 100 > cfg.max_funding_cost_pct:
                            continue
                        new_w[i] = -0.5 * cfg.leverage / K
                if cfg.trigger_turnover is not None and pending is not None:
                    d_turn = 0.5 * float(np.abs(new_w - w).sum())
                    if d_turn < cfg.trigger_turnover:
                        rebal = False
                if rebal:
                    pending = new_w
                    target = new_w

            # ---------------- risk overlays
            scale = 1.0
            gval = 1.0
            if cfg.gate is not None and dates[t] in cfg.gate.index:
                gval = float(cfg.gate.loc[dates[t]])
                if cfg.gate_action == "scale":
                    scale *= gval
            if cfg.vol_target is not None:
                scale *= self._vol_scale(cfg, t)
            if cfg.dd_throttle is not None:
                lvl, sc = cfg.dd_throttle
                if dd <= -lvl and dd <= -(cfg.dd_recovery):
                    scale *= sc
            scale = float(np.clip(scale, 0.0, 3.0))
            desired = target * scale
            if cfg.gate_action in ("freeze", "hold") and gval < 0.999:
                # risk-off: block all new/rebalance orders while the gate is active
                if cfg.gate_action == "hold":
                    desired = w.copy()
                else:
                    desired = np.zeros(len(cols)) if gval <= 0.001 else w.copy()

            # ---------------- per-name stops
            if cfg.stop_loss_pct or cfg.take_profit_pct:
                for i in np.where(np.abs(w) > 1e-9)[0]:
                    if not np.isfinite(entry_px[i]) or not np.isfinite(px[i]) or entry_px[i] <= 0:
                        continue
                    move = px[i] / entry_px[i] - 1
                    pnl_pct = move * np.sign(w[i])
                    if cfg.stop_loss_pct is not None and pnl_pct <= -cfg.stop_loss_pct:
                        desired[i] = 0.0
                    if cfg.take_profit_pct is not None and pnl_pct >= cfg.take_profit_pct:
                        desired[i] = 0.0

            # ---------------- trading rules (reduce-only gate + no-trade band)
            delta_w = desired - w
            traded_w = np.zeros(len(cols))
            for i in range(len(cols)):
                d = delta_w[i]
                if abs(d) < 1e-12:
                    continue
                reducing = abs(desired[i]) < abs(w[i]) or np.sign(desired[i]) != np.sign(w[i])
                if cfg.gate_reduce_only and scale < 1.0 and not reducing:
                    continue
                if cfg.no_trade_band > 0 and abs(d) < cfg.no_trade_band and not (desired[i] == 0):
                    continue
                traded_w[i] = d

            cap = equity
            eff_w = w + traded_w
            gross = float(np.nansum(eff_w * cfg.capital * np.nan_to_num(fwd.iloc[t].values, nan=0.0)))
            fees = float(np.sum(np.abs(traded_w)) * cfg.capital * cost)
            fd = np.nan_to_num(self.fund.iloc[t + 1].values, nan=0.0)
            funding = float(-np.sum(eff_w * cfg.capital * fd))
            pnl = gross - fees + funding
            equity += pnl
            peak_eq = max(peak_eq, equity)
            dd = equity / peak_eq - 1 if peak_eq > 0 else -1.0
            self.pnl_hist.append(pnl / cfg.capital)

            # update entries
            for i in range(len(cols)):
                if abs(traded_w[i]) > 1e-9:
                    if abs(eff_w[i]) < 1e-9:
                        entry_px[i], entry_w[i] = np.nan, 0.0
                    elif w[i] == 0 or np.sign(eff_w[i]) != np.sign(w[i]):
                        entry_px[i], entry_w[i] = px[i], eff_w[i]
            w = eff_w
            rows.append({"date": dates[t], "gross": gross, "fees": fees, "funding": funding, "net": pnl,
                         "turnover": float(np.sum(np.abs(traded_w))), "gross_exposure": float(np.sum(np.abs(eff_w))),
                         "net_exposure": float(np.sum(eff_w)), "scale": scale,
                         "n_long": int(np.sum(eff_w > 1e-9)), "n_short": int(np.sum(eff_w < -1e-9))})
            if collect_positions:
                poslog.append({cols[i]: eff_w[i] for i in range(len(cols)) if abs(eff_w[i]) > 1e-9})

        res = pd.DataFrame(rows).set_index("date")
        for c in ["gross", "fees", "funding", "net"]:
            res[c + "_pct"] = res[c] / cfg.capital
        res["equity"] = cfg.capital + res["net"].cumsum()
        out = {"frame": res, "cfg": cfg}
        if collect_positions:
            out["positions"] = poslog
        return out

    def _vol_scale(self, cfg: CFG, t: int) -> float:
        """Scale on the strategy's own recent realised vol (portfolio-level, not cross-sectional)."""
        h = self.pnl_hist
        if len(h) < max(10, cfg.vol_lookback):
            return 1.0
        rets = np.array(h[-cfg.vol_lookback:], dtype=float)
        sig = np.std(rets) * np.sqrt(365)
        if sig < 1e-6:
            return 1.0
        return float(np.clip(cfg.vol_target / sig, 0.25, 1.5))


def summary(name: str, r: dict, capital: float = 1000.0) -> dict:
    m = E.perf(r["frame"], capital)
    print(f"{name:56s} tot={m['total_pct']:8.1f}% cagr={m['cagr_pct']:6.1f}% vol={m['vol_pct']:5.1f}% "
          f"sharpe={m['sharpe']:5.2f} dd={m['max_dd_pct']:6.1f}% hit={m['hit_pct']:4.0f}% turn={m['turnover']:.2f} "
          f"fee=${m['fees']:6.0f} net=${m['net']:7.0f}")
    return m
