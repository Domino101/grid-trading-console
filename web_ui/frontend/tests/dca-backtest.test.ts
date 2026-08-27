// Phase C7：回測工作台純函式測試（node --test）。
// 對應稽核 7.4（truncated 誠實）/ 7.6（錯誤路徑）/ 7.7（未知不補零）。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    DCA_BACKTEST_BARS_OPTIONS,
    DCA_BACKTEST_MAX_BARS,
    DCA_BACKTEST_TIMEFRAMES,
    DCA_CYCLE_EXIT_REASON_LABELS,
    backtestErrorText,
    backtestNumberText,
    backtestPercentText,
    backtestRangeText,
    backtestSignedPercentText,
    backtestTimeText,
    backtestTone,
    backtestUnavailableText,
    cycleExitReasonLabel,
    sortSweepRuns,
    sweepBestLabel,
    sweepParamsEqual,
    sweepParamsText,
    sweepTruncatedText,
} from '../src/utils/dca.ts';

// ── 稽核 7.7：未知值一律「—」，絕不補 0 ──

test('7.7 null / undefined / NaN metrics render as — not 0', () => {
    for (const bad of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
        assert.equal(backtestNumberText(bad as number | null), '—');
        assert.equal(backtestPercentText(bad as number | null), '—');
        assert.equal(backtestSignedPercentText(bad as number | null), '—');
    }
});

test('7.7 profit_factor=null shows — while profit_factor=0 shows 0.00', () => {
    // 這是實測回應中真實出現的案例（無虧損輪 → profit_factor 為 null）
    assert.equal(backtestNumberText(null), '—');
    assert.equal(backtestNumberText(0), '0.00');
});

test('7.7 a genuine zero return is still displayed, not swallowed as unknown', () => {
    assert.equal(backtestSignedPercentText(0), '0.00%');
    assert.equal(backtestPercentText(0), '0.00%');
});

test('signed percent uses + / − and absolute value', () => {
    assert.equal(backtestSignedPercentText(4.2645), '+4.26%');
    assert.equal(backtestSignedPercentText(-4.2121), '−4.21%');
});

test('tone is neutral for unknown, never positive', () => {
    assert.equal(backtestTone(null), 'neutral');
    assert.equal(backtestTone(undefined), 'neutral');
    assert.equal(backtestTone(Number.NaN), 'neutral');
    assert.equal(backtestTone(0), 'neutral');
    assert.equal(backtestTone(1.5), 'positive');
    assert.equal(backtestTone(-1.5), 'negative');
});

test('time helpers degrade to — on unknown input', () => {
    assert.equal(backtestTimeText(null), '—');
    assert.equal(backtestRangeText(null, 123), '—');
    assert.equal(backtestRangeText(123, null), '—');
    assert.match(backtestRangeText(1700089200, 1700268300), /～/);
});

// ── 稽核 7.6：錯誤路徑各有中文訊息 ──

test('7.6 every documented error status maps to a distinct Chinese message', () => {
    const seen = new Set<string>();
    for (const status of [400, 404, 408, 422, 429, 500]) {
        const text = backtestErrorText(status, 'detail');
        assert.ok(text.length > 0, String(status));
        assert.notEqual(text, 'detail', `${status} 應有專屬文案`);
        seen.add(text);
    }
    assert.equal(seen.size, 6, '六種錯誤碼文案必須互異');
});

test('7.6 timeout and rate-limit messages are actionable', () => {
    assert.match(backtestErrorText(408, ''), /逾時/);
    assert.match(backtestErrorText(429, ''), /頻繁/);
});

test('7.6 unknown status falls back to the raw detail', () => {
    assert.equal(backtestErrorText(null, '網路中斷'), '網路中斷');
    assert.equal(backtestErrorText(418, '奇怪的錯誤'), '奇怪的錯誤');
});

test('7.6 data_status=unavailable produces a distrust warning', () => {
    const text = backtestUnavailableText('unavailable');
    assert.ok(text);
    assert.match(text, /不可信/);
    // 正常狀態不得誤報
    assert.equal(backtestUnavailableText('paper'), null);
    assert.equal(backtestUnavailableText('live'), null);
    assert.equal(backtestUnavailableText(null), null);
});

