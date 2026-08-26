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
    armed: { label: '監控中', badgeClass: 'badge badge-dca-armed', tone: 'warning' },
    triggering: { label: '進場中', badgeClass: 'badge badge-dca-triggering', tone: 'positive' },
    averaging: { label: '攤平中', badgeClass: 'badge badge-dca-averaging', tone: 'positive' },
    completed: { label: '本輪完成', badgeClass: 'badge badge-dca-completed', tone: 'neutral' },
    cooling: { label: '冷卻中', badgeClass: 'badge badge-dca-cooling', tone: 'neutral' },
    stopping: { label: '停止中', badgeClass: 'badge badge-stopping', tone: 'warning' },
    stopped: { label: '已停止', badgeClass: 'badge badge-stopped', tone: 'neutral' },
    failed: { label: '失敗', badgeClass: 'badge badge-failed', tone: 'negative' },
    degraded: { label: '降級', badgeClass: 'badge badge-degraded', tone: 'negative' },
    starting: { label: '啟動中', badgeClass: 'badge badge-starting', tone: 'warning' },
};

export const dcaStatusMeta = (status: string | null | undefined): DcaStatusMeta =>
    (status && DCA_STATUS_META[status]) || {
        label: status ? status : '未知',
        badgeClass: 'badge badge-stopped',
        tone: 'neutral' as const,
    };

export const dcaStatusText = (status: string | null | undefined): string =>
    dcaStatusMeta(status).label;

export const DIRECTION_LABELS: Record<string, string> = {
    long: '多',
    short: '空',
    both: '雙向',
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
    return `已等待 ${formatWaitSeconds(seconds)}`;
};

const MACD_CONDITION_LABELS: Record<string, string> = {
    cross: '交叉',
    hist_sign: '柱體符號',
    zero_cross: '零軸穿越',
};

const BOLLINGER_CONDITION_LABELS: Record<string, string> = {
    touch: '觸碰',
    break: '突破',
    return: '收回',
};

// 單一指標的可讀 chip 文案（預設值與 §4.3 凍結預設一致）。
export const indicatorChipText = (indicator: DcaIndicatorView): string => {
    switch (indicator.type) {
        case 'rsi': {
            const period = indicator.period ?? 14;
            const oversold = indicator.oversold ?? 30;
            const overbought = indicator.overbought ?? 70;
            const condition = indicator.condition ?? 'level';
            if (condition === 'cross_in') return `RSI(${period}) 穿越 ${oversold}/${overbought}`;
            if (condition === 'cross_out') return `RSI(${period}) 穿回 ${oversold}/${overbought}`;
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
            return `PriceChange ${lookback}根 ≤${longThreshold}% / ≥${shortThreshold}%`;
        }
        default:
            return indicator.type ?? '指標';
    }
};

// 入場區塊文案：立即入場 / 指標入場（含 AND/OR 與各指標 chip）。
export const dcaEntryText = (item: Pick<DcaSnapshotItem, 'entry_mode' | 'entry_logic' | 'entry_indicators'>): string => {
    if ((item.entry_mode ?? 'immediate') === 'immediate') return '立即入場';
    const logic = item.entry_logic === 'any' ? '任一 (OR)' : '全部 (AND)';
    const names = (item.entry_indicators ?? []).map(indicatorChipText);
    return names.length > 0 ? `指標入場 ${logic}：${names.join(' · ')}` : `指標入場 ${logic}`;
};
