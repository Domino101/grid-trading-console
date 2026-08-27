// Phase C6：DCA 圖表元件（lightweight-charts 封裝）。
//
// E-6.2（依賴隔離）：本檔是整個前端唯一允許 import lightweight-charts 的檔案。
//   其他元件一律透過 props 傳入中性描述物件（utils/dcaChart.ts），不得直接觸碰函式庫。
// E-6.1：不得回頭改寫網格頁 GridDetail.tsx。
// G-4（前端零計算）：本檔不做任何階梯 / 均價 / ROI / 止盈價數學，只負責繪製。
// RC-12（記憶體）：useEffect cleanup 必須 chart.remove()；price line 以 ref 保存，
//   props 變動時先 removePriceLine() 再重建。

import React, { useEffect, useMemo, useRef } from 'react';
import {
    CandlestickSeries,
    LineSeries,
    LineStyle,
    createChart,
    createSeriesMarkers,
    type IChartApi,
    type IPriceLine,
    type ISeriesApi,
    type ISeriesMarkersPluginApi,
    type SeriesMarker,
    type Time,
    type UTCTimestamp,
} from 'lightweight-charts';
import {
    toCandleSeries,
    toEquitySeries,
    toMarkerSpecs,
    toPriceLineSpecs,
    type ChartCandleInput,
    type ChartLineInput,
    type ChartMarkInput,
    type ChartMarkerShape,
} from '../utils/dcaChart';

export interface DcaChartProps {
    candles: readonly ChartCandleInput[] | null | undefined;
    marks?: readonly ChartMarkInput[] | null;
    lines?: readonly ChartLineInput[] | null;
    equity?: readonly { ts: number; equity: number }[] | null;
    /** 決定 base/safety 標記的 shape/color/position（§4.7）。 */
    direction?: string | null;
    /** 決策 D-10：最後一根未收線的棒以 snapshot current_price 在客戶端補畫。 */
    liveLastPrice?: number | null;
    height?: number;
    ariaLabel?: string;
}

/** 主題色以 CSS 變數解析（稽核：不得硬編碼新色票）。 */
const cssVar = (element: HTMLElement, name: string, fallback: string): string => {
    const value = getComputedStyle(element).getPropertyValue(name).trim();
    return value.length > 0 ? value : fallback;
};

/**
 * 函式庫不支援 'cross' 形狀（SeriesMarkerShape 僅 circle/square/arrowUp/arrowDown）。
 * 在此邊界降級為 square，語意（略過）由文字承載；純函式表維持 §4.7 原始語意。
 */
const toLibShape = (shape: ChartMarkerShape): 'circle' | 'square' | 'arrowUp' | 'arrowDown' =>
    shape === 'cross' ? 'square' : shape;

