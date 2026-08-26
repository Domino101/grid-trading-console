import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Search,
  LayoutGrid,
  List,
  PlusCircle,
  TrendingUp,
  DollarSign,
  Activity,
  Cpu,
  ShieldAlert,
} from 'lucide-react';
import {
  api,
  type DashboardGridItem,
  type DashboardSnapshot,
  type SystemHealth,
} from '../services/api';
import { ApiError } from '../utils/auth';
import { money, nextPollDelay, number } from '../utils/format';
import ConfirmDialog from './ConfirmDialog';
import GridCard from './GridCard';
import GridTable from './GridTable';
import KpiCard from './KpiCard';
import TopBar from './TopBar';

const REFRESH_MS = 15_000;

type FilterMode = 'all' | 'running' | 'live' | 'paper' | 'stale';

interface DashboardProps {
  onOpenGrid: (gridId: string) => void;
  onCreateNew?: () => void;
  onUnauthorized: () => void;
}

const Dashboard: React.FC<DashboardProps> = ({ onOpenGrid, onCreateNew, onUnauthorized }) => {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionGridId, setActionGridId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ grid: DashboardGridItem; action: 'stop' | 'delete' } | null>(null);

  // Filters & View State
  const [searchQuery, setSearchQuery] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');

  const inFlight = useRef(false);
  const failures = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const fetchSnapshot = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const [result, nextHealth] = await Promise.all([
        api.getDashboardSnapshot(controller.signal),
        api.getSystemHealth(controller.signal).catch(() => null),
      ]);
      setSnapshot(result);
      if (nextHealth) setHealth(nextHealth);
      setError(null);
      failures.current = 0;
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        onUnauthorized();
        return;
      }
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      failures.current += 1;
      setError(caught instanceof Error ? caught.message : '無法讀取網格總覽快照');
    } finally {
      setLoading(false);
      inFlight.current = false;
    }
  }, [onUnauthorized]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const schedule = (delay: number) => {
      timer = window.setTimeout(() => {
        if (cancelled) return;
        if (document.hidden) {
          schedule(REFRESH_MS);
          return;
        }
        void fetchSnapshot().then(() => {
          if (!cancelled) schedule(nextPollDelay(failures.current, REFRESH_MS));
        });
      }, delay);
    };

    const initial = window.setTimeout(() => {
      void fetchSnapshot().then(() => {
        if (!cancelled) schedule(REFRESH_MS);
      });
    }, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(initial);
      if (timer !== undefined) window.clearTimeout(timer);
      abortRef.current?.abort();
    };
  }, [fetchSnapshot]);

  const runAction = async (grid: DashboardGridItem, action: 'start' | 'stop' | 'delete') => {
    if (action === 'start' && grid.start_blocked_reason) {
      setError(grid.start_blocked_reason);
      return;
    }
    setActionGridId(grid.grid_id);
    try {
      if (action === 'start') await api.startGrid(grid.grid_id);
      if (action === 'stop') await api.stopGrid(grid.grid_id);
      if (action === 'delete') {
        const isRunningOrPending = grid.status === 'running' || grid.status === 'starting' || grid.status === 'stopping';
        if (isRunningOrPending) {
          try {
            await api.stopGrid(grid.grid_id);
          } catch {
            // continue to delete if already stopping/stopped
          }
        }
        await api.deleteGrid(grid.grid_id, 'dashboard', true);
      }
      await fetchSnapshot();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        onUnauthorized();
        return;
      }
      setError(caught instanceof Error ? caught.message : '策略操作失敗');
    } finally {
      setActionGridId(null);
      setConfirm(null);
    }
  };

  // Filter and search grids
  const grids = snapshot?.grids;
  const filteredGrids = useMemo(() => {
    if (!grids) return [];
    return grids.filter((grid) => {
      const matchSearch =
        !searchQuery ||
        (grid.symbol && grid.symbol.toLowerCase().includes(searchQuery.toLowerCase())) ||
        grid.grid_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (grid.exchange && grid.exchange.toLowerCase().includes(searchQuery.toLowerCase()));

      if (!matchSearch) return false;

      if (filterMode === 'running') return grid.status === 'running';
      if (filterMode === 'live') return grid.mode === 'live';
      if (filterMode === 'paper') return grid.mode === 'paper';
      if (filterMode === 'stale') return grid.data_status === 'stale' || grid.status !== 'running';

      return true;
    });
  }, [grids, searchQuery, filterMode]);

  const aggregate = snapshot?.aggregate;
  const totalEquity = useMemo(() => {
    if (!grids) return null;
    let sum = 0;
    let count = 0;
    for (const g of grids) {
      if (g.equity != null) {
        sum += g.equity;
        count++;
      }
    }
    return count > 0 ? sum : null;
  }, [grids]);

  return (
    <main className="dashboard" aria-busy={loading}>
      {/* TopBar Telemetry */}
      <TopBar
        dataStatus={snapshot?.data_status}
        enginePid={health?.engine.pid ?? null}
        engineRunning={health?.engine.process_running}
        circuitOpen={health?.circuit_breaker.is_open}
        lastSync={snapshot?.generated_at ?? null}
        accountMode={snapshot?.account_mode}
        onRefresh={() => void fetchSnapshot()}
        loading={loading}
      />

      {/* Dashboard Title & Actions */}
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Quantitative Engine Console</p>
          <h2>量化網格總控台</h2>
        </div>
        {onCreateNew && (
          <button className="btn btn-primary" type="button" onClick={onCreateNew}>
            <PlusCircle size={16} />
            <span>建立網格策略</span>
          </button>
        )}
      </header>

      {/* Notices */}
      <div className="notice notice-warning" role="status">
        <AlertTriangle size={18} style={{ flexShrink: 0 }} />
        <span>
          <strong>模式說明：</strong>PAPER 模式採用公開行情與虛擬撮合；LIVE 為實盤對接，請確保系統環境變數 <code>GRID_LIVE_TRADING_ENABLED=1</code> 已正確配置。
        </span>
      </div>

      {error && error !== '尚未同步' && (
        <div className="notice notice-error" role="alert">
          <ShieldAlert size={18} style={{ flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      )}

      {/* Hero KPI Matrix */}
      <section className="kpi-grid" aria-label="網格總覽指標">
        <KpiCard
          label={snapshot?.account_mode === 'paper' ? 'PAPER 淨盈虧' : 'LIVE 淨盈虧'}
          value={aggregate ? money(aggregate.net_pnl) : '—'}
          hint={`網格毛利 ${aggregate ? money(aggregate.grid_profit ?? aggregate.realized_pnl) : '—'} · 浮動 ${aggregate ? money(aggregate.unrealized_pnl) : '—'}`}
          tone={(aggregate?.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'}
          icon={<DollarSign size={16} />}
        />

        <KpiCard
          label="累計交易量 / 套利"
          value={aggregate ? money(aggregate.volume, 0) : '—'}
          hint={`完成網格套利 ${aggregate ? number(aggregate.completed_cycles, 0) : '—'} 格`}
          icon={<TrendingUp size={16} />}
        />

        <KpiCard
          label="運行中策略隊列"
          value={aggregate ? `${aggregate.running_grids} / ${aggregate.total_grids}` : '—'}
          hint={aggregate ? `${aggregate.available_grids} 即時同步 · ${aggregate.stale_grids} 過期` : '載入中…'}
          icon={<Activity size={16} />}
        />

        <KpiCard
          label="總管轄權益估算"
          value={totalEquity != null ? money(totalEquity, 2) : '—'}
          hint={`共 ${snapshot?.grids.length ?? 0} 個策略實例`}
          icon={<Cpu size={16} />}
        />
      </section>

      {/* Paper Band */}
      {snapshot?.aggregate_paper && snapshot.aggregate_paper.total_grids > 0 && snapshot.account_mode !== 'paper' && (
        <section className="paper-band" aria-label="PAPER 模擬帳戶">
          <div className="paper-band-title">
            <span className="badge badge-paper">PAPER 模擬隊列</span>
            <span>（獨立運算，不計入上方 LIVE KPI）</span>
          </div>
          <div className="font-mono">
            淨盈虧: <strong className={snapshot.aggregate_paper.net_pnl >= 0 ? 'positive' : 'negative'}>{money(snapshot.aggregate_paper.net_pnl)}</strong>
            {' · '}
            運行: <strong>{snapshot.aggregate_paper.running_grids}/{snapshot.aggregate_paper.total_grids}</strong>
          </div>
        </section>
      )}

      {/* Toolbar: Search, Filters, View Modes */}
      <section className="dashboard-toolbar" aria-label="策略篩選與檢視控制">
        <div className="toolbar-left">
          <div className="search-input-wrapper">
            <Search size={15} />
            <input
              className="search-input"
              type="text"
              placeholder="搜尋交易對、ID、交易所…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <div className="filter-pills" role="tablist">
            <button
              className={`filter-pill ${filterMode === 'all' ? 'active' : ''}`}
              type="button"
              onClick={() => setFilterMode('all')}
            >
              全部 ({snapshot?.grids.length ?? 0})
            </button>
            <button
              className={`filter-pill ${filterMode === 'running' ? 'active' : ''}`}
              type="button"
              onClick={() => setFilterMode('running')}
            >
              運行中 ({snapshot?.grids.filter((g) => g.status === 'running').length ?? 0})
            </button>
            <button
              className={`filter-pill ${filterMode === 'live' ? 'active' : ''}`}
              type="button"
              onClick={() => setFilterMode('live')}
            >
              LIVE 實盤
            </button>
            <button
              className={`filter-pill ${filterMode === 'paper' ? 'active' : ''}`}
              type="button"
              onClick={() => setFilterMode('paper')}
            >
              PAPER 模擬
            </button>
            <button
              className={`filter-pill ${filterMode === 'stale' ? 'active' : ''}`}
              type="button"
              onClick={() => setFilterMode('stale')}
            >
              已停止/待處理
            </button>
          </div>
        </div>

        <div className="toolbar-right">
          <div className="view-toggle">
            <button
              className={`view-btn ${viewMode === 'grid' ? 'active' : ''}`}
              type="button"
              onClick={() => setViewMode('grid')}
              title="卡片視覺檢視"
              aria-label="卡片檢視"
            >
              <LayoutGrid size={16} />
            </button>
            <button
              className={`view-btn ${viewMode === 'table' ? 'active' : ''}`}
              type="button"
              onClick={() => setViewMode('table')}
              title="高密度表格檢視"
              aria-label="表格檢視"
            >
              <List size={16} />
            </button>
          </div>
        </div>
      </section>

      {/* Loading & Empty States */}
      {loading && !snapshot && (
        <div className="glass-panel" style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          <Activity size={24} className="spin" style={{ margin: '0 auto 0.75rem' }} />
          <p>正在載入量化策略快照…</p>
        </div>
      )}

      {!loading && filteredGrids.length === 0 && (
        <div className="glass-panel" style={{ padding: '3.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          <p style={{ fontSize: '1rem', marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
            {searchQuery ? `找不到與「${searchQuery}」相符的策略。` : '目前沒有符合篩選條件的網格策略。'}
          </p>
          <small>您可以點擊上方「建立網格策略」配置新的量化交易實例。</small>
        </div>
      )}

      {/* Grid Cards or Table View */}
      {viewMode === 'grid' ? (
        <section className="strategy-grid" aria-label="網格策略列表">
          {filteredGrids.map((grid) => (
            <GridCard
              key={grid.grid_id}
              grid={grid}
              busy={actionGridId === grid.grid_id}
              onOpen={() => onOpenGrid(grid.grid_id)}
              onStart={() => void runAction(grid, 'start')}
              onStop={() => setConfirm({ grid, action: 'stop' })}
              onDelete={() => setConfirm({ grid, action: 'delete' })}
            />
          ))}
        </section>
      ) : (
        filteredGrids.length > 0 && (
          <GridTable
            grids={filteredGrids}
            actionGridId={actionGridId}
            onOpen={onOpenGrid}
            onStart={(grid) => void runAction(grid, 'start')}
            onStop={(grid) => setConfirm({ grid, action: 'stop' })}
            onDelete={(grid) => setConfirm({ grid, action: 'delete' })}
          />
        )
      )}

      {/* Confirmation Modal */}
      {confirm && (
        <ConfirmDialog
          title={
            confirm.action === 'stop'
              ? '停止策略運行'
              : confirm.grid.status === 'running' || confirm.grid.status === 'starting' || confirm.grid.status === 'stopping'
              ? '停止並刪除策略'
              : '刪除策略配置'
          }
          body={
            confirm.action === 'stop'
              ? `確定要停止 ${confirm.grid.symbol ?? confirm.grid.grid_id} 嗎？系統將撤銷所有掛單並停止運行，策略配置將保留在看板中（狀態變更為已停止）。此操作將寫入審計日誌。`
              : confirm.grid.status === 'running' || confirm.grid.status === 'starting' || confirm.grid.status === 'stopping'
              ? `確定要停止並刪除 ${confirm.grid.symbol ?? confirm.grid.grid_id} 嗎？此策略目前正在運行中，系統將先撤銷所有掛單、停止運行，並將配置檔案歸檔刪除。`
              : `確定要刪除 ${confirm.grid.symbol ?? confirm.grid.grid_id} 嗎？此策略已停止，關聯 YAML 將被歸檔刪除。`
          }
          confirmLabel={
            confirm.action === 'stop'
              ? '確認停止掛單（保留配置）'
              : confirm.grid.status === 'running' || confirm.grid.status === 'starting' || confirm.grid.status === 'stopping'
              ? '確認停止並刪除'
              : '確認刪除'
          }
          danger={confirm.action === 'delete'}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void runAction(confirm.grid, confirm.action)}
        />
      )}
    </main>
  );
};

export default Dashboard;
