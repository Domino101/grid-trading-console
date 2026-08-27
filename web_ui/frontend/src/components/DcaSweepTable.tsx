// Phase C7：參數掃描比較表（排序 / 最優列高亮 / truncated 誠實提示）。
//
// G-4 / 稽核 7.1：本檔不做任何金融計算。最優組合由**後端** `best` 欄位提供，
//   排序僅是顯示層的比較（`sortSweepRuns`），不重算任何指標。
// 稽核 7.4：truncated=true 時必須顯示「已完成 N/M 組」且文案含「已完成範圍內」。
// 稽核 7.7：null 指標顯示「—」，不得補 0。

import React, { useMemo, useState } from 'react';
import { AlertTriangle, Crown } from 'lucide-react';
import type { SweepResponse } from '../services/api';
import {
    backtestNumberText,
    backtestPercentText,
    backtestSignedPercentText,
    backtestTone,
    sortSweepRuns,
    sweepBestLabel,
    sweepParamsEqual,
    sweepParamsText,
    sweepTruncatedText,
} from '../utils/dca';

export interface DcaSweepTableProps {
    response: SweepResponse;
}

/** 可排序的指標欄（皆為 API 直供欄位）。 */
const SORTABLE_METRICS: readonly { key: string; label: string }[] = [
    { key: 'total_return_pct', label: '總報酬%' },
    { key: 'alpha_pct', label: 'Alpha%' },
    { key: 'profit_factor', label: '獲利因子' },
    { key: 'max_drawdown_pct', label: '最大回撤%' },
    { key: 'win_rate_pct', label: '勝率%' },
];

const DcaSweepTable: React.FC<DcaSweepTableProps> = ({ response }) => {
    const [sortBy, setSortBy] = useState<string>(response.best_by);

    const sorted = useMemo(
        () => sortSweepRuns(
            response.runs.map((r) => ({
                params: r.params,
                metrics: r.metrics as unknown as Record<string, number | null>,
                error: r.error,
                original: r,
            })),
            sortBy,
        ),
        [response.runs, sortBy],
    );

    const truncatedText = sweepTruncatedText(
        response.truncated,
        response.budget.runs_completed,
        response.budget.runs_total,
    );

    const bestParams = response.best?.params ?? null;

    return (
        <section className="glass-panel dca-sweep-table" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.25rem' }}>參數掃描比較</h3>
            <p className="muted" style={{ fontSize: '0.8rem' }}>
                共 {response.budget.runs_total} 組參數，已完成 {response.budget.runs_completed} 組
                （使用 {response.budget.bars_used} / {response.budget.bars_limit} 根棒預算）。
            </p>

            {/* 稽核 7.4：truncated 必須誠實且不得宣稱全域最佳。 */}
            {truncatedText && (
                <div className="notice notice-warning" role="alert" style={{ marginTop: '0.75rem' }}>
                    <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                    <span>{truncatedText}</span>
                </div>
            )}

            {/* 最優組合（後端判定） */}
            <div className="dca-sweep-best">
                <Crown size={15} />
                <span className="dca-sweep-best-label">{sweepBestLabel(response.truncated)}</span>
                <strong className="font-mono">{sweepParamsText(bestParams)}</strong>
                <span className="muted font-mono">
                    {response.best?.metric ?? response.best_by} = {backtestNumberText(response.best?.value)}
                </span>
            </div>

            <div className="dca-sweep-sort">
                <span className="muted">排序依據</span>
                {SORTABLE_METRICS.map((metric) => (
                    <button
                        key={metric.key}
                        type="button"
                        className={`dca-chart-tf ${sortBy === metric.key ? 'active' : ''}`}
                        aria-pressed={sortBy === metric.key}
                        onClick={() => setSortBy(metric.key)}
                    >
                        {metric.label}
                    </button>
                ))}
            </div>

            <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
                <table className="pro-table">
                    <thead>
                        <tr>
                            <th>參數組合</th>
                            <th>總報酬%</th>
                            <th>買入持有%</th>
                            <th>Alpha%</th>
                            <th>最大回撤%</th>
                            <th>勝率%</th>
                            <th>獲利因子</th>
                            <th>輪次</th>
                            <th>狀態</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((row, i) => {
                            const run = row.original;
                            const m = run.metrics;
                            const isBest = sweepParamsEqual(run.params, bestParams);
                            return (
                                <tr key={`${i}-${sweepParamsText(run.params)}`} className={isBest ? 'dca-sweep-row-best' : ''}>
                                    <td className="font-mono" style={{ fontSize: '0.74rem' }}>
                                        {isBest && <Crown size={12} style={{ marginRight: '0.3rem', verticalAlign: 'middle' }} />}
                                        {sweepParamsText(run.params)}
                                    </td>
                                    <td className={`font-mono ${backtestTone(m.total_return_pct) === 'positive' ? 'positive' : backtestTone(m.total_return_pct) === 'negative' ? 'negative' : ''}`}>
                                        {backtestSignedPercentText(m.total_return_pct)}
                                    </td>
                                    <td className="font-mono">{backtestSignedPercentText(m.buy_hold_return_pct)}</td>
                                    <td className={`font-mono ${backtestTone(m.alpha_pct) === 'positive' ? 'positive' : backtestTone(m.alpha_pct) === 'negative' ? 'negative' : ''}`}>
                                        {backtestSignedPercentText(m.alpha_pct)}
                                    </td>
                                    <td className="font-mono">{backtestPercentText(m.max_drawdown_pct)}</td>
                                    <td className="font-mono">{backtestPercentText(m.win_rate_pct)}</td>
                                    <td className="font-mono">{backtestNumberText(m.profit_factor)}</td>
                                    <td className="font-mono">{m.cycles_completed == null ? '—' : m.cycles_completed}</td>
                                    <td>
                                        {run.error
                                            ? <span className="badge badge-failed" title={run.error}>失敗</span>
                                            : <span className="badge badge-stopped">完成</span>}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <p className="muted" style={{ fontSize: '0.74rem', marginTop: '0.75rem' }}>
                所有指標由後端回測引擎計算後回傳；「—」代表該指標無定義（例如無虧損輪時的獲利因子），並非 0。
            </p>
        </section>
    );
};

export default DcaSweepTable;
