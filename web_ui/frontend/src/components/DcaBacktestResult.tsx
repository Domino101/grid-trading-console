// Phase C7：回測結果（指標卡 + 結果圖 + 輪次表 + warnings 全量揭露）。
//
// G-4 / 稽核 7.1：所有數值直讀 `result.metrics` 的欄位，本檔零計算。
// 稽核 7.2：`result.warnings` 必須**全部**渲染（測試斷言條目數 = API 回傳數）。
// 稽核 7.3：total_return_pct 與 buy_hold_return_pct 必須並排存在。
// 稽核 7.7：null 一律顯示「—」，不得補 0。
// 稽核 7.8：輪次表 > PAGE_SIZE 時分頁。

import React, { Suspense, lazy, useMemo, useState } from 'react';
import { AlertTriangle, TrendingUp } from 'lucide-react';
import type { BacktestResult, KlineCandle } from '../services/api';
import {
    backtestNumberText,
    backtestPercentText,
    backtestRangeText,
    backtestSignedPercentText,
    backtestTimeText,
    backtestTone,
    backtestUnavailableText,
    cycleExitReasonLabel,
} from '../utils/dca';
import { chartDataNotice } from '../utils/dcaChart';
import { money, number } from '../utils/format';

// 與 DcaDetail 同一個動態匯入模式，維持 E-6.2 隔離與獨立 chunk。
const DcaChart = lazy(() => import('./DcaChart'));

const CYCLE_PAGE_SIZE = 50;

export interface DcaBacktestResultProps {
    result: BacktestResult;
    /** 回測區間的 K 線；由父層以同一 timeframe/bars 取得。 */
    candles?: readonly KlineCandle[] | null;
    direction?: string | null;
}

interface MetricCardProps {
    label: string;
    value: string;
    tone?: 'positive' | 'negative' | 'neutral';
    hint?: string;
}

const MetricCard: React.FC<MetricCardProps> = ({ label, value, tone = 'neutral', hint }) => (
    <article className="kpi-card dca-metric-card" style={{ minHeight: 'auto', padding: '0.85rem' }}>
        <span className="text-secondary" style={{ fontSize: '0.72rem' }}>{label}</span>
        <strong
            className={`font-mono ${tone === 'positive' ? 'positive' : tone === 'negative' ? 'negative' : ''}`}
            style={{ fontSize: '1.15rem' }}
        >
            {value}
        </strong>
        {hint && <small className="font-mono text-muted">{hint}</small>}
    </article>
);

