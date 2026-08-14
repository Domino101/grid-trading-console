import React from 'react';
import { Play, RefreshCcw, Square, Trash2, ChevronRight, Layers, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { freshnessText, money, number } from '../utils/format';
import type { DashboardGridItem } from '../services/api';

interface GridCardProps {
  grid: DashboardGridItem;
  busy: boolean;
  onOpen: () => void;
  onStart: () => void;
  onStop: () => void;
  onDelete: () => void;
}

const GridCard: React.FC<GridCardProps> = ({ grid, busy, onOpen, onStart, onStop, onDelete }) => {
  const isRunning = grid.status === 'running';
  const isStarting = grid.status === 'starting';
  const isStopping = grid.status === 'stopping';
  const netPnl = grid.net_pnl;

  // Calculate position of current price within grid bounds
  let pricePercentage: number | null = null;
  if (
    grid.lower_price != null &&
    grid.upper_price != null &&
    grid.current_price != null &&
    grid.upper_price > grid.lower_price
  ) {
    const rawPct = ((grid.current_price - grid.lower_price) / (grid.upper_price - grid.lower_price)) * 100;
    pricePercentage = Math.min(100, Math.max(0, rawPct));
  }

  const isPositivePnl = netPnl != null && netPnl >= 0;

  return (
    <article className="strategy-card" aria-label={`策略 ${grid.symbol ?? grid.grid_id}`}>
      {/* Header */}
      <header className="strategy-card-header">
        <div className="strategy-symbol-group">
          <div className="strategy-symbol">
            <span className={`status-dot ${grid.data_status === 'live' && isRunning ? 'is-live' : isRunning ? 'is-warning' : ''}`} />
            <span>{grid.symbol ?? '未命名策略'}</span>
          </div>
          <div className="strategy-meta">
            <span className="exchange-pill">{grid.exchange ?? '未知交易所'}</span>
            <span>·</span>
            <span>{grid.grid_type ? grid.grid_type.toUpperCase() : '中性'}</span>
          </div>
        </div>

        <div className="badges-group">
          <span className={`badge badge-${grid.mode}`}>{grid.mode}</span>
          <span className={`badge badge-${grid.data_status}`}>{grid.data_status}</span>
        </div>
      </header>

      {/* PnL Hero */}
      <div className="strategy-pnl-hero">
        <div className="pnl-hero-left">
          <span className="pnl-label">淨盈虧 (Net PnL)</span>
          <span className={`pnl-amount font-mono ${netPnl === null ? '' : isPositivePnl ? 'positive' : 'negative'}`}>
            {money(netPnl)}
          </span>
        </div>
        {grid.pnl_rate != null && (
          <div className={`pnl-rate-badge ${grid.pnl_rate >= 0 ? 'positive' : 'negative'}`}>
            {grid.pnl_rate >= 0 ? <ArrowUpRight size={13} style={{ display: 'inline', verticalAlign: 'middle' }} /> : <ArrowDownRight size={13} style={{ display: 'inline', verticalAlign: 'middle' }} />}
            <span>{number(grid.pnl_rate)}%</span>
          </div>
        )}
      </div>

      {/* Grid Range Visualizer */}
      {grid.lower_price != null && grid.upper_price != null && (
        <div className="grid-range-visualizer" title="當前價格在網格上下限區間的位置">
          <div className="range-header">
            <span>網格區間</span>
            <span className="font-mono text-muted">現價: {number(grid.current_price)}</span>
          </div>
          <div className="range-track">
            <div className="range-fill" style={{ width: `${pricePercentage ?? 50}%` }} />
            {pricePercentage != null && (
              <div
                className="range-pin"
                style={{ left: `${pricePercentage}%` }}
                title={`價格位置: ${pricePercentage.toFixed(1)}%`}
              />
            )}
          </div>
          <div className="range-bounds">
            <span>{number(grid.lower_price)}</span>
            <span>{number(grid.upper_price)}</span>
          </div>
        </div>
      )}

      {/* Metrics Matrix */}
      <dl className="strategy-metrics-matrix">
        <div className="metric-cell">
          <dt>權益 / 持倉</dt>
          <dd title={money(grid.equity)}>{money(grid.equity)} <small className="text-muted">({number(grid.current_position)})</small></dd>
        </div>
        <div className="metric-cell">
          <dt>成交 (買/賣) / 套利</dt>
          <dd>
            {grid.filled_buy == null || grid.filled_sell == null
              ? '—'
              : `${grid.filled_buy + grid.filled_sell} (${grid.filled_buy}/${grid.filled_sell})`}
            {' · '}
            <span className="positive">{number(grid.completed_cycles, 0)} 格</span>
          </dd>
        </div>
        <div className="metric-cell">
          <dt>掛單數量</dt>
          <dd>{number(grid.pending_orders, 0)} 單</dd>
        </div>
        <div className="metric-cell">
          <dt>格距 / 每格量</dt>
          <dd>{number(grid.grid_interval)} / {number(grid.order_amount, 4)}</dd>
        </div>
      </dl>

      {/* Freshness & Alert */}
      <div className="card-footer-meta">
        <span className={`freshness-tag ${grid.data_status}`}>
          <Layers size={13} />
          {freshnessText(grid.data_status, grid.last_updated_at)}
        </span>
        {grid.leverage != null && (
          <span className="text-muted font-mono">{grid.leverage}x 槓桿</span>
        )}
      </div>

      {(grid.command_status === 'failed' || grid.command_status === 'timed_out') && (
        <div className="notice notice-error" style={{ padding: '0.45rem 0.65rem', fontSize: '0.78rem' }}>
          命令 {grid.command_status === 'timed_out' ? '確認逾時' : '執行失敗'}：請查看日誌。
        </div>
      )}

      {/* Action Dock */}
      <footer className="strategy-actions">
        <button className="btn btn-outline btn-detail" type="button" onClick={onOpen}>
          <span>工作台</span>
          <ChevronRight size={14} />
        </button>

        {isStarting || isStopping ? (
          <button className="btn btn-outline" type="button" disabled>
            <RefreshCcw size={14} className="spin" />
            <span>{isStopping ? '停止中…' : '啟動中…'}</span>
          </button>
        ) : isRunning ? (
          <button
            className="btn btn-warning"
            type="button"
            disabled={busy}
            onClick={onStop}
            title="停止策略並撤銷掛單"
          >
            <Square size={14} />
            <span>{busy ? '處理中…' : '停止'}</span>
          </button>
        ) : (
          <button
            className="btn btn-success"
            type="button"
            disabled={busy || Boolean(grid.start_blocked_reason)}
            title={grid.start_blocked_reason ?? '啟動策略'}
            onClick={onStart}
          >
            <Play size={14} />
            <span>{busy ? '處理中…' : grid.start_blocked_reason ? '鎖定' : '啟動'}</span>
          </button>
        )}

        <button
          className="btn btn-danger btn-icon-only"
          type="button"
          disabled={busy}
          title={isRunning || isStarting || isStopping ? '停止並刪除策略' : '刪除策略'}
          onClick={onDelete}
          aria-label="刪除策略"
        >
          <Trash2 size={14} />
        </button>
      </footer>
    </article>
  );
};

export default GridCard;
