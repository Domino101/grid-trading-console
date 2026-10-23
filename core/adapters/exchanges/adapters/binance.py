"""
Binance交易所适配器 - 重构版本

基于MESA架构重新实现的Binance适配器，提供统一的期货交易接口。
整合了分离的模块：binance_base.py、binance_rest.py、binance_websocket.py
使用ccxt库进行API调用，支持WebSocket实时数据流。
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from decimal import Decimal

from ....logging import get_logger

from ..adapter import ExchangeAdapter
from ..interface import ExchangeConfig
from ..models import *
from ..subscription_manager import create_subscription_manager, DataType
from .binance_base import BinanceBase
from .binance_rest import BinanceRest
from .binance_websocket import BinanceWebSocket


class BinanceAdapter(ExchangeAdapter):
    """Binance交易所适配器 - 统一接口"""

    def __init__(self, config: ExchangeConfig, event_bus=None):
        super().__init__(config, event_bus)
        
        # 初始化各个模块
        self._base = BinanceBase(config)
        self._rest = BinanceRest(config, self.logger)
        # 🔥 确保WebSocket使用相同的logger
        self._websocket = BinanceWebSocket(config, self.logger)
        self._position_cache = self._websocket._position_cache
        self.testnet = getattr(config, 'testnet', False)

        # 验证logger设置
        if self.logger:
            self.logger.info(f"📝 BinanceAdapter logger: {self.logger.name}")

        # 设置基础URL
        self.base_url = getattr(config, 'base_url', None) or self._base.base_url
        self.ws_url = getattr(config, 'ws_url', None) or self._base.ws_url

    @property
    def rest(self) -> BinanceRest:
        """获取 REST 适配器"""
        return self._rest

    @property
    def websocket(self) -> BinanceWebSocket:
        """获取 WebSocket 适配器"""
        return self._websocket
        
        # 符号映射
        self._symbol_mapping = getattr(config, 'symbol_mapping', {})
        
        # 连接状态
        self._connected = False
        self._authenticated = False
        
        # 缓存支持的交易对
        self._supported_symbols = []
        self._market_info = {}
        
        # 🚀 初始化订阅管理器 - 加载Binance配置文件
        try:
            subscription_config = {
                'subscription_mode': {
                    'mode': 'predefined',
                    'predefined': {
                        'data_types': {'ticker': True, 'orderbook': True, 'trades': True},
                        'symbols': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],  # 默认符号
                        'update_interval': 1000,  # 1秒
                        'batch_subscription': {'enabled': True, 'batch_size': 10, 'delay_between_batches': 1.0}
                    },
                    'dynamic': {
                        'data_types': {'ticker': True, 'orderbook': True, 'trades': True},
                        'discovery': {'enabled': True, 'filter_criteria': {}, 'auto_discovery_interval': 600, 'max_retry_attempts': 3, 'retry_delay': 5},
                        'dynamic_subscription': {'auto_discovery_interval': 600, 'max_retry_attempts': 3, 'retry_delay': 5}
                    }
                }
            }
            self.subscription_manager = create_subscription_manager(subscription_config)
            if self.logger:
                self.logger.info("✅ Binance订阅管理器初始化成功")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"⚠️ Binance订阅管理器初始化失败: {str(e)}")
            self.subscription_manager = None

    async def _do_connect(self) -> bool:
        """连接实现"""
        try:
            is_public_only = (
                str(getattr(self.config, 'api_key', '')).lower() == "public_data_only"
                or str(getattr(self.config, 'api_secret', '')).lower() == "public_data_only"
            )
            
            # 初始化REST API
            if is_public_only:
                # 公开数据访问模式：REST初始化失败不影响WebSocket连接
                self.logger.info("📡 公开数据访问模式（无需API认证）")
                try:
                    await self._rest.initialize()
                    self._market_info = self._rest._market_info
                except Exception as e:
                    self.logger.warning(f"⚠️ REST API初始化失败（预期行为）: {e}")
                    self._market_info = {}
            else:
                # 正常模式：REST必须成功
                rest_success = await self._rest.initialize()
                if not rest_success:
                    self.logger.error("❌ Binance REST API初始化失败")
                    return False
                self._market_info = self._rest._market_info
            
            # 初始化WebSocket（公开数据不需要认证）
            ws_success = await self._websocket.initialize()
            if not ws_success:
                if is_public_only:
                    self.logger.error("❌ WebSocket初始化失败")
                    return False
                else:
                    self.logger.warning("⚠️ Binance WebSocket初始化失败，仅使用REST API")
            
            market_count = len(self._market_info)
            self.logger.info(f"✅ Binance连接成功{f'，加载 {market_count} 个市场' if market_count > 0 else ''}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Binance连接失败: {str(e)}")
            return False

    async def _do_disconnect(self) -> None:
        """断开连接实现"""
        try:
            # 关闭REST连接
            await self._rest.close()
            
            # 关闭WebSocket连接
            await self._websocket.close()
            
            self.logger.info("✅ Binance连接已断开")
            
        except Exception as e:
            self.logger.error(f"❌ 断开Binance连接失败: {str(e)}")

    async def _do_authenticate(self) -> bool:
        """认证实现"""
        try:
            # 测试API访问
            health_data = await self._rest.health_check()
            if not health_data.get('api_accessible', False):
                return False
            
            self.logger.info("✅ Binance认证成功")
            return True

        except Exception as e:
            self.logger.error(f"❌ Binance认证失败: {str(e)}")
            return False

    async def _do_health_check(self) -> Dict[str, Any]:
        """健康检查实现"""
        try:
            return await self._rest.health_check()
        except Exception as e:
            return {
                'api_accessible': False,
                'error': str(e)
            }

    async def _do_heartbeat(self) -> None:
        """心跳实现"""
        try:
            await self._rest.heartbeat()
        except Exception as e:
            self.logger.error(f"❌ Binance心跳失败: {str(e)}")

    # ==================== 市场数据接口实现 ====================

    async def get_exchange_info(self) -> ExchangeInfo:
        """获取交易所信息"""
        return await self._rest.get_exchange_info()

    async def get_ticker(self, symbol: str) -> TickerData:
        """获取行情数据"""
        return await self._rest.get_ticker(symbol)

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        """获取多个行情数据"""
        return await self._rest.get_tickers(symbols)

    async def get_orderbook(self, symbol: str, limit: Optional[int] = None) -> OrderBookData:
        """获取订单簿"""
        return await self._rest.get_orderbook(symbol, limit)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OHLCVData]:
        """获取K线数据"""
        return await self._rest.get_ohlcv(symbol, timeframe, since, limit)

    async def get_trades(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[TradeData]:
        """获取成交数据"""
        return await self._rest.get_trades(symbol, since, limit)

    # ==================== 账户接口实现 ====================

    async def get_balances(self) -> List[BalanceData]:
        """获取账户余额"""
        return await self._rest.get_balances()

    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        """获取持仓信息"""
        positions = await self._rest.get_positions(symbols)
        for pos in positions:
            self._position_cache[pos.symbol] = {
                'size': pos.size if getattr(pos.side, "value", str(pos.side)).lower() == 'long' else -pos.size,
                'entry_price': pos.entry_price or Decimal('0'),
                'unrealized_pnl': pos.unrealized_pnl or Decimal('0'),
                'timestamp': datetime.now()
            }
        return positions

    # ==================== 交易接口实现 ====================

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> OrderData:
        """创建订单"""
        order = await self._rest.create_order(symbol, side, order_type, amount, price, params)
        
        # 触发订单创建事件
        await self._handle_order_update(order)
        
        return order

    async def cancel_order(self, order_id: str, symbol: str) -> OrderData:
        """取消订单"""
        order = await self._rest.cancel_order(order_id, symbol)
        
        # 触发订单更新事件
        await self._handle_order_update(order)
        
        return order

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """取消所有订单"""
        orders = await self._rest.cancel_all_orders(symbol)
        
        # 触发订单更新事件
        for order in orders:
            await self._handle_order_update(order)
        
        return orders

    async def get_order(self, order_id: str, symbol: str, params: Optional[Dict[str, Any]] = None) -> OrderData:
        """获取订单信息"""
        return await self._rest.get_order(order_id, symbol, params)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """获取开放订单"""
        return await self._rest.get_open_orders(symbol)

    async def get_order_history(
        self,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OrderData]:
        """获取历史订单"""
        return await self._rest.get_order_history(symbol, since, limit)

    # ==================== 设置接口实现 ====================

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """设置杠杆倍数"""
        return await self._rest.set_leverage(symbol, leverage)

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        """设置保证金模式"""
        return await self._rest.set_margin_mode(symbol, margin_mode)

    # ==================== 订阅接口实现 ====================

    async def subscribe_ticker(self, symbol: str, callback: Callable[[TickerData], None]) -> None:
        """订阅行情数据流"""
        try:
            # 检查是否为公开数据访问模式
            is_public_only = self.config.api_key == "public_data_only"
            
            # 尝试通过WebSocket订阅（内部会自动连接）
            try:
                await self._websocket.subscribe_ticker(symbol, callback)
                self.logger.info(f"✅ WebSocket订阅成功: {symbol}")
            except Exception as ws_error:
                if is_public_only:
                    # 公开数据访问模式：WebSocket是唯一选项
                    self.logger.error(f"❌ WebSocket订阅失败（公开数据模式）: {symbol} - {ws_error}")
                    raise
                else:
                    # 正常模式：降级到轮询
                    self.logger.warning(f"⚠️ WebSocket订阅失败，使用轮询模式: {symbol} - {ws_error}")
                    asyncio.create_task(self._poll_ticker(symbol, callback))
        except Exception as e:
            self.logger.error(f"❌ 订阅行情失败 {symbol}: {e}")

    async def subscribe_orderbook(self, symbol: str, callback: Callable[[OrderBookData], None]) -> None:
        """订阅订单簿数据流"""
        try:
            if self._websocket.is_connected:
                await self._websocket.subscribe_orderbook(symbol, callback)
            else:
                self.logger.warning(f"⚠️ WebSocket未连接，使用轮询模式订阅订单簿 {symbol}")
                asyncio.create_task(self._poll_orderbook(symbol, callback))
        except Exception as e:
            self.logger.error(f"❌ 订阅订单簿失败 {symbol}: {e}")

    async def subscribe_trades(self, symbol: str, callback: Callable[[TradeData], None]) -> None:
        """订阅成交数据流"""
        try:
            if self._websocket.is_connected:
                await self._websocket.subscribe_trades(symbol, callback)
            else:
                self.logger.warning(f"⚠️ WebSocket未连接，使用轮询模式订阅成交 {symbol}")
                asyncio.create_task(self._poll_trades(symbol, callback))
        except Exception as e:
            self.logger.error(f"❌ 订阅成交失败 {symbol}: {e}")

    async def subscribe_user_data(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """订阅用户数据流"""
        try:
            await self._websocket.subscribe_user_data(callback)
        except Exception as e:
            self.logger.error(f"❌ 订阅用户数据流失败，退回轮询模式: {e}")
            asyncio.create_task(self._poll_user_data(callback))

    async def unsubscribe(self, symbol: Optional[str] = None) -> None:
        """取消订阅"""
        try:
            await self._websocket.unsubscribe(symbol)
            
            # 停止轮询
            if not hasattr(self, '_stop_polling'):
                self._stop_polling = set()
            
            if symbol:
                self._stop_polling.add(symbol)
            else:
                self._stop_polling.add('ALL')
                
        except Exception as e:
            self.logger.error(f"❌ 取消订阅失败: {e}")

    # ==================== 轮询模式实现 ====================

    async def _poll_ticker(self, symbol: str, callback: Callable[[TickerData], None]) -> None:
        """轮询行情数据"""
        try:
            while symbol not in getattr(self, '_stop_polling', set()) and 'ALL' not in getattr(self, '_stop_polling', set()):
                ticker = await self.get_ticker(symbol)
                await self._safe_callback(callback, ticker)
                await asyncio.sleep(1)  # 1秒轮询间隔
        except Exception as e:
            self.logger.error(f"❌ 轮询行情失败 {symbol}: {e}")

    async def _poll_orderbook(self, symbol: str, callback: Callable[[OrderBookData], None]) -> None:
        """轮询订单簿数据"""
        try:
            while symbol not in getattr(self, '_stop_polling', set()) and 'ALL' not in getattr(self, '_stop_polling', set()):
                orderbook = await self.get_orderbook(symbol)
                await self._safe_callback(callback, orderbook)
                await asyncio.sleep(0.5)  # 0.5秒轮询间隔
        except Exception as e:
            self.logger.error(f"❌ 轮询订单簿失败 {symbol}: {e}")

    async def _poll_trades(self, symbol: str, callback: Callable[[TradeData], None]) -> None:
        """轮询成交数据"""
        try:
            last_trade_id = None
            while symbol not in getattr(self, '_stop_polling', set()) and 'ALL' not in getattr(self, '_stop_polling', set()):
                trades = await self.get_trades(symbol, limit=10)
                # 只推送新的成交
                for trade in trades:
                    if last_trade_id is None or trade.id != last_trade_id:
                        await self._safe_callback(callback, trade)
                        last_trade_id = trade.id
                await asyncio.sleep(1)  # 1秒轮询间隔
        except Exception as e:
            self.logger.error(f"❌ 轮询成交失败 {symbol}: {e}")

    async def _poll_user_data(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """轮询用户数据"""
        try:
            last_balances = {}
            last_orders = {}

            while 'ALL' not in getattr(self, '_stop_polling', set()):
                # 检查余额变化
                try:
                    current_balances = await self.get_balances()
                    if str(current_balances) != str(last_balances):
                        await self._safe_callback(callback, {'type': 'balance', 'data': current_balances})
                        last_balances = current_balances
                except Exception:
                    pass

                # 检查订单变化
                try:
                    current_orders = await self.get_open_orders()
                    if str(current_orders) != str(last_orders):
                        await self._safe_callback(callback, {'type': 'orders', 'data': current_orders})
                        last_orders = current_orders
                except Exception:
                    pass

                await asyncio.sleep(2)  # 2秒轮询间隔
        except Exception as e:
            self.logger.error(f"❌ 轮询用户数据失败: {e}")

    async def _safe_callback(self, callback: Callable, data: Any) -> None:
        """安全调用回调函数"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            self.logger.error(f"❌ 回调函数执行失败: {e}")

    # ==================== 工具方法 ====================

    def _map_symbol(self, symbol: str) -> str:
        """映射交易对符号"""
        return self._base.map_symbol_to_binance(symbol)

    def _reverse_map_symbol(self, exchange_symbol: str) -> str:
        """反向映射交易对符号"""
        return self._base.map_symbol_from_binance(exchange_symbol)

    def get_cached_ticker(self, symbol: str) -> Optional[TickerData]:
        """获取缓存的行情数据"""
        return self._websocket.get_cached_ticker(symbol)

    def get_cached_orderbook(self, symbol: str) -> Optional[OrderBookData]:
        """获取缓存的订单簿数据"""
        return self._websocket.get_cached_orderbook(symbol)

    @property
    def is_websocket_connected(self) -> bool:
        """检查WebSocket连接状态"""
        return self._websocket.is_connected

    @property
    def is_user_stream_connected(self) -> bool:
        """检查用户数据流连接状态"""
        return self._websocket.is_user_connected

    @property
    def _ws_connected(self) -> bool:
        """供 grid_engine_impl 智能監控檢測用戶數據流連線狀態"""
        return self._websocket.is_user_connected

    async def force_user_stream_reconnect(self, reason: str = "") -> bool:
        """強制重建用戶數據流（殭屍連線／REST 證明漏推送時由引擎呼叫）。"""
        return await self._websocket.force_user_stream_reconnect(reason)

    @property
    def supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        if self._market_info:
            return list(self._market_info.keys())
        return []

    def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取交易对信息"""
        binance_symbol = self._map_symbol(symbol)
        return self._market_info.get(binance_symbol)

    async def _handle_order_update(self, order: OrderData):
        """处理订单更新事件"""
        try:
            if self.event_bus:
                await self.event_bus.emit('order_update', {
                    'exchange': 'binance',
                    'order': order
                })
        except Exception as e:
            self.logger.error(f"❌ 处理订单更新事件失败: {e}")

    # ==================== 高级功能 ====================

    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取资金费率（期货特有）"""
        try:
            # 这里可以实现资金费率获取逻辑
            # 暂时返回None
            return None
        except Exception as e:
            self.logger.error(f"❌ 获取资金费率失败 {symbol}: {e}")
            return None

    async def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        """获取标记价格（期货特有）"""
        try:
            # 这里可以实现标记价格获取逻辑
            # 暂时返回None
            return None
        except Exception as e:
            self.logger.error(f"❌ 获取标记价格失败 {symbol}: {e}")
            return None

    async def get_insurance_fund(self) -> Optional[Dict[str, Any]]:
        """获取保险基金信息"""
        try:
            # 这里可以实现保险基金信息获取逻辑
            # 暂时返回None
            return None
        except Exception as e:
            self.logger.error(f"❌ 获取保险基金信息失败: {e}")
            return None

    def __str__(self) -> str:
        """字符串表示"""
        return f"BinanceAdapter(connected={self._connected}, symbols={len(self.supported_symbols)})"

    def __repr__(self) -> str:
        """详细字符串表示"""
        return (f"BinanceAdapter("
                f"config={self.config.exchange_id if self.config else None}, "
                f"rest_connected={self._rest.exchange is not None}, "
                f"ws_connected={self.is_websocket_connected}, "
                f"user_stream_connected={self.is_user_stream_connected}, "
                f"markets={len(self._market_info)})")
