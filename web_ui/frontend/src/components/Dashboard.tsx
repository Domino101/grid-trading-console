import React, { useState, useEffect, useRef } from 'react';
import { api, WS_BASE, type ProcessStatus } from '../services/api';
import { 
  Square, RefreshCcw, Activity, 
  TrendingUp, ShieldAlert, Zap, TerminalSquare
} from 'lucide-react';
import { 
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer 
} from 'recharts';

interface DashboardProps {
  processes: ProcessStatus[];
  selectedPid: number | null;
  onSelectPid: (pid: number) => void;
  onRefresh: () => void;
}

const Dashboard: React.FC<DashboardProps> = ({ processes, selectedPid, onRefresh }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [chartData, setChartData] = useState<any[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selectedPid) return;

    setLogs([]);
    setStats(null);
    setChartData([]);

    const ws = new WebSocket(`${WS_BASE}/${selectedPid}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const text = event.data;
      
      // Flexible matching for [STATS_JSON] with or without trailing space
      const statsMatch = text.match(/\[STATS_JSON\]\s*(\{.*\})/);
      
      if (statsMatch && statsMatch[1]) {
        try {
          const parsed = JSON.parse(statsMatch[1]);
          setStats(parsed);
          
          // Add point to chart with defensive checks
          const now = new Date();
          const totalPnl = parsed.profit?.total ?? parsed.total_pnl ?? 0;
          
          setChartData(prev => {
            const newPoint = { 
              time: now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'}), 
              pnl: totalPnl
            };
            const newData = [...prev, newPoint];
            return newData.slice(-50); // Keep last 50 points
          });
        } catch (e) {
          console.error("Failed to parse stats JSON:", e, "Text:", text);
        }
      } else {
        // Only append if it's not a heartbeat or empty line
        if (text.trim()) {
          setLogs(prev => [...prev.slice(-100), text]);
        }
      }
    };

    return () => {
      ws.close();
    };
  }, [selectedPid]);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  const handleStop = async () => {
    if (!selectedPid) return;
    if (confirm('Are you sure you want to stop this bot?')) {
      await api.stopProcess(selectedPid);
      onRefresh();
    }
  };

  const selectedProcess = processes.find(p => p.pid === selectedPid);

  if (!selectedPid || !selectedProcess) {
    return (
      <div className="glass-panel flex justify-center items-center" style={{ height: '100%' }}>
        <p className="text-muted">Select or start a bot to view dashboard.</p>
      </div>
    );
  }

  const formatMoney = (val: number | undefined) => {
    if (val === undefined || isNaN(val)) return '$0.00';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  const formatPercent = (val: number | undefined) => {
    if (val === undefined || isNaN(val)) return '0.00%';
    return (val > 0 ? '+' : '') + val.toFixed(2) + '%';
  };

  const pnlColor = (stats?.profit?.total || 0) >= 0 ? 'var(--status-success)' : 'var(--status-error)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: '100%', overflowY: 'auto', paddingRight: '0.5rem' }}>
      {/* Top Header */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-semibold flex items-center gap-3">
            <Activity color="var(--accent-primary)" />
            {selectedProcess.config_path?.split('/').pop()?.replace('.yaml', '') || `PID ${selectedPid}`}
            {selectedProcess.status === 'running' && <span className="badge badge-success" style={{ marginLeft: '1rem' }}>Active</span>}
          </h2>
          <p className="text-sm text-muted mt-1">PID: {selectedPid} | Started: {selectedProcess.start_time ? new Date(selectedProcess.start_time).toLocaleString() : 'Unknown'}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-outline" onClick={onRefresh}><RefreshCcw size={16} /> Refresh</button>
          <button className="btn btn-primary" onClick={handleStop} style={{ background: 'var(--status-error)', color: '#fff' }}><Square size={16} /> Stop Bot</button>
        </div>
      </div>

      {/* Main Stats Cards based on premium design */}
      <div className="grid grid-cols-4 gap-4">
        <div className="glass-panel flex flex-col gap-2">
          <div className="text-xs text-muted flex items-center gap-2 uppercase tracking-wider font-semibold">
            <Zap size={14} className="text-accent" /> Total Balance
          </div>
          <div className="text-3xl font-bold">{formatMoney(stats?.balance?.total)}</div>
          <div className="text-sm text-success flex items-center gap-1">
            <TrendingUp size={14} /> {(stats?.balance?.utilization || 0).toFixed(1)}% Utilized
          </div>
        </div>
        
        <div className="glass-panel flex flex-col gap-2">
          <div className="text-xs text-muted flex items-center gap-2 uppercase tracking-wider font-semibold">
            <Activity size={14} className="text-accent" /> Total PnL
          </div>
          <div className="text-3xl font-bold" style={{ color: pnlColor }}>
            {(stats?.profit?.total || 0) > 0 ? '+' : ''}{formatMoney(stats?.profit?.total)}
          </div>
          <div className="text-sm" style={{ color: pnlColor }}>
            {formatPercent(stats?.profit?.rate)} ROI
          </div>
        </div>
        
        <div className="glass-panel flex flex-col gap-2">
          <div className="text-xs text-muted flex items-center gap-2 uppercase tracking-wider font-semibold">
            <TrendingUp size={14} className="text-accent" /> Realized / Unrealized
          </div>
          <div className="text-xl font-medium">{formatMoney(stats?.profit?.realized)}</div>
          <div className="text-sm text-muted">
            Unrealized: <span style={{ color: (stats?.profit?.unrealized || 0) >= 0 ? 'var(--status-success)' : 'var(--status-error)'}}>{formatMoney(stats?.profit?.unrealized)}</span>
          </div>
        </div>

        <div className="glass-panel flex flex-col gap-2">
          <div className="text-xs text-muted flex items-center gap-2 uppercase tracking-wider font-semibold">
            <Activity size={14} className="text-accent" /> Pending Orders
          </div>
          <div className="text-3xl font-bold">{stats?.pending_orders?.total || 0}</div>
          <div className="text-sm text-muted">
            Buy: <span className="text-success">{stats?.pending_orders?.buy || 0}</span> | Sell: <span className="text-error">{stats?.pending_orders?.sell || 0}</span>
          </div>
        </div>
      </div>

      {/* Chart Section */}
      <div className="glass-panel" style={{ height: '300px', display: 'flex', flexDirection: 'column' }}>
        <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <TrendingUp size={18} className="text-accent" /> PnL Performance
        </h3>
        <div style={{ flex: 1 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="colorPnl" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--accent-primary)" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="var(--accent-primary)" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <XAxis dataKey="time" stroke="var(--text-muted)" fontSize={12} tickLine={false} axisLine={false} />
              <YAxis stroke="var(--text-muted)" fontSize={12} tickLine={false} axisLine={false} width={80} tickFormatter={(val) => `$${val}`} />
              <Tooltip 
                contentStyle={{ backgroundColor: 'var(--bg-panel)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)' }}
                itemStyle={{ color: 'var(--accent-primary)' }}
              />
              <Area type="monotone" dataKey="pnl" stroke="var(--accent-primary)" strokeWidth={2} fillOpacity={1} fill="url(#colorPnl)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Detailed Stats & Logs row */}
      <div className="grid grid-cols-2 gap-4" style={{ flex: 1, minHeight: '300px' }}>
        {/* Additional Stats */}
        <div className="glass-panel flex flex-col gap-4 overflow-y-auto">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <ShieldAlert size={18} className="text-accent" /> Grid Parameters
          </h3>
          {stats ? (
            <div className="flex flex-col gap-3">
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Current Price</span>
                <span className="font-medium text-accent">{formatMoney(stats.current_price)}</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Current Position</span>
                <span className="font-medium">{stats.current_position} (Avg: {formatMoney(stats.average_cost)})</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Grid Range</span>
                <span className="font-medium">{formatMoney(stats.price_range?.lower)} - {formatMoney(stats.price_range?.upper)}</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Grid Count / Interval</span>
                <span className="font-medium">{stats.grid_count} / {stats.grid_interval}</span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Filled Orders (B/S)</span>
                <span className="font-medium text-success">{stats.filled_orders?.buy} <span className="text-muted">/</span> <span className="text-error">{stats.filled_orders?.sell}</span></span>
              </div>
              <div className="flex justify-between items-center border-b border-gray-800 pb-2">
                <span className="text-muted">Running Time</span>
                <span className="font-medium">{stats.time?.running_time}</span>
              </div>
            </div>
          ) : (
             <div className="flex items-center justify-center h-full text-muted">Waiting for grid engine statistics...</div>
          )}
        </div>

        {/* Live Logs */}
        <div className="glass-panel flex flex-col" style={{ padding: '0', overflow: 'hidden' }}>
          <div style={{ padding: '1rem', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <TerminalSquare size={18} className="text-accent" />
            <h3 className="text-lg font-semibold">Live Logs</h3>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '1rem', background: '#000', fontFamily: 'monospace', fontSize: '0.85rem' }}>
            {logs.length === 0 && <span className="text-muted">No logs available...</span>}
            {logs.map((log, i) => {
              let color = 'var(--text-primary)';
              if (log.includes('ERROR')) color = 'var(--status-error)';
              if (log.includes('WARN')) color = 'var(--status-warning)';
              if (log.includes('[成交]')) color = 'var(--status-success)';
              if (log.includes('[反手]')) color = '#3b82f6';
              return <div key={i} style={{ color, marginBottom: '2px', wordBreak: 'break-all' }}>{log}</div>;
            })}
            <div ref={logsEndRef} />
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
