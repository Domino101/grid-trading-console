import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeft,
    RefreshCcw,
    Activity,
    Layers,
    Loader2,
    Sliders,
    Clock,
    LineChart,
    ListTree,
    AlertTriangle,
    CandlestickChart,
    FlaskConical,
} from 'lucide-react';
import {
    api,
    type BacktestResult,
    type SweepResponse,
    type DcaBacktestTimeframe,
    type DcaChartResponse,
    type KlineCandle,
    type DcaEvent,
    type DcaFillsResponse,
    type DcaLadderResponse,
    type DcaOrdersResponse,
    type DcaSnapshotItem,
    type DcaStatsResponse,
} from '../services/api';
import { ApiError } from '../utils/auth';
import { freshnessText, money, number } from '../utils/format';
import {
    dcaEntryText,
    dcaEventLabel,
    dcaEventTone,
    dcaOrderRoleLabel,
    dcaRoiPercent,
    dcaSideLabel,
    directionLabel,
    indicatorLiveInfo,
    isDcaArmed,
} from '../utils/dca';
import DcaStatusBadge from './DcaStatusBadge';
import DcaLadderTable from './DcaLadderTable';
import DcaBacktestPanel from './DcaBacktestPanel';
import { chartDataNotice, filterMarksForStatus } from '../utils/dcaChart';
import { backtestErrorText } from '../utils/dca';

// 回測結果較重（含圖表），與 DcaChart 同樣採動態匯入切成獨立 chunk。
const DcaBacktestResult = lazy(() => import('./DcaBacktestResult'));
const DcaSweepTable = lazy(() => import('./DcaSweepTable'));

// E-6.2 / 稽核 6.10：圖表函式庫只在 DcaChart.tsx 內，並以動態匯入切成
// 獨立 chunk（沿用 App.tsx 對 GridDetail 的模式），不得灌進 index-*.js 拖慢首屏。
const DcaChart = lazy(() => import('./DcaChart'));

interface DcaDetailProps {
    bot: DcaSnapshotItem;
    onBack: () => void;
    onUnauthorized: () => void;
}

type TabType = 'chart' | 'backtest' | 'ladder' | 'orders' | 'fills' | 'events' | 'indicators' | 'specs';

// 長列表門檻（稽核 7.9）：超過此數改為分頁，避免首屏卡頓。
const PAGE_SIZE = 50;

// 圖表分頁輪詢節奏（決策 D-10）：對齊 DcaDashboard.tsx 的 REFRESH_MS。
const CHART_REFRESH_MS = 15_000;

const CHART_TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d'] as const;
type ChartTimeframe = (typeof CHART_TIMEFRAMES)[number];

const CHART_LIMIT = 300;

// D-11 固定簡化事項：執行前就揭露（稽核 7.2）。有結果後另外渲染 API 的 warnings。
/** 把「1.0, 1.5, 2.0」解析成數字陣列；忽略空白與非數字項（純輸入解析，非金融計算）。 */
const parseSweepAxis = (raw: string): number[] =>
    raw.split(',').map((part) => Number(part.trim())).filter((n) => Number.isFinite(n));

const BACKTEST_DISCLOSURES = [
    '回測不模擬部分成交：每張單一次整張成交或完全不成交。',
    '除跳空外不套用滑價模型；手續費採設定檔的 fee_rate。',
    '未套用交易所最小名目限制（與實盤路徑一致）。',
    '歷史績效不代表未來，回測結果僅供參考。',
] as const;

const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;
const str = (v: unknown): string | null =>
    typeof v === 'string' ? v : v == null ? null : String(v);

