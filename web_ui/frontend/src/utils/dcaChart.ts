// Phase C6：DCA 圖表純函式（candles→series、marks→markers、lines→priceLines）。
//
// 設計約束（計畫 §3.2 E-6.3 / 稽核 6.1 / 6.2 / 6.3）：
//   * 本檔**不得** import 圖表函式庫（E-6.2：唯一允許 import 它的檔案是 DcaChart.tsx）。
//     這裡只產生「與函式庫無關的中性描述物件」，由 DcaChart.tsx 在邊界轉成函式庫型別。
//   * 本檔**不得**做任何階梯 / 均價 / ROI / 止盈價數學（G-4：前端零計算）。
//     所有價格、數量、報酬%都由 API 直接提供，這裡只做「排序、過濾、格式化文字」。
//   * 未知一律不畫（G-5）：price == null 的價格線被丟棄，quantity == null 時文字省略數量，
//     絕不以 0 代替未知。

// ── 中性描述型別（不依賴任何圖表函式庫）─────────────────────

/** K 線序列點（秒級 unix 時間戳，與圖表函式庫的 UTCTimestamp 同語意）。 */
export interface ChartCandlePoint {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
}

/** §4.7 凍結表允許的標記形狀。'cross' 為語意值，函式庫不支援時於邊界降級。 */
export type ChartMarkerShape = 'arrowUp' | 'arrowDown' | 'circle' | 'square' | 'cross';

export type ChartMarkerPosition = 'aboveBar' | 'belowBar' | 'inBar';

/** 中性標記描述；colorVar 為 CSS 變數名，實際色值於 DcaChart.tsx 以 getComputedStyle 解析。 */
export interface ChartMarkerSpec {
    time: number;
    shape: ChartMarkerShape;
    position: ChartMarkerPosition;
    colorVar: string;
    text: string;
    id: string;
}

export type ChartLineStyleName = 'solid' | 'dashed';

/** 中性價格線描述。 */
export interface ChartPriceLineSpec {
    kind: string;
    price: number;
    colorVar: string;
    style: ChartLineStyleName;
    title: string;
}

/** 權益曲線點（第二窗格，C7 會複用）。 */
export interface ChartEquityPoint {
    time: number;
    value: number;
}

// ── API 形狀（結構型別，避免與 services/api.ts 產生循環相依）──

export interface ChartCandleInput {
    t: number;
    o: number;
    h: number;
    l: number;
    c: number;
    v?: number;
}

export interface ChartMarkInput {
    ts: number;
    price: number;
    kind: string;
    role?: string | null;
    side?: string | null;
    quantity?: number | null;
    notional?: number | null;
    slot_index?: number | null;
    cycle_index?: number | null;
    realized_pct?: number | null;
}

export interface ChartLineInput {
    kind: string;
    price?: number | null;
}

// ── §4.7 標記語意凍結表 ──────────────────────────────────────
//
// | kind                | shape                    | color token             | position           |
// |---------------------|--------------------------|-------------------------|--------------------|
// | base_filled         | arrowUp(多)/arrowDown(空) | --dca-long/--dca-short  | belowBar/aboveBar  |
// | safety_filled       | circle                   | 藍(多)/橘(空)            | belowBar/aboveBar  |
// | take_profit_filled  | arrowUp                  | 綠                      | belowBar           |
// | stop_loss_filled    | arrowDown                | 紅                      | aboveBar           |
// | order_placed        | square                   | 灰                      | inBar              |
// | skipped             | cross                    | 橘                      | inBar              |

export interface MarkSemantics {
    shape: ChartMarkerShape;
    /** 方向相依時為 [long, short]，否則單一值。 */
    shapeShort?: ChartMarkerShape;
    colorVar: string;
    colorVarShort?: string;
    position: ChartMarkerPosition;
    positionShort?: ChartMarkerPosition;
}

export const DCA_MARK_SEMANTICS: Record<string, MarkSemantics> = {
    base_filled: {
        shape: 'arrowUp',
        shapeShort: 'arrowDown',
        colorVar: '--dca-long',
        colorVarShort: '--dca-short',
        position: 'belowBar',
        positionShort: 'aboveBar',
    },
    safety_filled: {
        shape: 'circle',
        shapeShort: 'circle',
        colorVar: '--dca-safety-long',
        colorVarShort: '--dca-safety-short',
        position: 'belowBar',
        positionShort: 'aboveBar',
    },
    take_profit_filled: {
        shape: 'arrowUp',
        colorVar: '--dca-take-profit',
        position: 'belowBar',
    },
    stop_loss_filled: {
        shape: 'arrowDown',
        colorVar: '--dca-stop-loss',
        position: 'aboveBar',
    },
    order_placed: {
        shape: 'square',
        colorVar: '--dca-order-placed',
        position: 'inBar',
    },
    skipped: {
        shape: 'cross',
        colorVar: '--dca-skipped',
        position: 'inBar',
    },
};

