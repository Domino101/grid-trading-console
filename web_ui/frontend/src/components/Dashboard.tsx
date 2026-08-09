import React, { useState, useEffect } from 'react';
import { api, type GridInfo, type GridStats } from '../services/api';
import { Activity, Play, Square, Trash2, RefreshCcw } from 'lucide-react';

const Dashboard: React.FC = () => {
  const [grids, setGrids] = useState<GridInfo[]>([]);
  const [statsMap, setStatsMap] = useState<Record<string, GridStats>>({});
  const [loading, setLoading] = useState(false);

  const fetchData = async () => {
    try {
      const res = await api.listGrids();
      setGrids(res.grids);
      
      const newStatsMap: Record<string, GridStats> = {};
      for (const grid of res.grids) {
        // Fetch stats regardless of status to show historical PNL if available
        try {
          const stats = await api.getGridStats(grid.grid_id);
          newStatsMap[grid.grid_id] = stats;
        } catch (e) {
          console.error(`Failed to fetch stats for grid ${grid.grid_id}`);
        }
      }
      setStatsMap(newStatsMap);
    } catch (e) {
      console.error('Failed to list grids', e);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleStart = async (gridId: string) => {
    setLoading(true);
    try {
      await api.startGrid(gridId);
      await fetchData();
    } catch (e: any) {
      alert(e.message || 'Failed to start grid');
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async (gridId: string) => {
    if (!confirm('確定要停止此網格嗎？')) return;
    setLoading(true);
    try {
      await api.stopGrid(gridId);
      await fetchData();
    } catch (e: any) {
      alert(e.message || 'Failed to stop grid');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (gridId: string) => {
    if (!confirm('確定要刪除此網格嗎？此操作無法還原。')) return;
    setLoading(true);
    try {
      await api.deleteGrid(gridId);
      await fetchData();
    } catch (e: any) {
      alert(e.message || 'Failed to delete grid');
    } finally {
      setLoading(false);
    }
  };

  const formatMoney = (val: number | undefined) => {
    if (val === undefined || isNaN(val)) return '$0.00';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: '100%', overflowY: 'auto', paddingRight: '0.5rem' }}>
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-semibold flex items-center gap-3">
          <Activity color="var(--accent-primary)" />
          網格儀表板
        </h2>
        <button className="btn btn-outline" onClick={fetchData} disabled={loading}>
          <RefreshCcw size={16} /> 重新整理
        </button>
      </div>

      {grids.length === 0 ? (
        <div className="glass-panel flex justify-center items-center" style={{ flex: 1 }}>
          <p className="text-muted">目前沒有網格策略，請先創建一個。</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {grids.map(grid => {
            const stats = statsMap[grid.grid_id];
            const isRunning = grid.status === 'running' || grid.status === 'active';
            const pnl = stats?.profit?.total || 0;
            const pnlColor = pnl >= 0 ? 'var(--status-success)' : 'var(--status-error)';
            const tradeCount = (stats?.filled_orders?.buy || 0) + (stats?.filled_orders?.sell || 0);

            return (
              <div key={grid.grid_id} className="glass-panel flex flex-col gap-4">
                <div className="flex justify-between items-start border-b border-gray-800 pb-2">
                  <div>
                    <h3 className="text-lg font-bold text-accent">{grid.symbol || 'Unknown'}</h3>
                    <span className="text-xs text-muted">ID: {grid.grid_id.substring(0, 8)}...</span>
                  </div>
                  <div className={`badge ${isRunning ? 'badge-success' : 'badge-error'}`}>
                    {grid.status}
                  </div>
                </div>

                <div className="flex flex-col gap-2 flex-1">
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted">總利潤 (PNL)</span>
                    <span className="font-bold text-lg" style={{ color: pnlColor }}>
                      {pnl > 0 ? '+' : ''}{formatMoney(pnl)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted">交易次數</span>
                    <span className="font-medium">{tradeCount}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted">價格區間</span>
                    <span className="font-medium">
                      {grid.lower_price} - {grid.upper_price}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted">每格大小</span>
                    <span className="font-medium">{grid.grid_interval}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted">下單量</span>
                    <span className="font-medium">{grid.order_amount}</span>
                  </div>
                </div>

                <div className="flex gap-2 mt-2 pt-2 border-t border-gray-800">
                  {!isRunning ? (
                    <button 
                      className="btn btn-primary flex-1 flex justify-center items-center gap-1"
                      onClick={() => handleStart(grid.grid_id)}
                      disabled={loading}
                    >
                      <Play size={16} /> 啟動
                    </button>
                  ) : (
                    <button 
                      className="btn flex-1 flex justify-center items-center gap-1"
                      style={{ background: 'var(--status-warning)', color: '#000' }}
                      onClick={() => handleStop(grid.grid_id)}
                      disabled={loading}
                    >
                      <Square size={16} /> 停止
                    </button>
                  )}
                  <button 
                    className="btn flex-1 flex justify-center items-center gap-1"
                    style={{ background: 'var(--status-error)', color: '#fff' }}
                    onClick={() => handleDelete(grid.grid_id)}
                    disabled={loading}
                  >
                    <Trash2 size={16} /> 刪除
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Dashboard;