#!/usr/bin/env python3
# scripts/verify_dca_convergence.py（Phase 1 交付時一併提供）
# fetch_limit=300 的收斂驗證（對應稽核項 1.5c）
import math
import random
from statistics import mean


def realistic(n, seed=42):
    """GBM 隨機遊走，波動度接近真實加密貨幣 15m K 線。"""
    random.seed(seed)
    p, out = 2500.0, []
    for _ in range(n):
        p *= math.exp(random.gauss(0, 0.004))
        out.append(round(p, 4))
    return out


def ema_seq(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            e = sum(xs[:n]) / n
            out.append(e)
        else:
            e = x * k + e * (1 - k)
            out.append(e)
    return out


def rsi_last(xs, P=14):
    d = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
    ag = mean(max(x, 0) for x in d[:P])
    al = mean(max(-x, 0) for x in d[:P])
    for x in d[P:]:
        ag = (ag * (P - 1) + max(x, 0)) / P
        al = (al * (P - 1) + max(-x, 0)) / P
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def macd_sig_last(xs):
    e12, e26 = ema_seq(xs, 12), ema_seq(xs, 26)
    line = [a - b if (a is not None and b is not None) else None
            for a, b in zip(e12, e26)]
    return ema_seq([x for x in line if x is not None], 9)[-1]


FULL = realistic(3000)

# 300 vs 301 根：已收斂，差異應可忽略
a, b = FULL[-300:], FULL[-301:]
assert abs(rsi_last(a) - rsi_last(b)) < 1e-6
assert abs(macd_sig_last(a) - macd_sig_last(b)) < 1e-6

# 50 根：明顯未收斂 —— 反向斷言，證明測試本身有鑑別力
ref_r, ref_s = rsi_last(FULL[-2500:]), macd_sig_last(FULL[-2500:])
assert abs(rsi_last(FULL[-50:]) - ref_r) > 1e-2
assert abs(macd_sig_last(FULL[-50:]) - ref_s) > 1e-2

print('DCA indicator convergence (fetch_limit=300): PASS')
