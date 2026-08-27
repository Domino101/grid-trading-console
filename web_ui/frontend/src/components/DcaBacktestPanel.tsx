// Phase C7：回測設定面板（期間 / 時框 / 初始資金 / 執行 + 簡化事項揭露）。
//
// G-4：本元件只蒐集參數並呼叫 API，不做任何金融計算。
// 稽核 7.5：執行中按鈕禁用 + spinner；父層負責 AbortController。
// 稽核 7.2：簡化事項在「執行前」就常駐揭露，不是等有結果才說。

import React from 'react';
import { Loader2, Play, XCircle, Info } from 'lucide-react';
import {
    DCA_BACKTEST_BARS_OPTIONS,
    DCA_BACKTEST_MAX_BARS,
    DCA_BACKTEST_TIMEFRAMES,
} from '../utils/dca';

export interface DcaBacktestPanelProps {
    timeframe: string;
    bars: number;
    initialCapital: string;
    running: boolean;
    /** 執行前即揭露的固定簡化事項（D-11）；有結果後改用 API 回傳的 warnings。 */
    disclosures: readonly string[];
    onTimeframeChange: (timeframe: string) => void;
    onBarsChange: (bars: number) => void;
    onInitialCapitalChange: (value: string) => void;
    onRun: () => void;
    onCancel: () => void;
    /** 精靈情境下的說明文字（稽核 7.9：明示送出的是表單當前參數）。 */
    scopeNote?: string;
}

const DcaBacktestPanel: React.FC<DcaBacktestPanelProps> = ({
    timeframe,
    bars,
    initialCapital,
    running,
    disclosures,
    onTimeframeChange,
    onBarsChange,
    onInitialCapitalChange,
    onRun,
    onCancel,
    scopeNote,
}) => {
    const capitalNumber = Number(initialCapital);
    const capitalInvalid =
        initialCapital.trim() === '' || !Number.isFinite(capitalNumber) || capitalNumber <= 0;
    const barsInvalid = !Number.isFinite(bars) || bars <= 0 || bars > DCA_BACKTEST_MAX_BARS;

    return (
        <section className="glass-panel dca-backtest-panel" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.25rem' }}>回測設定</h3>
            <p className="muted" style={{ fontSize: '0.8rem' }}>
                {scopeNote ?? '以歷史 K 線回放此機器人的設定；回測為唯讀操作，不會下任何真實訂單。'}
            </p>

            <div className="dca-backtest-controls">
                <label className="dca-backtest-field">
                    <span>時框</span>
                    <select
                        value={timeframe}
                        disabled={running}
                        onChange={(e) => onTimeframeChange(e.target.value)}
                    >
                        {DCA_BACKTEST_TIMEFRAMES.map((tf) => (
                            <option key={tf} value={tf}>{tf}</option>
                        ))}
                    </select>
                </label>

                <label className="dca-backtest-field">
                    <span>回測長度</span>
                    <select
                        value={String(bars)}
                        disabled={running}
                        onChange={(e) => onBarsChange(Number(e.target.value))}
                    >
                        {DCA_BACKTEST_BARS_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                    </select>
                </label>

                <label className="dca-backtest-field">
                    <span>初始資金 (USD)</span>
                    <input
                        type="number"
                        min="1"
                        step="100"
                        value={initialCapital}
                        disabled={running}
                        aria-invalid={capitalInvalid}
                        onChange={(e) => onInitialCapitalChange(e.target.value)}
                    />
                </label>

                <div className="dca-backtest-actions">
                    <button
                        className="btn btn-primary"
                        type="button"
                        disabled={running || capitalInvalid || barsInvalid}
                        onClick={onRun}
                        title={capitalInvalid ? '請輸入大於 0 的初始資金' : undefined}
                    >
                        {running ? (
                            <><Loader2 size={15} className="spin" /> 回測中…</>
                        ) : (
                            <><Play size={15} /> 執行回測</>
                        )}
                    </button>
                    {running && (
                        <button className="btn btn-outline" type="button" onClick={onCancel}>
                            <XCircle size={15} /> 取消
                        </button>
                    )}
                </div>
            </div>

            {/* 稽核 7.2：簡化事項常駐揭露，執行前就看得到。 */}
            <div className="dca-backtest-disclosure" role="note">
                <div className="dca-backtest-disclosure-head">
                    <Info size={14} />
                    <strong>回測簡化事項（務必閱讀）</strong>
                </div>
                <ul>
                    {disclosures.map((text, i) => (
                        <li key={`${i}-${text}`}>{text}</li>
                    ))}
                </ul>
            </div>
        </section>
    );
};

export default DcaBacktestPanel;
