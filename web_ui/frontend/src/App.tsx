import React, { lazy, Suspense, useState, useEffect } from 'react';
import { Activity, Menu, X, LogOut } from 'lucide-react';
import Dashboard from './components/Dashboard';
import DcaDashboard from './components/DcaDashboard';
import DcaForm from './components/DcaForm';
import GridForm, { type InitialGridConfig } from './components/GridForm';
import GridBacktestPage, { type GridBacktestInitialConfig } from './components/GridBacktestPage';
import MarketRankings from './components/MarketRankings';
import CrossSectionRankings from './components/CrossSectionRankings';
import { CoinFullPrimePage } from './components/CoinFullPrimePage';
import LoginPage from './components/LoginPage';
import LanguageToggle from './components/LanguageToggle';
import { useI18n } from './i18n/useI18n.ts';
import { api, type MarketRankingItem } from './services/api';
import { clearStoredToken, getStoredToken, storeToken } from './utils/auth';
import { parseHash, navigate, type RouteState } from './utils/router';
import { t } from './i18n/index.ts';

const GridDetail = lazy(() => import('./components/GridDetail'));

const App: React.FC = () => {
  const { lang } = useI18n();
  const [route, setRoute] = useState<RouteState>(() => {
    if (typeof window !== 'undefined' && window.location.hash) {
      return parseHash(window.location.hash);
    }
    return { tab: 'dashboard', gridId: null, dcaBotId: null };
  });

  const [prefilledGridConfig, setPrefilledGridConfig] = useState<InitialGridConfig | null>(null);
  const [prefilledBacktestConfig, setPrefilledBacktestConfig] = useState<GridBacktestInitialConfig | null>(null);
  const [hasToken, setHasToken] = useState(() => Boolean(getStoredToken()));
  const [authError, setAuthError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [liveTradingEnabled, setLiveTradingEnabled] = useState<boolean | null>(null);

  const activeTab = route.tab;
  const selectedGridId = route.gridId;

  useEffect(() => {
    const handleHashChange = () => {
      const parsed = parseHash(window.location.hash);
      setRoute(parsed);
    };

    if (typeof window !== 'undefined' && !window.location.hash) {
      window.history.replaceState(null, '', '#/dashboard');
    }

    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('popstate', handleHashChange);
    return () => {
      window.removeEventListener('hashchange', handleHashChange);
      window.removeEventListener('popstate', handleHashChange);
    };
  }, []);

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

  const go = (tab: 'dashboard' | 'market' | 'configs' | 'dca' | 'dca-create' | 'grid-backtest' | 'coinfull-prime' | 'cross-section') => {
    if (tab !== 'configs') {
      setPrefilledGridConfig(null);
    }
    if (tab !== 'grid-backtest') {
      setPrefilledBacktestConfig(null);
    }
    navigate({ tab, gridId: null, dcaBotId: null });
    setDrawerOpen(false);
  };

  const handleOpenGrid = (gridId: string) => {
    navigate({ tab: 'detail', gridId, dcaBotId: null });
    setDrawerOpen(false);
  };

  const handleSelectDcaBot = (botId: string | null) => {
    navigate({ tab: 'dca', gridId: null, dcaBotId: botId });
  };

  const handleSelectMarketBacktest = (item: MarketRankingItem, _period: string) => {
    void _period;
    setPrefilledBacktestConfig({
      symbol: item.symbol,
      exchange: item.exchange,
      lower_price: item.lower_price,
      upper_price: item.upper_price,
      grid_count: item.grid_count,
      grid_interval: item.grid_interval,
      recommended_order_amount: item.recommended_order_amount,
      estimated_apr: item.estimated_apr,
    });
    navigate({ tab: 'grid-backtest', gridId: null, dcaBotId: null });
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
      // 收窄到 GridForm 接受的時框聯集，避免 any（Phase 5 lint 修正）
      initialPeriod: period === '30d' || period === '180d' ? period : '7d',
      estimatedApr: item.estimated_apr,
    });
    navigate({ tab: 'configs', gridId: null, dcaBotId: null });
    setDrawerOpen(false);
  };

  if (!hasToken) {
    return <LoginPage error={authError} onSubmit={unlock} />;
  }

  return (
    <div className="app-shell" data-lang={lang}>
      {/* Backdrop for Mobile Drawer */}
      {drawerOpen && (
        <button
          className="drawer-backdrop"
          type="button"
          aria-label={t("關閉選單")}
          onClick={() => setDrawerOpen(false)}
        />
      )}

      <div className="mobile-topbar">
        <button
          className="btn btn-outline drawer-toggle"
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label={t("開啟選單")}
        >
          <Menu size={18} />
        </button>
        <span className="mobile-topbar-title">Grid Trading Console</span>
        <LanguageToggle compact />
      </div>

      {/* Sidebar */}
      <aside className={`app-sidebar ${drawerOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <div className="brand-icon-wrapper" aria-hidden="true">GT</div>
          <div className="brand-copy">
            <h1 className="brand-title">GRID TRADING</h1>
            <div className="brand-meta">
              <span className="brand-tag">PRO CONSOLE</span>
              {liveTradingEnabled === true && (
                <span className="brand-status is-live"><i aria-hidden="true" />{t("LIVE 實盤")}</span>
              )}
              {liveTradingEnabled === false && (
                <span className="brand-status is-paper"><i aria-hidden="true" />{t("PAPER 模擬")}</span>
              )}
            </div>
          </div>
          <button
            className="drawer-close"
            type="button"
            onClick={() => setDrawerOpen(false)}
            aria-label={t("關閉選單")}
          >
            <X size={18} />
          </button>
        </div>

        <nav className="app-nav" aria-label={t("系統導航")}>
          {/* 網格交易策略群組 */}
          <div className="nav-group-header">
            <span className="nav-group-code">01</span><span>{t("網格交易")}</span>
          </div>

          <button
            className={`btn ${activeTab === 'dashboard' ? 'active' : ''}`}
            type="button"
            onClick={() => go('dashboard')}
          >
            <span>{t("策略總覽")}</span>
          </button>

          <button
            className={`btn ${activeTab === 'market' ? 'active' : ''}`}
            type="button"
            onClick={() => go('market')}
          >
            <span>{t("策略市場")}</span>
          </button>

          <button
            className={`btn ${activeTab === 'grid-backtest' ? 'active' : ''}`}
            type="button"
            onClick={() => go('grid-backtest')}
          >
            <span>{t("網格回測")}</span>
          </button>

          <button
            className={`btn ${activeTab === 'configs' ? 'active' : ''}`}
            type="button"
            onClick={() => go('configs')}
            title={t("量化網格機器人專屬建立精靈")}
          >
            <span>{t("建立網格")}</span>
          </button>

          {/* DCA 機器人分頁（與網格完全隔離的獨立分頁） */}
          <div className="nav-group-header">
            <span className="nav-group-code">02</span><span>{t("馬丁 DCA")}</span>
          </div>

          <button
            className={`btn ${activeTab === 'dca' ? 'active' : ''}`}
            type="button"
            onClick={() => go('dca')}
          >
            <span>{t("DCA 機器人")}</span>
          </button>

          <button
            className={`btn ${activeTab === 'dca-create' ? 'active' : ''}`}
            type="button"
            onClick={() => go('dca-create')}
          >
            <span>{t("建立 DCA")}</span>
          </button>

          {/* CoinFull Prime 截面多空對沖策略群組 */}
          <div className="nav-group-header">
            <span className="nav-group-code">03</span><span>{t("截面多空")}</span>
          </div>

          <button
            className={`btn ${activeTab === 'cross-section' ? 'active' : ''}`}
            type="button"
            onClick={() => go('cross-section')}
            title={t("純盤面客觀 79 幣因子評分與多空名單")}
          >
            <span>{t("多空截面排行")}</span>
          </button>

          <button
            className={`btn ${activeTab === 'coinfull-prime' ? 'active' : ''}`}
            type="button"
            onClick={() => go('coinfull-prime')}
            title={t("CoinFull Prime 市場中性對沖策略機器人運行與換倉控制台")}
          >
            <span>{t("CoinFull 策略機器人")}</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-version">
            <span className="ws-status"><i aria-hidden="true" />{t("WS 已連線")}</span>
            <span title={`Build ${__APP_VERSION__}`}>v{__APP_VERSION__}</span>
          </div>

          <div className="sidebar-lang">
            <LanguageToggle />
          </div>

          <button
            className="btn btn-outline"
            type="button"
            onClick={() => lock()}
            style={{ width: '100%', justifyContent: 'center' }}
          >
            <LogOut size={15} />
            <span>{t("登出系統")}</span>
          </button>
        </div>
      </aside>

      {/* Main Workspace */}
      <div className="app-main">
        {activeTab === 'dashboard' && (
          <Dashboard
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
            onCreateNew={() => go('configs')}
            onOpenGrid={handleOpenGrid}
          />
        )}

        {activeTab === 'market' && (
          <MarketRankings
            onSelectSymbol={handleSelectMarketSymbol}
            onBacktest={handleSelectMarketBacktest}
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
          />
        )}

        {activeTab === 'grid-backtest' && (
          <GridBacktestPage
            initialConfig={prefilledBacktestConfig}
            onClearInitialConfig={() => setPrefilledBacktestConfig(null)}
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
          />
        )}

        {activeTab === 'configs' && (
          <GridForm
            initialConfig={prefilledGridConfig}
            onClearInitialConfig={() => setPrefilledGridConfig(null)}
            onOpenGrid={handleOpenGrid}
            onNavigateDashboard={() => go('dashboard')}
            onNavigateDca={() => go('dca-create')}
            onNavigateCoinFullPrime={() => go('coinfull-prime')}
          />
        )}

        {activeTab === 'detail' && selectedGridId && (
          <Suspense
            fallback={
              <div className="glass-panel" style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                <Activity size={24} className="spin" style={{ margin: '0 auto 0.75rem' }} />
                <p>{t("載入策略工作台…")}</p>
              </div>
            }
          >
            <GridDetail
              gridId={selectedGridId}
              onBack={() => go('dashboard')}
              onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
            />
          </Suspense>
        )}

        {activeTab === 'dca' && (
          <DcaDashboard
            onCreateNew={() => go('dca-create')}
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
            selectedBotId={route.dcaBotId}
            onSelectBot={handleSelectDcaBot}
          />
        )}

        {activeTab === 'dca-create' && (
          <DcaForm onNavigateDashboard={() => go('dca')} />
        )}

        {activeTab === 'cross-section' && (
          <CrossSectionRankings
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
            onGoToBot={() => go('coinfull-prime')}
          />
        )}

        {activeTab === 'coinfull-prime' && (
          <CoinFullPrimePage
            onUnauthorized={() => lock(t("Token 無效或已過期，請重新登入。"))}
            onGoToRankings={() => go('cross-section')}
          />
        )}
      </div>
    </div>
  );
};

export default App;