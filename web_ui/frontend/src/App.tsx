import React, { FormEvent, lazy, Suspense, useState } from 'react';
import { Activity, Settings, BarChart2 } from 'lucide-react';
import Dashboard from './components/Dashboard';
import GridForm from './components/GridForm';

const GridDetail = lazy(() => import('./components/GridDetail'));

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'configs' | 'detail'>('dashboard');
  const [selectedGridId, setSelectedGridId] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [hasToken, setHasToken] = useState(() => Boolean(localStorage.getItem('GRID_API_TOKEN')));

  const saveToken = (event: FormEvent) => {
    event.preventDefault();
    const token = tokenInput.trim();
    if (!token) return;
    localStorage.setItem('GRID_API_TOKEN', token);
    setHasToken(true);
  };

  if (!hasToken) {
    return (
      <main className="token-gate">
        <form className="strategy-card token-card" onSubmit={saveToken}>
          <p className="eyebrow">Grid Engine</p>
          <h2>輸入 API Token</h2>
          <p className="muted">瀏覽器只把 token 存在本機 localStorage，用於呼叫同源 /api。請使用伺服器上的 GRID_API_TOKEN 或 .grid_api_token，不要把金鑰貼到這裡。</p>
          <label>GRID_API_TOKEN
            <input value={tokenInput} onChange={event => setTokenInput(event.target.value)} autoComplete="off" />
          </label>
          <button className="btn btn-primary" type="submit">進入總控台</button>
        </form>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <div className="glass-panel app-sidebar">
        <div className="flex items-center gap-3" style={{ padding: '0.5rem' }}>
          <Activity color="var(--accent-primary)" size={28} />
          <h1 className="text-xl font-semibold">Grid Engine</h1>
        </div>

        <nav className="app-nav">
          <button
            className={`btn btn-outline ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            <BarChart2 size={18} />
            總覽
          </button>
          <button
            className={`btn btn-outline ${activeTab === 'configs' ? 'active' : ''}`}
            onClick={() => setActiveTab('configs')}
          >
            <Settings size={18} />
            建立策略
          </button>
        </nav>
      </div>

      <div className="app-main">
        {activeTab === 'dashboard' && <Dashboard onOpenGrid={(gridId) => { setSelectedGridId(gridId); setActiveTab('detail'); }} />}
        {activeTab === 'configs' && <GridForm />}
        {activeTab === 'detail' && selectedGridId && <Suspense fallback={<div className="dashboard-empty">載入策略工作台…</div>}><GridDetail gridId={selectedGridId} onBack={() => setActiveTab('dashboard')} /></Suspense>}
      </div>
    </div>
  );
};

export default App;
