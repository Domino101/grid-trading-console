import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, RefreshCcw } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  api,
  type DashboardGridItem,
  type GridCommand,
  type GridEvent,
  type GridFillRow,
  type GridFillsResponse,
  type GridHistoryPoint,
  type GridInfo,
  type GridOrderRow,
  type GridOrdersResponse,
} from '../services/api';
import { ApiError } from '../utils/auth';
import { money, number } from '../utils/format';

interface GridDetailProps {
  gridId: string;
  onBack: () => void;
  onUnauthorized: () => void;
}

const GridDetail = ({ gridId, onBack, onUnauthorized }: GridDetailProps) => {
  const [grid, setGrid] = useState<GridInfo | null>(null);
  const [item, setItem] = useState<DashboardGridItem | null>(null);
  const [commands, setCommands] = useState<GridCommand[]>([]);
  const [history, setHistory] = useState<GridHistoryPoint[]>([]);
  const [events, setEvents] = useState<GridEvent[]>([]);
  const [orders, setOrders] = useState<GridOrdersResponse | null>(null);
  const [fills, setFills] = useState<GridFillsResponse | null>(null);
  const [rangeHours, setRangeHours] = useState(24);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const [nextGrid, snapshot, commandResponse, historyResponse, eventResponse, orderResponse, fillResponse] = await Promise.all([
        api.getGrid(gridId, controller.signal),
        api.getDashboardSnapshot(controller.signal),
        api.listGridCommands(gridId, controller.signal),
        api.getGridHistory(gridId, rangeHours, controller.signal),
        api.listGridEvents(gridId, controller.signal),
        api.getGridOrders(gridId, controller.signal),
        api.getGridFills(gridId, controller.signal),
      ]);
      setGrid(nextGrid);
      setItem(snapshot.grids.find(entry => entry.grid_id === gridId) ?? null);
      setCommands(commandResponse.commands);
      setHistory(historyResponse.points);
      setEvents(eventResponse.events);
      setOrders(orderResponse);
      setFills(fillResponse);
      setError(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        onUnauthorized();
        return;
      }
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      setError(caught instanceof Error ? caught.message : '無法載入策略詳情');
    }
  }, [gridId, onUnauthorized, rangeHours]);

  useEffect(() => {
    const initial = window.setTimeout(() => { void load(); }, 0);
    return () => {
      window.clearTimeout(initial);
      abortRef.current?.abort();
    };
  }, [load]);

  return (
    <main className="dashboard grid-detail">
      <header className="dashboard-header">
        <div>
          <button className="back-link" type="button" onClick={onBack}><ArrowLeft size={16} />返回總覽</button>
          <p className="eyebrow">策略工作台</p>
          <h2>{grid?.symbol ?? gridId}</h2>
        </div>
        <button className="btn btn-outline" type="button" onClick={() => void load()}><RefreshCcw size={16} />重新整理</button>
      </header>
      {error && <div className="notice notice-error" role="alert">{error}</div>}
      {item?.start_blocked_reason && <div className="notice notice-warning" role="status">{item.start_blocked_reason}</div>}
      <section className="detail-layout">
        <article className="strategy-card">
          <h3>網格與風控摘要</h3>
          <dl className="strategy-metrics">
            <div><dt>交易所</dt><dd>{grid?.exchange ?? '—'}</dd></div>
            <div><dt>策略狀態</dt><dd>{item?.status ?? grid?.status ?? '—'}</dd></div>
            <div><dt>帳戶模式</dt><dd>{item?.mode ?? grid?.mode ?? '—'}</dd></div>
            <div><dt>價格區間</dt><dd>{grid?.lower_price === undefined || grid?.upper_price === undefined ? '—' : `${number(grid.lower_price)} – ${number(grid.upper_price)}`}</dd></div>
            <div><dt>格距 / 下單量</dt><dd>{number(grid?.grid_interval)} / {number(grid?.order_amount, 6)}</dd></div>
            <div><dt>槓桿</dt><dd>{number(grid?.leverage, 0)}</dd></div>
            <div><dt>資料狀態</dt><dd>{item?.data_status ?? '尚未同步'}</dd></div>
          </dl>
        </article>
        <article className="strategy-card">
          <h3>資產與收益</h3>
          <dl className="strategy-metrics">
            <div><dt>淨盈虧</dt><dd>{money(item?.net_pnl)}</dd></div>
            <div><dt>已實現 / 未實現</dt><dd>{money(item?.realized_pnl)} / {money(item?.unrealized_pnl)}</dd></div>
            <div><dt>手續費</dt><dd>{money(item?.fees)}</dd></div>
            <div><dt>當前價格</dt><dd>{number(item?.current_price)}</dd></div>
            <div><dt>持倉</dt><dd>{number(item?.current_position)}</dd></div>
            <div><dt>掛單</dt><dd>{number(item?.pending_orders, 0)}</dd></div>
          </dl>
        </article>
      </section>
      <section className="strategy-card">
        <div className="chart-heading">
          <h3>淨盈虧快照</h3>
          <div className="range-tabs" aria-label="歷史查詢區間">
            {[24, 168].map(hours => (
              <button className={rangeHours === hours ? 'active' : ''} key={hours} type="button" onClick={() => setRangeHours(hours)}>
                {hours === 24 ? '24H' : '7D'}
              </button>
            ))}
          </div>
        </div>
        {history.length < 2
          ? <p className="muted">尚無足夠歷史快照可繪製曲線。</p>
          : (
            <div className="history-chart">
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={history.map(point => ({
                  ...point,
                  time: new Date(point.recorded_at * 1000).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' }),
                }))}>
                  <XAxis dataKey="time" tick={{ fill: '#9CA3AF', fontSize: 11 }} minTickGap={36} />
                  <YAxis tick={{ fill: '#9CA3AF', fontSize: 11 }} width={70} />
                  <Tooltip contentStyle={{ background: '#16171c', border: '1px solid #334155' }} labelStyle={{ color: '#F3F4F6' }} />
                  <Line type="monotone" dataKey="net_pnl" name="淨盈虧" stroke="#10B981" strokeWidth={2} dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
      </section>
      <section className="detail-layout">
        <OrderTable
          title="掛單明細"
          available={orders?.available ?? false}
          empty={orders?.message || '引擎尚未提供掛單明細'}
          rows={orders?.orders ?? []}
        />
        <FillTable
          title="成交明細"
          available={fills?.available ?? false}
          empty={fills?.message || '引擎尚未提供成交明細'}
          rows={fills?.fills ?? []}
        />
      </section>
      <section className="strategy-card">
        <h3>命令與稽核時間線</h3>
        {commands.length === 0
          ? <p className="muted">尚無命令紀錄。</p>
          : (
            <ol className="command-timeline">
              {commands.map(command => (
                <li key={command.command_id}>
                  <strong>{command.action} · {command.status}</strong>
                  <span>{new Date(command.updated_at * 1000).toLocaleString('zh-TW')} · {command.source}</span>
                  {command.reason && <small>原因：{command.reason}</small>}
                  {command.detail && <small>詳情：{command.detail}</small>}
                </li>
              ))}
            </ol>
          )}
      </section>
      <section className="strategy-card">
        <h3>引擎事件</h3>
        {events.length === 0
          ? <p className="muted">尚未收到引擎 lifecycle 事件。</p>
          : (
            <ol className="command-timeline">
              {events.map(event => (
                <li key={`${event.occurred_at}-${event.status}-${event.event_type}`}>
                  <strong>{event.event_type} · {event.status}</strong>
                  <span>{new Date(event.occurred_at * 1000).toLocaleString('zh-TW')}</span>
                  {event.detail && <small>詳情：{event.detail}</small>}
                </li>
              ))}
            </ol>
          )}
      </section>
    </main>
  );
};

const OrderTable = ({ title, available, empty, rows }: { title: string; available: boolean; empty: string; rows: GridOrderRow[] }) => (
  <article className="strategy-card">
    <h3>{title}</h3>
    {!available || rows.length === 0
      ? <p className="muted">{empty}</p>
      : (
        <table className="data-table">
          <thead>
            <tr><th>方向</th><th>價格</th><th>數量</th><th>狀態</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.side}-${row.price}-${index}`}>
                <td>{row.side ?? '—'}</td>
                <td>{number(row.price)}</td>
                <td>{number(row.amount, 6)}</td>
                <td>{row.status ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
  </article>
);

const FillTable = ({ title, available, empty, rows }: { title: string; available: boolean; empty: string; rows: GridFillRow[] }) => (
  <article className="strategy-card">
    <h3>{title}</h3>
    {!available || rows.length === 0
      ? <p className="muted">{empty}</p>
      : (
        <table className="data-table">
          <thead>
            <tr><th>方向</th><th>價格</th><th>數量</th><th>時間</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.side}-${row.price}-${index}`}>
                <td>{row.side ?? '—'}</td>
                <td>{number(row.price)}</td>
                <td>{number(row.amount, 6)}</td>
                <td>{row.time == null ? '—' : String(row.time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
  </article>
);

export default GridDetail;
