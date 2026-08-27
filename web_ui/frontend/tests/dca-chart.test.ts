// Phase C6：DCA 圖表純函式測試（node --test，零瀏覽器相依）。
// 對應稽核 6.1（標記語意一致）/ 6.3（未知不畫 0）/ 6.6（armed 語意）/ 6.8 / 6.9。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    DCA_LINE_SEMANTICS,
    DCA_MARK_SEMANTICS,
    chartDataNotice,
    chartPercentText,
    chartPriceText,
    filterMarksForStatus,
    markerText,
    toCandleSeries,
    toEquitySeries,
    toMarkerSpecs,
    toPriceLineSpecs,
} from '../src/utils/dcaChart.ts';

const candle = (t: number, o: number, h: number, l: number, c: number) => ({ t, o, h, l, c, v: 1 });

// ── 稽核 6.1：kind → shape / color / position 對照表與 §4.7 逐行相符 ──

test('6.1 mark semantics table matches the frozen §4.7 spec (long)', () => {
    const specs = toMarkerSpecs(
        [
            { ts: 10, price: 100, kind: 'base_filled' },
            { ts: 20, price: 98, kind: 'safety_filled', slot_index: 1 },
            { ts: 30, price: 105, kind: 'take_profit_filled', realized_pct: 1.25 },
            { ts: 40, price: 90, kind: 'stop_loss_filled', realized_pct: -3.5 },
            { ts: 50, price: 97, kind: 'order_placed' },
            { ts: 60, price: 96, kind: 'skipped', slot_index: 2 },
        ],
        'long',
    );

    assert.deepEqual(
        specs.map((s) => [s.shape, s.position, s.colorVar]),
        [
            ['arrowUp', 'belowBar', '--dca-long'],
            ['circle', 'belowBar', '--dca-safety-long'],
            ['arrowUp', 'belowBar', '--dca-take-profit'],
            ['arrowDown', 'aboveBar', '--dca-stop-loss'],
            ['square', 'inBar', '--dca-order-placed'],
            ['cross', 'inBar', '--dca-skipped'],
        ],
    );
});

test('6.1 short direction flips base/safety shape, colour and position', () => {
    const [base, safety] = toMarkerSpecs(
        [
            { ts: 10, price: 100, kind: 'base_filled' },
            { ts: 20, price: 102, kind: 'safety_filled', slot_index: 1 },
        ],
        'short',
    );
    assert.deepEqual([base.shape, base.position, base.colorVar], ['arrowDown', 'aboveBar', '--dca-short']);
    assert.deepEqual([safety.shape, safety.position, safety.colorVar], ['circle', 'aboveBar', '--dca-safety-short']);
});

test('6.1 take_profit / stop_loss are direction independent', () => {
    for (const direction of ['long', 'short', null]) {
        const [tp, sl] = toMarkerSpecs(
            [
                { ts: 1, price: 1, kind: 'take_profit_filled', realized_pct: 1 },
                { ts: 2, price: 1, kind: 'stop_loss_filled', realized_pct: -1 },
            ],
            direction,
        );
        assert.deepEqual([tp.shape, tp.position], ['arrowUp', 'belowBar'], String(direction));
        assert.deepEqual([sl.shape, sl.position], ['arrowDown', 'aboveBar'], String(direction));
    }
});

test('6.1 every documented kind exists in the semantics table and nothing extra', () => {
    assert.deepEqual(Object.keys(DCA_MARK_SEMANTICS).sort(), [
        'base_filled', 'order_placed', 'safety_filled', 'skipped', 'stop_loss_filled', 'take_profit_filled',
    ]);
    assert.deepEqual(Object.keys(DCA_LINE_SEMANTICS).sort(), [
        'average_cost', 'entry', 'next_safety', 'stop_loss', 'take_profit',
    ]);
});

test('unknown mark kinds are dropped rather than guessed', () => {
    assert.equal(toMarkerSpecs([{ ts: 1, price: 1, kind: 'teleported' }], 'long').length, 0);
});

