import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Play, RefreshCcw, Square, Trash2 } from 'lucide-react';
import { api, type DashboardGridItem, type DashboardSnapshot } from '../services/api';

const REFRESH_MS = 15_000;

const money = (value: number | null, maximumFractionDigits = 2) => value === null
  ? '—'
  : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits }).format(value);

const number = (value: number | null, digits = 2) => value === null
  ? '—'
  : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value);

const freshnessText = (item: DashboardGridItem) => {
  if (item.data_status === 'unavailable') return '尚未同步';
  if (item.data_status === 'stale') return '資料過期';
  if (!item.last_updated_at) return '已同步';
  return `同步於 ${new Date(item.last_updated_at * 1000).toLocaleTimeString('zh-TW')}`;
};

const Dashboard = () => {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionGridId, setActionGridId] = useState<string | null>(null);
  const inFlight = useRef(false);

  const fetchSnapshot = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const result = await api.getDashboardSnapshot();
      setSnapshot(result);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '無法讀取網格總覽');
    } finally {
      setLoading(false);
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    // Defer the initial request so the effect only subscribes to external work.
    const initialRequest = window.setTimeout(() => void fetchSnapshot(), 0);
    const timer = window.setInterval(() => {
      if (!document.hidden) void fetchSnapshot();
    }, REFRESH_MS);
    return () => {
      window.clearTimeout(initialRequest);
      window.clearInterval(timer);
    };
  }, [fetchSnapshot]);

  const runAction = async (grid: DashboardGridItem, action: 'start' | 'stop' | 'delete') => {
    if (action !== 'start' && !window.confirm(`${action === 'delete' ? '刪除' : '停止'} ${grid.symbol ?? grid.grid_id}？此操作會記錄於稽核日誌。`)) return;
    setActionGridId(grid.grid_id);
    try {
      if (action === 'start') await api.startGrid(grid.grid_id);
      if (action === 'stop') await api.stopGrid(grid.grid_id);
      if (action === 'delete') await api.deleteGrid(grid.grid_id);
      await fetchSnapshot();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略操作失敗');
    } finally {
      setActionGridId(null);
    }
  };

  const aggregate = snapshot?.aggregate;
  return (
    <main className="dashboard" aria-busy={loading}>
      <header className="dashboard-header">
        <div>
          <p className="eyebrow"><span className={`status-dot ${snapshot?.data_status === 'live' ? 'is-live' : ''}`} /> 網格交易總控台</p>
          <h2>策略總覽</h2>
          <p className="muted">單一快照更新；不以零值掩蓋尚未同步的交易資料。</p>
        </div>
        <button className="btn btn-outline" onClick={() => void fetchSnapshot()} disabled={loading}>
          <RefreshCcw size={16} aria-hidden="true" /> 重新整理
        </button>
      </header>

      {error && <div className="notice notice-error" role="alert"><AlertTriangle size={18} />{error}</div>}

      <section className="kpi-grid" aria-label="網格總覽指標">
        <article className="kpi-card"><span>淨盈虧</span><strong className={(aggregate?.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'}>{aggregate ? money(aggregate.net_pnl) : '—'}</strong><small>已實現 {aggregate ? money(aggregate.realized_pnl) : '—'} / 未實現 {aggregate ? money(aggregate.unrealized_pnl) : '—'}</small></article>
        <article className="kpi-card"><span>累計交易量</span><strong>{aggregate ? money(aggregate.volume, 0) : '—'}</strong><small>完成網格 {aggregate ? number(aggregate.completed_cycles, 0) : '—'} 格</small></article>
        <article className="kpi-card"><span>運行狀態</span><strong>{aggregate ? `${aggregate.running_grids}/${aggregate.total_grids}` : '—'}</strong><small>{aggregate ? `${aggregate.available_grids} 已同步 · ${aggregate.stale_grids} 過期` : '載入中'}</small></article>
      </section>

      {loading && !snapshot ? <div className="dashboard-empty">正在載入策略快照…</div> : null}
      {!loading && snapshot?.grids.length === 0 ? <div className="dashboard-empty">目前沒有網格策略，請先建立一個策略。</div> : null}

      <section className="strategy-grid" aria-label="網格策略">
        {snapshot?.grids.map((grid) => {
          const isRunning = grid.status === 'running';
          const isBusy = actionGridId === grid.grid_id;
          const netPnl = grid.net_pnl;
          return <article className="strategy-card" key={grid.grid_id}>
            <header className="strategy-card-header">
              <div><p className="strategy-title"><span className={`status-dot ${grid.data_status === 'live' && isRunning ? 'is-live' : ''}`} />{grid.symbol ?? '未命名策略'}</p><small>{grid.exchange ?? '未知交易所'} · {grid.grid_type ?? '未知模式'}</small></div>
              <div className="badges"><span className={`badge badge-${grid.mode}`}>{grid.mode}</span><span className={`badge badge-${grid.data_status}`}>{grid.data_status}</span></div>
            </header>
            <div className="pnl-block"><span>淨盈虧</span><strong className={netPnl === null ? '' : netPnl >= 0 ? 'positive' : 'negative'}>{money(netPnl)}</strong><small>{grid.pnl_rate === null ? '收益率 —' : `收益率 ${number(grid.pnl_rate)}%`}</small></div>
            <dl className="strategy-metrics">
              <div><dt>權益</dt><dd>{money(grid.equity)}</dd></div><div><dt>最新價</dt><dd>{number(grid.current_price)}</dd></div>
              <div><dt>成交 / 格數</dt><dd>{grid.filled_buy === null || grid.filled_sell === null ? '—' : `${grid.filled_buy + grid.filled_sell} / ${number(grid.completed_cycles, 0)}`}</dd></div><div><dt>掛單</dt><dd>{number(grid.pending_orders, 0)}</dd></div>
              <div><dt>價格區間</dt><dd>{grid.lower_price === null || grid.upper_price === null ? '—' : `${number(grid.lower_price)} – ${number(grid.upper_price)}`}</dd></div><div><dt>每格大小</dt><dd>{number(grid.grid_interval)}</dd></div>
            </dl>
            <p className={`freshness freshness-${grid.data_status}`}>{freshnessText(grid)}</p>
            <footer className="strategy-actions">
              {isRunning ? <button className="btn btn-warning" disabled={isBusy} onClick={() => void runAction(grid, 'stop')}><Square size={15} />{isBusy ? '處理中…' : '停止'}</button> : <button className="btn btn-primary" disabled={isBusy} onClick={() => void runAction(grid, 'start')}><Play size={15} />{isBusy ? '處理中…' : '啟動'}</button>}
              <button className="btn btn-danger" disabled={isBusy || isRunning} title={isRunning ? '請先停止策略後再刪除' : undefined} onClick={() => void runAction(grid, 'delete')}><Trash2 size={15} />刪除</button>
            </footer>
          </article>;
        })}
      </section>
    </main>
  );
};

export default Dashboard;
