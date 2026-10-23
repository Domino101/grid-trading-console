// DCA 分頁純函式（Phase 5）：進度計算、狀態文案、距離換算、等待時長。
// 此檔不做任何階梯數學（G-4）；所有金額/數值顯示走 utils/format.ts 的 money()/number()。

import type { DcaIndicatorView, DcaSnapshotItem } from '../services/api';

export type DcaDirectionValue = 'long' | 'short' | 'both';

export interface DcaStatusMeta {
    label: string;
    badgeClass: string;
    tone: 'positive' | 'negative' | 'warning' | 'neutral';
}

// §4.6 狀態機全狀態徽章（armed/triggering/averaging/completed/cooling/stopping/stopped/failed
// ＋ 引擎附加 degraded 與命令 pending 期的 starting）；每一狀態獨立視覺與中文文案（稽核 5.11）。
export const DCA_STATUS_META: Record<string, DcaStatusMeta> = {
    armed: { label: t("監控中"), badgeClass: 'badge badge-dca-armed', tone: 'warning' },
    triggering: { label: t("進場中"), badgeClass: 'badge badge-dca-triggering', tone: 'positive' },
    averaging: { label: t("攤平中"), badgeClass: 'badge badge-dca-averaging', tone: 'positive' },
    completed: { label: t("本輪完成"), badgeClass: 'badge badge-dca-completed', tone: 'neutral' },
    cooling: { label: t("冷卻中"), badgeClass: 'badge badge-dca-cooling', tone: 'neutral' },
    stopping: { label: t("停止中"), badgeClass: 'badge badge-stopping', tone: 'warning' },
    stopped: { label: t("已停止"), badgeClass: 'badge badge-stopped', tone: 'neutral' },
    failed: { label: t("失敗"), badgeClass: 'badge badge-failed', tone: 'negative' },
    degraded: { label: t("降級"), badgeClass: 'badge badge-degraded', tone: 'negative' },
    starting: { label: t("啟動中"), badgeClass: 'badge badge-starting', tone: 'warning' },
};

export const dcaStatusMeta = (status: string | null | undefined): DcaStatusMeta =>
    (status && DCA_STATUS_META[status]) || {
        label: status ? status : t("未知"),
        badgeClass: 'badge badge-stopped',
        tone: 'neutral' as const,
    };

export const dcaStatusText = (status: string | null | undefined): string =>
    dcaStatusMeta(status).label;

export const DIRECTION_LABELS: Record<string, string> = {
    long: t("多"),
    short: t("空"),
    both: t("雙向"),
};

export const directionLabel = (direction: string | null | undefined): string =>
    DIRECTION_LABELS[direction ?? ''] ?? (direction ?? '—');

// 「運行中」口徑與後端 _RUNNING_STATUSES 對齊（另含命令 pending 期的 starting）。
export const DCA_RUNNING_STATUSES: readonly string[] = [
    'running', 'armed', 'triggering', 'averaging', 'completed', 'cooling', 'degraded', 'starting',
];

export const isDcaRunningStatus = (status: string | null | undefined): boolean =>
    typeof status === 'string' && DCA_RUNNING_STATUSES.includes(status);

// 稽核 5.7：armed 卡片必須顯示「監控中 · 尚未掛單」，且不顯示任何掛單數字或止盈價。
export const isDcaArmed = (status: string | null | undefined): boolean => status === 'armed';

export interface DcaLadderProgress {
    filled: number | null;
    max: number | null;
    percent: number | null;
}

// 安全單進度（已成交/上限）；未知一律 null，禁止 0 代替（G-5）。
export const dcaLadderProgress = (
    item: Pick<DcaSnapshotItem, 'ladder' | 'max_safety_orders'>,
): DcaLadderProgress => {
    const filled = item.ladder?.filled_safety_orders ?? null;
    const max = item.ladder?.max_safety_orders ?? item.max_safety_orders ?? null;
    const percent =
        filled != null && max != null && max > 0
            ? Math.min(100, Math.round((filled / max) * 1000) / 10)
            : null;
    return { filled, max, percent };
};

// 由 from 到 to 的有號距離百分比；任一未知或 from=0 → null。
export const distancePercent = (
    from: number | null | undefined,
    to: number | null | undefined,
): number | null => {
    if (from == null || to == null || from === 0) return null;
    return ((to - from) / from) * 100;
};

export const distanceText = (percent: number | null | undefined): string =>
    percent == null ? '—' : `${percent >= 0 ? '+' : ''}${percent.toFixed(2)}%`;

// 下一張安全單距離（多單為負 = 下方攤平；空單為正 = 上方攤平）。
export const nextSafetyDistance = (item: Pick<DcaSnapshotItem, 'current_price' | 'next_safety_order_price'>): number | null =>
    distancePercent(item.current_price, item.next_safety_order_price);

