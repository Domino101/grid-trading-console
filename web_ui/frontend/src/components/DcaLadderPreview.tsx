import React from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import type { DcaPreviewLadder, DcaPreviewResponse } from '../services/api';
import { money, number } from '../utils/format';
import { dcaFundsStatus, type DcaDirectionValue } from '../utils/dca';

interface DcaLadderPreviewProps {
    response: DcaPreviewResponse | null;
    loading: boolean;
    error: string | null;
    direction: DcaDirectionValue;
    // 目前版本後端無 DCA 專屬餘額端點 → 傳 null（顯示「—」，不做假設 — 稽核 6.15 例外）。
    availableBalance: number | null;
    accountMode: 'paper' | 'live';
}

// 單一方向階梯摘要（KPI + 完整階梯表）。
// 重要（稽核 6.2）：此元件只做欄位選取與格式化，所有數值直接取自
// POST /api/v1/dca/preview 的回應欄位，禁止任何階梯數學。
const LadderSection: React.FC<{ title: string; ladder: DcaPreviewLadder; requiredCapital: number }> = ({
    title,
    ladder,
    requiredCapital,
}) => {
    const lastLevel = ladder.levels.length > 0 ? ladder.levels[ladder.levels.length - 1] : null;
    const maxSafetyDeviationPercent = lastLevel ? lastLevel.deviation_percent : null;
    return (
        <section className="glass-panel dca-ladder-section" aria-label={`${title} 階梯`}>
            <h4 className="dca-ladder-title">{title}</h4>
            <div className="dca-summary-kpis">
                <div className="dca-summary-kpi">
                    <span>入場價 E</span>
                    <strong>{money(ladder.entry_price)}</strong>
                </div>
                <div className="dca-summary-kpi dca-summary-kpi-required">
                    <span>所需資金 Required</span>
                    <strong>{money(requiredCapital)} USD</strong>
                </div>
                <div className="dca-summary-kpi">
                    <span>最大覆蓋跌幅</span>
                    <strong>{ladder.max_drawdown_percent == null ? '—' : `${number(ladder.max_drawdown_percent, 2)}%`}</strong>
                </div>
                <div className="dca-summary-kpi">
                    <span>最大安全單偏移</span>
                    <strong>{maxSafetyDeviationPercent == null ? '—' : `${number(maxSafetyDeviationPercent, 2)}%`}</strong>
                </div>
                <div className="dca-summary-kpi">
                    <span>最終均價</span>
                    <strong>{money(ladder.final_average_price)}</strong>
                </div>
                <div className="dca-summary-kpi">
                    <span>最終止盈價</span>
                    <strong>{money(ladder.final_take_profit_price)}</strong>
                </div>
            </div>
            <div className="dca-ladder-table-wrap">
                <table className="dca-ladder-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>類型</th>
                            <th>偏移</th>
                            <th>價格</th>
                            <th>名目 (USD)</th>
                            <th>數量</th>
                            <th>累計均價</th>
                            <th>止盈價</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ladder.levels.map((level) => (
                            <tr key={level.index}>
                                <td>{level.index}</td>
                                <td>{level.index === 0 ? '基礎單' : `安全單 ${level.index}`}</td>
                                <td>{level.deviation_percent == null ? '—' : `${number(level.deviation_percent, 2)}%`}</td>
                                <td>{money(level.price)}</td>
                                <td>{money(level.notional)}</td>
                                <td>{number(level.quantity)}</td>
                                <td>{money(level.avg_price)}</td>
                                <td>{level.take_profit_price == null ? '—' : money(level.take_profit_price)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </section>
    );
};

// Summary 面板（精靈第 4 步右側）：由 /preview 驅動，不自算（稽核 6.2/6.3/6.5）。
const DcaLadderPreview: React.FC<DcaLadderPreviewProps> = ({
    response,
    loading,
    error,
    direction,
    availableBalance,
    accountMode,
}) => {
    if (loading && !response) {
        return (
            <aside className="glass-panel dca-ladder-preview" aria-label="Summary 階梯試算">
                <p className="muted dca-ladder-placeholder">正在試算階梯（debounce 300ms）…</p>
            </aside>
        );
    }

    if (error) {
        return (
            <aside className="glass-panel dca-ladder-preview" aria-label="Summary 階梯試算">
                <div className="notice notice-error" role="alert">
                    <AlertTriangle size={15} />
                    <span>試算失敗：{error}</span>
                </div>
            </aside>
        );
    }

    if (!response) {
        return (
            <aside className="glass-panel dca-ladder-preview" aria-label="Summary 階梯試算">
                <p className="muted dca-ladder-placeholder">
                    尚無試算結果：請確認已取得目前價格（交易對有效），並完成前三步必填欄位。
                </p>
            </aside>
        );
    }

    const funds = dcaFundsStatus(response.required_capital, availableBalance);
    const both = direction === 'both';

    return (
        <aside className="glass-panel dca-ladder-preview" aria-label="Summary 階梯試算">
            <div className="dca-ladder-preview-head">
                <h3 className="dca-ladder-preview-title">Summary · 階梯試算</h3>
                <span className="muted" style={{ fontSize: '0.72rem' }}>
                    數值來源：POST /api/v1/dca/preview（前端零階梯運算）
                </span>
            </div>

            {both && (
                <div className="notice notice-info dca-both-note" role="note">
                    <Info size={14} />
                    <span>
                        雙向（both）：同一時刻<span className="dca-strong">只會執行其中一個方向</span>；
                        所需資金 = max(多, 空)，<span className="dca-strong">不是兩組相加</span>。
                    </span>
                </div>
            )}

            {both ? (
                <div className="dca-ladder-stack">
                    {response.ladder_long && (
                        <LadderSection title="LONG 階梯" ladder={response.ladder_long} requiredCapital={response.required_capital} />
                    )}
                    {response.ladder_short && (
                        <LadderSection title="SHORT 階梯" ladder={response.ladder_short} requiredCapital={response.required_capital} />
                    )}
                </div>
            ) : (
                response.ladder && (
                    <LadderSection
                        title={direction === 'short' ? 'SHORT 階梯' : 'LONG 階梯'}
                        ladder={response.ladder}
                        requiredCapital={response.required_capital}
                    />
                )
            )}

            <div className="dca-funds-row">
                <span>可用餘額：{availableBalance == null ? '—（本版本無 DCA 專屬餘額端點）' : `${money(availableBalance)} USD`}</span>
                {funds === 'insufficient' && (
                    <span
                        className={accountMode === 'live' ? 'dca-funds-warning' : 'dca-funds-caution'}
                        role={accountMode === 'live' ? 'alert' : 'status'}
                    >
                        <AlertTriangle size={14} />
                        所需資金 {money(response.required_capital)} USD 超過可用餘額
                        {accountMode === 'live' ? '（LIVE 已禁止送出）' : '（PAPER 僅警告）'}
                    </span>
                )}
            </div>
        </aside>
    );
};

export default DcaLadderPreview;
