import { t } from '../i18n/index.ts';
export const money = (value: number | null | undefined, maximumFractionDigits = 2) => (
  value === null || value === undefined
    ? '—'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits }).format(value)
);

export const number = (value: number | null | undefined, digits = 4) => (
  value === null || value === undefined
    ? '—'
    : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value)
);

export const freshnessText = (dataStatus: string, lastUpdatedAt: number | null) => {
  if (dataStatus === 'unavailable') return t("尚未同步");
  if (dataStatus === 'stale') return t("資料過期");
  if (!lastUpdatedAt) return t("已同步");
  return (t("同步於 ") + (new Date(lastUpdatedAt * 1000).toLocaleTimeString('zh-TW', { hour12: false })));
};

export const canAdvanceCreateStep = (step: number, form: {
  symbol?: string;
  exchange?: string;
  lower_price?: number;
  upper_price?: number;
  grid_interval?: number;
  order_amount?: number;
}, mode: 'paper' | 'live', liveConfirmation: string, minNotionalError: boolean) => {
  if (step === 1) return Boolean(form.symbol && form.exchange);
  if (step === 2) {
    return Boolean(
      form.lower_price && form.upper_price && form.grid_interval && form.order_amount
      && form.lower_price < form.upper_price && !minNotionalError,
    );
  }
  return mode !== 'live' || liveConfirmation === 'CONFIRM LIVE';
};

export const nextPollDelay = (failures: number, base = 15_000, cap = 60_000) => {
  if (failures <= 0) return base;
  return Math.min(cap, base * (2 ** Math.min(failures, 5)));
};