const DcaDetail: React.FC<DcaDetailProps> = ({ bot, onBack, onUnauthorized }) => {
    const [activeTab, setActiveTab] = useState<TabType>('ladder');
    const [stats, setStats] = useState<DcaStatsResponse | null>(null);
    const [ladder, setLadder] = useState<DcaLadderResponse | null>(null);
    const [orders, setOrders] = useState<DcaOrdersResponse | null>(null);
    const [fills, setFills] = useState<DcaFillsResponse | null>(null);
    const [events, setEvents] = useState<DcaEvent[]>([]);
    const [yamlText, setYamlText] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [fillsPage, setFillsPage] = useState(0);
    const abortRef = useRef<AbortController | null>(null);

    // ── Phase C6 圖表分頁狀態 ──
    const [chart, setChart] = useState<DcaChartResponse | null>(null);
    const [chartTimeframe, setChartTimeframe] = useState<ChartTimeframe>('15m');
    const [chartError, setChartError] = useState<string | null>(null);
    const [chartLoading, setChartLoading] = useState(false);
    const chartAbortRef = useRef<AbortController | null>(null);

    // ── Phase C7 回測分頁狀態 ──
    const [btTimeframe, setBtTimeframe] = useState<DcaBacktestTimeframe>('15m');
    const [btBars, setBtBars] = useState(1000);
    const [btCapital, setBtCapital] = useState('1000');
    const [btResult, setBtResult] = useState<BacktestResult | null>(null);
    const [btCandles, setBtCandles] = useState<KlineCandle[] | null>(null);
    const [btError, setBtError] = useState<string | null>(null);
    const [btRunning, setBtRunning] = useState(false);
    const btAbortRef = useRef<AbortController | null>(null);

    // 參數掃描（sweep）：與單次回測共用時框/棒數，僅多兩條掃描軸。
    const [swTpRange, setSwTpRange] = useState('1.0, 1.5, 2.0');
    const [swSoRange, setSwSoRange] = useState('2, 3, 4');
    const [swBestBy, setSwBestBy] = useState('total_return_pct');
    const [swResult, setSwResult] = useState<SweepResponse | null>(null);
    const [swError, setSwError] = useState<string | null>(null);
    const [swRunning, setSwRunning] = useState(false);
    const swAbortRef = useRef<AbortController | null>(null);

    const botId = bot.bot_id;

    const load = useCallback(async () => {
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        setLoading(true);
        try {
            const [nextStats, nextLadder, nextOrders, nextFills, nextEvents] = await Promise.all([
                api.getDcaStats(botId, controller.signal),
                api.getDcaLadder(botId, controller.signal),
                api.getDcaOrders(botId, controller.signal),
                api.getDcaFills(botId, controller.signal),
                api.listDcaEvents(botId, controller.signal),
            ]);
            setStats(nextStats);
            setLadder(nextLadder);
            setOrders(nextOrders);
            setFills(nextFills);
            setEvents(nextEvents.events);
            setError(null);
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            if (caught instanceof DOMException && caught.name === 'AbortError') return;
            setError(caught instanceof Error ? caught.message : '無法載入 DCA 詳情');
        } finally {
            setLoading(false);
        }
    }, [botId, onUnauthorized]);

    useEffect(() => {
        const initial = window.setTimeout(() => {
            void load();
        }, 0);
        return () => {
            window.clearTimeout(initial);
            abortRef.current?.abort();
        };
    }, [load]);

    // 設定 tab 首次開啟時才拉 YAML 原文（唯讀）；沿用同源 /api/config 端點。
    const loadYaml = useCallback(async () => {
        if (yamlText !== null) return;
        try {
            const text = await api.getConfig(bot.config_path);
            setYamlText(text);
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            setYamlText('# 無法載入 YAML 原文');
        }
    }, [yamlText, bot.config_path, onUnauthorized]);

    // 圖表資料（K 線 + marks + lines）；此元件負責抓取，DcaChart 為純受控元件。
    const loadChart = useCallback(async () => {
        chartAbortRef.current?.abort();
        const controller = new AbortController();
        chartAbortRef.current = controller;
        setChartLoading(true);
        try {
            const next = await api.getDcaChart(botId, chartTimeframe, CHART_LIMIT, controller.signal);
            setChart(next);
            setChartError(null);
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            if (caught instanceof DOMException && caught.name === 'AbortError') return;
            setChartError(caught instanceof Error ? caught.message : '無法載入圖表資料');
        } finally {
            setChartLoading(false);
        }
    }, [botId, chartTimeframe, onUnauthorized]);

    // 稽核 6.5：只有停留在圖表分頁時才輪詢；切離分頁/卸載即 clearInterval + abort。
    useEffect(() => {
        if (activeTab !== 'chart') return;
        // 首抓延後到 effect 之外（沿用本檔既有 load 的 setTimeout(0) 模式），
        // 避免在 effect body 內同步 setState 造成串連 render。
        const initial = window.setTimeout(() => {
            void loadChart();
        }, 0);
        const timer = window.setInterval(() => {
            void loadChart();
        }, CHART_REFRESH_MS);
        return () => {
            window.clearTimeout(initial);
            window.clearInterval(timer);
            chartAbortRef.current?.abort();
        };
    }, [activeTab, loadChart]);

    // 回測執行：同時取回測結果與同區間 K 線（結果圖需要）。
    // 稽核 7.5：執行中禁用按鈕；切離分頁或卸載時 abort。
    const runBacktest = useCallback(async () => {
        btAbortRef.current?.abort();
        const controller = new AbortController();
        btAbortRef.current = controller;
        setBtRunning(true);
        setBtError(null);
        try {
            const response = await api.runDcaBacktest(
                { bot_id: botId, timeframe: btTimeframe, bars: btBars, initial_capital: btCapital },
                controller.signal,
            );
            setBtResult(response.result);

            // 優先使用回測返回的 K 線（保證時空標記一致），無附帶時才降級查詢
            if (response.result.candles && response.result.candles.length > 0) {
                setBtCandles(response.result.candles);
            } else if (bot.symbol && bot.exchange) {
                try {
                    const klines = await api.getKlines(
                        bot.symbol, bot.exchange, btTimeframe, btBars, controller.signal,
                    );
                    setBtCandles(klines.candles);
                } catch {
                    setBtCandles(null);
                }
            }
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            if (caught instanceof DOMException && caught.name === 'AbortError') return;
            const status = caught instanceof ApiError ? caught.status : null;
            const fallback = caught instanceof Error ? caught.message : '回測失敗';
            setBtError(backtestErrorText(status, fallback));
            setBtResult(null);
            setBtCandles(null);
        } finally {
            setBtRunning(false);
        }
    }, [botId, btTimeframe, btBars, btCapital, bot.symbol, bot.exchange, onUnauthorized]);

    const runSweep = useCallback(async () => {
        swAbortRef.current?.abort();
        const controller = new AbortController();
        swAbortRef.current = controller;
        setSwRunning(true);
        setSwError(null);
        try {
            const response = await api.runDcaSweep(
                {
                    bot_id: botId,
                    timeframe: btTimeframe,
                    bars: btBars,
                    initial_capital: btCapital,
                    best_by: swBestBy,
                    take_profit_percent_range: parseSweepAxis(swTpRange),
                    max_safety_orders_range: parseSweepAxis(swSoRange).map((n) => Math.round(n)),
                },
                controller.signal,
            );
            setSwResult(response);
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            if (caught instanceof DOMException && caught.name === 'AbortError') return;
            const status = caught instanceof ApiError ? caught.status : null;
            const fallback = caught instanceof Error ? caught.message : '掃描失敗';
            setSwError(backtestErrorText(status, fallback));
            setSwResult(null);
        } finally {
            setSwRunning(false);
        }
    }, [botId, btTimeframe, btBars, btCapital, swBestBy, swTpRange, swSoRange, onUnauthorized]);

    const cancelSweep = useCallback(() => {
        swAbortRef.current?.abort();
        setSwRunning(false);
    }, []);

    const cancelBacktest = useCallback(() => {
        btAbortRef.current?.abort();
        setBtRunning(false);
    }, []);

    // 稽核 7.5：離開回測分頁 / 卸載元件即取消進行中的長任務。
    useEffect(() => {
        if (activeTab === 'backtest') return;
        btAbortRef.current?.abort();
        swAbortRef.current?.abort();
    }, [activeTab]);

    useEffect(() => () => {
        btAbortRef.current?.abort();
        swAbortRef.current?.abort();
    }, []);

    const openTab = useCallback((tab: TabType) => {
        setActiveTab(tab);
        if (tab === 'specs') void loadYaml();
    }, [loadYaml]);

    // 詳情頁「均價/持倉/盈虧」統一取 stats（同一讀模型，稽核 7.7）；
    // stats 未就緒時退回 snapshot 卡片欄位（同 snapshot 來源，仍一致）。
    const position = stats?.position ?? bot.position ?? null;
    const profit = stats?.profit ?? bot.profit ?? null;
    const currentPrice = stats?.current_price ?? bot.current_price ?? null;
    const status = stats?.status ?? bot.status;
    const activeDirection = stats?.active_direction ?? bot.active_direction ?? bot.direction ?? null;
    const cycles = stats?.cycles ?? bot.cycles ?? null;
    const dataStatus = stats?.data_status ?? bot.data_status;
    const lastUpdatedAt = stats?.last_updated_at ?? bot.last_updated_at ?? null;
    const armed = isDcaArmed(status);

    const roi = dcaRoiPercent({ position, profit });
    const netPnl = (profit?.realized ?? 0) + (profit?.unrealized ?? 0);
    const isPositive = netPnl >= 0;

    const orderRows = orders?.available ? orders.orders : [];
    const fillRows = useMemo(() => (fills?.available ? fills.fills : []), [fills]);
    const pagedFills = useMemo(
        () => fillRows.slice(fillsPage * PAGE_SIZE, fillsPage * PAGE_SIZE + PAGE_SIZE),
        [fillRows, fillsPage],
    );
    const totalFillPages = Math.max(1, Math.ceil(fillRows.length / PAGE_SIZE));

    // 圖表衍生值：全部走 utils/dcaChart.ts 純函式（G-4：此檔零計算）。
    const chartMarks = useMemo(
        () => filterMarksForStatus(chart?.marks, status),
        [chart, status],
    );
    const chartNotice = useMemo(
        () => chartDataNotice(chart?.data_status, chart?.source, chart?.candles?.length ?? 0),
        [chart],
    );

    return (
        <main className="dashboard dca-detail">
            <header className="dashboard-header">
                <div>
                    <button className="btn btn-outline" type="button" onClick={onBack} style={{ marginBottom: '0.5rem', padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}>
                        <ArrowLeft size={14} />
                        <span>返回 DCA 大盤</span>
                    </button>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
                        <h2>{bot.symbol ?? botId}</h2>
                        <span className={`badge badge-${bot.mode === 'live' ? 'live' : 'paper'}`}>{(bot.mode ?? 'paper').toUpperCase()}</span>
                        <DcaStatusBadge status={status} />
                        {activeDirection && <span className="badge badge-stopped">{directionLabel(activeDirection)}</span>}
                        <span className="text-muted" style={{ fontSize: '0.78rem' }}>{freshnessText(dataStatus, lastUpdatedAt)}</span>
                    </div>
                </div>
                <button className="btn btn-outline" type="button" onClick={() => void load()} disabled={loading}>
                    <RefreshCcw size={15} className={loading ? 'spin' : ''} />
                    <span>刷新</span>
                </button>
            </header>

            {error && (
                <div className="notice notice-error" role="alert">
                    <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                    <span>{error}</span>
                </div>
            )}

            {/* armed 提醒：詳情頁明確標示「監控中 · 交易所零掛單」（稽核 7.1 / D-1） */}
            {armed && (
                <div className="notice notice-warning dca-armed-banner" role="status">
                    <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                    <span>
                        <strong>監控中 · 交易所零掛單。</strong>
                        入場條件成立前不會有任何真實掛單；下方「訂單」分頁應為空。
                    </span>
                </div>
            )}

            {/* KPI 摘要（與大盤卡片同一讀模型 → 數值一致，稽核 7.7） */}
            <section className="kpi-grid" aria-label="DCA 詳情摘要">
                <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
                    <span className="text-secondary" style={{ fontSize: '0.75rem' }}>淨盈虧（已實現＋浮動）</span>
                    <strong className={`font-mono ${isPositive ? 'positive' : 'negative'}`} style={{ fontSize: '1.4rem' }}>
                        {money(netPnl)}
                    </strong>
                    <small className="font-mono">
                        已實現 {money(profit?.realized)} · 浮動 {money(profit?.unrealized)} · ROI {roi == null ? '—' : `${roi.toFixed(2)}%`}
                    </small>
                </article>

                <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
                    <span className="text-secondary" style={{ fontSize: '0.75rem' }}>持倉 / 均價</span>
                    <strong className="font-mono" style={{ fontSize: '1.4rem' }}>
                        {armed ? '—' : number(position?.quantity, 4)}
                    </strong>
                    <small className="font-mono">
                        均價 {armed ? '—' : number(position?.average_cost)} · 成本 {armed ? '—' : money(position?.total_cost)}
                    </small>
                </article>

                <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
                    <span className="text-secondary" style={{ fontSize: '0.75rem' }}>安全單 / 循環</span>
                    <strong className="font-mono" style={{ fontSize: '1.4rem' }}>
                        {stats?.ladder ? `${stats.ladder.filled_safety_orders ?? 0} / ${stats.ladder.max_safety_orders ?? bot.max_safety_orders ?? '—'}` : '—'}
                    </strong>
                    <small className="font-mono">
                        掛單中 {armed ? 0 : (stats?.ladder?.active_safety_orders ?? 0)} · 完成循環 {cycles ?? 0} 輪
                    </small>
                </article>

                <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
                    <span className="text-secondary" style={{ fontSize: '0.75rem' }}>當前價 / 手續費</span>
                    <strong className="font-mono" style={{ fontSize: '1.4rem' }}>
                        {number(currentPrice)}
                    </strong>
                    <small className="font-mono">累積手續費 {money(profit?.fees)}</small>
                </article>
            </section>

            {/* 分頁導覽 */}
            <nav className="detail-nav-tabs" role="tablist">
                <button className={`detail-tab ${activeTab === 'chart' ? 'active' : ''}`} type="button" role="tab" aria-selected={activeTab === 'chart'} onClick={() => openTab('chart')}>
                    <CandlestickChart size={15} />
                    <span>圖表</span>
                </button>
                <button className={`detail-tab ${activeTab === 'backtest' ? 'active' : ''}`} type="button" role="tab" aria-selected={activeTab === 'backtest'} onClick={() => openTab('backtest')}>
                    <FlaskConical size={15} />
                    <span>回測</span>
                </button>
                <button className={`detail-tab ${activeTab === 'ladder' ? 'active' : ''}`} type="button" onClick={() => openTab('ladder')}>
                    <ListTree size={15} />
                    <span>階梯</span>
                </button>
                <button className={`detail-tab ${activeTab === 'orders' ? 'active' : ''}`} type="button" onClick={() => openTab('orders')}>
                    <Layers size={15} />
                    <span>訂單 ({orderRows.length})</span>
                </button>
                <button className={`detail-tab ${activeTab === 'fills' ? 'active' : ''}`} type="button" onClick={() => openTab('fills')}>
                    <Activity size={15} />
                    <span>成交 ({fillRows.length})</span>
                </button>
                <button className={`detail-tab ${activeTab === 'events' ? 'active' : ''}`} type="button" onClick={() => openTab('events')}>
                    <Clock size={15} />
                    <span>事件 ({events.length})</span>
                </button>
                <button className={`detail-tab ${activeTab === 'indicators' ? 'active' : ''}`} type="button" onClick={() => openTab('indicators')}>
                    <LineChart size={15} />
                    <span>指標</span>
                </button>
                <button className={`detail-tab ${activeTab === 'specs' ? 'active' : ''}`} type="button" onClick={() => openTab('specs')}>
                    <Sliders size={15} />
                    <span>設定</span>
                </button>
            </nav>

            {/* Tab: 圖表（Phase C6） */}
            {activeTab === 'chart' && (
                <section className="glass-panel dca-chart-panel" style={{ padding: '1.25rem' }}>
                    <div className="dca-chart-toolbar">
                        <div>
                            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.25rem' }}>即時圖表</h3>
                            <p className="muted" style={{ fontSize: '0.8rem' }}>
                                K 線 + 本機器人實際下單／成交／止盈標記 + 階梯／均價／止盈價格線；每 15 秒自動更新。
                            </p>
                        </div>
                        <div className="dca-chart-controls">
                            {chartNotice.sourceBadge && (
                                <span className="badge badge-paper dca-chart-source-badge">{chartNotice.sourceBadge}</span>
                            )}
                            <div className="dca-chart-timeframes" role="group" aria-label="K 線時框">
                                {CHART_TIMEFRAMES.map((tf) => (
                                    <button
                                        key={tf}
                                        type="button"
                                        className={`dca-chart-tf ${chartTimeframe === tf ? 'active' : ''}`}
                                        aria-pressed={chartTimeframe === tf}
                                        onClick={() => setChartTimeframe(tf)}
                                    >
                                        {tf}
                                    </button>
                                ))}
                            </div>
                            <button className="btn btn-outline" type="button" onClick={() => void loadChart()} disabled={chartLoading}>
                                <RefreshCcw size={14} className={chartLoading ? 'spin' : ''} />
                                <span>更新</span>
                            </button>
                        </div>
                    </div>

                    {/* 稽核 6.6：armed 時交易所零掛單，圖上不會有任何 order_placed 標記。 */}
                    {armed && (
                        <div className="notice notice-warning" role="status" style={{ marginTop: '0.75rem' }}>
                            <AlertTriangle size={16} style={{ flexShrink: 0 }} />
                            <span>監控中 · 交易所零掛單：入場條件成立前圖上不會出現掛單標記。</span>
                        </div>
                    )}

                    {chartError && (
                        <div className="notice notice-error" role="alert" style={{ marginTop: '0.75rem' }}>
                            <AlertTriangle size={16} style={{ flexShrink: 0 }} />
                            <span>{chartError}</span>
                        </div>
                    )}

                    {/* 稽核 6.8：資料不足必須明講，不得畫空白圖讓人誤以為無波動。 */}
                    {chartNotice.unavailable ? (
                        <p className="muted" style={{ padding: '3rem 0', textAlign: 'center' }}>
                            {chart === null && chartLoading ? '載入 K 線中…' : chartNotice.message}
                        </p>
                    ) : (
                        <Suspense fallback={<p className="muted" style={{ padding: '3rem 0', textAlign: 'center' }}>載入圖表元件中…</p>}>
                            <DcaChart
                                candles={chart?.candles}
                                marks={chartMarks}
                                lines={chart?.lines}
                                direction={activeDirection}
                                liveLastPrice={chart?.current_price ?? currentPrice}
                                height={440}
                                ariaLabel={`${bot.symbol ?? botId} ${chartTimeframe} K 線與 DCA 交易標記`}
                            />
                        </Suspense>
                    )}

                    {(chart?.warnings?.length ?? 0) > 0 && (
                        <ul className="dca-chart-warnings">
                            {chart?.warnings.map((w, i) => (
                                <li key={`${i}-${w}`}>{w}</li>
                            ))}
                        </ul>
                    )}

                    <p className="muted" style={{ fontSize: '0.74rem', marginTop: '0.75rem' }}>
                        最後一根 K 棒尚未收線，以當前市價即時補畫（決策 D-10）。所有價格與百分比皆由後端提供，前端不做任何計算。
                    </p>
                </section>
            )}

            {/* Tab: 回測（Phase C7） */}
            {activeTab === 'backtest' && (
                <>
                    <DcaBacktestPanel
                        timeframe={btTimeframe}
                        bars={btBars}
                        initialCapital={btCapital}
                        running={btRunning}
                        disclosures={BACKTEST_DISCLOSURES}
                        onTimeframeChange={(tf) => setBtTimeframe(tf as DcaBacktestTimeframe)}
                        onBarsChange={setBtBars}
                        onInitialCapitalChange={setBtCapital}
                        onRun={() => void runBacktest()}
                        onCancel={cancelBacktest}
                        scopeNote={`以歷史 K 線回放「${bot.symbol ?? botId}」目前的 YAML 設定；回測為唯讀操作，不會下任何真實訂單、也不會改動此機器人。`}
                    />

                    {/* 稽核 7.6：錯誤必須明確呈現，且不渲染任何結果 */}
                    {btError && (
                        <div className="notice notice-error" role="alert" style={{ marginTop: '0.85rem' }}>
                            <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                            <span>{btError}</span>
                        </div>
                    )}

                    {btRunning && !btResult && (
                        <p className="muted" style={{ padding: '2.5rem 0', textAlign: 'center' }}>
                            回測執行中，請稍候…（{btBars} 根 {btTimeframe} K 棒）
                        </p>
                    )}

                    {!btError && btResult && (
                        <Suspense fallback={<p className="muted" style={{ padding: '2.5rem 0', textAlign: 'center' }}>載入結果中…</p>}>
                            <DcaBacktestResult
                                result={btResult}
                                candles={btCandles}
                                direction={activeDirection ?? bot.direction}
                            />
                        </Suspense>
                    )}

                    {!btError && !btResult && !btRunning && (
                        <p className="muted" style={{ padding: '2.5rem 0', textAlign: 'center' }}>
                            設定回測參數後按「執行回測」，即可看到虛擬下單標記與績效指標。
                        </p>
                    )}

                    {/* ── 參數掃描：沿用上方的時框 / 棒數 / 初始資金 ── */}
                    <div className="glass-panel dca-backtest-panel" style={{ marginTop: '1.25rem' }}>
                        <div className="dca-backtest-subhead">
                            <Layers size={16} />
                            <span>參數掃描比較</span>
                        </div>
                        <p className="muted" style={{ fontSize: '0.76rem' }}>
                            對多組參數各跑一次回測並排比較，使用上方的時框（{btTimeframe}）、棒數（{btBars}）與初始資金。
                            組數＝兩軸長度相乘，過多會被後端預算截斷。
                        </p>

                        <div className="dca-backtest-controls">
                            <label className="dca-backtest-field" style={{ minWidth: 180 }}>
                                <span>止盈% 掃描軸（逗號分隔）</span>
                                <input
                                    value={swTpRange}
                                    onChange={(e) => setSwTpRange(e.target.value)}
                                    disabled={swRunning}
                                    placeholder="1.0, 1.5, 2.0"
                                />
                            </label>
                            <label className="dca-backtest-field" style={{ minWidth: 180 }}>
                                <span>安全單張數掃描軸</span>
                                <input
                                    value={swSoRange}
                                    onChange={(e) => setSwSoRange(e.target.value)}
                                    disabled={swRunning}
                                    placeholder="2, 3, 4"
                                />
                            </label>
                            <label className="dca-backtest-field">
                                <span>排名依據</span>
                                <select
                                    value={swBestBy}
                                    onChange={(e) => setSwBestBy(e.target.value)}
                                    disabled={swRunning}
                                >
                                    <option value="total_return_pct">總報酬%</option>
                                    <option value="alpha_pct">Alpha%</option>
                                    <option value="profit_factor">獲利因子</option>
                                    <option value="win_rate_pct">勝率%</option>
                                    <option value="max_drawdown_pct">最大回撤%</option>
                                </select>
                            </label>
                            <div className="dca-backtest-actions">
                                <button
                                    className="btn btn-secondary"
                                    type="button"
                                    onClick={() => void runSweep()}
                                    disabled={swRunning || parseSweepAxis(swTpRange).length === 0 || parseSweepAxis(swSoRange).length === 0}
                                >
                                    {swRunning
                                        ? <><Loader2 size={15} className="spin" /> 掃描中…</>
                                        : <>執行掃描（{parseSweepAxis(swTpRange).length * parseSweepAxis(swSoRange).length} 組）</>}
                                </button>
                                {swRunning && (
                                    <button className="btn btn-outline" type="button" onClick={cancelSweep}>
                                        取消
                                    </button>
                                )}
                            </div>
                        </div>
                    </div>

                    {swError && (
                        <div className="notice notice-error" role="alert" style={{ marginTop: '0.85rem' }}>
                            <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                            <span>{swError}</span>
                        </div>
                    )}

                    {!swError && swResult && (
                        <Suspense fallback={<p className="muted" style={{ padding: '1.5rem 0', textAlign: 'center' }}>載入比較表中…</p>}>
                            <DcaSweepTable response={swResult} />
                        </Suspense>
                    )}
                </>
            )}

            {/* Tab: 階梯 */}
            {activeTab === 'ladder' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.25rem' }}>安全單階梯狀態</h3>
                    <p className="muted" style={{ fontSize: '0.8rem' }}>
                        {ladder?.available === false ? (ladder.message ?? '目前無鎖定方向的實際階梯') : '已成交 / 掛單中 / 未觸及；標出當前價與均價線'}
                    </p>
                    <DcaLadderTable
                        levels={ladder?.available ? ladder.levels : []}
                        entryPrice={bot.entry_price ?? stats?.current_price ?? null}
                        currentPrice={currentPrice}
                        averagePrice={position?.average_cost ?? null}
                    />
                </section>
            )}

            {/* Tab: 訂單 */}
            {activeTab === 'orders' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>當前交易所掛單</h3>
                    {orderRows.length === 0 ? (
                        <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>
                            {armed
                                ? '監控中 · 交易所零掛單（armed 狀態下不存在任何真實掛單）。'
                                : orders?.available === false
                                    ? (orders.message ?? '引擎尚未提供掛單資料')
                                    : '目前無活躍掛單。'}
                        </p>
                    ) : (
                        <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
                            <table className="pro-table">
                                <thead>
                                    <tr>
                                        <th>角色</th>
                                        <th>方向</th>
                                        <th>價格</th>
                                        <th>數量</th>
                                        <th>名目</th>
                                        <th>Client ID</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {orderRows.map((o, i) => (
                                        <tr key={str(o.client_order_id) ?? i}>
                                            <td>{dcaOrderRoleLabel(str(o.role))}</td>
                                            <td>{dcaSideLabel(str(o.side))}</td>
                                            <td className="font-mono">{number(num(o.price))}</td>
                                            <td className="font-mono">{number(num(o.quantity))}</td>
                                            <td className="font-mono">{number(num(o.notional), 2)}</td>
                                            <td className="font-mono text-muted" style={{ fontSize: '0.72rem' }}>{str(o.client_order_id) ?? '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </section>
            )}

            {/* Tab: 成交 */}
            {activeTab === 'fills' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>歷史成交（含手續費與累積均價）</h3>
                    {fillRows.length === 0 ? (
                        <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>
                            {fills?.available === false ? (fills.message ?? '引擎尚未提供成交資料') : '尚無成交紀錄。'}
                        </p>
                    ) : (
                        <>
                            <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
                                <table className="pro-table">
                                    <thead>
                                        <tr>
                                            <th>時間</th>
                                            <th>角色</th>
                                            <th>方向</th>
                                            <th>價格</th>
                                            <th>數量</th>
                                            <th>手續費</th>
                                            <th>成交後均價</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {pagedFills.map((f, i) => (
                                            <tr key={`${str(f.client_order_id) ?? 'fill'}-${i}`}>
                                                <td className="font-mono" style={{ fontSize: '0.74rem' }}>
                                                    {num(f.occurred_at) != null ? new Date((num(f.occurred_at) as number) * 1000).toLocaleString('zh-TW') : '—'}
                                                </td>
                                                <td>{dcaOrderRoleLabel(str(f.role))}</td>
                                                <td>{dcaSideLabel(str(f.side))}</td>
                                                <td className="font-mono">{number(num(f.price))}</td>
                                                <td className="font-mono">{number(num(f.quantity))}</td>
                                                <td className="font-mono">{money(num(f.fee))}</td>
                                                <td className="font-mono">{number(num(f.cumulative_avg_price))}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            {totalFillPages > 1 && (
                                <div className="dca-pagination" role="navigation" aria-label="成交分頁">
                                    <button className="btn btn-outline" type="button" disabled={fillsPage === 0} onClick={() => setFillsPage((p) => Math.max(0, p - 1))}>上一頁</button>
                                    <span className="font-mono">{fillsPage + 1} / {totalFillPages}</span>
                                    <button className="btn btn-outline" type="button" disabled={fillsPage >= totalFillPages - 1} onClick={() => setFillsPage((p) => Math.min(totalFillPages - 1, p + 1))}>下一頁</button>
                                </div>
                            )}
                        </>
                    )}
                </section>
            )}

            {/* Tab: 事件 */}
            {activeTab === 'events' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>生命週期事件時間線</h3>
                    {events.length === 0 ? (
                        <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>尚無事件紀錄。</p>
                    ) : (
                        <ul className="dca-event-timeline">
                            {[...events].sort((a, b) => b.occurred_at - a.occurred_at).map((ev, i) => (
                                <li key={`${ev.event_type}-${ev.occurred_at}-${i}`} className={`dca-event-item tone-${dcaEventTone(ev.event_type, ev.status)}`}>
                                    <span className="dca-event-dot" aria-hidden="true" />
                                    <div className="dca-event-body">
                                        <div className="dca-event-head">
                                            <strong>{dcaEventLabel(ev.event_type)}</strong>
                                            <span className="badge badge-stopped" style={{ fontSize: '0.68rem' }}>{ev.status}</span>
                                            <span className="text-muted font-mono" style={{ fontSize: '0.72rem' }}>
                                                {new Date(ev.occurred_at * 1000).toLocaleString('zh-TW')}
                                            </span>
                                        </div>
                                        {ev.detail && <p className="dca-event-detail">{ev.detail}</p>}
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </section>
            )}

            {/* Tab: 指標 */}
            {activeTab === 'indicators' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>入場指標設定</h3>
                    <p className="muted" style={{ fontSize: '0.82rem', marginBottom: '0.75rem' }}>{dcaEntryText(bot)}</p>
                    {(bot.entry_indicators?.length ?? 0) === 0 ? (
                        <p className="muted" style={{ padding: '1.5rem 0', textAlign: 'center' }}>
                            此機器人為立即入場（immediate），無技術指標條件。
                        </p>
                    ) : (
                        <div className="table-wrapper">
                            <table className="pro-table">
                                <thead>
                                    <tr>
                                        <th>指標</th>
                                        <th>參數</th>
                                        <th>條件門檻</th>
                                        <th>當前即時值</th>
                                        <th>狀態</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {bot.entry_indicators.map((ind, i) => {
                                        const live = indicatorLiveInfo(ind);
                                        return (
                                            <tr key={`${ind.type}-${i}`}>
                                                <td className="font-mono"><strong>{live.name}</strong></td>
                                                <td className="font-mono" style={{ fontSize: '0.76rem' }}>
                                                    {[
                                                        ind.period != null ? `period=${ind.period}` : null,
                                                        ind.fast != null ? `fast=${ind.fast}` : null,
                                                        ind.slow != null ? `slow=${ind.slow}` : null,
                                                        ind.signal != null ? `signal=${ind.signal}` : null,
                                                        ind.oversold != null ? `oversold=${ind.oversold}` : null,
                                                        ind.overbought != null ? `overbought=${ind.overbought}` : null,
                                                        ind.std_dev != null ? `std=${ind.std_dev}` : null,
                                                        ind.lookback_bars != null ? `lookback=${ind.lookback_bars}` : null,
                                                        ind.long_threshold_percent != null ? `long≤${ind.long_threshold_percent}%` : null,
                                                        ind.short_threshold_percent != null ? `short≥${ind.short_threshold_percent}%` : null,
                                                    ].filter(Boolean).join(' · ') || '—'}
                                                </td>
                                                <td className="font-mono" style={{ fontSize: '0.82rem' }}>
                                                    {live.targetText || ind.condition || '—'}
                                                </td>
                                                <td className="font-mono" style={{ fontSize: '0.88rem' }}>
                                                    {live.valueText != null ? (
                                                        <span className={live.isTriggered ? 'positive' : live.isWarming ? 'text-muted' : ''} style={{ fontWeight: 600 }}>
                                                            {live.valueText}
                                                        </span>
                                                    ) : (
                                                        <span className="text-muted">—</span>
                                                    )}
                                                </td>
                                                <td>
                                                    {live.isTriggered ? (
                                                        <span className="badge badge-dca-triggering">條件成立</span>
                                                    ) : live.isWarming ? (
                                                        <span className="badge badge-dca-armed">暖機中</span>
                                                    ) : live.valueText != null ? (
                                                        <span className="badge badge-neutral">監控中</span>
                                                    ) : (
                                                        <span className="text-muted">—</span>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                    <p className="muted" style={{ fontSize: '0.74rem', marginTop: '0.75rem' }}>
                        指標於時框 {bot.entry_timeframe ?? '—'} 上共用同一 K 線管線評估；數值隨引擎快照自動即時同步。
                    </p>
                </section>
            )}

            {/* Tab: 設定 */}
            {activeTab === 'specs' && (
                <section className="glass-panel" style={{ padding: '1.25rem' }}>
                    <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>設定（唯讀）</h3>
                    <div className="dca-specs-grid">
                        <div><dt>交易對</dt><dd className="font-mono">{bot.symbol ?? '—'}</dd></div>
                        <div><dt>交易所</dt><dd className="font-mono">{bot.exchange ?? '—'}</dd></div>
                        <div><dt>方向</dt><dd className="font-mono">{directionLabel(bot.direction)}</dd></div>
                        <div><dt>帳戶模式</dt><dd className="font-mono">{(bot.mode ?? 'paper').toUpperCase()}</dd></div>
                        <div><dt>每單金額</dt><dd className="font-mono">{number(bot.base_order_amount)}</dd></div>
                        <div><dt>Max DCA orders</dt><dd className="font-mono">{bot.max_safety_orders ?? '—'}</dd></div>
                        <div><dt>止盈 %</dt><dd className="font-mono">{bot.take_profit_percent ?? '—'}</dd></div>
                        <div><dt>止損 %</dt><dd className="font-mono">{bot.stop_loss_percent ?? '停用'}</dd></div>
                        <div><dt>入場模式</dt><dd className="font-mono">{bot.entry_mode ?? 'immediate'}</dd></div>
                        <div><dt>設定檔</dt><dd className="font-mono" style={{ fontSize: '0.72rem' }}>{bot.config_path}</dd></div>
                    </div>
                    <h4 style={{ fontSize: '0.9rem', fontWeight: 600, margin: '1rem 0 0.5rem' }}>YAML 原文</h4>
                    <pre className="dca-yaml-view">{yamlText ?? '載入中…'}</pre>
                </section>
            )}
        </main>
    );
};

export default DcaDetail;
