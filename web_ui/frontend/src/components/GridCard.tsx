import { Play, RefreshCcw, Square, Trash2 } from 'lucide-react';
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

const GridCard = ({ grid, busy, onOpen, onStart, onStop, onDelete }: GridCardProps) => {
  const isRunning = grid.status === 'running';
  const isStarting = grid.status === 'starting';
  const isStopping = grid.status === 'stopping';
  const netPnl = grid.net_pnl;
  return (
    <article className="strategy-card">
      <header className="strategy-card-header">
        <div>
          <p className="strategy-title"><span className={`status-dot ${grid.data_status === 'live' && isRunning ? 'is-live' : ''}`} />{grid.symbol ?? '未命名策略'}</p>
          <small>{grid.exchange ?? '未知交易所'} · {grid.grid_type ?? '未知模式'}</small>
        </div>
        <div className="badges">
          <span className={`badge badge-${grid.mode}`}>{grid.mode}</span>
          <span className={`badge badge-${grid.data_status}`}>{grid.data_status}</span>
        </div>
      </header>
      <div className="pnl-block">
        <span>淨盈虧</span>
        <strong className={netPnl === null ? '' : netPnl >= 0 ? 'positive' : 'negative'}>{money(netPnl)}</strong>
        <small>{grid.pnl_rate === null ? '收益率 —' : `收益率 ${number(grid.pnl_rate)}%`}</small>
      </div>
      <dl className="strategy-metrics">
        <div><dt>權益</dt><dd>{money(grid.equity)}</dd></div>
        <div><dt>最新價</dt><dd>{number(grid.current_price)}</dd></div>
        <div><dt>成交 / 格數</dt><dd>{grid.filled_buy === null || grid.filled_sell === null ? '—' : `${grid.filled_buy + grid.filled_sell} / ${number(grid.completed_cycles, 0)}`}</dd></div>
        <div><dt>掛單</dt><dd>{number(grid.pending_orders, 0)}</dd></div>
        <div><dt>價格區間</dt><dd>{grid.lower_price === null || grid.upper_price === null ? '—' : `${number(grid.lower_price)} – ${number(grid.upper_price)}`}</dd></div>
        <div><dt>每格大小</dt><dd>{number(grid.grid_interval)}</dd></div>
      </dl>
      <p className={`freshness freshness-${grid.data_status}`}>{freshnessText(grid.data_status, grid.last_updated_at)}</p>
      {(grid.command_status === 'failed' || grid.command_status === 'timed_out') && <p className="command-alert" role="alert">命令 {grid.command_status === 'timed_out' ? '確認逾時' : '失敗'}：請進入詳情查看稽核紀錄。</p>}
      <footer className="strategy-actions">
        <button className="btn btn-outline" onClick={onOpen}>詳情</button>
        {isStarting || isStopping
          ? <button className="btn btn-outline" disabled><RefreshCcw size={15} />{isStopping ? '等待停止確認' : '等待引擎確認'}</button>
          : isRunning
            ? <button className="btn btn-warning" disabled={busy} onClick={onStop}><Square size={15} />{busy ? '處理中…' : '停止'}</button>
            : <button className="btn btn-primary" disabled={busy || Boolean(grid.start_blocked_reason)} title={grid.start_blocked_reason ?? undefined} onClick={onStart}><Play size={15} />{busy ? '處理中…' : grid.start_blocked_reason ? '不可啟動' : '啟動'}</button>}
        <button className="btn btn-danger" disabled={busy || isRunning || isStarting || isStopping} title={isRunning || isStarting || isStopping ? '請先等待策略停止後再刪除' : undefined} onClick={onDelete}><Trash2 size={15} />刪除</button>
      </footer>
    </article>
  );
};

export default GridCard;
