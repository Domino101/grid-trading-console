import React, { lazy, Suspense, useState, useEffect } from 'react';
import { Activity, Menu, PlusCircle, BarChart3, Trophy, X, LogOut, Terminal } from 'lucide-react';
import Dashboard from './components/Dashboard';
import GridForm, { type InitialGridConfig } from './components/GridForm';
import MarketRankings from './components/MarketRankings';
import LoginPage from './components/LoginPage';
import { api, type MarketRankingItem } from './services/api';
import { clearStoredToken, getStoredToken, storeToken } from './utils/auth';

const GridDetail = lazy(() => import('./components/GridDetail'));

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'market' | 'configs' | 'detail'>('dashboard');
  const [selectedGridId, setSelectedGridId] = useState<string | null>(null);
  const [prefilledGridConfig, setPrefilledGridConfig] = useState<InitialGridConfig | null>(null);
  const [hasToken, setHasToken] = useState(() => Boolean(getStoredToken()));
  const [authError, setAuthError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [liveTradingEnabled, setLiveTradingEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!hasToken) return;
    const fetchHealth = async () => {
      try {
        const h = await api.getSystemHealth();
        setLiveTradingEnabled(Boolean(h.live_trading_enabled));
      } catch {
        // silent background check
      }
    };
    void fetchHealth();
    const interval = setInterval(() => void fetchHealth(), 30000);
    return () => clearInterval(interval);
  }, [hasToken]);

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

  const go = (tab: 'dashboard' | 'market' | 'configs') => {
    if (tab !== 'configs') {
      setPrefilledGridConfig(null);
    }
    setActiveTab(tab);
    setDrawerOpen(false);
  };

  const handleSelectMarketSymbol = (item: MarketRankingItem, period: string) => {
    setPrefilledGridConfig({
      symbol: item.symbol,
      exchange: item.exchange,
      lower_price: item.lower_price,
      upper_price: item.upper_price,
      grid_interval: item.grid_interval,
      order_amount: item.recommended_order_amount,
      initialGridCount: item.grid_count,
      initialPeriod: period as any,
      estimatedApr: item.estimated_apr,
    });
    setActiveTab('configs');
    setDrawerOpen(false);
  };

  if (!hasToken) {
    return <LoginPage error={authError} onSubmit={unlock} />;
  }

  return (
    <div className="app-shell">
      {/* Mobile Drawer Trigger */}
      <button
        className="btn btn-outline drawer-toggle"
        type="button"
        onClick={() => setDrawerOpen(true)}
        aria-label="開啟選單"
      >
        <Menu size={18} />
      </button>

      {/* Backdrop for Mobile Drawer */}
      {drawerOpen && (
        <button
          className="drawer-backdrop"
          type="button"
          aria-label="關閉選單"
          onClick={() => setDrawerOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`app-sidebar ${drawerOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <div className="brand-icon-wrapper">
            <Activity color="var(--accent-primary)" size={20} />
          </div>
          <div>
            <h1 className="brand-title">Grid Trading</h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.2rem', flexWrap: 'wrap' }}>
              <span className="brand-tag">PRO CONSOLE</span>
              {liveTradingEnabled === true && (
                <span className="brand-tag" style={{ background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', fontWeight: 600 }}>
                  🟢 LIVE 實盤
                </span>
              )}
              {liveTradingEnabled === false && (
                <span className="brand-tag" style={{ background: 'rgba(245, 158, 11, 0.15)', color: '#f59e0b', border: '1px solid rgba(245, 158, 11, 0.3)', fontWeight: 600 }}>
                  🟡 PAPER 模擬
                </span>
              )}
            </div>
          </div>
          <button
            className="drawer-close"
            type="button"
            onClick={() => setDrawerOpen(false)}
            aria-label="關閉選單"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="app-nav" aria-label="系統導航">
          <button
            className={`btn ${activeTab === 'dashboard' ? 'active' : ''}`}
            type="button"
            onClick={() => go('dashboard')}
          >
            <BarChart3 size={17} />
            <span>策略總覽</span>
          </button>

          <button
            className={`btn ${activeTab === 'market' ? 'active' : ''}`}
            type="button"
            onClick={() => go('market')}
          >
            <Trophy size={17} />
            <span>策略市場</span>
          </button>

          <button
            className={`btn ${activeTab === 'configs' ? 'active' : ''}`}
            type="button"
            onClick={() => go('configs')}
          >
            <PlusCircle size={17} />
            <span>建立策略</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-version">
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <Terminal size={12} />
              <span>v2.4.0-pro</span>
            </span>
            <span className="positive">● CONNECTED</span>
          </div>

          <button
            className="btn btn-outline"
            type="button"
            onClick={() => lock()}
            style={{ width: '100%', justifyContent: 'center' }}
          >
            <LogOut size={15} />
            <span>登出系統</span>
          </button>
        </div>
      </aside>

      {/* Main Workspace */}
      <div className="app-main">
        {activeTab === 'dashboard' && (
          <Dashboard
            onUnauthorized={() => lock('Token 無效或已過期，請重新登入。')}
            onCreateNew={() => go('configs')}
            onOpenGrid={(gridId) => {
              setSelectedGridId(gridId);
              setActiveTab('detail');
            }}
          />
        )}

        {activeTab === 'market' && (
          <MarketRankings
            onSelectSymbol={handleSelectMarketSymbol}
            onUnauthorized={() => lock('Token 無效或已過期，請重新登入。')}
          />
        )}

        {activeTab === 'configs' && (
          <GridForm
            initialConfig={prefilledGridConfig}
            onClearInitialConfig={() => setPrefilledGridConfig(null)}
            onOpenGrid={(gridId) => {
              setSelectedGridId(gridId);
              setActiveTab('detail');
            }}
            onNavigateDashboard={() => go('dashboard')}
          />
        )}

        {activeTab === 'detail' && selectedGridId && (
          <Suspense
            fallback={
              <div className="glass-panel" style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                <Activity size={24} className="spin" style={{ margin: '0 auto 0.75rem' }} />
                <p>載入策略工作台…</p>
              </div>
            }
          >
            <GridDetail
              gridId={selectedGridId}
              onBack={() => setActiveTab('dashboard')}
              onUnauthorized={() => lock('Token 無效或已過期，請重新登入。')}
            />
          </Suspense>
        )}
      </div>
    </div>
  );
};

export default App;