const DcaChart: React.FC<DcaChartProps> = ({
    candles,
    marks,
    lines,
    equity,
    direction,
    liveLastPrice,
    height = 420,
    ariaLabel = 'DCA K 線圖表',
}) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
    const equitySeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
    const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
    const priceLinesRef = useRef<IPriceLine[]>([]);
    const fittedRef = useRef(false);

    // 純函式轉換（E-6.3）：全部數學/排序/文字都在 utils/dcaChart.ts。
    const candleSeries = useMemo(() => toCandleSeries(candles, liveLastPrice), [candles, liveLastPrice]);
    const markerSpecs = useMemo(() => toMarkerSpecs(marks, direction), [marks, direction]);
    const priceLineSpecs = useMemo(() => toPriceLineSpecs(lines), [lines]);
    const equitySeries = useMemo(() => toEquitySeries(equity), [equity]);

    // ── 建立 / 銷毀（RC-12：cleanup 必須 chart.remove()）──
    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        const chart = createChart(container, {
            width: container.clientWidth,
            height,
            layout: {
                background: { color: 'transparent' },
                textColor: cssVar(container, '--text-secondary', '#94a3b8'),
                fontFamily: cssVar(container, '--font-ui', 'sans-serif'),
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: cssVar(container, '--border-subtle', 'rgba(255,255,255,0.07)') },
                horzLines: { color: cssVar(container, '--border-subtle', 'rgba(255,255,255,0.07)') },
            },
            rightPriceScale: { borderColor: cssVar(container, '--border-default', 'rgba(255,255,255,0.12)') },
            timeScale: {
                borderColor: cssVar(container, '--border-default', 'rgba(255,255,255,0.12)'),
                timeVisible: true,
                secondsVisible: false,
            },
            crosshair: { mode: 0 },
        });

        const candleSeriesApi = chart.addSeries(CandlestickSeries, {
            upColor: cssVar(container, '--trading-green', '#10b981'),
            downColor: cssVar(container, '--trading-red', '#f43f5e'),
            borderUpColor: cssVar(container, '--trading-green', '#10b981'),
            borderDownColor: cssVar(container, '--trading-red', '#f43f5e'),
            wickUpColor: cssVar(container, '--trading-green', '#10b981'),
            wickDownColor: cssVar(container, '--trading-red', '#f43f5e'),
        });

        chartRef.current = chart;
        candleSeriesRef.current = candleSeriesApi;
        markersRef.current = createSeriesMarkers(candleSeriesApi, []);
        fittedRef.current = false;

        const observer = new ResizeObserver(() => {
            if (chartRef.current && container.clientWidth > 0) {
                chartRef.current.applyOptions({ width: container.clientWidth });
            }
        });
        observer.observe(container);

        return () => {
            observer.disconnect();
            priceLinesRef.current = [];
            markersRef.current = null;
            equitySeriesRef.current = null;
            candleSeriesRef.current = null;
            chartRef.current = null;
            // RC-12：唯一能真正釋放 canvas 的呼叫。
            chart.remove();
        };
    }, [height]);

    // ── K 線資料 ──
    useEffect(() => {
        const series = candleSeriesRef.current;
        if (!series) return;
        series.setData(
            candleSeries.map((point) => ({
                time: point.time as UTCTimestamp,
                open: point.open,
                high: point.high,
                low: point.low,
                close: point.close,
            })),
        );
        // 首次有資料時 fitContent；之後保留使用者的縮放平移位置。
        if (!fittedRef.current && candleSeries.length > 0) {
            chartRef.current?.timeScale().fitContent();
            fittedRef.current = true;
        }
    }, [candleSeries]);

    // ── 標記 ──
    useEffect(() => {
        const plugin = markersRef.current;
        const container = containerRef.current;
        if (!plugin || !container) return;
        const markers: SeriesMarker<Time>[] = markerSpecs.map((spec) => ({
            time: spec.time as UTCTimestamp,
            position: spec.position,
            shape: toLibShape(spec.shape),
            color: cssVar(container, spec.colorVar, '#94a3b8'),
            text: spec.text,
            id: spec.id,
        }));
        plugin.setMarkers(markers);
    }, [markerSpecs]);

    // ── 價格線（props 變動時整批重建，避免殘留舊線）──
    useEffect(() => {
        const series = candleSeriesRef.current;
        const container = containerRef.current;
        if (!series || !container) return;

        for (const line of priceLinesRef.current) {
            series.removePriceLine(line);
        }
        priceLinesRef.current = priceLineSpecs.map((spec) =>
            series.createPriceLine({
                price: spec.price,
                color: cssVar(container, spec.colorVar, '#94a3b8'),
                lineWidth: 1,
                lineStyle: spec.style === 'dashed' ? LineStyle.Dashed : LineStyle.Solid,
                axisLabelVisible: true,
                title: spec.title,
            }),
        );

        return () => {
            for (const line of priceLinesRef.current) {
                series.removePriceLine(line);
            }
            priceLinesRef.current = [];
        };
    }, [priceLineSpecs]);

    // ── 權益曲線（第二窗格；C7 回測結果會傳入）──
    useEffect(() => {
        const chart = chartRef.current;
        const container = containerRef.current;
        if (!chart || !container) return;

        if (equitySeries.length === 0) {
            if (equitySeriesRef.current) {
                chart.removeSeries(equitySeriesRef.current);
                equitySeriesRef.current = null;
            }
            return;
        }

        if (!equitySeriesRef.current) {
            equitySeriesRef.current = chart.addSeries(
                LineSeries,
                {
                    color: cssVar(container, '--accent-cyan', '#06b6d4'),
                    lineWidth: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                },
                1,
            );
        }
        equitySeriesRef.current.setData(
            equitySeries.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })),
        );
    }, [equitySeries]);

    return (
        <div
            ref={containerRef}
            className="dca-chart-canvas"
            role="img"
            aria-label={ariaLabel}
            style={{ width: '100%', height }}
        />
    );
};

export default DcaChart;
