import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../../services/api';
import type {
  CoinFullPrimeFill,
  CoinFullPrimeHistoryBatch,
  CoinFullPrimePosition,
  CoinFullPrimeStatus,
  RankedItem,
  TradingAccountProfile,
  UniverseRankResult,
} from '../../services/api';
import {
  compareSizing,
  computeManualSizing,
  computePositionStats,
  computeScheduleSizing,
  isStale,
  modeCopy,
  splitPositions,
  type ManualSizingResult,
  type ScheduleSizingResult,
  type SizingComparison,
  type SizingMode,
} from './format';
import { t } from '../../i18n/index.ts';

export type RankingFilter = 'baskets' | 'long' | 'short' | 'all';
export type HistoryView = 'batches' | 'fills';
export type CoinFullTab = 'positions' | 'rankings' | 'history';
export type NoticeKind = 'success' | 'warning' | 'error' | 'info';
export type DialogKind = 'live-mode' | 'rebalance' | 'apply-schedule' | 'scheduler-start';

export interface Notice {
  kind: NoticeKind;
  text: string;
}

export interface CoinFullDialog {
  kind: DialogKind;
}

const STORAGE = {
  account: 'coinfull_selected_account',
  dryRun: 'coinfull_dry_run_mode',
  sizingMode: 'coinfull_sizing_mode',
  capital: 'coinfull_capital_input',
  balancePct: 'coinfull_balance_pct_input',
  leverage: 'coinfull_leverage_input',
  excludeTradfi: 'coinfull_exclude_tradfi',
  filter: 'coinfull_filter_tab',
} as const;

const read = (key: string): string | null => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};

const write = (key: string, value: string): void => {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable in private mode; UI state still works in memory.
  }
};

const asNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const asString = (value: unknown): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null;

const fallbackAccount = (id: string): TradingAccountProfile => {
  const environment: TradingAccountProfile['environment'] =
    id === 'binance_testnet' ? 'testnet' : id === 'binance_live' ? 'live' : 'paper';
  return {
    id,
    name: environment === 'testnet' ? t("Binance 合約測試網") : environment === 'live' ? t("Binance 合約實盤") : t("Paper 本地模擬盤"),
    environment,
    is_ready: environment === 'paper',
    balance_usdt: environment === 'paper' ? 10_000 : null,
    missing_keys: [],
    description: '',
    endpoint: '',
  };
};

const localizeAccount = (account: TradingAccountProfile): TradingAccountProfile => {
  const name = t(account.name);
  const description = t(account.description);
  return name === account.name && description === account.description
    ? account
    : { ...account, name, description };
};

const knownAccount = (id: string | null): id is string => id === 'paper' || id === 'binance_live' || id === 'binance_testnet';

const initialAccount = (): string => {
  const saved = read(STORAGE.account);
  return knownAccount(saved) ? saved : 'binance_live';
};

const initialFilter = (): RankingFilter => {
  const saved = read(STORAGE.filter);
  return saved === 'long' || saved === 'short' || saved === 'all' || saved === 'baskets' ? saved : 'baskets';
};