// ── 稽核 7.4：truncated 誠實 ──

test('7.4 truncated shows completed/total and refuses to claim global optimum', () => {
    const text = sweepTruncatedText(true, 6, 24);
    assert.ok(text);
    assert.match(text, /6\/24/);
    assert.match(text, /已完成範圍內/);
    assert.match(text, /並非全域最佳/);
});

test('7.4 non-truncated sweeps show no warning at all', () => {
    assert.equal(sweepTruncatedText(false, 4, 4), null);
});

test('7.4 best label wording changes with truncation', () => {
    assert.equal(sweepBestLabel(true), '已完成範圍內最優');
    assert.equal(sweepBestLabel(false), '最優組合');
});

// ── 掃描排序（顯示層比較，非重算） ──

const run = (params: Record<string, unknown>, metrics: Record<string, number | null>, error: string | null = null) =>
    ({ params, metrics, error });

test('sweep rows sort by the chosen metric descending', () => {
    const rows = [
        run({ tp: 1.0 }, { total_return_pct: 0.5 }),
        run({ tp: 1.5 }, { total_return_pct: 2.5 }),
        run({ tp: 2.0 }, { total_return_pct: 1.5 }),
    ];
    assert.deepEqual(
        sortSweepRuns(rows, 'total_return_pct').map((r) => r.metrics.total_return_pct),
        [2.5, 1.5, 0.5],
    );
});

test('null metrics sink to the bottom instead of ranking first', () => {
    const rows = [
        run({ tp: 1.0 }, { profit_factor: null }),
        run({ tp: 1.5 }, { profit_factor: 3.2 }),
        run({ tp: 2.0 }, { profit_factor: null }),
        run({ tp: 2.5 }, { profit_factor: 1.1 }),
    ];
    const sorted = sortSweepRuns(rows, 'profit_factor');
    assert.deepEqual(sorted.map((r) => r.metrics.profit_factor), [3.2, 1.1, null, null]);
});

test('errored runs sink to the bottom even when they carry metrics', () => {
    const rows = [
        run({ tp: 1.0 }, { total_return_pct: 99 }, '參數不合法'),
        run({ tp: 1.5 }, { total_return_pct: 1 }),
    ];
    const sorted = sortSweepRuns(rows, 'total_return_pct');
    assert.equal(sorted[0].error, null, '失敗列不得排在最前面');
    assert.equal(sorted[1].error, '參數不合法');
});

test('sorting does not mutate the input array', () => {
    const rows = [run({ a: 1 }, { m: 1 }), run({ a: 2 }, { m: 9 })];
    const before = rows.map((r) => r.params.a);
    sortSweepRuns(rows, 'm');
    assert.deepEqual(rows.map((r) => r.params.a), before);
});

// ── 參數顯示與最優比對 ──

test('params text is stable regardless of key insertion order', () => {
    const a = sweepParamsText({ take_profit_percent: 1.5, max_safety_orders: 3 });
    const b = sweepParamsText({ max_safety_orders: 3, take_profit_percent: 1.5 });
    assert.equal(a, b);
    assert.equal(a, 'max_safety_orders=3 · take_profit_percent=1.5');
});

test('params text handles null / empty defensively', () => {
    assert.equal(sweepParamsText(null), '—');
    assert.equal(sweepParamsText(undefined), '—');
    assert.equal(sweepParamsText({}), '—');
});

test('best-row matching is order independent and null safe', () => {
    assert.equal(sweepParamsEqual({ a: 1, b: 2 }, { b: 2, a: 1 }), true);
    assert.equal(sweepParamsEqual({ a: 1 }, { a: 2 }), false);
    // best.params 可能為 null（實測：所有 profit_factor 皆 null 時）→ 不得誤高亮
    assert.equal(sweepParamsEqual({ a: 1 }, null), false);
    assert.equal(sweepParamsEqual(null, null), false);
});

