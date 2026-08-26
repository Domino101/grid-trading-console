#!/usr/bin/env python3
# scripts/verify_dca_indicators.py（Phase 1 交付時一併提供）
# 指標鎖定向量的可執行版本（docs/dca_bot_implementation_plan_zh-TW.md §11）
import math
from statistics import mean, pstdev

# 確定性輸入序列（無外部依賴，可完全重現）
V = [round(100 + 10 * math.sin(i / 7.0) + 0.15 * i, 4) for i in range(120)]
TOL = 1e-6


def ema_seq(xs, n):
    """EMA，種子為前 n 根的 SMA。"""
    k = 2 / (n + 1)
    out, e = [], None
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


# RSI（Wilder 平滑）
d = [V[i] - V[i - 1] for i in range(1, len(V))]
P = 14
ag = mean(max(x, 0) for x in d[:P])
al = mean(max(-x, 0) for x in d[:P])
for x in d[P:]:
    ag = (ag * (P - 1) + max(x, 0)) / P
    al = (al * (P - 1) + max(-x, 0)) / P
rsi = 100 - 100 / (1 + ag / al)

# MACD(12,26,9)
e12, e26 = ema_seq(V, 12), ema_seq(V, 26)
line = [a - b if (a is not None and b is not None) else None
        for a, b in zip(e12, e26)]
comp = [x for x in line if x is not None]
sig = [None] * (len(V) - len(comp)) + ema_seq(comp, 9)
macd_line, macd_sig = line[-1], sig[-1]
macd_hist = macd_line - macd_sig

# Bollinger(20, 2.0)，母體標準差
w = V[-20:]
mid, sd = mean(w), pstdev(w)
lo, up = mid - 2 * sd, mid + 2 * sd

# Price Change：往回第 24 根（close[119] vs close[95]），非 V[-24]
LB = 24
pc = (V[-1] - V[-1 - LB]) / V[-1 - LB] * 100
pc23 = (V[-1] - V[-1 - 23]) / V[-1 - 23] * 100

for name, got, want in [
    ('RSI(14)', rsi, 21.977841),
    ('MACD line', macd_line, -2.240581),
    ('MACD signal', macd_sig, -0.784542),
    ('MACD hist', macd_hist, -1.456039),
    ('BB lower', lo, 105.193225),
    ('BB mid', mid, 116.876210),
    ('BB upper', up, 128.559195),
    ('priceChange(24)', pc, -11.782277),
    ('priceChange(23)', pc23, -12.373066),
]:
    assert abs(got - want) < TOL, f'{name}: got {got!r}, want {want!r}'

# 暖機期首個有效索引
assert next(i for i, x in enumerate(ema_seq(V, 12)) if x is not None) == 11
assert next(i for i, x in enumerate(line) if x is not None) == 25
assert next(i for i, x in enumerate(sig) if x is not None) == 33
print('DCA indicator vectors: PASS')
