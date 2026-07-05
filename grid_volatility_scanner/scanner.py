"""
网格波动率扫描器 - Grid Volatility Scanner

主扫描器逻辑：
1. 初始化交易所适配器
2. 加载市场配置
3. 创建虚拟网格
4. 监控价格并更新网格
5. 计算APR并排序
6. 实时更新UI
"""

import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import yaml

from .models.virtual_grid import VirtualGrid
from .models.simulation_result import SimulationResult
from .ui.scanner_ui import ScannerUI
from .core.apr_calculator import APRCalculator
from .core.apr_alert import APRAlertManager


logger = logging.getLogger(__name__)


class GridVolatilityScanner:
    """
    网格波动率扫描器

    功能：
    - 监控所有市场的实时价格
    - 模拟网格交易（不实际下单）
    - 统计循环次数
    - 计算预估APR
    - 生成推荐列表
    """

    def __init__(
        self,
        exchange_adapter,
        config_path: Optional[str] = None
    ):
        """
        初始化扫描器

        Args:
            exchange_adapter: 交易所适配器（如LighterAdapter）
            config_path: 配置文件路径
        """
        self.adapter = exchange_adapter
        self.config_path = config_path or self._get_default_config_path()

        # 配置数据
        self.market_configs: Dict = {}
        self.scanner_config: Dict = {}
        self.exchange_profiles: Dict = {}   # 🔥 各交易所适配配置(2026-06-27 新增)
        self.exchange_id: str = ""         # 🔥 當前交易所 id(2026-06-27 新增)
        self.default_exchange_profile: Dict = {}  # 🔥 未匹配交易所時的 fallback

        # 虚拟网格字典 {symbol: VirtualGrid}
        self.virtual_grids: Dict[str, VirtualGrid] = {}

        # UI
        self.ui: Optional[ScannerUI] = None

        # 🔔 APR报警管理器
        self.alert_manager: Optional[APRAlertManager] = None

        # 运行状态
        self._running = False
        self._scan_start_time: Optional[datetime] = None

        # 🔥 订阅统计
        self._subscribed_symbols_count = 0  # 已订阅的代币数量
        self._subscribed_symbols_list = []  # 已订阅的代币列表（保留顺序）
        self._failed_subscribe_symbols = []  # 订阅失败的代币列表
        self._received_ticker_symbols = set()  # 实际收到价格推送的代币集合
        self._no_data_symbols = []  # 订阅成功但无数据的代币列表

        logger.info("网格波动率扫描器初始化")

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径"""
        current_dir = Path(__file__).parent
        return str(current_dir / "config" / "market_config.yaml")

    async def initialize(self):
        """初始化扫描器"""
        logger.info("开始初始化扫描器...")

        # 1. 加载配置文件
        await self._load_config()

        # 2. 解析當前交易所 id 並套用對應 profile(2026-06-27 新增)
        self.exchange_id = self._resolve_exchange_id()
        profile = self.get_exchange_profile(self.exchange_id)

        # 把交易所的費率覆蓋到 scanner_config,確保 APR 計算用對的數字
        if 'fee_rate_percent' in profile:
            self.scanner_config['fee_rate_percent'] = profile['fee_rate_percent']
            logger.info(
                f"🌐 交易所 '{self.exchange_id}' 套用費率: "
                f"{profile['fee_rate_percent']}%"
            )

        # 3. 连接交易所（如果尚未连接）
        if not hasattr(self.adapter, '_connected') or not self.adapter._connected:
            logger.info("连接交易所...")
            await self.adapter.connect()

        # 4. 获取所有市场
        logger.info("获取市场列表...")
        markets = await self._get_all_markets()
        logger.info(f"获取到 {len(markets)} 个市场")

        # 5. 创建虚拟网格
        logger.info("创建虚拟网格...")
        await self._create_virtual_grids(markets)
        logger.info(f"创建了 {len(self.virtual_grids)} 个虚拟网格")

        # 6. 创建UI
        logger.info("初始化UI...")
        self.ui = ScannerUI()
        # 🔥 修复 P0-3：把 scanner_config 传给 UI 以支持 top_n_results 等配置
        self.ui.scanner_config = self.scanner_config
        self.ui.update_stats(
            total_markets=len(markets),
            active_markets=len(self.virtual_grids)
        )

        # 7. 🔔 初始化APR报警管理器
        apr_threshold = self.scanner_config.get('apr_alert_threshold', 100.0)
        max_alerts = self.scanner_config.get('apr_alert_max_count', 3)
        cooldown = self.scanner_config.get('apr_alert_cooldown_seconds', 300)
        self.alert_manager = APRAlertManager(
            apr_threshold=apr_threshold,
            max_alerts_per_symbol=max_alerts,
            alert_cooldown_seconds=cooldown
        )
        logger.info(f"✅ APR报警管理器已初始化（阈值={apr_threshold}%）")

        logger.info("✅ 扫描器初始化完成")

    async def _load_config(self):
        """加载配置文件(2026-06-27 增强:同時載入 exchanges 區塊)"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            # 提取市场配置
            self.market_configs = {
                k: v for k, v in config.items()
                if k not in ['default', 'scanner_config', 'exchanges']
            }

            # 默认配置
            self.default_config = config.get('default', {
                'grid_width_percent': 5.0,
                'grid_interval_percent': 0.5
            })

            # 扫描器配置
            self.scanner_config = config.get('scanner_config', {
                'min_24h_volume_usdc': 100000,
                'scan_duration_seconds': 3600,
                'order_value_usdc': 10,
                'fee_rate_percent': 0.004,
            })

            # 🔥 交易所适配配置 (2026-06-27 新增)
            all_profiles = config.get('exchanges', {})
            self.default_exchange_profile = all_profiles.get('default', {
                'fee_rate_percent': self.scanner_config.get('fee_rate_percent', 0.004),
                'subscription_batch_size': self.scanner_config.get('subscription_batch_size', 30),
                'subscription_batch_delay_ms': self.scanner_config.get('subscription_batch_delay_ms', 200),
                'quote_currency_aliases': ['USDT', 'USDC', 'USD'],
                'skip_symbol_prefixes': [],
            })
            # 把 default 排除,只保留具名交易所
            self.exchange_profiles = {
                k: v for k, v in all_profiles.items() if k != 'default'
            }

            logger.info(f"配置加载成功: {len(self.market_configs)} 个市场配置, "
                        f"{len(self.exchange_profiles)} 个交易所适配配置")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            raise

    def _resolve_exchange_id(self) -> str:
        """取得當前交易所 id(從 adapter 或 config 推斷,2026-06-27 新增)

        順序:
          1. adapter.exchange_id (直接屬性)
          2. adapter.config.exchange_id (ExchangeConfig 內建欄位,所有 adapter 都有)
          3. adapter.id / adapter.name
          4. scanner_config['_active_exchange_id'] (CLI 注入)
          5. 'default'
        """
        # 1. 直接屬性
        for attr in ('exchange_id', 'id', 'name'):
            val = getattr(self.adapter, attr, None)
            if isinstance(val, str) and val:
                return val.lower()

        # 2. 從 ExchangeConfig 內部欄位讀(所有標準 adapter 都有 config)
        adapter_cfg = getattr(self.adapter, 'config', None)
        if adapter_cfg is not None:
            for attr in ('exchange_id', 'id', 'name'):
                val = getattr(adapter_cfg, attr, None)
                if isinstance(val, str) and val:
                    return val.lower()

        # 3. CLI 注入
        cfg_id = self.scanner_config.get('_active_exchange_id')
        if isinstance(cfg_id, str):
            return cfg_id.lower()

        return 'default'

    def get_exchange_profile(self, exchange_id: str = None) -> Dict:
        """取得指定交易所的適配 profile,找不到時用 default (2026-06-27 新增)"""
        eid = (exchange_id or self.exchange_id or 'default').lower()
        return self.exchange_profiles.get(eid, self.default_exchange_profile)

    async def _get_all_markets(self) -> List[Dict]:
        """
        获取所有市场

        Returns:
            市场信息列表
        """
        try:
            # 获取交易所信息（返回ExchangeInfo对象）
            exchange_info = await self.adapter.get_exchange_info()

            # 从ExchangeInfo对象中提取markets字典
            markets_dict = exchange_info.markets if hasattr(
                exchange_info, 'markets') else {}

            logger.info(f"获取到 {len(markets_dict)} 个原始市场")

            # 转换为列表格式
            filtered_markets = []
            for symbol, market_info in markets_dict.items():
                # 跳过稳定币
                if symbol in ['USDT', 'USDC', 'DAI', 'BUSD', 'USDT-USD', 'USDC-USD']:
                    continue

                # 构建标准化的市场信息字典
                market_data = {
                    'symbol': symbol,
                    'info': market_info if isinstance(market_info, dict) else {}
                }

                filtered_markets.append(market_data)

            logger.info(f"过滤后保留 {len(filtered_markets)} 个市场")
            return filtered_markets

        except Exception as e:
            logger.error(f"获取市场列表失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    async def _precreate_virtual_grids(self, symbols: List[str]) -> int:
        """
        批量获取初始价格并预创建虚拟网格

        Args:
            symbols: 代币符号列表

        Returns:
            成功预创建的虚拟网格数量
        """
        precreated_count = 0

        # 批量获取ticker数据（如果交易所支持）
        try:
            # 尝试批量获取所有ticker
            tickers = await self.adapter.get_tickers(symbols)

            # 为每个成功获取价格的代币创建虚拟网格
            for ticker in tickers:
                # 🔥 修复 P1-4：统一使用 ticker.last（与 TickerData 字段一致）
                if not ticker or not ticker.last or ticker.last <= 0:
                    continue

                symbol = ticker.symbol
                if symbol in self.virtual_grids:
                    # 已经存在，跳过
                    continue

                # 创建虚拟网格
                if await self._create_single_virtual_grid(symbol, Decimal(str(ticker.last_price))):
                    precreated_count += 1

        except Exception as e:
            logger.debug(f"批量获取ticker失败，将使用懒加载模式: {e}")

        # 如果批量获取失败或部分失败，尝试逐个获取（限制并发数）
        if precreated_count < len(symbols):
            logger.info(
                f"📊 批量获取了 {precreated_count}/{len(symbols)} 个价格，尝试逐个获取剩余 {len(symbols) - precreated_count} 个代币...")

            # 分批处理，每批10个，避免过多并发请求
            batch_size = 10
            remaining_symbols = [
                s for s in symbols if s not in self.virtual_grids]

            total_batches = (len(remaining_symbols) +
                             batch_size - 1) // batch_size
            for batch_idx, i in enumerate(range(0, len(remaining_symbols), batch_size), 1):
                batch = remaining_symbols[i:i + batch_size]
                logger.debug(f"⏳ 处理第 {batch_idx}/{total_batches} 批: {batch}")

                # 并发获取这批代币的价格
                tasks = [self._try_create_virtual_grid(
                    symbol) for symbol in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 统计成功数量和失败数量
                batch_success = sum(
                    1 for success in results if success is True)
                batch_failed = len(batch) - batch_success
                precreated_count += batch_success

                logger.debug(
                    f"✅ 第 {batch_idx} 批完成: 成功{batch_success}, 失败{batch_failed}, 累计{precreated_count}/{len(symbols)}")

                # 添加小延迟，避免请求过快
                if i + batch_size < len(remaining_symbols):
                    await asyncio.sleep(0.1)

            logger.info(
                f"📊 逐个获取完成，总共预创建了 {precreated_count}/{len(symbols)} 个虚拟网格")

        return precreated_count

    async def _try_create_virtual_grid(self, symbol: str) -> bool:
        """
        尝试为单个代币创建虚拟网格

        Args:
            symbol: 代币符号

        Returns:
            是否成功创建
        """
        try:
            # 尝试获取ticker
            ticker = await self.adapter.get_ticker(symbol)
            # 🔥 修复 P1-4：统一使用 ticker.last
            if ticker and ticker.last and ticker.last > 0:
                return await self._create_single_virtual_grid(symbol, Decimal(str(ticker.last)))
        except Exception as e:
            logger.debug(f"获取 {symbol} 价格失败: {e}")

        return False

    async def _create_single_virtual_grid(self, symbol: str, current_price: Decimal) -> bool:
        """
        为单个代币创建虚拟网格

        Args:
            symbol: 代币符号
            current_price: 当前价格

        Returns:
            是否成功创建
        """
        try:
            # 提取基础符号
            base_symbol = symbol.split('-')[0] if '-' in symbol else symbol

            # 获取配置（未配置的使用默认配置）
            if base_symbol in self.market_configs:
                market_config = self.market_configs[base_symbol]
                config_type = "自定义"
            else:
                market_config = self.default_config
                config_type = "默认"

            # 创建虚拟网格
            grid = VirtualGrid(
                symbol=symbol,
                current_price=current_price,
                grid_width_percent=Decimal(
                    str(market_config['grid_width_percent'])),
                grid_interval_percent=Decimal(
                    str(market_config['grid_interval_percent']))
            )

            self.virtual_grids[symbol] = grid

            logger.debug(
                f"✅ 预创建虚拟网格: {symbol:12s} | "
                f"价格=${current_price:>12} | "
                f"配置={config_type:4s}"
            )

            return True

        except Exception as e:
            logger.debug(f"创建虚拟网格失败 {symbol}: {e}")
            return False

    async def _create_virtual_grids(self, markets: List[Dict]):
        """
        为所有市场订阅WebSocket价格流(交易所自適應版,2026-06-27 重構)

        策略:
        1. 預先過濾:跳過穩定幣 / 外匯 / 交易所 profile 中的 skip_symbol_prefixes
        2. 完全依賴 WebSocket 懶加載(預建 API 易觸發各家交易所 429)
        3. 批次大小、批次延遲均讀取 exchanges.<id>.profile,不再硬編碼

        Args:
            markets: 市场信息列表
        """
        # 🔥 取得當前交易所 profile(2026-06-27 新增)
        profile = self.get_exchange_profile()
        batch_size = profile.get(
            'subscription_batch_size',
            self.scanner_config.get('subscription_batch_size', 30)
        )
        batch_delay_ms = profile.get(
            'subscription_batch_delay_ms',
            self.scanner_config.get('subscription_batch_delay_ms', 200)
        )
        quote_aliases = set(profile.get(
            'quote_currency_aliases', ['USDT', 'USDC', 'USD']))
        skip_prefixes = tuple(profile.get('skip_symbol_prefixes', []))

        logger.info("📡 使用预创建 + WebSocket订阅模式 (交易所自適應)")
        logger.info(f"🌐 交易所 '{self.exchange_id}': "
                    f"批次={batch_size}, 延遲={batch_delay_ms}ms, "
                    f"計價別名={sorted(quote_aliases)}")
        logger.info(f"🔍 从交易所获取到 {len(markets)} 个市场")

        # 提取需要监控的符号列表
        symbols_to_monitor = []
        skipped_symbols = []

        for market in markets:
            try:
                symbol = market.get('symbol', '')
                if not symbol:
                    continue

                # 提取基础符号(如 BTC-USD → BTC)
                base_symbol = symbol.split('-')[0] if '-' in symbol else symbol
                quote_symbol = symbol.split('-')[1] if '-' in symbol else ''

                # 🔥 跳過穩定幣(基礎幣)
                stablecoin_base = ['USDT', 'USDC',
                                   'DAI', 'BUSD', 'TUSD', 'USDD']

                # 跳過條件:
                # 1. 基礎符號是穩定幣
                if base_symbol in stablecoin_base:
                    skipped_symbols.append(symbol)
                    continue

                # 2. 計價貨幣不在交易所允許的 alias 內(2026-06-27 自適應)
                #    例:BTC-USD 對 Hyperliquid 通過,BTC-JPY 對所有交易所跳過
                fiat_quotes = ['JPY', 'EUR', 'GBP', 'CAD', 'AUD', 'CHF', 'CNY']
                if quote_symbol:
                    if quote_symbol.upper() in fiat_quotes:
                        skipped_symbols.append(symbol)
                        continue
                    # 若交易所 profile 明確列出計價別名,嚴格匹配
                    if quote_aliases and quote_symbol.upper() not in quote_aliases:
                        skipped_symbols.append(symbol)
                        continue

                # 3. 交易所 profile 指定的合成代幣前綴(2026-06-27 新增)
                if skip_prefixes and base_symbol.startswith(skip_prefixes):
                    skipped_symbols.append(symbol)
                    continue

                symbols_to_monitor.append(symbol)

            except Exception as e:
                logger.error(f"处理市场 {symbol} 失败: {e}")
                continue

        logger.info(f"📊 将监控 {len(symbols_to_monitor)} 个市场")
        logger.info(f"🚫 跳过 {len(skipped_symbols)} 个稳定币/外汇/合成代币市场")

        # 🔥 调试：显示前20个监控的市场
        if len(symbols_to_monitor) > 0:
            sample_symbols = symbols_to_monitor[:20]
            logger.info(f"📋 前20个监控市场: {', '.join(sample_symbols)}")

        # 🔥 步驟1: 跳過預建,完全依賴 WebSocket 懶加載
        #   不同交易所 REST 限流各異,統一策略:不預建 → WS 收到 tick 再 lazy create
        logger.info(
            f"⚠️  跳过预创建,依赖WebSocket懒加载 {len(symbols_to_monitor)} 个虚拟网格")
        precreated_count = 0

        subscription_count = 0
        failed_count = 0

        # 🔥 構建 symbol 映射表(交易所自適應版,2026-06-27 增強)
        #   為每個監控 symbol 派生 4 種鍵,讓 ticker.symbol 用任何常見格式回推都能命中:
        #     1. 完整鍵 (e.g. "BTC-USD")
        #     2. 大寫鍵 (e.g. "BTC-USD")
        #     3. 緊湊大寫 (e.g. "BTCUSD")
        #     4. 緊湊小寫 (e.g. "btcusd")
        symbol_map = {}
        for monitor_symbol in symbols_to_monitor:
            # 建立完整格式鍵
            symbol_map[monitor_symbol] = monitor_symbol

            # 基礎符號（若存在 '-' 取前半，否則保留原始）
            base = monitor_symbol.split('-')[0] if '-' in monitor_symbol else monitor_symbol
            symbol_map[base] = monitor_symbol

            # 緊湊格式鍵（去除分隔，移除 :USDT/:USDC 後綴）
            compact_upper = monitor_symbol.upper().replace('/', '').replace('-', '')
            compact_upper = compact_upper.replace(':USDT', '').replace(':USDC', '')
            compact_lower = compact_upper.lower()
            symbol_map[compact_upper] = monitor_symbol
            symbol_map[compact_lower] = monitor_symbol

        logger.info(f"📋 构建symbol映射表，共 {len(symbol_map)} 个映射")

        # 定义统一回调（只注册一次，处理所有symbol）
        async def unified_ticker_callback(ticker):
            """
            统一回调:处理所有订阅symbol的ticker更新(交易所自適應,2026-06-27)

            不同交易所推送的 ticker.symbol 格式各異:
              - Lighter:    "BTC", "ETH"
              - Hyperliquid: "@107" (asset index,需另外映射)
              - Binance:    "BTCUSDT"
              - Backpack:   "BTC_USDC"
            symbol_map 已預建 4 種鍵做模糊匹配,這裡統一處理。

            Args:
                ticker: TickerData对象
            """
            try:
                ticker_symbol = getattr(ticker, 'symbol', None)
                if not ticker_symbol:
                    return

                # 🔥 規範化符號，提升匹配成功率
                raw = str(ticker_symbol)
                upper = raw.upper()
                compact = upper.replace('/', '').replace('-', '')
                compact = compact.replace(':USDT', '').replace(':USDC', '')

                # 優先使用原始鍵，其次大小寫、緊湊鍵
                matched_symbol = (
                    symbol_map.get(raw)
                    or symbol_map.get(upper)
                    or symbol_map.get(compact)
                    or symbol_map.get(compact.lower())
                )

                if matched_symbol:
                    await self._on_ticker_update(matched_symbol, ticker)
                # else:
                #     # 调试：记录未匹配的symbol
                #     logger.debug(f"未匹配的ticker: {ticker_symbol}")

            except Exception as e:
                logger.error(
                    f"统一回调处理失败 (ticker={getattr(ticker, 'symbol', 'unknown')}): {e}")

        # 🔥 分批訂閱(2026-06-27:批次大小/延遲由 exchanges profile 決定,不再硬編碼)
        total_batches = (len(symbols_to_monitor) + batch_size - 1) // batch_size
        batch_delay_sec = batch_delay_ms / 1000.0

        logger.info(f"📊 使用分批訂閱策略: 每批{batch_size}個, "
                    f"批次間隔 {batch_delay_ms}ms,共{total_batches}批")

        # 🔥 修复 P2-13：订阅去重，避免同一 symbol 重复订阅
        subscribed_set: set = set()
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min((batch_idx + 1) * batch_size, len(symbols_to_monitor))
            batch_symbols = symbols_to_monitor[start_idx:end_idx]

            logger.info(f"📡 正在订阅第{batch_idx + 1}/{total_batches}批: {len(batch_symbols)}个代币...")

            # 订阅本批次的所有symbol
            for idx, symbol in enumerate(batch_symbols):
                try:
                    # 🔥 修复 P2-13：去重检查，避免重复订阅
                    if symbol in subscribed_set:
                        logger.debug(f"⏭️ [{symbol}] 已在订阅列表中，跳过")
                        continue
                    subscribed_set.add(symbol)

                    # 🔥 為所有交易對註冊同一個回調（避免不同交易所的 None 行為不一致）
                    await self.adapter.subscribe_ticker(
                        symbol=symbol,
                        callback=unified_ticker_callback
                    )
                    logger.info(f"✅ {symbol} (已註冊回調)")

                    subscription_count += 1
                    # 🔥 记录成功订阅的代币
                    self._subscribed_symbols_list.append(symbol)
                    logger.debug(f"📡 订阅成功: {symbol} (#{subscription_count})")

                    # 每10个订阅添加极小延迟,避免消息发送过快
                    if subscription_count % 10 == 0:
                        await asyncio.sleep(0.01)  # 10ms延迟

                except Exception as e:
                    failed_count += 1
                    # 🔥 记录订阅失败的代币和原因
                    self._failed_subscribe_symbols.append((symbol, str(e)))
                    logger.error(f"❌ 订阅失败: {symbol} | 原因: {e}")
                    import traceback
                    logger.debug(f"   详细错误:\n{traceback.format_exc()}")
                    continue

            # 🔥 每批之間延遲由交易所 profile 決定(2026-06-27)
            if batch_idx < total_batches - 1:
                logger.debug(f"⏸️  第{batch_idx + 1}批完成，等待{batch_delay_ms}ms後繼續...")
                await asyncio.sleep(batch_delay_sec)

            logger.info(f"✅ 第{batch_idx + 1}/{total_batches}批訂閱完成: 已訂閱{subscription_count}個，失敗{failed_count}個")

        # 🔥 记录订阅数量
        self._subscribed_symbols_count = subscription_count
        
        logger.info("=" * 80)
        logger.info("📊 订阅完成统计")
        logger.info("=" * 80)
        logger.info(f"✅ 订阅成功: {subscription_count} 个")
        logger.info(f"❌ 订阅失败: {failed_count} 个")
        logger.info(f"📡 总计尝试: {subscription_count + failed_count} 个")
        
        # 如果有订阅失败的代币，立即输出列表
        if self._failed_subscribe_symbols:
            logger.warning("=" * 80)
            logger.warning(f"⚠️  订阅失败的代币列表 ({len(self._failed_subscribe_symbols)}个):")
            logger.warning("=" * 80)
            for idx, (symbol, error) in enumerate(self._failed_subscribe_symbols, 1):
                logger.warning(f"  {idx}. {symbol} | 原因: {error}")
            logger.warning("=" * 80)
        
        logger.info(f"⏳ 等待价格推送并动态创建虚拟网格...")
        logger.info(f"💡 提示: '{self.exchange_id}' 交易所可能仅推送有交易活动的市场")
        logger.info(f"   如果显示的代币数<{subscription_count}，说明部分市场暂时无交易活动")
        logger.info(f"📊 将在运行5分钟后生成详细的订阅统计报告")
        logger.info("=" * 80)

    async def _on_ticker_update(self, symbol: str, ticker):
        """
        处理WebSocket ticker更新（懒加载虚拟网格）

        Args:
            symbol: 交易对符号
            ticker: TickerData对象，包含价格等信息
        """
        try:
            # 提取价格
            if not ticker or not ticker.last or ticker.last <= 0:
                logger.warning(
                    f"⚠️ [{symbol}] ticker 数据无效 (last={getattr(ticker, 'last', None)})，跳过"
                )
                return

            current_price = Decimal(str(ticker.last))

            # 🔥 写入 24h 成交量 / 价格变动（修复 P0-1：永远为 0 的问题）
            # TickerData 字段：quote_volume（USDC 计价的 24h 成交额）、percentage（24h 涨跌幅 %）
            quote_volume = getattr(ticker, 'quote_volume', None)
            percentage = getattr(ticker, 'percentage', None)

            # 🔥 记录收到价格推送的代币（用于统计）
            if symbol not in self._received_ticker_symbols:
                self._received_ticker_symbols.add(symbol)
                # 首次收到时记录日志
                logger.debug(f"📡 首次收到价格推送: {symbol} = ${current_price}")

            # 如果虚拟网格尚未创建，现在创建它（使用统一的方法）
            if symbol not in self.virtual_grids:
                # 🔥 预过滤：24h 成交量低于阈值不创建（修复 P0-2）
                min_volume = self.scanner_config.get('min_24h_volume_usdc', 0)
                if quote_volume is not None and Decimal(str(quote_volume)) < Decimal(str(min_volume)):
                    logger.debug(
                        f"🚫 [{symbol}] 24h 成交量 ${quote_volume} 低于阈值 ${min_volume}，跳过创建"
                    )
                    return

                # 🔥 使用统一的方法创建虚拟网格
                if await self._create_single_virtual_grid(symbol, current_price):
                    # 写入 24h 数据到 grid
                    grid = self.virtual_grids[symbol]
                    if quote_volume is not None:
                        grid.volume_24h_usdc = Decimal(str(quote_volume))
                    if percentage is not None:
                        grid.price_change_24h_percent = Decimal(str(percentage))

                    # 日志：显示配置类型和参数
                    base_symbol = symbol.split(
                        '-')[0] if '-' in symbol else symbol
                    if base_symbol in self.market_configs:
                        config_type = "自定义"
                        market_config = self.market_configs[base_symbol]
                    else:
                        config_type = "默认"
                        market_config = self.default_config

                    logger.info(
                        f"🎯 WebSocket创建虚拟网格: {symbol:12s} | "
                        f"价格=${current_price:>12} | "
                        f"配置={config_type:4s} | "
                        f"宽度={market_config['grid_width_percent']:>4}% | "
                        f"间距={market_config['grid_interval_percent']:>4}% | "
                        f"网格数={grid.grid_count:>2} | "
                        f"24h量=${grid.volume_24h_usdc:,.0f} | "
                        f"24h变动={grid.price_change_24h_percent:+.2f}%"
                    )

                    # 更新UI统计（total_markets = 已创建的虚拟网格数）
                    if self.ui:
                        self.ui.update_stats(
                            total_markets=len(self.virtual_grids),
                            active_markets=len(self.virtual_grids)
                        )
            else:
                # 已存在 → 持续更新 24h 数据（让 UI 反映最新行情）
                grid = self.virtual_grids[symbol]
                if quote_volume is not None:
                    grid.volume_24h_usdc = Decimal(str(quote_volume))
                if percentage is not None:
                    grid.price_change_24h_percent = Decimal(str(percentage))

            # 调用原有的价格更新处理
            await self._price_update_callback(symbol, current_price)

        except Exception as e:
            logger.error(f"处理ticker更新失败 {symbol}: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    async def _price_update_callback(self, symbol: str, price: Decimal):
        """
        价格更新回调

        Args:
            symbol: 交易对符号
            price: 新价格
        """
        if symbol not in self.virtual_grids:
            return

        grid = self.virtual_grids[symbol]

        # 更新价格并检测穿越
        cross_direction = grid.update_price(price)

        if cross_direction:
            # 计算APR(15分钟滚动窗口,与 config 一致)
            grid.calculate_apr(
                order_value_usdc=Decimal(
                    str(self.scanner_config['order_value_usdc'])),
                fee_rate_percent=Decimal(
                    str(self.scanner_config['fee_rate_percent'])),
                time_window_minutes=self.scanner_config.get(
                    'apr_time_window_minutes', 15)
            )

            # 🔔 检查APR是否超过阈值并触发报警
            if self.alert_manager and grid.estimated_apr > 0:
                self.alert_manager.check_and_alert(symbol, grid.estimated_apr)

            # 记录穿越日志
            logger.debug(
                f"{symbol} {cross_direction} 穿越: "
                f"价格=${price:.2f}, "
                f"循环={grid.complete_cycles}, "
                f"APR={grid.estimated_apr:.2f}%"
            )

    async def _monitor_prices(self):
        """
        监控价格更新

        使用轮询方式获取价格（简化实现）
        实际应该集成WebSocket实时推送
        """
        logger.info("开始价格监控（轮询模式）...")

        update_interval = 1  # 每秒更新一次

        while self._running:
            try:
                # 批量获取所有市场的价格
                for symbol in list(self.virtual_grids.keys()):
                    try:
                        ticker = await self.adapter.get_ticker(symbol)
                        # 🔥 修复 P1-4：统一使用 ticker.last
                        if ticker and ticker.last:
                            price = Decimal(str(ticker.last))
                            await self._price_update_callback(symbol, price)
                    except Exception as e:
                        logger.warning(f"更新 {symbol} 价格失败: {e}")

                await asyncio.sleep(update_interval)

            except Exception as e:
                logger.error(f"价格监控循环错误: {e}")
                await asyncio.sleep(5)

    async def _generate_subscription_report(self):
        """
        生成详细的订阅统计报告
        
        包括：
        1. 订阅失败的代币
        2. 订阅成功但无数据的代币
        3. 成功接收数据的代币
        """
        received_count = len(self._received_ticker_symbols)
        subscribed_count = self._subscribed_symbols_count
        failed_count = len(self._failed_subscribe_symbols)
        
        # 计算订阅成功但未收到数据的代币
        subscribed_set = set(self._subscribed_symbols_list)
        no_data_symbols = subscribed_set - self._received_ticker_symbols
        self._no_data_symbols = sorted(no_data_symbols)
        
        # 生成控制台报告
        logger.info("=" * 80)
        logger.info("📊 订阅统计报告（运行5分钟）")
        logger.info("=" * 80)
        logger.info(f"📡 尝试订阅的代币总数: {subscribed_count + failed_count}")
        logger.info(f"✅ 订阅成功: {subscribed_count}")
        logger.info(f"❌ 订阅失败: {failed_count}")
        logger.info(f"📈 收到价格推送: {received_count} ({received_count/subscribed_count*100 if subscribed_count > 0 else 0:.1f}%)")
        logger.info(f"🚫 订阅成功但无数据: {len(no_data_symbols)} ({len(no_data_symbols)/subscribed_count*100 if subscribed_count > 0 else 0:.1f}%)")
        logger.info("=" * 80)
        
        # 写入详细报告到日志文件
        from pathlib import Path
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_file = log_dir / f"subscription_report_{timestamp}.log"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("📊 网格波动率扫描器 - 订阅统计详细报告\n")
            f.write("=" * 80 + "\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"运行时长: 5分钟\n\n")
            
            # 1. 总览
            f.write("【总览】\n")
            f.write(f"  尝试订阅: {subscribed_count + failed_count} 个代币\n")
            f.write(f"  订阅成功: {subscribed_count} 个\n")
            f.write(f"  订阅失败: {failed_count} 个\n")
            f.write(f"  收到数据: {received_count} 个 ({received_count/subscribed_count*100 if subscribed_count > 0 else 0:.1f}%)\n")
            f.write(f"  无数据推送: {len(no_data_symbols)} 个 ({len(no_data_symbols)/subscribed_count*100 if subscribed_count > 0 else 0:.1f}%)\n\n")
            
            # 2. 订阅失败的代币
            if self._failed_subscribe_symbols:
                f.write("=" * 80 + "\n")
                f.write(f"【订阅失败】共 {len(self._failed_subscribe_symbols)} 个代币\n")
                f.write("=" * 80 + "\n")
                for idx, (symbol, error) in enumerate(self._failed_subscribe_symbols, 1):
                    f.write(f"{idx}. {symbol}\n")
                    f.write(f"   原因: {error}\n\n")
            else:
                f.write("【订阅失败】无\n\n")
            
            # 3. 订阅成功但无数据的代币
            if no_data_symbols:
                f.write("=" * 80 + "\n")
                f.write(f"【订阅成功但无数据】共 {len(no_data_symbols)} 个代币\n")
                f.write("=" * 80 + "\n")
                f.write("说明: 这些代币订阅成功，但5分钟内未收到价格推送\n")
                f.write("可能原因:\n")
                f.write("  1. 交易活动极低，暂时无价格更新\n")
                f.write("  2. 市场已下架或停止交易\n")
                f.write("  3. WebSocket订阅消息未生效（需要重启扫描器）\n\n")
                
                # 按字母顺序排序并分组显示
                sorted_symbols = sorted(no_data_symbols)
                for idx, symbol in enumerate(sorted_symbols, 1):
                    f.write(f"{idx}. {symbol}\n")
                f.write("\n")
            else:
                f.write("【订阅成功但无数据】无\n\n")
            
            # 4. 成功接收数据的代币
            f.write("=" * 80 + "\n")
            f.write(f"【成功接收数据】共 {received_count} 个代币\n")
            f.write("=" * 80 + "\n")
            sorted_received = sorted(self._received_ticker_symbols)
            for idx, symbol in enumerate(sorted_received, 1):
                # 获取该代币的虚拟网格信息
                if symbol in self.virtual_grids:
                    grid = self.virtual_grids[symbol]
                    f.write(f"{idx}. {symbol:15s} | 循环: {grid.complete_cycles:3d} | APR: {grid.estimated_apr:7.2f}%\n")
                else:
                    f.write(f"{idx}. {symbol}\n")
            f.write("\n")
            
            f.write("=" * 80 + "\n")
            f.write("报告结束\n")
            f.write("=" * 80 + "\n")
        
        logger.info(f"📄 详细报告已保存: {report_file}")
        logger.info(f"💡 提示: 如果无数据代币过多（>50%），建议重启扫描器")
        logger.info("=" * 80)

    async def _update_ui_loop(self):
        """UI更新循环"""
        logger.info("开始UI更新循环...")
        
        # 🔥 订阅统计标志（只显示一次）
        subscription_stats_logged = False

        while self._running:
            try:
                # 🔥 定期重新计算所有网格的APR（即使没有新穿越）
                # 这样可以：
                # 1. 清理过期的循环事件（超过5分钟窗口）
                # 2. 更新cycles_per_hour为最新的5分钟数据
                # 3. 即使代币暂时不波动，也能反映实时状态
                for symbol, grid in self.virtual_grids.items():
                    grid.calculate_apr(
                        order_value_usdc=Decimal(
                            str(self.scanner_config['order_value_usdc'])),
                        fee_rate_percent=Decimal(
                            str(self.scanner_config['fee_rate_percent'])),
                        time_window_minutes=self.scanner_config.get(
                            'apr_time_window_minutes', 5)
                    )

                    # 🔔 检查APR是否超过阈值并触发报警
                    if self.alert_manager and grid.estimated_apr > 0:
                        self.alert_manager.check_and_alert(
                            symbol, grid.estimated_apr)

                # 收集所有结果
                results = []
                min_cycles = self.scanner_config.get(
                    'min_cycles_to_display', 0)
                for grid in self.virtual_grids.values():
                    # 🔥 根据配置决定是否显示：
                    # - min_cycles_to_display=0: 显示所有虚拟网格（包括循环为0的）
                    # - min_cycles_to_display>0: 只显示循环次数>=min_cycles的，但BTC例外（即使循环为0也显示）
                    symbol_upper = grid.symbol.upper()
                    is_btc = 'BTC' in symbol_upper and not any(
                        x in symbol_upper for x in ['WBTC', 'TBTC', 'RBTC'])

                    if min_cycles == 0 or grid.complete_cycles >= min_cycles or is_btc:
                        # 2026-06-27: 傳入 order_value_usdc,讓評分可計算資金占用
                        order_value = Decimal(
                            str(self.scanner_config.get('order_value_usdc', 10)))
                        result = SimulationResult.from_virtual_grid(
                            grid, order_value_usdc=order_value)
                        results.append(result)

                # 更新UI
                if self.ui:
                    self.ui.update_results(results)
                    self.ui.update_stats(
                        total_markets=len(self.virtual_grids),
                        active_markets=len(
                            [g for g in self.virtual_grids.values() if g.complete_cycles > 0])
                    )
                
                # 🔥 在运行5分钟后显示订阅统计（只显示一次）
                if not subscription_stats_logged and self._scan_start_time:
                    elapsed_seconds = (datetime.now() - self._scan_start_time).total_seconds()
                    if elapsed_seconds >= 300:  # 5分钟
                        # 生成详细统计报告
                        await self._generate_subscription_report()
                        subscription_stats_logged = True

                await asyncio.sleep(0.5)  # 每0.5秒更新一次UI

            except Exception as e:
                logger.error(f"UI更新循环错误: {e}")
                await asyncio.sleep(1)

    async def scan(self, duration_seconds: Optional[int] = None, no_ui: bool = False):
        """
        开始扫描

        Args:
            duration_seconds: 扫描时长（秒），None表示持续运行直到用户中断
            no_ui: 是否禁用终端 UI(背景模式,結果只輸出到日誌/stdout)
        """
        self._running = True
        self._scan_start_time = datetime.now()

        if duration_seconds is None:
            logger.info("🎯 开始持续监控模式（按 Ctrl+C 停止）")
            logger.info("📊 APR计算: 实时滚动计算过去5分钟的数据")
        else:
            logger.info(f"🎯 开始定时扫描模式，运行 {duration_seconds} 秒")

        try:
            # 启动UI更新任务(背景模式下也會跑,用於週期性結果輸出與APR計算)
            ui_update_task = asyncio.create_task(self._update_ui_loop())

            # 🔥 背景模式:不啟動 rich Live UI(PM2 無 TTY 會 crash),
            #              改為週期性 print_summary() + 訂閱統計
            if no_ui:
                logger.info("🖥️ 背景模式啟動(--no-ui),週期性輸出結果到 stdout")
                # 啟動週期性 print_summary 協程
                summary_task = asyncio.create_task(self._background_summary_loop())

                # 阻塞直到超時或中斷
                if duration_seconds is None:
                    while self._running:
                        await asyncio.sleep(1)
                else:
                    # 用 sleep + 提前結束 的方式支援中斷
                    slept = 0
                    while self._running and slept < duration_seconds:
                        await asyncio.sleep(1)
                        slept += 1

                summary_task.cancel()
                try:
                    await summary_task
                except asyncio.CancelledError:
                    pass
            elif self.ui:
                # 一般模式:啟動 rich Live UI
                if duration_seconds is None:
                    # 持续运行模式：传递None，UI会持续显示直到Ctrl+C
                    await self.ui.run(scan_duration=None)
                else:
                    # 定时模式：传递具体时长
                    await self.ui.run(scan_duration=duration_seconds)
            else:
                if duration_seconds is None:
                    # 没有UI时，持续运行直到被中断
                    while self._running:
                        await asyncio.sleep(1)
                else:
                    await asyncio.sleep(duration_seconds)

            # 停止任务
            self._running = False
            ui_update_task.cancel()

            # 等待任务完成
            try:
                await ui_update_task
            except asyncio.CancelledError:
                pass

            logger.info("扫描完成")

        except KeyboardInterrupt:
            logger.info("用户中断扫描")
            self._running = False
        except Exception as e:
            logger.error(f"扫描过程错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            await self.cleanup()

    async def _background_summary_loop(self):
        """背景模式下的週期性摘要輸出(每 60 秒一次)"""
        try:
            while self._running:
                await asyncio.sleep(60)
                if not self._running:
                    break
                try:
                    self.print_summary()
                except Exception as e:
                    logger.error(f"背景摘要輸出失敗: {e}")
        except asyncio.CancelledError:
            pass

    async def cleanup(self):
        """清理资源"""
        logger.info("清理资源...")

        try:
            # 断开交易所连接
            if self.adapter:
                await self.adapter.disconnect()

            # 停止UI
            if self.ui:
                self.ui.stop()

            logger.info("资源清理完成")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")

    def get_results(self) -> List[SimulationResult]:
        """
        获取扫描结果

        Returns:
            按APR排序的模拟结果列表（BTC永远排第一）
        """
        results = []
        min_cycles = self.scanner_config.get('min_cycles_to_display', 0)
        for grid in self.virtual_grids.values():
            # 🔥 根据配置决定是否显示：
            # - min_cycles_to_display=0: 显示所有虚拟网格（包括循环为0的）
            # - min_cycles_to_display>0: 只显示循环次数>=min_cycles的，但BTC例外（即使循环为0也显示）
            symbol_upper = grid.symbol.upper()
            is_btc = 'BTC' in symbol_upper and not any(
                x in symbol_upper for x in ['WBTC', 'TBTC', 'RBTC'])

            if min_cycles == 0 or grid.complete_cycles >= min_cycles or is_btc:
                # 2026-06-27: 傳入 order_value_usdc,讓評分可計算資金占用
                order_value = Decimal(
                    str(self.scanner_config.get('order_value_usdc', 10)))
                result = SimulationResult.from_virtual_grid(
                    grid, order_value_usdc=order_value)
                results.append(result)

        # 🔥 自定义排序：BTC永远第一，其他按APR排序
        def sort_key(result):
            # 检查是否为BTC（匹配 BTC, BTC-USD, BTCUSDT 等）
            symbol_upper = result.symbol.upper()
            is_btc = 'BTC' in symbol_upper and not any(
                x in symbol_upper for x in ['WBTC', 'TBTC', 'RBTC'])

            if is_btc:
                # BTC返回极高值，确保排第一
                return (float('inf'), float(result.estimated_apr))
            else:
                # 其他代币按APR排序
                return (0, float(result.estimated_apr))

        results.sort(key=sort_key, reverse=True)

        return results

    def print_summary(self):
        """打印扫描摘要"""
        results = self.get_results()

        if not results:
            print("\n⚠️ 没有有效结果")
            return

        print("\n" + "="*80)
        print("📊 扫描结果摘要")
        print("="*80)
        print(f"监控市场数: {len(self.virtual_grids)}")
        print(f"有效结果数: {len(results)}")

        # 显示Top 10
        print("\n🏆 Top 10 推荐:")
        print("-"*80)
        for i, result in enumerate(results[:10], 1):
            print(
                f"{i:2d}. {result.symbol:<12} "
                f"APR: {result.estimated_apr:>8.2f}%  "
                f"循环: {result.complete_cycles:>4}次  "
                f"评级: {result.rating}"
            )

        print("="*80)
