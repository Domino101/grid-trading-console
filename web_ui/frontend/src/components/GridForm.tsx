import React, { useMemo, useState, useEffect } from 'react';
import {
  AlertTriangle,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Settings,
  ShieldCheck,
  Zap,
  Info,
  DollarSign,
  Sparkles,
  Flame,
  RefreshCw,
  BarChart3,
  PlusCircle,
  Activity,
  ArrowRight,
} from 'lucide-react';
import {
  api,
  type CreateGridRequest,
  type MarketInfo,
  type GridRecommendationResponse,
} from '../services/api';
import { canAdvanceCreateStep } from '../utils/format';


type Notice = { type: 'success' | 'error'; text: string } | null;

export interface InitialGridConfig extends Partial<CreateGridRequest> {
  initialGridCount?: number;
  initialPeriod?: '7d' | '30d' | '180d';
  estimatedApr?: number;
}

export interface CreatedGridInfo {
  grid_id: string;
  config_path: string;
  status: string;
  message: string;
  command_id?: string | null;
  symbol: string;
  exchange: string;
  mode: 'paper' | 'live';
  auto_start: boolean;
  lower_price?: number;
  upper_price?: number;
  grid_interval?: number;
  order_amount?: number;
  leverage?: number;
}

interface GridFormProps {
  initialConfig?: InitialGridConfig | null;
  onClearInitialConfig?: () => void;
  onOpenGrid?: (gridId: string) => void;
  onNavigateDashboard?: () => void;
}

const initialForm: CreateGridRequest = {
  symbol: 'BTC/USDT',
  exchange: 'binance',
  grid_type: 'neutral',
  lower_price: 60000,
  upper_price: 70000,
  grid_interval: 100,
  order_amount: 0.001,
  leverage: 1,
  margin_mode: 'cross',
  account_mode: 'paper',
  auto_start: false,
};

