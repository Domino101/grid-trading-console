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
    type DcaSnapshotItem,
    type DcaSnapshotResponse,
    type SystemHealth,
} from '../services/api';
import { ApiError } from '../utils/auth';
import { money, nextPollDelay, number } from '../utils/format';
import { isDcaRunningStatus } from '../utils/dca';
import ConfirmDialog from './ConfirmDialog';
import DcaCard from './DcaCard';
import DcaTable from './DcaTable';
import DcaDetail from './DcaDetail';
import KpiCard from './KpiCard';
import TopBar from './TopBar';

const REFRESH_MS = 15_000;

type FilterMode = 'all' | 'running' | 'live' | 'paper' | 'stale';

interface DcaDashboardProps {
    onCreateNew?: () => void;
    onUnauthorized: () => void;
}

// DCA 分頁（Phase 5）：與網格分頁完全隔離——只呼叫 /api/v1/dca/* 與 /api/v1/system/health
// （稽核 5.2）；總覽資料一律來自單次 snapshot（稽核 5.4）。
const DcaDashboard: React.FC<DcaDashboardProps> = ({ onCreateNew, onUnauthorized }) => {
    const [snapshot, setSnapshot] = useState<DcaSnapshotResponse | null>(null);
    const [health, setHealth] = useState<SystemHealth | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionBotId, setActionBotId] = useState<string | null>(null);
    const [confirm, setConfirm] = useState<{ bot: DcaSnapshotItem; action: 'stop' | 'delete' } | null>(null);
    const [detailBot, setDetailBot] = useState<DcaSnapshotItem | null>(null);
    const [now, setNow] = useState(() => Date.now());

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
                api.getDcaSnapshot(controller.signal),
                api.getSystemHealth(controller.signal).catch(() => null),
            ]);
            setSnapshot(result);
            setNow(Date.now());
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
            setError(caught instanceof Error ? caught.message : '無法讀取 DCA 總覽快照');
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

    const runAction = async (bot: DcaSnapshotItem, action: 'start' | 'stop' | 'delete') => {
        if (action === 'start' && bot.start_blocked_reason) {
            setError(bot.start_blocked_reason);
            return;
        }
        setActionBotId(bot.bot_id);
        try {
            if (action === 'start') await api.startDca(bot.bot_id);
            if (action === 'stop') await api.stopDca(bot.bot_id);
            if (action === 'delete') {
                if (isDcaRunningStatus(bot.status)) {
                    try {
                        await api.stopDca(bot.bot_id);
                    } catch {
                        // 已在停止/停止中時直接繼續刪除
                    }
                }
                await api.deleteDca(bot.bot_id, 'dashboard', true);
                if (detailBot?.bot_id === bot.bot_id) setDetailBot(null);
            }
            await fetchSnapshot();
        } catch (caught) {
            if (caught instanceof ApiError && caught.isUnauthorized) {
                onUnauthorized();
                return;
            }
            setError(caught instanceof Error ? caught.message : 'DCA 操作失敗');
        } finally {
            setActionBotId(null);
            setConfirm(null);
        }
    };

    const filteredBots = useMemo(() => {
        const bots = snapshot?.bots ?? [];
        return bots.filter((bot) => {
            const matchSearch =
                !searchQuery ||
                (bot.symbol && bot.symbol.toLowerCase().includes(searchQuery.toLowerCase())) ||
                bot.bot_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
                (bot.exchange && bot.exchange.toLowerCase().includes(searchQuery.toLowerCase()));

            if (!matchSearch) return false;

            if (filterMode === 'running') return isDcaRunningStatus(bot.status);
            if (filterMode === 'live') return bot.mode === 'live';
            if (filterMode === 'paper') return bot.mode === 'paper';
            if (filterMode === 'stale') return bot.data_status === 'stale' || !isDcaRunningStatus(bot.status);

            return true;
        });
    }, [snapshot, searchQuery, filterMode]);

    const aggregate = snapshot?.aggregate;
    const runningCount = useMemo(
        () => (snapshot?.bots ?? []).filter((b) => isDcaRunningStatus(b.status)).length,
        [snapshot],
    );

    const detailCurrent = detailBot
        ? (snapshot?.bots ?? []).find((b) => b.bot_id === detailBot.bot_id) ?? detailBot
        : null;

    // Phase 7：選取機器人後切換為完整詳情工作台（六分頁）
    if (detailCurrent) {
        return (
            <DcaDetail
              bot={detailCurrent}
              onBack={() => setDetailBot(null)}
              onUnauthorized={onUnauthorized}
            />
        );
    }

    return (
        <main className="dashboard dca-dashboard" aria-busy={loading}>
            {/* TopBar Telemetry（複用網格遙測；標題改為 DCA） */}
            <TopBar
              title="DCA 機器人"
              dataStatus={snapshot?.data_status}
              enginePid={health?.engine.pid ?? null}
              engineRunning={health?.engine.process_running}
              circuitOpen={health?.circuit_breaker.is_open}
              lastSync={snapshot?.generated_at ?? null}
              accountMode={snapshot?.account_mode}
              onRefresh={() => void fetchSnapshot()}
              loading={loading}
            />

            {/* 標題與動作 */}
            <header className="dashboard-header">
                <div>
                    <p className="eyebrow">Dollar-Cost Averaging Console</p>
                    <h2>DCA 機器人大盤</h2>
                </div>
                {onCreateNew && (
                    <button className="btn btn-primary" type="button" onClick={onCreateNew}>
                        <PlusCircle size={16} />
                        <span>建立 DCA 機器人</span>
                    </button>
                )}
            </header>

            {/* armed 語意說明（D-1：平常不掛單，UI 顯示監控中） */}
            <div className="notice notice-warning" role="status">
                <AlertTriangle size={18} style={{ flexShrink: 0 }} />
                <span>
                    <strong>armed 語意：</strong>
                    DCA 機器人啟動後先進入「監控中」狀態，入場條件成立前交易所端不會有任何掛單；
                    觸發後才下基礎單與安全單階梯。停止操作僅撤銷掛單、不平倉。
                </span>
            </div>

            {error && (
                <div className="notice notice-error" role="alert">
                    <ShieldAlert size={18} style={{ flexShrink: 0 }} />
                    <span>{error}</span>
                </div>
            )}

            {/* Hero KPI Matrix（LIVE/PAPER 雙重編碼：標籤文字含模式，稽核 5.9） */}
            <section className="kpi-grid" aria-label="DCA 總覽指標">
                <KpiCard
                  label={snapshot?.account_mode === 'paper' ? 'PAPER 淨盈虧' : 'LIVE 淨盈虧'}
                  value={aggregate ? money(aggregate.net_pnl) : '—'}
                  hint={
                    aggregate
                        ? `已實現 ${money(aggregate.realized_pnl)} · 浮動 ${money(aggregate.unrealized_pnl)}`
                        : '載入中…'
                  }
                  tone={aggregate && aggregate.net_pnl >= 0 ? 'positive' : 'negative'}
                  icon={<DollarSign size={16} />}
                />

                <KpiCard
                  label="安全單成交 / 循環"
                  value={aggregate ? `${number(aggregate.filled_safety_orders, 0)} 張` : '—'}
                  hint={aggregate ? `完成循環 ${number(aggregate.completed_cycles, 0)} 輪` : '載入中…'}
                  icon={<TrendingUp size={16} />}
                />

                <KpiCard
                  label="運行中 DCA 隊列"
                  value={aggregate ? `${aggregate.running_bots} / ${aggregate.total_bots}` : '—'}
                  hint={
                    aggregate
                        ? `${aggregate.available_bots} 即時同步 · ${aggregate.stale_bots} 過期`
                        : '載入中…'
                  }
                  icon={<Activity size={16} />}
                />

                <KpiCard
                  label="成本手續費"
                  value={aggregate ? money(aggregate.fees) : '—'}
                  hint={aggregate ? `已實現盈虧 ${money(aggregate.realized_pnl)}` : '載入中…'}
                  icon={<Cpu size={16} />}
                />
            </section>

            {/* Paper Band（與網格一致：PAPER 獨立運算，不計入 LIVE KPI） */}
            {snapshot?.aggregate_paper && snapshot.aggregate_paper.total_bots > 0 && snapshot.account_mode !== 'paper' && (
                <section className="paper-band" aria-label="PAPER 模擬帳戶">
                    <div className="paper-band-title">
                        <span className="badge badge-paper">PAPER 模擬隊列</span>
                        <span>（獨立運算，不計入上方 LIVE KPI）</span>
                    </div>
                    <div className="font-mono">
                        淨盈虧: <strong className={snapshot.aggregate_paper.net_pnl >= 0 ? 'positive' : 'negative'}>{money(snapshot.aggregate_paper.net_pnl)}</strong>
                        {' · '}
                        運行: <strong>{snapshot.aggregate_paper.running_bots}/{snapshot.aggregate_paper.total_bots}</strong>
                    </div>
                </section>
            )}

            {/* Toolbar: Search, Filters, View Modes */}
            <section className="dashboard-toolbar" aria-label="DCA 篩選與檢視控制">
                <div className="toolbar-left">
                    <div className="search-input-wrapper">
                        <Search size={15} />
                        <input
                          className="search-input"
                          type="text"
                          placeholder="搜尋交易對、ID、交易所…"
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          aria-label="搜尋 DCA 機器人"
                        />
                    </div>

                    <div className="filter-pills" role="tablist">
                        <button
                          className={`filter-pill ${filterMode === 'all' ? 'active' : ''}`}
                          type="button"
                          onClick={() => setFilterMode('all')}
                        >
                            全部 ({snapshot?.bots.length ?? 0})
                        </button>
                        <button
                          className={`filter-pill ${filterMode === 'running' ? 'active' : ''}`}
                          type="button"
                          onClick={() => setFilterMode('running')}
                        >
                            運行中 ({runningCount})
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
                    <p>正在載入 DCA 機器人快照…</p>
                </div>
            )}

            {!loading && filteredBots.length === 0 && (
                <div className="glass-panel" style={{ padding: '3.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                    <p style={{ fontSize: '1rem', marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                        {searchQuery ? `找不到與「${searchQuery}」相符的 DCA 機器人。` : '目前沒有符合篩選條件的 DCA 機器人。'}
                    </p>
                    <small>點擊上方「建立 DCA 機器人」配置新的 DCA 策略實例。</small>
                </div>
            )}

            {/* 卡片或表格 */}
            {viewMode === 'grid' ? (
                <section className="strategy-grid" aria-label="DCA 機器人列表">
                    {filteredBots.map((bot) => (
                        <DcaCard
                          key={bot.bot_id}
                          bot={bot}
                          busy={actionBotId === bot.bot_id}
                          now={now}
                          onOpen={() => setDetailBot(bot)}
                          onStart={() => void runAction(bot, 'start')}
                          onStop={() => setConfirm({ bot, action: 'stop' })}
                          onDelete={() => setConfirm({ bot, action: 'delete' })}
                        />
                    ))}
                </section>
            ) : (
                filteredBots.length > 0 && (
                    <DcaTable
                      bots={filteredBots}
                      actionBotId={actionBotId}
                      onOpen={(botId) => {
                          const target = (snapshot?.bots ?? []).find((b) => b.bot_id === botId);
                          if (target) setDetailBot(target);
                      }}
                      onStart={(bot) => void runAction(bot, 'start')}
                      onStop={(bot) => setConfirm({ bot, action: 'stop' })}
                      onDelete={(bot) => setConfirm({ bot, action: 'delete' })}
                    />
                )
            )}

            {/* 危險操作二次確認（稽核 5.12：含 bot 名稱、mode、撤單但不平倉） */}
            {confirm && (
                <ConfirmDialog
                  title={
                      confirm.action === 'stop'
                          ? '停止 DCA 機器人'
                          : isDcaRunningStatus(confirm.bot.status)
                          ? '停止並刪除 DCA 機器人'
                          : '刪除 DCA 配置'
                  }
                  body={
                      confirm.action === 'stop'
                          ? `確定要停止 ${confirm.bot.symbol ?? confirm.bot.bot_id}（${confirm.bot.mode.toUpperCase()} 模式）嗎？系統將撤銷所有掛單（撤單但不平倉，現有部位將保留），機器人狀態轉為已停止。此操作將寫入審計日誌。`
                          : isDcaRunningStatus(confirm.bot.status)
                          ? `確定要停止並刪除 ${confirm.bot.symbol ?? confirm.bot.bot_id}（${confirm.bot.mode.toUpperCase()} 模式）嗎？此機器人目前運行中，系統將先撤銷所有掛單（撤單但不平倉）、停止運行，並將配置檔案歸檔刪除。`
                          : `確定要刪除 ${confirm.bot.symbol ?? confirm.bot.bot_id}（${confirm.bot.mode.toUpperCase()} 模式）嗎？此機器人已停止，配置檔案將被歸檔刪除。`
                  }
                  confirmLabel={
                      confirm.action === 'stop'
                          ? '確認停止（撤單不平倉）'
                          : isDcaRunningStatus(confirm.bot.status)
                          ? '確認停止並刪除'
                          : '確認刪除'
                  }
                  danger={confirm.action === 'delete'}
                  onCancel={() => setConfirm(null)}
                  onConfirm={() => void runAction(confirm.bot, confirm.action)}
                />
            )}
        </main>
    );
};

export default DcaDashboard;
