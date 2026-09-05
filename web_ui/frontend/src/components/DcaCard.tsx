import React from 'react';
import {
    Play,
    RefreshCcw,
    Square,
    Trash2,
    ChevronRight,
    Radar,
    AlertTriangle,
} from 'lucide-react';
import { freshnessText, money, number } from '../utils/format';
import {
    armedWaitText,
    dcaEntryText,
    dcaLadderProgress,
    dcaPositionNotional,
    dcaRoiPercent,
    directionLabel,
    distanceText,
    indicatorLiveInfo,
    isDcaArmed,
    isDcaRunningStatus,
    nextSafetyDistance,
    takeProfitDistance,
} from '../utils/dca';
import type { DcaSnapshotItem } from '../services/api';
import DcaStatusBadge from './DcaStatusBadge';

interface DcaCardProps {
    bot: DcaSnapshotItem;
    busy: boolean;
    now: number;
    onOpen: () => void;
    onStart: () => void;
    onStop: () => void;
    onDelete: () => void;
}

const DcaCard: React.FC<DcaCardProps> = ({ bot, busy, now, onOpen, onStart, onStop, onDelete }) => {
    const armed = isDcaArmed(bot.status);
    const running = isDcaRunningStatus(bot.status);
    const isStopping = bot.status === 'stopping';
    const isStarting = bot.status === 'starting';
    const progress = dcaLadderProgress(bot);
    const unrealized = bot.profit?.unrealized ?? null;
    const realized = bot.profit?.realized ?? 0;
    const fees = bot.profit?.fees ?? 0;
    const netRealized = realized - fees;
    const netPnl = (bot.profit?.realized != null || bot.profit?.unrealized != null)
        ? (realized + (unrealized ?? 0) - fees)
        : null;
    const isPositivePnl = netPnl != null && netPnl >= 0;
    const roi = dcaRoiPercent(bot);
    const notional = dcaPositionNotional(bot);
    const waitText = armed ? armedWaitText(bot.armed_since, now) : null;
    const hasPosition = (bot.position?.quantity ?? 0) > 0;

    const dotClass =
        bot.data_status === 'live' && running ? 'is-live' : running ? 'is-warning' : '';

    return (
        <article className="strategy-card dca-card" aria-label={`DCA 機器人 ${bot.symbol ?? bot.bot_id}`}>
            {/* 標頭：symbol / exchange / 方向 / LIVE-PAPER / 狀態 / 新鮮度 */}
            <header className="strategy-card-header">
                <div className="strategy-symbol-group">
                    <div className="strategy-symbol">
                        <span className={`status-dot ${dotClass}`} />
                        <span>{bot.symbol ?? '未命名 DCA'}</span>
                    </div>
                    <div className="strategy-meta">
                        <span className="exchange-pill">{bot.exchange ?? '未知交易所'}</span>
                        <span>·</span>
                        <span title="方向（雙向 = 同一時刻單一方向互斥）">{directionLabel(bot.direction)}</span>
                        {bot.active_direction && (
                            <span title="本輪鎖定方向" style={{ marginLeft: '0.35rem' }}>
                                （本輪 {directionLabel(bot.active_direction)}）
                            </span>
                        )}
                    </div>
                </div>

                <div className="badges-group">
                    <span className={`badge badge-${bot.mode}`}>{bot.mode.toUpperCase()}</span>
                    <DcaStatusBadge status={bot.status} />
                </div>
            </header>

            {/* PnL Hero: 核心收益與套利戰果看板（常駐展示，不因 armed 隱藏） */}
            <div className="strategy-pnl-hero dca-pnl-hero">
                <div className="pnl-hero-left">
                    <span className="pnl-label">策略純淨收益 (Net PnL)</span>
                    <span className={`pnl-amount font-mono ${netPnl === null ? '' : isPositivePnl ? 'positive' : 'negative'}`}>
                        {money(netPnl)}
                    </span>
                    <div className="pnl-breakdown-sub" style={{ fontSize: '0.78rem', color: 'var(--text-muted, #8b949e)', marginTop: '2px' }}>
                        <span>實現: <strong className={netRealized >= 0 ? 'positive' : 'negative'}>{money(netRealized)}</strong></span>
                        <span style={{ margin: '0 4px' }}>·</span>
                        <span>浮動: <strong className={(unrealized ?? 0) >= 0 ? 'positive' : 'negative'}>{money(unrealized)}</strong></span>
                        {roi != null && (
                            <span style={{ marginLeft: '4px' }}>
                                ({roi >= 0 ? '+' : ''}{roi.toFixed(2)}%)
                            </span>
                        )}
                    </div>
                </div>

                <div className="dca-hero-right" style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
                    <span className="pnl-label">套利戰果</span>
                    <span className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>
                        {number(bot.cycles, 0)} <span style={{ fontSize: '0.8rem', fontWeight: 400, color: 'var(--text-muted)' }}>輪循環</span>
                    </span>
                    <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
                        {bot.total_trades != null ? (
                            <span><strong>{bot.total_trades}</strong> 筆成交</span>
                        ) : hasPosition ? (
                            <span>持倉中</span>
                        ) : (
                            <span>空倉待命中</span>
                        )}
                        {fees > 0 && <span> · 費 {money(fees)}</span>}
                    </div>
                </div>
            </div>

            {/* armed 專屬提示（稽核 5.7：明確告知尚未掛單，避免誤以為故障） */}
            {armed && (
                <div className="notice notice-warning dca-armed-banner" role="status">
                    <Radar size={16} style={{ flexShrink: 0 }} />
                    <span>
                        監控中 · 尚未掛單
                        {waitText ? `（${waitText}）` : ''}
                        <small className="text-muted"> 入場條件成立前，交易所端無任何掛單</small>
                    </span>
                </div>
            )}

            {/* 命令異常提示 */}
            {(bot.command_status === 'failed' || bot.command_status === 'timed_out') && (
                <div className="notice notice-error dca-command-error" role="alert">
                    <AlertTriangle size={14} style={{ flexShrink: 0 }} />
                    <span>
                        命令 {bot.command_status === 'timed_out' ? '確認逾時' : '執行失敗'}
                        {bot.start_blocked_reason ? `：${bot.start_blocked_reason}` : '，請查看日誌。'}
                    </span>
                </div>
            )}

            {/* 入場區塊：模式 + 指標即時值 + 等待時長 */}
            <dl className="strategy-metrics-matrix dca-entry-matrix">
                <div className="metric-cell">
                    <dt>入場</dt>
                    <dd title={dcaEntryText(bot)}>{dcaEntryText(bot)}</dd>
                </div>
                <div className="metric-cell">
                    <dt>{bot.entry_timeframe ?? '—'} 時框</dt>
                    <dd className="dca-indicator-chips">
                        {(bot.entry_indicators ?? []).length === 0 && (
                            <span className="text-muted">—</span>
                        )}
                        {(bot.entry_indicators ?? []).map((indicator, index) => {
                            const info = indicatorLiveInfo(indicator);
                            return (
                                <span
                                    key={index}
                                    className={`dca-indicator-chip ${info.chipClass}`}
                                    title={info.tooltip}
                                >
                                    <span className="dca-indicator-name">{info.name}</span>
                                    {info.valueText && (
                                        <span className="dca-indicator-val">{info.valueText}</span>
                                    )}
                                </span>
                            );
                        })}
                    </dd>
                </div>
            </dl>

            {/* 階梯區塊（armed 時依 5.7 隱藏掛單數字） */}
            {!armed && (
                <div className="dca-ladder-block">
                    <div className="dca-ladder-head">
                        <span>安全單 {progress.filled != null && progress.max != null ? `${progress.filled} / ${progress.max}` : '—'}</span>
                        <span className="font-mono text-muted">
                            {progress.max != null
                                ? `剩餘 ${Math.max(0, (progress.max ?? 0) - (progress.filled ?? 0))} 張`
                                : ''}
                        </span>
                    </div>
                    <div
                      className="dca-ladder-progress"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={progress.max ?? 100}
                      aria-valuenow={progress.filled ?? 0}
                      aria-label="安全單成交進度"
                    >
                        <div
                          className="dca-ladder-progress-fill"
                          style={{ width: `${progress.percent ?? 0}%` }}
                        />
                    </div>
                    {!hasPosition && (
                        <small className="text-muted">下一張安全單 —（尚無部位）</small>
                    )}
                    {hasPosition && (
                        <small className="text-muted">
                            下一張安全單：
                            <span className="font-mono">{number(bot.next_safety_order_price)}</span>
                            <span style={{ marginLeft: '0.4rem' }}>
                                {distanceText(nextSafetyDistance(bot))}
                            </span>
                        </small>
                    )}
                </div>
            )}

            {/* 部位區塊 */}
            <dl className="strategy-metrics-matrix">
                <div className="metric-cell">
                    <dt>均價 / 現價</dt>
                    <dd className="font-mono">
                        {hasPosition ? number(bot.position?.average_cost ?? null) : '—'}
                        <span className="text-muted"> / {number(bot.current_price)}</span>
                    </dd>
                </div>
                <div className="metric-cell">
                    <dt>持倉量 / 名目</dt>
                    <dd className="font-mono">
                        {hasPosition ? (
                            <>
                                {number(bot.position?.quantity ?? null, 6)}
                                <span className="text-muted"> · {money(notional)}</span>
                            </>
                        ) : (
                            <span className="text-muted">空倉待命</span>
                        )}
                    </dd>
                </div>
                <div className="metric-cell">
                    <dt>未實現盈虧 / ROI</dt>
                    <dd className={`font-mono ${unrealized == null ? '' : unrealized >= 0 ? 'positive' : 'negative'}`}>
                        {hasPosition ? (
                            <>
                                {money(unrealized)}
                                <span className="text-muted">
                                    {' '}· {roi == null ? '—' : `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`}
                                </span>
                            </>
                        ) : (
                            <span className="text-muted">$0.00 (空倉)</span>
                        )}
                    </dd>
                </div>
                <div className="metric-cell">
                    <dt>入場價 / 每筆金額</dt>
                    <dd className="font-mono">
                        {hasPosition ? number(bot.entry_price) : '—'}
                        <span className="text-muted"> · {money(bot.base_order_amount)}</span>
                    </dd>
                </div>
            </dl>

            {/* 出場與循環區塊（armed 時顯示目標%與歷史循環，averaging 時顯示確切價格與距離） */}
            {!armed ? (
                <dl className="strategy-metrics-matrix">
                    <div className="metric-cell">
                        <dt>止盈價（{number(bot.take_profit_percent)}%）</dt>
                        <dd className="font-mono">
                            {number(bot.take_profit_price)}
                            <span className="text-muted"> {distanceText(takeProfitDistance(bot))}</span>
                        </dd>
                    </div>
                    <div className="metric-cell">
                        <dt>{bot.stop_loss_percent != null ? `止損價（${number(bot.stop_loss_percent)}%）` : '止損'}</dt>
                        <dd className="font-mono">
                            {bot.stop_loss_percent != null ? number(bot.stop_loss_price) : '—（未啟用）'}
                        </dd>
                    </div>
                    <div className="metric-cell">
                        <dt>完成循環 / 成交</dt>
                        <dd className="font-mono">
                            {number(bot.cycles, 0)} 輪
                            {bot.total_trades != null && <span className="text-muted"> · {bot.total_trades} 筆</span>}
                        </dd>
                    </div>
                </dl>
            ) : (
                <dl className="strategy-metrics-matrix">
                    <div className="metric-cell">
                        <dt>止盈目標</dt>
                        <dd className="font-mono">+{number(bot.take_profit_percent)}%</dd>
                    </div>
                    <div className="metric-cell">
                        <dt>止損保護</dt>
                        <dd className="font-mono">
                            {bot.stop_loss_percent != null ? `-${number(bot.stop_loss_percent)}%` : '—（未啟用）'}
                        </dd>
                    </div>
                    <div className="metric-cell">
                        <dt>完成循環 / 成交</dt>
                        <dd className="font-mono">
                            {number(bot.cycles, 0)} 輪
                            {bot.total_trades != null && <span className="text-muted"> · {bot.total_trades} 筆</span>}
                        </dd>
                    </div>
                </dl>
            )}

            {/* 新鮮度與資料狀態（5.10：stale 明確標示，不顯示運行正常） */}
            <div className="card-footer-meta">
                <span className={`freshness-tag ${bot.data_status}`}>
                    {freshnessText(bot.data_status, bot.last_updated_at)}
                </span>
                {bot.data_status === 'stale' && (
                    <span className="text-muted" title="資料過期：引擎心跳中斷或快照逾時">
                        最後同步 {bot.last_updated_at ? new Date(bot.last_updated_at * 1000).toLocaleString('zh-TW', { hour12: false }) : '—'}
                    </span>
                )}
            </div>

            {/* 操作：詳情 / 啟動・停止 / 刪除（各自獨立 busy 狀態，稽核 5.13） */}
            <footer className="strategy-actions">
                <button className="btn btn-outline btn-detail" type="button" onClick={onOpen} title="開啟 DCA 詳情">
                    <span>詳情</span>
                    <ChevronRight size={14} />
                </button>

                {isStarting || isStopping ? (
                    <button className="btn btn-outline" type="button" disabled>
                        <RefreshCcw size={14} className="spin" />
                        <span>{isStopping ? '停止中…' : '啟動中…'}</span>
                    </button>
                ) : running ? (
                    <button
                      className="btn btn-warning"
                      type="button"
                      disabled={busy}
                      onClick={onStop}
                      title="停止 DCA（撤單但不平倉）"
                    >
                        <Square size={14} />
                        <span>{busy ? '處理中…' : '停止'}</span>
                    </button>
                ) : (
                    <button
                      className="btn btn-success"
                      type="button"
                      disabled={busy || Boolean(bot.start_blocked_reason)}
                      title={bot.start_blocked_reason ?? '啟動 DCA（armed 監控）'}
                      onClick={onStart}
                    >
                        <Play size={14} />
                        <span>{busy ? '處理中…' : bot.start_blocked_reason ? '鎖定' : '啟動'}</span>
                    </button>
                )}

                <button
                  className="btn btn-danger btn-icon-only"
                  type="button"
                  disabled={busy}
                  title={running ? '停止並刪除 DCA' : '刪除 DCA'}
                  onClick={onDelete}
                  aria-label="刪除 DCA 機器人"
                >
                    <Trash2 size={14} />
                </button>
            </footer>
        </article>
    );
};

export default DcaCard;