// 距止盈百分比（多單為正 = 上方止盈；空單為負）。
export const takeProfitDistance = (item: Pick<DcaSnapshotItem, 'current_price' | 'take_profit_price'>): number | null =>
    distancePercent(item.current_price, item.take_profit_price);

// 未實現 ROI% = 未實現盈虧 / 已成交成本；未知或成本為 0 → null。
export const dcaRoiPercent = (
    item: Pick<DcaSnapshotItem, 'position' | 'profit'>,
): number | null => {
    const cost = item.position?.total_cost ?? null;
    const pnl = item.profit?.unrealized ?? null;
    if (cost == null || pnl == null || cost === 0) return null;
    return (pnl / cost) * 100;
};

// 持倉名目 = 持倉量 × 現價（顯示層換算，非階梯數學）；未知 → null。
export const dcaPositionNotional = (
    item: Pick<DcaSnapshotItem, 'position' | 'current_price'>,
): number | null => {
    const quantity = item.position?.quantity ?? null;
    if (quantity == null || item.current_price == null) return null;
    return quantity * item.current_price;
};

// 等待時長格式化：秒 → 中文可讀（≥1h 顯示 h+m；≥1m 顯示 m+s；否則 s）。
export const formatWaitSeconds = (totalSeconds: number): string => {
    if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return '—';
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = Math.floor(totalSeconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
};

// armed 已等待時長；armedSince 未知 → null（顯示層改顯示「—」）。
export const armedWaitText = (
    armedSince: number | null | undefined,
    now: number,
): string | null => {
    if (armedSince == null) return null;
    const seconds = (now - armedSince * 1000) / 1000;
    return (t("已等待 ") + (formatWaitSeconds(seconds)));
};

const MACD_CONDITION_LABELS: Record<string, string> = {
    cross: t("交叉"),
    hist_sign: t("柱體符號"),
    zero_cross: t("零軸穿越"),
};

const BOLLINGER_CONDITION_LABELS: Record<string, string> = {
    touch: t("觸碰"),
    break: t("突破"),
    return: t("收回"),
};

// 單一指標的可讀 chip 文案（預設值與 §4.3 凍結預設一致）。
export const indicatorChipText = (indicator: DcaIndicatorView): string => {
    switch (indicator.type) {
        case 'rsi': {
            const period = indicator.period ?? 14;
            const oversold = indicator.oversold ?? 30;
            const overbought = indicator.overbought ?? 70;
            const condition = indicator.condition ?? 'level';
            if (condition === 'cross_in') return ("RSI(" + (period) + t(") 穿越 ") + (oversold) + "/" + (overbought));
            if (condition === 'cross_out') return ("RSI(" + (period) + t(") 穿回 ") + (oversold) + "/" + (overbought));
            return `RSI(${period}) ≤${oversold} / ≥${overbought}`;
        }
        case 'macd': {
            const fast = indicator.fast ?? 12;
            const slow = indicator.slow ?? 26;
            const signal = indicator.signal ?? 9;
            const condition = indicator.condition ?? 'cross';
            return `MACD(${fast},${slow},${signal}) ${MACD_CONDITION_LABELS[condition] ?? condition}`;
        }
        case 'bollinger': {
            const period = indicator.period ?? 20;
            const stdDev = indicator.std_dev ?? 2.0;
            const condition = indicator.condition ?? 'touch';
            return `BB(${period}, ${stdDev}) ${BOLLINGER_CONDITION_LABELS[condition] ?? condition}`;
        }
        case 'price_change': {
            const lookback = indicator.lookback_bars ?? 24;
            const longThreshold = indicator.long_threshold_percent ?? -3;
            const shortThreshold = indicator.short_threshold_percent ?? 3;
            return ("PriceChange " + (lookback) + t("根 ≤") + (longThreshold) + "% / ≥" + (shortThreshold) + "%");
        }
        default:
            return indicator.type ?? t("指標");
    }
};

export interface IndicatorLiveDisplay {
    name: string;
    valueText: string | null;
    isTriggered: boolean;
    isWarming: boolean;
    targetText: string;
    tooltip: string;
    chipClass: string;
}

export const indicatorLiveInfo = (indicator: DcaIndicatorView): IndicatorLiveDisplay => {
    const type = indicator.type ?? 'indicator';
    let name = type.toUpperCase();
    let targetText = '';

    switch (type) {
        case 'rsi': {
            const period = indicator.period ?? 14;
            const oversold = indicator.oversold ?? 30;
            const overbought = indicator.overbought ?? 70;
            name = `RSI(${period})`;
            targetText = `≤${oversold} / ≥${overbought}`;
            break;
        }
        case 'macd': {
            const fast = indicator.fast ?? 12;
            const slow = indicator.slow ?? 26;
            const signal = indicator.signal ?? 9;
            name = `MACD(${fast},${slow},${signal})`;
            const cond = indicator.condition ?? 'cross';
            targetText = MACD_CONDITION_LABELS[cond] ?? cond;
            break;
        }
        case 'bollinger': {
            const period = indicator.period ?? 20;
            const stdDev = indicator.std_dev ?? 2.0;
            name = `BB(${period},${stdDev})`;
            const cond = indicator.condition ?? 'touch';
            targetText = BOLLINGER_CONDITION_LABELS[cond] ?? cond;
            break;
        }
        case 'price_change': {
            const lookback = indicator.lookback_bars ?? 24;
            const longTh = indicator.long_threshold_percent ?? -3;
            const shortTh = indicator.short_threshold_percent ?? 3;
            name = `PC(${lookback})`;
            targetText = `≤${longTh}% / ≥${shortTh}%`;
            break;
        }
    }

    const isWarming = Boolean(indicator.insufficient_data);
    const isTriggered = Boolean(
        indicator.triggered || indicator.triggered_long || indicator.triggered_short
    );

    let valueText: string | null = null;
    if (isWarming) {
        valueText = (t("暖機 ") + (indicator.warmup_have ?? 0) + "/" + (indicator.warmup_need ?? '?'));
    } else if (indicator.current_value != null) {
        const val = indicator.current_value;
        if (type === 'rsi') {
            valueText = val.toFixed(1);
        } else if (type === 'macd') {
            valueText = `${val >= 0 ? '+' : ''}${val.toFixed(3)}`;
        } else if (type === 'bollinger') {
            valueText = `${val.toFixed(2)}`;
        } else if (type === 'price_change') {
            valueText = `${val >= 0 ? '+' : ''}${val.toFixed(2)}%`;
        } else {
            valueText = String(val);
        }
    }

    let chipClass = '';
    if (isTriggered) {
        chipClass = 'is-triggered';
    } else if (isWarming) {
        chipClass = 'is-warming';
    } else if (valueText != null) {
        chipClass = 'is-live';
    }

    const statusDesc = isTriggered ? t("已滿足入場條件") : isWarming ? t("K棒累積暖機中") : t("監控比對中");
    const valDesc = valueText != null ? (t(" | 即時值: ") + (valueText)) : '';
    const tooltip = ((name) + t(" 目標: ") + (targetText) + (valDesc) + " (" + (statusDesc) + ")");

    return {
        name,
        valueText,
        isTriggered,
        isWarming,
        targetText,
        tooltip,
        chipClass,
    };
};

// 入場區塊文案：立即入場 / 指標入場（含 AND/OR 與各指標 chip）。
export const dcaEntryText = (item: Pick<DcaSnapshotItem, 'entry_mode' | 'entry_logic' | 'entry_indicators'>): string => {
    if ((item.entry_mode ?? 'immediate') === 'immediate') return t("立即入場");
    const logic = item.entry_logic === 'any' ? t("任一 (OR)") : t("全部 (AND)");
    const names = (item.entry_indicators ?? []).map(indicatorChipText);
    return names.length > 0 ? (t("指標入場 ") + (logic) + t("：") + (names.join(' · '))) : (t("指標入場 ") + (logic));
};


// ════════════════════════════════════════════════════════════════
// Phase 6：建立 DCA 精靈純函式
// 規則：此檔只做「欄位映射」與「顯示層換算」，絕不做階梯數學（G-4 / 稽核 6.2）。
// 所有 Summary 數值的來源是 POST /api/v1/dca/preview 的回應欄位。
// ════════════════════════════════════════════════════════════════

import type {
    DcaCreateRequest,
    DcaIndicatorRequest,
    DcaPreviewRequest,
    MarketInfo,
} from '../services/api';
import { t } from '../i18n/index.ts';

export type DcaWizardMode = 'paper' | 'live';
export type DcaEntryMode = 'immediate' | 'indicator';

export interface DcaRsiParams {
    enabled: boolean;
    period: number;
    oversold: number;
    overbought: number;
    condition: 'level' | 'cross_in' | 'cross_out';
}

export interface DcaMacdParams {
    enabled: boolean;
    fast: number;
    slow: number;
    signal: number;
    condition: 'cross' | 'hist_sign' | 'zero_cross';
}

export interface DcaBollingerParams {
    enabled: boolean;
    period: number;
    std_dev: number;
    condition: 'touch' | 'break' | 'return';
}

export interface DcaPriceChangeParams {
    enabled: boolean;
    lookback_bars: number;
    long_threshold_percent: number;
    short_threshold_percent: number;
}

export interface DcaIndicatorParams {
    rsi: DcaRsiParams;
    macd: DcaMacdParams;
    bollinger: DcaBollingerParams;
    price_change: DcaPriceChangeParams;
}

// §4.3 凍結預設值（稽核 6.8：開啟指標時欄位預填值必須與此完全一致）。
export const DCA_RSI_DEFAULTS = { period: 14, oversold: 30, overbought: 70, condition: 'level' as const };
export const DCA_MACD_DEFAULTS = { fast: 12, slow: 26, signal: 9, condition: 'cross' as const };
export const DCA_BOLLINGER_DEFAULTS = { period: 20, std_dev: 2.0, condition: 'touch' as const };
export const DCA_PRICE_CHANGE_DEFAULTS = { lookback_bars: 24, long_threshold_percent: -3.0, short_threshold_percent: 3.0 };

// §4.3.0 共用管線預設值（稽核 6.8 共用區）。
export const DCA_ENTRY_DEFAULTS = {
    mode: 'immediate' as DcaEntryMode,
    logic: 'all' as const,
    timeframe: '15m',
    fetch_limit: 300,
    price_source: 'close' as const,
    confirm_bars: 1,
};

export const dcaIndicatorDefaults = (): DcaIndicatorParams => ({
    rsi: { enabled: false, ...DCA_RSI_DEFAULTS },
    macd: { enabled: false, ...DCA_MACD_DEFAULTS },
    bollinger: { enabled: false, ...DCA_BOLLINGER_DEFAULTS },
    price_change: { enabled: false, ...DCA_PRICE_CHANGE_DEFAULTS },
});

export interface DcaEntryWizard {
    mode: DcaEntryMode;
    logic: 'all' | 'any';
    timeframe: string;
    fetch_limit: number;
    price_source: 'close' | 'hlc3' | 'ohlc4';
    confirm_bars: number;
    indicators: DcaIndicatorParams;
}

export interface DcaWizardForm {
    // Step 1 — General
    symbol: string;
    exchange: string;
    direction: DcaDirectionValue;
    account_mode: DcaWizardMode;
    // market/info 對「目前」symbol+exchange 成功過（稽核 6.4）；symbol/exchange 變更時重置。
    marketInfoOk: boolean;
    // Step 2 — Entry
    entry: DcaEntryWizard;
    // Step 3 — DCA
    base_order_amount: number;
    amount_unit: 'quote' | 'base';
    dca_mode: 'order' | 'position';
    averaging_method: 'fixed_value' | 'fixed_coin';
    max_safety_orders: number;
    price_deviation_percent: number;
    order_size_multiplier: number;
    price_deviation_multiplier: number;
    take_profit_percent: number;
    stop_loss_percent: number | null;
    max_position_notional: number | null;
    fee_rate: number | null;
    leverage: number;
    margin_mode: 'cross' | 'isolated';
    repeat_after_take_profit: boolean;
    cooldown_seconds: number;
    max_cycles: number | null;
    both_tiebreak: 'long_first' | 'short_first';
    active_safety_orders_window: number;
    monitoring_interval: number;
}

// 啟用指標 → 後端 DcaIndicatorConfig 欄位名（純映射，不補運算）。
export const enabledDcaIndicators = (entry: DcaEntryWizard): DcaIndicatorRequest[] => {
    if (entry.mode !== 'indicator') return [];
    const list: DcaIndicatorRequest[] = [];
    const rsi = entry.indicators.rsi;
    if (rsi.enabled) list.push({ type: 'rsi', period: rsi.period, oversold: rsi.oversold, overbought: rsi.overbought, condition: rsi.condition });
    const macd = entry.indicators.macd;
    if (macd.enabled) list.push({ type: 'macd', fast: macd.fast, slow: macd.slow, signal: macd.signal, condition: macd.condition });
    const bb = entry.indicators.bollinger;
    if (bb.enabled) list.push({ type: 'bollinger', period: bb.period, std_dev: bb.std_dev, condition: bb.condition });
    const pc = entry.indicators.price_change;
    if (pc.enabled) {
        list.push({
            type: 'price_change',
            lookback_bars: pc.lookback_bars,
            long_threshold_percent: pc.long_threshold_percent,
            short_threshold_percent: pc.short_threshold_percent,
        });
    }
    return list;
};

// 共用管線 + 指標參數的前端輔助校驗（稽核 6.12：僅輔助，最終強制在後端）。
export const validateDcaEntry = (entry: DcaEntryWizard): string[] => {
    const errors: string[] = [];
    if (!Number.isFinite(entry.fetch_limit) || entry.fetch_limit < 100 || entry.fetch_limit > 500) {
        errors.push(t("取樣根數 fetch_limit 須介於 100–500"));
    }
    if (!Number.isFinite(entry.confirm_bars) || entry.confirm_bars < 1 || entry.confirm_bars > 5) {
        errors.push(t("確認 K 棒 confirm_bars 須介於 1–5"));
    }
    if (!entry.timeframe.trim()) errors.push(t("請選擇 K 線時框"));
    if (entry.mode === 'indicator') {
        if (enabledDcaIndicators(entry).length === 0) {
            errors.push(t("指標模式至少要啟用一種指標（RSI / MACD / BB / Price Change）"));
        }
        const p = entry.indicators;
        const inRange = (label: string, value: number, min: number, max: number) => {
            if (!Number.isFinite(value) || value < min || value > max) {
                errors.push(((label) + t(" 須介於 ") + (min) + "–" + (max)));
            }
        };
        inRange('RSI period', p.rsi.period, 2, 100);
        inRange('RSI oversold', p.rsi.oversold, 1, 50);
        inRange('RSI overbought', p.rsi.overbought, 50, 99);
        inRange('MACD fast', p.macd.fast, 2, 100);
        inRange('MACD slow', p.macd.slow, 3, 200);
        inRange('MACD signal', p.macd.signal, 2, 50);
        if (Number.isFinite(p.macd.fast) && Number.isFinite(p.macd.slow) && p.macd.slow <= p.macd.fast) {
            errors.push(t("MACD slow 必須大於 fast"));
        }
        inRange('BB period', p.bollinger.period, 2, 200);
        inRange('BB std_dev', p.bollinger.std_dev, 0.5, 5.0);
        inRange('PriceChange lookback_bars', p.price_change.lookback_bars, 1, 500);
        inRange(t("PriceChange 多門檻%"), p.price_change.long_threshold_percent, -100, 100);
        inRange(t("PriceChange 空門檻%"), p.price_change.short_threshold_percent, -100, 100);
    }
    return errors;
};

// Step 3 核心欄位的前端輔助校驗（同樣僅輔助；後端 422 為最終把關 — 稽核 6.12）。
export const validateDcaStep3 = (form: DcaWizardForm): string[] => {
    const errors: string[] = [];
    if (!Number.isFinite(form.base_order_amount) || form.base_order_amount <= 0) {
        errors.push(t("每單金額必須大於 0"));
    }
    if (!Number.isInteger(form.max_safety_orders) || form.max_safety_orders < 1 || form.max_safety_orders > 50) {
        errors.push(t("Max DCA orders 須為 1–50 的整數"));
    }
    if (!Number.isFinite(form.price_deviation_percent) || form.price_deviation_percent <= 0) {
        errors.push(t("Price deviation 必須大於 0"));
    }
    if (!Number.isFinite(form.take_profit_percent) || form.take_profit_percent <= 0) {
        errors.push(t("Take profit target(%) 必須大於 0"));
    }
    if (form.stop_loss_percent != null && (!Number.isFinite(form.stop_loss_percent) || form.stop_loss_percent <= 0)) {
        errors.push(t("Stop loss(%) 留空停用，填入時必須大於 0"));
    }
    if (!Number.isInteger(form.leverage) || form.leverage < 1 || form.leverage > 125) {
        errors.push(t("槓桿須為 1–125 的整數"));
    }
    if (!Number.isInteger(form.monitoring_interval) || form.monitoring_interval < 5 || form.monitoring_interval > 300) {
        errors.push(t("監控間隔須介於 5–300 秒"));
    }
    return errors;
};

// 步驟推進規則（稽核 6.13；比照 utils/format.ts 的 canAdvanceCreateStep）。
export const canAdvanceDcaStep = (
    step: number,
    form: DcaWizardForm,
    liveConfirmation: string,
): boolean => {
    if (step === 1) {
        return Boolean(form.symbol.trim()) && Boolean(form.exchange) && form.marketInfoOk;
    }
    if (step === 2) {
        return validateDcaEntry(form.entry).length === 0;
    }
    if (step === 3) {
        return validateDcaStep3(form).length === 0;
    }
    // step 4（Summary）：LIVE 需輸入 CONFIRM LIVE（稽核 6.14）。
    return form.account_mode !== 'live' || liveConfirmation === 'CONFIRM LIVE';
};

// 由表單組出 preview 請求（純映射；entry_price 由呼叫端传入「目前價格」）。
export const buildDcaPreviewRequest = (form: DcaWizardForm, entryPrice: number): DcaPreviewRequest => {
    return {
        symbol: form.symbol,
        exchange: form.exchange,
        direction: form.direction,
        entry_price: entryPrice,
        base_order_amount: form.base_order_amount,
        amount_unit: form.amount_unit,
        dca_mode: form.dca_mode,
        averaging_method: form.averaging_method,
        max_safety_orders: form.max_safety_orders,
        price_deviation_percent: form.price_deviation_percent,
        price_deviation_multiplier: form.price_deviation_multiplier,
        order_size_multiplier: form.order_size_multiplier,
        take_profit_percent: form.take_profit_percent,
        stop_loss_percent: form.stop_loss_percent,
        max_position_notional: form.max_position_notional,
        fee_rate: form.fee_rate,
        leverage: form.leverage,
        margin_mode: form.margin_mode,
        repeat_after_take_profit: form.repeat_after_take_profit,
        cooldown_seconds: form.cooldown_seconds,
        max_cycles: form.max_cycles,
        both_tiebreak: form.both_tiebreak,
        active_safety_orders_window: form.active_safety_orders_window,
        monitoring_interval: form.monitoring_interval,
        entry: {
            mode: form.entry.mode,
            logic: form.entry.logic,
            timeframe: form.entry.timeframe,
            fetch_limit: form.entry.fetch_limit,
            price_source: form.entry.price_source,
            confirm_bars: form.entry.confirm_bars,
            indicators: enabledDcaIndicators(form.entry),
        },
    };
};

// 由表單組出建立請求（純映射）；精度來自 market/info（稽核 6.4）。
export const buildDcaCreateRequest = (
    form: DcaWizardForm,
    entryPrice: number,
    autoStart: boolean,
    marketInfo: MarketInfo | null,
): DcaCreateRequest => {
    return {
        ...buildDcaPreviewRequest(form, entryPrice),
        account_mode: form.account_mode,
        auto_start: autoStart,
        quantity_precision: marketInfo?.quantity_precision ?? null,
        price_decimals: marketInfo?.price_precision ?? null,
    };
};

// lookback 時間換算（稽核 6.8c；顯示層換算，非階梯數學）：
// 「24 根 × 15m = 6 小時」。timeframe 解析失敗或參數無效 → null。
export const formatLookbackTime = (lookbackBars: number, timeframe: string): string | null => {
    if (!Number.isFinite(lookbackBars) || lookbackBars <= 0) return null;
    const m = /^(\d+)([smhd])$/.exec(timeframe.trim());
    if (!m) return null;
    const minutes = Number(m[1]) * { s: 1 / 60, m: 1, h: 60, d: 1440 }[m[2] as 's' | 'm' | 'h' | 'd'];
    const totalMinutes = lookbackBars * minutes;
    if (totalMinutes >= 1440) {
        const days = Math.round((totalMinutes / 1440) * 10) / 10;
        return ((lookbackBars) + t(" 根 × ") + (timeframe) + " = " + (days) + t(" 天"));
    }
    if (totalMinutes >= 60) {
        const hours = Math.round((totalMinutes / 60) * 10) / 10;
        return ((lookbackBars) + t(" 根 × ") + (timeframe) + " = " + (hours) + t(" 小時"));
    }
    const mins = Math.round(totalMinutes * 10) / 10;
    return ((lookbackBars) + t(" 根 × ") + (timeframe) + " = " + (mins) + t(" 分"));
};

// 6.15 資金口徑：後端目前沒有 DCA 專屬餘額端點 → available 為 null 時一律
// 'unknown'（不得誤判為充足，也不得誤判為不足）。
export type DcaFundsStatus = 'ok' | 'insufficient' | 'unknown';

export const dcaFundsStatus = (required: number | null, available: number | null): DcaFundsStatus => {
    if (required == null || available == null) return 'unknown';
    return required > available ? 'insufficient' : 'ok';
};

// Summary 顯示用欄位選取（純選取，不做運算；數值全部來自 preview 回應 — 稽核 6.2）。
// direction=both 時由呼叫端對 ladder_long / ladder_short 各取一次。
export interface DcaSummarySelection {
    requiredCapital: number | null;
    maxDrawdownPercent: number | null;
    maxSafetyDeviationPercent: number | null;
    finalAveragePrice: number | null;
    finalTakeProfitPrice: number | null;
    entryPrice: number | null;
}

export const selectDcaSummary = (
    requiredCapital: number | null,
    ladder: { max_drawdown_percent: number; final_average_price: number; final_take_profit_price: number; entry_price: number; levels: Array<{ index: number; deviation_percent: number | null }> } | null,
): DcaSummarySelection => {
    if (!ladder) {
        return {
            requiredCapital,
            maxDrawdownPercent: null,
            maxSafetyDeviationPercent: null,
            finalAveragePrice: null,
            finalTakeProfitPrice: null,
            entryPrice: null,
        };
    }
    const last = ladder.levels.length > 0 ? ladder.levels[ladder.levels.length - 1] : null;
    return {
        requiredCapital,
        maxDrawdownPercent: ladder.max_drawdown_percent,
        maxSafetyDeviationPercent: last ? last.deviation_percent : null,
        finalAveragePrice: ladder.final_average_price,
        finalTakeProfitPrice: ladder.final_take_profit_price,
        entryPrice: ladder.entry_price,
    };
};


// ════════════════════════════════════════════════════════════════
// Phase 7：DCA 詳情工作台純函式
// 規則：此區只做「狀態文案」與「顯示層換算」，絕不做階梯數學（G-4）。
// 所有階梯價格/數量來自 GET /api/v1/dca/{id}/ladder（引擎快照或後端重算）。
// ════════════════════════════════════════════════════════════════

export type DcaSlotStatus = 'filled' | 'active' | 'waiting' | 'skipped' | (string & {});

export interface DcaSlotStatusMeta {
    label: string;
    className: string;
}

// 階梯槽位三態（+skipped）文案與樣式（稽核 7.8）；語意不只靠顏色，皆含文字。
export const DCA_SLOT_STATUS_META: Record<string, DcaSlotStatusMeta> = {
    filled: { label: t("已成交"), className: 'dca-slot-filled' },
    active: { label: t("掛單中"), className: 'dca-slot-active' },
    waiting: { label: t("未觸及"), className: 'dca-slot-waiting' },
    skipped: { label: t("已略過"), className: 'dca-slot-skipped' },
};

export const dcaSlotStatusMeta = (status: DcaSlotStatus): DcaSlotStatusMeta =>
    DCA_SLOT_STATUS_META[status] ?? { label: status || t("未知"), className: 'dca-slot-waiting' };

// DCA 事件類型 → 中文文案（詳情「事件」時間線；未知類型原樣回傳）。
export const DCA_EVENT_LABELS: Record<string, string> = {
    engine_lifecycle: t("引擎生命週期"),
    tp_replaced: t("止盈重掛"),
    window_realigned: t("急跌重對齊"),
    position_cap_reached: t("曝險上限"),
    safety_below_min_notional: t("安全單低於最小名目"),
    stop_loss_triggered: t("止損觸發"),
    both_directions_matched: t("雙向命中（單方向互斥）"),
    command: t("控制命令"),
};

export const dcaEventLabel = (eventType: string | null | undefined): string =>
    (eventType && DCA_EVENT_LABELS[eventType]) || (eventType ?? t("事件"));

// 事件狀態語氣（決定時間線點的顏色類別）。
export const dcaEventTone = (
    eventType: string | null | undefined,
    status: string | null | undefined,
): 'positive' | 'negative' | 'warning' | 'neutral' => {
    if (status === 'failed' || eventType === 'stop_loss_triggered') return 'negative';
    if (status === 'running' || eventType === 'tp_replaced') return 'positive';
    if (eventType === 'position_cap_reached'
        || eventType === 'safety_below_min_notional'
        || eventType === 'window_realigned') return 'warning';
    return 'neutral';
};

// 訂單 role → 中文標記（詳情「訂單」tab）。
export const DCA_ORDER_ROLE_LABELS: Record<string, string> = {
    base: t("基礎單"),
    safety: t("安全單"),
    take_profit: t("止盈單"),
    stop_loss: t("止損單"),
};

export const dcaOrderRoleLabel = (role: string | null | undefined): string =>
    (role && DCA_ORDER_ROLE_LABELS[role]) || (role ?? '—');

// 成交 side → 中文（buy/sell）。
export const dcaSideLabel = (side: string | null | undefined): string =>
    side === 'buy' ? t("買入") : side === 'sell' ? t("賣出") : (side ?? '—');

// ── Phase C7：回測工作台純函式 ────────────────────────────────────────
//
// 約束（稽核 7.1 / G-4）：本區塊**只做格式化與挑選**（小數位、%、時間、排序、
// 錯誤訊息對照），**不做任何金融計算**。所有指標數值一律由 API 直接提供；
// 任何「算出來」的數字都應該在後端算好再回傳。

/** 回測期間選項（棒數 × 時框，僅供 UI 下拉；實際區間由後端依 bars 決定）。 */
export const DCA_BACKTEST_TIMEFRAMES: readonly string[] = ['5m', '15m', '1h', '4h', '1d'];

export interface DcaBacktestBarsOption {
    value: number;
    label: string;
}

export const DCA_BACKTEST_BARS_OPTIONS: readonly DcaBacktestBarsOption[] = [
    { value: 300, label: t("300 根") },
    { value: 1000, label: t("1000 根") },
    { value: 2000, label: t("2000 根") },
    { value: 5000, label: t("5000 根") },
];

/** 後端 A-4 上限（`schemas.py` BacktestRequest.bars le=20000）。 */
export const DCA_BACKTEST_MAX_BARS = 20000;

/**
 * 指標顯示格式化：未知一律「—」，**絕不以 0 代替**（稽核 7.7）。
 * 真實 0 仍會顯示為 0.00%，這與「未知」是不同語意。
 */
export const backtestPercentText = (value: number | null | undefined, digits = 2): string => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    return `${value.toFixed(digits)}%`;
};

