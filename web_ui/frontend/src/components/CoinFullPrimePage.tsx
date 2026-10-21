import React from 'react';
import { Activity, AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import ConfirmDialog, { type ConfirmDialogDetail } from './ConfirmDialog';
import { useCoinFullPrime, type CoinFullTab } from './coinfull/useCoinFullPrime';
import { CoinFullHeader } from './coinfull/CoinFullHeader';
import { CoinFullControls } from './coinfull/CoinFullControls';
import { CoinFullStatusStrip } from './coinfull/CoinFullStatusStrip';
import { CoinFullSummary } from './coinfull/CoinFullSummary';
import { CoinFullPositions } from './coinfull/CoinFullPositions';
import { CoinFullHistory } from './coinfull/CoinFullHistory';
import { RankingsToolbar } from './coinfull/RankingsToolbar';
import { RankingsTable } from './coinfull/RankingsTable';
import { RankingsHelp } from './coinfull/RankingsHelp';
import { formatTaipei, formatUsd } from './coinfull/format';

interface CoinFullPrimePageProps {
  onUnauthorized?: () => void;
  onGoToRankings?: () => void;
}

const TAB_LABELS: Record<CoinFullTab, string> = {
  positions: '持倉',
  rankings: '排行',
  history: '歷史',
};

export const CoinFullPrimePage: React.FC<CoinFullPrimePageProps> = ({ onUnauthorized, onGoToRankings }) => {
  const controller = useCoinFullPrime(onUnauthorized);
  const {
    loading,
    refreshing,
    rebalancing,
    error,
    notice,
    clearError,
    clearNotice,
    rankResult,
    currentAccount,
    selectedAccount,
    dryRunMode,
    sizingMode,
    manualSizing,
    targetCount,
    shortList,
    isBenchmarkHedge,
    excludeTradfi,
    activeTab,
    setActiveTab,
    managedStats,
    historyBatches,
    universeSize,
    filterTab,
    setFilter,
    searchQuery,
    setSearchQuery,
    showDisabledFactors,
    setShowDisabledFactors,
    displayedItems,
    actions,
    dialog,
    closeDialog,
    confirmDialog,
    scheduleAccount,
    scheduleMode,
    scheduleSizing,
  } = controller;

  if (loading && !rankResult) {
    return (
      <div className="coinfull-container cfp-page">
        <div className="cfp-panel cfp-loading">
          <Activity size={30} className="spin text-accent" aria-hidden="true" />
          <h3>載入 CoinFull Prime 截面排行</h3>
          <p>正在同步交易所合約特徵與因子評分。</p>
        </div>
      </div>
    );
  }

  const counts = {
    baskets: (rankResult?.long_list?.length ?? 0) + (rankResult?.short_list?.length ?? 0),
    long: rankResult?.long_list?.length ?? 0,
    short: rankResult?.short_list?.length ?? 0,
    all: rankResult?.all_rankings?.length ?? 0,
  };
  const orderCount = isBenchmarkHedge ? targetCount + Math.max(1, shortList.length) : targetCount * 2;
  const liveTarget = currentAccount.environment === 'live' && !dryRunMode;

  const dialogView = (() => {
    if (!dialog) return null;
    if (dialog.kind === 'live-mode') {
      return {
        title: '切換為真實發單模式',
        body: '手動換倉將在確認後送出真實訂單。背景排程不會跟著變更，需另外按「套用至排程」。',
        confirmLabel: '切換為真實發單',
        danger: true,
        details: [
          { label: '帳號', value: currentAccount.name },
          { label: '目前模式', value: '僅試算' },
          { label: '切換後', value: '真實發單', tone: 'negative' as const },
        ] satisfies ConfirmDialogDetail[],
        note: '切換後仍不會自動送出訂單；每次換倉都需要再次確認。',
        requireText: undefined,
      };
    }
    if (dialog.kind === 'rebalance') {
      return {
        title: dryRunMode ? '確認執行模擬換倉' : '確認送出真實訂單',
        body: dryRunMode
          ? '系統會依目前排行計算訂單並寫入紀錄，不會送到交易所。'
          : '系統會向交易所送出真實訂單並調整保證金倉位。請確認帳號、本金與模式。',
        confirmLabel: dryRunMode ? '執行模擬換倉' : '送出真實訂單',
        danger: !dryRunMode,
        details: [
          { label: '帳號', value: currentAccount.name },
          { label: '模式', value: dryRunMode ? '僅試算' : '真實發單', tone: (dryRunMode ? 'neutral' : 'negative') as 'neutral' | 'negative' },
          { label: '倉位模式', value: sizingMode === 'fixed' ? '固定本金' : '餘額百分比' },
          { label: '本金', value: formatUsd(manualSizing.capital) },
          { label: '總名義曝險', value: formatUsd(manualSizing.grossExposure) },
          { label: '單幣約', value: formatUsd(manualSizing.perSymbolNotional) },
          { label: '預估訂單', value: `${orderCount} 筆` },
          { label: 'TradFi', value: excludeTradfi ? '排除' : '包含' },
        ] satisfies ConfirmDialogDetail[],
        note: '外部網格與 DCA 持倉會自動隔離，不會被平倉；只有 CoinFull 籃子中退榜的標的會平倉。',
        requireText: !dryRunMode && liveTarget ? 'LIVE' : undefined,
      };
    }
    if (dialog.kind === 'apply-schedule') {
      return {
        title: '套用設定至背景排程',
        body: '這會改寫每週自動換倉的帳號、模式與倉位規模。手動設定不會自動同步，必須在此確認。',
        confirmLabel: '套用至排程',
        danger: liveTarget,
        details: [
          { label: '排程目標', value: selectedAccount === scheduleAccount.id ? currentAccount.name : `${currentAccount.name}（目前排程為 ${scheduleAccount.name}）` },
          { label: '排程模式', value: dryRunMode ? '僅試算' : '真實發單', tone: (dryRunMode ? 'neutral' : 'negative') as 'neutral' | 'negative' },
          { label: '倉位模式', value: sizingMode === 'fixed' ? '固定本金' : '餘額百分比' },
          { label: '本金', value: formatUsd(manualSizing.capital) },
          { label: '單幣約', value: formatUsd(manualSizing.perSymbolNotional) },
          { label: 'TradFi', value: excludeTradfi ? '排除' : '包含' },
        ] satisfies ConfirmDialogDetail[],
        note: '排程啟動後會在下次排程時間自動執行，不會立即下單。',
        requireText: liveTarget ? 'LIVE' : undefined,
      };
    }
    return {
      title: '啟動定時自動換倉',
      body: '啟動後系統會依排程自動執行換倉，使用下列帳號、模式與倉位設定。',
      confirmLabel: '啟動排程',
      danger: liveTarget,
      details: [
        { label: '帳號', value: currentAccount.name },
        { label: '模式', value: dryRunMode ? '僅試算' : '真實發單', tone: (dryRunMode ? 'neutral' : 'negative') as 'neutral' | 'negative' },
        { label: '本金', value: formatUsd(manualSizing.capital) },
        { label: '單幣約', value: formatUsd(manualSizing.perSymbolNotional) },
        { label: '排程', value: scheduleSizing.capital === null ? '尚未設定' : scheduleMode.label },
      ] satisfies ConfirmDialogDetail[],
      note: '啟動前請確認排程目標與模式；停止排程不會自動平倉。',
      requireText: liveTarget ? 'LIVE' : undefined,
    };
  })();

  return (
    <div className="coinfull-container cfp-page">
      <CoinFullHeader controller={controller} onGoToRankings={onGoToRankings} />

      {error && (
        <div className="alert-banner alert-danger" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <span>{error}</span>
          <button type="button" className="close-btn" onClick={clearError} aria-label="關閉錯誤訊息">×</button>
        </div>
      )}

      {notice && (
        <div className={`alert-banner alert-${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'} aria-live="polite">
          {notice.kind === 'success' ? <CheckCircle2 size={18} aria-hidden="true" /> : notice.kind === 'error' ? <XCircle size={18} aria-hidden="true" /> : <Info size={18} aria-hidden="true" />}
          <span>{notice.text}</span>
          <button type="button" className="close-btn" onClick={clearNotice} aria-label="關閉提示">×</button>
        </div>
      )}

      <CoinFullControls controller={controller} />
      <CoinFullStatusStrip controller={controller} />
      <CoinFullSummary controller={controller} onOpenTab={setActiveTab} />

      <nav className="cfp-main-tabs" role="tablist" aria-label="CoinFull Prime 檢視">
        {(['positions', 'rankings', 'history'] as CoinFullTab[]).map((tab) => {
          const badge = tab === 'positions' ? managedStats.count : tab === 'rankings' ? universeSize : historyBatches.length;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              id={`cfp-tab-${tab}`}
              aria-selected={activeTab === tab}
              aria-controls={`cfp-panel-${tab}`}
              className={activeTab === tab ? 'is-active' : ''}
              onClick={() => setActiveTab(tab)}
            >
              {TAB_LABELS[tab]} <span>{badge}</span>
            </button>
          );
        })}
        <span className="cfp-tabs-spacer" />
        {activeTab === 'rankings' && (
          <span className="cfp-tabs-hint">
            {searchQuery.trim() ? `在全部 ${universeSize} 幣中搜尋` : `資料 ${rankResult?.timestamp ? formatTaipei(rankResult.timestamp) : '—'}`}
          </span>
        )}
      </nav>

      <div
        id={`cfp-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`cfp-tab-${activeTab}`}
        className="cfp-tab-panel"
      >
        {activeTab === 'positions' && <CoinFullPositions controller={controller} />}

        {activeTab === 'rankings' && (
          <>
            <RankingsToolbar
              filterTab={filterTab}
              onFilter={setFilter}
              counts={counts}
              universeSize={universeSize}
              excludeTradfi={excludeTradfi}
              onToggleTradfi={(next) => void actions.toggleExcludeTradfi(next)}
              searchQuery={searchQuery}
              onSearch={setSearchQuery}
              showDisabledFactors={showDisabledFactors}
              onToggleDisabled={setShowDisabledFactors}
              busy={refreshing}
            />
            <section className="cfp-panel cfp-rankings">
              <RankingsTable
                items={displayedItems}
                isBenchmarkHedge={isBenchmarkHedge}
                grossExposure={manualSizing.grossExposure}
                sizing={manualSizing}
                showDisabledFactors={showDisabledFactors}
                universeSize={universeSize}
              />
            </section>
            <RankingsHelp />
          </>
        )}

        {activeTab === 'history' && <CoinFullHistory controller={controller} />}
      </div>

      {rebalancing && (
        <div className="cfp-busy-overlay" role="status" aria-live="assertive">
          <Activity size={18} className="spin" aria-hidden="true" />
          <span>換倉執行中，請勿關閉頁面。</span>
        </div>
      )}

      {dialogView && (
        <ConfirmDialog
          title={dialogView.title}
          body={dialogView.body}
          confirmLabel={dialogView.confirmLabel}
          danger={dialogView.danger}
          details={dialogView.details}
          note={dialogView.note}
          requireText={dialogView.requireText}
          onConfirm={confirmDialog}
          onCancel={closeDialog}
        />
      )}
    </div>
  );
};