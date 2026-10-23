import React from 'react';
import { Layers, RefreshCw, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import type { CoinFullPrimePosition } from '../../services/api';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import { formatPrice, formatQty, formatSigned, formatUsd } from './format';
import { t } from '../../i18n/index.ts';

interface Props {
  controller: CoinFullPrimeController;
}

const PositionCard: React.FC<{
  position: CoinFullPrimePosition;
  managed: boolean;
  inBasket: boolean;
  isBenchmarkHedge: boolean;
}> = ({ position, managed, inBasket, isBenchmarkHedge }) => {
  const isLong = position.side === 'long';
  const tag = managed
    ? (inBasket
      ? { className: 'badge-success', label: t("留榜 · 換倉僅微調") }
      : { className: 'badge-warning', label: t("退榜 · 下次平倉") })
    : { className: 'badge-neutral', label: t("外部策略 · 已隔離") };

  return (
    <article className={`cfp-position-card ${isLong ? 'is-long' : 'is-short'} ${managed ? '' : 'is-external'}`}>
      <header>
        <span className="cfp-position-symbol">{position.symbol}</span>
        <span className={`badge ${isLong ? 'badge-long' : 'badge-short'}`}>
          {isLong ? t("多") : isBenchmarkHedge ? t("對沖空") : t("空")}
        </span>
        <span className={`badge ${tag.className}`}>{tag.label}</span>
      </header>
      <dl className="cfp-position-body">
        <div><dt>{t("數量")}</dt><dd>{formatQty(position.size)}</dd></div>
        <div><dt>{t("名義")}</dt><dd>{formatUsd(position.notional_usdt)}</dd></div>
        <div><dt>{t("開倉均價")}</dt><dd>{formatPrice(position.entry_price)}</dd></div>
        <div>
          <dt>{t("未實現")}</dt>
          <dd className={position.unrealized_pnl >= 0 ? 'text-positive' : 'text-negative'}>
            {formatSigned(position.unrealized_pnl)} USDT
          </dd>
        </div>
      </dl>
    </article>
  );
};

export const CoinFullPositions: React.FC<Props> = ({ controller }) => {
  const {
    managedPositions,
    externalPositions,
    managedStats,
    externalStats,
    accountStats,
    longList,
    shortList,
    isBenchmarkHedge,
    loadingPositions,
    selectedAccount,
    actions,
  } = controller;
  const isPaper = selectedAccount === 'paper';
  const inBasket = (position: CoinFullPrimePosition): boolean =>
    longList.some((item) => item.symbol === position.symbol) || shortList.some((item) => item.symbol === position.symbol);

  return (
    <section className="cfp-panel cfp-positions" aria-label={t("當前持倉")}>
      <header className="cfp-section-header">
        <div className="cfp-section-title">
          <Layers size={18} aria-hidden="true" />
          <h3>{t("CoinFull 專屬籃子")}</h3>
          <span className="badge badge-neutral">{managedStats.count} {' '}{t("筆")}</span>
          {managedStats.count > 0 && (
            <span className={`cfp-section-pnl ${managedStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
              {formatSigned(managedStats.totalPnl)} USDT
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={() => void actions.refreshPositions()}
          disabled={loadingPositions}
        >
          <RefreshCw size={13} className={loadingPositions ? 'spin' : ''} aria-hidden="true" />
          <span>{loadingPositions ? t("查詢中…") : t("刷新持倉")}</span>
        </button>
      </header>

      {managedPositions.length === 0 ? (
        <div className="cfp-empty">
          <strong>{isPaper ? t("Paper 帳號沒有交易所持倉") : t("CoinFull 目前沒有持倉")}</strong>
          <p>
            {isPaper
              ? t("Paper 採本地無狀態撮合；切換至 Binance 測試網或實盤可讀取交易所庫存。")
              : (t("下次換倉會建立 ") + (longList.length || 5) + t(" 筆多頭與 ") + (isBenchmarkHedge ? t('1 筆基準對沖') : (shortList.length || 5) + t(" 筆空頭")) + t("。"))}
          </p>
          {externalPositions.length > 0 && (
            <p className="cfp-muted">
              {t("帳戶另有")}{' '}{externalPositions.length} {' '}{t("筆其他策略持倉（")}{formatUsd(externalStats.totalNotional)}{t("），已隔離且不計入策略績效。")}{' '}</p>
          )}
        </div>
      ) : (
        <>
          <div className="cfp-pnl-banner">
            <div className="cfp-pnl-card is-long">
              <header><span><TrendingUp size={14} aria-hidden="true" />{t("多頭未實現")}</span><span className="badge badge-long">{managedStats.longCount} {' '}{t("筆")}</span></header>
              <div className={managedStats.longPnl >= 0 ? 'text-positive' : 'text-negative'}>
                {formatSigned(managedStats.longPnl)} <span className="cfp-unit">USDT</span>
              </div>
              <footer>
                {t("名義")}{' '}{formatUsd(managedStats.longNotional)}
                {managedStats.longNotional > 0 && ` · ${((managedStats.longPnl / managedStats.longNotional) * 100).toFixed(2)}%`}
              </footer>
            </div>
            <div className="cfp-pnl-card is-short">
              <header><span><TrendingDown size={14} aria-hidden="true" />{t("空頭未實現")}</span><span className="badge badge-short">{managedStats.shortCount} {' '}{t("筆")}</span></header>
              <div className={managedStats.shortPnl >= 0 ? 'text-positive' : 'text-negative'}>
                {formatSigned(managedStats.shortPnl)} <span className="cfp-unit">USDT</span>
              </div>
              <footer>
                {t("名義")}{' '}{formatUsd(managedStats.shortNotional)}
                {managedStats.shortNotional > 0 && ` · ${((managedStats.shortPnl / managedStats.shortNotional) * 100).toFixed(2)}%`}
              </footer>
            </div>
            <div className="cfp-pnl-card is-total">
              <header><span><ShieldCheck size={14} aria-hidden="true" />{t("策略合計")}</span><span className="badge badge-neutral">{managedStats.count} {' '}{t("筆")}</span></header>
              <div className={managedStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}>
                {formatSigned(managedStats.totalPnl)} <span className="cfp-unit">USDT</span>
              </div>
              <footer>{t("淨敞口")}{' '}{formatSigned(managedStats.netExposure)} {' '}{t("· 總名義")}{' '}{formatUsd(managedStats.totalNotional)}</footer>
            </div>
          </div>

          <div className="cfp-position-grid">
            {managedPositions.map((position) => (
              <PositionCard
                key={`${position.symbol}-${position.side}`}
                position={position}
                managed
                inBasket={inBasket(position)}
                isBenchmarkHedge={isBenchmarkHedge}
              />
            ))}
          </div>
        </>
      )}

      {externalPositions.length > 0 && (
        <details className="cfp-external">
          <summary>
            <span>{t("其他策略持倉（不計入 CoinFull）")}</span>
            <span className="cfp-muted">
              {externalPositions.length} {' '}{t("筆 ·")}{' '}{formatUsd(externalStats.totalNotional)} · {formatSigned(externalStats.totalPnl)} USDT
            </span>
          </summary>
          <div className="cfp-position-grid">
            {externalPositions.map((position) => (
              <PositionCard
                key={`${position.symbol}-${position.side}-external`}
                position={position}
                managed={false}
                inBasket={false}
                isBenchmarkHedge={isBenchmarkHedge}
              />
            ))}
          </div>
        </details>
      )}

      <footer className="cfp-account-total">
        {t("帳戶總計（含其他策略）：")}{accountStats.count} {' '}{t("筆 · 名義")}{' '}{formatUsd(accountStats.totalNotional)} {' '}{t("· 未實現")}{' '}<span className={accountStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}>{formatSigned(accountStats.totalPnl)} USDT</span>
      </footer>
    </section>
  );
};