/** 帶正負號的百分比（報酬/alpha 用）。未知為「—」。 */
export const backtestSignedPercentText = (value: number | null | undefined, digits = 2): string => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    const sign = value > 0 ? '+' : value < 0 ? '−' : '';
    return `${sign}${Math.abs(value).toFixed(digits)}%`;
};

/** 一般數值（profit_factor / 棒數等）。未知為「—」（稽核 7.7 的主要案例）。 */
export const backtestNumberText = (value: number | null | undefined, digits = 2): string => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    return value.toFixed(digits);
};

/** 依數值正負回傳語意色調；未知為 neutral（不得誤導為正向）。 */
export const backtestTone = (
    value: number | null | undefined,
): 'positive' | 'negative' | 'neutral' => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'neutral';
    if (value > 0) return 'positive';
    if (value < 0) return 'negative';
    return 'neutral';
};

/** unix 秒 → 本地日期時間；未知為「—」。 */
export const backtestTimeText = (ts: number | null | undefined): string => {
    if (typeof ts !== 'number' || !Number.isFinite(ts)) return '—';
    return new Date(ts * 1000).toLocaleString('zh-TW', { hour12: false });
};

/** 回測涵蓋區間文字（純顯示層字串拼接）。 */
export const backtestRangeText = (
    firstTs: number | null | undefined,
    lastTs: number | null | undefined,
): string => {
    if (typeof firstTs !== 'number' || typeof lastTs !== 'number') return '—';
    return `${backtestTimeText(firstTs)} ～ ${backtestTimeText(lastTs)}`;
};

