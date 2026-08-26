import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    DCA_RUNNING_STATUSES,
    DCA_STATUS_META,
    armedWaitText,
    dcaEntryText,
    dcaLadderProgress,
    dcaPositionNotional,
    dcaRoiPercent,
    dcaStatusMeta,
    dcaStatusText,
    directionLabel,
    distancePercent,
    distanceText,
    formatWaitSeconds,
    indicatorChipText,
    isDcaArmed,
    isDcaRunningStatus,
    nextSafetyDistance,
    takeProfitDistance,
} from '../src/utils/dca.ts';

const NOW = 1_750_000_000_000; // 固定基準時間（ms）

test('every §4.6 state has a distinct Chinese label and badge class (5.11)', () => {
    const required = [
        'armed', 'triggering', 'averaging', 'completed',
        'cooling', 'stopping', 'stopped', 'failed', 'degraded',
    ];
    const labels = new Set<string>();
    for (const status of required) {
        const meta = DCA_STATUS_META[status];
        assert.ok(meta, `missing meta for ${status}`);
        assert.ok(meta.label.length > 0);
        assert.ok(meta.badgeClass.includes('badge'));
        labels.add(meta.label);
    }
    // 八個核心狀態文案互異（degraded/starting 允許與既有網格徽章重疊）
    const coreLabels = required.slice(0, 8).map((s) => DCA_STATUS_META[s].label);
    assert.equal(new Set(coreLabels).size, 8);
});

test('unknown status falls back to a neutral badge', () => {
    assert.equal(dcaStatusText('mystery'), 'mystery');
    assert.equal(dcaStatusText(null), '未知');
    assert.equal(dcaStatusMeta(null).tone, 'neutral');
});

test('running statuses mirror the backend _RUNNING_STATUSES', () => {
    for (const status of ['running', 'armed', 'triggering', 'averaging', 'completed', 'cooling', 'degraded']) {
        assert.equal(isDcaRunningStatus(status), true, status);
    }
    assert.equal(isDcaRunningStatus('stopped'), false);
    assert.equal(isDcaRunningStatus('stopping'), false);
    assert.equal(isDcaRunningStatus('failed'), false);
    assert.equal(isDcaRunningStatus(null), false);
    assert.equal(DCA_RUNNING_STATUSES.includes('starting'), true);
});

test('armed is the only status that hides order counts (5.7)', () => {
    assert.equal(isDcaArmed('armed'), true);
    assert.equal(isDcaArmed('triggering'), false);
    assert.equal(isDcaArmed('averaging'), false);
    assert.equal(isDcaArmed(null), false);
});

test('ladder progress is null-safe and capped at 100%', () => {
    assert.deepEqual(
        dcaLadderProgress({ ladder: null, max_safety_orders: 20 }),
        { filled: null, max: 20, percent: null },
    );
    assert.deepEqual(
        dcaLadderProgress({
            ladder: { max_safety_orders: 20, filled_safety_orders: 3, active_safety_orders: 5, take_profit_orders: 1 },
            max_safety_orders: 20,
        }),
        { filled: 3, max: 20, percent: 15 },
    );
    // filled > max 時不得超過 100（資料異常防禦）
    assert.equal(
        dcaLadderProgress({
            ladder: { max_safety_orders: 2, filled_safety_orders: 9, active_safety_orders: null, take_profit_orders: null },
            max_safety_orders: 2,
        }).percent,
        100,
    );
});

test('distance percent is signed and null-safe', () => {
    assert.equal(distancePercent(null, 2500), null);
    assert.equal(distancePercent(2500, null), null);
    assert.equal(distancePercent(0, 2500), null);
    assert.ok(Math.abs(distancePercent(2500, 2480)! - (-0.8)) < 1e-9);
    assert.ok(Math.abs(distancePercent(2500, 2520)! - 0.8) < 1e-9);
    assert.equal(distanceText(null), '—');
    assert.equal(distanceText(0.8), '+0.80%');
    assert.equal(distanceText(-0.8), '-0.80%');
});

test('next safety and take-profit distances use the right sign per direction', () => {
    const longBot = { current_price: 2500, next_safety_order_price: 2480, take_profit_price: 2545 };
    assert.ok(nextSafetyDistance(longBot) as number < 0, 'long safety below market');
    assert.ok(takeProfitDistance(longBot) as number > 0, 'long TP above market');
    const shortBot = { current_price: 2500, next_safety_order_price: 2520, take_profit_price: 2455 };
    assert.ok(nextSafetyDistance(shortBot) as number > 0, 'short safety above market');
    assert.ok(takeProfitDistance(shortBot) as number < 0, 'short TP below market');
});

