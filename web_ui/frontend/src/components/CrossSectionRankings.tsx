import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { api, ApiError } from '../services/api';
import type { UniverseRankResult, RankedItem } from '../services/api';
import {
  Activity,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Search,
  Sliders,
  Info,
  ArrowRight,
  Bot,
  Sparkles,
  AlertTriangle,
  ShieldCheck,
} from 'lucide-react';

interface CrossSectionRankingsProps {
  onUnauthorized?: () => void;
  onGoToBot?: () => void;
}

export const CrossSectionRankings: React.FC<CrossSectionRankingsProps> = ({
  onUnauthorized,
  onGoToBot,
}) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [rankResult, setRankResult] = useState<UniverseRankResult | null>(null);

  // TradFi (美股/ETF/大宗商品) 排除開關狀態 (持久化至 localStorage，預設 true)
  const [excludeTradfi, setExcludeTradfi] = useState<boolean>(() => {
    const saved = localStorage.getItem('coinfull_exclude_tradfi');
    if (saved !== null) return saved === 'true';
    return true;
  });

  // 篩選與搜尋狀態
  const [filterTab, setFilterTab] = useState<'baskets' | 'long' | 'short' | 'all'>(() => {
    const saved = localStorage.getItem('cross_section_filter_tab');
    if (saved === 'baskets' || saved === 'long' || saved === 'short' || saved === 'all') return saved;
    return 'baskets';
  });
  const [searchQuery, setSearchQuery] = useState<string>('');

  const loadData = useCallback(async (isRefresh = false, overrideExcludeTradfi?: boolean) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);

    try {
      const exTradfi = overrideExcludeTradfi !== undefined ? overrideExcludeTradfi : excludeTradfi;
      const rankings = await api.getCoinFullPrimeRankings(isRefresh, exTradfi);
      setRankResult(rankings);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 401 && onUnauthorized) {
        onUnauthorized();
        return;
      }
      const msg = err instanceof Error ? err.message : '載入多空截面排行數據失敗';
      setError(msg);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onUnauthorized, excludeTradfi]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadData(false);
  }, [loadData]);

  const handleToggleExcludeTradfi = (val: boolean) => {
    setExcludeTradfi(val);
    try {
      localStorage.setItem('coinfull_exclude_tradfi', String(val));
    } catch {
      // ignore
    }
    void loadData(true, val);
  };

  const handleFilterTabChange = (tab: 'baskets' | 'long' | 'short' | 'all') => {
    setFilterTab(tab);
    try {
      localStorage.setItem('cross_section_filter_tab', tab);
    } catch {
      // ignore
    }
  };

  const displayedItems = useMemo(() => {
    if (!rankResult) return [];

    let items: RankedItem[];
    if (filterTab === 'baskets') {
      items = [...(rankResult.long_list || []), ...(rankResult.short_list || [])];
    } else if (filterTab === 'long') {
      items = rankResult.long_list || [];
    } else if (filterTab === 'short') {
      items = rankResult.short_list || [];
    } else {
      items = rankResult.all_rankings || [];
    }

    if (!searchQuery.trim()) return items;
    const q = searchQuery.trim().toUpperCase();
    return items.filter(x => x.symbol.toUpperCase().includes(q));
  }, [rankResult, filterTab, searchQuery]);

  const getScoreBadgeClass = (score: number) => {
    if (score >= 1.0) return 'score-very-strong';
    if (score > 0.3) return 'score-strong';
    if (score <= -1.0) return 'score-very-weak';
    if (score < -0.3) return 'score-weak';
    return 'score-neutral';
  };

  if (loading && !rankResult) {
    return (
      <div className="coinfull-container">
        <div className="glass-panel" style={{ padding: '3.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          <Activity size={32} className="spin text-accent" style={{ margin: '0 auto 1rem' }} />
          <h3 style={{ color: 'var(--text-primary)', marginBottom: '0.5rem' }}>正在計算全市場多空截面量化矩陣…</h3>
          <p style={{ fontSize: '0.88rem' }}>透過非同步並行管線聚合 79 幣多時距混合動能 (60%) 與平滑資金費率 (40%)，結合 Regime Gate 風險閘門</p>
        </div>
      </div>
    );
  }

  const longList = rankResult?.long_list || [];
  const shortList = rankResult?.short_list || [];
  const targetCount = longList.length || 5;
  const isBenchmarkHedge = shortList.length <= 2 || shortList.some(x => x.symbol === 'BTCUSDT' || x.evaluations?.overall_eval?.includes('基準'));
  const top1Long = longList[0];
  const top1Short = shortList.length > 0
    ? (isBenchmarkHedge ? shortList[0] : shortList[shortList.length - 1])
    : undefined;

  return (
    <div className="coinfull-container">
      {/* 頁頭與控制列 */}
      <header className="coinfull-header">
        <div className="header-title-block">
          <div className="title-with-badge">
            <h2>多空截面排行</h2>
            <span className="badge badge-accent">市場中性 Alpha (Beta ≈ 0)</span>
            <span className="badge badge-secondary" title="純盤面行情客觀分析與量化矩陣打分">
              📊 盤面分析視角
            </span>
          </div>
          <p className="text-muted">
            對 Top 79 主流幣合約進行「多時距混合動能 (60%) + 平滑資金費率反向 (40%) + Regime Gate 風險閘門」橫截面量化打分
          </p>
        </div>

        <div className="header-actions">
          <button
            type="button"
            className={`pill-btn ${excludeTradfi ? 'active' : 'warning'}`}
            onClick={() => handleToggleExcludeTradfi(!excludeTradfi)}
            style={{
              fontSize: '0.78rem',
              padding: '0.35rem 0.75rem',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              borderColor: excludeTradfi ? 'rgba(34, 197, 94, 0.4)' : 'rgba(234, 179, 8, 0.5)',
              background: excludeTradfi ? 'rgba(34, 197, 94, 0.15)' : 'rgba(234, 179, 8, 0.15)'
            }}
            title="AI 顧問建議：排除美股、韓股、指數 ETF 與大宗商品等 TradFi 合約，保持純加密貨幣市場中性對沖"
          >
            <ShieldCheck size={14} className={excludeTradfi ? 'text-success' : 'text-warning'} />
            <span>{excludeTradfi ? '🛡️ 排除美股/ETF/商品 (純加密)' : '⚠️ 包含 TradFi 美股/ETF'}</span>
          </button>

          <button
            type="button"
            className="btn btn-outline"
            onClick={() => loadData(true)}
            disabled={refreshing}
          >
            <RefreshCw size={15} className={refreshing ? 'spin' : ''} />
            <span>{refreshing ? '重新計算中…' : '即時重新計算'}</span>
          </button>

          {onGoToBot && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={onGoToBot}
              title="前往 CoinFull Prime 策略機器人進行實盤/測試網調倉與自動排程管理"
            >
              <Bot size={15} />
              <span>CoinFull Prime 策略機器人</span>
              <ArrowRight size={14} />
            </button>
          )}
        </div>
      </header>

      {/* 提示或錯誤訊息 */}
      {error && (
        <div className="alert-banner alert-danger">
          <AlertTriangle size={18} />
          <span>{error}</span>
          <button type="button" className="close-btn" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* 核心資訊摘要卡片 */}
      <section className="coinfull-summary-cards">
        <div className="glass-card summary-card long-card">
          <div className="card-top">
            <span className="card-label">🟢 最強多頭首選 (Top 1)</span>
            <TrendingUp size={20} className="text-positive" />
          </div>
          <div className="card-symbol">{top1Long?.symbol || '—'}</div>
          <div className="card-score-row">
            <span className="score-val text-positive">+{top1Long?.score.toFixed(4)}</span>
            <span className="eval-tag">{top1Long?.evaluations.overall_eval}</span>
          </div>
          <div className="card-detail-sub">
            動能: {(top1Long?.raw_params?.price_change_pct ?? 0) > 0 ? '+' : ''}{top1Long?.raw_params?.price_change_pct ?? 0}% ({top1Long?.evaluations?.momentum_eval || '—'}) · 費率: {top1Long?.raw_params?.funding_rate_pct ?? 0}% ({top1Long?.evaluations?.funding_eval || '—'})
          </div>
        </div>

        <div className="glass-card summary-card short-card">
          <div className="card-top">
            <span className="card-label">
              {isBenchmarkHedge ? '🛡️ 基準指數對沖 (Benchmark)' : '🔴 最弱空頭重災 (Bottom 1)'}
            </span>
            <TrendingDown size={20} className="text-negative" />
          </div>
          <div className="card-symbol">
            {isBenchmarkHedge
              ? (shortList.length > 1 ? shortList.map(s => s.symbol).join(' + ') : (top1Short?.symbol || 'BTCUSDT'))
              : (top1Short?.symbol || '—')}
          </div>
          <div className="card-score-row">
            {isBenchmarkHedge ? (
              <>
                <span className="score-val text-accent">
                  對沖權重: {(((top1Short?.target_weight ?? 0.5)) * 100).toFixed(0)}%
                </span>
                <span className="eval-tag">
                  {top1Short?.evaluations?.overall_eval || '單主流 BTC 指數對沖 🛡️'}
                </span>
              </>
            ) : (
              <>
                <span className="score-val text-negative">{top1Short?.score.toFixed(4)}</span>
                <span className="eval-tag">{top1Short?.evaluations.overall_eval}</span>
              </>
            )}
          </div>
          <div className="card-detail-sub">
            {isBenchmarkHedge ? (
              <span>
                市價: {top1Short?.raw_params?.price ? `$${top1Short.raw_params.price.toLocaleString()}` : '—'} · 單主流 BTC 對沖，徹底消除小幣軋空風險
              </span>
            ) : (
              <span>
                動能: {(top1Short?.raw_params?.price_change_pct ?? 0) > 0 ? '+' : ''}{top1Short?.raw_params?.price_change_pct ?? 0}% ({top1Short?.evaluations?.momentum_eval || '—'}) · 流向: {top1Short?.raw_params?.taker_flow_ratio ?? 1} ({top1Short?.evaluations?.flow_eval || '—'})
              </span>
            )}
          </div>
        </div>

        <div className="glass-card summary-card neutral-card">
          <div className="card-top">
            <span className="card-label">
              {isBenchmarkHedge ? '⚖️ 多空對沖組合配置' : '⚖️ 對沖籃子組合配置'}
            </span>
            <Sliders size={20} className="text-accent" />
          </div>
          <div className="card-stats-grid">
            <div className="stat-box">
              <span className="stat-num text-positive">{targetCount} 隻</span>
              <span className="stat-desc">
                {isBenchmarkHedge
                  ? `做多標的 (各 ${(50 / targetCount).toFixed(1)}% 資金)`
                  : `做多標的 (各 ${(100 / targetCount).toFixed(1)}%)`}
              </span>
            </div>
            <div className="stat-divider">VS</div>
            <div className="stat-box">
              <span className="stat-num text-negative">
                {isBenchmarkHedge
                  ? `${shortList.length || 1} 隻 (${shortList.map(s => s.symbol.replace('USDT', '')).join('+') || 'BTC'})`
                  : `${shortList.length || targetCount} 隻`}
              </span>
              <span className="stat-desc">
                {isBenchmarkHedge
                  ? '基準對沖 (佔 50.0% 資金)'
                  : `做空標的 (各 ${(100 / (shortList.length || targetCount)).toFixed(1)}%)`}
              </span>
            </div>
          </div>
          <div className="card-detail-sub text-center">
            {isBenchmarkHedge
              ? `做多強勢標的 (${targetCount} 隻 · 50%) vs 基準 BTC 空頭對沖 (50%) · 徹底規避小幣反抽軋空風險`
              : '做多強勢標的 vs 做空劣勢標的 · 消除市場系統性 Beta 波動風險'}
          </div>
        </div>

        <div className="glass-card summary-card info-card">
          <div className="card-top">
            <span className="card-label">🤖 策略執行導引</span>
            <Sparkles size={20} className="text-accent" />
          </div>
          <div className="schedule-status-row">
            <span className="status-dot online"></span>
            <span className="status-title">CoinFull Prime 策略機器人</span>
          </div>
          <div className="schedule-detail">
            <span>母體覆蓋: <strong>{rankResult?.universe_size || 79}</strong> 個主流合約</span>
            <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '0.2rem', lineHeight: 1.5 }}>
              本分頁專注於展示即時客觀盤面排行。如需執行調倉、設定本金槓桿或開啟定時排程，請切換至專屬機器人分頁。
            </div>
          </div>
          {onGoToBot && (
            <div className="card-detail-sub" style={{ marginTop: '0.4rem', textAlign: 'right' }}>
              <button
                type="button"
                className="btn btn-outline"
                style={{ padding: '0.2rem 0.6rem', fontSize: '0.72rem', borderRadius: '4px', width: '100%', justifyContent: 'center' }}
                onClick={onGoToBot}
              >
                前往機器人控制台 ➔
              </button>
            </div>
          )}
        </div>
      </section>

      {/* 篩選與搜尋工具列 */}
      <section className="coinfull-toolbar glass-panel">
        <div className="tab-pills">
          <button
            type="button"
            className={`pill-btn ${filterTab === 'baskets' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('baskets')}
          >
            🌟 今日多空組合 ({((rankResult?.long_list?.length || 0) + (rankResult?.short_list?.length || 0))})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'long' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('long')}
          >
            🟢 Top {targetCount} 做多籃子 ({rankResult?.long_list?.length || 0})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'short' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('short')}
          >
            {isBenchmarkHedge
              ? `🛡️ 基準指數對沖 (${rankResult?.short_list?.length || 0})`
              : `🔴 Bottom ${targetCount} 做空籃子 (${rankResult?.short_list?.length || 0})`}
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'all' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('all')}
          >
            📋 全部 79 幣完整截面總榜 ({rankResult?.universe_size || 0})
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          <span
            className={`badge ${excludeTradfi ? 'badge-success' : 'badge-warning'}`}
            style={{ fontSize: '0.74rem', padding: '0.3rem 0.6rem', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
            onClick={() => handleToggleExcludeTradfi(!excludeTradfi)}
            title="點擊切換 TradFi 合約過濾狀態"
          >
            {excludeTradfi ? '🛡️ 純加密 79 幣宇宙' : '⚠️ 含 TradFi (美股/ETF/商品)'}
          </span>

          <div className="search-box">
            <Search size={16} className="search-icon" />
            <input
              type="text"
              placeholder="搜尋幣種代號 (例: BTC, SOL, DOGE)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>
      </section>

      {/* 主表格：數值與相關參數及評價 */}
      <section className="coinfull-table-panel glass-panel">
        <div className="table-responsive">
          <table className="coinfull-table">
            <thead>
              <tr>
                <th style={{ width: '80px' }}>排名</th>
                <th style={{ width: '130px' }}>幣種 / 市價</th>
                <th style={{ width: '180px' }}>綜合評分 & 策略結論</th>
                <th>多時距動能 (60%)</th>
                <th>買賣流 (0% 停用)</th>
                <th>資金費率反向 (40%)</th>
                <th>大戶多空比 (0% 停用)</th>
                <th style={{ width: '130px' }}>目標配置權重</th>
              </tr>
            </thead>
            <tbody>
              {displayedItems.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    查無符合條件之幣種數據
                  </td>
                </tr>
              ) : (
                displayedItems.map((item) => {
                  const raw = item.raw_params;
                  const sig = item.signals;
                  const evals = item.evaluations;
                  const isLong = item.side === 'long';
                  const isShort = item.side === 'short';

                  return (
                    <tr
                      key={item.symbol}
                      className={`rank-row ${isLong ? 'row-long' : ''} ${isShort ? 'row-short' : ''}`}
                    >
                      {/* 1. 排名 */}
                      <td className="rank-cell">
                        {isLong ? (
                          <span className="rank-pill rank-long">#{item.rank} 多</span>
                        ) : isShort ? (
                          <span className="rank-pill rank-short">
                            {isBenchmarkHedge ? '對沖 空' : `#${item.rank} 空`}
                          </span>
                        ) : (
                          <span className="rank-pill rank-neutral">#{item.rank}</span>
                        )}
                      </td>

                      {/* 2. 幣種代號與市價 */}
                      <td className="symbol-cell">
                        <div className="symbol-name">{item.symbol}</div>
                        <div className="price-tag">
                          {raw.price > 0 ? `$${raw.price >= 1 ? raw.price.toFixed(3) : raw.price.toFixed(5)}` : '—'}
                        </div>
                      </td>

                      {/* 3. 綜合得分與評價 */}
                      <td className="score-cell">
                        <div className={`score-badge ${getScoreBadgeClass(item.score)}`}>
                          <span className="score-number">{item.score > 0 ? `+${item.score.toFixed(4)}` : item.score.toFixed(4)}</span>
                        </div>
                        <div className="verdict-label">{evals.overall_eval}</div>
                      </td>

                      {/* 4. 動能參數 (漲跌幅 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className={raw.price_change_pct >= 0 ? 'text-positive' : 'text-negative'}>
                            {raw.price_change_pct >= 0 ? `+${raw.price_change_pct.toFixed(2)}%` : `${raw.price_change_pct.toFixed(2)}%`}
                          </span>
                          <span className="eval-tag-inline">({evals.momentum_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.mom_z >= 0 ? `+${sig.mom_z.toFixed(2)}` : sig.mom_z.toFixed(2)}</div>
                      </td>

                      {/* 5. Taker 買賣流 (比例 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className="font-mono">{raw.taker_flow_ratio.toFixed(3)}</span>
                          <span className="eval-tag-inline">({evals.flow_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.flow_z >= 0 ? `+${sig.flow_z.toFixed(2)}` : sig.flow_z.toFixed(2)}</div>
                      </td>

                      {/* 6. 資金費率 (費率 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className={raw.funding_rate_pct > 0.01 ? 'text-negative font-mono' : 'text-positive font-mono'}>
                            {raw.funding_rate_pct >= 0 ? `+${raw.funding_rate_pct.toFixed(4)}%` : `${raw.funding_rate_pct.toFixed(4)}%`}
                          </span>
                          <span className="eval-tag-inline">({evals.funding_eval})</span>
                        </div>
                        <div className="zscore-sub">反向 Z: {sig.fund_z >= 0 ? `+${sig.fund_z.toFixed(2)}` : sig.fund_z.toFixed(2)}</div>
                      </td>

                      {/* 7. 大戶多空比 (比率 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className="font-mono">{raw.top_trader_ratio.toFixed(3)}</span>
                          <span className="eval-tag-inline">({evals.liquidation_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.liq_z >= 0 ? `+${sig.liq_z.toFixed(2)}` : sig.liq_z.toFixed(2)}</div>
                      </td>

                      {/* 8. 目標配置權重 */}
                      <td className="allocation-cell">
                        {(item.target_weight ?? 0) > 0 ? (
                          <>
                            <div className={`alloc-amount ${isLong ? 'text-positive' : 'text-negative'}`}>
                              {isLong ? '🟢 做多' : (isBenchmarkHedge ? '🛡️ 基準對沖' : '🔴 做空')}
                            </div>
                            <div className="alloc-pct">權重: {((item.target_weight ?? 0) * 100).toFixed(1)}%</div>
                          </>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* 策略說明與底層數學原理註釋 */}
      <footer className="coinfull-footer-note glass-panel">
        <div className="note-title">
          <Info size={16} className="text-accent" />
          <span>策略指標評價與決策原則說明</span>
        </div>
        <div className="note-grid">
          <div className="note-item">
            <strong>🔥 多時距混合動能 (60%)</strong>：結合 1d (50%)、7d (30%) 與 30d (20%) 多週期日線漲跌幅。Z &gt; 1.5 判定為「動能極強」，Z &lt; -1.5 判定為「動能極弱」。
          </div>
          <div className="note-item">
            <strong>⚠️ 平滑資金費率反向 (40%)</strong>：取最近 3 次 8 小時結算費率之平滑平均反向取值。深度負費率代表貼水超賣，反向加分。
          </div>
          <div className="note-item">
            <strong>🛡️ 基準指數對沖 (v3.0 Opt C)</strong>：空方改採單主流 BTCUSDT（或雙主流 BTC+ETH）佔 50% 名義資金對沖系統性 Beta，徹底規避小幣反抽軋空；多方維持 Top 5 動能強者（各 10% 資金）獲取橫截面超額 Alpha。
          </div>
          <div className="note-item">
            <strong>🛡️ Regime Gate 風險閘門</strong>：即時監測 BTC 50 日均線、宇宙 50 日廣度 (&gt;30%) 與全市場費率百分位 (&gt;45%)。具備 T1 停單清倉與 T2 崩跌延後防護。
          </div>
          <div className="note-item">
            <strong>⚪ 主動買賣流與大戶博弈 (0%)</strong>：v2 優化已依五年實證統計剃除無 Alpha 貢獻因子，保留欄位以相容介面。
          </div>
        </div>
      </footer>
    </div>
  );
};

export default CrossSectionRankings;
