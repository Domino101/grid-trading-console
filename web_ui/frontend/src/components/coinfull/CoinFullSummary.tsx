import React from 'react';
import { ArrowRight, Layers, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import type { CoinFullTab } from './useCoinFullPrime';
import { cleanText, formatPct, formatPrice, formatScore, formatSigned, formatUsd } from './format';

interface Props {
  controller: CoinFullPrimeController;
  onOpenTab: (tab: CoinFullTab) => void;
}

export const CoinFullSummary: React.FC<Props> = ({ controller, onOpenTab }) => {
  const {
    top1Long,
    top1Short,
    isBenchmarkHedge,
    shortList,
    targetCount,
    manualSizing,
    managedStats,
    externalStats,
    universeSize,
    statusInfo,
  } = controller;
  const perSymbolPct = (50 / Math.max(1, targetCount)).toFixed(1);
  const protectedCount = statusInfo?.protected_bot_symbols?.length ?? 0;
  const hasManaged = managedStats.count > 0;

  return (
    <section className="cfp-summary" aria-label="策略摘要">
      <article className="cfp-summary-card is-long">
        <header>
          <span>多頭首選</span>
          <TrendingUp size={18} aria-hidden="true" />
        </header>
        <div className="cfp-symbol">{top1Long?.symbol ?? '—'}</div>
        <div className="cfp-score-row">
          <span className="cfp-score is-long">{formatScore(top1Long?.score)}</span>
          <span className="cfp-eval">{cleanText(top1Long?.evaluations?.overall_eval) || '—'}</span>
        </div>
        <div className="cfp-card-metric">配置 {formatUsd(manualSizing.perSymbolNotional)} · {perSymbolPct}% 總名義</div>
        <footer>
          動能 {formatPct(top1Long?.raw_params?.price_change_pct ?? 0)} · 費率{' '}
          {formatPct(top1Long?.raw_params?.funding_rate_pct ?? 0, 4)}
        </footer>
      </article>

      <article className="cfp-summary-card is-short">
        <header>
          <span>{isBenchmarkHedge ? '基準對沖' : '空頭首選'}</span>
          <TrendingDown size={18} aria-hidden="true" />
        </header>
        <div className="cfp-symbol">
          {isBenchmarkHedge
            ? (shortList.length > 0 ? shortList.map((item) => item.symbol.replace('USDT', '')).join(' + ') : 'BTC')
            : (top1Short?.symbol ?? '—')}
        </div>
        <div className="cfp-score-row">
          {isBenchmarkHedge ? (
            <>
              <span className="cfp-score is-short">50.0%</span>
              <span className="cfp-eval">對沖空頭名義</span>
            </>
          ) : (
            <>
              <span className="cfp-score is-short">{formatScore(top1Short?.score)}</span>
              <span className="cfp-eval">{cleanText(top1Short?.evaluations?.overall_eval) || '—'}</span>
            </>
          )}
        </div>
        <footer>
          {isBenchmarkHedge
            ? `配置 ${formatUsd(manualSizing.shortSleeve)} · 市價 ${formatPrice(top1Short?.raw_params?.price)}`
            : `動能 ${formatPct(top1Short?.raw_params?.price_change_pct ?? 0)} · 配置 ${formatUsd(manualSizing.shortSleeve)}`}
        </footer>
      </article>

      <article className={`cfp-summary-card is-neutral ${hasManaged ? '' : 'is-empty'}`}>
        <header>
          <span>CoinFull 持倉</span>
          <Layers size={18} aria-hidden="true" />
        </header>
        <div className="cfp-metric-row">
          <span className="cfp-metric">{managedStats.count} 筆</span>
          <span className="cfp-metric-sub">多 {managedStats.longCount} · 空 {managedStats.shortCount}</span>
        </div>
        {hasManaged ? (
          <>
            <div className={`cfp-pnl ${managedStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
              {formatSigned(managedStats.totalPnl)} <span>USDT</span>
            </div>
            <footer>
              名義 {formatUsd(managedStats.totalNotional)} · 淨敞口 {formatSigned(managedStats.netExposure)}
            </footer>
          </>
        ) : (
          <footer className="cfp-empty-note">
            尚未建立 CoinFull 持倉；下次換倉將配置 {targetCount} 多 / {isBenchmarkHedge ? '1' : targetCount} 空，單幣約 {perSymbolPct}% 總名義。
          </footer>
        )}
      </article>

      <article className="cfp-summary-card is-risk">
        <header>
          <span>防護與帳戶</span>
          <ShieldCheck size={18} aria-hidden="true" />
        </header>
        <dl className="cfp-risk-list">
          <div>
            <dt>隔離標的</dt>
            <dd>{protectedCount} 個</dd>
          </div>
          <div>
            <dt>母體覆蓋</dt>
            <dd>{universeSize} 幣</dd>
          </div>
          <div>
            <dt>其他策略</dt>
            <dd>{externalStats.count} 筆 · {formatUsd(externalStats.totalNotional)}</dd>
          </div>
        </dl>
        <footer>
          <span className="cfp-muted">外部持倉不計入策略績效</span>
          <button type="button" className="cfp-text-link" onClick={() => onOpenTab('positions')}>
            查看持倉 <ArrowRight size={13} aria-hidden="true" />
          </button>
        </footer>
      </article>
    </section>
  );
};