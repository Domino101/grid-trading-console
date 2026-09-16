import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import {
    AlertTriangle,
    Check,
    CheckCircle,
    ChevronLeft,
    ChevronRight,
    Info,
    Layers,
    Loader2,
    Rocket,
    FlaskConical,
} from 'lucide-react';
import {
    api,
    ApiError,
    type BacktestResult,
    type DcaBacktestTimeframe,
    type DcaCreateResponse,
    type KlineCandle,
    type DcaPreviewResponse,
    type MarketInfo,
    type MarketIndicatorsResponse,
} from '../services/api';
import { money } from '../utils/format';
import {
    buildDcaCreateRequest,
    buildDcaPreviewRequest,
    DCA_ENTRY_DEFAULTS,
    canAdvanceDcaStep,
    dcaFundsStatus,
    dcaIndicatorDefaults,
    directionLabel,
    selectDcaSummary,
    validateDcaEntry,
    validateDcaStep3,
    backtestErrorText,
    type DcaWizardForm,
} from '../utils/dca';
import IndicatorConfigPanel from './IndicatorConfigPanel';
import DcaLadderPreview from './DcaLadderPreview';
import DcaBacktestPanel from './DcaBacktestPanel';
import { SymbolSelect } from './SymbolSelect';

// 與 DcaDetail 相同的動態匯入，維持圖表函式庫的獨立 chunk（E-6.2）。
const DcaBacktestResult = lazy(() => import('./DcaBacktestResult'));

// DCA 建立步驟定義
const DCA_STEPS = [
    { num: 1, title: '基本設定', en: 'General', desc: '帳戶模式與市場方向' },
    { num: 2, title: '入場條件', en: 'Entry', desc: '即時進場與指標條件' },
    { num: 3, title: '攤平參數', en: 'DCA', desc: '網格階梯倍率與風控' },
    { num: 4, title: '摘要審查', en: 'Summary', desc: '試算預覽與送出' },
] as const;

// D-11 固定簡化事項（與 DcaDetail 同一份文案）。
const WIZARD_BACKTEST_DISCLOSURES = [
    '回測不模擬部分成交：每張單一次整張成交或完全不成交。',
    '除跳空外不套用滑價模型；手續費採表單的 fee_rate。',
    '未套用交易所最小名目限制（與實盤路徑一致）。',
    '歷史績效不代表未來，回測結果僅供參考。',
] as const;

interface DcaFormProps {
    onNavigateDashboard: () => void;
}

type Notice = { type: 'success' | 'error'; text: string } | null;

const initialForm = (): DcaWizardForm => ({
    symbol: '',
    exchange: 'binance',
    direction: 'long',
    account_mode: 'paper',
    marketInfoOk: false,
    entry: { ...DCA_ENTRY_DEFAULTS, indicators: dcaIndicatorDefaults() },
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
});

// 可空的數字輸入（留空 = 停用/不送出）。
const OptionalNumberInput: React.FC<{
    label: string;
    value: number | null;
    min?: number;
    max?: number;
    step?: number | string;
    placeholder?: string;
    onChange: (value: number | null) => void;
}> = ({ label, value, min, max, step, placeholder, onChange }) => (
    <label className="input-label">
        <span>{label}</span>
        <input
            type="number"
            value={value == null ? '' : value}
            min={min}
            max={max}
            step={step}
            placeholder={placeholder ?? '留空停用'}
            onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        />
    </label>
);

