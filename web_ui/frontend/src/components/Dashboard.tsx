import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { api, type DashboardGridItem, type DashboardSnapshot, type SystemHealth } from '../services/api';
import { ApiError } from '../utils/auth';
import { money, nextPollDelay, number } from '../utils/format';
import ConfirmDialog from './ConfirmDialog';
import GridCard from './GridCard';
import KpiCard from './KpiCard';
import TopBar from './TopBar';

const REFRESH_MS = 15_000;

interface DashboardProps {
  onOpenGrid: (gridId: string) => void;
  onUnauthorized: () => void;
}

const Dashboard = ({ onOpenGrid, onUnauthorized }: DashboardProps) => {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionGridId, setActionGridId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ grid: DashboardGridItem; action: 'stop' | 'delete' } | null>(null);
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
      setError(caught instanceof Error ? caught.message : '無法讀取網格總覽');
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
      if (action === 'delete') await api.deleteGrid(grid.grid_id);
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

  const aggregate = snapshot?.aggregate;
  return (
    <main className="dashboard" aria-busy={loading}>
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
      <header className="dashboard-header">
        <div>
          <h2>策略總覽</h2>
          <p className="muted">只顯示已註冊策略；未知值顯示 —，401 與尚未同步分開處理。</p>
        </div>
      </header>

      <div className="notice notice-warning safety-banner" role="status">
        <AlertTriangle size={18} />
        PAPER 使用模擬盤。未標模式的既有 YAML 與 LIVE 啟動仍是實盤，且 LIVE 需要 GRID_LIVE_TRADING_ENABLED。
      </div>
      {error && error !== '尚未同步' && <div className="notice notice-error" role="alert"><AlertTriangle size={18} />{error}</div>}

      <section className="kpi-grid" aria-label="網格總覽指標">
        <KpiCard label={snapshot?.account_mode === 'paper' ? 'PAPER 淨盈虧' : 'LIVE 淨盈虧'} value={aggregate ? money(aggregate.net_pnl) : '—'} hint={`已實現 ${aggregate ? money(aggregate.realized_pnl) : '—'} / 未實現 ${aggregate ? money(aggregate.unrealized_pnl) : '—'}`} tone={(aggregate?.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'} />
        <KpiCard label="累計交易量" value={aggregate ? money(aggregate.volume, 0) : '—'} hint={`完成網格 ${aggregate ? number(aggregate.completed_cycles, 0) : '—'} 格`} />
        <KpiCard label="運行狀態" value={aggregate ? `${aggregate.running_grids}/${aggregate.total_grids}` : '—'} hint={aggregate ? `${aggregate.available_grids} 已同步 · ${aggregate.stale_grids} 過期` : '載入中'} />
      </section>
      {snapshot?.aggregate_paper && snapshot.aggregate_paper.total_grids > 0 && snapshot.account_mode !== 'paper' ? (
        <section className="paper-band" aria-label="PAPER 帳戶">
          <strong>PAPER 模擬盤（不計入上方 LIVE KPI）</strong>
          <span>淨盈虧 {money(snapshot.aggregate_paper.net_pnl)} · 運行 {snapshot.aggregate_paper.running_grids}/{snapshot.aggregate_paper.total_grids}</span>
        </section>
      ) : null}

      {loading && !snapshot ? <div className="dashboard-empty">正在載入策略快照…</div> : null}
      {!loading && snapshot?.grids.length === 0 ? <div className="dashboard-empty">目前沒有已註冊策略。建立策略後會出現在這裡，不會列出 archived 或歷史 YAML。</div> : null}

      <section className="strategy-grid" aria-label="網格策略">
        {snapshot?.grids.map(grid => (
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
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'stop' ? '停止策略' : '刪除策略'}
          body={confirm.action === 'stop'
            ? `停止 ${confirm.grid.symbol ?? confirm.grid.grid_id} 會取消掛單，預設不平倉。此操作會寫入稽核日誌。`
            : `刪除 ${confirm.grid.symbol ?? confirm.grid.grid_id} 僅允許已停止的策略，YAML 會歸檔。`}
          confirmLabel={confirm.action === 'stop' ? '確認停止並撤單' : '確認刪除'}
          danger={confirm.action === 'delete'}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void runAction(confirm.grid, confirm.action)}
        />
      )}
    </main>
  );
};

export default Dashboard;
