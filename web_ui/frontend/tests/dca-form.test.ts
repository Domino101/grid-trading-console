import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildDcaCreateRequest,
    buildDcaPreviewRequest,
    DCA_BOLLINGER_DEFAULTS,
    DCA_ENTRY_DEFAULTS,
    DCA_MACD_DEFAULTS,
    DCA_PRICE_CHANGE_DEFAULTS,
    DCA_RSI_DEFAULTS,
    canAdvanceDcaStep,
    dcaFundsStatus,
    dcaIndicatorDefaults,
    enabledDcaIndicators,
    formatLookbackTime,
    selectDcaSummary,
    validateDcaEntry,
    validateDcaStep3,
    type DcaWizardForm,
} from '../src/utils/dca.ts';

const baseForm = (patch: Partial<DcaWizardForm> = {}): DcaWizardForm => ({
    symbol: 'ETH/USDC:USDC',
    exchange: 'binance',
    direction: 'long',
    account_mode: 'paper',
    marketInfoOk: true,
    entry: {
        mode: 'immediate',
        logic: 'all',
        timeframe: '15m',
        fetch_limit: 300,
        price_source: 'close',
        confirm_bars: 1,
        indicators: dcaIndicatorDefaults(),
    },
    base_order_amount: 20,
    amount_unit: 'quote',
    dca_mode: 'order',
    averaging_method: 'fixed_value',
    max_safety_orders: 20,
    price_deviation_percent: 0.8,
    order_size_multiplier: 1.0,
    price_deviation_multiplier: 1.2,
    take_profit_percent: 1.0,
    stop_loss_percent: null,
    max_position_notional: null,
    fee_rate: null,
    leverage: 10,
    margin_mode: 'isolated',
    repeat_after_take_profit: true,
    cooldown_seconds: 300,
    max_cycles: null,
    both_tiebreak: 'long_first',
    active_safety_orders_window: 5,
    monitoring_interval: 15,
    ...patch,
});

// ── 6.8：§4.3 凍結預設值 ──

test('indicator defaults match §4.3 frozen values (6.8)', () => {
    const d = dcaIndicatorDefaults();
    assert.equal(d.rsi.period, DCA_RSI_DEFAULTS.period);
    assert.equal(d.rsi.oversold, DCA_RSI_DEFAULTS.oversold);
    assert.equal(d.rsi.overbought, DCA_RSI_DEFAULTS.overbought);
    assert.equal(d.rsi.condition, 'level');
    assert.equal(d.macd.fast, DCA_MACD_DEFAULTS.fast);
    assert.equal(d.macd.slow, DCA_MACD_DEFAULTS.slow);
    assert.equal(d.macd.signal, DCA_MACD_DEFAULTS.signal);
    assert.equal(d.macd.condition, 'cross');
    assert.equal(d.bollinger.period, DCA_BOLLINGER_DEFAULTS.period);
    assert.equal(d.bollinger.std_dev, DCA_BOLLINGER_DEFAULTS.std_dev);
    assert.equal(d.bollinger.condition, 'touch');
    assert.equal(d.price_change.lookback_bars, DCA_PRICE_CHANGE_DEFAULTS.lookback_bars);
    assert.equal(d.price_change.long_threshold_percent, DCA_PRICE_CHANGE_DEFAULTS.long_threshold_percent);
    assert.equal(d.price_change.short_threshold_percent, DCA_PRICE_CHANGE_DEFAULTS.short_threshold_percent);
    assert.deepEqual(
        [DCA_ENTRY_DEFAULTS.timeframe, DCA_ENTRY_DEFAULTS.fetch_limit, DCA_ENTRY_DEFAULTS.price_source, DCA_ENTRY_DEFAULTS.logic, DCA_ENTRY_DEFAULTS.confirm_bars],
        ['15m', 300, 'close', 'all', 1],
    );
    // 全部預設為停用（需手動開啟）
    assert.equal(d.rsi.enabled, false);
    assert.equal(d.macd.enabled, false);
    assert.equal(d.bollinger.enabled, false);
    assert.equal(d.price_change.enabled, false);
});

// ── 6.13：步驟推進規則 ──

test('step 1 requires symbol + exchange + confirmed market info (6.4/6.13)', () => {
    assert.equal(canAdvanceDcaStep(1, baseForm(), ''), true);
    assert.equal(canAdvanceDcaStep(1, baseForm({ symbol: '' }), ''), false);
    assert.equal(canAdvanceDcaStep(1, baseForm({ exchange: '' }), ''), false);
    assert.equal(canAdvanceDcaStep(1, baseForm({ marketInfoOk: false }), ''), false);
});

