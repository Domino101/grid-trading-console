import type { CoinFullPrimePosition } from '../../services/api';

export type SizingMode = 'fixed' | 'percent';

const TAIPEI_TZ = 'Asia/Taipei';

const finite = (value: number | null | undefined, fallback = 0): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

export const formatTaipei = (iso?: string | null, fallback = '—'): string => {
  if (!iso) return fallback;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('zh-TW', {
    timeZone: TAIPEI_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
};

export const formatTaipeiWithZone = (iso?: string | null, fallback = '—'): string => {
  const text = formatTaipei(iso, fallback);
  return text === fallback ? text : `${text}（台北）`;
};

export const relativeAge = (iso: string | null | undefined, now = Date.now()): string => {
  if (!iso) return '—';
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return '—';
  const diff = Math.max(0, now - time);
  if (diff < 60_000) return '剛剛';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分鐘前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小時前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
};

export const isStale = (iso: string | null | undefined, now = Date.now(), maxAgeMs = 86_400_000): boolean => {
  if (!iso) return true;
  const time = new Date(iso).getTime();
  return Number.isNaN(time) ? true : now - time > maxAgeMs;
};

export const formatUsd = (value: number | null | undefined, digits = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
};

export const formatSigned = (value: number | null | undefined, digits = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};

export const formatPct = (value: number | null | undefined, digits = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
};

const EMOJI_PATTERN = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/gu;
const EMOJI_JOINERS = /\uFE0F|\u200D/g;

export const cleanText = (value?: string | null): string =>
  (value ?? '').replace(EMOJI_PATTERN, '').replace(EMOJI_JOINERS, '').replace(/\s{2,}/g, ' ').trim();

export const formatZ = (value: number | null | undefined, digits = 2): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
};

export const formatScore = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(4)}`;
};

export const formatPrice = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—';
  if (value >= 1) {
    const digits = value >= 1000 ? 2 : 4;
    return `$${value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  const digits = value >= 0.01 ? 5 : value >= 0.0001 ? 6 : 8;
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: digits })}`;
};

export const formatQty = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const digits = value >= 1000 ? 2 : value >= 1 ? 4 : 6;
  return value.toLocaleString('en-US', { maximumFractionDigits: digits });
};

export interface PositionStats {
  count: number;
  longCount: number;
  shortCount: number;
  longNotional: number;
  shortNotional: number;
  totalNotional: number;
  netExposure: number;
  longPnl: number;
  shortPnl: number;
  totalPnl: number;
}

const emptyStats = (): PositionStats => ({
  count: 0,
  longCount: 0,
  shortCount: 0,
  longNotional: 0,
  shortNotional: 0,
  totalNotional: 0,
  netExposure: 0,
  longPnl: 0,
  shortPnl: 0,
  totalPnl: 0,
});

export const computePositionStats = (positions: CoinFullPrimePosition[]): PositionStats => {
  const stats = emptyStats();
  for (const position of positions) {
    const notional = finite(position.notional_usdt);
    const pnl = finite(position.unrealized_pnl);
    stats.count += 1;
    stats.totalNotional += notional;
    stats.totalPnl += pnl;
    if (position.side === 'long') {
      stats.longCount += 1;
      stats.longNotional += notional;
      stats.longPnl += pnl;
    } else {
      stats.shortCount += 1;
      stats.shortNotional += notional;
      stats.shortPnl += pnl;
    }
  }
  stats.netExposure = stats.longNotional - stats.shortNotional;
  return stats;
};

export const isManagedPosition = (position: CoinFullPrimePosition): boolean => {
  if (position.role === 'coinfull_prime') return true;
  if (position.role === 'external_bot' || position.role === 'unmanaged') return false;
  return Boolean(position.is_managed) && !position.is_protected;
};

export const splitPositions = (
  positions: CoinFullPrimePosition[],
): { managed: CoinFullPrimePosition[]; external: CoinFullPrimePosition[] } => {
  const managed: CoinFullPrimePosition[] = [];
  const external: CoinFullPrimePosition[] = [];
  for (const position of positions) {
    (isManagedPosition(position) ? managed : external).push(position);
  }
  return { managed, external };
};

export interface ManualSizingInput {
  mode: SizingMode;
  capital: number;
  balancePct: number;
  leverage: number;
  balanceUsdt: number | null;
  targetCount: number;
}

export interface ManualSizingResult {
  mode: SizingMode;
  capital: number;
  grossExposure: number;
  longSleeve: number;
  shortSleeve: number;
  perSymbolNotional: number;
  balanceKnown: boolean;
  affordable: boolean;
  shortfall: number;
}

export const DEFAULT_BALANCE = 10_000;

export const computeManualSizing = (input: ManualSizingInput): ManualSizingResult => {
  const targetCount = Math.max(1, Math.floor(input.targetCount || 1));
  const leverage = input.leverage > 0 ? input.leverage : 1;
  const balanceKnown = typeof input.balanceUsdt === 'number' && Number.isFinite(input.balanceUsdt);
  const balance = balanceKnown ? (input.balanceUsdt as number) : DEFAULT_BALANCE;
  const capital = input.mode === 'percent'
    ? Math.max(0, balance * (clamp(input.balancePct, 0, 100) / 100))
    : Math.max(0, input.capital > 0 ? input.capital : 1000);
  const grossExposure = capital * leverage;
  const longSleeve = grossExposure * 0.5;
  const shortSleeve = grossExposure * 0.5;
  const affordable = !balanceKnown || capital <= balance + 0.005;
  return {
    mode: input.mode,
    capital,
    grossExposure,
    longSleeve,
    shortSleeve,
    perSymbolNotional: longSleeve / targetCount,
    balanceKnown,
    affordable,
    shortfall: Math.max(0, capital - balance),
  };
};

export interface ScheduleSizingInput {
  capital: number | null;
  balancePct: number | null;
  leverage: number | null;
  targetCount: number | null;
  balanceUsdt: number | null;
}

export interface ScheduleSizingResult {
  mode: SizingMode | 'unknown';
  capital: number | null;
  balancePct: number | null;
  grossExposure: number | null;
  longSleeve: number | null;
  shortSleeve: number | null;
  perSymbolNotional: number | null;
  leverage: number;
  targetCount: number;
}

export const computeScheduleSizing = (input: ScheduleSizingInput): ScheduleSizingResult => {
  const leverage = input.leverage && input.leverage > 0 ? input.leverage : 1;
  const targetCount = Math.max(1, Math.floor(input.targetCount || 1));
  const balance = typeof input.balanceUsdt === 'number' && Number.isFinite(input.balanceUsdt)
    ? input.balanceUsdt
    : DEFAULT_BALANCE;
  let mode: SizingMode | 'unknown' = 'unknown';
  let capital: number | null = null;
  if (input.balancePct !== null && input.balancePct > 0) {
    mode = 'percent';
    capital = balance * (clamp(input.balancePct, 0, 100) / 100);
  } else if (input.capital !== null && input.capital > 0) {
    mode = 'fixed';
    capital = input.capital;
  }
  if (capital === null) {
    return {
      mode,
      capital: null,
      balancePct: input.balancePct,
      grossExposure: null,
      longSleeve: null,
      shortSleeve: null,
      perSymbolNotional: null,
      leverage,
      targetCount,
    };
  }
  const grossExposure = capital * leverage;
  return {
    mode,
    capital,
    balancePct: input.balancePct,
    grossExposure,
    longSleeve: grossExposure * 0.5,
    shortSleeve: grossExposure * 0.5,
    perSymbolNotional: (grossExposure * 0.5) / targetCount,
    leverage,
    targetCount,
  };
};

export interface SizingComparison {
  aligned: boolean;
  reasons: string[];
}

const sizingLabel = (mode: SizingMode): string => (mode === 'fixed' ? '固定本金' : '餘額百分比');

export const compareSizing = (
  manual: ManualSizingResult,
  schedule: ScheduleSizingResult,
  manualBalancePct: number,
  manualLeverage: number,
): SizingComparison => {
  if (schedule.mode === 'unknown' || schedule.capital === null) {
    return { aligned: false, reasons: ['排程尚未設定倉位規模'] };
  }
  const reasons: string[] = [];
  if (manual.mode !== schedule.mode) {
    reasons.push(`手動使用${sizingLabel(manual.mode)}，排程使用${sizingLabel(schedule.mode)}`);
  } else if (manual.mode === 'fixed' && Math.abs(manual.capital - schedule.capital) > 0.5) {
    reasons.push(`手動本金 ${formatUsd(manual.capital)}，排程本金 ${formatUsd(schedule.capital)}`);
  } else if (manual.mode === 'percent' && Math.abs(manualBalancePct - (schedule.balancePct ?? 0)) > 0.1) {
    reasons.push(`手動餘額 ${manualBalancePct}%，排程餘額 ${schedule.balancePct ?? 0}%`);
  }
  if (Math.abs(manualLeverage - schedule.leverage) > 0.001) {
    reasons.push(`手動槓桿 ${manualLeverage}x，排程槓桿 ${schedule.leverage}x`);
  }
  return { aligned: reasons.length === 0, reasons };
};

export const isLiveReal = (environment: string | undefined, dryRun: boolean): boolean =>
  environment === 'live' && !dryRun;

export const modeCopy = (
  environment: string | undefined,
  dryRun: boolean,
): { label: string; tone: 'success' | 'warning' | 'danger' | 'info' } => {
  if (environment === 'live') {
    return dryRun
      ? { label: '實盤 · 僅試算', tone: 'warning' }
      : { label: '實盤 · 真實發單', tone: 'danger' };
  }
  if (environment === 'testnet') {
    return dryRun
      ? { label: '測試網 · 僅試算', tone: 'warning' }
      : { label: '測試網 · API 撮合', tone: 'info' };
  }
  return { label: 'Paper · 本地模擬', tone: 'success' };
};

export const scoreTone = (score: number): 'strong-long' | 'long' | 'neutral' | 'short' | 'strong-short' => {
  if (score >= 0.8) return 'strong-long';
  if (score >= 0.3) return 'long';
  if (score <= -0.8) return 'strong-short';
  if (score <= -0.3) return 'short';
  return 'neutral';
};

