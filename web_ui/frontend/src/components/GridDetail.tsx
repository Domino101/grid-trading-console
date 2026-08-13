import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, RefreshCcw } from 'lucide-react';
import { api, type GridCommand, type GridInfo, type GridStats } from '../services/api';

interface GridDetailProps { gridId: string; onBack: () => void; }

const value = (number: number | null | undefined, digits = 2) => number === null || number === undefined ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(number);

const GridDetail = ({ gridId, onBack }: GridDetailProps) => {
  const [grid, setGrid] = useState<GridInfo | null>(null);
  const [stats, setStats] = useState<GridStats | null>(null);
  const [commands, setCommands] = useState<GridCommand[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextGrid, nextStats, commandResponse] = await Promise.all([
        api.getGrid(gridId),
        api.getGridStats(gridId),
        api.listGridCommands(gridId),
      ]);
      setGrid(nextGrid); setStats(nextStats); setCommands(commandResponse.commands); setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '無法載入策略詳情'); }
  }, [gridId]);

  useEffect(() => {
    const initialRequest = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initialRequest);
  }, [load]);

  return <main className="dashboard grid-detail">
    <header className="dashboard-header"><div><button className="back-link" onClick={onBack}><ArrowLeft size={16} />返回總覽</button><p className="eyebrow">策略工作台</p><h2>{grid?.symbol ?? gridId}</h2></div><button className="btn btn-outline" onClick={() => void load()}><RefreshCcw size={16} />重新整理</button></header>
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    <section className="detail-layout">
      <article className="strategy-card"><h3>網格與風控摘要</h3><dl className="strategy-metrics"><div><dt>交易所</dt><dd>{grid?.exchange ?? '—'}</dd></div><div><dt>策略狀態</dt><dd>{stats?.status ?? grid?.status ?? '—'}</dd></div><div><dt>價格區間</dt><dd>{grid?.lower_price === undefined || grid?.upper_price === undefined ? '—' : `${value(grid.lower_price)} – ${value(grid.upper_price)}`}</dd></div><div><dt>格距 / 下單量</dt><dd>{value(grid?.grid_interval)} / {value(grid?.order_amount, 6)}</dd></div><div><dt>槓桿</dt><dd>{value(grid?.leverage, 0)}</dd></div><div><dt>資料狀態</dt><dd>{stats?.stale ? '過期' : '已同步'}</dd></div></dl></article>
      <article className="strategy-card"><h3>資產與收益</h3><dl className="strategy-metrics"><div><dt>淨盈虧</dt><dd>{value(stats?.profit?.net)}</dd></div><div><dt>已實現 / 未實現</dt><dd>{value(stats?.profit?.realized)} / {value(stats?.profit?.unrealized)}</dd></div><div><dt>手續費</dt><dd>{value(stats?.profit?.fees)}</dd></div><div><dt>當前價格</dt><dd>{value(stats?.current_price)}</dd></div><div><dt>持倉 / 均價</dt><dd>{value(stats?.current_position)} / {value(stats?.average_cost)}</dd></div><div><dt>掛單</dt><dd>{value(stats?.pending_orders?.total, 0)}</dd></div></dl></article>
    </section>
    <section className="strategy-card"><h3>命令與稽核時間線</h3>{commands.length === 0 ? <p className="muted">尚無命令紀錄。</p> : <ol className="command-timeline">{commands.map(command => <li key={command.command_id}><strong>{command.action} · {command.status}</strong><span>{new Date(command.updated_at * 1000).toLocaleString('zh-TW')} · {command.source}</span>{command.reason && <small>原因：{command.reason}</small>}{command.detail && <small>詳情：{command.detail}</small>}</li>)}</ol>}</section>
  </main>;
};

export default GridDetail;
