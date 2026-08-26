#!/usr/bin/env python3
# scripts/verify_dca_vectors.py（Phase 1 交付時一併提供）
# 不依賴任何測試框架，可直接 python3 執行，用於稽核現場快速驗證
# 本檔為 docs/dca_bot_implementation_plan_zh-TW.md §11 附錄的獨立參考實作。
from decimal import Decimal as D, getcontext
getcontext().prec = 28


def ladder(E, b, m, s, N, V0, tp):
    """order-averaging + fixed_value，做多"""
    E, b, m, s, V0, tp = (D(str(E)), D(str(b)) / 100, D(str(m)),
                          D(str(s)), D(str(V0)), D(str(tp)) / 100)
    p, tq, tc = E, V0 / E, V0
    rows = [(0, D(0), E, V0, tc / tq)]
    for i in range(1, N + 1):
        dev = b * m ** (i - 1)
        p = p * (1 - dev)
        v = V0 * s ** i
        tq += v / p
        tc += v
        rows.append((i, dev * 100, p, v, tc / tq))
    avg = tc / tq
    return rows, tc, (E - p) / E * 100, avg * (1 + tp)


TOL = D('1e-6')

# 向量 A
rows, required, dd, tpp = ladder(2500, 0.8, 1.2, 1.0, 20, 20, 1.0)
assert abs(rows[1][1] - D('0.8')) < TOL, rows[1][1]
assert abs(rows[20][1] - D('25.5584')) < TOL, rows[20][1]
assert abs(rows[1][2] - D('2480')) < TOL, rows[1][2]
assert abs(rows[5][2] - D('2354.614306')) < TOL, rows[5][2]
assert abs(rows[20][2] - D('496.706826')) < TOL, rows[20][2]
assert abs(rows[20][4] - D('1468.987729')) < TOL, rows[20][4]
assert abs(tpp - D('1483.677606')) < TOL, tpp
assert abs(required - D('420')) < TOL, required
assert abs(dd - D('80.131727')) < TOL, dd
print('DCA ladder vector A: PASS')

# 向量 B
rows, required, dd, tpp = ladder(2500, 1.0, 1.0, 1.5, 5, 20, 1.0)
assert abs(rows[5][2] - D('2377.475125')) < TOL, rows[5][2]
assert abs(rows[5][4] - D('2411.443413')) < TOL, rows[5][4]
assert abs(required - D('415.625')) < TOL, required
assert abs(dd - D('4.900995')) < TOL, dd
print('DCA ladder vector B: PASS')
