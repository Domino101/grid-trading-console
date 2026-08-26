import React from 'react';
import { Activity, CheckCircle, XCircle } from 'lucide-react';
import type { MarketIndicatorsResponse } from '../services/api';
import { number } from '../utils/format';
import {
    indicatorChipText,
    formatLookbackTime,
    type DcaDirectionValue,
    type DcaEntryWizard,
    type DcaIndicatorParams,
} from '../utils/dca';

interface IndicatorConfigPanelProps {
    direction: DcaDirectionValue;
    entry: DcaEntryWizard;
    onChange: (entry: DcaEntryWizard) => void;
    live: MarketIndicatorsResponse | null;
    liveLoading: boolean;
}

const TIMEFRAME_OPTIONS = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d'];

// 單一參數輸入欄位（全部可編輯 — 稽核 6.8b）。
const ParamInput: React.FC<{
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    suffix?: string;
    onChange: (value: number) => void;
}> = ({ label, value, min, max, step, suffix, onChange }) => (
    <label className="input-label dca-param-input">
        <span>{label}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input
                type="number"
                value={Number.isFinite(value) ? value : ''}
                min={min}
                max={max}
                step={step}
                onChange={(e) => onChange(e.target.value === '' ? Number.NaN : Number(e.target.value))}
            />
            {suffix ? <span className="muted" style={{ fontSize: '0.72rem' }}>{suffix}</span> : null}
        </span>
    </label>
);

const liveValueText = (
    result: { value: number | null; insufficient_data: boolean; warmup_have: number | null; warmup_need: number | null } | null,
    triggered: boolean | null,
    conditionHint: string,
): string => {
    if (!result || result.insufficient_data) {
        if (result && (result.warmup_have != null || result.warmup_need != null)) {
            return `資料不足（${result.warmup_have ?? 0}/${result.warmup_need ?? 0} 根）`;
        }
        return '資料不足';
    }
    const value = result.value == null ? '—' : number(result.value);
    const mark = triggered ? '成立 ✓' : '不成立 ✗';
    return `${value}（${conditionHint} ${mark}）`;
};

// 依方向取觸發旗標（端點固定以 long 語意回傳 triggered_long；short 方向取 triggered_short）。
const directionTriggered = (
    result: { triggered_long: boolean | null; triggered_short: boolean | null } | null,
    direction: DcaDirectionValue,
): boolean | null => {
    if (!result) return null;
    return direction === 'short' ? result.triggered_short : result.triggered_long;
};