const DcaBacktestResult: React.FC<DcaBacktestResultProps> = ({ result, candles, direction }) => {
    const [cyclePage, setCyclePage] = useState(0);
    const m = result.metrics;

    const unavailableText = backtestUnavailableText(result.data_status);
    const notice = useMemo(
        () => chartDataNotice(result.data_status, result.source, candles?.length ?? 0),
        [result.data_status, result.source, candles],
    );

    const pagedCycles = useMemo(
        () => result.cycles.slice(cyclePage * CYCLE_PAGE_SIZE, cyclePage * CYCLE_PAGE_SIZE + CYCLE_PAGE_SIZE),
        [result.cycles, cyclePage],
    );
    const totalCyclePages = Math.max(1, Math.ceil(result.cycles.length / CYCLE_PAGE_SIZE));

    return (
        <div className="dca-backtest-result">
            {/* 稽核 7.6：資料不可信時明確警示，且下方不渲染結果圖。 */}
            {unavailableText && (
                <div className="notice notice-error" role="alert">
                    <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                    <span>{unavailableText}</span>
                </div>
            )}

            {/* 稽核 7.3：DCA 報酬與買入持有並排，alpha 為負以警示色呈現。 */}
            <section className="kpi-grid dca-metric-grid" aria-label="回測績效指標">
                <MetricCard
                    label="DCA 總報酬"
                    value={backtestSignedPercentText(m.total_return_pct)}
                    tone={backtestTone(m.total_return_pct)}
                    hint={`期末 ${money(m.final_equity)} / 初始 ${money(m.initial_capital)}`}
                />
                <MetricCard
                    label="買入持有報酬"
                    value={backtestSignedPercentText(m.buy_hold_return_pct)}
                    tone={backtestTone(m.buy_hold_return_pct)}
                    hint="同期間單純持有的對照基準"
                />
                <MetricCard
                    label="超額報酬 Alpha"
                    value={backtestSignedPercentText(m.alpha_pct)}
                    tone={backtestTone(m.alpha_pct)}
                    hint={
                        typeof m.alpha_pct === 'number' && m.alpha_pct < 0
                            ? '策略落後買入持有'
                            : '策略相對買入持有的超額'
                    }
                />
                <MetricCard
                    label="最大回撤"
                    value={backtestPercentText(m.max_drawdown_pct)}
                    tone={typeof m.max_drawdown_pct === 'number' && m.max_drawdown_pct > 0 ? 'negative' : 'neutral'}
                    hint="峰值到谷底的最大跌幅"
                />
                <MetricCard
                    label="已實現損益 / 手續費"
                    value={money(m.realized_pnl)}
                    tone={backtestTone(m.realized_pnl)}
                    hint={`手續費 ${money(m.total_fees)}`}
                />
                <MetricCard
                    label="完成輪次 / 勝率"
                    value={m.cycles_completed == null ? '—' : String(m.cycles_completed)}
                    hint={`勝率 ${backtestPercentText(m.win_rate_pct)}`}
                />
                <MetricCard
                    label="獲利因子"
                    value={backtestNumberText(m.profit_factor)}
                    hint="無虧損輪時為 —（非 0）"
                />
                <MetricCard
                    label="持倉時間占比"
                    value={backtestPercentText(m.exposure_pct)}
                    hint={`平均每輪 ${backtestNumberText(m.avg_cycle_bars, 1)} 根`}
                />
            </section>

            <p className="muted dca-backtest-meta">
                {result.bars} 根 {result.timeframe} K 棒 · {backtestRangeText(result.first_ts, result.last_ts)}
                {notice.sourceBadge && <span className="badge badge-paper" style={{ marginLeft: '0.5rem' }}>{notice.sourceBadge}</span>}
            </p>

            {/* 結果圖：複用 C6 的 DcaChart（同一元件，標記語意一致）。 */}
            {!unavailableText && !notice.unavailable && (
                <section className="glass-panel" style={{ padding: '1rem', marginTop: '0.85rem' }}>
                    <h4 className="dca-backtest-subhead"><TrendingUp size={15} /> 回測交易標記與權益曲線</h4>
                    <Suspense fallback={<p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>載入圖表中…</p>}>
                        <DcaChart
                            candles={candles ?? result.candles}
                            marks={result.marks}
                            equity={result.equity}
                            direction={direction}
                            height={400}
                            ariaLabel={`回測結果 K 線與交易標記，共 ${result.marks.length} 個標記`}
                        />
                    </Suspense>
                </section>
            )}

            {/* 稽核 7.2：warnings 全量渲染，一條都不能少。 */}
            {result.warnings.length > 0 && (
                <div className="notice notice-warning dca-backtest-warnings" role="note">
                    <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                    <div>
                        <strong>回測簡化事項</strong>
                        <ul>
                            {result.warnings.map((w, i) => (
                                <li key={`${i}-${w}`}>{w}</li>
                            ))}
                        </ul>
                    </div>
                </div>
            )}

            {/* 輪次表（稽核 7.8 分頁） */}
            <section className="glass-panel" style={{ padding: '1rem', marginTop: '0.85rem' }}>
                <h4 className="dca-backtest-subhead">輪次明細（{result.cycles.length}）</h4>
                {result.cycles.length === 0 ? (
                    <p className="muted" style={{ padding: '1.5rem 0', textAlign: 'center' }}>
                        此期間沒有完成任何輪次。
                    </p>
                ) : (
                    <>
                        <div className="table-wrapper">
                            <table className="pro-table">
                                <thead>
                                    <tr>
                                        <th>#</th>
                                        <th>方向</th>
                                        <th>進場時間</th>
                                        <th>均價</th>
                                        <th>出場價</th>
                                        <th>安全單</th>
                                        <th>報酬%</th>
                                        <th>損益</th>
                                        <th>棒數</th>
                                        <th>出場原因</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {pagedCycles.map((c) => (
                                        <tr key={c.index}>
                                            <td className="font-mono">{c.index}</td>
                                            <td>{c.direction === 'short' ? '空' : '多'}</td>
                                            <td className="font-mono" style={{ fontSize: '0.74rem' }}>{backtestTimeText(c.entry_ts)}</td>
                                            <td className="font-mono">{number(c.average_cost)}</td>
                                            <td className="font-mono">{number(c.exit_price)}</td>
                                            <td className="font-mono">{c.safety_filled}</td>
                                            <td className={`font-mono ${backtestTone(c.realized_pct) === 'positive' ? 'positive' : backtestTone(c.realized_pct) === 'negative' ? 'negative' : ''}`}>
                                                {backtestSignedPercentText(c.realized_pct)}
                                            </td>
                                            <td className="font-mono">{money(c.realized_pnl)}</td>
                                            <td className="font-mono">{c.bars}</td>
                                            <td>{cycleExitReasonLabel(c.exit_reason)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {totalCyclePages > 1 && (
                            <div className="dca-pagination">
                                <button
                                    className="btn btn-outline"
                                    type="button"
                                    disabled={cyclePage === 0}
                                    onClick={() => setCyclePage((p) => Math.max(0, p - 1))}
                                >
                                    上一頁
                                </button>
                                <span className="muted font-mono">{cyclePage + 1} / {totalCyclePages}</span>
                                <button
                                    className="btn btn-outline"
                                    type="button"
                                    disabled={cyclePage >= totalCyclePages - 1}
                                    onClick={() => setCyclePage((p) => Math.min(totalCyclePages - 1, p + 1))}
                                >
                                    下一頁
                                </button>
                            </div>
                        )}
                    </>
                )}
            </section>
        </div>
    );
};

export default DcaBacktestResult;
