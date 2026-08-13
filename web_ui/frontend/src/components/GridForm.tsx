import { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle, ChevronLeft, ChevronRight, Settings } from 'lucide-react';
import { api, type CreateGridRequest, type MarketInfo } from '../services/api';
import { canAdvanceCreateStep } from '../utils/format';

type Notice = { type: 'success' | 'error'; text: string } | null;

const initialForm: CreateGridRequest = {
  symbol: 'BTC/USDT', exchange: 'binance', grid_type: 'neutral',
  lower_price: 60000, upper_price: 70000, grid_interval: 100, order_amount: 0.001,
  leverage: 1, margin_mode: 'cross', account_mode: 'paper', auto_start: false,
};

const GridForm = () => {
  const [step, setStep] = useState(1);
  const [formData, setFormData] = useState<CreateGridRequest>(initialForm);
  const [mode, setMode] = useState<'paper' | 'live'>('paper');
  const [loading, setLoading] = useState(false);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketInfo, setMarketInfo] = useState<MarketInfo | null>(null);
  const [gridCount, setGridCount] = useState<number | null>(null);
  const [liveConfirmation, setLiveConfirmation] = useState('');
  const [notice, setNotice] = useState<Notice>(null);

  const estimatedCapital = useMemo(() => {
    if (!formData.lower_price || !formData.upper_price || !formData.grid_interval || !formData.order_amount) return null;
    const levels = Math.max(1, Math.ceil((formData.upper_price - formData.lower_price) / formData.grid_interval));
    return { levels, amount: ((formData.lower_price + formData.upper_price) / 2) * formData.order_amount * Math.min(levels, 10) };
  }, [formData]);

  const update = (name: keyof CreateGridRequest, value: string | number | boolean) => setFormData(current => ({ ...current, [name]: value }));
  const loadMarketInfo = async () => {
    setMarketLoading(true); setNotice(null);
    try {
      const info = await api.getMarketInfo(formData.symbol, formData.exchange);
      setMarketInfo(info);
      setFormData(current => ({ ...current, price_decimals: info.price_precision ?? current.price_decimals, quantity_precision: info.quantity_precision ?? current.quantity_precision }));
    } catch (caught) { setMarketInfo(null); setNotice({ type: 'error', text: caught instanceof Error ? caught.message : '無法讀取市場規則' }); }
    finally { setMarketLoading(false); }
  };
  const perGridNotional = formData.lower_price && formData.upper_price ? ((formData.lower_price + formData.upper_price) / 2) * formData.order_amount : null;
  const minNotionalError = marketInfo?.min_notional && perGridNotional !== null && perGridNotional < marketInfo.min_notional;
  const canAdvance = canAdvanceCreateStep(step, formData, mode, liveConfirmation, Boolean(minNotionalError));

  const submit = async () => {
    setLoading(true); setNotice(null);
    try {
      const precision = marketInfo?.price_precision;
      const normalizedInterval = precision === null || precision === undefined ? formData.grid_interval : Number(formData.grid_interval.toFixed(precision));
      await api.createGrid({ ...formData, grid_interval: normalizedInterval, account_mode: mode, metadata: { source: 'dashboard', reason: `${mode} strategy created from guided form` } });
      setNotice({ type: 'success', text: mode === 'paper' ? 'PAPER 策略已建立。啟動後只會在模擬盤撮合，不會打真實下單 API。' : 'LIVE 策略已建立。只有引擎 heartbeat 確認後才視為運行。' });
      setStep(3);
    } catch (caught) { setNotice({ type: 'error', text: caught instanceof Error ? caught.message : '網格創建失敗' }); }
    finally { setLoading(false); }
  };

  return <main className="grid-form-page">
    <header className="form-header"><div><p className="eyebrow">策略建立精靈</p><h2><Settings size={24} />建立網格策略</h2><p className="muted">PAPER 目前只是標籤，不能啟動。LIVE 需要 GRID_LIVE_TRADING_ENABLED，啟動即實盤下單。</p></div></header>
    <ol className="stepper" aria-label="建立策略步驟"><li className={step >= 1 ? 'active' : ''}>1. 市場</li><li className={step >= 2 ? 'active' : ''}>2. 網格</li><li className={step >= 3 ? 'active' : ''}>3. 風控確認</li></ol>
    {notice && <div className={`notice ${notice.type === 'success' ? 'notice-success' : 'notice-error'}`} role="alert">{notice.type === 'success' ? <CheckCircle size={18} /> : <AlertTriangle size={18} />}{notice.text}</div>}
    <section className="strategy-card form-card">
      {step === 1 && <div className="form-fields"><h3>市場與帳戶模式</h3><label>帳戶模式<div className="mode-toggle"><button className={mode === 'paper' ? 'active' : ''} onClick={() => setMode('paper')}>PAPER</button><button className={mode === 'live' ? 'active danger' : ''} onClick={() => setMode('live')}>LIVE</button></div></label><label>交易所<select value={formData.exchange} onChange={event => update('exchange', event.target.value)}><option value="binance">Binance</option><option value="lighter">Lighter</option><option value="hyperliquid">Hyperliquid</option></select></label><label>交易對<input value={formData.symbol} onChange={event => update('symbol', event.target.value.toUpperCase())} placeholder="BTC/USDT" /></label><label>網格方向<select value={formData.grid_type} onChange={event => update('grid_type', event.target.value)}><option value="neutral">中性網格</option><option value="long">做多網格</option><option value="short">做空網格</option></select></label><aside className="market-rules"><div><strong>交易對規則</strong>{marketInfo ? <small>價格精度 {marketInfo.price_precision ?? '—'} · 數量精度 {marketInfo.quantity_precision ?? '—'} · 最小名義額 {marketInfo.min_notional ?? '—'}</small> : <small>建立前建議讀取交易所最小下單與精度規則。</small>}</div><button className="btn btn-outline" type="button" disabled={marketLoading || !formData.symbol} onClick={() => void loadMarketInfo()}>{marketLoading ? '讀取中…' : '讀取規則'}</button></aside></div>}
      {step === 2 && <div className="form-fields"><h3>價格區間與下單</h3><label>下限價格<input type="number" value={formData.lower_price ?? ''} onChange={event => update('lower_price', Number(event.target.value))} /></label><label>上限價格<input type="number" value={formData.upper_price ?? ''} onChange={event => update('upper_price', Number(event.target.value))} /></label><label>每格間距<input type="number" min="0" step="any" value={formData.grid_interval} onChange={event => { setGridCount(null); update('grid_interval', Number(event.target.value)); }} /></label><label>預計格數（可選）<input type="number" min="1" value={gridCount ?? ''} onChange={event => { const count = Number(event.target.value); setGridCount(count || null); if (count > 0 && formData.lower_price && formData.upper_price) update('grid_interval', (formData.upper_price - formData.lower_price) / count); }} /></label><label>每格下單量<input type="number" min="0" step="any" value={formData.order_amount} onChange={event => update('order_amount', Number(event.target.value))} /></label>{estimatedCapital && <aside className="capital-estimate">預估格數：<strong>{estimatedCapital.levels}</strong> 前 10 格名義資金：約 <strong>{estimatedCapital.amount.toLocaleString('en-US', { maximumFractionDigits: 2 })}</strong><small>單格名義額：約 {perGridNotional?.toLocaleString('en-US', { maximumFractionDigits: 2 }) ?? '—'}；交易所最小下單額、精度與實際資金仍以後端驗證為準。</small>{minNotionalError && <small className="validation-error">每格名義額低於交易所最小要求 {marketInfo?.min_notional}。</small>}</aside>}</div>}
      {step === 3 && <div className="form-fields"><h3>風控與確認</h3><label>槓桿<input type="number" min="1" max="125" value={formData.leverage ?? 1} onChange={event => update('leverage', Number(event.target.value))} /></label><label>保證金模式<select value={formData.margin_mode ?? 'cross'} onChange={event => update('margin_mode', event.target.value)}><option value="cross">全倉 Cross</option><option value="isolated">逐倉 Isolated</option></select></label><label className="checkbox-label"><input type="checkbox" checked={formData.auto_start ?? false} onChange={event => update('auto_start', event.target.checked)} />建立後提交啟動意圖（PAPER 僅模擬撮合；LIVE 會下真實單）</label>{mode === 'live' && <label className="live-confirmation">輸入 <strong>CONFIRM LIVE</strong> 以確認建立 LIVE 策略<input value={liveConfirmation} onChange={event => setLiveConfirmation(event.target.value)} autoComplete="off" /></label>}<aside className="capital-estimate">{mode === 'live' ? <strong>LIVE 警告：啟動即實盤。本畫面不保證引擎已開始掛單。</strong> : <strong>PAPER：使用公開行情與記憶體訂單簿，不會呼叫私有下單 API。</strong>}<small>{formData.symbol} · {formData.exchange} · {formData.grid_type} · {formData.lower_price} – {formData.upper_price}</small></aside></div>}
      <footer className="form-actions">{step > 1 ? <button className="btn btn-outline" onClick={() => setStep(step - 1)}><ChevronLeft size={16} />上一步</button> : <span />}{step < 3 ? <button className="btn btn-primary" disabled={!canAdvance} onClick={() => setStep(step + 1)}>下一步<ChevronRight size={16} /></button> : <button className="btn btn-primary" disabled={loading || !canAdvance} onClick={() => void submit()}>{loading ? '建立中…' : '確認建立策略'}</button>}</footer>
    </section>
  </main>;
};

export default GridForm;