test('markers are sorted ascending by time (lightweight-charts requirement)', () => {
    const specs = toMarkerSpecs(
        [
            { ts: 300, price: 1, kind: 'base_filled' },
            { ts: 100, price: 1, kind: 'base_filled' },
            { ts: 200, price: 1, kind: 'base_filled' },
        ],
        'long',
    );
    assert.deepEqual(specs.map((s) => s.time), [100, 200, 300]);
});

// ── 標記文字（§4.7 文字欄）──

test('marker text follows the frozen §4.7 text column', () => {
    assert.equal(markerText({ ts: 1, price: 2500.5, kind: 'base_filled' }), 'B @ 2500.5');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'safety_filled', slot_index: 3 }), 'S3');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'take_profit_filled', realized_pct: 1.234 }), 'TP +1.23%');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'stop_loss_filled', realized_pct: -2.5 }), 'SL −2.50%');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'order_placed' }), '');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'skipped' }), '略過');
});

// ── 稽核 6.3：未知不畫 0 ──

test('6.3 null realized_pct omits the percentage instead of showing 0.00%', () => {
    assert.equal(markerText({ ts: 1, price: 1, kind: 'take_profit_filled', realized_pct: null }), 'TP');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'stop_loss_filled' }), 'SL');
    assert.equal(chartPercentText(null), '');
    assert.equal(chartPercentText(undefined), '');
    assert.equal(chartPercentText(Number.NaN), '');
    // 真實 0 仍然要顯示 —— 「未知」與「零」不可混為一談。
    assert.equal(chartPercentText(0), '+0.00%');
});

test('6.3 null slot_index omits the number instead of showing S0', () => {
    assert.equal(markerText({ ts: 1, price: 1, kind: 'safety_filled', slot_index: null }), 'S');
    assert.equal(markerText({ ts: 1, price: 1, kind: 'safety_filled', slot_index: 0 }), 'S0');
});

test('6.3 price lines with null price produce no line at all', () => {
    const specs = toPriceLineSpecs([
        { kind: 'entry', price: null },
        { kind: 'average_cost', price: undefined },
        { kind: 'take_profit', price: Number.NaN },
        { kind: 'next_safety', price: 97.5 },
        { kind: 'stop_loss', price: null },
    ]);
    assert.deepEqual(specs.map((s) => s.kind), ['next_safety']);
    assert.equal(specs[0].price, 97.5);
    assert.equal(specs[0].style, 'dashed');
    assert.equal(specs[0].colorVar, '--dca-line-next-safety');
});

test('price line styles match §4.7 (average_cost solid, everything else dashed)', () => {
    const specs = toPriceLineSpecs([
        { kind: 'entry', price: 100 },
        { kind: 'average_cost', price: 99 },
        { kind: 'take_profit', price: 105 },
        { kind: 'next_safety', price: 97 },
        { kind: 'stop_loss', price: 90 },
    ]);
    assert.deepEqual(
        specs.map((s) => [s.kind, s.style]),
        [
            ['entry', 'dashed'],
            ['average_cost', 'solid'],
            ['take_profit', 'dashed'],
            ['next_safety', 'dashed'],
            ['stop_loss', 'dashed'],
        ],
    );
});

test('unknown line kinds are dropped', () => {
    assert.equal(toPriceLineSpecs([{ kind: 'moon', price: 1 }]).length, 0);
});

// ── candles → series ──

test('candles are sorted, de-duplicated and stripped of non-finite values', () => {
    const series = toCandleSeries([
        candle(300, 3, 3, 3, 3),
        candle(100, 1, 1, 1, 1),
        candle(100, 9, 9, 9, 9), // 同時間戳，保留最後一筆
        { t: 200, o: Number.NaN, h: 2, l: 2, c: 2, v: 1 }, // 非有限值，丟棄
    ]);
    assert.deepEqual(series.map((c) => [c.time, c.open]), [[100, 9], [300, 3]]);
});

test('D-10 liveLastPrice updates only the last candle close and expands high/low', () => {
    const series = toCandleSeries([candle(100, 1, 1, 1, 1), candle(200, 10, 11, 9, 10)], 12);
    assert.deepEqual(series[0], { time: 100, open: 1, high: 1, low: 1, close: 1 });
    assert.deepEqual(series[1], { time: 200, open: 10, high: 12, low: 9, close: 12 });
});