/** §4.7 價格線語意凍結表。 */
export const DCA_LINE_SEMANTICS: Record<string, { colorVar: string; style: ChartLineStyleName; title: string }> = {
    entry: { colorVar: '--dca-line-entry', style: 'dashed', title: '入場價' },
    average_cost: { colorVar: '--dca-line-average', style: 'solid', title: '均價' },
    take_profit: { colorVar: '--dca-line-take-profit', style: 'dashed', title: '止盈' },
    next_safety: { colorVar: '--dca-line-next-safety', style: 'dashed', title: '下一安全單' },
    stop_loss: { colorVar: '--dca-line-stop-loss', style: 'dashed', title: '止損' },
};

// ── 小工具 ───────────────────────────────────────────────────

const finite = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;

/** 顯示用價格格式化（純顯示，不做金融計算）。 */
export const chartPriceText = (price: number): string => {
    const abs = Math.abs(price);
    const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
    return price.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
};

/** 帶正負號的百分比文字；null 回傳空字串（G-5：不得顯示 0）。 */
export const chartPercentText = (percent: number | null | undefined): string => {
    const value = finite(percent);
    if (value === null) return '';
    const sign = value >= 0 ? '+' : '−';
    return `${sign}${Math.abs(value).toFixed(2)}%`;
};

// ── candles → series ────────────────────────────────────────

/**
 * K 線轉序列：過濾非有限值、依時間升冪排序、去除重複時間戳（保留最後一筆）。
 *
 * `liveLastPrice`（決策 D-10）：最後一根尚未收線的棒，以 snapshot 的 current_price
 * 更新 close 並擴張 high/low。**不新增 WS、不新增輪詢端點**。
 */
export const toCandleSeries = (
    candles: readonly ChartCandleInput[] | null | undefined,
    liveLastPrice?: number | null,
): ChartCandlePoint[] => {
    if (!candles || candles.length === 0) return [];

    const byTime = new Map<number, ChartCandlePoint>();
    for (const raw of candles) {
        const t = finite(raw?.t);
        const o = finite(raw?.o);
        const h = finite(raw?.h);
        const l = finite(raw?.l);
        const c = finite(raw?.c);
        if (t === null || o === null || h === null || l === null || c === null) continue;
        byTime.set(t, { time: t, open: o, high: h, low: l, close: c });
    }

    const series = [...byTime.values()].sort((a, b) => a.time - b.time);
    const live = finite(liveLastPrice);
    if (live !== null && series.length > 0) {
        const last = series[series.length - 1];
        series[series.length - 1] = {
            ...last,
            close: live,
            high: Math.max(last.high, live),
            low: Math.min(last.low, live),
        };
    }
    return series;
};

// ── marks → markers ─────────────────────────────────────────

/** 單一標記的文字（§4.7 文字欄）。數量未知時省略，不得顯示 0（稽核 6.3）。 */
export const markerText = (mark: ChartMarkInput): string => {
    const price = finite(mark.price);
    const priceText = price === null ? '' : chartPriceText(price);
    switch (mark.kind) {
        case 'base_filled':
            return priceText ? `B @ ${priceText}` : 'B';
        case 'safety_filled': {
            const slot = mark.slot_index;
            return typeof slot === 'number' && Number.isFinite(slot) ? `S${slot}` : 'S';
        }
        case 'take_profit_filled': {
            const pct = chartPercentText(mark.realized_pct);
            return pct ? `TP ${pct}` : 'TP';
        }
        case 'stop_loss_filled': {
            const pct = chartPercentText(mark.realized_pct);
            return pct ? `SL ${pct}` : 'SL';
        }
        case 'order_placed':
            return '';
        case 'skipped':
            return '略過';
        default:
            return '';
    }
};

/**
 * 標記轉中性 marker 描述。
 *
 * `direction`：'long' | 'short'，決定 base/safety 的 shape/color/position（§4.7）。
 * 未知 kind 直接丟棄（不猜測語意）。輸出依時間升冪排序（圖表函式庫要求）。
 */