export const DCA_CYCLE_EXIT_REASON_LABELS: Record<string, string> = {
    take_profit: t("止盈出場"),
    stop_loss: t("止損出場"),
    max_cycles: t("達最大循環"),
    end_of_data: t("資料結束仍持倉"),
    running: t("進行中"),
};

export const cycleExitReasonLabel = (reason: string | null | undefined): string =>
    (reason && DCA_CYCLE_EXIT_REASON_LABELS[reason]) || (reason ?? '—');

/**
 * 錯誤碼 → 中文訊息（稽核 7.6）。
 * 400/422（參數）、408（逾時）、429（頻率）、404（不存在）各有專屬文案，
 * 其餘退回後端 detail 或通用訊息；**絕不把錯誤呈現為正常結果**。
 */
export const backtestErrorText = (status: number | null, fallback: string): string => {
    switch (status) {
        case 400:
            return (t("參數不合法：") + (fallback));
        case 404:
            return t("找不到此 DCA 設定，可能已被刪除。");
        case 408:
            return t("回測執行逾時，請縮短回測棒數或改用較長的時框。");
        case 422:
            return (t("參數驗證失敗：") + (fallback));
        case 429:
            return t("請求過於頻繁，請稍候再試（K 線來源有速率限制）。");
        case 500:
        case 502:
        case 503:
            return (t("伺服器錯誤，回測未完成：") + (fallback));
        default:
            return fallback;
    }
};

