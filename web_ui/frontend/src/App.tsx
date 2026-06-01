import React, { useState, useEffect } from 'react';
import { 
  Activity, 
  Settings, 
  BarChart2
} from 'lucide-react';
import { api, type ProcessStatus } from './services/api';
import Dashboard from './components/Dashboard';
import Configs from './components/Configs';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'logs' | 'configs'>('dashboard');
  const [processes, setProcesses] = useState<ProcessStatus[]>([]);
  const [selectedPid, setSelectedPid] = useState<number | null>(null);

  useEffect(() => {
    fetchProcesses();
    const interval = setInterval(fetchProcesses, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchProcesses = async () => {
    try {
      const procs = await api.getProcesses();
      setProcesses(procs);
      if (procs.length > 0 && selectedPid === null) {
        setSelectedPid(procs[0].pid);
      }
    } catch (e) {
      console.error('Failed to fetch processes', e);
    }
  };

  return (
    <div style={{ display: 'flex', minHeight: '100vh', backgroundColor: 'var(--bg-dark)' }}>
      {/* Sidebar */}
      <div className="glass-panel" style={{ width: '260px', margin: '1rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <div className="flex items-center gap-3" style={{ padding: '0.5rem' }}>
          <Activity color="var(--accent-primary)" size={28} />
          <h1 className="text-xl font-semibold">Grid Engine</h1>
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <button 
            className={`btn btn-outline ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
            style={{ justifyContent: 'flex-start', padding: '0.75rem 1rem', border: activeTab === 'dashboard' ? '1px solid var(--accent-primary)' : '1px solid transparent', color: activeTab === 'dashboard' ? 'var(--accent-primary)' : 'var(--text-secondary)' }}
          >
            <BarChart2 size={18} />
            Dashboard
          </button>
          
          <button 
            className={`btn btn-outline ${activeTab === 'configs' ? 'active' : ''}`}
            onClick={() => setActiveTab('configs')}
            style={{ justifyContent: 'flex-start', padding: '0.75rem 1rem', border: activeTab === 'configs' ? '1px solid var(--accent-primary)' : '1px solid transparent', color: activeTab === 'configs' ? 'var(--accent-primary)' : 'var(--text-secondary)' }}
          >
            <Settings size={18} />
            Configurations
          </button>
        </nav>

        <div style={{ marginTop: 'auto' }}>
          <h3 className="text-xs text-muted font-medium" style={{ textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '1rem', paddingLeft: '0.5rem' }}>
            Active Bots
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {processes.filter(p => p.status === 'running').length === 0 && (
              <div className="text-sm text-muted" style={{ paddingLeft: '0.5rem' }}>No active bots</div>
            )}
            {processes.filter(p => p.status === 'running').map(p => (
              <div 
                key={p.pid} 
                onClick={() => setSelectedPid(p.pid)}
                style={{ 
                  display: 'flex', alignItems: 'center', gap: '0.5rem', 
                  padding: '0.5rem', borderRadius: 'var(--radius-sm)', 
                  cursor: 'pointer',
                  background: selectedPid === p.pid ? 'rgba(255,215,0,0.1)' : 'transparent',
                  border: selectedPid === p.pid ? '1px solid rgba(255,215,0,0.2)' : '1px solid transparent'
                }}
              >
                <div className="pulse-indicator success"></div>
                <span className="text-sm" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {p.config_path?.split('/').pop()?.replace('.yaml', '') || `PID ${p.pid}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, padding: '1rem 1rem 1rem 0', display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        {activeTab === 'dashboard' && (
          <Dashboard 
            processes={processes} 
            selectedPid={selectedPid} 
            onSelectPid={setSelectedPid}
            onRefresh={fetchProcesses}
          />
        )}
        {activeTab === 'configs' && <Configs />}
      </div>
    </div>
  );
};

export default App;