test('step 2: immediate always ok; indicator needs at least one enabled (6.13)', () => {
    assert.equal(canAdvanceDcaStep(2, baseForm({ entry: { ...baseForm().entry, mode: 'immediate' } }), ''), true);
    const noIndicator = { ...baseForm().entry, mode: 'indicator' as const };
    assert.equal(canAdvanceDcaStep(2, baseForm({ entry: noIndicator }), ''), false);
    const rsiOn = { ...baseForm().entry, mode: 'indicator' as const, indicators: { ...dcaIndicatorDefaults(), rsi: { ...dcaIndicatorDefaults().rsi, enabled: true } } };
    assert.equal(canAdvanceDcaStep(2, baseForm({ entry: rsiOn }), ''), true);
});

test('step 2: out-of-range indicator params block advance (6.7/6.13)', () => {
    const bad = {
        ...baseForm().entry,
        mode: 'indicator' as const,
        indicators: {
            ...dcaIndicatorDefaults(),
            rsi: { ...dcaIndicatorDefaults().rsi, enabled: true, oversold: 80 }, // > 50
        },
    };
    assert.equal(canAdvanceDcaStep(2, baseForm({ entry: bad }), ''), false);
    assert.ok(validateDcaEntry(bad).length > 0);

    const macdSlowLtFast = {
        ...baseForm().entry,
        mode: 'indicator' as const,
        indicators: {
            ...dcaIndicatorDefaults(),
            macd: { ...dcaIndicatorDefaults().macd, enabled: true, fast: 30, slow: 20 },
        },
    };
    assert.equal(canAdvanceDcaStep(2, baseForm({ entry: macdSlowLtFast }), ''), false);
});

test('step 3 requires core params (6.10/6.13)', () => {
    assert.equal(canAdvanceDcaStep(3, baseForm(), ''), true);
    assert.equal(canAdvanceDcaStep(3, baseForm({ base_order_amount: 0 }), ''), false);
    assert.equal(canAdvanceDcaStep(3, baseForm({ max_safety_orders: 0 }), ''), false);
    assert.equal(canAdvanceDcaStep(3, baseForm({ price_deviation_percent: 0 }), ''), false);
    assert.equal(canAdvanceDcaStep(3, baseForm({ take_profit_percent: 0 }), ''), false);
    assert.equal(canAdvanceDcaStep(3, baseForm({ stop_loss_percent: -1 }), ''), false);
    assert.equal(validateDcaStep3(baseForm()).length, 0);
});

test('step 4: LIVE requires CONFIRM LIVE, PAPER does not (6.14)', () => {
    const paper = baseForm();
    assert.equal(canAdvanceDcaStep(4, paper, ''), true);
    const live = baseForm({ account_mode: 'live' });
    assert.equal(canAdvanceDcaStep(4, live, ''), false);
    assert.equal(canAdvanceDcaStep(4, live, 'CONFIRM'), false);
    assert.equal(canAdvanceDcaStep(4, live, 'CONFIRM LIVE'), true);
});

// ── 6.6/6.7：payload 映射 ──

test('immediate mode payload has mode=immediate and no indicators (6.6)', () => {
    const req = buildDcaPreviewRequest(baseForm(), 2500);
    assert.equal(req.entry?.mode, 'immediate');
    assert.deepEqual(req.entry?.indicators, []);
    assert.equal(req.symbol, 'ETH/USDC:USDC');
    assert.equal(req.entry_price, 2500);
});

test('indicator mode payload maps backend field names exactly (6.7/6.8b)', () => {
    const entry = {
        ...baseForm().entry,
        mode: 'indicator' as const,
        indicators: {
            rsi: { ...dcaIndicatorDefaults().rsi, enabled: true },
            macd: { ...dcaIndicatorDefaults().macd, enabled: true },
            bollinger: { ...dcaIndicatorDefaults().bollinger, enabled: true },
            price_change: { ...dcaIndicatorDefaults().price_change, enabled: true },
        },
    };
    const req = buildDcaPreviewRequest(baseForm({ entry }), 2500);
    assert.equal(req.entry?.mode, 'indicator');
    const indicators = req.entry?.indicators ?? [];
    assert.equal(indicators.length, 4);
    assert.deepEqual(indicators[0], { type: 'rsi', period: 14, oversold: 30, overbought: 70, condition: 'level' });
    assert.deepEqual(indicators[1], { type: 'macd', fast: 12, slow: 26, signal: 9, condition: 'cross' });
    assert.deepEqual(indicators[2], { type: 'bollinger', period: 20, std_dev: 2.0, condition: 'touch' });
    assert.deepEqual(indicators[3], { type: 'price_change', lookback_bars: 24, long_threshold_percent: -3.0, short_threshold_percent: 3.0 });
});

