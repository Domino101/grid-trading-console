import React from 'react';
import { RefreshCcw, ShieldCheck, ShieldAlert, Cpu, Activity } from 'lucide-react';

interface TopBarProps {
  dataStatus?: string;
  enginePid?: number | null;
  engineRunning?: boolean;
  circuitOpen?: boolean;
  lastSync?: number | null;
  accountMode?: string;
  onRefresh: () => void;
  loading?: boolean;
}

const TopBar: React.FC<TopBarProps> = ({
  dataStatus,
  enginePid,
  engineRunning,
  circuitOpen,
  lastSync,
  accountMode,
  onRefresh,
  loading,
}) => {
  const mode = accountMode || 'unknown';

  return (
    <header className="topbar" aria-label="系統即時遙測看板">
      <div className="topbar-left">
        <div className="topbar-title">
          <span className={`status-dot ${dataStatus === 'live' ? 'is-live' : dataStatus === 'stale' ? 'is-warning' : ''}`} />
          <span>網格總控台</span>
        </div>
        <span className={`badge badge-${mode}`}>
          {mode.toUpperCase()}
        </span>
      </div>

      <div className="topbar-telemetry">
        <div className="telemetry-item" title="交易引擎運行狀態">
          <Cpu size={14} />
          <span>引擎:</span>
          <span className={`telemetry-dot ${engineRunning ? 'online' : 'warning'}`} />
          <strong>{engineRunning ? `PID ${enginePid ?? '—'}` : '未運行'}</strong>
        </div>

        <div className="telemetry-item" title="全域風控熔斷機制">
          {circuitOpen ? (
            <>
              <ShieldAlert size={14} color="var(--trading-red)" />
              <strong className="negative">熔斷觸發</strong>
            </>
          ) : (
            <>
              <ShieldCheck size={14} color="var(--trading-green)" />
              <span className="positive">熔斷正常</span>
            </>
          )}
        </div>

        <div className="telemetry-item" title="資料快照最後同步時間">
          <Activity size={14} />
          <span>同步:</span>
          <strong>{lastSync ? new Date(lastSync * 1000).toLocaleTimeString('zh-TW', { hour12: false }) : '尚未同步'}</strong>
        </div>

        <button
          className="btn btn-outline topbar-refresh-btn"
          type="button"
          onClick={onRefresh}
          disabled={loading}
          title="立即重新整理快照"
        >
          <RefreshCcw size={14} className={loading ? 'spin' : ''} aria-hidden="true" />
          <span>{loading ? '同步中…' : '刷新'}</span>
        </button>
      </div>
    </header>
  );
};

export default TopBar;
