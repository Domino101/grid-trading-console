import React from 'react';
import { RefreshCcw, ShieldCheck, ShieldAlert, Cpu, Activity, Radio } from 'lucide-react';
import { t } from '../i18n/index.ts';

interface TopBarProps {
    dataStatus?: string;
    enginePid?: number | null;
    engineRunning?: boolean;
    circuitOpen?: boolean;
    lastSync?: number | null;
    accountMode?: string;
    eventsLive?: boolean;
    onRefresh: () => void;
    loading?: boolean;
    title?: string;
}

const TopBar: React.FC<TopBarProps> = ({
    dataStatus,
    enginePid,
    engineRunning,
    circuitOpen,
    lastSync,
    accountMode,
    eventsLive,
    onRefresh,
    loading,
    title = t("網格總控台"),
}) => {
  const mode = accountMode || 'unknown';

  return (
    <header className="topbar" aria-label={t("系統即時遙測看板")}>
      <div className="topbar-left">
        <div className="topbar-title">
          <span className={`status-dot ${dataStatus === 'live' ? 'is-live' : dataStatus === 'stale' ? 'is-warning' : ''}`} />
          <span>{title}</span>
        </div>
        <span className={`badge badge-${mode}`}>
          {mode.toUpperCase()}
        </span>
      </div>

      <div className="topbar-telemetry">
        <div className="telemetry-item" title={t("交易引擎運行狀態")}>
          <Cpu size={14} />
          <span>{t("引擎:")}</span>
          <span className={`telemetry-dot ${engineRunning ? 'online' : 'warning'}`} />
          <strong>{engineRunning ? `PID ${enginePid ?? '—'}` : t("未運行")}</strong>
        </div>

        <div className="telemetry-item" title={t("全域風控熔斷機制")}>
          {circuitOpen ? (
            <>
              <ShieldAlert size={14} color="var(--trading-red)" />
              <strong className="negative">{t("熔斷觸發")}</strong>
            </>
          ) : (
            <>
              <ShieldCheck size={14} color="var(--trading-green)" />
              <span className="positive">{t("熔斷正常")}</span>
            </>
          )}
        </div>

        <div className="telemetry-item" title={t("事件推送（WebSocket）連線狀態：中斷時以輪詢為準")}>
          <Radio size={14} />
          <span>{t("事件:")}</span>
          <span className={`telemetry-dot ${eventsLive ? 'online' : 'warning'}`} />
          <strong>{eventsLive ? t("即時") : t("輪詢")}</strong>
        </div>

        <div className="telemetry-item" title={t("資料快照最後同步時間")}>
          <Activity size={14} />
          <span>{t("同步:")}</span>
          <strong>{lastSync ? new Date(lastSync * 1000).toLocaleTimeString('zh-TW', { hour12: false }) : t("尚未同步")}</strong>
        </div>

        <button
          className="btn btn-outline topbar-refresh-btn"
          type="button"
          onClick={onRefresh}
          disabled={loading}
          title={t("立即重新整理快照")}
        >
          <RefreshCcw size={14} className={loading ? 'spin' : ''} aria-hidden="true" />
          <span>{loading ? t("同步中…") : t("刷新")}</span>
        </button>
      </div>
    </header>
  );
};

export default TopBar;
