import { RefreshCcw } from 'lucide-react';

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

const TopBar = ({ dataStatus, enginePid, engineRunning, circuitOpen, lastSync, accountMode, onRefresh, loading }: TopBarProps) => (
  <header className="topbar" aria-label="系統狀態">
    <div className="topbar-left">
      <span className={`status-dot ${dataStatus === 'live' ? 'is-live' : ''}`} />
      <strong>網格交易總控台</strong>
      <span className={`badge badge-${accountMode || 'unknown'}`}>{accountMode || 'unknown'}</span>
    </div>
    <div className="topbar-meta">
      <span>引擎 {engineRunning ? `PID ${enginePid}` : '未運行'}</span>
      <span className={circuitOpen ? 'negative' : ''}>{circuitOpen ? '熔斷開啟' : '熔斷關閉'}</span>
      <span>{lastSync ? `同步 ${new Date(lastSync * 1000).toLocaleTimeString('zh-TW')}` : '尚未同步'}</span>
      <button className="btn btn-outline" onClick={onRefresh} disabled={loading}><RefreshCcw size={16} aria-hidden="true" /> 重新整理</button>
    </div>
  </header>
);

export default TopBar;
