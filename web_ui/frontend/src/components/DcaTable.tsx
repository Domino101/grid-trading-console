import React from 'react';
import { Play, RefreshCcw, Square, Trash2, ChevronRight } from 'lucide-react';
import { freshnessText, money, number } from '../utils/format';
import {
    dcaEntryText,
    dcaLadderProgress,
    dcaPositionNotional,
    dcaRoiPercent,
    directionLabel,
    distanceText,
    isDcaArmed,
    isDcaRunningStatus,
    nextSafetyDistance,
    takeProfitDistance,
} from '../utils/dca';
import type { DcaSnapshotItem } from '../services/api';
import DcaStatusBadge from './DcaStatusBadge';

interface DcaTableProps {
    bots: DcaSnapshotItem[];
    actionBotId: string | null;
    onOpen: (botId: string) => void;
    onStart: (bot: DcaSnapshotItem) => void;
    onStop: (bot: DcaSnapshotItem) => void;
    onDelete: (bot: DcaSnapshotItem) => void;
}

const DcaTable: React.FC<DcaTableProps> = ({
    bots,
    actionBotId,
    onOpen,
    onStart,
    onStop,
    onDelete,
}) => {
    return (
        <div className="table-wrapper">
            <table className="pro-table" aria-label="DCA 機器人一覽表">
                <thead>
                    <tr>
                        <th>交易對 / 方向 / 模式</th>
                        <th>狀態</th>
                        <th>入場</th>
                        <th>安全單進度</th>
                        <th>均價 / 現價</th>
                        <th>未實現 / ROI</th>
                        <th>止盈價</th>
                        <th>循環</th>
                        <th style={{ textAlign: 'right' }}>操作</th>
                    </tr>
                </thead>
                <tbody>
                    {bots.map((bot) => {
                        const running = isDcaRunningStatus(bot.status);
                        const isStopping = bot.status === 'stopping';
                        const isStarting = bot.status === 'starting';
                        const armed = isDcaArmed(bot.status);
                        const isBusy = actionBotId === bot.bot_id;
                        const progress = dcaLadderProgress(bot);
                        const unrealized = bot.profit?.unrealized ?? null;
                        const roi = dcaRoiPercent(bot);
                        const notional = dcaPositionNotional(bot);

                        return (
                            <tr key={bot.bot_id}>
                                <td>
                                    <div className="pro-table-symbol">
                                        <span className={`status-dot ${bot.data_status === 'live' && running ? 'is-live' : running ? 'is-warning' : ''}`} />
                                        <span>{bot.symbol ?? bot.bot_id}</span>
                                        <span className={`badge badge-${bot.mode}`}>{bot.mode.toUpperCase()}</span>
                                        <span className="exchange-pill" title="方向">
                                            {directionLabel(bot.direction)}
                                        </span>
                                    </div>
                                    <small className="text-muted">{bot.exchange ?? '—'}</small>
                                </td>

                                <td>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'flex-start' }}>
                                        <DcaStatusBadge status={bot.status} />
                                        <span className={`freshness-tag ${bot.data_status}`}>
                                            {freshnessText(bot.data_status, bot.last_updated_at)}
                                        </span>
                                    </div>
                                </td>

                                <td>
                                    <span style={{ fontSize: '0.8rem' }}>{dcaEntryText(bot)}</span>
                                    {armed && (
                                        <small className="text-muted" style={{ display: 'block' }}>
                                            尚未掛單
                                        </small>
                                    )}
                                </td>

                                <td>
                                    {armed ? (
                                        <span className="text-muted">—（armed）</span>
                                    ) : (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                            <span className="font-mono">
                                                {progress.filled != null && progress.max != null
                                                    ? `${progress.filled} / ${progress.max}`
                                                    : '—'}
                                            </span>
                                            <div
                                              className="dca-ladder-progress"
                                              style={{ width: '120px' }}
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
                                            {(bot.position?.quantity ?? 0) > 0 && (
                                                <small className="text-muted font-mono">
                                                    下一張 {number(bot.next_safety_order_price)}{' '}
                                                    {distanceText(nextSafetyDistance(bot))}
                                                </small>
                                            )}
                                        </div>
                                    )}
                                </td>

                                <td>
                                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                                        <span className="font-mono">{number(bot.position?.average_cost ?? null)}</span>
                                        <small className="text-muted font-mono">{number(bot.current_price)}</small>
                                    </div>
                                </td>

                                <td>
                                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                                        <strong className={`font-mono ${unrealized == null ? '' : unrealized >= 0 ? 'positive' : 'negative'}`}>
                                            {money(unrealized)}
                                        </strong>
                                        <small className={`font-mono ${roi == null ? '' : roi >= 0 ? 'positive' : 'negative'}`}>
                                            {roi == null ? '—' : `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`}
                                            <span className="text-muted"> · {money(notional)}</span>
                                        </small>
                                    </div>
                                </td>

                                <td>
                                    <span className="font-mono">
                                        {armed ? '—' : number(bot.take_profit_price)}
                                    </span>
                                    {!armed && bot.take_profit_price != null && (
                                        <small className="text-muted font-mono" style={{ display: 'block' }}>
                                            {distanceText(takeProfitDistance(bot))}
                                        </small>
                                    )}
                                </td>

                                <td className="font-mono">{number(bot.cycles, 0)} 輪</td>

                                <td style={{ textAlign: 'right' }}>
                                    <div style={{ display: 'inline-flex', gap: '0.4rem', justifyContent: 'flex-end' }}>
                                        <button
                                          className="btn btn-outline"
                                          style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                                          type="button"
                                          onClick={() => onOpen(bot.bot_id)}
                                          title="開啟 DCA 詳情"
                                        >
                                            <span>詳情</span>
                                            <ChevronRight size={13} />
                                        </button>

                                        {isStarting || isStopping ? (
                                            <button className="btn btn-outline" type="button" disabled style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}>
                                                <RefreshCcw size={13} className="spin" />
                                            </button>
                                        ) : running ? (
                                            <button
                                              className="btn btn-warning"
                                              style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                                              type="button"
                                              disabled={isBusy}
                                              onClick={() => onStop(bot)}
                                              title="停止 DCA（撤單但不平倉）"
                                            >
                                                <Square size={13} />
                                            </button>
                                        ) : (
                                            <button
                                              className="btn btn-success"
                                              style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                                              type="button"
                                              disabled={isBusy || Boolean(bot.start_blocked_reason)}
                                              onClick={() => onStart(bot)}
                                              title={bot.start_blocked_reason ?? '啟動 DCA'}
                                            >
                                                <Play size={13} />
                                            </button>
                                        )}

                                        <button
                                          className="btn btn-danger"
                                          style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                                          type="button"
                                          disabled={isBusy}
                                          onClick={() => onDelete(bot)}
                                          title={running ? '停止並刪除 DCA' : '刪除 DCA'}
                                          aria-label={`刪除 ${bot.symbol ?? bot.bot_id}`}
                                        >
                                            <Trash2 size={13} />
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

export default DcaTable;
