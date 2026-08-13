import React, { useState } from 'react';
import { Activity, Settings, BarChart2 } from 'lucide-react';
import Dashboard from './components/Dashboard';
import GridForm from './components/GridForm';
import GridDetail from './components/GridDetail';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'configs' | 'detail'>('dashboard');
  const [selectedGridId, setSelectedGridId] = useState<string | null>(null);

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
            Create Grid
          </button>
        </nav>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, padding: '1rem 1rem 1rem 0', display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        {activeTab === 'dashboard' && <Dashboard onOpenGrid={(gridId) => { setSelectedGridId(gridId); setActiveTab('detail'); }} />}
        {activeTab === 'configs' && <GridForm />}
        {activeTab === 'detail' && selectedGridId && <GridDetail gridId={selectedGridId} onBack={() => setActiveTab('dashboard')} />}
      </div>
    </div>
  );
};

export default App;
