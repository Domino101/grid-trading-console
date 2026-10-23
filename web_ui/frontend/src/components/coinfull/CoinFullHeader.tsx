import React from 'react';
import { BarChart3, Clock, RefreshCw } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import { formatTaipei, relativeAge } from './format';
import { t } from '../../i18n/index.ts';

interface Props {
  controller: CoinFullPrimeController;
  onGoToRankings?: () => void;
}

export const CoinFullHeader: React.FC<Props> = ({ controller, onGoToRankings }) => {
  const { mode, rankTimestamp, rankStale, refreshing, actions } = controller;
  return (
    <header className="cfp-header">
      <div className="cfp-header-title">
        <p className="cfp-eyebrow">{t("COINFULL PRIME / 截面多空")}</p>
        <div className="cfp-title-row">
          <h2>{t("策略機器人")}</h2>
          <span className={`badge badge-${mode.tone}`}>{mode.label}</span>
        </div>
        <p className="cfp-subtitle">
          {t("每日截面多空 · 自動隔離網格與 DCA 標的 · 手動執行與背景排程分開設定")}{' '}</p>
      </div>
      <div className="cfp-header-actions">
        <span className={`cfp-data-time ${rankStale ? 'is-stale' : ''}`} title={t("排行資料的產生時間（台北）")}>
          <Clock size={14} aria-hidden="true" />
          <span>{rankTimestamp ? (t("資料 ") + (formatTaipei(rankTimestamp)) + " · " + (relativeAge(rankTimestamp))) : t("尚無排行資料")}</span>
        </span>
        {onGoToRankings && (
          <button type="button" className="btn btn-outline" onClick={onGoToRankings}>
            <BarChart3 size={15} aria-hidden="true" />
            <span>{t("多空截面")}</span>
          </button>
        )}
        <button
          type="button"
          className="btn btn-outline"
          onClick={() => void actions.refresh()}
          disabled={refreshing}
        >
          <RefreshCw size={15} className={refreshing ? 'spin' : ''} aria-hidden="true" />
          <span>{refreshing ? t("重新計算中…") : t("重新計算")}</span>
        </button>
      </div>
    </header>
  );
};