/**
 * `data_status=unavailable` 時回測結果不可信（稽核 7.6）：
 * 回傳警示文案，呼叫端據此**不渲染結果圖**。
 */
export const backtestUnavailableText = (
    dataStatus: string | null | undefined,
): string | null =>
    dataStatus === 'unavailable'
        ? t("無法取得足夠的歷史 K 線，此結果不可信，請改用其他時框或縮短回測長度。")
        : null;

/**
 * truncated 文案（稽核 7.4）：必須說明「已完成 N/M 組」，
 * 且最優僅限「已完成範圍內」，不得宣稱為全域最佳。
 */
export const sweepTruncatedText = (
    truncated: boolean,
    runsCompleted: number,
    runsTotal: number,
): string | null => {
    if (!truncated) return null;
    return (t("因預算限制，僅完成 ") + (runsCompleted) + "/" + (runsTotal) + t(" 組參數；以下最優僅為「已完成範圍內」的最優，並非全域最佳。"));
};

/** 最優標題文案：truncated 與否用字不同（稽核 7.4）。 */
export const sweepBestLabel = (truncated: boolean): string =>
    truncated ? t("已完成範圍內最優") : t("最優組合");

/**
 * 掃描列排序：依指定指標由大到小；null / 有 error 的列一律沉底
 * （不得讓「算不出來」看起來像「排第一」）。純比較，不做計算。
 */
