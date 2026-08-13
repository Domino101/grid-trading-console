import React, { lazy, Suspense, useState } from 'react';
import { Activity, Menu, Settings, BarChart2, X } from 'lucide-react';
import Dashboard from './components/Dashboard';
import GridForm from './components/GridForm';
import LoginPage from './components/LoginPage';
import { clearStoredToken, getStoredToken, storeToken } from './utils/auth';

const GridDetail = lazy(() => import('./components/GridDetail'));

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'configs' | 'detail'>('dashboard');
  const [selectedGridId, setSelectedGridId] = useState<string | null>(null);
  const [hasToken, setHasToken] = useState(() => Boolean(getStoredToken()));
  const [authError, setAuthError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const unlock = (token: string) => {
    storeToken(token);
    setAuthError(null);
    setHasToken(true);
  };

  const lock = (message?: string) => {
    clearStoredToken();
    setHasToken(false);
    setAuthError(message ?? null);
  };

  const go = (tab: 'dashboard' | 'configs') => {
    setActiveTab(tab);
    setDrawerOpen(false);
  };

  if (!hasToken) {
    return <LoginPage error={authError} onSubmit={unlock} />;
  }

  return (
    <div className="app-shell">
      <button className="btn btn-outline drawer-toggle" type="button" onClick={() => setDrawerOpen(true)} aria-label="開啟選單"><Menu size={20} /></button>
      {drawerOpen && <button className="drawer-backdrop" type="button" aria-label="關閉選單" onClick={() => setDrawerOpen(false)} />}
      <aside className={`glass-panel app-sidebar ${drawerOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <Activity color="var(--accent-primary)" size={28} />
          <h1 className="text-xl font-semibold">Grid Console</h1>
          <button className="drawer-close" type="button" onClick={() => setDrawerOpen(false)} aria-label="關閉"><X size={18} /></button>
        </div>
        <nav className="app-nav">
          <button className={`btn btn-outline ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => go('dashboard')}>
            <BarChart2 size={18} /> 總覽
          </button>
          <button className={`btn btn-outline ${activeTab === 'configs' ? 'active' : ''}`} onClick={() => go('configs')}>
            <Settings size={18} /> 建立策略
          </button>
        </nav>
        <button className="btn btn-outline logout-btn" type="button" onClick={() => lock()}>登出</button>
      </aside>
      <div className="app-main">
        {activeTab === 'dashboard' && <Dashboard onUnauthorized={() => lock('Token 無效或已過期，請重新登入。')} onOpenGrid={(gridId) => { setSelectedGridId(gridId); setActiveTab('detail'); }} />}
        {activeTab === 'configs' && <GridForm />}
        {activeTab === 'detail' && selectedGridId && (
          <Suspense fallback={<div className="dashboard-empty">載入策略工作台…</div>}>
            <GridDetail gridId={selectedGridId} onBack={() => setActiveTab('dashboard')} onUnauthorized={() => lock('Token 無效或已過期，請重新登入。')} />
          </Suspense>
        )}
      </div>
    </div>
  );
};

export default App;