export const toMarkerSpecs = (
    marks: readonly ChartMarkInput[] | null | undefined,
    direction: string | null | undefined,
): ChartMarkerSpec[] => {
    if (!marks || marks.length === 0) return [];
    const isShort = direction === 'short';

    const specs: ChartMarkerSpec[] = [];
    marks.forEach((mark, index) => {
        const semantics = DCA_MARK_SEMANTICS[mark?.kind];
        const time = finite(mark?.ts);
        if (!semantics || time === null) return;

        specs.push({
            time,
            shape: (isShort && semantics.shapeShort) || semantics.shape,
            position: (isShort && semantics.positionShort) || semantics.position,
            colorVar: (isShort && semantics.colorVarShort) || semantics.colorVar,
            text: markerText(mark),
            id: `${mark.kind}-${time}-${index}`,
        });
    });

    return specs.sort((a, b) => a.time - b.time);
};

// ── lines → price lines ─────────────────────────────────────

/**
 * 價格線轉中性描述。price 為 null / 非有限值一律丟棄（稽核 6.3：未知不畫 0）。
 * 未知 kind 亦丟棄。
 */
export const toPriceLineSpecs = (
    lines: readonly ChartLineInput[] | null | undefined,
): ChartPriceLineSpec[] => {
    if (!lines || lines.length === 0) return [];
    const specs: ChartPriceLineSpec[] = [];
    for (const line of lines) {
        const semantics = DCA_LINE_SEMANTICS[line?.kind];
        const price = finite(line?.price);
        if (!semantics || price === null) continue;
        specs.push({
            kind: line.kind,
            price,
            colorVar: semantics.colorVar,
            style: semantics.style,
            title: `${semantics.title} ${chartPriceText(price)}`,
        });
    }
    return specs;
};

// ── equity ──────────────────────────────────────────────────

/** 權益曲線轉序列（第二窗格）。非有限值丟棄，依時間升冪排序。 */
export const toEquitySeries = (
    points: readonly { ts: number; equity: number }[] | null | undefined,
): ChartEquityPoint[] => {
    if (!points || points.length === 0) return [];
    const byTime = new Map<number, ChartEquityPoint>();
    for (const point of points) {
        const time = finite(point?.ts);
        const value = finite(point?.equity);
        if (time === null || value === null) continue;
        byTime.set(time, { time, value });
    }
    return [...byTime.values()].sort((a, b) => a.time - b.time);
};

// ── 誠實回報（稽核 6.8 / 6.9）───────────────────────────────

export interface ChartDataNotice {
    /** 是否應該顯示「資料不足／無法取得 K 線」而非空白圖表。 */
    unavailable: boolean;
    /** 空狀態文案；unavailable 為 false 時是 null。 */
    message: string | null;
    /** 右上角資料來源標籤；無需標示時為 null。 */
    sourceBadge: string | null;
    /** 是否為合成 K 線（PAPER）。 */
    synthetic: boolean;
}

/**
 * 依 data_status / source / 棒數決定空狀態與角標。
 *
 * 稽核 6.8：data_status=unavailable 或零棒 → 必須明講「資料不足」，
 *           不得畫一張空圖讓使用者誤以為市場沒有波動。
 * 稽核 6.9：source=paper_synthetic → 角標「合成資料」。
 */
export const chartDataNotice = (
    dataStatus: string | null | undefined,
    source: string | null | undefined,
    candleCount: number,
): ChartDataNotice => {
    const synthetic = source === 'paper_synthetic';
    let sourceBadge: string | null = null;
    if (synthetic) sourceBadge = '合成資料';
    else if (dataStatus === 'stale') sourceBadge = '陳舊資料';

    if (dataStatus === 'unavailable') {
        return { unavailable: true, message: '無法取得 K 線資料', sourceBadge, synthetic };
    }
    if (candleCount === 0) {
        return { unavailable: true, message: '資料不足，尚無可繪製的 K 棒', sourceBadge, synthetic };
    }
    return { unavailable: false, message: null, sourceBadge, synthetic };
};

/**
 * armed 語意（稽核 6.6）：監控中時交易所零掛單，因此 order_placed 標記本應為空。
 * 若後端仍回傳（引擎殘留），前端一律過濾掉，避免與「零掛單」文案自相矛盾。
 */
export const filterMarksForStatus = (
    marks: readonly ChartMarkInput[] | null | undefined,
    status: string | null | undefined,
): ChartMarkInput[] => {
    const list = marks ? [...marks] : [];
    if (status !== 'armed') return list;
    return list.filter((mark) => mark?.kind !== 'order_placed');
};
