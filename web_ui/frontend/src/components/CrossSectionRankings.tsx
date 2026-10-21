import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, Bot, RefreshCw, Sliders, TrendingDown, TrendingUp } from 'lucide-react';
import { api, ApiError } from '../services/api';
import type { RankedItem, UniverseRankResult } from '../services/api';
import { RankingsToolbar } from './coinfull/RankingsToolbar';
import { RankingsTable } from './coinfull/RankingsTable';
import { RankingsHelp } from './coinfull/RankingsHelp';
import { formatPct, formatPrice, formatScore, formatTaipei, relativeAge } from './coinfull/format';
import type { RankingFilter } from './coinfull/useCoinFullPrime';

interface CrossSectionRankingsProps {
  onUnauthorized?: () => void;
  onGoToBot?: () => void;
}

const readFilter = (): RankingFilter => {
  const saved = localStorage.getItem('cross_section_filter_tab');
  return saved === 'long' || saved === 'short' || saved === 'all' || saved === 'baskets' ? saved : 'baskets';
};

export const CrossSectionRankings: React.FC<CrossSectionRankingsProps> = ({
  onUnauthorized,
  onGoToBot,
}) => {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rankResult, setRankResult] = useState<UniverseRankResult | null>(null);
  const [excludeTradfi, setExcludeTradfi] = useState<boolean>(() => localStorage.getItem('coinfull_exclude_tradfi') !== 'false');
  const [filterTab, setFilterTab] = useState<RankingFilter>(readFilter);
  const [searchQuery, setSearchQuery] = useState('');
  const [showDisabledFactors, setShowDisabledFactors] = useState(false);
  const unauthorizedRef = useRef(onUnauthorized);
  const excludeTradfiRef = useRef(excludeTradfi);

  useEffect(() => {
    unauthorizedRef.current = onUnauthorized;
  }, [onUnauthorized]);

  useEffect(() => {
    excludeTradfiRef.current = excludeTradfi;
  }, [excludeTradfi]);

  const loadData = useCallback(async (isRefresh = false, overrides?: boolean) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const rankings = await api.getCoinFullPrimeRankings(isRefresh, overrides ?? excludeTradfiRef.current);
      setRankResult(rankings);
    } catch (err: unknown) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        unauthorizedRef.current?.();
        return;
      }
      setError(err instanceof Error ? err.message : '載入多空截面排行失敗');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadData(false);
  }, [loadData]);

  const toggleTradfi = (next: boolean) => {
    setExcludeTradfi(next);
    localStorage.setItem('coinfull_exclude_tradfi', String(next));
    void loadData(true, next);
  };

  const changeFilter = (next: RankingFilter) => {
    setFilterTab(next);
    localStorage.setItem('cross_section_filter_tab', next);
  };

  const longList = useMemo<RankedItem[]>(() => rankResult?.long_list ?? [], [rankResult?.long_list]);
  const shortList = useMemo<RankedItem[]>(() => rankResult?.short_list ?? [], [rankResult?.short_list]);
  const targetCount = longList.length || 5;
  const isBenchmarkHedge = shortList.length === 0 || shortList.length <= 2 || shortList.some((item) => item.symbol === 'BTCUSDT' || item.evaluations?.overall_eval?.includes('基準'));
  const top1Long = longList[0];
  const top1Short = shortList.length > 0 ? (isBenchmarkHedge ? shortList[0] : shortList[shortList.length - 1]) : undefined;
  const universeSize = rankResult?.universe_size ?? 0;

  const displayedItems = useMemo<RankedItem[]>(() => {
    if (!rankResult) return [];
    const query = searchQuery.trim().toUpperCase();
    if (query) return (rankResult.all_rankings ?? []).filter((item) => item.symbol.toUpperCase().includes(query));
    if (filterTab === 'baskets') return [...longList, ...shortList];
    if (filterTab === 'long') return longList;
    if (filterTab === 'short') return shortList;
    return rankResult.all_rankings ?? [];
  }, [filterTab, longList, rankResult, searchQuery, shortList]);

  if (loading && !rankResult) {
    return (
      <div className="coinfull-container cfp-page">
        <div className="cfp-panel cfp-loading">
          <Activity size={30} className="spin text-accent" aria-hidden="true" />
          <h3>正在計算多空截面排行</h3>
          <p>聚合多時距動能與資金費率因子。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="coinfull-container cfp-page">
      <header className="cfp-header">
        <div className="cfp-header-title">
          <div className="cfp-title-row">
            <h2>多空截面排行</h2>
            <span className="badge badge-accent">市場中性 Alpha</span>
            <span className="badge badge-neutral">盤面分析視角</span>
          </div>
          <p className="cfp-subtitle">
            對 {universeSize || '—'} 個主流幣合約計算動能（60%）與資金費率反向（40%），只用來選幣，不下單。
          </p>
        </div>
        <div className="cfp-header-actions">
          <span className="cfp-data-time">
            {rankResult?.timestamp ? `資料 ${formatTaipei(rankResult.timestamp)} · ${relativeAge(rankResult.timestamp)}` : '尚無資料'}
          </span>
          <button type="button" className="btn btn-outline" onClick={() => void loadData(true)} disabled={refreshing}>
            <RefreshCw size={15} className={refreshing ? 'spin' : ''} aria-hidden="true" />
            <span>{refreshing ? '重新計算中…' : '重新計算'}</span>
          </button>
          {onGoToBot && (
            <button type="button" className="btn btn-primary" onClick={onGoToBot}>
              <Bot size={15} aria-hidden="true" />
              <span>前往機器人</span>
              <ArrowRight size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="alert-banner alert-danger" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <span>{error}</span>
          <button type="button" className="close-btn" onClick={() => setError(null)} aria-label="關閉錯誤訊息">×</button>
        </div>
      )}

      <section className="cfp-summary" aria-label="排行摘要">
        <article className="cfp-summary-card is-long">
          <header><span>多頭首選</span><TrendingUp size={18} aria-hidden="true" /></header>
          <div className="cfp-symbol">{top1Long?.symbol ?? '—'}</div>
          <div className="cfp-score-row">
            <span className="cfp-score is-long">{formatScore(top1Long?.score)}</span>
            <span className="cfp-eval">{top1Long?.evaluations?.overall_eval ?? '—'}</span>
          </div>
          <footer>動能 {formatPct(top1Long?.raw_params?.price_change_pct ?? 0)} · 費率 {formatPct(top1Long?.raw_params?.funding_rate_pct ?? 0, 4)}</footer>
        </article>

        <article className="cfp-summary-card is-short">
          <header><span>{isBenchmarkHedge ? '基準對沖' : '空頭首選'}</span><TrendingDown size={18} aria-hidden="true" /></header>
          <div className="cfp-symbol">
            {isBenchmarkHedge
              ? (shortList.length > 0 ? shortList.map((item) => item.symbol.replace('USDT', '')).join(' + ') : 'BTC')
              : (top1Short?.symbol ?? '—')}
          </div>
          <div className="cfp-score-row">
            {isBenchmarkHedge ? (
              <><span className="cfp-score is-short">50.0%</span><span className="cfp-eval">對沖空頭名義</span></>
            ) : (
              <><span className="cfp-score is-short">{formatScore(top1Short?.score)}</span><span className="cfp-eval">{top1Short?.evaluations?.overall_eval ?? '—'}</span></>
            )}
          </div>
          <footer>市價 {formatPrice(top1Short?.raw_params?.price)} · 對沖系統性 Beta</footer>
        </article>

        <article className="cfp-summary-card is-neutral">
          <header><span>籃子配置</span><Sliders size={18} aria-hidden="true" /></header>
          <div className="cfp-metric-row">
            <span className="cfp-metric">{targetCount} 多</span>
            <span className="cfp-metric-sub">vs {isBenchmarkHedge ? `${shortList.length || 1} 對沖` : `${shortList.length || targetCount} 空`}</span>
          </div>
          <footer>
            {isBenchmarkHedge
              ? `多頭各 ${(50 / targetCount).toFixed(1)}% · 基準對沖 50%`
              : `多頭各 ${(50 / targetCount).toFixed(1)}% · 空頭各 ${(50 / (shortList.length || targetCount)).toFixed(1)}%`}
          </footer>
        </article>

        <article className="cfp-summary-card is-risk">
          <header><span>執行導引</span><Bot size={18} aria-hidden="true" /></header>
          <dl className="cfp-risk-list">
            <div><dt>母體覆蓋</dt><dd>{universeSize} 幣</dd></div>
            <div><dt>多空籃子</dt><dd>{longList.length} / {shortList.length}</dd></div>
            <div><dt>本頁</dt><dd>只讀排行</dd></div>
          </dl>
          <footer>
            <span className="cfp-muted">調倉與排程請到機器人頁</span>
            {onGoToBot && (
              <button type="button" className="cfp-text-link" onClick={onGoToBot}>
                前往 <ArrowRight size={13} aria-hidden="true" />
              </button>
            )}
          </footer>
        </article>
      </section>

      <RankingsToolbar
        filterTab={filterTab}
        onFilter={changeFilter}
        counts={{
          baskets: longList.length + shortList.length,
          long: longList.length,
          short: shortList.length,
          all: rankResult?.all_rankings?.length ?? 0,
        }}
        universeSize={universeSize}
        excludeTradfi={excludeTradfi}
        onToggleTradfi={toggleTradfi}
        searchQuery={searchQuery}
        onSearch={setSearchQuery}
        showDisabledFactors={showDisabledFactors}
        onToggleDisabled={setShowDisabledFactors}
        busy={refreshing}
      />

      <section className="cfp-panel cfp-rankings">
        <RankingsTable
          items={displayedItems}
          isBenchmarkHedge={isBenchmarkHedge}
          grossExposure={0}
          showDisabledFactors={showDisabledFactors}
          universeSize={universeSize}
        />
      </section>

      <RankingsHelp />
    </div>
  );
};

export default CrossSectionRankings;