test('disabled indicators are omitted from the payload', () => {
    const entry = {
        ...baseForm().entry,
        mode: 'indicator' as const,
        indicators: { ...dcaIndicatorDefaults(), rsi: { ...dcaIndicatorDefaults().rsi, enabled: true } },
    };
    assert.equal(enabledDcaIndicators(entry).length, 1);
});

test('create request carries account_mode, auto_start and market precisions (6.4)', () => {
    const req = buildDcaCreateRequest(
        baseForm({ account_mode: 'live' }),
        2500,
        true,
        { symbol: 'ETH/USDC:USDC', exchange: 'binance', price_precision: 2, quantity_precision: 3, min_order_amount: 5, min_notional: 5, contract_size: null, cached: false },
    );
    assert.equal(req.account_mode, 'live');
    assert.equal(req.auto_start, true);
    assert.equal(req.quantity_precision, 3);
    assert.equal(req.price_decimals, 2);
    // 沒有 marketInfo 時精度為 null（後端會自動補填）
    const noInfo = buildDcaCreateRequest(baseForm(), 2500, false, null);
    assert.equal(noInfo.quantity_precision, null);
    assert.equal(noInfo.price_decimals, null);
});

// ── 6.8c：lookback 時間換算 ──

test('formatLookbackTime converts bars x timeframe (6.8c)', () => {
    assert.equal(formatLookbackTime(24, '15m'), '24 根 × 15m = 6 小時');
    assert.equal(formatLookbackTime(48, '1h'), '48 根 × 1h = 2 天');
    assert.equal(formatLookbackTime(1, '1h'), '1 根 × 1h = 1 小時');
    assert.equal(formatLookbackTime(30, '1m'), '30 根 × 1m = 30 分');
    assert.equal(formatLookbackTime(2, '1d'), '2 根 × 1d = 2 天');
    assert.equal(formatLookbackTime(24, '7w'), null); // 無法解析的時框
    assert.equal(formatLookbackTime(0, '15m'), null);
    assert.equal(formatLookbackTime(Number.NaN, '15m'), null);
});

// ── 6.15：資金口徑（無 DCA 專屬餘額端點 → null 不得誤判）──

test('funds status: null balance is unknown, never assumed ok/insufficient (6.15)', () => {
    assert.equal(dcaFundsStatus(420, null), 'unknown');
    assert.equal(dcaFundsStatus(null, 1000), 'unknown');
    assert.equal(dcaFundsStatus(420, 1000), 'ok');
    assert.equal(dcaFundsStatus(420, 400), 'insufficient');
    assert.equal(dcaFundsStatus(420, 420), 'ok'); // 恰好足夠 = ok
});

// ── 6.2：Summary 欄位選取（純選取、末層偏移取自 levels）──

test('selectDcaSummary picks fields without arithmetic (6.2/6.3)', () => {
    const ladder = {
        entry_price: 2500,
        required_capital: 420,
        max_drawdown_percent: 80.13172696,
        final_average_price: 493.92,
        final_take_profit_price: 1483.67760612,
        levels: [
            { index: 0, deviation_percent: null, price: 2500, notional: 20, quantity: 0.008, avg_price: 2500, take_profit_price: null },
            { index: 1, deviation_percent: 0.8, price: 2480, notional: 20, quantity: 0.0081, avg_price: 2490.12, take_profit_price: null },
            { index: 20, deviation_percent: 80.13, price: 494, notional: 200, quantity: 0.405, avg_price: 493.92, take_profit_price: 1483.68 },
        ],
    };
    const s = selectDcaSummary(420, ladder);
    assert.equal(s.requiredCapital, 420);
    assert.equal(s.maxDrawdownPercent, 80.13172696);
    assert.equal(s.maxSafetyDeviationPercent, 80.13); // 末層（index 20）的 deviation
    assert.equal(s.finalTakeProfitPrice, 1483.67760612);
    assert.equal(s.entryPrice, 2500);

    const empty = selectDcaSummary(420, null);
    assert.equal(empty.requiredCapital, 420);
    assert.equal(empty.maxDrawdownPercent, null);
});