test('D-10 liveLastPrice below the low expands the low instead of corrupting it', () => {
    const series = toCandleSeries([candle(200, 10, 11, 9, 10)], 8);
    assert.deepEqual(series[0], { time: 200, open: 10, high: 11, low: 8, close: 8 });
});

test('null liveLastPrice leaves the candles untouched', () => {
    const series = toCandleSeries([candle(200, 10, 11, 9, 10)], null);
    assert.equal(series[0].close, 10);
});

test('empty or missing candles yield an empty series (never a fake bar)', () => {
    assert.deepEqual(toCandleSeries(null), []);
    assert.deepEqual(toCandleSeries([]), []);
    assert.deepEqual(toCandleSeries([], 100), []);
});

// ── 稽核 6.6：armed 語意 ──

test('6.6 armed status filters out every order_placed mark', () => {
    const marks = [
        { ts: 1, price: 1, kind: 'order_placed' },
        { ts: 2, price: 1, kind: 'base_filled' },
        { ts: 3, price: 1, kind: 'order_placed' },
    ];
    const filtered = filterMarksForStatus(marks, 'armed');
    assert.equal(filtered.filter((m) => m.kind === 'order_placed').length, 0);
    assert.equal(filtered.length, 1);
    // 非 armed 狀態一筆都不能少
    assert.equal(filterMarksForStatus(marks, 'averaging').length, 3);
});

test('6.6 armed + order_placed round trip produces zero markers on the chart', () => {
    const specs = toMarkerSpecs(filterMarksForStatus([{ ts: 1, price: 1, kind: 'order_placed' }], 'armed'), 'long');
    assert.equal(specs.length, 0);
});

// ── 稽核 6.8 / 6.9：誠實回報 ──

test('6.8 unavailable data_status reports a message rather than a blank chart', () => {
    const notice = chartDataNotice('unavailable', 'cache', 0);
    assert.equal(notice.unavailable, true);
    assert.ok(notice.message && notice.message.length > 0);
});

test('6.8 zero candles with a live status still reports insufficient data', () => {
    const notice = chartDataNotice('live', 'exchange', 0);
    assert.equal(notice.unavailable, true);
    assert.match(notice.message ?? '', /資料不足/);
});

test('6.8 healthy data renders the chart with no empty-state message', () => {
    const notice = chartDataNotice('live', 'exchange', 300);
    assert.equal(notice.unavailable, false);
    assert.equal(notice.message, null);
    assert.equal(notice.sourceBadge, null);
});

test('6.9 paper_synthetic source is badged as 合成資料', () => {
    const notice = chartDataNotice('paper', 'paper_synthetic', 300);
    assert.equal(notice.synthetic, true);
    assert.equal(notice.sourceBadge, '合成資料');
    assert.equal(notice.unavailable, false);
});

test('stale data is badged even when candles exist', () => {
    const notice = chartDataNotice('stale', 'cache', 300);
    assert.equal(notice.sourceBadge, '陳舊資料');
    assert.equal(notice.unavailable, false);
});

// ── equity ──

test('equity points are sorted, de-duplicated and filtered', () => {
    const series = toEquitySeries([
        { ts: 200, equity: 1010 },
        { ts: 100, equity: 1000 },
        { ts: 300, equity: Number.POSITIVE_INFINITY },
    ]);
    assert.deepEqual(series, [
        { time: 100, value: 1000 },
        { time: 200, value: 1010 },
    ]);
});

test('missing equity yields an empty series', () => {
    assert.deepEqual(toEquitySeries(null), []);
    assert.deepEqual(toEquitySeries([]), []);
});

// ── 價格格式化 ──

test('price text scales precision with magnitude and trims trailing zeros', () => {
    assert.equal(chartPriceText(2500), '2500');
    assert.equal(chartPriceText(2500.25), '2500.25');
    assert.equal(chartPriceText(12.5), '12.5');
    assert.equal(chartPriceText(0.00012345), '0.00012345');
});
