import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  RefreshCcw,
  TrendingUp,
  Activity,
  Layers,
  Sliders,
  Clock,
  Play,
  Square,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  api,
  type DashboardGridItem,
  type GridCommand,
  type GridEvent,
  type GridFillsResponse,
  type GridHistoryPoint,
  type GridInfo,
  type GridOrdersResponse,
} from '../services/api';
import { ApiError } from '../utils/auth';
import { freshnessText, money, number } from '../utils/format';

interface GridDetailProps {
  gridId: string;
  onBack: () => void;
  onUnauthorized: () => void;
}

type TabType = 'chart' | 'orders' | 'fills' | 'audit' | 'specs';

const GridDetail: React.FC<GridDetailProps> = ({ gridId, onBack, onUnauthorized }) => {
  const [activeTab, setActiveTab] = useState<TabType>('chart');
  const [grid, setGrid] = useState<GridInfo | null>(null);
  const [item, setItem] = useState<DashboardGridItem | null>(null);
  const [commands, setCommands] = useState<GridCommand[]>([]);
  const [history, setHistory] = useState<GridHistoryPoint[]>([]);
  const [events, setEvents] = useState<GridEvent[]>([]);
  const [orders, setOrders] = useState<GridOrdersResponse | null>(null);
  const [fills, setFills] = useState<GridFillsResponse | null>(null);
  const [rangeHours, setRangeHours] = useState(24);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const handleStart = async () => {
    if (item?.start_blocked_reason) {
      setActionError(item.start_blocked_reason);
      return;
    }
    setActionLoading(true);
    setActionError(null);
    try {
      await api.startGrid(gridId, 'detail');
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        onUnauthorized();
        return;
      }
      setActionError(caught instanceof Error ? caught.message : '啟動策略失敗');
    } finally {
      setActionLoading(false);
    }
  };

  const handleStop = async () => {
    if (!window.confirm(`確定要停止策略 ${grid?.symbol ?? gridId} 嗎？`)) {
      return;
    }
    setActionLoading(true);
    setActionError(null);
    try {
      await api.stopGrid(gridId, 'detail');
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        onUnauthorized();
        return;
      }
      setActionError(caught instanceof Error ? caught.message : '停止策略失敗');
    } finally {
      setActionLoading(false);
    }
  };

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const [nextGrid, snapshot, commandResponse, historyResponse, eventResponse, orderResponse, fillResponse] =
        await Promise.all([
          api.getGrid(gridId, controller.signal),
          api.getDashboardSnapshot(controller.signal),
          api.listGridCommands(gridId, controller.signal),
          api.getGridHistory(gridId, rangeHours, controller.signal),
          api.listGridEvents(gridId, controller.signal),
          api.getGridOrders(gridId, controller.signal),
          api.getGridFills(gridId, controller.signal),
        ]);
      setGrid(nextGrid);
      setItem(snapshot.grids.find((entry) => entry.grid_id === gridId) ?? null);
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
    } finally {
      setLoading(false);
    }
  }, [gridId, onUnauthorized, rangeHours]);

  useEffect(() => {
    const initial = window.setTimeout(() => {
      void load();
    }, 0);
    return () => {
      window.clearTimeout(initial);
      abortRef.current?.abort();
    };
  }, [load]);

  const grossProfit = item?.grid_profit ?? item?.realized_pnl ?? 0;
  const unrealized = item?.unrealized_pnl ?? 0;
  const fees = item?.fees ?? 0;
  const totalPnl = (item?.total_pnl !== null && item?.total_pnl !== undefined)
    ? item.total_pnl
    : (grossProfit + unrealized);
  const calculatedNetPnl = totalPnl - fees;
  const netPnl = (item?.net_pnl !== null && item?.net_pnl !== undefined && !(item?.net_pnl === 0 && calculatedNetPnl !== 0))
    ? item.net_pnl
    : calculatedNetPnl;
  const isPositiveTotal = totalPnl != null && totalPnl >= 0;

  return (
    <main className="dashboard grid-detail">
      {/* Header */}
      <header className="dashboard-header">
        <div>
          <button className="btn btn-outline" type="button" onClick={onBack} style={{ marginBottom: '0.5rem', padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}>
            <ArrowLeft size={14} />
            <span>返回總覽</span>
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
            <h2>{grid?.symbol ?? gridId}</h2>
            {item && <span className={`badge badge-${item.mode}`}>{item.mode.toUpperCase()}</span>}
            {item && (
              <span className={`badge badge-${item.status}`}>
                {item.status === 'running'
                  ? '運行中'
                  : item.status === 'stopped'
                  ? '已停止'
                  : item.status === 'starting'
                  ? '啟動中'
                  : item.status === 'stopping'
                  ? '停止中'
                  : item.status}
              </span>
            )}
            <span className="exchange-pill">{grid?.exchange ?? '未知交易所'}</span>
            {item && (
              <span className={`freshness-tag ${item.data_status}`} style={{ fontSize: '0.75rem' }}>
                {freshnessText(item.data_status, item.last_updated_at)}
              </span>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {item && (item.status === 'stopped' || item.status === 'created' || item.status === 'failed') && (
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => void handleStart()}
              disabled={actionLoading || Boolean(item.start_blocked_reason)}
              title={item.start_blocked_reason ?? '啟動策略'}
            >
              <Play size={14} />
              <span>{actionLoading ? '處理中…' : '啟動策略'}</span>
            </button>
          )}

          {item && (item.status === 'running' || item.status === 'starting') && (
            <button
              className="btn btn-danger"
              type="button"
              onClick={() => void handleStop()}
              disabled={actionLoading}
              title="停止策略"
            >
              <Square size={14} />
              <span>{actionLoading ? '處理中…' : '停止策略'}</span>
            </button>
          )}

          <button className="btn btn-outline" type="button" onClick={() => void load()} disabled={loading}>
            <RefreshCcw size={14} className={loading ? 'spin' : ''} />
            <span>{loading ? '更新中…' : '重新整理'}</span>
          </button>
        </div>
      </header>

      {actionError && <div className="notice notice-error" role="alert">{actionError}</div>}
      {error && <div className="notice notice-error" role="alert">{error}</div>}
      {item?.start_blocked_reason && <div className="notice notice-warning" role="status">{item.start_blocked_reason}</div>}

      {/* Mini KPI Bar */}
      <section className="kpi-grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
          <span className="text-secondary" style={{ fontSize: '0.75rem' }}>策略總收益 (Total PnL)</span>
          <strong className={`font-mono ${totalPnl === null ? '' : isPositiveTotal ? 'positive' : 'negative'}`} style={{ fontSize: '1.4rem' }}>
            {money(totalPnl)}
          </strong>
          <small className="font-mono">
            毛利: {money(grossProfit)} · 浮動: {money(unrealized)} · 手續費: {money(fees)} (純淨: {money(netPnl)})
          </small>
        </article>

        <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
          <span className="text-secondary" style={{ fontSize: '0.75rem' }}>網格持倉 / 全帳戶持倉</span>
          <strong className="font-mono" style={{ fontSize: '1.4rem' }}>
            {number(item?.grid_position ?? item?.current_position, 4)}
          </strong>
          <small className="font-mono">
            {item?.account_position != null ? `全帳戶: ${number(item.account_position, 4)} · ` : ''}手續費: {money(item?.fees)}
          </small>
        </article>

        <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
          <span className="text-secondary" style={{ fontSize: '0.75rem' }}>網格區間 / 格距</span>
          <strong className="font-mono" style={{ fontSize: '1.3rem' }}>
            {grid?.lower_price != null && grid?.upper_price != null ? `${number(grid.lower_price)} - ${number(grid.upper_price)}` : '—'}
          </strong>
          <small className="font-mono">格距: {number(grid?.grid_interval)} · 每格量: {number(grid?.order_amount, 4)}</small>
        </article>

        <article className="kpi-card" style={{ minHeight: 'auto', padding: '1rem' }}>
          <span className="text-secondary" style={{ fontSize: '0.75rem' }}>掛單 / 成交套利</span>
          <strong className="font-mono" style={{ fontSize: '1.4rem' }}>
            {number(item?.pending_orders, 0)} <small style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>掛單</small>
          </strong>
          <small className="font-mono positive">已套利 {number(item?.completed_cycles, 0)} 格 (買 {item?.filled_buy ?? 0} / 賣 {item?.filled_sell ?? 0})</small>
        </article>
      </section>

      {/* Navigation Tabs */}
      <nav className="detail-nav-tabs" role="tablist">
        <button
          className={`detail-tab ${activeTab === 'chart' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('chart')}
        >
          <TrendingUp size={15} />
          <span>收益走勢</span>
        </button>

        <button
          className={`detail-tab ${activeTab === 'orders' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('orders')}
        >
          <Layers size={15} />
          <span>掛單深度 ({orders?.orders.length ?? 0})</span>
        </button>

        <button
          className={`detail-tab ${activeTab === 'fills' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('fills')}
        >
          <Activity size={15} />
          <span>成交紀錄 ({fills?.fills.length ?? 0})</span>
        </button>

        <button
          className={`detail-tab ${activeTab === 'audit' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('audit')}
        >
          <Clock size={15} />
          <span>命令與事件 ({commands.length + events.length})</span>
        </button>

        <button
          className={`detail-tab ${activeTab === 'specs' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('specs')}
        >
          <Sliders size={15} />
          <span>策略參數</span>
        </button>
      </nav>

      {/* Tab 1: Chart */}
      {activeTab === 'chart' && (
        <section className="glass-panel chart-card" style={{ padding: '1.25rem' }}>
          <div className="chart-heading">
            <div>
              <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>策略收益歷史走勢</h3>
              <p className="muted">展示策略總收益（毛利 + 浮動）與扣除手續費後的純淨收益走勢</p>
            </div>
            <div className="range-tabs" aria-label="查詢時段">
              {[24, 168].map((hours) => (
                <button
                  key={hours}
                  className={rangeHours === hours ? 'active' : ''}
                  type="button"
                  onClick={() => setRangeHours(hours)}
                >
                  {hours === 24 ? '24 小時' : '7 天'}
                </button>
              ))}
            </div>
          </div>

          {history.length < 2 ? (
            <div style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
              尚無足夠歷史快照數據可繪製曲線（至少需 2 筆紀錄）。
            </div>
          ) : (
            <div className="history-chart-wrapper">
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart
                  data={history.map((point) => {
                    const tPnl = point.total_pnl ?? ((point.realized_pnl ?? 0) + (point.unrealized_pnl ?? 0));
                    const nPnl = point.net_pnl ?? (tPnl - (point.fees ?? 0));
                    return {
                      ...point,
                      total_pnl: tPnl,
                      net_pnl: nPnl,
                      time: rangeHours > 24
                        ? new Date(point.recorded_at * 1000).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit' })
                        : new Date(point.recorded_at * 1000).toLocaleTimeString('zh-TW', {
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                          }),
                    };
                  })}
                  margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="totalPnlGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--trading-green)" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="var(--trading-green)" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="netPnlGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="time" tick={{ fill: '#94A3B8', fontSize: 11 }} minTickGap={40} />
                  <YAxis tick={{ fill: '#94A3B8', fontSize: 11 }} width={65} domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={{
                      background: 'rgba(15, 20, 32, 0.95)',
                      border: '1px solid rgba(255,255,255,0.15)',
                      borderRadius: '8px',
                      fontFamily: 'var(--font-mono)',
                    }}
                    labelStyle={{ color: '#F8FAFC' }}
                  />
                  <Area
                    type="monotone"
                    dataKey="total_pnl"
                    name="總收益 (Total PnL)"
                    stroke="var(--trading-green)"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#totalPnlGradient)"
                    connectNulls
                  />
                  <Area
                    type="monotone"
                    dataKey="net_pnl"
                    name="純淨收益 (Net PnL)"
                    stroke="#38bdf8"
                    strokeWidth={1.5}
                    strokeDasharray="4 4"
                    fillOpacity={1}
                    fill="url(#netPnlGradient)"
                    connectNulls
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      )}

      {/* Tab 2: Orders Ladder */}
      {activeTab === 'orders' && (
        <section className="glass-panel" style={{ padding: '1.25rem' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>當前網格掛單階梯</h3>
          {!orders?.available || orders.orders.length === 0 ? (
            <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>
              {orders?.message || '引擎目前無活躍掛單或尚未同步'}
            </p>
          ) : (
            <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
              <table className="pro-table">
                <thead>
                  <tr>
                    <th>方向 (Side)</th>
                    <th>價格 (Price)</th>
                    <th>數量 (Amount)</th>
                    <th>狀態 (Status)</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.orders.map((row, idx) => {
                    const isBuy = row.side?.toLowerCase() === 'buy';
                    return (
                      <tr key={`${row.side}-${row.price}-${idx}`}>
                        <td>
                          <span className={`badge ${isBuy ? 'badge-success' : 'badge-failed'}`}>
                            {row.side?.toUpperCase() ?? '—'}
                          </span>
                        </td>
                        <td className={`font-mono ${isBuy ? 'positive' : 'negative'}`}>
                          {number(row.price)}
                        </td>
                        <td className="font-mono">{number(row.amount, 6)}</td>
                        <td>
                          <span className="text-secondary font-mono">{row.status ?? 'OPEN'}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Tab 3: Fills */}
      {activeTab === 'fills' && (
        <section className="glass-panel" style={{ padding: '1.25rem' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>成交與套利紀錄</h3>
          {!fills?.available || fills.fills.length === 0 ? (
            <p className="muted" style={{ padding: '2rem 0', textAlign: 'center' }}>
              {fills?.message || '尚無成交明細紀錄'}
            </p>
          ) : (
            <div className="table-wrapper" style={{ marginTop: '0.75rem' }}>
              <table className="pro-table">
                <thead>
                  <tr>
                    <th>方向 (Side)</th>
                    <th>成交價格 (Price)</th>
                    <th>成交量 (Amount)</th>
                    <th>實現盈虧 (PnL)</th>
                    <th>手續費 (Fee)</th>
                    <th>成交時間 (Time)</th>
                  </tr>
                </thead>
                <tbody>
                  {fills.fills.map((row, idx) => {
                    const isBuy = row.side?.toLowerCase() === 'buy';
                    const hasProfit = row.profit != null && row.profit !== 0;
                    return (
                      <tr key={`${row.side}-${row.price}-${idx}`}>
                        <td>
                          <span className={`badge ${isBuy ? 'badge-success' : 'badge-failed'}`}>
                            {row.side?.toUpperCase() ?? '—'}
                          </span>
                        </td>
                        <td className={`font-mono ${isBuy ? 'positive' : 'negative'}`}>
                          {number(row.price)}
                        </td>
                        <td className="font-mono">{number(row.amount, 6)}</td>
                        <td className="font-mono">
                          {hasProfit ? (
                            <span className={row.profit! >= 0 ? 'positive' : 'negative'}>
                              {money(row.profit, 4)}
                            </span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td className="font-mono text-muted">{money(row.fee, 4)}</td>
                        <td className="font-mono text-muted">{row.time != null ? String(row.time) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Tab 4: Audit Timeline & Events */}
      {activeTab === 'audit' && (
        <div className="detail-layout-grid">
          <section className="glass-panel" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.75rem' }}>命令審計日誌</h3>
            {commands.length === 0 ? (
              <p className="muted">尚無命令紀錄。</p>
            ) : (
              <ol className="timeline-stream">
                {commands.map((cmd) => (
                  <li className="timeline-item" key={cmd.command_id}>
                    <div className="timeline-title">
                      <span className="badge badge-success">{cmd.action.toUpperCase()}</span>
                      <span>{cmd.status}</span>
                    </div>
                    <span className="timeline-time">
                      {new Date(cmd.updated_at * 1000).toLocaleString('zh-TW')} · 來源: {cmd.source}
                    </span>
                    {cmd.reason && <small className="text-secondary">原因: {cmd.reason}</small>}
                    {cmd.detail && <small className="text-muted">詳情: {cmd.detail}</small>}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="glass-panel" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.75rem' }}>引擎生命週期事件</h3>
            {events.length === 0 ? (
              <p className="muted">尚未收到引擎事件。</p>
            ) : (
              <ol className="timeline-stream">
                {events.map((evt, idx) => (
                  <li className="timeline-item" key={`${evt.occurred_at}-${idx}`}>
                    <div className="timeline-title">
                      <span className="badge badge-paper">{evt.event_type}</span>
                      <span>{evt.status}</span>
                    </div>
                    <span className="timeline-time">
                      {new Date(evt.occurred_at * 1000).toLocaleString('zh-TW')}
                    </span>
                    {evt.detail && <small className="text-muted">{evt.detail}</small>}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}

      {/* Tab 5: Strategy Specs */}
      {activeTab === 'specs' && (
        <section className="glass-panel" style={{ padding: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem' }}>完整策略與風控規格</h3>
          <div className="form-fields-grid">
            <div className="metric-cell">
              <dt>策略識別碼 (Grid ID)</dt>
              <dd className="font-mono">{grid?.grid_id ?? gridId}</dd>
            </div>
            <div className="metric-cell">
              <dt>配置路徑 (Config Path)</dt>
              <dd className="font-mono text-muted">{grid?.config_path ?? '—'}</dd>
            </div>
            <div className="metric-cell">
              <dt>槓桿倍數 (Leverage)</dt>
              <dd className="font-mono">{grid?.leverage ?? 1}x</dd>
            </div>
            <div className="metric-cell">
              <dt>網格方向 (Grid Type)</dt>
              <dd className="font-mono">{grid?.grid_type?.toUpperCase() ?? 'NEUTRAL'}</dd>
            </div>
            <div className="metric-cell">
              <dt>建立者 (Created By)</dt>
              <dd className="font-mono">{grid?.created_by ?? 'system'}</dd>
            </div>
            <div className="metric-cell">
              <dt>當前命令狀態</dt>
              <dd className="font-mono">{grid?.command_status ?? 'NORMAL'}</dd>
            </div>
          </div>
        </section>
      )}
    </main>
  );
};

export default GridDetail;