// 建立 DCA 精靈（Phase 6）：4 步 + 即時階梯試算（/preview 驅動，前端零階梯運算）。
const DcaForm: React.FC<DcaFormProps> = ({ onNavigateDashboard }) => {
    const [step, setStep] = useState(1);
    const [form, setForm] = useState<DcaWizardForm>(initialForm);
    const [liveConfirmation, setLiveConfirmation] = useState('');
    const [autoStart, setAutoStart] = useState(true);
    const [liveTradingEnabled, setLiveTradingEnabled] = useState<boolean | null>(null);
    const [notice, setNotice] = useState<Notice>(null);
    const [submitting, setSubmitting] = useState(false);
    const [created, setCreated] = useState<DcaCreateResponse | null>(null);

    // market/info 與指標即時值：單一 state + 派生（scope 不匹配即視為過期，
    // 避免 effect 內同步 setState — 全部更新都在 async 継続で実行）。
    const [marketState, setMarketState] = useState<{
        kind: 'idle' | 'loading' | 'ready' | 'error';
        scope: { symbol: string; exchange: string; timeframe: string; limit: number };
        info?: MarketInfo;
        data?: MarketIndicatorsResponse;
        message?: string;
    }>({ kind: 'idle', scope: { symbol: '', exchange: '', timeframe: '15m', limit: 300 } });
    const [previewState, setPreviewState] = useState<{
        kind: 'idle' | 'loading' | 'ready' | 'error';
        response?: DcaPreviewResponse;
        message?: string;
    }>({ kind: 'idle' });
    const previewAbortRef = useRef<AbortController | null>(null);

    // ── Phase C7：精靈內「先回測看看」（稽核 7.9：送出的是表單當前參數 draft）──
    const [wizardBtOpen, setWizardBtOpen] = useState(false);
    const [wizardBtTimeframe, setWizardBtTimeframe] = useState<DcaBacktestTimeframe>('15m');
    const [wizardBtBars, setWizardBtBars] = useState(1000);
    const [wizardBtCapital, setWizardBtCapital] = useState('1000');
    const [wizardBtResult, setWizardBtResult] = useState<BacktestResult | null>(null);
    const [wizardBtCandles, setWizardBtCandles] = useState<KlineCandle[] | null>(null);
    const [wizardBtError, setWizardBtError] = useState<string | null>(null);
    const [wizardBtRunning, setWizardBtRunning] = useState(false);
    const wizardBtAbortRef = useRef<AbortController | null>(null);

    const update = <K extends keyof DcaWizardForm>(key: K, value: DcaWizardForm[K]) =>
        setForm((f) => ({ ...f, [key]: value }));

    const symbol = form.symbol.trim();
    const scope = { symbol, exchange: form.exchange, timeframe: form.entry.timeframe, limit: form.entry.fetch_limit };
    const scopeMatches = (s: { symbol: string; exchange: string; timeframe: string; limit: number }) =>
        s.symbol === symbol && s.exchange === form.exchange && s.timeframe === form.entry.timeframe && s.limit === form.entry.fetch_limit;
    const marketInfoOk = marketState.kind === 'ready' && scopeMatches(marketState.scope);
    const scopedMarketData = marketState.kind === 'ready' && scopeMatches(marketState.scope) ? marketState.data ?? null : null;
    const scopedMarketError = marketState.kind === 'error' && scopeMatches(marketState.scope) ? marketState.message ?? null : null;
    const marketLoading = marketState.kind === 'loading' && scopeMatches(marketState.scope);
    const currentPrice = scopedMarketData?.current_price ?? null;
    // preview 顯示用派生（blocked 訊息在 render 層推出，不走 state）
    const preview = previewState.kind === 'ready' ? previewState.response ?? null : null;
    const previewError =
        previewState.kind === 'error' ? previewState.message ?? null
        : currentPrice == null ? '尚未取得目前價格（交易對未確認或行情端點不可用），無法試算。'
        : validateDcaStep3(form).length > 0 ? '第三步還有參數未完成，請先回上一步補齊。'
        : null;
    const previewLoading = previewState.kind === 'loading';

    // LIVE feature flag（與網格精靈同一健康端點）。
    useEffect(() => {
        let cancelled = false;
        const fetchHealth = async () => {
            try {
                const h = await api.getSystemHealth();
                if (!cancelled) setLiveTradingEnabled(Boolean(h.live_trading_enabled));
            } catch {
                // silent check
            }
        };
        void fetchHealth();
        return () => { cancelled = true; };
    }, []);

    // 交易對確認 + 指標即時值（debounce 600ms + abort；404 → 明確「找不到交易對」— 稽核 6.4/6.9）。
    useEffect(() => {
        if (!symbol) return;
        const controller = new AbortController();
        const timer = setTimeout(async () => {
            setMarketState({ kind: 'loading', scope: { ...scope } });
            try {
                const [info, indicators] = await Promise.all([
                    api.getMarketInfo(symbol, form.exchange),
                    api.getMarketIndicators(symbol, form.exchange, form.entry.timeframe, form.entry.fetch_limit, controller.signal),
                ]);
                if (controller.signal.aborted) return;
                if (info.canonical_symbol && info.canonical_symbol !== form.symbol) {
                    setForm((prev) => ({ ...prev, symbol: info.canonical_symbol || prev.symbol }));
                }
                setMarketState({ kind: 'ready', scope: { ...scope }, info, data: indicators });
            } catch (e) {
                if (controller.signal.aborted) return;
                setMarketState({
                    kind: 'error',
                    scope: { ...scope },
                    message: e instanceof ApiError
                        ? (e.status === 404
                            ? `找不到交易對 ${symbol}（exchange=${form.exchange}），請檢查符號格式，例如 BTC/USDT。`
                            : `市場資料端點錯誤：${e.message}`)
                        : '市場資料端點暫時無法連線（可能離線或交易所 API 不可用）。',
                });
            }
        }, 600);
        return () => {
            clearTimeout(timer);
            controller.abort();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [symbol, form.exchange, form.entry.timeframe, form.entry.fetch_limit]);

    // Summary 試算：僅在第 4 步；debounce 300ms；每次重試先取消前一請求（稽核 6.16）。
    useEffect(() => {
        if (step !== 4) return;
        if (currentPrice == null) return;
        if (validateDcaStep3(form).length > 0) return;
        previewAbortRef.current?.abort();
        const controller = new AbortController();
        previewAbortRef.current = controller;
        const timer = setTimeout(async () => {
            setPreviewState({ kind: 'loading' });
            try {
                const res = await api.previewDca(buildDcaPreviewRequest(form, currentPrice), controller.signal);
                if (!controller.signal.aborted) {
                    setPreviewState({ kind: 'ready', response: res });
                }
            } catch (e) {
                if (controller.signal.aborted) return;
                setPreviewState({ kind: 'error', message: e instanceof ApiError ? e.message : '試算失敗，請稍後再試。' });
            }
        }, 300);
        return () => {
            clearTimeout(timer);
            controller.abort();
        };
    }, [step, form, currentPrice]);

    // 稽核 6.2：Summary 所有數值來自 preview 回應；這裡只做「選取」供確認文案使用。
    const summaryForConfirm = preview
        ? selectDcaSummary(
            preview.required_capital,
            form.direction === 'both' ? preview.ladder_long : preview.ladder,
          )
        : null;

    // 稽核 7.9：draft 直接由「表單當前參數」組出（與 /preview 同一個 builder），
    // 送的是使用者眼前正在調的參數，不是任何已建立的 bot。
    const runWizardBacktest = useCallback(async () => {
        if (currentPrice == null) return;
        wizardBtAbortRef.current?.abort();
        const controller = new AbortController();
        wizardBtAbortRef.current = controller;
        setWizardBtRunning(true);
        setWizardBtError(null);
        try {
            const draft = buildDcaPreviewRequest(form, currentPrice) as unknown as Record<string, unknown>;
            const response = await api.runDcaBacktest(
                {
                    draft,
                    timeframe: wizardBtTimeframe,
                    bars: wizardBtBars,
                    initial_capital: wizardBtCapital,
                },
                controller.signal,
            );
            setWizardBtResult(response.result);
            if (response.result.candles && response.result.candles.length > 0) {
                setWizardBtCandles(response.result.candles);
            } else {
                try {
                    const klines = await api.getKlines(
                        form.symbol, form.exchange, wizardBtTimeframe, wizardBtBars, controller.signal,
                    );
                    setWizardBtCandles(klines.candles);
                } catch {
                    setWizardBtCandles(null);
                }
            }
        } catch (e) {
            if (controller.signal.aborted) return;
            const status = e instanceof ApiError ? e.status : null;
            const fallback = e instanceof Error ? e.message : '回測失敗';
            setWizardBtError(backtestErrorText(status, fallback));
            setWizardBtResult(null);
            setWizardBtCandles(null);
        } finally {
            setWizardBtRunning(false);
        }
    }, [form, currentPrice, wizardBtTimeframe, wizardBtBars, wizardBtCapital]);

    const cancelWizardBacktest = useCallback(() => {
        wizardBtAbortRef.current?.abort();
        setWizardBtRunning(false);
    }, []);

    useEffect(() => () => { wizardBtAbortRef.current?.abort(); }, []);

    const funds = preview ? dcaFundsStatus(preview.required_capital, null) : 'unknown';
    const fundsBlocked = form.account_mode === 'live' && funds === 'insufficient';

    const formForValidation: DcaWizardForm = { ...form, marketInfoOk };
    const canAdvance = canAdvanceDcaStep(step, formForValidation, liveConfirmation);

    const liveConfirmBlocked =
        form.account_mode === 'live' && liveConfirmation !== 'CONFIRM LIVE';
    const canSubmit =
        step === 4 &&
        !submitting &&
        preview != null &&
        !previewError &&
        !fundsBlocked &&
        (form.account_mode !== 'live' || liveConfirmation === 'CONFIRM LIVE');

    const submit = async () => {
        if (currentPrice == null) {
            setNotice({ type: 'error', text: '尚未取得目前價格，無法送出。' });
            return;
        }
        setSubmitting(true);
        setNotice(null);
        try {
            const req = buildDcaCreateRequest(form, currentPrice, autoStart, marketInfoOk ? marketState.info ?? null : null);
            const res = await api.createDca(req);
            setCreated(res);
            setStep(5);
            setNotice({ type: 'success', text: res.message });
        } catch (e) {
            setNotice({ type: 'error', text: e instanceof ApiError ? e.message : '建立失敗，請稍後再試。' });
        } finally {
            setSubmitting(false);
        }
    };

    const reset = () => {
        setForm(initialForm());
        setStep(1);
        setLiveConfirmation('');
        setCreated(null);
        setNotice(null);
        setPreviewState({ kind: 'idle' });
        setMarketState({ kind: 'idle', scope: { symbol: '', exchange: '', timeframe: '15m', limit: 300 } });
    };

    // ── Step 5：成功卡片（稽核 6.17）──
    if (step === 5 && created) {
        return (
            <main className="dca-wizard">
                <div className="glass-panel dca-success-card" role="status">
                    <CheckCircle size={28} className="dca-success-icon" />
                    <h2>DCA 機器人已建立</h2>
                    <p className="dca-success-bid" style={{ fontFamily: 'monospace' }}>{created.bot_id}</p>
                    <p className="muted">{created.message}</p>
                    <p className="muted" style={{ fontSize: '0.78rem' }}>
                        {created.status === 'pending_engine'
                            ? '引擎將在 5 秒內接手並開始監控（熱插拔）。'
                            : '已寫入設定檔；可在 DCA 總覽手動啟動。'}
                    </p>
                    <div className="dca-success-actions">
                        <button className="btn btn-primary" type="button" onClick={onNavigateDashboard}>
                            前往 DCA 總覽
                        </button>
                        <button className="btn btn-outline" type="button" onClick={reset}>
                            再建立一台
                        </button>
                    </div>
                </div>
            </main>
        );
    }

    const isStepClickable = (n: number) => {
        if (n < step) return true;
        if (n === step + 1 && canAdvance) return true;
        return false;
    };

    const handleStepClick = (targetStep: number) => {
        if (targetStep === step) return;
        if (targetStep < step) {
            setStep(targetStep);
        } else if (targetStep === step + 1 && canAdvance) {
            setStep(targetStep);
        }
    };

    const progressPercent = (step / DCA_STEPS.length) * 100;

    return (
        <main className="dca-wizard">
            <header className="form-header">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <div className="brand-icon-wrapper" style={{ width: '36px', height: '36px', borderRadius: 'var(--radius-sm)' }}>
                            <Layers size={20} color="var(--accent-primary)" />
                        </div>
                        <div>
                            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0, letterSpacing: '-0.02em' }}>建立 DCA 馬丁格爾策略</h2>
                            <p className="muted" style={{ fontSize: '0.8rem', margin: 0 }}>
                                引導式參數配置。支援立即入場與多指標觸發、固定/口徑等差等比階梯試算與即時回測。
                            </p>
                        </div>
                    </div>
                    <button className="btn btn-outline" type="button" onClick={onNavigateDashboard} style={{ fontSize: '0.82rem', padding: '0.45rem 0.85rem' }}>
                        <ChevronLeft size={15} /> 返回 DCA 總覽
                    </button>
                </div>
            </header>

            <div className="stepper-container">
                <div className="stepper-progress-track" role="progressbar" aria-valuenow={progressPercent} aria-valuemin={0} aria-valuemax={100}>
                    <div className="stepper-progress-fill" style={{ width: `${progressPercent}%` }} />
                </div>
                <div className="dca-stepper-header" role="navigation" aria-label="建立 DCA 步驟">
                    {DCA_STEPS.map((s) => {
                        const isCurrent = step === s.num;
                        const isCompleted = step > s.num;
                        const clickable = isStepClickable(s.num);
                        return (
                            <div
                                key={s.num}
                                className={`step-box ${isCurrent ? 'active' : ''} ${isCompleted ? 'completed' : ''} ${clickable ? 'clickable' : ''}`}
                                aria-current={isCurrent ? 'step' : undefined}
                                onClick={() => clickable && handleStepClick(s.num)}
                                role={clickable ? 'button' : undefined}
                                tabIndex={clickable ? 0 : undefined}
                                onKeyDown={(e) => {
                                    if (clickable && (e.key === 'Enter' || e.key === ' ')) {
                                        e.preventDefault();
                                        handleStepClick(s.num);
                                    }
                                }}
                            >
                                <div className="step-num">
                                    {isCompleted ? <Check size={14} strokeWidth={2.8} /> : s.num}
                                </div>
                                <div className="step-text">
                                    <strong className="step-title">
                                        {s.num}. {s.title} <span style={{ opacity: 0.65, fontSize: '0.72rem', fontWeight: 500 }}>({s.en})</span>
                                    </strong>
                                    <span className="step-sub">{s.desc}</span>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {notice && (
                <div className={`notice ${notice.type === 'error' ? 'notice-error' : 'notice-success'}`} role={notice.type === 'error' ? 'alert' : 'status'}>
                    {notice.type === 'error' ? <AlertTriangle size={15} /> : <CheckCircle size={15} />}
                    <span>{notice.text}</span>
                </div>
            )}

            <div className={`dca-wizard-grid ${step === 4 ? 'with-summary' : ''}`}>
                <div className="dca-wizard-main">
                    {/* Step 1: General */}
                    {step === 1 && (
                        <div className="form-fields-grid">
                            <div className="field-full">
                                <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>帳戶模式與交易市場</h3>
                                <p className="muted">指定本 DCA 机器人的執行環境、對接市場與方向</p>
                            </div>

                            <div className="field-full">
                                <span className="input-label" style={{ marginBottom: '0.5rem' }}>帳戶模式 (Account Mode)</span>
                                <div className="mode-selector-grid">
                                    <button
                                        type="button"
                                        className={`mode-choice-card ${form.account_mode === 'paper' ? 'active paper' : ''}`}
                                        onClick={() => update('account_mode', 'paper')}
                                    >
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                            <strong>PAPER 模擬盤</strong>
                                            <span className="badge badge-paper">零風險</span>
                                        </div>
                                        <p className="muted" style={{ fontSize: '0.78rem' }}>
                                            使用公開行情進行虛擬撮合，不涉及任何真實資產。
                                        </p>
                                    </button>
                                    <button
                                        type="button"
                                        className={`mode-choice-card ${form.account_mode === 'live' ? 'active live' : ''}`}
                                        onClick={() => update('account_mode', 'live')}
                                    >
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                            <strong>LIVE 實盤交易</strong>
                                            <span className="badge badge-live">實盤資金</span>
                                        </div>
                                        <p className="muted" style={{ fontSize: '0.78rem' }}>
                                            直接對接交易所 API 進行真實下單。
                                        </p>
                                    </button>
                                </div>

                                {form.account_mode === 'live' && (
                                    <div style={{ marginTop: '0.65rem' }}>
                                        {liveTradingEnabled === true ? (
                                            <div className="notice notice-success" style={{ fontSize: '0.78rem', padding: '0.5rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                <CheckCircle size={15} />
                                                <span>🟢 實盤環境已解鎖，可直接建立並啟動實盤 DCA。</span>
                                            </div>
                                        ) : liveTradingEnabled === false ? (
                                            <div className="notice notice-warning" style={{ fontSize: '0.78rem', padding: '0.5rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                <AlertTriangle size={15} />
                                                <span>⚠️ 實盤開關尚未啟用：伺服器 .env 需設定 <code>GRID_LIVE_TRADING_ENABLED=1</code>，否則建立 LIVE DCA 會被拒絕（403）。</span>
                                            </div>
                                        ) : null}
                                    </div>
                                )}
                            </div>

                            <label className="input-label">
                                <span>交易所 (Exchange)</span>
                                <select value={form.exchange} onChange={(e) => update('exchange', e.target.value)}>
                                    <option value="binance">Binance (幣安)</option>
                                    <option value="okx">OKX (歐易)</option>
                                    <option value="hyperliquid">Hyperliquid L1</option>
                                    <option value="lighter">Lighter DEX</option>
                                    <option value="backpack">Backpack</option>
                                </select>
                            </label>

                            <label className="input-label">
                                <span>交易對 (Symbol)</span>
                                <SymbolSelect
                                    value={form.symbol}
                                    exchange={form.exchange}
                                    onChange={(val) => update('symbol', val)}
                                    placeholder={
                                        form.exchange === 'okx' ? '例如: ETH-USDT-SWAP 或 BTC-USDT-SWAP' :
                                        form.exchange === 'hyperliquid' ? '例如: ETH/USDC:USDC 或 SOL/USDC:USDC' :
                                        form.exchange === 'lighter' ? '例如: ETH-USD 或 BTC-USD' :
                                        form.exchange === 'backpack' ? '例如: ETH_USDC_PERP' :
                                        '例如: ETH/USDC:USDC 或 牛来USDT'
                                    }
                                    ariaDescribedBy="dca-market-info"
                                />
                            </label>

                            <label className="input-label">
                                <span>方向 (Direction)</span>
                                <select value={form.direction} onChange={(e) => update('direction', e.target.value as DcaWizardForm['direction'])}>
                                    <option value="long">做多 (Long)</option>
                                    <option value="short">做空 (Short)</option>
                                    <option value="both">雙向 (Both，單向互斥)</option>
                                </select>
                            </label>

                            <div className="field-full" id="dca-market-info" style={{ marginTop: '0.5rem' }}>
                                <div className="glass-panel dca-market-info-strip">
                                    {marketLoading ? (
                                        <span className="muted" style={{ fontSize: '0.8rem' }}>
                                            <Loader2 size={14} className="spin" /> 正在確認交易對與精度…
                                        </span>
                                    ) : scopedMarketError ? (
                                        <span className="dca-market-info-error" role="alert">
                                            <AlertTriangle size={14} /> {scopedMarketError}
                                        </span>
                                    ) : marketInfoOk && marketState.info ? (
                                        <span style={{ fontSize: '0.8rem' }}>
                                            <CheckCircle size={14} className="dca-market-info-ok" />
                                            交易對已確認：價格精度 {marketState.info.price_precision ?? '—'} 位 ·
                                            數量精度 {marketState.info.quantity_precision ?? '—'} 位 ·
                                            最小名義額 {marketState.info.min_notional ?? '—'}
                                            {currentPrice != null && <> · 目前價格 {money(currentPrice)}</>}
                                        </span>
                                    ) : (
                                        <span className="muted" style={{ fontSize: '0.8rem' }}>
                                            <Info size={14} /> 輸入交易對後自動確認（market/info 精度查詢）。
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Step 2: Entry */}
                    {step === 2 && (
                        <div className="form-fields-grid">
                            <div className="field-full">
                                <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>Bot 啟動條件（入場）</h3>
                                <p className="muted">選擇立即入場，或等待指標條件成立後入場</p>
                            </div>

                            <div className="field-full dca-entry-mode-cards">
                                <button
                                    type="button"
                                    className={`mode-choice-card ${form.entry.mode === 'immediate' ? 'active paper' : ''}`}
                                    onClick={() => update('entry', { ...form.entry, mode: 'immediate' })}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                        <strong>Immediate 立即入場</strong>
                                        <span className="badge badge-dca-averaging">啟動即進場</span>
                                    </div>
                                    <p className="muted" style={{ fontSize: '0.78rem' }}>
                                        引擎接手後立即以市價建立基礎單，不等待任何指標。
                                    </p>
                                </button>
                                <button
                                    type="button"
                                    className={`mode-choice-card ${form.entry.mode === 'indicator' ? 'active live' : ''}`}
                                    onClick={() => update('entry', { ...form.entry, mode: 'indicator' })}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                        <strong>Indicator 指標入場</strong>
                                        <span className="badge badge-dca-armed">armed 監控中</span>
                                    </div>
                                    <p className="muted" style={{ fontSize: '0.78rem' }}>
                                        先以 armed 狀態監控（交易所零掛單），條件成立才進場。
                                    </p>
                                </button>
                            </div>

                            {form.entry.mode === 'immediate' && (
                                <div className="notice notice-info field-full" role="note">
                                    <Info size={14} />
                                    <span>立即入場不需設定指標；入場價以引擎接手當下價格為準。</span>
                                </div>
                            )}

                            {form.entry.mode === 'indicator' && (
                                <div className="field-full">
                                    <IndicatorConfigPanel
                                        direction={form.direction}
                                        entry={form.entry}
                                        onChange={(entry) => update('entry', entry)}
                                        live={scopedMarketData}
                                        liveLoading={marketLoading}
                                    />
                                    {validateDcaEntry(form.entry).map((err) => (
                                        <div key={err} className="notice notice-error dca-field-error" role="alert">
                                            <AlertTriangle size={14} />
                                            <span>{err}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step 3: DCA 參數 */}
                    {step === 3 && (
                        <div className="form-fields-grid">
                            <div className="field-full">
                                <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>DCA 攤平與風險參數</h3>
                                <p className="muted">所有階梯數字由後端 /preview 計算；此頁只填參數</p>
                            </div>

                            <label className="input-label">
                                <span>每單金額 (Amount per trade)</span>
                                <span style={{ display: 'flex', gap: '0.5rem' }}>
                                    <input
                                        type="number"
                                        value={Number.isFinite(form.base_order_amount) ? form.base_order_amount : ''}
                                        min={0}
                                        step="any"
                                        onChange={(e) => update('base_order_amount', Number(e.target.value))}
                                    />
                                    <select
                                        value={form.amount_unit}
                                        onChange={(e) => update('amount_unit', e.target.value as DcaWizardForm['amount_unit'])}
                                        aria-label="金額單位"
                                    >
                                        <option value="quote">USD (quote)</option>
                                        <option value="base">幣 (base)</option>
                                    </select>
                                </span>
                            </label>

                            <label className="input-label">
                                <span>DCA Mode</span>
                                <select value={form.dca_mode} onChange={(e) => update('dca_mode', e.target.value as DcaWizardForm['dca_mode'])}>
                                    <option value="order">order（每單固定）</option>
                                    <option value="position">position（部位口徑）</option>
                                </select>
                            </label>

                            <label className="input-label">
                                <span>Averaging Method（攤平方式）</span>
                                <select value={form.averaging_method} onChange={(e) => update('averaging_method', e.target.value as DcaWizardForm['averaging_method'])}>
                                    <option value="fixed_value">fixed_value（固定金額）</option>
                                    <option value="fixed_coin">fixed_coin（固定幣量）</option>
                                </select>
                            </label>

                            <label className="input-label">
                                <span>Max DCA orders（安全單上限）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.max_safety_orders) ? form.max_safety_orders : ''}
                                    min={1}
                                    max={50}
                                    step={1}
                                    onChange={(e) => update('max_safety_orders', Number(e.target.value))}
                                />
                            </label>

                            <label className="input-label">
                                <span>Price deviation（第一張偏移 %）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.price_deviation_percent) ? form.price_deviation_percent : ''}
                                    min={0}
                                    step="any"
                                    onChange={(e) => update('price_deviation_percent', Number(e.target.value))}
                                />
                            </label>

                            <label className="input-label">
                                <span>Order size multiplier（單量倍數）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.order_size_multiplier) ? form.order_size_multiplier : ''}
                                    min={1}
                                    max={3}
                                    step={0.1}
                                    onChange={(e) => update('order_size_multiplier', Number(e.target.value))}
                                />
                            </label>

                            <label className="input-label">
                                <span>Price dev. multiplier（偏移遞增倍數）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.price_deviation_multiplier) ? form.price_deviation_multiplier : ''}
                                    min={1}
                                    max={3}
                                    step={0.1}
                                    onChange={(e) => update('price_deviation_multiplier', Number(e.target.value))}
                                />
                            </label>

                            <label className="input-label">
                                <span>Take profit target（止盈 %）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.take_profit_percent) ? form.take_profit_percent : ''}
                                    min={0}
                                    step="any"
                                    onChange={(e) => update('take_profit_percent', Number(e.target.value))}
                                />
                            </label>

                            <OptionalNumberInput
                                label="Stop loss（止損 %，留空停用）"
                                value={form.stop_loss_percent}
                                min={0}
                                step="any"
                                onChange={(v) => update('stop_loss_percent', v)}
                            />

                            <OptionalNumberInput
                                label="最大持倉名目 (USD，留空不限)"
                                value={form.max_position_notional}
                                min={0}
                                step="any"
                                onChange={(v) => update('max_position_notional', v)}
                            />

                            <OptionalNumberInput
                                label="手續費率 %（留空用交易所預設）"
                                value={form.fee_rate}
                                min={0}
                                max={1}
                                step={0.01}
                                onChange={(v) => update('fee_rate', v)}
                            />

                            <label className="input-label">
                                <span>槓桿 (Leverage)</span>
                                <span style={{ display: 'flex', gap: '0.5rem' }}>
                                    <input
                                        type="number"
                                        value={Number.isFinite(form.leverage) ? form.leverage : ''}
                                        min={1}
                                        max={125}
                                        step={1}
                                        onChange={(e) => update('leverage', Number(e.target.value))}
                                    />
                                    <select
                                        value={form.margin_mode}
                                        onChange={(e) => update('margin_mode', e.target.value as DcaWizardForm['margin_mode'])}
                                        aria-label="保證金模式"
                                    >
                                        <option value="isolated">isolated</option>
                                        <option value="cross">cross</option>
                                    </select>
                                </span>
                            </label>

                            <div className="field-full" style={{ padding: '0.85rem', background: 'rgba(255,255,255,0.02)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)' }}>
                                <label className="dca-checkbox" style={{ margin: 0 }}>
                                    <input
                                        type="checkbox"
                                        checked={form.repeat_after_take_profit}
                                        onChange={(e) => update('repeat_after_take_profit', e.target.checked)}
                                    />
                                    <span style={{ fontWeight: 600 }}>止盈後自動循環（Repeat after Take Profit）</span>
                                </label>

                                {form.repeat_after_take_profit && (
                                    <div style={{ marginTop: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                                        <label className="input-label" style={{ marginBottom: '0.35rem' }}>
                                            <span style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                                                <span>每輪冷卻時間 (Cooldown)</span>
                                                <span className="muted" style={{ fontSize: '0.78rem', fontWeight: 'normal' }}>
                                                    {form.cooldown_seconds === 0
                                                        ? '⚡ 0 秒（立即重進，不冷卻）'
                                                        : `⏱️ ${form.cooldown_seconds} 秒（約 ${(form.cooldown_seconds / 60).toFixed(1).replace(/\.0$/, '')} 分鐘）`}
                                                </span>
                                            </span>
                                            <input
                                                type="number"
                                                value={Number.isFinite(form.cooldown_seconds) ? form.cooldown_seconds : ''}
                                                min={0}
                                                step={30}
                                                onChange={(e) => update('cooldown_seconds', Math.max(0, Number(e.target.value)))}
                                                placeholder="300"
                                            />
                                        </label>
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.4rem' }}>
                                            {[
                                                { label: '0s (立即重進)', val: 0 },
                                                { label: '60s (1分鐘)', val: 60 },
                                                { label: '300s (5分鐘·預設)', val: 300 },
                                                { label: '600s (10分鐘)', val: 600 },
                                                { label: '1800s (30分鐘)', val: 1800 },
                                            ].map((preset) => (
                                                <button
                                                    key={preset.val}
                                                    type="button"
                                                    className={`btn btn-xs ${form.cooldown_seconds === preset.val ? 'btn-primary' : 'btn-outline'}`}
                                                    style={{ fontSize: '0.72rem', padding: '0.2rem 0.55rem', borderRadius: '4px' }}
                                                    onClick={() => update('cooldown_seconds', preset.val)}
                                                >
                                                    {preset.label}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="muted" style={{ fontSize: '0.74rem', margin: '0.4rem 0 0 0' }}>
                                            每輪止盈完成後進入冷卻（cooling）狀態，倒數結束後才解除鎖定回到監控（armed），防止同一波行情立即追高/殺跌。
                                        </p>
                                    </div>
                                )}
                            </div>

                            <OptionalNumberInput
                                label="最大循環數（留空 = 無限）"
                                value={form.max_cycles}
                                min={1}
                                step={1}
                                onChange={(v) => update('max_cycles', v)}
                            />

                            {form.direction === 'both' && (
                                <label className="input-label">
                                    <span>雙向同時成立時的優先 (Tiebreak)</span>
                                    <select value={form.both_tiebreak} onChange={(e) => update('both_tiebreak', e.target.value as DcaWizardForm['both_tiebreak'])}>
                                        <option value="long_first">long 優先</option>
                                        <option value="short_first">short 優先</option>
                                    </select>
                                </label>
                            )}

                            <label className="input-label">
                                <span>在掛安全單窗口 (Window)</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.active_safety_orders_window) ? form.active_safety_orders_window : ''}
                                    min={1}
                                    max={50}
                                    step={1}
                                    onChange={(e) => update('active_safety_orders_window', Number(e.target.value))}
                                />
                            </label>

                            <label className="input-label">
                                <span>監控間隔（秒）</span>
                                <input
                                    type="number"
                                    value={Number.isFinite(form.monitoring_interval) ? form.monitoring_interval : ''}
                                    min={5}
                                    max={300}
                                    step={5}
                                    onChange={(e) => update('monitoring_interval', Number(e.target.value))}
                                />
                            </label>

                            {validateDcaStep3(form).map((err) => (
                                <div key={err} className="notice notice-error dca-field-error field-full" role="alert">
                                    <AlertTriangle size={14} />
                                    <span>{err}</span>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Step 4: Summary */}
                    {step === 4 && (
                        <div className="form-fields-grid">
                            <div className="field-full">
                                <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>確認並送出</h3>
                                <p className="muted">右側 Summary 由 POST /api/v1/dca/preview 即時驅動（debounce 300ms）</p>
                            </div>

                            <div className="glass-panel dca-review-list">
                                <div className="dca-review-row"><span>交易對</span><strong>{symbol}（{form.exchange}）</strong></div>
                                <div className="dca-review-row"><span>方向</span><strong>{directionLabel(form.direction)}</strong></div>
                                <div className="dca-review-row">
                                    <span>帳戶模式</span>
                                    <strong>{form.account_mode === 'live' ? 'LIVE 實盤' : 'PAPER 模擬'}</strong>
                                </div>
                                <div className="dca-review-row">
                                    <span>入場</span>
                                    <strong>
                                        {form.entry.mode === 'immediate'
                                            ? '立即入場'
                                            : `指標入場（${(Object.keys(form.entry.indicators) as Array<keyof typeof form.entry.indicators>).filter((k) => form.entry.indicators[k].enabled).length} 項指標，${form.entry.logic === 'any' ? 'OR' : 'AND'}）`}
                                    </strong>
                                </div>
                                <div className="dca-review-row">
                                    <span>目前價格（入場價 E）</span>
                                    <strong>{currentPrice == null ? '—' : money(currentPrice)}</strong>
                                </div>
                                <div className="dca-review-row">
                                    <span>安全單</span>
                                    <strong>{form.max_safety_orders} 張（dev {form.price_deviation_percent}% ×{form.price_deviation_multiplier}）</strong>
                                </div>
                                <div className="dca-review-row"><span>止盈</span><strong>{form.take_profit_percent}%</strong></div>
                                {form.stop_loss_percent != null && (
                                    <div className="dca-review-row"><span>止損</span><strong>{form.stop_loss_percent}%</strong></div>
                                )}
                                <div className="dca-review-row">
                                    <span>止盈後循環</span>
                                    <strong>{form.repeat_after_take_profit ? '啟用（自動循環）' : '停用（單輪結束即停止）'}</strong>
                                </div>
                                {form.repeat_after_take_profit && (
                                    <div className="dca-review-row" style={{ alignItems: 'center' }}>
                                        <span title="開啟/建立前可直接於此微調">每輪冷卻時間（可微調）</span>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                            <input
                                                type="number"
                                                value={Number.isFinite(form.cooldown_seconds) ? form.cooldown_seconds : ''}
                                                min={0}
                                                step={30}
                                                style={{ width: '85px', padding: '0.2rem 0.4rem', fontSize: '0.85rem', textAlign: 'right', borderRadius: '4px', background: 'var(--bg-input, rgba(0,0,0,0.3))', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                                                onChange={(e) => update('cooldown_seconds', Math.max(0, Number(e.target.value)))}
                                                title="開啟前可手動調整每輪冷卻秒數"
                                            />
                                            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                                秒 ({form.cooldown_seconds === 0 ? '不冷卻' : `${(form.cooldown_seconds / 60).toFixed(1).replace(/\.0$/, '')} 分鐘`})
                                            </span>
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* 稽核 7.9：先回測看看 — 對「表單當前參數」跑回測，不建立任何機器人 */}
                            <div className="field-full dca-wizard-backtest">
                                <button
                                    className="btn btn-outline"
                                    type="button"
                                    onClick={() => setWizardBtOpen((open) => !open)}
                                    disabled={currentPrice == null}
                                    title={currentPrice == null ? '等待市場價格載入' : undefined}
                                >
                                    <FlaskConical size={15} />
                                    <span>{wizardBtOpen ? '收起回測' : '先回測看看'}</span>
                                </button>
                                <span className="muted" style={{ fontSize: '0.76rem', marginLeft: '0.6rem' }}>
                                    以上方目前的參數跑歷史回放，不會建立機器人、也不會下單。
                                </span>

                                {wizardBtOpen && (
                                    <div style={{ marginTop: '0.85rem' }}>
                                        <DcaBacktestPanel
                                            timeframe={wizardBtTimeframe}
                                            bars={wizardBtBars}
                                            initialCapital={wizardBtCapital}
                                            running={wizardBtRunning}
                                            disclosures={WIZARD_BACKTEST_DISCLOSURES}
                                            onTimeframeChange={(tf) => setWizardBtTimeframe(tf as DcaBacktestTimeframe)}
                                            onBarsChange={setWizardBtBars}
                                            onInitialCapitalChange={setWizardBtCapital}
                                            onRun={() => void runWizardBacktest()}
                                            onCancel={cancelWizardBacktest}
                                            scopeNote="回測對象＝上方表單當前參數（draft），與已建立的機器人無關；此操作為唯讀。"
                                        />

                                        {wizardBtError && (
                                            <div className="notice notice-error" role="alert" style={{ marginTop: '0.85rem' }}>
                                                <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                                                <span>{wizardBtError}</span>
                                            </div>
                                        )}

                                        {!wizardBtError && wizardBtResult && (
                                            <Suspense fallback={<p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>載入結果中…</p>}>
                                                <DcaBacktestResult
                                                    result={wizardBtResult}
                                                    candles={wizardBtCandles}
                                                    direction={form.direction}
                                                />
                                            </Suspense>
                                        )}
                                    </div>
                                )}
                            </div>

                            {form.account_mode === 'live' && (
                                <div className="field-full dca-confirm-live-box" role="alert">
                                    <AlertTriangle size={16} />
                                    <div>
                                        <strong>您正在建立 LIVE 實盤 DCA 機器人</strong>
                                        <p className="muted" style={{ fontSize: '0.78rem' }}>
                                            交易對 {symbol}，方向 {directionLabel(form.direction)}，
                                            所需資金 {summaryForConfirm?.requiredCapital == null ? '—' : `${money(summaryForConfirm.requiredCapital)} USD`}，
                                            最大覆蓋跌幅 {summaryForConfirm?.maxDrawdownPercent == null ? '—' : `${summaryForConfirm.maxDrawdownPercent}%`}。
                                            請在下方輸入 <code>CONFIRM LIVE</code> 以確認送出：
                                        </p>
                                        <input
                                            value={liveConfirmation}
                                            onChange={(e) => setLiveConfirmation(e.target.value)}
                                            placeholder="輸入 CONFIRM LIVE"
                                            aria-label="LIVE 二次確認"
                                        />
                                    </div>
                                </div>
                            )}

                            <label className="dca-checkbox field-full">
                                <input
                                    type="checkbox"
                                    checked={autoStart}
                                    onChange={(e) => setAutoStart(e.target.checked)}
                                />
                                <span>建立後立即啟動（引擎 5 秒內熱插拔接手）</span>
                            </label>

                            {fundsBlocked && (
                                <div className="notice notice-error field-full" role="alert">
                                    <AlertTriangle size={14} />
                                    <span>所需資金超過可用餘額，LIVE 模式禁止送出（PAPER 模式僅警告）。</span>
                                </div>
                            )}
                        </div>
                    )}

                    {/* 導航列 */}
                    <div className="dca-wizard-nav">
                        {step > 1 && (
                            <button className="btn btn-outline" type="button" onClick={() => setStep(step - 1)}>
                                <ChevronLeft size={15} /> 上一步
                            </button>
                        )}
                        <span className="muted" style={{ fontSize: '0.76rem', flex: 1 }}>
                            步驟 {step} / 4
                        </span>
                        {step < 4 && (
                            <button
                                className="btn btn-primary"
                                type="button"
                                disabled={!canAdvance}
                                onClick={() => setStep(step + 1)}
                                title={!canAdvance ? '尚有必填欄位未完成' : undefined}
                            >
                                下一步 <ChevronRight size={15} />
                            </button>
                        )}
                        {step === 4 && (
                            <button
                                className="btn btn-primary"
                                type="button"
                                disabled={!canSubmit}
                                onClick={() => void submit()}
                                title={
                                    !canSubmit
                                        ? liveConfirmBlocked
                                            ? '請輸入 CONFIRM LIVE'
                                            : '等待 Summary 試算完成'
                                        : undefined
                                }
                            >
                                {submitting ? (
                                    <>
                                        <Loader2 size={15} className="spin" /> 送出中…
                                    </>
                                ) : (
                                    <>
                                        <Rocket size={15} /> 建立 DCA
                                    </>
                                )}
                            </button>
                        )}
                    </div>
                </div>

                {step === 4 && (
                    <DcaLadderPreview
                        response={preview}
                        loading={previewLoading}
                        error={previewError}
                        direction={form.direction}
                        availableBalance={null}
                        accountMode={form.account_mode}
                    />
                )}
            </div>
        </main>
    );
};

export default DcaForm;