export interface SweepRunLike {
    params: Record<string, unknown>;
    metrics: Record<string, number | null>;
    error: string | null;
}

export const sortSweepRuns = <T extends SweepRunLike>(runs: readonly T[], metric: string): T[] => {
    const value = (run: T): number | null => {
        if (run.error) return null;
        const raw = run.metrics?.[metric];
        return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
    };
    return [...runs].sort((a, b) => {
        const va = value(a);
        const vb = value(b);
        if (va === null && vb === null) return 0;
        if (va === null) return 1;   // null 沉底
        if (vb === null) return -1;
        return vb - va;              // 由大到小
    });
};

/** 參數組合 → 顯示字串（穩定排序，供表格與最優列共用）。 */
export const sweepParamsText = (params: Record<string, unknown> | null | undefined): string => {
    if (!params) return '—';
    const keys = Object.keys(params).sort();
    if (keys.length === 0) return '—';
    return keys.map((k) => `${k}=${String(params[k])}`).join(' · ');
};

/** 兩個參數組合是否相同（用於在表中高亮最優列）。 */
export const sweepParamsEqual = (
    a: Record<string, unknown> | null | undefined,
    b: Record<string, unknown> | null | undefined,
): boolean => {
    if (!a || !b) return false;
    return sweepParamsText(a) === sweepParamsText(b);
};