// 四種指標的啟用開關＋參數編輯＋共用管線設定＋即時值（稽核 6.7/6.8/6.8b/6.9/6.8c）。
const IndicatorConfigPanel: React.FC<IndicatorConfigPanelProps> = ({ direction, entry, onChange, live, liveLoading }) => {
    const setShared = (patch: Partial<Omit<DcaEntryWizard, 'indicators'>>) => onChange({ ...entry, ...patch });
    const setIndicator = <K extends keyof DcaIndicatorParams>(key: K, patch: Partial<DcaIndicatorParams[K]>) =>
        onChange({ ...entry, indicators: { ...entry.indicators, [key]: { ...entry.indicators[key], ...patch } } });

    const pc = entry.indicators.price_change;
    const lookbackHint = formatLookbackTime(pc.lookback_bars, entry.timeframe);

    return (
        <div className="dca-indicator-panel">
            <div className="dca-indicator-shared">
                <h4 className="dca-indicator-section-title">
                    <Activity size={14} />
                    共用管線（§4.3.0，所有指標共用）
                </h4>
                <div className="dca-shared-grid">
                    <label className="input-label dca-param-input">
                        <span>K 線時框</span>
                        <select value={entry.timeframe} onChange={(e) => setShared({ timeframe: e.target.value })}>
                            {TIMEFRAME_OPTIONS.map((tf) => (
                                <option key={tf} value={tf}>{tf}</option>
                            ))}
                        </select>
                    </label>
                    <ParamInput label="取樣根數 fetch_limit" value={entry.fetch_limit} min={100} max={500} step={10} onChange={(v) => setShared({ fetch_limit: v })} />
                    <label className="input-label dca-param-input">
                        <span>價格來源 price_source</span>
                        <select value={entry.price_source} onChange={(e) => setShared({ price_source: e.target.value as DcaEntryWizard['price_source'] })}>
                            <option value="close">close</option>
                            <option value="hlc3">hlc3</option>
                            <option value="ohlc4">ohlc4</option>
                        </select>
                    </label>
                    <label className="input-label dca-param-input">
                        <span>多指標邏輯 logic</span>
                        <select value={entry.logic} onChange={(e) => setShared({ logic: e.target.value as DcaEntryWizard['logic'] })}>
                            <option value="all">全部成立 (AND)</option>
                            <option value="any">任一成立 (OR)</option>
                        </select>
                    </label>
                    <label className="input-label dca-param-input">
                        <span>確認 K 棒 confirm_bars</span>
                        <select value={entry.confirm_bars} onChange={(e) => setShared({ confirm_bars: Number(e.target.value) })}>
                            {[1, 2, 3, 4, 5].map((n) => (
                                <option key={n} value={n}>{n}</option>
                            ))}
                        </select>
                    </label>
                </div>
                <p className="muted dca-indicator-note">
                    即時值以 §4.3 預設參數計算（端點固定值），僅供判斷市場狀態；實際觸發以您的參數為準。
                </p>
            </div>

            <div className="dca-indicator-list">
                {/* RSI */}
                <div className={`dca-indicator-row ${entry.indicators.rsi.enabled ? 'enabled' : ''}`}>
                    <label className="dca-indicator-toggle">
                        <input
                            type="checkbox"
                            checked={entry.indicators.rsi.enabled}
                            onChange={(e) => setIndicator('rsi', { enabled: e.target.checked })}
                        />
                        <strong>RSI 相對強弱</strong>
                        <span className="muted" style={{ fontSize: '0.74rem' }}>
                            {indicatorChipText({ type: 'rsi', ...entry.indicators.rsi })}
                        </span>
                    </label>
                    {entry.indicators.rsi.enabled && (
                        <div className="dca-indicator-params">
                            <ParamInput label="period" value={entry.indicators.rsi.period} min={2} max={100} step={1} onChange={(v) => setIndicator('rsi', { period: v })} />
                            <ParamInput label="oversold（多）" value={entry.indicators.rsi.oversold} min={1} max={50} step={1} onChange={(v) => setIndicator('rsi', { oversold: v })} />
                            <ParamInput label="overbought（空）" value={entry.indicators.rsi.overbought} min={50} max={99} step={1} onChange={(v) => setIndicator('rsi', { overbought: v })} />
                            <label className="input-label dca-param-input">
                                <span>condition</span>
                                <select value={entry.indicators.rsi.condition} onChange={(e) => setIndicator('rsi', { condition: e.target.value as 'level' | 'cross_in' | 'cross_out' })}>
                                    <option value="level">level（區間判定）</option>
                                    <option value="cross_in">cross_in（穿越進入）</option>
                                    <option value="cross_out">cross_out（穿回）</option>
                                </select>
                            </label>
                            <div className="dca-indicator-live">
                                {liveLoading ? '載入即時值…' : (
                                    <CheckCircle size={12} className="dca-live-icon" />
                                )}
                                {liveValueText(live?.rsi ?? null, directionTriggered(live?.rsi ?? null, direction), `RSI ≤${entry.indicators.rsi.oversold} / ≥${entry.indicators.rsi.overbought}`)}
                            </div>
                        </div>
                    )}
                </div>

                {/* MACD */}
                <div className={`dca-indicator-row ${entry.indicators.macd.enabled ? 'enabled' : ''}`}>
                    <label className="dca-indicator-toggle">
                        <input
                            type="checkbox"
                            checked={entry.indicators.macd.enabled}
                            onChange={(e) => setIndicator('macd', { enabled: e.target.checked })}
                        />
                        <strong>MACD 移動平均匯聚背離</strong>
                        <span className="muted" style={{ fontSize: '0.74rem' }}>
                            {indicatorChipText({ type: 'macd', ...entry.indicators.macd })}
                        </span>
                    </label>
                    {entry.indicators.macd.enabled && (
                        <div className="dca-indicator-params">
                            <ParamInput label="fast" value={entry.indicators.macd.fast} min={2} max={100} step={1} onChange={(v) => setIndicator('macd', { fast: v })} />
                            <ParamInput label="slow（須 > fast）" value={entry.indicators.macd.slow} min={3} max={200} step={1} onChange={(v) => setIndicator('macd', { slow: v })} />
                            <ParamInput label="signal" value={entry.indicators.macd.signal} min={2} max={50} step={1} onChange={(v) => setIndicator('macd', { signal: v })} />
                            <label className="input-label dca-param-input">
                                <span>condition</span>
                                <select value={entry.indicators.macd.condition} onChange={(e) => setIndicator('macd', { condition: e.target.value as 'cross' | 'hist_sign' | 'zero_cross' })}>
                                    <option value="cross">cross（金/死叉）</option>
                                    <option value="hist_sign">hist_sign（柱體符號）</option>
                                    <option value="zero_cross">zero_cross（零軸穿越）</option>
                                </select>
                            </label>
                            <div className="dca-indicator-live">
                                {liveLoading ? '載入即時值…' : (
                                    <CheckCircle size={12} className="dca-live-icon" />
                                )}
                                {liveValueText(live?.macd ?? null, directionTriggered(live?.macd ?? null, direction), 'MACD 柱體交叉')}
                            </div>
                        </div>
                    )}
                </div>

                {/* Bollinger */}
                <div className={`dca-indicator-row ${entry.indicators.bollinger.enabled ? 'enabled' : ''}`}>
                    <label className="dca-indicator-toggle">
                        <input
                            type="checkbox"
                            checked={entry.indicators.bollinger.enabled}
                            onChange={(e) => setIndicator('bollinger', { enabled: e.target.checked })}
                        />
                        <strong>Bollinger 布林通道</strong>
                        <span className="muted" style={{ fontSize: '0.74rem' }}>
                            {indicatorChipText({ type: 'bollinger', ...entry.indicators.bollinger })}
                        </span>
                    </label>
                    {entry.indicators.bollinger.enabled && (
                        <div className="dca-indicator-params">
                            <ParamInput label="period" value={entry.indicators.bollinger.period} min={2} max={200} step={1} onChange={(v) => setIndicator('bollinger', { period: v })} />
                            <ParamInput label="std_dev" value={entry.indicators.bollinger.std_dev} min={0.5} max={5} step={0.1} onChange={(v) => setIndicator('bollinger', { std_dev: v })} />
                            <label className="input-label dca-param-input">
                                <span>condition</span>
                                <select value={entry.indicators.bollinger.condition} onChange={(e) => setIndicator('bollinger', { condition: e.target.value as 'touch' | 'break' | 'return' })}>
                                    <option value="touch">touch（觸碰邊界）</option>
                                    <option value="break">break（突破當下）</option>
                                    <option value="return">return（收回）</option>
                                </select>
                            </label>
                            <div className="dca-indicator-live">
                                {liveLoading ? '載入即時值…' : (
                                    <CheckCircle size={12} className="dca-live-icon" />
                                )}
                                {liveValueText(live?.bollinger ?? null, directionTriggered(live?.bollinger ?? null, direction), `close 觸碰 ${entry.indicators.bollinger.std_dev}σ 邊界`)}
                            </div>
                        </div>
                    )}
                </div>

                {/* Price Change */}
                <div className={`dca-indicator-row ${entry.indicators.price_change.enabled ? 'enabled' : ''}`}>
                    <label className="dca-indicator-toggle">
                        <input
                            type="checkbox"
                            checked={entry.indicators.price_change.enabled}
                            onChange={(e) => setIndicator('price_change', { enabled: e.target.checked })}
                        />
                        <strong>Price Change 區間漲跌幅</strong>
                        <span className="muted" style={{ fontSize: '0.74rem' }}>
                            {indicatorChipText({ type: 'price_change', ...entry.indicators.price_change })}
                        </span>
                    </label>
                    {entry.indicators.price_change.enabled && (
                        <div className="dca-indicator-params">
                            <ParamInput
                                label="lookback_bars"
                                value={entry.indicators.price_change.lookback_bars}
                                min={1}
                                max={500}
                                step={1}
                                onChange={(v) => setIndicator('price_change', { lookback_bars: v })}
                            />
                            {lookbackHint && (
                                <span className="dca-lookback-hint" aria-live="polite">{lookbackHint}</span>
                            )}
                            <ParamInput label="多門檻 %（跌幅達標）" value={entry.indicators.price_change.long_threshold_percent} min={-100} max={100} step={0.5} suffix="%" onChange={(v) => setIndicator('price_change', { long_threshold_percent: v })} />
                            <ParamInput label="空門檻 %（漲幅達標）" value={entry.indicators.price_change.short_threshold_percent} min={-100} max={100} step={0.5} suffix="%" onChange={(v) => setIndicator('price_change', { short_threshold_percent: v })} />
                            <div className="dca-indicator-live">
                                {liveLoading ? '載入即時值…' : (
                                    <CheckCircle size={12} className="dca-live-icon" />
                                )}
                                {liveValueText(live?.price_change ?? null, directionTriggered(live?.price_change ?? null, direction),
                                    direction === 'short'
                                        ? `change% ≥${entry.indicators.price_change.short_threshold_percent}%`
                                        : `change% ≤${entry.indicators.price_change.long_threshold_percent}%`)}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {!live && !liveLoading && (
                <div className="notice notice-warning dca-indicator-nodata" role="status">
                    <XCircle size={14} />
                    <span>尚未取得指標即時值（交易對未確認或行情端點不可用）。</span>
                </div>
            )}
        </div>
    );
};

export default IndicatorConfigPanel;