export const useCoinFullPrime = (onUnauthorized?: () => void) => {
  const unauthorizedRef = useRef(onUnauthorized);
  useEffect(() => {
    unauthorizedRef.current = onUnauthorized;
  }, [onUnauthorized]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [rebalancing, setRebalancing] = useState(false);
  const [togglingScheduler, setTogglingScheduler] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [rankResult, setRankResult] = useState<UniverseRankResult | null>(null);
  const [statusInfo, setStatusInfo] = useState<CoinFullPrimeStatus | null>(null);
  const [accounts, setAccounts] = useState<TradingAccountProfile[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>(initialAccount);
  const [positions, setPositions] = useState<CoinFullPrimePosition[]>([]);
  const [loadingPositions, setLoadingPositions] = useState(false);
  const [dryRunMode, setDryRunMode] = useState<boolean>(() => {
    const saved = read(STORAGE.dryRun);
    return saved === null ? true : saved === 'true';
  });
  const [excludeTradfi, setExcludeTradfi] = useState<boolean>(() => read(STORAGE.excludeTradfi) !== 'false');
  const [sizingMode, setSizingMode] = useState<SizingMode>(() => (read(STORAGE.sizingMode) === 'percent' ? 'percent' : 'fixed'));
  const [capitalInput, setCapitalInput] = useState<string>(() => read(STORAGE.capital) || '1000');
  const [balancePct, setBalancePct] = useState<number>(() => {
    const saved = Number(read(STORAGE.balancePct));
    return Number.isFinite(saved) && saved > 0 ? saved : 50;
  });
  const [leverage, setLeverage] = useState<number>(() => {
    const saved = Number(read(STORAGE.leverage));
    return Number.isFinite(saved) && saved > 0 ? saved : 1;
  });
  const [filterTab, setFilterTab] = useState<RankingFilter>(initialFilter);
  const [searchQuery, setSearchQuery] = useState('');
  const [showDisabledFactors, setShowDisabledFactors] = useState(false);
  const [activeTab, setActiveTab] = useState<CoinFullTab>('rankings');
  const [historyBatches, setHistoryBatches] = useState<CoinFullPrimeHistoryBatch[]>([]);
  const [tradeFills, setTradeFills] = useState<CoinFullPrimeFill[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyViewTab, setHistoryViewTab] = useState<HistoryView>('batches');
  const [includeSimulated, setIncludeSimulated] = useState(true);
  const [expandedBatchId, setExpandedBatchId] = useState<number | null>(null);
  const [historyAccountFilter, setHistoryAccountFilter] = useState('all');
  const [dialog, setDialog] = useState<CoinFullDialog | null>(null);

  const selectedAccountRef = useRef(selectedAccount);
  useEffect(() => {
    selectedAccountRef.current = selectedAccount;
  }, [selectedAccount]);
  const excludeTradfiRef = useRef(excludeTradfi);
  useEffect(() => {
    excludeTradfiRef.current = excludeTradfi;
  }, [excludeTradfi]);
  const initializedRef = useRef(false);

  const notify = useCallback((kind: NoticeKind, text: string) => {
    setNotice({ kind, text });
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), notice.kind === 'error' ? 12_000 : 7_000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const handleApiError = useCallback((err: unknown, fallback: string): string => {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      unauthorizedRef.current?.();
      return t("登入憑證已失效，請重新登入");
    }
    return err instanceof Error ? err.message : fallback;
  }, []);

  const loadPositions = useCallback(async (accountId: string) => {
    if (accountId === 'paper') {
      setPositions([]);
      return;
    }
    try {
      setLoadingPositions(true);
      setPositions([]);
      const result = await api.getCoinFullPrimePositions(accountId);
      setPositions(Array.isArray(result.positions) ? result.positions : []);
      setError(null);
    } catch (err: unknown) {
      setPositions([]);
      setError(handleApiError(err, t("無法讀取帳戶持倉")));
    } finally {
      setLoadingPositions(false);
    }
  }, [handleApiError]);

  const loadAll = useCallback(async (isRefresh: boolean) => {
    setError(null);
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    const accountId = selectedAccountRef.current;
    try {
      const results = await Promise.allSettled([
        api.getCoinFullPrimeRankings(false, excludeTradfiRef.current),
        api.getCoinFullPrimeStatus(),
        api.getCoinFullPrimeAccounts(),
        accountId === 'paper' ? Promise.resolve({ positions: [] as CoinFullPrimePosition[] }) : api.getCoinFullPrimePositions(accountId),
      ]);
      const [rankingsResult, statusResult, accountsResult, positionsResult] = results;
      if (rankingsResult.status === 'rejected') throw rankingsResult.reason;
      if (statusResult.status === 'rejected') throw statusResult.reason;
      if (accountsResult.status === 'rejected') throw accountsResult.reason;
      if (positionsResult.status === 'rejected') {
        setError(handleApiError(positionsResult.reason, t("無法讀取帳戶持倉")));
        setPositions([]);
      } else {
        setPositions(Array.isArray(positionsResult.value.positions) ? positionsResult.value.positions : []);
      }
      const rankings = rankingsResult.value;
      const status = statusResult.value;
      const accountsData = accountsResult.value;
      setRankResult(rankings);
      setStatusInfo(status);
      setAccounts(Array.isArray(accountsData.accounts) ? accountsData.accounts.map(localizeAccount) : []);

      if (!initializedRef.current) {
        const saved = read(STORAGE.account);
        const configured = asString(status?.scheduler_config?.account_id);
        const nextAccount = [saved, configured, accountsData.selected_account, 'binance_live']
          .find((candidate): candidate is string => Boolean(candidate) && Boolean(accountsData.accounts.some((account) => account.id === candidate))) ?? 'binance_live';
        setSelectedAccount(nextAccount);
        selectedAccountRef.current = nextAccount;
        write(STORAGE.account, nextAccount);
        setDryRunMode(() => {
          if (nextAccount === 'paper') return true;
          const savedMode = read(STORAGE.dryRun);
          if (savedMode !== null) return savedMode === 'true';
          return nextAccount === 'binance_testnet' ? false : true;
        });
        initializedRef.current = true;
        if (nextAccount !== accountId) void loadPositions(nextAccount);
      }
    } catch (err: unknown) {
      setError(handleApiError(err, t("載入 CoinFull Prime 數據失敗")));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [handleApiError, loadPositions]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadAll(false);
  }, [loadAll]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const accountFilter = historyAccountFilter === 'all' ? undefined : historyAccountFilter;
      const [history, fills] = await Promise.all([
        api.getCoinFullPrimeHistory(accountFilter, 50),
        api.getCoinFullPrimeFills(accountFilter, 200, includeSimulated),
      ]);
      if (Array.isArray(history.batches)) setHistoryBatches(history.batches);
      if (Array.isArray(fills.fills)) setTradeFills(fills.fills);
    } catch (err: unknown) {
      notify('error', handleApiError(err, t("載入調倉歷史失敗")));
    } finally {
      setHistoryLoading(false);
    }
  }, [handleApiError, historyAccountFilter, includeSimulated, notify]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadHistory();
  }, [loadHistory]);

  const currentAccount = useMemo(
    () => accounts.find((account) => account.id === selectedAccount) ?? fallbackAccount(selectedAccount),
    [accounts, selectedAccount],
  );

  const longList = useMemo<RankedItem[]>(() => rankResult?.long_list ?? [], [rankResult?.long_list]);
  const shortList = useMemo<RankedItem[]>(() => rankResult?.short_list ?? [], [rankResult?.short_list]);
  const targetCount = useMemo(
    () => longList.length || statusInfo?.current_portfolio?.long_top8?.length || 5,
    [longList.length, statusInfo?.current_portfolio?.long_top8?.length],
  );
  const isBenchmarkHedge = useMemo(
    () => shortList.length === 0 || shortList.length <= 2 || shortList.some((item) => item.symbol === 'BTCUSDT' || item.evaluations?.overall_eval?.includes('基準')),
    [shortList],
  );
  const top1Long = longList[0];
  const top1Short = shortList.length > 0
    ? (isBenchmarkHedge ? shortList[0] : shortList[shortList.length - 1])
    : undefined;

  const manualSizing: ManualSizingResult = useMemo(
    () => computeManualSizing({
      mode: sizingMode,
      capital: Number(capitalInput),
      balancePct,
      leverage,
      balanceUsdt: currentAccount.balance_usdt,
      targetCount,
    }),
    [balancePct, capitalInput, currentAccount.balance_usdt, leverage, sizingMode, targetCount],
  );

  const scheduleConfig = statusInfo?.scheduler_config;
  const scheduleAccountId = asString(scheduleConfig?.account_id) ?? asString(statusInfo?.selected_account) ?? 'paper';
  const scheduleDryRun = scheduleConfig?.dry_run === undefined ? true : Boolean(scheduleConfig.dry_run);
  const scheduleAccount = useMemo(
    () => accounts.find((account) => account.id === scheduleAccountId) ?? fallbackAccount(scheduleAccountId),
    [accounts, scheduleAccountId],
  );
  const scheduleSizing: ScheduleSizingResult = useMemo(
    () => computeScheduleSizing({
      capital: asNumber(scheduleConfig?.capital),
      balancePct: asNumber(scheduleConfig?.balance_pct),
      leverage: asNumber(scheduleConfig?.leverage),
      targetCount: asNumber(scheduleConfig?.target_count) ?? targetCount,
      balanceUsdt: scheduleAccount.balance_usdt ?? currentAccount.balance_usdt,
    }),
    [currentAccount.balance_usdt, scheduleAccount.balance_usdt, scheduleConfig?.balance_pct, scheduleConfig?.capital, scheduleConfig?.leverage, scheduleConfig?.target_count, targetCount],
  );

  const comparison: SizingComparison = useMemo(
    () => compareSizing(manualSizing, scheduleSizing, balancePct, leverage),
    [balancePct, leverage, manualSizing, scheduleSizing],
  );

  const scheduleReasons = useMemo(() => {
    const reasons = [...comparison.reasons];
    if (scheduleAccountId !== selectedAccount) reasons.unshift((t("排程目標為 ") + (scheduleAccount.name) + t("，目前檢視 ") + (currentAccount.name)));
    if (scheduleDryRun !== dryRunMode) reasons.push((t("排程模式為 ") + (scheduleDryRun ? t('僅試算') : t('真實發單')) + t("，目前執行模式為 ") + (dryRunMode ? t('僅試算') : t('真實發單'))));
    if (scheduleConfig?.exclude_tradfi !== undefined && Boolean(scheduleConfig.exclude_tradfi) !== excludeTradfi) {
      reasons.push(t("排程的 TradFi 過濾與目前設定不同"));
    }
    return reasons;
  }, [comparison.reasons, currentAccount.name, dryRunMode, excludeTradfi, scheduleAccount.name, scheduleAccountId, scheduleConfig, scheduleDryRun, selectedAccount]);
  const scheduleAligned = scheduleReasons.length === 0;

  const positionSplit = useMemo(() => splitPositions(positions), [positions]);
  const managedStats = useMemo(() => computePositionStats(positionSplit.managed), [positionSplit.managed]);
  const externalStats = useMemo(() => computePositionStats(positionSplit.external), [positionSplit.external]);
  const accountStats = useMemo(() => computePositionStats(positions), [positions]);

  const displayedItems = useMemo<RankedItem[]>(() => {
    if (!rankResult) return [];
    const query = searchQuery.trim().toUpperCase();
    if (query) {
      return (rankResult.all_rankings ?? []).filter((item) => item.symbol.toUpperCase().includes(query));
    }
    if (filterTab === 'baskets') return [...longList, ...shortList];
    if (filterTab === 'long') return longList;
    if (filterTab === 'short') return shortList;
    return rankResult.all_rankings ?? [];
  }, [filterTab, longList, rankResult, searchQuery, shortList]);

  const universeSize = rankResult?.universe_size ?? 0;
  const rankTimestamp = rankResult?.timestamp ?? null;
  const rankStale = isStale(rankTimestamp);

  const applyMode = useCallback((next: boolean) => {
    setDryRunMode(next);
    write(STORAGE.dryRun, String(next));
    notify('info', next
      ? t("執行模式已切換為僅試算。背景排程未變更，需按「套用至排程」才會影響自動換倉。")
      : t("執行模式已切換為真實發單。背景排程未變更，手動換倉將在確認後送出真單。"));
  }, [notify]);

  const requestModeChange = useCallback((next: boolean) => {
    if (next === dryRunMode) return;
    if (!next) {
      setDialog({ kind: 'live-mode' });
      return;
    }
    applyMode(true);
  }, [applyMode, dryRunMode]);

  const selectAccount = useCallback((accountId: string) => {
    setSelectedAccount(accountId);
    selectedAccountRef.current = accountId;
    write(STORAGE.account, accountId);
    if (accountId === 'paper') {
      setDryRunMode(true);
      write(STORAGE.dryRun, 'true');
    }
    void loadPositions(accountId);
  }, [loadPositions]);

  const setSizing = useCallback((mode: SizingMode) => {
    setSizingMode(mode);
    write(STORAGE.sizingMode, mode);
  }, []);

  const updateCapital = useCallback((value: string) => {
    setCapitalInput(value);
    write(STORAGE.capital, value);
  }, []);

  const updateBalancePct = useCallback((value: number) => {
    setBalancePct(value);
    write(STORAGE.balancePct, String(value));
  }, []);

  const updateLeverage = useCallback((value: number) => {
    setLeverage(value);
    write(STORAGE.leverage, String(value));
  }, []);

  const setFilter = useCallback((next: RankingFilter) => {
    setFilterTab(next);
    write(STORAGE.filter, next);
  }, []);

  const toggleExcludeTradfi = useCallback(async (next: boolean) => {
    setExcludeTradfi(next);
    write(STORAGE.excludeTradfi, String(next));
    setRefreshing(true);
    try {
      const rankings = await api.getCoinFullPrimeRankings(true, next);
      setRankResult(rankings);
      notify('info', next ? t("已切換為純加密宇宙，並重新計算排行。") : t("已納入 TradFi 合約，並重新計算排行。"));
    } catch (err: unknown) {
      notify('error', handleApiError(err, t("重新計算排行失敗")));
    } finally {
      setRefreshing(false);
    }
  }, [handleApiError, notify]);

  const requestRebalance = useCallback(() => {
    if (!manualSizing.affordable) {
      notify('error', (t("本金 ") + (manualSizing.capital.toFixed(2)) + t(" USDT 超過帳戶可用保證金，還差 ") + (manualSizing.shortfall.toFixed(2)) + t(" USDT。請改用餘額百分比或降低本金。")));
      return;
    }
    if (!dryRunMode && !currentAccount.is_ready) {
      notify('error', (t("帳號「") + (currentAccount.name) + t("」尚未就緒：缺少 ") + (currentAccount.missing_keys.join(', ') || t('API 金鑰')) + t("。請先補齊設定或改用 Paper。")));
      return;
    }
    setDialog({ kind: 'rebalance' });
  }, [currentAccount.is_ready, currentAccount.missing_keys, currentAccount.name, dryRunMode, manualSizing, notify]);

  const executeRebalance = useCallback(async () => {
    setRebalancing(true);
    setError(null);
    try {
      const options = sizingMode === 'percent'
        ? { balancePct, leverage, excludeTradfi }
        : { capital: manualSizing.capital, leverage, excludeTradfi };
      const result = await api.triggerCoinFullPrimeRebalance(dryRunMode, selectedAccount, options);
      const report = result.report as Record<string, unknown> | undefined;
      const reportStatus = report?.status as string | undefined;
      const reportError = report?.error as string | undefined;
      if (reportStatus === 'failed') {
        notify('error', (t("換倉失敗：") + (reportError || t('下單發生異常'))));
      } else if (reportStatus === 'partial') {
        const orders = (report?.orders as Array<{ status: string }> | undefined) ?? [];
        const filled = orders.filter((order) => order.status === 'filled' || order.status === 'simulated').length;
        const failed = orders.filter((order) => order.status === 'failed').length;
        const skipped = orders.filter((order) => order.status === 'skipped').length;
        notify('warning', (t("換倉部分成功：完成 ") + (filled) + t(" 筆、失敗 ") + (failed) + t(" 筆、跳過 ") + (skipped) + t(" 筆。") + (reportError ? t("原因：") + reportError : '')));
      } else {
        const ordersCount = typeof result.orders_count === 'number' ? result.orders_count : 0;
        const closed = Array.isArray(result.closed_symbols) ? result.closed_symbols.length : 0;
        const opened = Array.isArray(result.opened_symbols) ? result.opened_symbols.length : 0;
        notify('success', (t("換倉完成：") + (ordersCount) + t(" 筆訂單（平倉 ") + (closed) + t("、開倉 ") + (opened) + t("），模式 ") + (result.dry_run ? t('僅試算') : t('真實發單')) + t("。")));
      }
      await Promise.all([loadPositions(selectedAccount), loadHistory()]);
      try {
        setStatusInfo(await api.getCoinFullPrimeStatus());
      } catch {
        // The orders already succeeded; a status refresh failure must not be reported as a failed rebalance.
      }
    } catch (err: unknown) {
      notify('error', handleApiError(err, t("執行換倉失敗")));
    } finally {
      setRebalancing(false);
    }
  }, [balancePct, dryRunMode, excludeTradfi, handleApiError, leverage, loadHistory, loadPositions, manualSizing.capital, notify, selectedAccount, sizingMode]);

  const requestApplySchedule = useCallback(() => {
    if (!manualSizing.affordable) {
      notify('error', (t("本金 ") + (manualSizing.capital.toFixed(2)) + t(" USDT 超過帳戶可用保證金，請先改用餘額百分比或降低本金。")));
      return;
    }
    setDialog({ kind: 'apply-schedule' });
  }, [manualSizing.affordable, manualSizing.capital, notify]);

  const executeApplySchedule = useCallback(async () => {
    try {
      await api.updateCoinFullPrimeSchedulerTarget({
        accountId: selectedAccount,
        dryRun: dryRunMode,
        capital: sizingMode === 'fixed' ? manualSizing.capital : 0,
        leverage,
        balancePct: sizingMode === 'percent' ? balancePct : 0,
        excludeTradfi,
      });
      try {
        setStatusInfo(await api.getCoinFullPrimeStatus());
      } catch {
        // Keep the optimistic status strip; the target write itself succeeded.
      }
      notify('success', (t("排程目標已同步為「") + (currentAccount.name) + t("」／") + (dryRunMode ? t('僅試算') : t('真實發單')) + t("。")));
    } catch (err: unknown) {
      notify('error', handleApiError(err, t("同步排程目標失敗")));
    }
  }, [balancePct, currentAccount.name, dryRunMode, excludeTradfi, handleApiError, leverage, manualSizing.capital, notify, selectedAccount, sizingMode]);

  const requestSchedulerToggle = useCallback(async () => {
    if (statusInfo?.is_running) {
      setTogglingScheduler(true);
      try {
        await api.controlCoinFullPrimeScheduler('stop');
        notify('info', t("定時自動換倉已停止。"));
        try {
          setStatusInfo(await api.getCoinFullPrimeStatus());
        } catch {
          // The stop request succeeded; the next refresh will confirm the state.
        }
      } catch (err: unknown) {
        notify('error', handleApiError(err, t("停止排程失敗")));
      } finally {
        setTogglingScheduler(false);
      }
      return;
    }
    if (!manualSizing.affordable) {
      notify('error', (t("本金 ") + (manualSizing.capital.toFixed(2)) + t(" USDT 超過帳戶可用保證金，請先調整倉位。")));
      return;
    }
    setDialog({ kind: 'scheduler-start' });
  }, [handleApiError, manualSizing.affordable, manualSizing.capital, notify, statusInfo?.is_running]);

  const executeStartScheduler = useCallback(async () => {
    setTogglingScheduler(true);
    try {
      await api.controlCoinFullPrimeScheduler('start', {
        accountId: selectedAccount,
        dryRun: dryRunMode,
        capital: sizingMode === 'fixed' ? manualSizing.capital : 0,
        leverage,
        balancePct: sizingMode === 'percent' ? balancePct : 0,
        excludeTradfi,
      });
      try {
        setStatusInfo(await api.getCoinFullPrimeStatus());
      } catch {
        // The scheduler start succeeded; the next refresh will pick up its state.
      }
      notify('success', t("定時自動換倉已啟動，將依排程執行。"));
    } catch (err: unknown) {
      notify('error', handleApiError(err, t("啟動排程失敗")));
    } finally {
      setTogglingScheduler(false);
    }
  }, [balancePct, dryRunMode, excludeTradfi, handleApiError, leverage, manualSizing.capital, notify, selectedAccount, sizingMode]);

  const confirmDialog = useCallback(() => {
    const kind = dialog?.kind;
    setDialog(null);
    if (kind === 'live-mode') applyMode(false);
    if (kind === 'rebalance') void executeRebalance();
    if (kind === 'apply-schedule') void executeApplySchedule();
    if (kind === 'scheduler-start') void executeStartScheduler();
  }, [applyMode, dialog?.kind, executeApplySchedule, executeRebalance, executeStartScheduler]);

  const mode = useMemo(() => modeCopy(currentAccount.environment, dryRunMode), [currentAccount.environment, dryRunMode]);
  const scheduleMode = useMemo(() => modeCopy(scheduleAccount.environment, scheduleDryRun), [scheduleAccount.environment, scheduleDryRun]);

  return {
    loading,
    refreshing,
    rebalancing,
    togglingScheduler,
    error,
    notice,
    clearError: () => setError(null),
    clearNotice: () => setNotice(null),
    rankResult,
    statusInfo,
    accounts,
    selectedAccount,
    currentAccount,
    positions,
    loadingPositions,
    managedPositions: positionSplit.managed,
    externalPositions: positionSplit.external,
    managedStats,
    externalStats,
    accountStats,
    dryRunMode,
    excludeTradfi,
    sizingMode,
    capitalInput,
    balancePct,
    leverage,
    manualSizing,
    scheduleConfig,
    scheduleAccountId,
    scheduleAccount,
    scheduleDryRun,
    scheduleSizing,
    scheduleReasons,
    scheduleAligned,
    filterTab,
    setFilter,
    searchQuery,
    setSearchQuery,
    showDisabledFactors,
    setShowDisabledFactors,
    displayedItems,
    universeSize,
    rankTimestamp,
    rankStale,
    longList,
    shortList,
    targetCount,
    isBenchmarkHedge,
    top1Long,
    top1Short,
    activeTab,
    setActiveTab,
    historyBatches,
    tradeFills,
    historyLoading,
    historyViewTab,
    setHistoryViewTab,
    includeSimulated,
    setIncludeSimulated,
    expandedBatchId,
    setExpandedBatchId,
    historyAccountFilter,
    setHistoryAccountFilter,
    dialog,
    closeDialog: () => setDialog(null),
    confirmDialog,
    mode,
    scheduleMode,
    actions: {
      refresh: () => loadAll(true),
      selectAccount,
      requestModeChange,
      applyMode,
      setSizing,
      updateCapital,
      updateBalancePct,
      updateLeverage,
      toggleExcludeTradfi,
      requestRebalance,
      requestApplySchedule,
      requestSchedulerToggle,
      refreshPositions: () => loadPositions(selectedAccount),
      refreshHistory: loadHistory,
      reload: () => loadAll(false),
    },
  };
};

export type CoinFullPrimeController = ReturnType<typeof useCoinFullPrime>;