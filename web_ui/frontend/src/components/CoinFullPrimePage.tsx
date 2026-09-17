import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { api, ApiError } from '../services/api';
import type {
  UniverseRankResult,
  RankedItem,
  CoinFullPrimeStatus,
  TradingAccountProfile,
  CoinFullPrimePosition,
  CoinFullPrimeFill,
  CoinFullPrimeHistoryBatch
} from '../services/api';
import {
  Activity,
  RefreshCw,
  Play,
  CheckCircle2,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Search,
  Sliders,
  Info,
  Wallet,
  ShieldCheck,
  ExternalLink,
  Layers,
  History,
  ChevronDown,
  ChevronRight,
  Clock
} from 'lucide-react';

interface CoinFullPrimePageProps {
  onUnauthorized?: () => void;
  onGoToRankings?: () => void;
}

const formatIsoToTaipei = (isoString?: string | null): string => {
  if (!isoString) return '待排程啟動';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString('zh-TW', {
      timeZone: 'Asia/Taipei',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }) + ' (台北時間)';
  } catch {
    return isoString;
  }
};

export const CoinFullPrimePage: React.FC<CoinFullPrimePageProps> = ({ onUnauthorized, onGoToRankings }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [rebalancing, setRebalancing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [rankResult, setRankResult] = useState<UniverseRankResult | null>(null);
  const [statusInfo, setStatusInfo] = useState<CoinFullPrimeStatus | null>(null);

  // 交易帳號與執行環境狀態 (全面持久化至 localStorage，防止重整/刷新時狀態跑掉)
  const [accounts, setAccounts] = useState<TradingAccountProfile[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>(() => {
    return localStorage.getItem('coinfull_selected_account') || 'binance_live';
  });

  // 篩選與搜尋狀態
  const [filterTab, setFilterTab] = useState<'baskets' | 'long' | 'short' | 'all'>(() => {
    const saved = localStorage.getItem('coinfull_filter_tab');
    if (saved === 'baskets' || saved === 'long' || saved === 'short' || saved === 'all') return saved;
    return 'baskets';
  });
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [dryRunMode, setDryRunMode] = useState<boolean>(() => {
    const saved = localStorage.getItem('coinfull_dry_run_mode');
    if (saved !== null) return saved === 'true';
    return true;
  });
  const hasInitializedModeRef = useRef<boolean>(false);
  const [lastActionMsg, setLastActionMsg] = useState<string | null>(null);

  // 換倉歷史與成交流水狀態
  const [historyBatches, setHistoryBatches] = useState<CoinFullPrimeHistoryBatch[]>([]);
  const [tradeFills, setTradeFills] = useState<CoinFullPrimeFill[]>([]);
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);
  const [historyViewTab, setHistoryViewTab] = useState<'batches' | 'fills'>('batches');
  const [includeSimulated, setIncludeSimulated] = useState<boolean>(true);
  const [expandedBatchId, setExpandedBatchId] = useState<number | null>(null);
  const [historyAccountFilter, setHistoryAccountFilter] = useState<string>('all');

  // 倉位規模與槓桿配置狀態
  const [sizingMode, setSizingMode] = useState<'fixed' | 'percent'>(() => {
    const saved = localStorage.getItem('coinfull_sizing_mode');
    return saved === 'percent' ? 'percent' : 'fixed';
  });
  const [capitalInput, setCapitalInput] = useState<string>(() => {
    return localStorage.getItem('coinfull_capital_input') || '1000';
  });
  const [balancePctInput, setBalancePctInput] = useState<number>(() => {
    const saved = localStorage.getItem('coinfull_balance_pct_input');
    return saved ? parseFloat(saved) : 80;
  });
  const [leverageInput, setLeverageInput] = useState<number>(() => {
    const saved = localStorage.getItem('coinfull_leverage_input');
    return saved ? parseFloat(saved) : 1.0;
  });

  // 當前選取的帳號物件
  const currentAccount = useMemo(() => {
    return accounts.find(a => a.id === selectedAccount) || {
      id: selectedAccount,
      name: selectedAccount === 'binance_testnet' ? 'Binance 合約測試網' : selectedAccount === 'binance_live' ? 'Binance 合約實盤' : 'Paper 本地模擬盤',
      environment: (selectedAccount === 'binance_testnet' ? 'testnet' : selectedAccount === 'binance_live' ? 'live' : 'paper') as 'paper' | 'testnet' | 'live',
      is_ready: selectedAccount === 'paper',
      balance_usdt: selectedAccount === 'paper' ? 10000 : null,
      missing_keys: selectedAccount === 'binance_testnet' ? ['BINANCE_TESTNET_API_KEY', 'BINANCE_TESTNET_API_SECRET'] : [],
      description: '',
      endpoint: ''
    };
  }, [accounts, selectedAccount]);

  // 計算有效總名義本金 (Effective Capital)
  const effectiveCapital = useMemo(() => {
    if (sizingMode === 'percent') {
      const avail = currentAccount.balance_usdt ?? 10000;
      return Math.max(0, Math.round((avail * (balancePctInput / 100)) * 100) / 100);
    }
    const val = parseFloat(capitalInput);
    return isNaN(val) || val <= 0 ? 1000 : val;
  }, [sizingMode, capitalInput, balancePctInput, currentAccount.balance_usdt]);

  // 目標配置數量 K (動態自適應 K 多 K 空平配)
  const targetCount = useMemo(() => {
    return rankResult?.long_list?.length || statusInfo?.current_portfolio?.long_top8?.length || 5;
  }, [rankResult?.long_list?.length, statusInfo?.current_portfolio?.long_top8?.length]);

  // 總名義曝險與單幣目標金額
  const grossExposure = useMemo(() => {
    return Math.round((effectiveCapital * leverageInput) * 100) / 100;
  }, [effectiveCapital, leverageInput]);

  const perSymbolNotional = useMemo(() => {
    return Math.round(((grossExposure * 0.5) / targetCount) * 100) / 100;
  }, [grossExposure, targetCount]);

  const updateDryRunMode = (val: boolean, overrideAcc?: string) => {
    setDryRunMode(val);
    try { localStorage.setItem('coinfull_dry_run_mode', String(val)); } catch { /* ignore */ }

    const accId = overrideAcc || selectedAccount;
    // 即時雙向同步後端背景定時排程下單模式
    void api.updateCoinFullPrimeSchedulerTarget({
      accountId: accId,
      dryRun: val,
      capital: sizingMode === 'fixed' ? effectiveCapital : undefined,
      leverage: leverageInput,
      balancePct: sizingMode === 'percent' ? balancePctInput : undefined
    }).then(() => {
      // 局部刷新排程狀態卡片
      void api.getCoinFullPrimeStatus().then(st => {
        if (st) setStatusInfo(st);
      });
    }).catch(e => {
      console.warn('同步後端排程下單模式異常:', e);
    });
  };

  const handleFilterTabChange = (tab: 'baskets' | 'long' | 'short' | 'all') => {
    setFilterTab(tab);
    try { localStorage.setItem('coinfull_filter_tab', tab); } catch { /* ignore */ }
  };

  const updateSizingMode = (mode: 'fixed' | 'percent') => {
    setSizingMode(mode);
    try { localStorage.setItem('coinfull_sizing_mode', mode); } catch { /* ignore */ }
  };

  const updateCapitalInput = (val: string) => {
    setCapitalInput(val);
    try { localStorage.setItem('coinfull_capital_input', val); } catch { /* ignore */ }
  };

  const updateBalancePctInput = (val: number) => {
    setBalancePctInput(val);
    try { localStorage.setItem('coinfull_balance_pct_input', String(val)); } catch { /* ignore */ }
  };

  const updateLeverageInput = (val: number) => {
    setLeverageInput(val);
    try { localStorage.setItem('coinfull_leverage_input', String(val)); } catch { /* ignore */ }
  };

  // 當前持倉狀態
  const [positions, setPositions] = useState<CoinFullPrimePosition[]>([]);
  const [loadingPositions, setLoadingPositions] = useState<boolean>(false);

  // 當前持倉彙總統計 (多方、空方、合計名義與未實現收益)
  const positionStats = useMemo(() => {
    const longPositions = positions.filter(p => p.side === 'long');
    const shortPositions = positions.filter(p => p.side === 'short');

    const longNotional = longPositions.reduce((s, p) => s + (p.notional_usdt || 0), 0);
    const shortNotional = shortPositions.reduce((s, p) => s + (p.notional_usdt || 0), 0);
    const totalNotional = longNotional + shortNotional;
    const netExposure = longNotional - shortNotional;

    const longPnl = longPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    const shortPnl = shortPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    const totalPnl = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);

    return {
      longPositions,
      shortPositions,
      longCount: longPositions.length,
      shortCount: shortPositions.length,
      totalCount: positions.length,
      longNotional,
      shortNotional,
      totalNotional,
      netExposure,
      longPnl,
      shortPnl,
      totalPnl,
    };
  }, [positions]);

  const loadPositions = useCallback(async (accId: string) => {
    if (accId === 'paper') {
      setPositions([]);
      return;
    }
    try {
      setLoadingPositions(true);
      const res = await api.getCoinFullPrimePositions(accId);
      if (res && Array.isArray(res.positions)) {
        setPositions(res.positions);
      } else {
        setPositions([]);
      }
    } catch (e) {
      console.warn('載入持倉異常:', e);
      setPositions([]);
    } finally {
      setLoadingPositions(false);
    }
  }, []);

  // 載入資料 (同步取得排行、狀態、帳號清單與持倉)
  const loadData = useCallback(async (refresh = false) => {
    try {
      setError(null);
      if (refresh) setRefreshing(true);
      else setLoading(true);

      const targetAcc = localStorage.getItem('coinfull_selected_account') || selectedAccount;

      const [rankings, status, accountsData, posData] = await Promise.all([
        api.getCoinFullPrimeRankings(refresh),
        api.getCoinFullPrimeStatus(),
        api.getCoinFullPrimeAccounts(),
        targetAcc !== 'paper' ? api.getCoinFullPrimePositions(targetAcc) : Promise.resolve({ positions: [] })
      ]);

      setRankResult(rankings);
      setStatusInfo(status);

      if (posData && Array.isArray(posData.positions)) {
        setPositions(posData.positions);
      }

      if (accountsData && Array.isArray(accountsData.accounts)) {
        setAccounts(accountsData.accounts);
        if (!hasInitializedModeRef.current) {
          const savedAcc = localStorage.getItem('coinfull_selected_account');
          const validAcc = (savedAcc && accountsData.accounts.some(a => a.id === savedAcc))
            ? savedAcc
            : (status?.scheduler_config && (status.scheduler_config as Record<string, unknown>).account_id
                ? String((status.scheduler_config as Record<string, unknown>).account_id)
                : (accountsData.selected_account || 'binance_live'));
          setSelectedAccount(validAcc);
          try { localStorage.setItem('coinfull_selected_account', validAcc); } catch { /* ignore */ }

          const savedDry = localStorage.getItem('coinfull_dry_run_mode');
          if (savedDry !== null) {
            setDryRunMode(savedDry === 'true');
          } else if (status?.scheduler_config && (status.scheduler_config as Record<string, unknown>).dry_run !== undefined) {
            const backendDry = Boolean((status.scheduler_config as Record<string, unknown>).dry_run);
            setDryRunMode(backendDry);
            try { localStorage.setItem('coinfull_dry_run_mode', String(backendDry)); } catch { /* ignore */ }
          } else {
            const defaultDry = validAcc === 'binance_testnet' ? false : true;
            setDryRunMode(defaultDry);
            try { localStorage.setItem('coinfull_dry_run_mode', String(defaultDry)); } catch { /* ignore */ }
          }
          hasInitializedModeRef.current = true;
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        onUnauthorized?.();
        return;
      }
      setError(err instanceof Error ? err.message : '載入 CoinFull Prime 數據失敗');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onUnauthorized, selectedAccount]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadData(false);
  }, [loadData]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPositions(selectedAccount);
  }, [selectedAccount, loadPositions]);

  // 載入調倉歷史與成交流水
  const loadHistory = useCallback(async () => {
    try {
      setHistoryLoading(true);
      const accFilter = historyAccountFilter === 'all' ? undefined : historyAccountFilter;
      const [histRes, fillsRes] = await Promise.all([
        api.getCoinFullPrimeHistory(accFilter, 50),
        api.getCoinFullPrimeFills(accFilter, 200, includeSimulated)
      ]);
      if (histRes && Array.isArray(histRes.batches)) {
        setHistoryBatches(histRes.batches);
      }
      if (fillsRes && Array.isArray(fillsRes.fills)) {
        setTradeFills(fillsRes.fills);
      }
    } catch (e) {
      console.warn('載入 CoinFull Prime 歷史與成交流水異常:', e);
    } finally {
      setHistoryLoading(false);
    }
  }, [historyAccountFilter, includeSimulated]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadHistory();
  }, [loadHistory]);

  // 切換交易帳號環境，智慧連動下單模式並持久化，並即時同步後端定時排程目標
  const handleAccountChange = async (accId: string) => {
    setSelectedAccount(accId);
    try { localStorage.setItem('coinfull_selected_account', accId); } catch { /* ignore */ }

    const isDry = accId === 'paper' ? false : dryRunMode;
    updateDryRunMode(isDry, accId);
    void loadPositions(accId);
  };

  // 手動同步排程目標：保留使用者當前選取的發單模式 (避免誤重置為安全試算)
  const handleSyncSchedulerTarget = async () => {
    try {
      await api.updateCoinFullPrimeSchedulerTarget({
        accountId: selectedAccount,
        dryRun: dryRunMode,
        capital: sizingMode === 'fixed' ? effectiveCapital : undefined,
        leverage: leverageInput,
        balancePct: sizingMode === 'percent' ? balancePctInput : undefined
      });
      setLastActionMsg(`✅ 已成功將背景定時換倉排程同步至【${currentAccount.name}】(${dryRunMode ? '🛡️ 安全試算保護' : '🚀 實盤真實發單'})`);
      void loadData(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '同步排程失敗');
    }
  };

  // 手動觸發換倉
  const handleTriggerRebalance = async () => {
    if (!dryRunMode && !currentAccount.is_ready) {
      alert(`⚠️ 執行被阻擋：帳號【${currentAccount.name}】未就緒！\n缺少配置：${currentAccount.missing_keys?.join(', ')}\n請先在 .env 補齊金鑰或切換為 Paper 本地模擬盤。`);
      return;
    }

    const sizingSummary = sizingMode === 'percent'
      ? `帳戶可用保證金 ${balancePctInput}% (約 $${effectiveCapital.toLocaleString()} USDT)`
      : `固定本金 $${effectiveCapital.toLocaleString()} USDT`;

    let confirmMsg: string;
    const rebalanceRuleNotice = `\n\n💡 換倉執行邏輯：\n1. 查詢交易所當前持倉。\n2. 掉出今日 Top ${targetCount} / Bottom ${targetCount} 的標的將自動全數平倉 (釋放保證金)。\n3. 留榜標的僅微調數量差額 (不重複平開，節省摩擦手續費)。\n4. 新進榜標的建立全新多/空部位。`;

    if (dryRunMode) {
      confirmMsg = `即將執行 [DRY-RUN 模擬換倉]\n• 目標環境: ${currentAccount.name}\n• 總名義本金: ${sizingSummary}\n• 槓桿倍數: ${leverageInput}x (總名義曝險: $${grossExposure.toLocaleString()} USDT)\n• 預估單幣分配 (${targetCount}多${targetCount}空): $${perSymbolNotional.toFixed(2)} USDT${rebalanceRuleNotice}\n\n僅計算並在日誌記錄訂單，不會發送真實交易。確定執行嗎？`;
    } else if (currentAccount.environment === 'testnet') {
      confirmMsg = `🧪 注意：即將向【Binance 合約測試網】發送真實 API 訂單！\n• 總名義本金: ${sizingSummary}\n• 槓桿倍數: ${leverageInput}x (總名義曝險: $${grossExposure.toLocaleString()} USDT)\n• 單幣預計下單: $${perSymbolNotional.toFixed(2)} USDT (${targetCount}多${targetCount}空共 ${targetCount * 2} 筆)${rebalanceRuleNotice}\n\n將使用測試網沙盒資金撮合。確定繼續嗎？`;
    } else if (currentAccount.environment === 'live') {
      confirmMsg = `🚨 極度危險警告：即將向【Binance 實盤】發送真實真金訂單！\n• 總名義本金: ${sizingSummary}\n• 槓桿倍數: ${leverageInput}x (總名義曝險: $${grossExposure.toLocaleString()} USDT)\n• 單幣預計下單: $${perSymbolNotional.toFixed(2)} USDT (${targetCount}多${targetCount}空共 ${targetCount * 2} 筆)${rebalanceRuleNotice}\n\n這將在幣安交易所真實下單並調整真實保證金倉位！\n請務必再次確認是否繼續？`;
    } else {
      confirmMsg = `即將在【Paper 本地模擬盤】執行模擬撮合換倉。\n• 總名義本金: ${sizingSummary}\n• 槓桿倍數: ${leverageInput}x (單幣 $${perSymbolNotional.toFixed(2)} USDT)${rebalanceRuleNotice}\n確定執行嗎？`;
    }

    if (!window.confirm(confirmMsg)) return;

    try {
      setRebalancing(true);
      setLastActionMsg(null);
      const options = sizingMode === 'percent'
        ? { balancePct: balancePctInput, leverage: leverageInput }
        : { capital: effectiveCapital, leverage: leverageInput };

      const res = await api.triggerCoinFullPrimeRebalance(dryRunMode, selectedAccount, options);
      const ordersCount = typeof res.orders_count === 'number' ? res.orders_count : 0;
      const isDry = Boolean(res.dry_run);
      const closed = Array.isArray(res.closed_symbols) ? res.closed_symbols.length : 0;
      const opened = Array.isArray(res.opened_symbols) ? res.opened_symbols.length : 0;
      setLastActionMsg(
        `✅ 換倉完成！共生成 ${ordersCount} 筆訂單 (平掉舊倉: ${closed} 隻, 開立新倉: ${opened} 隻) | 環境: ${currentAccount.name}, 本金: $${effectiveCapital.toLocaleString()} USDT, 槓桿: ${leverageInput}x, 模式: ${isDry ? 'Dry-Run 模擬' : '已實際下單'}`
      );
      void loadData(false);
      void loadPositions(selectedAccount);
      void loadHistory();
    } catch (err: unknown) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        onUnauthorized?.();
        return;
      }
      setError(err instanceof Error ? err.message : '執行換倉失敗');
    } finally {
      setRebalancing(false);
    }
  };

  const [togglingScheduler, setTogglingScheduler] = useState<boolean>(false);

  const handleToggleScheduler = async () => {
    const isCurrentlyRunning = Boolean(statusInfo?.is_running);
    const action = isCurrentlyRunning ? 'stop' : 'start';
    try {
      setTogglingScheduler(true);
      setError(null);
      const options = action === 'start' ? {
        accountId: selectedAccount,
        dryRun: dryRunMode,
        capital: sizingMode === 'fixed' ? effectiveCapital : undefined,
        leverage: leverageInput,
        balancePct: sizingMode === 'percent' ? balancePctInput : undefined
      } : undefined;

      await api.controlCoinFullPrimeScheduler(action, options);
      const modeDesc = currentAccount.environment === 'testnet'
        ? (!dryRunMode ? '測試網真實下單 (API 撮合)' : '測試網純試算')
        : currentAccount.environment === 'live'
          ? (!dryRunMode ? '實盤真實真金發單' : '實盤安全試算保護')
          : '本地模擬撮合';

      const schedDesc = ((statusInfo?.scheduler_config as Record<string, unknown> | undefined)?.description as string)
        || '每週週四 UTC 00:05';
      setLastActionMsg(
        action === 'start'
          ? `🟢 定時自動換倉已啟動！已依設定排程（${schedDesc}）自動以【${currentAccount.name}】(${modeDesc}) 執行換倉。`
          : '⚪ 定時自動換倉排程已停止。'
      );
      void loadData(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '操作排程失敗');
    } finally {
      setTogglingScheduler(false);
    }
  };

  // 依篩選條件過濾列表
  const displayedItems = useMemo(() => {
    if (!rankResult || !rankResult.all_rankings) return [];
    let list: RankedItem[];

    if (filterTab === 'baskets') {
      list = [...rankResult.long_list, ...rankResult.short_list];
    } else if (filterTab === 'long') {
      list = rankResult.long_list;
    } else if (filterTab === 'short') {
      list = rankResult.short_list;
    } else {
      list = rankResult.all_rankings;
    }

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toUpperCase();
      list = list.filter(item => item.symbol.toUpperCase().includes(q));
    }

    return list;
  }, [rankResult, filterTab, searchQuery]);

  // 輔助函式：評價樣式標籤顏色
  const getScoreBadgeClass = (score: number) => {
    if (score >= 0.8) return 'badge-long-strong';
    if (score >= 0.3) return 'badge-long-medium';
    if (score <= -0.8) return 'badge-short-strong';
    if (score <= -0.3) return 'badge-short-medium';
    return 'badge-neutral';
  };

  if (loading && !rankResult) {
    return (
      <div className="coinfull-container">
        <div className="glass-panel text-center" style={{ padding: '4rem 1rem' }}>
          <Activity size={32} className="spin text-accent" style={{ margin: '0 auto 1rem' }} />
          <h3>載入 CoinFull Prime 每日截面多空排行中…</h3>
          <p className="text-muted">正在非同步同步交易所合約微觀特徵與 NumPy 矩陣評分</p>
        </div>
      </div>
    );
  }

  const top1Long = rankResult?.long_list?.[0];
  const top1Short = rankResult?.short_list?.[rankResult.short_list.length - 1];

  return (
    <div className="coinfull-container">
      {/* 頁頭與控制列 */}
      <header className="coinfull-header">
        <div className="header-title-block">
          <div className="title-with-badge">
            <h2>CoinFull Prime 策略機器人</h2>
            <span className="badge badge-accent">{targetCount} 多 {targetCount} 空市場中性量化對沖</span>
            {currentAccount.environment === 'testnet' && !dryRunMode && (
              <span className="badge badge-warning" title="已連線幣安合約測試網，發送 API 真實撮合訂單">
                🧪 測試網 API 撮合中
              </span>
            )}
            {currentAccount.environment === 'live' && !dryRunMode && (
              <span className="badge badge-danger" title="警告：目前處於幣安實盤真金白銀發單模式">
                🚨 實盤真金交易中
              </span>
            )}
            {(dryRunMode || currentAccount.environment === 'paper') && (
              <span className="badge badge-secondary" title="僅在本地試算，不發送任何外部訂單">
                {currentAccount.environment === 'paper' ? '📝 本地模擬環境' : '🔍 僅試算 (不發單)'}
              </span>
            )}
          </div>
          <p className="text-muted">
            專屬調倉與運行控制台 · 自動防護隔離網格與 DCA 標的 · 依據每日截面因子打分動態對沖調倉
          </p>
        </div>

        <div className="header-actions">
          {onGoToRankings && (
            <button
              type="button"
              className="btn btn-outline"
              onClick={onGoToRankings}
              title="前往查看今日 79 幣因子評分與客觀盤面多空排行榜"
            >
              <span>📊 查看多空截面排行</span>
              <ExternalLink size={13} style={{ marginLeft: '0.2rem' }} />
            </button>
          )}

          <button
            type="button"
            className="btn btn-outline"
            onClick={() => loadData(true)}
            disabled={refreshing || rebalancing}
          >
            <RefreshCw size={15} className={refreshing ? 'spin' : ''} />
            <span>{refreshing ? '重新計算中…' : '即時重新計算'}</span>
          </button>

          <button
            type="button"
            className="btn btn-primary"
            onClick={handleTriggerRebalance}
            disabled={refreshing || rebalancing}
          >
            <Play size={15} />
            <span>{rebalancing ? '換倉執行中…' : '立即執行換倉'}</span>
          </button>
        </div>
      </header>

      {/* 交易帳號與執行環境智能控制列 */}
      <section className="account-env-bar glass-panel">
        <div className="account-env-left">
          <div className="env-selector-group">
            <label className="env-label" htmlFor="coinfull-account-select">
              <Wallet size={15} className="text-accent" />
              <span>交易帳號 / 運行環境:</span>
            </label>
            <select
              id="coinfull-account-select"
              className="env-select"
              value={selectedAccount}
              onChange={(e) => handleAccountChange(e.target.value)}
              disabled={rebalancing}
            >
              {accounts.map(acc => (
                <option key={acc.id} value={acc.id}>
                  {acc.name} {acc.balance_usdt !== null ? `($${acc.balance_usdt.toLocaleString()} USDT)` : (!acc.is_ready ? '(⚠️ 未配置金鑰)' : '')}
                </option>
              ))}
            </select>
          </div>

          <div className="account-status-chip">
            {currentAccount.is_ready ? (
              <span className={`badge ${currentAccount.environment === 'live' ? 'badge-success' : currentAccount.environment === 'testnet' ? 'badge-warning' : 'badge-accent'}`}>
                <ShieldCheck size={12} style={{ marginRight: '0.25rem' }} />
                <span>{currentAccount.environment === 'live' ? '實盤就緒' : currentAccount.environment === 'testnet' ? '測試網已連線' : '本地模擬撮合'}</span>
              </span>
            ) : (
              <span className="badge badge-danger">
                <AlertTriangle size={12} style={{ marginRight: '0.25rem' }} />
                <span>未配置金鑰</span>
              </span>
            )}

            {currentAccount.balance_usdt !== null && currentAccount.balance_usdt !== undefined ? (
              <span className="balance-chip">
                可用保證金: <strong>${currentAccount.balance_usdt.toLocaleString()} USDT</strong>
              </span>
            ) : null}

            {/* 背景排程真實鎖定狀態展示 */}
            {(() => {
              const schedCfg = statusInfo?.scheduler_config as Record<string, unknown> | undefined;
              const schedAccId = (typeof schedCfg?.account_id === 'string' ? schedCfg.account_id : null) || statusInfo?.selected_account || 'paper';
              const schedDryRun = schedCfg?.dry_run !== undefined ? Boolean(schedCfg.dry_run) : true;
              const schedAccObj = accounts.find(a => a.id === schedAccId);
              const schedAccName = schedAccObj?.name || (schedAccId === 'binance_live' ? 'Binance 合約實盤' : schedAccId === 'binance_testnet' ? 'Binance 測試網' : 'Paper 模擬盤');
              const schedIsLive = schedAccId === 'binance_live';
              const isAligned = schedAccId === selectedAccount && schedDryRun === dryRunMode;

              return (
                <div
                  className="scheduler-lock-badge"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.35rem',
                    background: isAligned ? 'rgba(34, 197, 94, 0.12)' : 'rgba(234, 179, 8, 0.15)',
                    border: `1px solid ${isAligned ? 'rgba(34, 197, 94, 0.3)' : 'rgba(234, 179, 8, 0.4)'}`,
                    padding: '0.2rem 0.55rem',
                    borderRadius: '5px',
                    fontSize: '0.74rem'
                  }}
                >
                  <Clock size={12} className={isAligned ? 'text-success' : 'text-warning'} />
                  <span>
                    【背景定時鎖定】：<strong>{schedAccName}</strong> ·{' '}
                    <span style={{ fontWeight: 600, color: schedIsLive && !schedDryRun ? '#ef4444' : schedDryRun ? 'var(--text-muted)' : '#3b82f6' }}>
                      {schedIsLive
                        ? (!schedDryRun ? '🚨 實盤真金' : '🛡️ 試算保護')
                        : schedAccId === 'binance_testnet'
                        ? (!schedDryRun ? '🚀 測試網' : '🔍 測試試算')
                        : (!schedDryRun ? '📝 模擬撮合' : '🔍 模擬試算')}
                    </span>
                  </span>
                  {!isAligned && (
                    <button
                      type="button"
                      className="btn btn-outline"
                      style={{
                        padding: '0.05rem 0.35rem',
                        fontSize: '0.68rem',
                        borderColor: 'var(--warning)',
                        color: 'var(--warning)',
                        marginLeft: '0.15rem',
                        borderRadius: '3px'
                      }}
                      onClick={handleSyncSchedulerTarget}
                      title="將後端背景定時排程目標立即同步為目前選取的帳號與下單模式"
                    >
                      ⚠️ 同步排程至【{currentAccount.name}】
                    </button>
                  )}
                </div>
              );
            })()}
          </div>
        </div>

        <div className="account-env-right">
          <div className="dry-run-control">
            <span className="control-label">下單模式:</span>
            {currentAccount.environment === 'testnet' && (
              <>
                <button
                  type="button"
                  className={`pill-btn warning ${!dryRunMode ? 'active' : ''}`}
                  onClick={() => updateDryRunMode(false)}
                  style={{ fontSize: '0.78rem', padding: '0.25rem 0.65rem' }}
                  title="將真實呼叫 Binance 合約測試網 API 進行下單撮合 (使用測試網沙盒資金)"
                >
                  🚀 測試網真實下單 (API 撮合)
                </button>
                <button
                  type="button"
                  className={`pill-btn ${dryRunMode ? 'active' : ''}`}
                  onClick={() => updateDryRunMode(true)}
                  style={{ fontSize: '0.78rem', padding: '0.25rem 0.65rem' }}
                  title="僅在記憶體中試算選幣與部位，不發送任何訂單至交易所"
                >
                  🔍 僅試算 (不發單)
                </button>
              </>
            )}

            {currentAccount.environment === 'live' && (
              <>
                <button
                  type="button"
                  className={`pill-btn ${dryRunMode ? 'active' : ''}`}
                  onClick={() => updateDryRunMode(true)}
                  style={{ fontSize: '0.78rem', padding: '0.25rem 0.65rem' }}
                  title="安全防護：僅試算不發單，防止誤操作扣除真金白銀"
                >
                  🛡️ 安全試算保護 (不發單)
                </button>
                <button
                  type="button"
                  className={`pill-btn danger ${!dryRunMode ? 'active' : ''}`}
                  onClick={() => {
                    if (window.confirm('🚨 確定要切換為【實盤真實發單】嗎？\n這將在幣安實盤帳戶中以真實 USDT 開立合約倉位！')) {
                      updateDryRunMode(false);
                    }
                  }}
                  style={{ fontSize: '0.78rem', padding: '0.25rem 0.65rem', color: !dryRunMode ? '#ef4444' : undefined }}
                  title="警告：直接使用真實資金在幣安合約發單！"
                >
                  🚨 實盤真實發單 (真金)
                </button>
              </>
            )}

            {currentAccount.environment === 'paper' && (
              <button
                type="button"
                className="pill-btn active"
                style={{ fontSize: '0.78rem', padding: '0.25rem 0.65rem' }}
                title="純本地內部模擬撮合，無需網路金鑰"
              >
                📝 本地模擬撮合 (無外部風險)
              </button>
            )}
          </div>
        </div>
      </section>

      {/* 倉位規模與槓桿控制列 */}
      <section className="sizing-control-bar glass-panel">
        <div className="sizing-bar-left">
          <div className="control-group">
            <span className="control-label">
              <Sliders size={13} style={{ marginRight: '0.25rem' }} />
              本金規模:
            </span>
            <div className="segmented-pill">
              <button
                type="button"
                className={`seg-btn ${sizingMode === 'fixed' ? 'active' : ''}`}
                onClick={() => updateSizingMode('fixed')}
              >
                固定 USDT
              </button>
              <button
                type="button"
                className={`seg-btn ${sizingMode === 'percent' ? 'active' : ''}`}
                onClick={() => updateSizingMode('percent')}
              >
                可用餘額 %
              </button>
            </div>
          </div>

          {sizingMode === 'fixed' ? (
            <div className="sizing-input-wrap">
              <span className="input-prefix">$</span>
              <input
                type="number"
                min="50"
                step="50"
                className="sizing-input"
                value={capitalInput}
                onChange={(e) => updateCapitalInput(e.target.value)}
                placeholder="1000"
              />
              <span className="input-suffix">USDT</span>
            </div>
          ) : (
            <div className="percent-buttons-wrap">
              {[25, 50, 80, 100].map((pct) => (
                <button
                  key={pct}
                  type="button"
                  className={`pct-quick-btn ${balancePctInput === pct ? 'active' : ''}`}
                  onClick={() => updateBalancePctInput(pct)}
                >
                  {pct}%
                </button>
              ))}
              <span className="pct-calc-preview">
                (≈ <strong>${effectiveCapital.toLocaleString()}</strong> USDT)
              </span>
            </div>
          )}

          <div className="control-group leverage-group">
            <span className="control-label">槓桿倍數:</span>
            <div className="segmented-pill">
              {[1.0, 1.2, 1.5, 2.0].map((lev) => (
                <button
                  key={lev}
                  type="button"
                  className={`seg-btn ${leverageInput === lev ? 'active' : ''}`}
                  onClick={() => updateLeverageInput(lev)}
                >
                  {lev}x
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="sizing-bar-right">
          <div className="sizing-calc-chip">
            <span className="calc-title">💡 換倉試算 ({targetCount}多{targetCount}空):</span>
            <span className="calc-metric">總名義曝險 <strong>${grossExposure.toLocaleString()}</strong></span>
            <span className="calc-divider">•</span>
            <span className="calc-metric highlight">單幣目標 <strong>${perSymbolNotional.toFixed(2)} USDT</strong></span>
          </div>
        </div>
      </section>

      {/* 測試網引導卡片 (若切換至測試網但尚未在 .env 配置金鑰) */}
      {selectedAccount === 'binance_testnet' && !currentAccount.is_ready && (
        <div className="alert-banner alert-warning" style={{ margin: '0.75rem 0', display: 'flex', flexDirection: 'column', gap: '0.35rem', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
            <Info size={16} />
            <span>尚未配置 Binance 合約測試網金鑰（目前僅能以 Dry-Run 模擬運行）</span>
          </div>
          <div style={{ fontSize: '0.78rem', color: '#cbd5e1', lineHeight: 1.6 }}>
            1. 前往 <a href="https://testnet.binancefuture.com" target="_blank" rel="noreferrer" style={{ color: '#60a5fa', textDecoration: 'underline' }}>testnet.binancefuture.com <ExternalLink size={11} style={{ display: 'inline' }} /></a> 使用 GitHub / Google 免費登入。<br />
            2. 在頁面底端點擊生成 <strong>ApiKey</strong> 與 <strong>SecretKey</strong>，並在頂端點擊水龍頭免費領取 15,000 虛擬 USDT。<br />
            3. 在專案 <code>.env</code> 檔案加入以下兩行並重啟後台即可通訊：<br />
            <code style={{ background: 'rgba(0,0,0,0.4)', padding: '0.15rem 0.4rem', borderRadius: '4px', display: 'inline-block', marginTop: '0.2rem' }}>
              BINANCE_TESTNET_API_KEY=你的測試網金鑰<br />
              BINANCE_TESTNET_API_SECRET=你的測試網密鑰
            </code>
          </div>
        </div>
      )}

      {/* 提示或錯誤訊息 */}
      {error && (
        <div className="alert-banner alert-danger">
          <AlertTriangle size={18} />
          <span>{error}</span>
          <button type="button" className="close-btn" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {lastActionMsg && (
        <div className="alert-banner alert-success">
          <CheckCircle2 size={18} />
          <span>{lastActionMsg}</span>
          <button type="button" className="close-btn" onClick={() => setLastActionMsg(null)}>✕</button>
        </div>
      )}

      {/* 核心資訊摘要卡片 */}
      <section className="coinfull-summary-cards">
        <div className="glass-card summary-card long-card">
          <div className="card-top">
            <span className="card-label">🟢 最強多頭首選 (Top 1)</span>
            <TrendingUp size={20} className="text-positive" />
          </div>
          <div className="card-symbol">{top1Long?.symbol || '—'}</div>
          <div className="card-score-row">
            <span className="score-val text-positive">+{top1Long?.score.toFixed(4)}</span>
            <span className="eval-tag">{top1Long?.evaluations.overall_eval}</span>
          </div>
          <div className="card-detail-sub">
            動能: {(top1Long?.raw_params?.price_change_pct ?? 0) > 0 ? '+' : ''}{top1Long?.raw_params?.price_change_pct ?? 0}% ({top1Long?.evaluations?.momentum_eval || '—'}) · 費率: {top1Long?.raw_params?.funding_rate_pct ?? 0}% ({top1Long?.evaluations?.funding_eval || '—'})
          </div>
        </div>

        <div className="glass-card summary-card short-card">
          <div className="card-top">
            <span className="card-label">🔴 最弱空頭重災 (Bottom 1)</span>
            <TrendingDown size={20} className="text-negative" />
          </div>
          <div className="card-symbol">{top1Short?.symbol || '—'}</div>
          <div className="card-score-row">
            <span className="score-val text-negative">{top1Short?.score.toFixed(4)}</span>
            <span className="eval-tag">{top1Short?.evaluations.overall_eval}</span>
          </div>
          <div className="card-detail-sub">
            動能: {(top1Short?.raw_params?.price_change_pct ?? 0) > 0 ? '+' : ''}{top1Short?.raw_params?.price_change_pct ?? 0}% ({top1Short?.evaluations?.momentum_eval || '—'}) · 流向: {top1Short?.raw_params?.taker_flow_ratio ?? 1} ({top1Short?.evaluations?.flow_eval || '—'})
          </div>
        </div>

        <div className="glass-card summary-card neutral-card">
          <div className="card-top">
            <span className="card-label">⚖️ 對沖籃子組合配置</span>
            <Sliders size={20} className="text-accent" />
          </div>
          <div className="card-stats-grid">
            <div className="stat-box">
              <span className="stat-num text-positive">{targetCount} 隻</span>
              <span className="stat-desc">做多標的 (各 {(100 / targetCount).toFixed(1)}%)</span>
              {positions.length > 0 && (
                <span
                  className={`stat-pnl-chip ${positionStats.longPnl >= 0 ? 'text-positive' : 'text-negative'}`}
                  title="目前選定帳號之多方持倉未實現收益"
                >
                  多方: {positionStats.longPnl >= 0 ? '+' : ''}{positionStats.longPnl.toFixed(2)} U
                </span>
              )}
            </div>
            <div className="stat-divider">VS</div>
            <div className="stat-box">
              <span className="stat-num text-negative">{targetCount} 隻</span>
              <span className="stat-desc">做空標的 (各 {(100 / targetCount).toFixed(1)}%)</span>
              {positions.length > 0 && (
                <span
                  className={`stat-pnl-chip ${positionStats.shortPnl >= 0 ? 'text-positive' : 'text-negative'}`}
                  title="目前選定帳號之空方持倉未實現收益"
                >
                  空方: {positionStats.shortPnl >= 0 ? '+' : ''}{positionStats.shortPnl.toFixed(2)} U
                </span>
              )}
            </div>
          </div>
          <div className="card-detail-sub text-center">
            {positions.length > 0 ? (
              <span>
                持倉未實現: <strong className={positionStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}>{positionStats.totalPnl >= 0 ? '+' : ''}{positionStats.totalPnl.toFixed(2)} USDT</strong> · 淨敞口: {positionStats.netExposure >= 0 ? '+' : ''}${positionStats.netExposure.toFixed(1)} USDT
              </span>
            ) : (
              <span>名義本金: ${effectiveCapital.toLocaleString()} USDT · 槓桿: {leverageInput}x (多空對沖淨敞口 ≈ 0)</span>
            )}
          </div>
        </div>

        <div className="glass-card summary-card info-card">
          <div className="card-top">
            <span className="card-label">⏰ 定時排程與健康狀態</span>
            {statusInfo?.is_running ? (
              <CheckCircle2 size={20} className="text-positive" />
            ) : (
              <AlertTriangle size={20} className="text-muted" />
            )}
          </div>
          <div className="schedule-status-row">
            <span className={`status-dot ${statusInfo?.is_running ? 'online' : 'offline'}`}></span>
            <span className="status-title">
              {statusInfo?.is_running ? '定時自動換倉監聽中 (運行中)' : '排程待機中 (未啟動)'}
            </span>
          </div>
          <div className="schedule-detail">
            <div>
              <span>
                換倉排程: <strong>{((statusInfo?.scheduler_config as Record<string, unknown> | undefined)?.description as string) || '每週週四 UTC 00:05 換倉'}</strong>
              </span>
            </div>
            <div>
              <span>
                下次定時換倉: <strong>{statusInfo?.is_running ? formatIsoToTaipei(statusInfo.next_scheduled_run_utc) : '待排程啟動'}</strong>
                {statusInfo?.is_running && (statusInfo.hours_until_next_run || statusInfo.seconds_until_next_run) && (
                  <span> (約 {statusInfo.hours_until_next_run || Math.round((statusInfo.seconds_until_next_run || 0) / 3600)} 小時後)</span>
                )}
              </span>
            </div>
            {(() => {
              const schedCfg = statusInfo?.scheduler_config as Record<string, unknown> | undefined;
              const schedAccId = (typeof schedCfg?.account_id === 'string' ? schedCfg.account_id : null) || statusInfo?.selected_account || selectedAccount;
              const schedDryRun = schedCfg?.dry_run !== undefined ? Boolean(schedCfg.dry_run) : true;
              const targetAccName = accounts.find(a => a.id === schedAccId)?.name || (schedAccId === 'binance_live' ? 'Binance 合約實盤' : schedAccId === 'binance_testnet' ? 'Binance 測試網' : 'Paper 模擬盤');
              const needsSync = Boolean(schedCfg?.account_id && schedCfg.account_id !== selectedAccount);
              const schedIsLive = schedAccId === 'binance_live';

              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                  <span>
                    定時排程實際目標: <strong>{targetAccName}</strong>
                    · {schedIsLive ? (!schedDryRun ? '🚨 實盤真實發單 (真金)' : '🛡️ 實盤安全試算') : schedAccId === 'binance_testnet' ? (!schedDryRun ? '🚀 測試網真實下單' : '🔍 測試網試算') : (!schedDryRun ? '📝 本地模擬撮合' : '🔍 模擬試算')}
                  </span>
                  {needsSync && (
                    <button
                      type="button"
                      className="btn btn-outline"
                      style={{ padding: '0.1rem 0.4rem', fontSize: '0.68rem', borderColor: 'var(--accent)', color: 'var(--accent)' }}
                      onClick={handleSyncSchedulerTarget}
                      title="將後端背景定時排程目標同步更新為目前選取的帳號與下單模式"
                    >
                      🔄 同步排程至【{currentAccount.name}】
                    </button>
                  )}
                </div>
              );
            })()}
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              🛡️ 三層防護護欄啟用：已自動隔離 <strong>{(statusInfo as unknown as { protected_bot_symbols?: string[] })?.protected_bot_symbols?.length || 0}</strong> 個網格/DCA外部機器人標的
            </div>
          </div>
          <div className="card-detail-sub" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.4rem' }}>
            <span>母體覆蓋: {rankResult?.universe_size || 79} 幣</span>
            <button
              type="button"
              className={`btn ${statusInfo?.is_running ? 'btn-outline' : 'btn-primary'}`}
              style={{ padding: '0.2rem 0.6rem', fontSize: '0.72rem', borderRadius: '4px' }}
              onClick={handleToggleScheduler}
              disabled={togglingScheduler}
            >
              {togglingScheduler ? '處理中…' : statusInfo?.is_running ? '暫停自動排程' : '▶ 啟動定時自動換倉'}
            </button>
          </div>
        </div>
      </section>

      {/* 帳號當前實時持倉與隔日換倉狀態 */}
      <section className="coinfull-positions-panel glass-panel">
        <div className="positions-header">
          <div className="positions-title-wrap">
            <Layers size={18} className="text-accent" />
            <span className="positions-title">
              【{currentAccount.name}】當前合約庫存狀態
            </span>
            <span className="badge badge-neutral">
              {positions.length > 0 ? `${positions.length} 隻合約持倉中` : '目前零持倉'}
            </span>
            {positions.length > 0 && (
              <span className="positions-stats-summary">
                多方收益: <strong className={positionStats.longPnl >= 0 ? 'text-positive' : 'text-negative'}>{positionStats.longPnl >= 0 ? '+' : ''}{positionStats.longPnl.toFixed(2)} USDT</strong>
                {' · '}
                空方收益: <strong className={positionStats.shortPnl >= 0 ? 'text-positive' : 'text-negative'}>{positionStats.shortPnl >= 0 ? '+' : ''}{positionStats.shortPnl.toFixed(2)} USDT</strong>
                {' · '}
                總未實現: <strong className={positionStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}>{positionStats.totalPnl >= 0 ? '+' : ''}{positionStats.totalPnl.toFixed(2)} USDT</strong>
                {' · '}
                <span className="positions-notional-sub">
                  多頭: ${positionStats.longNotional.toLocaleString()} / 空頭: ${positionStats.shortNotional.toLocaleString()}
                </span>
              </span>
            )}
          </div>
          <div className="positions-actions">
            <span className="positions-hint">
              💡 三層防護狀態機：外部網格/DCA機器人持倉自動標記保護（🛡️ 絕對不平倉、不干擾）。換倉時僅針對 CoinFull Prime 專屬籃子中退榜標的平倉以釋放保證金。
            </span>
            <button
              type="button"
              className="btn btn-outline"
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
              onClick={() => loadPositions(selectedAccount)}
              disabled={loadingPositions}
            >
              <RefreshCw size={12} className={loadingPositions ? 'spin' : ''} />
              <span>{loadingPositions ? '查詢中…' : '刷新庫存'}</span>
            </button>
          </div>
        </div>

        {/* 多空盈虧即時總覽看板 */}
        {positions.length > 0 && (
          <div className="coinfull-pnl-banner">
            <div className="pnl-banner-card card-long">
              <div className="pnl-card-header">
                <span className="pnl-card-title">
                  <TrendingUp size={15} className="text-positive" />
                  <span>多方部位未實現收益</span>
                </span>
                <span className="badge badge-long-basket">
                  {positionStats.longCount} 隻做多 (Long)
                </span>
              </div>
              <div className="pnl-card-value-row">
                <span className={`pnl-card-val ${positionStats.longPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                  {positionStats.longPnl >= 0 ? '+' : ''}{positionStats.longPnl.toFixed(2)}
                  <span className="pnl-unit">USDT</span>
                </span>
              </div>
              <div className="pnl-card-sub">
                <span>名義曝險: <strong>${positionStats.longNotional.toLocaleString()} USDT</strong></span>
                {positionStats.longNotional > 0 && (
                  <span className={`pnl-yield ${positionStats.longPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                    ({positionStats.longPnl >= 0 ? '+' : ''}{((positionStats.longPnl / positionStats.longNotional) * 100).toFixed(2)}%)
                  </span>
                )}
              </div>
            </div>

            <div className="pnl-banner-card card-short">
              <div className="pnl-card-header">
                <span className="pnl-card-title">
                  <TrendingDown size={15} className="text-negative" />
                  <span>空方部位未實現收益</span>
                </span>
                <span className="badge badge-short-basket">
                  {positionStats.shortCount} 隻做空 (Short)
                </span>
              </div>
              <div className="pnl-card-value-row">
                <span className={`pnl-card-val ${positionStats.shortPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                  {positionStats.shortPnl >= 0 ? '+' : ''}{positionStats.shortPnl.toFixed(2)}
                  <span className="pnl-unit">USDT</span>
                </span>
              </div>
              <div className="pnl-card-sub">
                <span>名義曝險: <strong>${positionStats.shortNotional.toLocaleString()} USDT</strong></span>
                {positionStats.shortNotional > 0 && (
                  <span className={`pnl-yield ${positionStats.shortPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                    ({positionStats.shortPnl >= 0 ? '+' : ''}{((positionStats.shortPnl / positionStats.shortNotional) * 100).toFixed(2)}%)
                  </span>
                )}
              </div>
            </div>

            <div className="pnl-banner-card card-total">
              <div className="pnl-card-header">
                <span className="pnl-card-title">
                  <Sliders size={15} className="text-accent" />
                  <span>總未實現收益 (對沖合計)</span>
                </span>
                <span className="badge badge-accent">
                  {positions.length} 隻對沖
                </span>
              </div>
              <div className="pnl-card-value-row">
                <span className={`pnl-card-val ${positionStats.totalPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                  {positionStats.totalPnl >= 0 ? '+' : ''}{positionStats.totalPnl.toFixed(2)}
                  <span className="pnl-unit">USDT</span>
                </span>
              </div>
              <div className="pnl-card-sub">
                <span>淨敞口 Delta: <strong>{positionStats.netExposure >= 0 ? '+' : ''}${positionStats.netExposure.toFixed(1)} USDT</strong></span>
                <span className="pnl-gross">總曝險: ${positionStats.totalNotional.toLocaleString()}</span>
              </div>
            </div>
          </div>
        )}

        {positions.length === 0 ? (
          <div className="positions-empty">
            <span>
              {selectedAccount === 'paper'
                ? '📝 Paper 本地模擬盤採用無狀態撮合。切換為【Binance 合約測試網】或【Binance 合約實盤】即可讀取真實交易所持倉。'
                : `當前帳號無任何未平倉合約。下次執行換倉時，系統將全額開立 ${targetCount} 多 ${targetCount} 空共 ${targetCount * 2} 隻標的。`}
            </span>
          </div>
        ) : (
          <div className="positions-grid">
            {positions.map((pos) => {
              const inLong = rankResult?.long_list?.some(x => x.symbol === pos.symbol);
              const inShort = rankResult?.short_list?.some(x => x.symbol === pos.symbol);
              const stillInBasket = inLong || inShort;

              let statusBadge: React.ReactNode;
              if (pos.role === 'external_bot' || pos.is_protected) {
                statusBadge = (
                  <span className="status-tag tag-protected" title="此標的屬於運行中的網格或DCA機器人，CoinFull Prime 已啟用三層防護護欄，絕對不平倉、不干擾。">
                    🛡️ 外部機器人保護中
                  </span>
                );
              } else if (pos.role === 'unmanaged') {
                statusBadge = (
                  <span className="status-tag tag-neutral" title="非 CoinFull Prime 專屬籃子部位，自動隔離豁免，絕不平倉。">
                    ⚪ 外部獨立持倉 (已隔離)
                  </span>
                );
              } else if (stillInBasket) {
                statusBadge = (
                  <span className="status-tag tag-stay" title={`仍在 ${targetCount} 多 ${targetCount} 空名單中，換倉時僅微調數量差額`}>
                    🔵 專屬籃子 (留榜微調)
                  </span>
                );
              } else {
                statusBadge = (
                  <span className="status-tag tag-exit" title="已跌出名單，換倉時將市價平倉以釋放保證金">
                    🟠 專屬籃子 (退榜平倉)
                  </span>
                );
              }

              return (
                <div key={`${pos.symbol}-${pos.side}`} className={`position-chip ${pos.side === 'long' ? 'pos-long' : 'pos-short'}`}>
                  <div className="pos-chip-header">
                    <span className="pos-sym">{pos.symbol}</span>
                    <span className={`pos-side-badge ${pos.side === 'long' ? 'text-positive' : 'text-negative'}`}>
                      {pos.side === 'long' ? '🟢 做多 (Long)' : '🔴 做空 (Short)'}
                    </span>
                    {statusBadge}
                  </div>
                  <div className="pos-chip-body">
                    <div>
                      <span>持有數量: </span>
                      <strong>{pos.size}</strong>
                    </div>
                    <div>
                      <span>名義價值: </span>
                      <strong>≈ ${pos.notional_usdt.toLocaleString()} USDT</strong>
                    </div>
                    <div>
                      <span>開倉均價: </span>
                      <span>${pos.entry_price > 0 ? pos.entry_price.toFixed(4) : '—'}</span>
                    </div>
                    <div>
                      <span>未實現盈虧: </span>
                      <strong className={pos.unrealized_pnl >= 0 ? 'text-positive' : 'text-negative'}>
                        {pos.unrealized_pnl >= 0 ? '+' : ''}{pos.unrealized_pnl.toFixed(2)} USDT
                      </strong>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 換倉歷史與成交流水 (Canonical Ledger) */}
      <section className="coinfull-history-panel glass-panel" style={{ marginTop: '1.25rem' }}>
        <div className="positions-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div className="positions-title-wrap" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <History size={18} className="text-accent" />
            <span className="positions-title" style={{ fontWeight: 600, fontSize: '1rem' }}>
              調倉歷史與成交紀錄 (Canonical Ledger)
            </span>
            <span className="badge badge-neutral">
              {historyViewTab === 'batches' ? `${historyBatches.length} 次換倉紀錄` : `${tradeFills.length} 筆成交流水`}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
            {/* 視圖切換 */}
            <div className="tab-pills" style={{ display: 'flex', gap: '0.3rem', background: 'rgba(0,0,0,0.3)', padding: '0.2rem', borderRadius: '6px' }}>
              <button
                type="button"
                className={`pill-btn ${historyViewTab === 'batches' ? 'active' : ''}`}
                style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem', borderRadius: '4px' }}
                onClick={() => setHistoryViewTab('batches')}
              >
                調倉批次歷史 ({historyBatches.length})
              </button>
              <button
                type="button"
                className={`pill-btn ${historyViewTab === 'fills' ? 'active' : ''}`}
                style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem', borderRadius: '4px' }}
                onClick={() => setHistoryViewTab('fills')}
              >
                逐筆成交流水 ({tradeFills.length})
              </button>
            </div>

            {/* 帳號篩選 */}
            <select
              className="env-select"
              style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem', height: '32px' }}
              value={historyAccountFilter}
              onChange={(e) => setHistoryAccountFilter(e.target.value)}
            >
              <option value="all">🌐 全部帳號環境</option>
              <option value="binance_live">Binance 實盤</option>
              <option value="binance_testnet">Binance 測試網</option>
              <option value="paper">Paper 模擬盤</option>
            </select>

            {/* 包含模擬 checkbox */}
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.75rem', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={includeSimulated}
                onChange={(e) => setIncludeSimulated(e.target.checked)}
              />
              <span>含 Dry-Run 模擬</span>
            </label>

            <button
              type="button"
              className="btn btn-outline"
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem', height: '32px' }}
              onClick={() => void loadHistory()}
              disabled={historyLoading}
              title="重新載入帳本紀錄"
            >
              <RefreshCw size={13} className={historyLoading ? 'spin' : ''} />
              <span>{historyLoading ? '載入中…' : '刷新紀錄'}</span>
            </button>
          </div>
        </div>

        {/* 內容區塊 */}
        {historyLoading && historyBatches.length === 0 && tradeFills.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
            <RefreshCw size={24} className="spin" style={{ margin: '0 auto 0.5rem' }} />
            <div>正在讀取真實帳本歷史紀錄…</div>
          </div>
        ) : historyViewTab === 'batches' ? (
          /* 調倉批次檢視 */
          <div style={{ marginTop: '0.75rem' }}>
            {historyBatches.length === 0 ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', background: 'rgba(0,0,0,0.2)', borderRadius: '8px' }}>
                目前尚無換倉批次紀錄
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                {historyBatches.map((b) => {
                  const isExpanded = expandedBatchId === b.id;
                  const timeFormatted = b.timestamp ? new Date(b.timestamp).toLocaleString('zh-TW', { hour12: false }) : '—';
                  const closedCount = b.closed_symbols?.length || 0;
                  const openedCount = b.opened_symbols?.length || 0;
                  const totalOrders = b.orders_count || (b.orders?.length ?? 0);

                  return (
                    <div
                      key={b.id || b.timestamp}
                      style={{
                        background: 'rgba(255,255,255,0.03)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                        padding: '0.75rem',
                        transition: 'all 0.2s ease'
                      }}
                    >
                      <div
                        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', flexWrap: 'wrap', gap: '0.5rem' }}
                        onClick={() => setExpandedBatchId(isExpanded ? null : b.id)}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <span style={{ color: 'var(--text-muted)' }}>
                            {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                          </span>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600 }}>
                            {timeFormatted}
                          </span>
                          <span className={`badge ${b.dry_run ? 'badge-warning' : 'badge-success'}`}>
                            {b.dry_run ? 'Dry-Run 模擬' : '🚀 實盤已發單'}
                          </span>
                          <span className="badge badge-neutral" style={{ textTransform: 'capitalize' }}>
                            帳號: {b.account_id}
                          </span>
                          {b.capital && (
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                              名義本金: <strong>${Number(b.capital).toLocaleString()} USDT</strong> ({b.leverage || 1.0}x)
                            </span>
                          )}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', fontSize: '0.8rem' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>
                            訂單總數: <strong>{totalOrders}</strong> 筆
                            {closedCount > 0 && <span style={{ color: 'var(--trading-red)', marginLeft: '0.3rem' }}>(平倉 {closedCount})</span>}
                            {openedCount > 0 && <span style={{ color: 'var(--trading-green)', marginLeft: '0.3rem' }}>(開倉 {openedCount})</span>}
                          </span>
                          <span className="badge badge-accent" style={{ fontSize: '0.7rem' }}>
                            {isExpanded ? '收合訂單' : '展開明細'}
                          </span>
                        </div>
                      </div>

                      {/* 展開後的訂單明細 */}
                      {isExpanded && (
                        <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.5rem' }}>
                          {b.orders && b.orders.length > 0 ? (
                            <div className="table-responsive" style={{ maxHeight: '320px', overflowY: 'auto' }}>
                              <table className="coinfull-table" style={{ fontSize: '0.75rem' }}>
                                <thead>
                                  <tr>
                                    <th>標的</th>
                                    <th>方向</th>
                                    <th>操作</th>
                                    <th>數量</th>
                                    <th>預估價格</th>
                                    <th>名義價值</th>
                                    <th>原因</th>
                                    <th>執行狀態</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {b.orders.map((ord, idx) => (
                                    <tr key={`${ord.symbol}-${idx}`}>
                                      <td style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{ord.symbol}</td>
                                      <td>
                                        <span className={`badge ${ord.side === 'buy' ? 'badge-success' : 'badge-danger'}`} style={{ padding: '0.1rem 0.35rem', fontSize: '0.68rem' }}>
                                          {ord.side.toUpperCase()}
                                        </span>
                                      </td>
                                      <td>
                                        <span style={{ color: ord.reduce_only ? 'var(--trading-red)' : 'var(--trading-green)' }}>
                                          {ord.reduce_only ? '🛑 平倉' : '🟢 開倉/調倉'}
                                        </span>
                                      </td>
                                      <td style={{ fontFamily: 'var(--font-mono)' }}>{Number(ord.amount).toFixed(4)}</td>
                                      <td style={{ fontFamily: 'var(--font-mono)' }}>${Number(ord.price).toFixed(4)}</td>
                                      <td style={{ fontFamily: 'var(--font-mono)' }}>${Number(ord.notional_usdt).toFixed(2)}</td>
                                      <td style={{ color: 'var(--text-muted)' }}>{ord.reason}</td>
                                      <td>
                                        <span className={`badge ${ord.status === 'filled' ? 'badge-success' : ord.status === 'simulated' ? 'badge-neutral' : 'badge-danger'}`} style={{ fontSize: '0.68rem' }}>
                                          {ord.status === 'filled' ? '已成交' : ord.status === 'simulated' ? '模擬完成' : ord.status}
                                        </span>
                                        {ord.error && <span style={{ color: 'var(--trading-red)', marginLeft: '0.3rem' }}>({ord.error})</span>}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          ) : (
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', padding: '0.5rem' }}>
                              （該次歷史批次為早期紀錄，無個別訂單陣列；新紀錄將完整保存）
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          /* 逐筆成交流水檢視 */
          <div style={{ marginTop: '0.75rem' }}>
            {tradeFills.length === 0 ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', background: 'rgba(0,0,0,0.2)', borderRadius: '8px' }}>
                目前尚無成交流水紀錄
              </div>
            ) : (
              <div className="table-responsive" style={{ maxHeight: '420px', overflowY: 'auto' }}>
                <table className="coinfull-table" style={{ fontSize: '0.78rem' }}>
                  <thead>
                    <tr>
                      <th>時間</th>
                      <th>帳號環境</th>
                      <th>標的</th>
                      <th>方向</th>
                      <th>角色</th>
                      <th>成交價格</th>
                      <th>成交數量</th>
                      <th>名義價值</th>
                      <th>手續費</th>
                      <th>模式</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradeFills.map((f) => {
                      const timeStr = f.time ? new Date(f.time).toLocaleString('zh-TW', { hour12: false }) : '—';
                      return (
                        <tr key={f.fill_id}>
                          <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
                            {timeStr}
                          </td>
                          <td>
                            <span className="badge badge-neutral" style={{ fontSize: '0.68rem' }}>
                              {f.account_id}
                            </span>
                          </td>
                          <td style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                            {f.symbol}
                          </td>
                          <td>
                            <span className={`badge ${f.side === 'buy' ? 'badge-success' : 'badge-danger'}`} style={{ padding: '0.1rem 0.35rem', fontSize: '0.68rem' }}>
                              {f.side.toUpperCase()}
                            </span>
                          </td>
                          <td style={{ fontSize: '0.72rem' }}>
                            {f.role.includes('exit') ? (
                              <span style={{ color: 'var(--trading-red)' }}>平倉退出</span>
                            ) : (
                              <span style={{ color: 'var(--trading-green)' }}>建倉/加減</span>
                            )}
                          </td>
                          <td style={{ fontFamily: 'var(--font-mono)' }}>${Number(f.price).toFixed(4)}</td>
                          <td style={{ fontFamily: 'var(--font-mono)' }}>{Number(f.quantity).toFixed(4)}</td>
                          <td style={{ fontFamily: 'var(--font-mono)' }}>${Number(f.notional_usdt).toFixed(2)} USDT</td>
                          <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                            {f.fee > 0 ? `${f.fee} ${f.fee_currency || 'USDT'}` : '—'}
                          </td>
                          <td>
                            <span className={`badge ${f.is_simulated ? 'badge-warning' : 'badge-success'}`} style={{ fontSize: '0.65rem' }}>
                              {f.is_simulated ? '模擬成交' : '實盤成交'}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* 篩選與搜尋工具列 */}
      <section className="coinfull-toolbar glass-panel">
        <div className="tab-pills">
          <button
            type="button"
            className={`pill-btn ${filterTab === 'baskets' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('baskets')}
          >
            🌟 今日多空組合 ({((rankResult?.long_list?.length || 0) + (rankResult?.short_list?.length || 0))})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'long' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('long')}
          >
            🟢 Top {targetCount} 做多籃子 ({rankResult?.long_list?.length || 0})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'short' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('short')}
          >
            🔴 Bottom {targetCount} 做空籃子 ({rankResult?.short_list?.length || 0})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterTab === 'all' ? 'active' : ''}`}
            onClick={() => handleFilterTabChange('all')}
          >
            📋 全部 79 幣完整截面總榜 ({rankResult?.universe_size || 0})
          </button>
        </div>

        <div className="search-box">
          <Search size={16} className="search-icon" />
          <input
            type="text"
            placeholder="搜尋幣種代號 (例: BTC, SOL, DOGE)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </section>

      {/* 主表格：數值與相關參數及評價 */}
      <section className="coinfull-table-panel glass-panel">
        <div className="table-responsive">
          <table className="coinfull-table">
            <thead>
              <tr>
                <th style={{ width: '80px' }}>排名</th>
                <th style={{ width: '130px' }}>幣種 / 市價</th>
                <th style={{ width: '180px' }}>綜合評分 & 策略結論</th>
                <th>多時距動能 (60%)</th>
                <th>買賣流 (0% 停用)</th>
                <th>資金費率反向 (40%)</th>
                <th>大戶多空比 (0% 停用)</th>
                <th style={{ width: '130px' }}>目標配置金額</th>
              </tr>
            </thead>
            <tbody>
              {displayedItems.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    查無符合條件之幣種數據
                  </td>
                </tr>
              ) : (
                displayedItems.map((item) => {
                  const raw = item.raw_params;
                  const sig = item.signals;
                  const evals = item.evaluations;
                  const isLong = item.side === 'long';
                  const isShort = item.side === 'short';

                  return (
                    <tr
                      key={item.symbol}
                      className={`rank-row ${isLong ? 'row-long' : ''} ${isShort ? 'row-short' : ''}`}
                    >
                      {/* 1. 排名 */}
                      <td className="rank-cell">
                        {isLong ? (
                          <span className="rank-pill rank-long">#{item.rank} 多</span>
                        ) : isShort ? (
                          <span className="rank-pill rank-short">#{item.rank} 空</span>
                        ) : (
                          <span className="rank-pill rank-neutral">#{item.rank}</span>
                        )}
                      </td>

                      {/* 2. 幣種代號與市價 */}
                      <td className="symbol-cell">
                        <div className="symbol-name">{item.symbol}</div>
                        <div className="price-tag">
                          {raw.price > 0 ? `$${raw.price >= 1 ? raw.price.toFixed(3) : raw.price.toFixed(5)}` : '—'}
                        </div>
                      </td>

                      {/* 3. 綜合得分與評價 */}
                      <td className="score-cell">
                        <div className={`score-badge ${getScoreBadgeClass(item.score)}`}>
                          <span className="score-number">{item.score > 0 ? `+${item.score.toFixed(4)}` : item.score.toFixed(4)}</span>
                        </div>
                        <div className="verdict-label">{evals.overall_eval}</div>
                      </td>

                      {/* 4. 動能參數 (漲跌幅 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className={raw.price_change_pct >= 0 ? 'text-positive' : 'text-negative'}>
                            {raw.price_change_pct >= 0 ? `+${raw.price_change_pct.toFixed(2)}%` : `${raw.price_change_pct.toFixed(2)}%`}
                          </span>
                          <span className="eval-tag-inline">({evals.momentum_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.mom_z >= 0 ? `+${sig.mom_z.toFixed(2)}` : sig.mom_z.toFixed(2)}</div>
                      </td>

                      {/* 5. Taker 買賣流 (比例 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className="font-mono">{raw.taker_flow_ratio.toFixed(3)}</span>
                          <span className="eval-tag-inline">({evals.flow_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.flow_z >= 0 ? `+${sig.flow_z.toFixed(2)}` : sig.flow_z.toFixed(2)}</div>
                      </td>

                      {/* 6. 資金費率 (費率 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className={raw.funding_rate_pct > 0.01 ? 'text-negative font-mono' : 'text-positive font-mono'}>
                            {raw.funding_rate_pct >= 0 ? `+${raw.funding_rate_pct.toFixed(4)}%` : `${raw.funding_rate_pct.toFixed(4)}%`}
                          </span>
                          <span className="eval-tag-inline">({evals.funding_eval})</span>
                        </div>
                        <div className="zscore-sub">反向 Z: {sig.fund_z >= 0 ? `+${sig.fund_z.toFixed(2)}` : sig.fund_z.toFixed(2)}</div>
                      </td>

                      {/* 7. 大戶多空比 (比率 + 評價 + Z) */}
                      <td className="param-cell">
                        <div className="param-main-val">
                          <span className="font-mono">{raw.top_trader_ratio.toFixed(3)}</span>
                          <span className="eval-tag-inline">({evals.liquidation_eval})</span>
                        </div>
                        <div className="zscore-sub">Z-Score: {sig.liq_z >= 0 ? `+${sig.liq_z.toFixed(2)}` : sig.liq_z.toFixed(2)}</div>
                      </td>

                      {/* 8. 目標配置 */}
                      <td className="allocation-cell">
                        {(item.target_notional ?? 0) > 0 ? (
                          <>
                            <div className={`alloc-amount ${isLong ? 'text-positive' : 'text-negative'}`}>
                              ${(item.target_notional ?? 0).toFixed(1)} USDT
                            </div>
                            <div className="alloc-pct">權重: {((item.target_weight ?? 0) * 100).toFixed(1)}%</div>
                          </>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* 策略說明與底層數學原理註釋 */}
      <footer className="coinfull-footer-note glass-panel">
        <div className="note-title">
          <Info size={16} className="text-accent" />
          <span>策略指標評價與決策原則說明</span>
        </div>
        <div className="note-grid">
          <div className="note-item">
            <strong>🔥 多時距混合動能 (60%)</strong>：結合 1d (50%)、7d (30%) 與 30d (20%) 多週期日線漲跌幅。Z &gt; 1.5 判定為「動能極強」，Z &lt; -1.5 判定為「動能極弱」。
          </div>
          <div className="note-item">
            <strong>⚠️ 平滑資金費率反向 (40%)</strong>：取最近 3 次 8 小時結算費率之平滑平均反向取值。深度負費率代表貼水超賣，反向加分。
          </div>
          <div className="note-item">
            <strong>🛡️ Regime Gate 風險閘門</strong>：即時監測 BTC 50 日均線、宇宙 50 日廣度 (&gt;30%) 與全市場費率百分位 (&gt;45%)。具備 T1 停單清倉與 T2 崩跌延後防護。
          </div>
          <div className="note-item">
            <strong>⚪ 主動買賣流與大戶博弈 (0%)</strong>：v2 優化已依五年實證統計剃除無 Alpha 貢獻因子，保留欄位以相容介面。
          </div>
        </div>
      </footer>
    </div>
  );
};