test('ROI and notional are null-safe (no zero substitution, G-5)', () => {
    assert.equal(
        dcaRoiPercent({ position: null, profit: null }),
        null,
    );
    assert.equal(
        dcaRoiPercent({
            position: { quantity: 1, average_cost: 2500, total_cost: 0 },
            profit: { realized: 0, unrealized: 1, fees: 0 },
        }),
        null,
        'cost=0 must be unknown, not Infinity/0',
    );
    const roi = dcaRoiPercent({
        position: { quantity: 2, average_cost: 2480, total_cost: 4960 },
        profit: { realized: 0, unrealized: 49.6, fees: 0 },
    });
    assert.ok(Math.abs(roi! - 1) < 1e-9);
    assert.equal(dcaPositionNotional({ position: null, current_price: 2500 }), null);
    assert.ok(
        Math.abs(dcaPositionNotional({ position: { quantity: 1.2, average_cost: null, total_cost: null }, current_price: 2500 })! - 3000) < 1e-9,
    );
});

test('wait time formatting covers s/m/h buckets', () => {
    assert.equal(formatWaitSeconds(5), '5s');
    assert.equal(formatWaitSeconds(95), '1m 35s');
    assert.equal(formatWaitSeconds(3723), '1h 2m');
    assert.equal(formatWaitSeconds(-1), '—');
    assert.equal(formatWaitSeconds(Number.NaN), '—');
});

test('armed wait text is null without armed_since', () => {
    assert.equal(armedWaitText(null, NOW), null);
    assert.equal(armedWaitText((NOW / 1000) - 125, NOW), '已等待 2m 5s');
});

test('direction labels follow the plan (long/short/both)', () => {
    assert.equal(directionLabel('long'), '多');
    assert.equal(directionLabel('short'), '空');
    assert.equal(directionLabel('both'), '雙向');
    assert.equal(directionLabel(null), '—');
});

test('indicator chips use §4.3 frozen defaults when params absent', () => {
    assert.equal(indicatorChipText({ type: 'rsi' }), 'RSI(14) ≤30 / ≥70');
    assert.equal(indicatorChipText({ type: 'rsi', condition: 'cross_in' }), 'RSI(14) 穿越 30/70');
    assert.equal(indicatorChipText({ type: 'rsi', condition: 'cross_out' }), 'RSI(14) 穿回 30/70');
    assert.equal(indicatorChipText({ type: 'macd' }), 'MACD(12,26,9) 交叉');
    assert.equal(indicatorChipText({ type: 'macd', fast: 8, slow: 21, condition: 'zero_cross' }), 'MACD(8,21,9) 零軸穿越');
    assert.equal(indicatorChipText({ type: 'bollinger' }), 'BB(20, 2) 觸碰');
    assert.equal(indicatorChipText({ type: 'price_change' }), 'PriceChange 24根 ≤-3% / ≥3%');
    assert.equal(indicatorChipText({ type: 'price_change', lookback_bars: 48 }), 'PriceChange 48根 ≤-3% / ≥3%');
    assert.equal(indicatorChipText({ type: 'unknown_type' }), 'unknown_type');
});

test('entry text distinguishes immediate from indicator modes', () => {
    assert.equal(dcaEntryText({ entry_mode: 'immediate', entry_logic: null, entry_indicators: [] }), '立即入場');
    assert.equal(dcaEntryText({ entry_mode: null, entry_logic: null, entry_indicators: [] }), '立即入場');
    assert.equal(
        dcaEntryText({
            entry_mode: 'indicator',
            entry_logic: 'any',
            entry_indicators: [{ type: 'rsi' }, { type: 'macd' }],
        }),
        '指標入場 任一 (OR)：RSI(14) ≤30 / ≥70 · MACD(12,26,9) 交叉',
    );
    assert.equal(
        dcaEntryText({ entry_mode: 'indicator', entry_logic: 'all', entry_indicators: [{ type: 'bollinger' }] }),
        '指標入場 全部 (AND)：BB(20, 2) 觸碰',
    );
});
