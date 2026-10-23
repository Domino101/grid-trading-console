import React, { useState, useEffect, useMemo } from 'react';
import {
  Trophy,
  Flame,
  Search,
  RefreshCw,
  Sparkles,
  TrendingUp,
  AlertCircle,
  Activity,
  Layers,
  ArrowRight,
  ShieldCheck,
} from 'lucide-react';

import { api, ApiError, type MarketRankingItem, type MarketRankingsResponse } from '../services/api';
import { t } from '../i18n/index.ts';

interface MarketRankingsProps {
  onSelectSymbol: (item: MarketRankingItem, period: string) => void;
  onBacktest?: (item: MarketRankingItem, period: string) => void;
  onUnauthorized?: () => void;
}

const MarketRankings: React.FC<MarketRankingsProps> = ({ onSelectSymbol, onBacktest, onUnauthorized }) => {
  const [exchange, setExchange] = useState<string>('binance');
  const [period, setPeriod] = useState<string>('7d');
  const [search, setSearch] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [data, setData] = useState<MarketRankingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchRankings = async (targetExchange = exchange, targetPeriod = period) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getMarketRankings(targetExchange, targetPeriod, 25);
      setData(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && onUnauthorized) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : t("無法讀取市場年化排行榜"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 交易所/週期變更時重新抓排行；fetchRankings 內含 setState（載入態）故此處抑制規則。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchRankings(exchange, period);
  }, [exchange, period]);

  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    if (!search.trim()) return data.items;
    const q = search.trim().toUpperCase();
    return data.items.filter((item) => item.symbol.toUpperCase().includes(q));
  }, [data, search]);

  const topPerformer = useMemo(() => {
    if (!data?.items || data.items.length === 0) return null;
    return data.items[0];
  }, [data]);

  const avgApr = useMemo(() => {
    if (!data?.items || data.items.length === 0) return 0;
    const total = data.items.reduce((acc, curr) => acc + curr.estimated_apr, 0);
    return Math.round((total / data.items.length) * 100) / 100;
  }, [data]);

  const getRankBadge = (rank: number) => {
    if (rank === 1) return <span className="rank-medal gold">🥇 1</span>;
    if (rank === 2) return <span className="rank-medal silver">🥈 2</span>;
    if (rank === 3) return <span className="rank-medal bronze">🥉 3</span>;
    return <span className="rank-number">{rank}</span>;
  };

  const getRatingBadge = (rating: string) => {
    switch (rating) {
      case 'S':
        return <span className="badge-rating rating-s">{t("🔥 S 級")}</span>;
      case 'A':
        return <span className="badge-rating rating-a">{t("⭐ A 級")}</span>;
      case 'B':
        return <span className="badge-rating rating-b">{t("B 級")}</span>;
      default:
        return <span className="badge-rating rating-c">{t("C 級")}</span>;
    }
  };

  const formatVolume = (vol: number) => {
    if (vol >= 1_000_000_000) {
      return `$${(vol / 1_000_000_000).toFixed(2)}B`;
    }
    if (vol >= 1_000_000) {
      return `$${(vol / 1_000_000).toFixed(1)}M`;
    }
    return `$${Math.round(vol).toLocaleString()}`;
  };

  return (
    <div className="rankings-page">
      {/* Header */}
      <header className="rankings-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <div className="brand-icon-wrapper" style={{ width: '32px', height: '32px' }}>
                <Trophy size={18} color="#a855f7" />
              </div>
              <h2 style={{ fontSize: '1.35rem', fontWeight: 700 }}>{t("網格策略市場 · 歷史回測年化排行榜")}</h2>
            </div>
            <p className="muted" style={{ marginTop: '0.25rem', fontSize: '0.84rem' }}>
              {t("基於真實歷史 K 線撮合模擬 · 依年化收益率與流動性排行 · 支援一鍵自動帶入參數開格")}{' '}</p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
            <div className="safe-cache-pill">
              <ShieldCheck size={14} color="#10b981" />
              <span>{t("15分鐘平滑快取防護 · 零 API 額度消耗")}</span>
            </div>

            <button
              type="button"
              className="btn btn-outline"
              onClick={() => void fetchRankings(exchange, period)}
              disabled={loading}
              style={{ fontSize: '0.82rem', padding: '0.5rem 0.85rem' }}
            >
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
              <span>{loading ? t("計算中…") : t("重新整理")}</span>
            </button>
          </div>
        </div>
      </header>

      {/* KPI Cards */}
      <div className="rankings-kpi-grid">
        <div className="rankings-kpi-card">
          <div className="kpi-icon-box" style={{ background: 'rgba(239, 68, 68, 0.15)', color: '#ef4444' }}>
            <Flame size={20} />
          </div>
          <div>
            <span className="kpi-label">{t("最佳年化收益標的")}</span>
            <div className="kpi-value-row">
              <strong className="kpi-main-val" style={{ color: '#10b981' }}>
                {topPerformer ? `${topPerformer.estimated_apr.toFixed(1)}%` : '—'}
              </strong>
              {topPerformer && <span className="kpi-sub-tag">{topPerformer.symbol}</span>}
            </div>
          </div>
        </div>

        <div className="rankings-kpi-card">
          <div className="kpi-icon-box" style={{ background: 'rgba(99, 102, 241, 0.15)', color: 'var(--accent-primary)' }}>
            <TrendingUp size={20} />
          </div>
          <div>
            <span className="kpi-label">{t("市場平均回測年化 (APR)")}</span>
            <div className="kpi-value-row">
              <strong className="kpi-main-val">+{avgApr.toFixed(1)}%</strong>
              <span className="kpi-sub-tag">{period.toUpperCase()} {' '}{t("週期")}</span>
            </div>
          </div>
        </div>

        <div className="rankings-kpi-card">
          <div className="kpi-icon-box" style={{ background: 'rgba(6, 182, 212, 0.15)', color: 'var(--accent-cyan)' }}>
            <Layers size={20} />
          </div>
          <div>
            <span className="kpi-label">{t("監控高流動性代幣")}</span>
            <div className="kpi-value-row">
              <strong className="kpi-main-val">{data?.items?.length ?? 0} {' '}{t("個交易對")}</strong>
              <span className="kpi-sub-tag">24h Vol &gt; $1M</span>
            </div>
          </div>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="rankings-toolbar glass-panel">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          {/* Exchange Switcher */}
          <div className="select-pill-group">
            <button
              type="button"
              className={`select-pill ${exchange === 'binance' ? 'active' : ''}`}
              onClick={() => setExchange('binance')}
            >
              {t("Binance (幣安)")}{' '}</button>
            <button
              type="button"
              className={`select-pill ${exchange === 'okx' ? 'active' : ''}`}
              onClick={() => setExchange('okx')}
            >
              OKX
            </button>
            <button
              type="button"
              className={`select-pill ${exchange === 'hyperliquid' ? 'active' : ''}`}
              onClick={() => setExchange('hyperliquid')}
            >
              Hyperliquid
            </button>
            <button
              type="button"
              className={`select-pill ${exchange === 'lighter' ? 'active' : ''}`}
              onClick={() => setExchange('lighter')}
            >
              Lighter
            </button>
            <button
              type="button"
              className={`select-pill ${exchange === 'backpack' ? 'active' : ''}`}
              onClick={() => setExchange('backpack')}
            >
              Backpack
            </button>
          </div>

          {/* Period Switcher */}
          <div className="select-pill-group">
            <button
              type="button"
              className={`select-pill ${period === '7d' ? 'active' : ''}`}
              onClick={() => setPeriod('7d')}
            >
              {t("7 天 (7D)")}{' '}</button>
            <button
              type="button"
              className={`select-pill ${period === '30d' ? 'active' : ''}`}
              onClick={() => setPeriod('30d')}
            >
              {t("30 天 (30D)")}{' '}</button>
            <button
              type="button"
              className={`select-pill ${period === '180d' ? 'active' : ''}`}
              onClick={() => setPeriod('180d')}
            >
              {t("180 天 (180D)")}{' '}</button>
          </div>
        </div>

        {/* Search Box */}
        <div className="search-input-wrapper" style={{ maxWidth: '280px', width: '100%' }}>
          <Search size={16} className="search-icon" />
          <input
            type="text"
            className="search-input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("搜尋幣種 (例: BTC, SOL, ETH)...")}
          />
        </div>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="notice notice-error">
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      )}

      {/* Table Container */}
      <div className="rankings-table-container glass-panel">
        {loading && !data ? (
          <div style={{ padding: '4rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
            <Activity size={28} className="spin" style={{ margin: '0 auto 0.75rem' }} />
            <p>{t("正在分析全市場歷史 K 線與虛擬網格撮合年化…")}</p>
          </div>
        ) : filteredItems.length === 0 ? (
          <div style={{ padding: '3.5rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
            <p>{t("未找到符合條件的交易對。")}</p>
          </div>
        ) : (
          <table className="rankings-table">
            <thead>
              <tr>
                <th style={{ width: '70px', textAlign: 'center' }}>{t("排名")}</th>
                <th>{t("交易對 (Symbol)")}</th>
                <th>{t("當前價格")}</th>
                <th>{t("24h 成交額")}</th>
                <th>{period.toUpperCase()} {' '}{t("回測年化 (APR)")}</th>
                <th>{t("推薦震盪區間 ($Lower ~ $Upper)")}</th>
                <th>{t("推薦網格")}</th>
                <th>{t("評級")}</th>
                <th style={{ textAlign: 'right' }}>{t("操作")}</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item) => (
                <tr key={item.symbol} className="rankings-row">
                  <td style={{ textAlign: 'center' }}>{getRankBadge(item.rank)}</td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <strong style={{ fontSize: '0.95rem', letterSpacing: '-0.01em' }}>{item.symbol}</strong>
                      <span
                        className={`change-pill ${item.price_change_24h_percent >= 0 ? 'positive' : 'negative'}`}
                      >
                        {item.price_change_24h_percent >= 0 ? '+' : ''}
                        {item.price_change_24h_percent.toFixed(2)}%
                      </span>
                    </div>
                  </td>
                  <td>
                    <span className="mono-num">${item.current_price.toLocaleString()}</span>
                  </td>
                  <td>
                    <span className="mono-num" style={{ color: 'var(--text-secondary)' }}>
                      {formatVolume(item.volume_24h)}
                    </span>
                  </td>
                  <td>
                    <div className="apr-display-cell">
                      <Flame size={15} color="#10b981" />
                      <strong className="mono-num apr-num">+{item.estimated_apr.toFixed(2)}%</strong>
                    </div>
                  </td>
                  <td>
                    <div className="range-display-cell">
                      <span className="mono-num">
                        ${item.lower_price.toLocaleString()} ~ ${item.upper_price.toLocaleString()}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="grid-density-cell">
                      <span className="mono-num">{item.grid_count} {' '}{t("格")}</span>
                      <span className="grid-sub-info">(${item.grid_interval})</span>
                    </div>
                  </td>
                  <td>{getRatingBadge(item.rating)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: '0.4rem', justifyContent: 'flex-end' }}>
                      <button
                        type="button"
                        className="btn-create-grid"
                        onClick={() => onSelectSymbol(item, period)}
                      >
                        <Sparkles size={14} />
                        <span>{t("一鍵開格")}</span>
                        <ArrowRight size={13} />
                      </button>
                      {onBacktest && (
                        <button
                          type="button"
                          className="btn btn-outline"
                          style={{ fontSize: '0.74rem', padding: '0.42rem 0.6rem' }}
                          onClick={() => onBacktest(item, period)}
                        >
                          <Activity size={13} />
                          <span>{t("回測")}</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default MarketRankings;
