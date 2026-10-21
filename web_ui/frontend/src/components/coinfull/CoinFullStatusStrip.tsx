import React from 'react';
import { CalendarClock, Play, RefreshCw, Square } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import { formatTaipeiWithZone, formatUsd } from './format';

interface Props {
  controller: CoinFullPrimeController;
}

export const CoinFullStatusStrip: React.FC<Props> = ({ controller }) => {
  const {
    statusInfo,
    scheduleAccount,
    scheduleMode,
    scheduleSizing,
    scheduleAligned,
    scheduleReasons,
    manualSizing,
    togglingScheduler,
    rebalancing,
    actions,
  } = controller;
  const isRunning = Boolean(statusInfo?.is_running);
  const blocked = !manualSizing.affordable;
  const nextRun = statusInfo?.next_scheduled_run_utc;

  return (
    <section className={`cfp-panel cfp-status-strip ${scheduleAligned ? 'is-aligned' : 'is-misaligned'}`} aria-label="背景排程狀態">
      <div className="cfp-status-main">
        <span className={`status-dot ${isRunning ? 'is-live' : 'is-idle'}`} aria-hidden="true" />
        <strong>{isRunning ? '排程運行中' : '排程待機'}</strong>
        <span className="cfp-status-sep" aria-hidden="true">·</span>
        <span className="cfp-status-item">
          <CalendarClock size={14} aria-hidden="true" />
          {isRunning && nextRun ? `下次 ${formatTaipeiWithZone(nextRun)}` : '尚未排程'}
        </span>
        <span className="cfp-status-sep" aria-hidden="true">·</span>
        <span className="cfp-status-item">
          目標 <strong>{scheduleAccount.name}</strong>
          <span className={`badge badge-${scheduleMode.tone}`}>{scheduleMode.label}</span>
        </span>
        <span className="cfp-status-sep" aria-hidden="true">·</span>
        <span className="cfp-status-item">
          單幣約 <strong>{scheduleSizing.perSymbolNotional === null ? '—' : formatUsd(scheduleSizing.perSymbolNotional)}</strong>
        </span>
      </div>

      <div className="cfp-status-actions">
        <span className={`badge ${scheduleAligned ? 'badge-success' : 'badge-warning'}`}>
          {scheduleAligned ? '排程已同步' : '排程未同步'}
        </span>
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={actions.requestApplySchedule}
          disabled={scheduleAligned || rebalancing || blocked}
          title={blocked ? '本金超過可用保證金，請先調整倉位' : undefined}
        >
          <RefreshCw size={14} aria-hidden="true" />
          <span>套用目前設定</span>
        </button>
        <button
          type="button"
          className={`btn btn-sm ${isRunning ? 'btn-outline' : 'btn-primary'}`}
          onClick={() => void actions.requestSchedulerToggle()}
          disabled={togglingScheduler || (!isRunning && blocked)}
          title={!isRunning && blocked ? '本金超過可用保證金，請先調整倉位' : undefined}
        >
          {isRunning ? <Square size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
          <span>{togglingScheduler ? '處理中…' : isRunning ? '停止排程' : '啟動排程'}</span>
        </button>
      </div>

      {!scheduleAligned && scheduleReasons.length > 0 && (
        <ul className="cfp-status-reasons">
          {scheduleReasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </section>
  );
};