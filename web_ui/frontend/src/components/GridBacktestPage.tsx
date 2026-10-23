// P3：網格完整回測分頁（初期版本：網格資金感知回測 + 壓力情境 + 參數掃描）。
// P4：同一分頁支援 DCA 單向/雙向（含指標回測），DCA 模式直接呼叫既有 /api/v1/dca/backtest，
//     與 DcaDetail 共用同一套 DcaBacktestPanel / DcaBacktestResult，不重做 DCA 引擎。
// 依賴隔離：本元件只負責參數蒐集、呼叫 API、渲染結果；不自行做金融計算。

import React, { lazy, Suspense, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  FlaskConical,
  Gauge,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  TrendingUp,
  Repeat,
} from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer } from 'recharts';
import { api, ApiError, type GridBacktestRequest, type GridBacktestResponse, type GridBacktestStressResponse, type GridBacktestSweepResponse, type DcaSnapshotItem, type BacktestResult, type KlineCandle, type DcaBacktestTimeframe } from '../services/api';
import { money, number } from '../utils/format';
import DcaBacktestPanel from './DcaBacktestPanel';
import { t } from '../i18n/index.ts';

const DcaBacktestResult = lazy(() => import('./DcaBacktestResult'));

export interface GridBacktestInitialConfig {
  symbol: string;
  exchange: string;
  lower_price: number;
  upper_price: number;
  grid_count: number;
  grid_interval: number;
  recommended_order_amount: number;
  estimated_apr: number;
}

interface GridBacktestPageProps {
  initialConfig?: GridBacktestInitialConfig | null;
  onClearInitialConfig?: () => void;
  onUnauthorized?: () => void;
}

type WindowPreset = '7d' | '30d' | '180d' | '1y' | 'custom';
type Mode = 'run' | 'stress' | 'sweep';
type StrategyType = 'grid' | 'dca';

// DCA 回測常用上限（與後端 A-4 一致）。
const DCA_BACKTEST_MAX_BARS = 20000;
const DCA_INDICATOR_DEFAULTS = {
  rsi: { type: 'rsi', period: 14, oversold: 30, overbought: 70, condition: 'level' },
  macd: { type: 'macd', fast: 12, slow: 26, signal: 9, condition: 'cross' },
  bollinger: { type: 'bollinger', period: 20, std_dev: 2.0, condition: 'touch' },
  price_change: { type: 'price_change', lookback_bars: 24, long_threshold_percent: -3.0, short_threshold_percent: 3.0 },
} as const;

const PRESET_DAYS: Record<Exclude<WindowPreset, 'custom'>, number> = {
  '7d': 7,
  '30d': 30,
  '180d': 180,
  '1y': 365,
};

// 模組層級：避免在 render 中呼叫 Date.now()（react-hooks/purity）。
const INITIAL_NOW = Math.floor(Date.now() / 1000);

const fraction = (value: number | null | undefined, digits = 2): string =>
  value == null ? '—' : `${value.toFixed(digits)}%`;

const signature = (value: number | null | undefined, digits = 2): string =>
  value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;

