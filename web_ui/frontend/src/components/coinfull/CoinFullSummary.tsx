import React from 'react';
import { ArrowRight } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import type { CoinFullTab } from './useCoinFullPrime';
import { cleanText, formatPct, formatPrice, formatScore, formatSigned, formatUsd, localizeEvaluation } from './format';
import { t } from '../../i18n/index.ts';

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
    <section className="cfp-summary" aria-label={t("策略摘要")}>
      <article className="cfp-card cfp-card-feature is-long">
        <header className="cfp-card-head">
          <span className="cfp-card-code">01</span>
          <span className="cfp-card-label">{t("多頭首選")}{' '}<em>/ MOMENTUM</em></span>
        </header>
        <div className="cfp-feature-main">
          <div className="cfp-feature-id">
            <div className="cfp-symbol">{top1Long?.symbol ?? '—'}</div>
            <span className="cfp-eval">{localizeEvaluation(cleanText(top1Long?.evaluations?.overall_eval)) || '—'}</span>
          </div>
          <div className="cfp-feature-score is-long">{formatScore(top1Long?.score)}</div>
        </div>
        <div className="cfp-feature-meta">
          {t("配置")}{' '}{formatUsd(manualSizing.perSymbolNotional)} · {perSymbolPct}{t("% 總名義")}{' '}</div>
        <footer className="cfp-card-foot">
          <span>{t("動能")}{' '}<b className="text-positive">{formatPct(top1Long?.raw_params?.price_change_pct ?? 0)}</b></span>
          <span>{t("費率")}{' '}<b>{formatPct(top1Long?.raw_params?.funding_rate_pct ?? 0, 4)}</b></span>
        </footer>
      </article>

      <article className="cfp-card is-short">
        <header className="cfp-card-head">
          <span className="cfp-card-code">02</span>
          <span className="cfp-card-label">{isBenchmarkHedge ? t("基準對沖") : t("空頭首選")} <em>/ HEDGE</em></span>
        </header>
        <div className="cfp-hedge-main">
          <div className="cfp-feature-id">
            <div className="cfp-symbol">
              {isBenchmarkHedge
                ? (shortList.length > 0 ? shortList.map((item) => item.symbol.replace('USDT', '')).join(' + ') : 'BTC')
                : (top1Short?.symbol ?? '—')}
            </div>
            <span className="cfp-eval">
              {isBenchmarkHedge ? t("對沖空頭名義") : (localizeEvaluation(cleanText(top1Short?.evaluations?.overall_eval)) || '—')}
            </span>
          </div>
          {isBenchmarkHedge ? (
            <div className="cfp-ring" aria-label={t("對沖比重 50%")}>
              <svg viewBox="0 0 64 64" aria-hidden="true">
                <circle cx="32" cy="32" r="25" className="cfp-ring-track" />
                <circle cx="32" cy="32" r="25" className="cfp-ring-value" />
              </svg>
              <span>50.0%</span>
            </div>
          ) : (
            <div className="cfp-feature-score is-short">{formatScore(top1Short?.score)}</div>
          )}
        </div>
        <div className="cfp-feature-meta">
          {isBenchmarkHedge
            ? (t("配置 ") + (formatUsd(manualSizing.shortSleeve)) + t(" · 市價 ") + (formatPrice(top1Short?.raw_params?.price)))
            : (t("配置 ") + (formatUsd(manualSizing.shortSleeve)))}
        </div>
        <footer className="cfp-card-foot">
          <span>{isBenchmarkHedge ? t("系統性 Beta 對沖 · 單腿空") : (t("動能 ") + (formatPct(top1Short?.raw_params?.price_change_pct ?? 0)))}</span>
        </footer>
      </article>

      <article className={`cfp-card is-neutral ${hasManaged ? '' : 'is-empty'}`}>
        <header className="cfp-card-head">
          <span className="cfp-card-code">03</span>
          <span className="cfp-card-label">{t("CoinFull 持倉")}{' '}<em>/ EXPOSURE</em></span>
        </header>
        <div className="cfp-zero"><b>{managedStats.count}</b><span>{t("筆")}</span></div>
        <div className="cfp-duo">
          <div><span>{t("多")}</span><b>{managedStats.longCount}</b></div>
          <div><span>{t("空")}</span><b>{managedStats.shortCount}</b></div>
        </div>
        {hasManaged ? (
          <footer className="cfp-card-foot">
            <span className={`cfp-pnl ${managedStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
              {formatSigned(managedStats.totalPnl)} <span>USDT</span>
            </span>
            <span>{t("淨敞口")}{' '}{formatSigned(managedStats.netExposure)}</span>
          </footer>
        ) : (
          <p className="cfp-empty-note">
            {t("尚未建立 CoinFull 持倉；下次換倉將配置")}{' '}{targetCount} {' '}{t("多 /")}{' '}{isBenchmarkHedge ? '1' : targetCount} {' '}{t("空，單幣約")}{' '}{perSymbolPct}{t("% 總名義。")}{' '}</p>
        )}
      </article>

      <article className="cfp-card is-risk">
        <header className="cfp-card-head">
          <span className="cfp-card-code">04</span>
          <span className="cfp-card-label">{t("防護與帳戶")}{' '}<em>/ GUARD</em></span>
        </header>
        <dl className="cfp-risk-list">
          <div>
            <dt>{t("隔離標的")}</dt>
            <dd>{protectedCount} {' '}{t("個")}</dd>
          </div>
          <div>
            <dt>{t("母體覆蓋")}</dt>
            <dd>{universeSize} {' '}{t("幣")}</dd>
          </div>
          <div>
            <dt>{t("其他策略")}</dt>
            <dd>{externalStats.count} {' '}{t("筆 ·")}{' '}{formatUsd(externalStats.totalNotional)}</dd>
          </div>
        </dl>
        <footer className="cfp-card-foot is-split">
          <span className="cfp-muted">{t("外部持倉不計入策略績效")}</span>
          <button type="button" className="cfp-text-link" onClick={() => onOpenTab('positions')}>
            {t("查看")}{' '}<ArrowRight size={13} aria-hidden="true" />
          </button>
        </footer>
      </article>
    </section>
  );
};