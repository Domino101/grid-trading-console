import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, RefreshCcw } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type GridCommand, type GridEvent, type GridHistoryPoint, type GridInfo, type GridStats } from '../services/api';

interface GridDetailProps { gridId: string; onBack: () => void; }

const value = (number: number | null | undefined, digits = 2) => number === null || number === undefined ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(number);

const GridDetail = ({ gridId, onBack }: GridDetailProps) => {
  const [grid, setGrid] = useState<GridInfo | null>(null);
  const [stats, setStats] = useState<GridStats | null>(null);
  const [commands, setCommands] = useState<GridCommand[]>([]);
  const [history, setHistory] = useState<GridHistoryPoint[]>([]);
  const [events, setEvents] = useState<GridEvent[]>([]);
  const [rangeHours, setRangeHours] = useState(24);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextGrid, nextStats, commandResponse, historyResponse, eventResponse] = await Promise.all([
        api.getGrid(gridId),
        api.getGridStats(gridId),
        api.listGridCommands(gridId),
        api.getGridHistory(gridId, rangeHours),
        api.listGridEvents(gridId),
      ]);
      setGrid(nextGrid); setStats(nextStats); setCommands(commandResponse.commands); setHistory(historyResponse.points); setEvents(eventResponse.events); setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '無法載入策略詳情'); }
  }, [gridId, rangeHours]);

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
    <section className="strategy-card"><div className="chart-heading"><h3>淨盈虧快照</h3><div className="range-tabs" aria-label="歷史查詢區間">{[24, 168].map(hours => <button className={rangeHours === hours ? 'active' : ''} key={hours} onClick={() => setRangeHours(hours)}>{hours === 24 ? '24H' : '7D'}</button>)}</div></div>{history.length < 2 ? <p className="muted">尚無足夠歷史快照可繪製曲線。</p> : <div className="history-chart"><ResponsiveContainer width="100%" height={240}><LineChart data={history.map(point => ({ ...point, time: new Date(point.recorded_at * 1000).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' }) }))}><XAxis dataKey="time" tick={{ fill: '#9CA3AF', fontSize: 11 }} minTickGap={36} /><YAxis tick={{ fill: '#9CA3AF', fontSize: 11 }} width={70} /><Tooltip contentStyle={{ background: '#16171c', border: '1px solid #334155' }} labelStyle={{ color: '#F3F4F6' }} /><Line type="monotone" dataKey="net_pnl" name="淨盈虧" stroke="#10B981" strokeWidth={2} dot={false} connectNulls /></LineChart></ResponsiveContainer></div>}</section>
    <section className="strategy-card"><h3>命令與稽核時間線</h3>{commands.length === 0 ? <p className="muted">尚無命令紀錄。</p> : <ol className="command-timeline">{commands.map(command => <li key={command.command_id}><strong>{command.action} · {command.status}</strong><span>{new Date(command.updated_at * 1000).toLocaleString('zh-TW')} · {command.source}</span>{command.reason && <small>原因：{command.reason}</small>}{command.detail && <small>詳情：{command.detail}</small>}</li>)}</ol>}</section>
    <section className="strategy-card"><h3>引擎事件</h3>{events.length === 0 ? <p className="muted">尚未收到引擎 lifecycle 事件；成交明細需由交易所 adapter 提供後才會顯示。</p> : <ol className="command-timeline">{events.map(event => <li key={`${event.occurred_at}-${event.status}`}><strong>{event.event_type} · {event.status}</strong><span>{new Date(event.occurred_at * 1000).toLocaleString('zh-TW')}</span>{event.detail && <small>詳情：{event.detail}</small>}</li>)}</ol>}</section>
  </main>;
};

export default GridDetail;
