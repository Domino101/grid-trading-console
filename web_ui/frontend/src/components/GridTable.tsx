import React from 'react';
import { Play, RefreshCcw, Square, Trash2, ChevronRight } from 'lucide-react';
import { freshnessText, money, number } from '../utils/format';
import type { DashboardGridItem } from '../services/api';

interface GridTableProps {
  grids: DashboardGridItem[];
  actionGridId: string | null;
  onOpen: (gridId: string) => void;
  onStart: (grid: DashboardGridItem) => void;
  onStop: (grid: DashboardGridItem) => void;
  onDelete: (grid: DashboardGridItem) => void;
}

const GridTable: React.FC<GridTableProps> = ({
  grids,
  actionGridId,
  onOpen,
  onStart,
  onStop,
  onDelete,
}) => {
  return (
    <div className="table-wrapper">
      <table className="pro-table" aria-label="網格策略一覽表">
        <thead>
          <tr>
            <th>交易對 / 模式</th>
            <th>交易所</th>
            <th>淨盈虧 (PnL)</th>
            <th>現價 / 區間</th>
            <th>權益 / 持倉</th>
            <th>成交 / 套利</th>
            <th>掛單</th>
            <th>狀態</th>
            <th style={{ textAlign: 'right' }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {grids.map((grid) => {
            const isRunning = grid.status === 'running';
            const isStarting = grid.status === 'starting';
            const isStopping = grid.status === 'stopping';
            const isBusy = actionGridId === grid.grid_id;
            const netPnl = grid.net_pnl;
            const isPositive = netPnl != null && netPnl >= 0;

            return (
              <tr key={grid.grid_id}>
                <td>
                  <div className="pro-table-symbol">
                    <span className={`status-dot ${grid.data_status === 'live' && isRunning ? 'is-live' : isRunning ? 'is-warning' : ''}`} />
                    <span>{grid.symbol ?? grid.grid_id}</span>
                    <span className={`badge badge-${grid.mode}`}>{grid.mode}</span>
                  </div>
                </td>

                <td>
                  <span className="exchange-pill">{grid.exchange ?? '—'}</span>
                </td>

                <td>
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    <strong className={`font-mono ${netPnl === null ? '' : isPositive ? 'positive' : 'negative'}`}>
                      {money(netPnl)}
                    </strong>
                    {grid.pnl_rate != null && (
                      <small className={`font-mono ${grid.pnl_rate >= 0 ? 'positive' : 'negative'}`}>
                        {number(grid.pnl_rate)}%
                      </small>
                    )}
                  </div>
                </td>

                <td>
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    <span className="font-mono">{number(grid.current_price)}</span>
                    <small className="text-muted font-mono">
                      {grid.lower_price != null && grid.upper_price != null
                        ? `${number(grid.lower_price)} - ${number(grid.upper_price)}`
                        : '—'}
                    </small>
                  </div>
                </td>

                <td>
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    <span className="font-mono">{money(grid.equity)}</span>
                    <small className="text-muted font-mono">pos: {number(grid.current_position)}</small>
                  </div>
                </td>

                <td>
                  <span className="font-mono">
                    {grid.filled_buy != null && grid.filled_sell != null
                      ? `${grid.filled_buy + grid.filled_sell} (${grid.filled_buy}/${grid.filled_sell})`
                      : '—'}
                  </span>
                  <br />
                  <small className="positive font-mono">{number(grid.completed_cycles, 0)} 格</small>
                </td>

                <td className="font-mono">
                  {number(grid.pending_orders, 0)}
                </td>

                <td>
                  <span className={`freshness-tag ${grid.data_status}`}>
                    {freshnessText(grid.data_status, grid.last_updated_at)}
                  </span>
                </td>

                <td style={{ textAlign: 'right' }}>
                  <div style={{ display: 'inline-flex', gap: '0.4rem', justifyContent: 'flex-end' }}>
                    <button
                      className="btn btn-outline"
                      style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                      type="button"
                      onClick={() => onOpen(grid.grid_id)}
                      title="開啟策略工作台"
                    >
                      <span>工作台</span>
                      <ChevronRight size={13} />
                    </button>

                    {isStarting || isStopping ? (
                      <button className="btn btn-outline" type="button" disabled style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}>
                        <RefreshCcw size={13} className="spin" />
                      </button>
                    ) : isRunning ? (
                      <button
                        className="btn btn-warning"
                        style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                        type="button"
                        disabled={isBusy}
                        onClick={() => onStop(grid)}
                        title="停止策略"
                      >
                        <Square size={13} />
                      </button>
                    ) : (
                      <button
                        className="btn btn-success"
                        style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                        type="button"
                        disabled={isBusy || Boolean(grid.start_blocked_reason)}
                        onClick={() => onStart(grid)}
                        title={grid.start_blocked_reason ?? '啟動策略'}
                      >
                        <Play size={13} />
                      </button>
                    )}

                    <button
                      className="btn btn-danger"
                      style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
                      type="button"
                      disabled={isBusy}
                      onClick={() => onDelete(grid)}
                      title={isRunning || isStarting || isStopping ? '停止並刪除策略' : '刪除策略'}
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

export default GridTable;
