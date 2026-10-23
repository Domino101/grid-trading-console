import React from 'react';
import { Search, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { RankingFilter } from './useCoinFullPrime';
import { t } from '../../i18n/index.ts';

interface Counts {
  baskets: number;
  long: number;
  short: number;
  all: number;
}

interface Props {
  filterTab: RankingFilter;
  onFilter: (filter: RankingFilter) => void;
  counts: Counts;
  universeSize: number;
  excludeTradfi: boolean;
  onToggleTradfi: (next: boolean) => void;
  searchQuery: string;
  onSearch: (query: string) => void;
  showDisabledFactors: boolean;
  onToggleDisabled: (next: boolean) => void;
  busy?: boolean;
}

export const RankingsToolbar: React.FC<Props> = ({
  filterTab,
  onFilter,
  counts,
  universeSize,
  excludeTradfi,
  onToggleTradfi,
  searchQuery,
  onSearch,
  showDisabledFactors,
  onToggleDisabled,
  busy,
}) => (
  <section className="cfp-toolbar cfp-panel" aria-label={t("排行篩選")}>
    <div className="cfp-tab-pills" role="tablist" aria-label={t("排行範圍")}>
      <button type="button" role="tab" aria-selected={filterTab === 'baskets'} className={filterTab === 'baskets' ? 'is-active' : ''} onClick={() => onFilter('baskets')}>
        {t("今日多空組合")}{' '}<span>{counts.baskets}</span>
      </button>
      <button type="button" role="tab" aria-selected={filterTab === 'long'} className={filterTab === 'long' ? 'is-active' : ''} onClick={() => onFilter('long')}>
        {t("多頭籃子")}{' '}<span>{counts.long}</span>
      </button>
      <button type="button" role="tab" aria-selected={filterTab === 'short'} className={filterTab === 'short' ? 'is-active' : ''} onClick={() => onFilter('short')}>
        {t("對沖籃子")}{' '}<span>{counts.short}</span>
      </button>
      <button type="button" role="tab" aria-selected={filterTab === 'all'} className={filterTab === 'all' ? 'is-active' : ''} onClick={() => onFilter('all')}>
        {t("全部")}{' '}<span>{counts.all || universeSize}</span>
      </button>
    </div>

    <div className="cfp-toolbar-right">
      <button
        type="button"
        className={`cfp-toggle ${excludeTradfi ? 'is-on' : 'is-warn'}`}
        aria-pressed={excludeTradfi}
        onClick={() => onToggleTradfi(!excludeTradfi)}
        disabled={busy}
        title={t("排除美股、ETF 與大宗商品合約，維持純加密宇宙")}
      >
        {excludeTradfi ? <ShieldCheck size={14} aria-hidden="true" /> : <TriangleAlert size={14} aria-hidden="true" />}
        <span>{excludeTradfi ? (t("純加密 ") + (universeSize) + t(" 幣")) : t("含 TradFi 合約")}</span>
      </button>

      <label className="cfp-check">
        <input type="checkbox" checked={showDisabledFactors} onChange={(event) => onToggleDisabled(event.target.checked)} />
        <span>{t("顯示 0% 因子")}</span>
      </label>

      <label className="cfp-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">{t("搜尋幣種")}</span>
        <input
          type="search"
          placeholder={t("搜尋全部幣種（BTC、SOL…）")}
          value={searchQuery}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
    </div>
  </section>
);
