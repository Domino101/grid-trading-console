export type AppTab = 'dashboard' | 'market' | 'configs' | 'detail' | 'dca' | 'dca-create' | 'grid-backtest' | 'coinfull-prime' | 'cross-section';

export interface RouteState {
  tab: AppTab;
  gridId: string | null;
  dcaBotId: string | null;
}

const VALID_TABS = new Set<AppTab>(['dashboard', 'market', 'configs', 'detail', 'dca', 'dca-create', 'grid-backtest', 'coinfull-prime', 'cross-section']);

function safeDecode(val: string): string {
  try {
    return decodeURIComponent(val);
  } catch {
    return val;
  }
}

/**
 * 解析 URL Hash 為結構化的路由狀態
 * 支援以下格式：
 * - #/dashboard 或 #dashboard -> { tab: 'dashboard' }
 * - #/market 或 #market -> { tab: 'market' }
 * - #/configs 或 #configs -> { tab: 'configs' }
 * - #/dca 或 #dca -> { tab: 'dca' }
 * - #/dca?id=xxx 或 #/dca/xxx -> { tab: 'dca', dcaBotId: 'xxx' }
 * - #/dca-create 或 #dca-create -> { tab: 'dca-create' }
 * - #/cross-section 或 #cross-section -> { tab: 'cross-section' }
 * - #/coinfull-prime 或 #coinfull-prime -> { tab: 'coinfull-prime' }
 * - #/detail?id=xxx 或 #/detail/xxx 或 #/grid/xxx -> { tab: 'detail', gridId: 'xxx' }
 */
export function parseHash(rawHash: string): RouteState {
  if (!rawHash) {
    return { tab: 'dashboard', gridId: null, dcaBotId: null };
  }

  // 去除開頭的 '#' 與 '!/' 或 '/'
  let clean = rawHash.trim();
  if (clean.startsWith('#')) {
    clean = clean.slice(1);
  }
  if (clean.startsWith('!')) {
    clean = clean.slice(1);
  }
  if (clean.startsWith('/')) {
    clean = clean.slice(1);
  }

  if (!clean || clean === 'dashboard') {
    return { tab: 'dashboard', gridId: null, dcaBotId: null };
  }

  // 拆分 path 與 query string
  const [pathPart, queryPart] = clean.split('?');
  const params = new URLSearchParams(queryPart || '');

  // 0. 網格回測分頁（必須先於 startsWith('grid') 判斷，避免被當成 Grid Detail）
  if (pathPart === 'grid-backtest') {
    return { tab: 'grid-backtest', gridId: null, dcaBotId: null };
  }

  // 0.1 多空截面排行（純盤面行情觀測）
  if (pathPart === 'cross-section' || pathPart === 'cross-section-ranking') {
    return { tab: 'cross-section', gridId: null, dcaBotId: null };
  }

  // 0.2 CoinFull Prime 策略機器人分頁
  if (pathPart === 'coinfull-prime') {
    return { tab: 'coinfull-prime', gridId: null, dcaBotId: null };
  }

  // 1. Grid Detail 路由判斷
  if (pathPart.startsWith('detail') || pathPart.startsWith('grid')) {
    let gridId = params.get('id') || params.get('gridId');
    if (!gridId) {
      const parts = pathPart.split('/');
      if (parts.length > 1 && parts[1]) {
        gridId = parts[1];
      }
    }
    if (gridId) {
      return { tab: 'detail', gridId: safeDecode(gridId), dcaBotId: null };
    }
    return { tab: 'dashboard', gridId: null, dcaBotId: null };
  }

  // 2. DCA 相關路由判斷
  if (pathPart === 'dca-create') {
    return { tab: 'dca-create', gridId: null, dcaBotId: null };
  }

  if (pathPart.startsWith('dca')) {
    let dcaBotId = params.get('id') || params.get('botId');
    if (!dcaBotId) {
      const parts = pathPart.split('/');
      if (parts.length > 1 && parts[1]) {
        dcaBotId = parts[1];
      }
    }
    return {
      tab: 'dca',
      gridId: null,
      dcaBotId: dcaBotId ? safeDecode(dcaBotId) : null,
    };
  }

  // 3. 一般分頁判斷
  const primaryTab = pathPart.split('/')[0] as AppTab;
  if (VALID_TABS.has(primaryTab)) {
    return { tab: primaryTab, gridId: null, dcaBotId: null };
  }

  // 4. 未知分頁回退至 dashboard
  return { tab: 'dashboard', gridId: null, dcaBotId: null };
}

/**
 * 將路由狀態格式化為標準 URL Hash 字串
 */
export function formatHash(state: { tab: AppTab; gridId?: string | null; dcaBotId?: string | null }): string {
  switch (state.tab) {
    case 'dashboard':
      return '#/dashboard';
    case 'market':
      return '#/market';
    case 'configs':
      return '#/configs';
    case 'dca':
      return state.dcaBotId ? `#/dca?id=${encodeURIComponent(state.dcaBotId)}` : '#/dca';
    case 'dca-create':
      return '#/dca-create';
    case 'grid-backtest':
      return '#/grid-backtest';
    case 'cross-section':
      return '#/cross-section';
    case 'coinfull-prime':
      return '#/coinfull-prime';
    case 'detail':
      return state.gridId ? `#/detail?id=${encodeURIComponent(state.gridId)}` : '#/dashboard';
    default:
      return '#/dashboard';
  }
}

/**
 * 導航至指定路由
 */
export function navigate(state: { tab: AppTab; gridId?: string | null; dcaBotId?: string | null }): void {
  if (typeof window === 'undefined') return;
  const nextHash = formatHash(state);
  if (window.location.hash !== nextHash) {
    window.location.hash = nextHash;
  }
}