const GridForm: React.FC<GridFormProps> = ({
  initialConfig,
  onClearInitialConfig,
  onOpenGrid,
  onNavigateDashboard,
}) => {
  const [step, setStep] = useState(initialConfig ? 2 : 1);
  const [formData, setFormData] = useState<CreateGridRequest>(() => ({
    ...initialForm,
    ...(initialConfig || {}),
  }));
  const [mode, setMode] = useState<'paper' | 'live'>('paper');
  const [loading, setLoading] = useState(false);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketInfo, setMarketInfo] = useState<MarketInfo | null>(null);
  const [gridCount, setGridCount] = useState<number | null>(initialConfig?.initialGridCount ?? null);
  const [liveConfirmation, setLiveConfirmation] = useState('');
  const [notice, setNotice] = useState<Notice>(null);
  const [createdGrid, setCreatedGrid] = useState<CreatedGridInfo | null>(null);
  const [redirectCountdown, setRedirectCountdown] = useState<number | null>(null);

  // AI 推薦與歷史回測狀態
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendation, setRecommendation] = useState<GridRecommendationResponse | null>(null);
  const [selectedPeriodKey, setSelectedPeriodKey] = useState<'7d' | '30d' | '180d'>(
    initialConfig?.initialPeriod ?? '7d'
  );
  const [liveTradingEnabled, setLiveTradingEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const h = await api.getSystemHealth();
        setLiveTradingEnabled(Boolean(h.live_trading_enabled));
      } catch {
        // silent check
      }
    };
    void fetchHealth();
  }, []);

  useEffect(() => {
    if (initialConfig) {
      // 由策略市場帶入的初始設定：一次性同步到表單狀態（prop → state 同步）。
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFormData((prev) => ({
        ...prev,
        ...initialConfig,
      }));
      if (initialConfig.initialGridCount) {
        setGridCount(initialConfig.initialGridCount);
      }
      if (initialConfig.initialPeriod) {
        setSelectedPeriodKey(initialConfig.initialPeriod);
      }
      setStep(2);
      if (initialConfig.estimatedApr) {
        setNotice({
          type: 'success',
          text: `✨ 已從策略市場載入 ${initialConfig.symbol} AI 推薦參數（預估歷史回測年化: ${initialConfig.estimatedApr}%）`,
        });
      }
    }
  }, [initialConfig]);



  useEffect(() => {
    if (redirectCountdown === null || redirectCountdown <= 0) {
      if (redirectCountdown === 0 && createdGrid && onOpenGrid) {
        onOpenGrid(createdGrid.grid_id);
      }
      return;
    }
    const timer = window.setTimeout(() => {
      setRedirectCountdown((prev) => (prev !== null ? prev - 1 : null));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [redirectCountdown, createdGrid, onOpenGrid]);

  const handleCreateAnother = () => {
    setCreatedGrid(null);
    setRedirectCountdown(null);
    setFormData(initialForm);
    setGridCount(null);
    setMode('paper');
    setLiveConfirmation('');
    setNotice(null);
    setStep(1);
    onClearInitialConfig?.();
  };

  const estimatedCapital = useMemo(() => {
    if (!formData.lower_price || !formData.upper_price || !formData.grid_interval || !formData.order_amount) {
      return null;
    }
    const levels = Math.max(1, Math.ceil((formData.upper_price - formData.lower_price) / formData.grid_interval));
    const avgPrice = (formData.lower_price + formData.upper_price) / 2;
    const nominalPerGrid = avgPrice * formData.order_amount;
    const amount = nominalPerGrid * Math.min(levels, 10);
    return { levels, amount, nominalPerGrid };
  }, [formData]);

  const update = (name: keyof CreateGridRequest, value: string | number | boolean) => {
    setFormData((current) => ({ ...current, [name]: value }));
  };

  const loadMarketInfo = async () => {
    setMarketLoading(true);
    setNotice(null);
    try {
      const info = await api.getMarketInfo(formData.symbol, formData.exchange);
      setMarketInfo(info);
      setFormData((current) => ({
        ...current,
        price_decimals: info.price_precision ?? current.price_decimals,
        quantity_precision: info.quantity_precision ?? current.quantity_precision,
      }));
    } catch (caught) {
      setMarketInfo(null);
      setNotice({
        type: 'error',
        text: caught instanceof Error ? caught.message : '無法讀取市場規則',
      });
    } finally {
      setMarketLoading(false);
    }
  };

  const loadRecommendation = async (period: '7d' | '30d' | '180d' = selectedPeriodKey) => {
    setRecommendationLoading(true);
    setSelectedPeriodKey(period);
    setNotice(null);
    try {
      const days = period === '180d' ? 180 : period === '30d' ? 30 : 7;
      const res = await api.getGridRecommendation(formData.symbol, formData.exchange, days);
      setRecommendation(res);
      setFormData((current) => ({
        ...current,
        price_decimals: res.price_precision ?? current.price_decimals,
        quantity_precision: res.quantity_precision ?? current.quantity_precision,
      }));
    } catch (caught) {
      setNotice({
        type: 'error',
        text: caught instanceof Error ? caught.message : '無法獲取 AI 推薦參數與回測數據',
      });
    } finally {
      setRecommendationLoading(false);
    }
  };

  const applyRecommendation = (periodKey: '7d' | '30d' | '180d' = selectedPeriodKey) => {
    if (!recommendation || !recommendation.periods[periodKey]) return;
    const p = recommendation.periods[periodKey];
    setFormData((current) => ({
      ...current,
      lower_price: p.lower_price,
      upper_price: p.upper_price,
      grid_interval: p.grid_interval,
      order_amount: p.recommended_order_amount,
      price_decimals: recommendation.price_precision,
      quantity_precision: recommendation.quantity_precision,
    }));
    setGridCount(p.grid_count);
    setNotice({
      type: 'success',
      text: `✨ 已套用 ${p.days}D AI 推薦網格參數！區間: $${p.lower_price} ~ $${p.upper_price} (${p.grid_count} 格，歷史回測年化 APY: ${p.estimated_apr}%)`,
    });
  };

  const perGridNotional =
    formData.lower_price && formData.upper_price
      ? ((formData.lower_price + formData.upper_price) / 2) * formData.order_amount
      : null;

  const minNotionalError =
    marketInfo?.min_notional != null &&
    perGridNotional !== null &&
    perGridNotional < marketInfo.min_notional;

  const canAdvance = canAdvanceCreateStep(step, formData, mode, liveConfirmation, Boolean(minNotionalError));

  const submit = async () => {
    setLoading(true);
    setNotice(null);
    try {
      const precision = marketInfo?.price_precision;
      const normalizedInterval =
        precision === null || precision === undefined
          ? formData.grid_interval
          : Number(formData.grid_interval.toFixed(precision));

      const res = await api.createGrid({
        ...formData,
        grid_interval: normalizedInterval,
        account_mode: mode,
        metadata: {
          source: 'dashboard',
          reason: `${mode} strategy created from guided form`,
        },
      });

      const newCreated: CreatedGridInfo = {
        grid_id: res.grid_id,
        config_path: res.config_path,
        status: res.status,
        message: res.message,
        command_id: res.command_id,
        symbol: formData.symbol,
        exchange: formData.exchange,
        mode: mode,
        auto_start: Boolean(formData.auto_start),
        lower_price: formData.lower_price,
        upper_price: formData.upper_price,
        grid_interval: normalizedInterval,
        order_amount: formData.order_amount,
        leverage: formData.leverage,
      };

      setCreatedGrid(newCreated);
      setNotice({
        type: 'success',
        text:
          mode === 'paper'
            ? `🎉 PAPER 策略 ${res.grid_id} 建立成功！`
            : `🎉 LIVE 實盤策略 ${res.grid_id} 建立成功！`,
      });
      setStep(4);
      onClearInitialConfig?.();
      // Start 5s countdown for automatic redirect
      setRedirectCountdown(5);
    } catch (caught) {
      setNotice({
        type: 'error',
        text: caught instanceof Error ? caught.message : '網格創建失敗',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="grid-form-page">
      {/* Header */}
      <header className="form-header">
        <p className="eyebrow">Strategy Wizard</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <Settings size={22} color="var(--accent-primary)" />
          <h2>建立量化網格策略</h2>
        </div>
        <p className="muted">
          引導式參數配置。PAPER 使用虛擬模擬撮合；LIVE 模式下單即進入實盤，需具備 <code>GRID_LIVE_TRADING_ENABLED</code> 授權。
        </p>
      </header>

      {/* Stepper Header */}
      <div className="stepper-header" role="navigation" aria-label="建立策略步驟">
        <div className={`step-box ${step >= 1 ? 'active' : ''}`}>
          <div className="step-num">1</div>
          <div>
            <strong style={{ fontSize: '0.85rem' }}>市場與模式</strong>
            <p className="muted" style={{ fontSize: '0.72rem' }}>帳戶與交易對</p>
          </div>
        </div>

        <div className={`step-box ${step >= 2 ? 'active' : ''}`}>
          <div className="step-num">2</div>
          <div>
            <strong style={{ fontSize: '0.85rem' }}>價格與網格</strong>
            <p className="muted" style={{ fontSize: '0.72rem' }}>區間與每格單量</p>
          </div>
        </div>

        <div className={`step-box ${step >= 3 ? 'active' : ''}`}>
          <div className="step-num">3</div>
          <div>
            <strong style={{ fontSize: '0.85rem' }}>風控與部署</strong>
            <p className="muted" style={{ fontSize: '0.72rem' }}>槓桿與確認提交</p>
          </div>
        </div>
      </div>

      {/* Notice Alert */}
      {notice && (
        <div className={`notice ${notice.type === 'success' ? 'notice-success' : 'notice-error'}`} role="alert">
          {notice.type === 'success' ? <CheckCircle size={18} style={{ flexShrink: 0 }} /> : <AlertTriangle size={18} style={{ flexShrink: 0 }} />}
          <span>{notice.text}</span>
        </div>
      )}

      {/* Wizard Form Card */}
      <section className="form-card">
        {/* Step 1: Market & Mode */}
        {step === 1 && (
          <div className="form-fields-grid">
            <div className="field-full">
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>選擇帳戶模式與交易所</h3>
              <p className="muted">指定本策略的執行環境與目標對接市場</p>
            </div>

            <div className="field-full">
              <span className="input-label" style={{ marginBottom: '0.5rem' }}>帳戶模式 (Account Mode)</span>
              <div className="mode-selector-grid">
                <button
                  type="button"
                  className={`mode-choice-card ${mode === 'paper' ? 'active paper' : ''}`}
                  onClick={() => setMode('paper')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <strong>PAPER 模擬盤</strong>
                    <span className="badge badge-paper">零風險</span>
                  </div>
                  <p className="muted" style={{ fontSize: '0.78rem' }}>
                    使用即時公開訂單簿進行虛擬撮合，不涉及任何真實資產。
                  </p>
                </button>

                <button
                  type="button"
                  className={`mode-choice-card ${mode === 'live' ? 'active live' : ''}`}
                  onClick={() => setMode('live')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <strong>LIVE 實盤交易</strong>
                    <span className="badge badge-live">實盤資金</span>
                  </div>
                  <p className="muted" style={{ fontSize: '0.78rem' }}>
                    直接對接交易所 API 進行真實下單與保證金扣款。
                  </p>
                </button>
              </div>

              {mode === 'live' && (
                <div style={{ marginTop: '0.65rem' }}>
                  {liveTradingEnabled === true ? (
                    <div className="notice notice-success" style={{ fontSize: '0.78rem', padding: '0.5rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <CheckCircle size={15} color="#10b981" />
                      <span>🟢 實盤環境已解鎖 (GRID_LIVE_TRADING_ENABLED=1)，可直接建立並啟動實盤網格。</span>
                    </div>
                  ) : liveTradingEnabled === false ? (
                    <div className="notice notice-warning" style={{ fontSize: '0.78rem', padding: '0.5rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <AlertTriangle size={15} color="#f59e0b" />
                      <span>⚠️ 實盤開關尚未啟用：伺服器 .env 需設定 <code>GRID_LIVE_TRADING_ENABLED=1</code> 才能啟動實盤。</span>
                    </div>
                  ) : null}
                </div>
              )}
            </div>

            <label className="input-label">
              <span>交易所 (Exchange)</span>
              <select value={formData.exchange} onChange={(e) => update('exchange', e.target.value)}>
                <option value="binance">Binance (幣安)</option>
                <option value="lighter">Lighter DEX</option>
                <option value="hyperliquid">Hyperliquid L1</option>
              </select>
            </label>

            <label className="input-label">
              <span>交易對 (Symbol)</span>
              <input
                value={formData.symbol}
                onChange={(e) => update('symbol', e.target.value.toUpperCase())}
                placeholder="例如: BTC/USDT"
              />
            </label>

            <label className="input-label">
              <span>網格方向 (Grid Direction)</span>
              <select value={formData.grid_type} onChange={(e) => update('grid_type', e.target.value)}>
                <option value="neutral">中性網格 (Neutral)</option>
                <option value="long">做多網格 (Long)</option>
                <option value="short">做空網格 (Short)</option>
              </select>
            </label>

            <div className="field-full" style={{ marginTop: '0.5rem' }}>
              <div className="glass-panel" style={{ padding: '0.85rem 1rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                  <Info size={16} color="var(--accent-cyan)" />
                  <div>
                    <strong style={{ fontSize: '0.84rem' }}>交易對市場規範檢測</strong>
                    <p className="muted" style={{ fontSize: '0.76rem' }}>
                      {marketInfo
                        ? `價格精度: ${marketInfo.price_precision ?? '—'} 位 · 數量精度: ${marketInfo.quantity_precision ?? '—'} 位 · 最小名義額: ${marketInfo.min_notional ?? '—'} USDT`
                        : '建議在設定網格參數前，點擊右側按鈕自動檢測最小下單要求。'}
                    </p>
                  </div>
                </div>

                <button
                  className="btn btn-outline"
                  type="button"
                  disabled={marketLoading || !formData.symbol}
                  onClick={() => void loadMarketInfo()}
                  style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}
                >
                  {marketLoading ? '檢測中…' : '檢測市場規則'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Price Bounds & Geometry */}
        {step === 2 && (
          <div className="form-fields-grid">
            <div className="field-full">
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>價格區間與網格密度配置</h3>
              <p className="muted">設定網格的上下邊界、格距與單筆掛單大小</p>
            </div>

            {/* AI Recommendation & Backtest Panel */}
            <div className="field-full">
              <div className="ai-recommend-card">
                <div className="ai-recommend-header">
                  <div className="ai-title-badge">
                    <Sparkles size={18} color="#a855f7" />
                    <h4>AI 智能網格推薦與歷史回測</h4>
                    <span className="badge badge-paper" style={{ fontSize: '0.7rem' }}>
                      {formData.exchange.toUpperCase()} 歷史 K 線撮合
                    </span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {recommendation && (
                      <div className="ai-period-tabs">
                        <button
                          type="button"
                          className={`ai-period-tab ${selectedPeriodKey === '7d' ? 'active' : ''}`}
                          onClick={() => {
                            setSelectedPeriodKey('7d');
                          }}
                        >
                          7 天 (7D)
                        </button>
                        <button
                          type="button"
                          className={`ai-period-tab ${selectedPeriodKey === '30d' ? 'active' : ''}`}
                          onClick={() => {
                            setSelectedPeriodKey('30d');
                          }}
                        >
                          30 天 (30D)
                        </button>
                        <button
                          type="button"
                          className={`ai-period-tab ${selectedPeriodKey === '180d' ? 'active' : ''}`}
                          onClick={() => {
                            setSelectedPeriodKey('180d');
                          }}
                        >
                          180 天 (180D)
                        </button>
                      </div>
                    )}

                    <button
                      type="button"
                      className="btn btn-outline"
                      disabled={recommendationLoading || !formData.symbol}
                      onClick={() => void loadRecommendation(selectedPeriodKey)}
                      style={{ fontSize: '0.78rem', padding: '0.35rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}
                    >
                      <RefreshCw size={14} className={recommendationLoading ? 'spin' : ''} />
                      {recommendationLoading ? '分析回測中…' : recommendation ? '重新分析' : '獲取 AI 推薦'}
                    </button>
                  </div>
                </div>

                {recommendation && recommendation.periods[selectedPeriodKey] ? (
                  <>
                    <div className="ai-stats-grid">
                      <div className="ai-stat-box">
                        <span className="stat-label">歷史回測年化 (Backtest APY)</span>
                        <div className="stat-value ai-apr-highlight">
                          <Flame size={16} color="#10b981" />
                          <span>{recommendation.periods[selectedPeriodKey].estimated_apr.toFixed(2)}%</span>
                        </div>
                      </div>

                      <div className="ai-stat-box">
                        <span className="stat-label">推薦價格區間</span>
                        <div className="stat-value" style={{ fontSize: '0.86rem' }}>
                          ${recommendation.periods[selectedPeriodKey].lower_price.toLocaleString()} ~ ${recommendation.periods[selectedPeriodKey].upper_price.toLocaleString()}
                        </div>
                      </div>

                      <div className="ai-stat-box">
                        <span className="stat-label">推薦網格數 / 間距</span>
                        <div className="stat-value">
                          {recommendation.periods[selectedPeriodKey].grid_count} 格
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '0.35rem', fontWeight: 400 }}>
                            (${recommendation.periods[selectedPeriodKey].grid_interval})
                          </span>
                        </div>
                      </div>

                      <div className="ai-stat-box">
                        <span className="stat-label">回測套利循環次數</span>
                        <div className="stat-value">
                          {recommendation.periods[selectedPeriodKey].completed_cycles} 次
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                      <button
                        type="button"
                        className="btn-ai-apply"
                        onClick={() => applyRecommendation(selectedPeriodKey)}
                      >
                        <Sparkles size={16} />
                        <span>一鍵套用 {recommendation.periods[selectedPeriodKey].days}D 推薦參數</span>
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="muted" style={{ fontSize: '0.8rem', margin: 0 }}>
                    依據過去 7D / 30D / 180D 的真實歷史 K 線與波動率，自動推算最佳震盪區間並透過虛擬網格撮合計算真實歷史回測年化。點擊上方「獲取 AI 推薦」立即分析。
                  </p>
                )}
              </div>
            </div>

            <label className="input-label">
              <span>下限價格 (Lower Price)</span>
              <input
                type="number"
                step="any"
                value={formData.lower_price ?? ''}
                onChange={(e) => update('lower_price', Number(e.target.value))}
                placeholder="例如: 60000"
              />
            </label>


            <label className="input-label">
              <span>上限價格 (Upper Price)</span>
              <input
                type="number"
                step="any"
                value={formData.upper_price ?? ''}
                onChange={(e) => update('upper_price', Number(e.target.value))}
                placeholder="例如: 70000"
              />
            </label>

            <label className="input-label">
              <span>每格間距 (Grid Interval)</span>
              <input
                type="number"
                min="0"
                step="any"
                value={formData.grid_interval}
                onChange={(e) => {
                  setGridCount(null);
                  update('grid_interval', Number(e.target.value));
                }}
              />
            </label>

            <label className="input-label">
              <span>預計格數 (可選自動推算格距)</span>
              <input
                type="number"
                min="1"
                value={gridCount ?? ''}
                onChange={(e) => {
                  const count = Number(e.target.value);
                  setGridCount(count || null);
                  if (count > 0 && formData.lower_price && formData.upper_price) {
                    update('grid_interval', (formData.upper_price - formData.lower_price) / count);
                  }
                }}
                placeholder="輸入格數自動計算格距"
              />
            </label>

            <label className="input-label field-full">
              <span>每格下單量 (Order Amount)</span>
              <input
                type="number"
                min="0"
                step="any"
                value={formData.order_amount}
                onChange={(e) => update('order_amount', Number(e.target.value))}
              />
            </label>

            {/* Real-time Capital Estimation Preview */}
            {estimatedCapital && (
              <div className="field-full capital-preview-card">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-accent)' }}>
                  <DollarSign size={16} />
                  <strong style={{ fontSize: '0.88rem' }}>即時本金與名義額預估</strong>
                </div>

                <div className="capital-preview-stats">
                  <div className="capital-stat-item">
                    <span>預估網格數</span>
                    <strong>{estimatedCapital.levels} 格</strong>
                  </div>
                  <div className="capital-stat-item">
                    <span>單格名義金額</span>
                    <strong>約 {perGridNotional?.toLocaleString('en-US', { maximumFractionDigits: 2 }) ?? '—'} USDT</strong>
                  </div>
                  <div className="capital-stat-item">
                    <span>前 10 格運作名義本金</span>
                    <strong className="positive">約 {estimatedCapital.amount.toLocaleString('en-US', { maximumFractionDigits: 2 })} USDT</strong>
                  </div>
                </div>

                {minNotionalError && (
                  <div className="notice notice-error" style={{ padding: '0.45rem 0.75rem', marginTop: '0.35rem' }}>
                    <AlertTriangle size={15} />
                    <span>單格名義額低於交易所最低限制 ({marketInfo?.min_notional} USDT)，請提高單格下單量。</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Step 3: Risk & Confirmation */}
        {step === 3 && (
          <div className="form-fields-grid">
            <div className="field-full">
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: '0.25rem' }}>風控參數與部署確認</h3>
              <p className="muted">最後檢查保證金槓桿倍數與安全啟動授權</p>
            </div>

            <label className="input-label">
              <span>槓桿倍數 (Leverage: 1 - 125x)</span>
              <input
                type="number"
                min="1"
                max="125"
                value={formData.leverage ?? 1}
                onChange={(e) => update('leverage', Number(e.target.value))}
              />
            </label>

            <label className="input-label">
              <span>保證金模式 (Margin Mode)</span>
              <select value={formData.margin_mode ?? 'cross'} onChange={(e) => update('margin_mode', e.target.value)}>
                <option value="cross">全倉模式 (Cross Margin)</option>
                <option value="isolated">逐倉模式 (Isolated Margin)</option>
              </select>
            </label>

            <div className="field-full">
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', cursor: 'pointer', fontSize: '0.86rem' }}>
                <input
                  type="checkbox"
                  checked={formData.auto_start ?? false}
                  onChange={(e) => update('auto_start', e.target.checked)}
                />
                <span>建立成功後自動向引擎發送啟動指令 (Auto Start)</span>
              </label>
            </div>

            {mode === 'live' && (
              <div className="field-full notice notice-error" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '0.5rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <AlertTriangle size={18} />
                  <strong>實盤 LIVE 雙重安全確認：</strong>
                </div>
                <p style={{ fontSize: '0.82rem' }}>
                  您正在建立實盤交易策略。請在下方輸入 <code>CONFIRM LIVE</code> 以確認提交：
                </p>
                <input
                  className="search-input"
                  style={{ width: '100%', maxWidth: '300px', background: 'rgba(0,0,0,0.4)' }}
                  value={liveConfirmation}
                  onChange={(e) => setLiveConfirmation(e.target.value)}
                  placeholder="輸入 CONFIRM LIVE"
                  autoComplete="off"
                />
              </div>
            )}

            <div className="field-full glass-panel" style={{ padding: '1rem' }}>
              <strong style={{ fontSize: '0.86rem', display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                <ShieldCheck size={16} color="var(--trading-green)" />
                策略摘要預覽
              </strong>
              <p className="muted font-mono" style={{ fontSize: '0.8rem', marginTop: '0.35rem' }}>
                {formData.symbol} · {formData.exchange.toUpperCase()} · {formData.grid_type.toUpperCase()} · 區間 [{formData.lower_price} - {formData.upper_price}] · 槓桿 {formData.leverage}x
              </p>
            </div>
          </div>
        )}

        {/* Step 4: Creation Success & Deployment View */}
        {step === 4 && createdGrid && (
          <div className="creation-success-card">
            <div className="success-badge-header">
              <div className="success-icon-pulse">
                <CheckCircle size={32} color="#10b981" />
              </div>
              <div>
                <h3 className="success-title">
                  {createdGrid.mode === 'live' ? '🟢 實盤 LIVE 策略建立完成' : '🟡 模擬 PAPER 策略建立完成'}
                </h3>
                <p className="muted" style={{ fontSize: '0.82rem', margin: '0.2rem 0 0 0' }}>
                  {createdGrid.auto_start
                    ? '已成功發送啟動指令至引擎 (5 秒內熱插拔載入生效)'
                    : '策略設定檔已安全保存至磁碟 (目前為停止狀態，可隨時手動啟動)'}
                </p>
              </div>
            </div>

            <div className="created-grid-details-grid">
              <div className="created-detail-item">
                <span className="detail-label">策略 ID (Grid ID)</span>
                <span className="detail-value font-mono highlight">{createdGrid.grid_id}</span>
              </div>
              <div className="created-detail-item">
                <span className="detail-label">交易對 / 交易所</span>
                <span className="detail-value">{createdGrid.symbol} · {createdGrid.exchange.toUpperCase()}</span>
              </div>
              <div className="created-detail-item">
                <span className="detail-label">價格區間</span>
                <span className="detail-value font-mono">${createdGrid.lower_price} ~ ${createdGrid.upper_price}</span>
              </div>
              <div className="created-detail-item">
                <span className="detail-label">格距 / 每格單量</span>
                <span className="detail-value font-mono">${createdGrid.grid_interval} / {createdGrid.order_amount}</span>
              </div>
              <div className="created-detail-item">
                <span className="detail-label">運行模式</span>
                <span className="detail-value">
                  <span className={`badge badge-${createdGrid.mode}`}>
                    {createdGrid.mode.toUpperCase()}
                  </span>
                </span>
              </div>
              <div className="created-detail-item">
                <span className="detail-label">啟動狀態</span>
                <span className="detail-value positive">
                  {createdGrid.auto_start ? '🚀 啟動中 (Pending Engine)' : '⏸️ 已保存 (未啟動)'}
                </span>
              </div>
            </div>

            {/* Countdown / Auto-Redirect Banner */}
            {redirectCountdown !== null && (
              <div className="redirect-countdown-banner">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Zap size={16} color="var(--accent-primary)" />
                  <span>
                    系統將在 <strong style={{ color: 'var(--accent-primary)', fontSize: '1rem' }}>{redirectCountdown}</strong> 秒後自動進入策略監控工作台…
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {onOpenGrid && (
                    <button
                      type="button"
                      className="btn btn-primary"
                      style={{ fontSize: '0.78rem', padding: '0.3rem 0.75rem' }}
                      onClick={() => {
                        setRedirectCountdown(null);
                        onOpenGrid(createdGrid.grid_id);
                      }}
                    >
                      <span>立即前往</span>
                      <ArrowRight size={13} />
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-outline"
                    style={{ fontSize: '0.78rem', padding: '0.3rem 0.6rem' }}
                    onClick={() => setRedirectCountdown(null)}
                  >
                    取消倒數
                  </button>
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div className="success-action-buttons">
              {onOpenGrid && (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    setRedirectCountdown(null);
                    onOpenGrid(createdGrid.grid_id);
                  }}
                  style={{ padding: '0.75rem 1.25rem', fontSize: '0.9rem', justifyContent: 'center' }}
                >
                  <Activity size={17} />
                  <span>前往策略監控工作台 (Workbench)</span>
                </button>
              )}

              {onNavigateDashboard && (
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => {
                    setRedirectCountdown(null);
                    onNavigateDashboard();
                  }}
                  style={{ padding: '0.75rem 1.25rem', fontSize: '0.9rem', justifyContent: 'center' }}
                >
                  <BarChart3 size={17} />
                  <span>返回策略總覽列表 (Dashboard)</span>
                </button>
              )}

              <button
                type="button"
                className="btn btn-outline"
                onClick={handleCreateAnother}
                style={{ padding: '0.75rem 1.25rem', fontSize: '0.9rem', justifyContent: 'center' }}
              >
                <PlusCircle size={17} />
                <span>繼續建立新策略</span>
              </button>
            </div>
          </div>
        )}

        {/* Wizard Navigation Footer */}
        {step < 4 && (
          <footer style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border-subtle)', paddingTop: '1.25rem', marginTop: '0.5rem' }}>
            {step > 1 ? (
              <button className="btn btn-outline" type="button" onClick={() => setStep(step - 1)}>
                <ChevronLeft size={16} />
                <span>上一步</span>
              </button>
            ) : (
              <div />
            )}

            {step < 3 ? (
              <button className="btn btn-primary" type="button" disabled={!canAdvance} onClick={() => setStep(step + 1)}>
                <span>下一步</span>
                <ChevronRight size={16} />
              </button>
            ) : (
              <button className="btn btn-primary" type="button" disabled={loading || !canAdvance} onClick={() => void submit()}>
                <Zap size={16} />
                <span>{loading ? '正在建立策略…' : '確認建立並保存策略'}</span>
              </button>
            )}
          </footer>
        )}
      </section>
    </main>
  );
};

export default GridForm;