// ── 輪次出場原因 ──

test('every engine exit_reason has a Chinese label', () => {
    for (const reason of ['take_profit', 'stop_loss', 'max_cycles', 'end_of_data', 'running']) {
        assert.ok(DCA_CYCLE_EXIT_REASON_LABELS[reason], reason);
        assert.equal(cycleExitReasonLabel(reason), DCA_CYCLE_EXIT_REASON_LABELS[reason]);
    }
    assert.equal(cycleExitReasonLabel(null), '—');
    assert.equal(cycleExitReasonLabel('unknown_reason'), 'unknown_reason');
});

// ── 面板選項與上限 ──

test('panel options stay within the backend A-4 guard', () => {
    assert.ok(DCA_BACKTEST_BARS_OPTIONS.length > 0);
    for (const opt of DCA_BACKTEST_BARS_OPTIONS) {
        assert.ok(opt.value > 0 && opt.value <= DCA_BACKTEST_MAX_BARS, String(opt.value));
    }
    assert.equal(DCA_BACKTEST_MAX_BARS, 20000);
});

test('offered timeframes are a subset of what the backend accepts', () => {
    const backendSupported = new Set(['1m', '5m', '15m', '30m', '1h', '4h', '1d']);
    for (const tf of DCA_BACKTEST_TIMEFRAMES) {
        assert.ok(backendSupported.has(tf), `後端不支援 ${tf}`);
    }
});

// ── 稽核 7.2：warnings 必須「全部」渲染，不得截斷 ──
// 本專案前端測試無 DOM 環境，故沿用 repo 既有的「原始碼契約 grep」手法
// （與 tests/test_dca_frontend_contract.py 的 _read_code 同一思路）：
// 先剝除註解，再確認渲染路徑沒有任何 slice / 上限。

import { readFileSync } from 'node:fs';

const stripComments = (src: string): string =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

const resultSource = stripComments(
    readFileSync(new URL('../src/components/DcaBacktestResult.tsx', import.meta.url), 'utf8'),
);

test('7.2 warnings are mapped in full, with no slice or cap on the list', () => {
    assert.match(resultSource, /warnings\.map\(/, '必須逐條 map warnings');
    // 任何 warnings.slice(...) / warnings 上的 0..N 限制都會讓條目數 < API 回傳數
    assert.doesNotMatch(resultSource, /warnings\.slice\(/, 'warnings 不得被 slice 截斷');
    assert.doesNotMatch(resultSource, /warnings\.filter\(/, 'warnings 不得被過濾掉任何一條');
});

test('7.2 the warnings block is driven purely by array length, not a fixed list', () => {
    assert.match(resultSource, /warnings\.length/, '以長度決定是否顯示區塊');
});

// 稽核 7.3：兩個報酬率元素缺一即失敗。
test('7.3 both total_return_pct and buy_hold_return_pct are rendered', () => {
    // 元件把 result.metrics 取別名為 m，故兩種存取形式皆可接受。
    assert.match(resultSource, /\b(metrics|m)\.total_return_pct\b/);
    assert.match(resultSource, /\b(metrics|m)\.buy_hold_return_pct\b/);
    assert.match(resultSource, /\b(metrics|m)\.alpha_pct\b/);
    // 稽核 7.3：alpha 為負必須是警示色，不得一律綠色。
    assert.match(resultSource, /alpha_pct\s*<\s*0/);
});

// 稽核 7.1：結果與掃描元件零本地金融計算。
test('7.1 backtest components never recompute ladder/financial fields locally', () => {
    for (const file of ['DcaBacktestPanel.tsx', 'DcaBacktestResult.tsx', 'DcaSweepTable.tsx']) {
        const src = stripComments(
            readFileSync(new URL(`../src/components/${file}`, import.meta.url), 'utf8'),
        );
        for (const banned of ['required_capital', 'price_deviation', 'take_profit_price']) {
            assert.ok(!src.includes(banned), `${file} 不得出現 ${banned}`);
        }
    }
});