const GridBacktestPage: React.FC<GridBacktestPageProps> = ({ initialConfig, onClearInitialConfig, onUnauthorized }) => {
  const [strategyType, setStrategyType] = useState<StrategyType>('grid');
  const [symbol, setSymbol] = useState(initialConfig?.symbol ?? '');
  const [exchange, setExchange] = useState(initialConfig?.exchange ?? 'binance');
  const [gridType, setGridType] = useState('neutral');
  const [lowerPrice, setLowerPrice] = useState(initialConfig?.lower_price ? String(initialConfig.lower_price) : '');
  const [upperPrice, setUpperPrice] = useState(initialConfig?.upper_price ? String(initialConfig.upper_price) : '');
  const [gridCount, setGridCount] = useState(initialConfig?.grid_count ? String(initialConfig.grid_count) : '');
  const [gridInterval, setGridInterval] = useState(initialConfig?.grid_interval ? String(initialConfig.grid_interval) : '');
  const [perGridNotional, setPerGridNotional] = useState('');
  const [perGridCapitalPercent, setPerGridCapitalPercent] = useState('');
  const [initialCapital, setInitialCapital] = useState('10000');
  const [leverage, setLeverage] = useState('10');
  const [timeframe, setTimeframe] = useState('1h');
  const [windowPreset, setWindowPreset] = useState<WindowPreset>('30d');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');

  const [mode, setMode] = useState<Mode>('run');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GridBacktestResponse['result'] | null>(null);
  const [stress, setStress] = useState<GridBacktestStressResponse | null>(null);
  const [sweep, setSweep] = useState<GridBacktestSweepResponse | null>(null);
  const [sweepGridCounts, setSweepGridCounts] = useState('5,10,20');
  const [sweepNotionals, setSweepNotionals] = useState('500,1000');

  // ── DCA 回測狀態（P4）──
  const [dcaSource, setDcaSource] = useState<'manual' | 'existing'>('manual');
  const [dcaBotId, setDcaBotId] = useState('');
  const [dcaBots, setDcaBots] = useState<DcaSnapshotItem[]>([]);
  const [dcaSymbol, setDcaSymbol] = useState('ETH/USDC:USDC');
  const [dcaExchange, setDcaExchange] = useState('binance');
  const [dcaDirection, setDcaDirection] = useState<'long' | 'short' | 'both'>('both');
  const [dcaBaseOrderAmount, setDcaBaseOrderAmount] = useState('20');
  const [dcaMaxSafetyOrders, setDcaMaxSafetyOrders] = useState('20');
  const [dcaPriceDeviationPercent, setDcaPriceDeviationPercent] = useState('0.8');
  const [dcaPriceDeviationMultiplier, setDcaPriceDeviationMultiplier] = useState('1.2');
  const [dcaOrderSizeMultiplier, setDcaOrderSizeMultiplier] = useState('1.0');
  const [dcaTakeProfitPercent, setDcaTakeProfitPercent] = useState('1.0');
  const [dcaStopLossPercent, setDcaStopLossPercent] = useState('');
  const [dcaLeverage, setDcaLeverage] = useState('10');
  const [dcaFeeRate, setDcaFeeRate] = useState('0.0004');
  const [dcaEntryMode, setDcaEntryMode] = useState<'immediate' | 'indicator'>('indicator');
  const [dcaEntryLogic, setDcaEntryLogic] = useState<'all' | 'any'>('all');
  const [dcaEntryTimeframe, setDcaEntryTimeframe] = useState('15m');
  const [dcaIndicators, setDcaIndicators] = useState<Record<'rsi' | 'macd' | 'bollinger' | 'price_change', boolean>>({
    rsi: false,
    macd: false,
    bollinger: false,
    price_change: true,
  });
  const [dcaTimeframe, setDcaTimeframe] = useState<DcaBacktestTimeframe>('15m');
  const [dcaBars, setDcaBars] = useState(2000);
  const [dcaInitialCapital, setDcaInitialCapital] = useState('10000');
  const [dcaRunning, setDcaRunning] = useState(false);
  const [dcaError, setDcaError] = useState<string | null>(null);
  const [dcaResult, setDcaResult] = useState<BacktestResult | null>(null);
  const [dcaCandles, setDcaCandles] = useState<KlineCandle[] | null>(null);

  const usingNotional = perGridNotional.trim() !== '';
  const usingPercent = !usingNotional && perGridCapitalPercent.trim() !== '';

  const nowSeconds = INITIAL_NOW;
  const windowBounds = useMemo(() => {
    if (windowPreset === 'custom') {
      const start = customStart ? Math.floor(new Date(customStart).getTime() / 1000) : null;
      const end = customEnd ? Math.floor(new Date(customEnd).getTime() / 1000) : null;
      return { start, end };
    }
    const days = PRESET_DAYS[windowPreset as keyof typeof PRESET_DAYS] ?? 30;
    return { start: nowSeconds - days * 86400, end: nowSeconds };
  }, [windowPreset, customStart, customEnd, nowSeconds]);

  const buildRequest = (): GridBacktestRequest => {
    if (!symbol.trim() || !lowerPrice || !upperPrice) throw new Error(t("請填入交易對、價格上下限"));
    if (Number(lowerPrice) >= Number(upperPrice)) throw new Error(t("價格下限必須小於上限"));
    if (!initialCapital || Number(initialCapital) <= 0) throw new Error(t("初始資金必須大於 0"));
    if (!usingNotional && !usingPercent) throw new Error(t("請提供每格金額或每格佔比"));
    return {
      symbol: symbol.trim().toUpperCase(),
      exchange,
      grid_type: gridType,
      lower_price: Number(lowerPrice),
      upper_price: Number(upperPrice),
      grid_count: Number(gridCount) || null,
      grid_interval: Number(gridInterval) || null,
      per_grid_notional: Number(perGridNotional) || null,
      per_grid_capital_percent: Number(perGridCapitalPercent) || null,
      initial_capital: Number(initialCapital),
      leverage: Number(leverage) || 10,
      timeframe,
      start_ts: windowBounds.start,
      end_ts: windowBounds.end,
      bars: null,
      source: initialConfig ? 'market_ranking' : 'manual',
    };
  };

  const buildSweepRequest = (): GridBacktestRequest => {
    const base = buildRequest();
    return {
      ...base,
      sweep_grid_counts: sweepGridCounts.split(',').map((x) => Number(x.trim())).filter((x) => Number.isFinite(x) && x > 0),
      sweep_per_grid_notional: sweepNotionals.split(',').map((x) => Number(x.trim())).filter((x) => Number.isFinite(x) && x > 0),
      max_total_bars: 300000,
      runs_limit: 20,
    };
  };

  const runRequest = async (kind: Mode, request: GridBacktestRequest) => {
    setRunning(true);
    setError(null);
    try {
      if (kind === 'run') {
        const res = await api.runGridBacktest(request);
        setResult(res.result);
        setStress(null);
        setSweep(null);
      } else if (kind === 'stress') {
        const res = await api.runGridStress(request);
        setStress(res);
        setResult(null);
        setSweep(null);
      } else {
        const res = await api.runGridSweep(request);
        setSweep(res);
        setResult(null);
        setStress(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && onUnauthorized) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : t("執行失敗"));
    } finally {
      setRunning(false);
    }
  };

  const run = async () => {
    try {
      await runRequest(mode, buildRequest());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("參數錯誤"));
    }
  };

  const runSweep = async () => {
    try {
      await runRequest('sweep', buildSweepRequest());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("參數錯誤"));
    }
  };

  // ── DCA 回測（P4）──
  const loadDcaBots = async () => {
    try {
      const snap = await api.getDcaSnapshot();
      setDcaBots(snap.bots ?? []);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && onUnauthorized) onUnauthorized();
    }
  };

  const buildDcaIndicators = () => {
    const selected: Array<Record<string, unknown>> = [];
    if (dcaIndicators.rsi) selected.push({ ...DCA_INDICATOR_DEFAULTS.rsi });
    if (dcaIndicators.macd) selected.push({ ...DCA_INDICATOR_DEFAULTS.macd });
    if (dcaIndicators.bollinger) selected.push({ ...DCA_INDICATOR_DEFAULTS.bollinger });
    if (dcaIndicators.price_change) selected.push({ ...DCA_INDICATOR_DEFAULTS.price_change });
    return selected;
  };

  const buildDcaDraft = () => {
    if (!dcaSymbol.trim()) throw new Error(t("請填入 DCA 交易對"));
    if (!dcaBaseOrderAmount || Number(dcaBaseOrderAmount) <= 0) throw new Error(t("基礎單金額必須大於 0"));
    if (!dcaTakeProfitPercent || Number(dcaTakeProfitPercent) <= 0) throw new Error(t("止盈百分比必須大於 0"));
    if (dcaEntryMode === 'indicator' && buildDcaIndicators().length === 0) throw new Error(t("指標模式至少需勾選一個指標"));
    return {
      exchange: dcaExchange,
      symbol: dcaSymbol.trim().toUpperCase(),
      direction: dcaDirection,
      account_mode: 'paper',
      base_order_amount: Number(dcaBaseOrderAmount),
      leverage: Number(dcaLeverage) || 10,
      margin_mode: 'isolated',
      fee_rate: Number(dcaFeeRate) || 0.0004,
      max_safety_orders: Number(dcaMaxSafetyOrders) || 20,
      price_deviation_percent: Number(dcaPriceDeviationPercent) || 0.8,
      price_deviation_multiplier: Number(dcaPriceDeviationMultiplier) || 1.2,
      order_size_multiplier: Number(dcaOrderSizeMultiplier) || 1.0,
      take_profit_percent: Number(dcaTakeProfitPercent) || 1.0,
      stop_loss_percent: dcaStopLossPercent ? Number(dcaStopLossPercent) : undefined,
      entry: {
        mode: dcaEntryMode,
        logic: dcaEntryLogic,
        timeframe: dcaEntryTimeframe,
        fetch_limit: 300,
        price_source: 'close',
        confirm_bars: 1,
        indicators: dcaEntryMode === 'indicator' ? buildDcaIndicators() : [],
      },
    };
  };

  const runDcaBacktest = async () => {
    setDcaRunning(true);
    setDcaError(null);
    setDcaResult(null);
    setDcaCandles(null);
    try {
      if (dcaSource === 'existing' && !dcaBotId) throw new Error(t("請選擇或輸入既有 DCA bot_id"));
      if (dcaBars <= 0 || dcaBars > DCA_BACKTEST_MAX_BARS) throw new Error((t("回測棒數上限為 ") + (DCA_BACKTEST_MAX_BARS)));
      const request: Parameters<typeof api.runDcaBacktest>[0] = dcaSource === 'existing'
        ? { bot_id: dcaBotId, timeframe: dcaTimeframe, bars: dcaBars, initial_capital: dcaInitialCapital }
        : { draft: buildDcaDraft(), timeframe: dcaTimeframe, bars: dcaBars, initial_capital: dcaInitialCapital, exchange: dcaExchange, symbol: dcaSymbol.trim().toUpperCase() };
      const response = await api.runDcaBacktest(request);
      setDcaResult(response.result);
      setDcaCandles(response.result.candles?.length ? response.result.candles : null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && onUnauthorized) {
        onUnauthorized();
        return;
      }
      setDcaError(err instanceof Error ? err.message : t("DCA 回測失敗"));
      setDcaResult(null);
      setDcaCandles(null);
    } finally {
      setDcaRunning(false);
    }
  };

  const m = result?.metrics as Record<string, number | null> | undefined;
  const r = result?.risk as Record<string, number | boolean | null> | undefined;

  const equityData = useMemo(() => {
    if (!result?.equity?.length) return [];
    return (result.equity as Array<{ ts: number; equity: number }>).map((p) => ({
      time: new Date(p.ts * 1000).toISOString().slice(5, 16).replace('T', ' '),
      equity: p.equity,
    }));
  }, [result]);

  return (
    <div className="rankings-page">
      <header className="rankings-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <div className="brand-icon-wrapper" style={{ width: '32px', height: '32px' }}>
                <Gauge size={18} color="#06b6d4" />
              </div>
              <h2 style={{ fontSize: '1.35rem', fontWeight: 700 }}>{t("網格完整回測")}</h2>
            </div>
            <p className="muted" style={{ marginTop: '0.25rem', fontSize: '0.84rem' }}>
              {t("資金感知回測：權益曲線 / 槓桿逐倉 / 爆倉風險 / 追加資金建議")}{' '}</p>
          </div>
          <div className="safe-cache-pill">
            <ShieldCheck size={14} color="#10b981" />
            <span>{t("唯讀回測 · 不下真实單 · 不寫 active 設定")}</span>
          </div>
        </div>
      </header>

      {/* 策略類型切換（P4）：網格 / DCA 單向與雙向共用同一回測分頁 */}
      <div className="rankings-toolbar glass-panel" style={{ marginBottom: '0.8rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="select-pill-group">
            <button type="button" className={`select-pill ${strategyType === 'grid' ? 'active' : ''}`} onClick={() => setStrategyType('grid')}>
              <Gauge size={14} /> {' '}{t("網格回測")}{' '}</button>
            <button type="button" className={`select-pill ${strategyType === 'dca' ? 'active' : ''}`} onClick={() => { setStrategyType('dca'); void loadDcaBots(); }}>
              <Repeat size={14} /> {' '}{t("DCA 回測（單向/雙向 + 指標）")}{' '}</button>
          </div>
          <span className="muted" style={{ fontSize: '0.78rem' }}>
            {t("DCA 模式複用既有 /api/v1/dca/backtest 真實引擎，支援 RSI/MACD/BB/PriceChange 指標。")}{' '}</span>
        </div>
      </div>

      {initialConfig && strategyType === 'grid' && (
        <div className="notice notice-info" style={{ marginBottom: '0.8rem' }}>
          <Search size={16} />
          <span>
            {t("已從策略市場帶入")}{' '}{initialConfig.symbol} ({initialConfig.exchange}{t("）參數，年化估")}{' '}{number(initialConfig.estimated_apr, 1)}%
            <button type="button" className="btn btn-outline" style={{ marginLeft: '0.6rem', padding: '0.25rem 0.5rem', fontSize: '0.72rem' }} onClick={onClearInitialConfig}>
              {t("清除帶入")}{' '}</button>
          </span>
        </div>
      )}

      {strategyType === 'grid' && (
      <div className="rankings-toolbar glass-panel">
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <label className="backtest-field">
            <span>{t("交易所")}</span>
            <select value={exchange} onChange={(e) => setExchange(e.target.value)}>
              <option value="binance">Binance</option>
              <option value="okx">OKX</option>
              <option value="hyperliquid">Hyperliquid</option>
              <option value="lighter">Lighter</option>
              <option value="backpack">Backpack</option>
            </select>
          </label>
          <label className="backtest-field">
            <span>{t("交易對")}</span>
            <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="SOL/USDC:USDC" />
          </label>
          <label className="backtest-field">
            <span>{t("方向")}</span>
            <select value={gridType} onChange={(e) => setGridType(e.target.value)}>
              <option value="neutral">{t("中性")}</option>
              <option value="long">{t("做多")}</option>
              <option value="short">{t("做空")}</option>
            </select>
          </label>
          <label className="backtest-field">
            <span>Lower</span>
            <input type="number" value={lowerPrice} onChange={(e) => setLowerPrice(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>Upper</span>
            <input type="number" value={upperPrice} onChange={(e) => setUpperPrice(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>{t("格數")}</span>
            <input type="number" value={gridCount} onChange={(e) => setGridCount(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>{t("間距")}</span>
            <input type="number" value={gridInterval} onChange={(e) => setGridInterval(e.target.value)} />
          </label>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.5rem' }}>
          <label className="backtest-field">
            <span>{t("每格金額 (USD)")}</span>
            <input type="number" value={perGridNotional} onChange={(e) => setPerGridNotional(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>{t("每格佔總本金 %")}</span>
            <input type="number" value={perGridCapitalPercent} onChange={(e) => setPerGridCapitalPercent(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>{t("初始資金 (USD)")}</span>
            <input type="number" value={initialCapital} onChange={(e) => setInitialCapital(e.target.value)} />
          </label>
          <label className="backtest-field">
            <span>{t("槓桿")}</span>
            <select value={leverage} onChange={(e) => setLeverage(e.target.value)}>
              {[1,2,3,5,10,15,20].map((x) => <option key={x} value={x}>{x}x</option>)}
            </select>
          </label>
          <label className="backtest-field">
            <span>{t("時框")}</span>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {['15m','30m','1h','4h','1d'].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
          <label className="backtest-field">
            <span>{t("區間")}</span>
            <select value={windowPreset} onChange={(e) => setWindowPreset(e.target.value as WindowPreset)}>
              <option value="7d">{t("7 天")}</option>
              <option value="30d">{t("30 天")}</option>
              <option value="180d">{t("180 天")}</option>
              <option value="1y">{t("1 年")}</option>
              <option value="custom">{t("自訂")}</option>
            </select>
          </label>
        </div>

        {windowPreset === 'custom' && (
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.5rem' }}>
            <label className="backtest-field">
              <span>{t("開始 (UTC)")}</span>
              <input type="datetime-local" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
            </label>
            <label className="backtest-field">
              <span>{t("結束 (UTC)")}</span>
              <input type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
            </label>
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.75rem' }}>
          <div className="select-pill-group">
            <button type="button" className={`select-pill ${mode === 'run' ? 'active' : ''}`} onClick={() => setMode('run')}>
              {t("單次回測")}{' '}</button>
            <button type="button" className={`select-pill ${mode === 'stress' ? 'active' : ''}`} onClick={() => setMode('stress')}>
              {t("壓力情境")}{' '}</button>
            <button type="button" className={`select-pill ${mode === 'sweep' ? 'active' : ''}`} onClick={() => setMode('sweep')}>
              {t("參數掃描")}{' '}</button>
          </div>
          {mode === 'sweep' && (
            <>
              <label className="backtest-field">
                <span>{t("掃描格數")}</span>
                <input value={sweepGridCounts} onChange={(e) => setSweepGridCounts(e.target.value)} />
              </label>
              <label className="backtest-field">
                <span>{t("掃描每格金額")}</span>
                <input value={sweepNotionals} onChange={(e) => setSweepNotionals(e.target.value)} />
              </label>
            </>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={running}
            onClick={mode === 'sweep' ? runSweep : run}
          >
            {running ? <><RefreshCw size={15} className="spin" /> {' '}{t("執行中…")}</> : mode === 'stress' ? <><FlaskConical size={15} /> {' '}{t("執行壓力")}</> : mode === 'sweep' ? <><Search size={15} /> {' '}{t("執行掃描")}</> : <><Play size={15} /> {' '}{t("執行回測")}</>}
          </button>
        </div>
      </div>
      )}

      {strategyType === 'grid' && (
      <div className="notice notice-warning" role="note" style={{ marginTop: '0.8rem' }}>
        <AlertTriangle size={16} />
        <span>
          {t("網格回測：逐倉保證金/爆倉為近似；不模擬部分成交、資金費率、滑價；NEUTRAL v1 以做多循環近似；未納入 follow/scalping/martingale。")}{' '}</span>
      </div>
      )}

      {/* DCA 單向/雙向回測（P4） */}
      {strategyType === 'dca' && (
        <>
          <div className="glass-panel" style={{ marginTop: '0.8rem', padding: '1.15rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.7rem' }}>
              <Repeat size={16} color="#a855f7" />
              <h4 style={{ fontSize: '1rem', fontWeight: 600 }}>{t("DCA 回測設定")}</h4>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
              <label className="backtest-field">
                <span>{t("來源")}</span>
                <select value={dcaSource} onChange={(e) => setDcaSource(e.target.value as 'manual' | 'existing')}>
                  <option value="manual">{t("手動參數（draft）")}</option>
                  <option value="existing">{t("既有 DCA bot")}</option>
                </select>
              </label>
              {dcaSource === 'existing' && (
                <>
                  <label className="backtest-field" style={{ minWidth: 220 }}>
                    <span>{t("既有 DCA bot")}</span>
                    <select value={dcaBotId} onChange={(e) => setDcaBotId(e.target.value)}>
                      <option value="">{t("— 請選擇 —")}</option>
                      {dcaBots.map((b) => (
                        <option key={b.bot_id} value={b.bot_id}>
                          {b.bot_id} · {b.symbol ?? ''} · {b.direction ?? ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className="btn btn-outline" onClick={() => void loadDcaBots()} style={{ alignSelf: 'flex-end' }}>
                    <RefreshCw size={14} /> {' '}{t("重新載入")}{' '}</button>
                </>
              )}
            </div>

            {dcaSource === 'manual' && (
              <>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.6rem' }}>
                  <label className="backtest-field">
                    <span>{t("交易所")}</span>
                    <select value={dcaExchange} onChange={(e) => setDcaExchange(e.target.value)}>
                      <option value="binance">Binance</option>
                      <option value="okx">OKX</option>
                      <option value="hyperliquid">Hyperliquid</option>
                      <option value="lighter">Lighter</option>
                      <option value="backpack">Backpack</option>
                    </select>
                  </label>
                  <label className="backtest-field">
                    <span>{t("交易對")}</span>
                    <input value={dcaSymbol} onChange={(e) => setDcaSymbol(e.target.value)} placeholder="ETH/USDC:USDC" />
                  </label>
                  <label className="backtest-field">
                    <span>{t("方向")}</span>
                    <select value={dcaDirection} onChange={(e) => setDcaDirection(e.target.value as 'long' | 'short' | 'both')}>
                      <option value="long">{t("單向做多")}</option>
                      <option value="short">{t("單向做空")}</option>
                      <option value="both">{t("雙向")}</option>
                    </select>
                  </label>
                  <label className="backtest-field">
                    <span>{t("基礎單金額")}</span>
                    <input type="number" value={dcaBaseOrderAmount} onChange={(e) => setDcaBaseOrderAmount(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("安全單上限")}</span>
                    <input type="number" value={dcaMaxSafetyOrders} onChange={(e) => setDcaMaxSafetyOrders(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("價差%")}</span>
                    <input type="number" step="0.1" value={dcaPriceDeviationPercent} onChange={(e) => setDcaPriceDeviationPercent(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("價差倍率")}</span>
                    <input type="number" step="0.1" value={dcaPriceDeviationMultiplier} onChange={(e) => setDcaPriceDeviationMultiplier(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("單量倍率")}</span>
                    <input type="number" step="0.1" value={dcaOrderSizeMultiplier} onChange={(e) => setDcaOrderSizeMultiplier(e.target.value)} />
                  </label>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.6rem' }}>
                  <label className="backtest-field">
                    <span>{t("止盈%")}</span>
                    <input type="number" step="0.1" value={dcaTakeProfitPercent} onChange={(e) => setDcaTakeProfitPercent(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("止損%")}</span>
                    <input type="number" step="0.1" value={dcaStopLossPercent} onChange={(e) => setDcaStopLossPercent(e.target.value)} />
                  </label>
                  <label className="backtest-field">
                    <span>{t("槓桿")}</span>
                    <select value={dcaLeverage} onChange={(e) => setDcaLeverage(e.target.value)}>
                      {[1,2,3,5,10,15,20].map((x) => <option key={x} value={x}>{x}x</option>)}
                    </select>
                  </label>
                  <label className="backtest-field">
                    <span>{t("費率")}</span>
                    <input type="number" step="0.0001" value={dcaFeeRate} onChange={(e) => setDcaFeeRate(e.target.value)} />
                  </label>
                </div>

                <div className="dca-backtest-disclosure" role="note" style={{ marginTop: '0.7rem' }}>
                  <strong>{t("指標入場（若選擇 indicator）")}</strong>
                  <div className="dca-backtest-controls" style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginTop: '0.4rem' }}>
                    <label className="backtest-field">
                      <span>{t("入場模式")}</span>
                      <select value={dcaEntryMode} onChange={(e) => setDcaEntryMode(e.target.value as 'immediate' | 'indicator')}>
                        <option value="immediate">{t("立即進場")}</option>
                        <option value="indicator">{t("指標進場")}</option>
                      </select>
                    </label>
                    <label className="backtest-field">
                      <span>{t("邏輯")}</span>
                      <select value={dcaEntryLogic} onChange={(e) => setDcaEntryLogic(e.target.value as 'all' | 'any')}>
                        <option value="all">{t("全部（AND）")}</option>
                        <option value="any">{t("任一（OR）")}</option>
                      </select>
                    </label>
                    <label className="backtest-field">
                      <span>{t("指標時框")}</span>
                      <select value={dcaEntryTimeframe} onChange={(e) => setDcaEntryTimeframe(e.target.value)}>
                        {['5m','15m','30m','1h','4h','1d'].map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="dca-backtest-controls" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
                    {(['rsi', 'macd', 'bollinger', 'price_change'] as const).map((key) => (
                      <label key={key} className="indicator-check" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.82rem' }}>
                        <input type="checkbox" checked={dcaIndicators[key]} onChange={(e) => setDcaIndicators((prev) => ({ ...prev, [key]: e.target.checked }))} />
                        {key.replace('_', ' ').toUpperCase()}
                      </label>
                    ))}
                  </div>
                </div>
              </>
            )}

            <DcaBacktestPanel
              timeframe={dcaTimeframe}
              bars={dcaBars}
              initialCapital={dcaInitialCapital}
              running={dcaRunning}
              disclosures={[
                t("回測不模擬部分成交"),
                t("未套用交易所最小名目限制"),
                t("歷史績效不代表未來"),
                t("DCA 回測使用真實 DcaCoordinator/Executor；指標為實際判定。"),
              ]}
              scopeNote={t("以歷史 K 線回放 DCA 設定（單向或雙向）；回測為唯讀操作，不會下任何真實訂單。")}
              onTimeframeChange={(tf) => setDcaTimeframe(tf as DcaBacktestTimeframe)}
              onBarsChange={setDcaBars}
              onInitialCapitalChange={setDcaInitialCapital}
              onRun={() => void runDcaBacktest()}
              onCancel={() => setDcaRunning(false)}
            />

            {dcaError && (
              <div className="notice notice-error" role="alert" style={{ marginTop: '0.6rem' }}>
                <AlertTriangle size={16} />
                <span>{dcaError}</span>
              </div>
            )}
          </div>

          {dcaResult && (
            <Suspense fallback={<p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>{t("載入 DCA 回測結果…")}</p>}>
              <div style={{ marginTop: '0.9rem' }}>
                <DcaBacktestResult result={dcaResult} candles={dcaCandles} direction={dcaDirection} />
              </div>
            </Suspense>
          )}
        </>
      )}

      {error && (
        <div className="notice notice-error" style={{ marginTop: '0.8rem' }}>
          <AlertTriangle size={18} />
          <span>{error}</span>
        </div>
      )}

      {/* 單次回測結果 */}
      {strategyType === 'grid' && mode === 'run' && result && (
        <div className="glass-panel" style={{ marginTop: '0.9rem', padding: '1.1rem' }}>
          <h4 className="dca-backtest-subhead"><TrendingUp size={15} /> {' '}{t("回測結果（")}{result.bars} {' '}{t("根")}{' '}{result.timeframe})</h4>
          <div className="kpi-grid dca-metric-grid">
            <MetricCard label={t("總報酬")} value={signature(typeof m?.total_return_pct === 'number' ? m.total_return_pct : null)} />
            <MetricCard label={t("買入持有")} value={signature(typeof m?.buy_hold_return_pct === 'number' ? m.buy_hold_return_pct : null)} />
            <MetricCard label="Alpha" value={signature(typeof m?.alpha_pct === 'number' ? m.alpha_pct : null)} />
            <MetricCard label={t("最大回撤")} value={fraction(typeof m?.max_drawdown_pct === 'number' ? m.max_drawdown_pct : null)} />
            <MetricCard label={t("期末權益")} value={money(typeof m?.final_equity === 'number' ? m.final_equity : null)} />
            <MetricCard label={t("已實現 / 手續費")} value={`${money(typeof m?.realized_pnl === 'number' ? m.realized_pnl : null)} / ${money(typeof m?.total_fees === 'number' ? m.total_fees : null)}`} />
            <MetricCard label={t("完成循環")} value={typeof m?.cycles_completed === 'number' ? String(m.cycles_completed) : '—'} />
          </div>

          {r?.liquidation_occurred ? (
            <div className="notice notice-error" role="alert" style={{ marginTop: '0.6rem' }}>
              <AlertTriangle size={18} />
              <span>
                {t("模擬期間觸發爆倉（")}{r.liquidation_ts ? new Date(Number(r.liquidation_ts) * 1000).toISOString() : ''}{t("）。 若初始資金再增加")}{' '}<strong>{money(typeof r.additional_capital_to_prevent_liquidation === 'number' ? Number(r.additional_capital_to_prevent_liquidation) : null)}</strong>{t("，可避免觸發清算。")}{' '}</span>
            </div>
          ) : (
            <div className="notice notice-success" style={{ marginTop: '0.6rem', color: 'inherit', background: 'rgba(16,185,129,0.08)' }}>
              <ShieldCheck size={16} color="#10b981" />
              <span>
                {t("未觸發爆倉。距離清算最接近的餘裕：")}{fraction(typeof r?.worst_margin_buffer === 'number' ? (Number(r.worst_margin_buffer) + 1) * 100 - 100 : null)}{' '}
                {r?.additional_capital_to_prevent_liquidation != null && Number(r.additional_capital_to_prevent_liquidation) > 0 ? (t("（最嚴重時需再注入 ") + (money(Number(r.additional_capital_to_prevent_liquidation))) + t("）")) : ''}
              </span>
            </div>
          )}

          {equityData.length > 0 && (
            <div style={{ width: '100%', height: 280, marginTop: '0.8rem' }}>
              <EquityMini chartData={equityData} />
            </div>
          )}

          {result.warnings.length > 0 && (
            <div className="dca-backtest-disclosure" role="note" style={{ marginTop: '0.7rem' }}>
              <strong>{t("回測簡化事項")}</strong>
              <ul>{result.warnings.map((w, i) => <li key={`${i}-${w}`}>{w}</li>)}</ul>
            </div>
          )}
        </div>
      )}

      {/* 壓力情境 */}
      {strategyType === 'grid' && mode === 'stress' && stress && (
        <div className="glass-panel" style={{ marginTop: '0.9rem', padding: '1.1rem' }}>
          <h4 className="dca-backtest-subhead"><FlaskConical size={15} /> {' '}{t("壓力情境（起價")}{' '}{money(stress.start_price)})</h4>
          <div className="table-wrapper">
            <table className="pro-table">
              <thead><tr><th>{t("移動%")}</th><th>{t("爆倉")}</th><th>{t("最低權益")}</th><th>{t("期末權益")}</th><th>{t("需追加資金防爆倉")}</th></tr></thead>
              <tbody>
                {stress.scenarios.map((s) => (
                  <tr key={s.movement_pct}>
                    <td className={`font-mono ${s.movement_pct < 0 ? 'negative' : 'positive'}`}>{signature(s.movement_pct)}</td>
                    <td>{s.liquidation_occurred ? t("⚠️ 爆倉") : t("安全")}</td>
                    <td className="font-mono">{money(s.min_equity ?? null)}</td>
                    <td className="font-mono">{money(s.final_equity)}</td>
                    <td className="font-mono">{money(s.additional_capital_to_prevent_liquidation ?? null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 參數掃描 */}
      {strategyType === 'grid' && mode === 'sweep' && sweep && (
        <div className="glass-panel" style={{ marginTop: '0.9rem', padding: '1.1rem' }}>
          <h4 className="dca-backtest-subhead"><Search size={15} /> {' '}{t("參數掃描（")}{sweep.runs.length} {' '}{t("組）")}</h4>
          <div className="table-wrapper">
            <table className="pro-table">
              <thead><tr><th>{t("格數")}</th><th>{t("每格金額")}</th><th>{t("槓桿")}</th><th>{t("總報酬")}</th><th>{t("回撤")}</th><th>{t("爆倉")}</th></tr></thead>
              <tbody>
                {sweep.runs.map((run, i) => {
                  const p = run.params as Record<string, number>;
                  const mtr = run.metrics as Record<string, number | null>;
                  const risk = run.risk as Record<string, boolean | number | null>;
                  return (
                    <tr key={`${p.grid_count}-${p.per_grid_notional}-${p.leverage}-${i}`}>
                      <td className="font-mono">{p.grid_count}</td>
                      <td className="font-mono">{money(p.per_grid_notional ?? null)}</td>
                      <td className="font-mono">{p.leverage}x</td>
                      <td className="font-mono">{signature(typeof mtr.total_return_pct === 'number' ? mtr.total_return_pct : null)}</td>
                      <td className="font-mono">{fraction(typeof mtr.max_drawdown_pct === 'number' ? mtr.max_drawdown_pct : null)}</td>
                      <td>{risk.liquidation_occurred ? '⚠️' : '✓'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {strategyType === 'grid' && !result && !stress && !sweep && !running && (
        <div className="glass-panel" style={{ marginTop: '0.9rem', padding: '2.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          <Activity size={24} className="spin" style={{ margin: '0 auto 0.6rem' }} />
          <p>{t("輸入參數後執行回測 / 壓力 / 掃描。")}</p>
        </div>
      )}
    </div>
  );
};

const MetricCard: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <article className="kpi-card dca-metric-card" style={{ minHeight: 'auto', padding: '0.85rem' }}>
    <span className="text-secondary" style={{ fontSize: '0.72rem' }}>{label}</span>
    <strong className="font-mono" style={{ fontSize: '1.1rem' }}>{value}</strong>
  </article>
);

const EquityMini: React.FC<{ chartData: Array<{ time: string; equity: number }> }> = ({ chartData }) => (
  <ResponsiveContainer width="100%" height="100%">
    <LineChart data={chartData}>
      <XAxis dataKey="time" tick={{ fontSize: 10 }} stroke="#94a3b8" />
      <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
      <Line type="monotone" dataKey="equity" stroke="#06b6d4" strokeWidth={2} dot={false} />
    </LineChart>
  </ResponsiveContainer>
);

export default GridBacktestPage;
