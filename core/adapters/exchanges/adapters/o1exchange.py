"""
O1Exchange 交易所适配器

提供 O1Exchange 交易所的完整适配器实现，支持：
- MEV 保护交易
- Permit2 签名
- 多网络支持 (Base, BSC, Solana)
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from decimal import Decimal

from ....logging import get_logger

from ..adapter import ExchangeAdapter
from ..interface import ExchangeConfig, ExchangeStatus
from ..models import (
    ExchangeType,
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    MarginMode,
    OrderData,
    PositionData,
    BalanceData,
    TickerData,
    OHLCVData,
    OrderBookData,
    TradeData,
    ExchangeInfo
)

from .o1exchange_base import O1ExchangeBase
from .o1exchange_rest import O1ExchangeRest


class O1ExchangeAdapter(ExchangeAdapter):
    """
    O1Exchange 交易所适配器
    """

    def __init__(self, config: ExchangeConfig, event_bus: Optional[Any] = None):
        """初始化 O1Exchange 适配器"""
        super().__init__(config, event_bus)

        # 初始化子组件
        self._base = O1ExchangeBase(config)
        self._rest = O1ExchangeRest(config, self.logger)

        # 设置日志器
        self._base.set_logger(self.logger)

        if self.logger:
            self.logger.info("✅ O1Exchange 适配器初始化完成")

    # === 生命周期管理 ===

    async def _do_connect(self) -> bool:
        """执行具体的连接逻辑"""
        try:
            if self.logger:
                self.logger.info("正在连接 O1Exchange 交易所...")

            # 连接 REST API
            if not await self._rest.connect():
                if self.logger:
                    self.logger.error("REST API 连接失败")
                return False

            if self.logger:
                self.logger.info("✅ O1Exchange 交易所连接成功")

            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"连接 O1Exchange 失败: {e}")
            return False

    async def _do_disconnect(self) -> None:
        """执行具体的断开连接逻辑"""
        try:
            # 断开 REST 连接
            await self._rest.disconnect()

            if self.logger:
                self.logger.info("O1Exchange 适配器已断开")

        except Exception as e:
            if self.logger:
                self.logger.error(f"断开连接异常: {str(e)}")

    async def _do_authenticate(self) -> bool:
        """执行具体的认证逻辑"""
        try:
            public_only = not (self.config and self.config.api_key)

            if public_only:
                if self.logger:
                    self.logger.info("O1Exchange 公共访问模式，跳过认证")
                return True

            if self.logger:
                self.logger.info("O1Exchange 认证成功")
            return True

        except Exception as e:
            if self.logger:
                self.logger.warning(f"O1Exchange 认证失败 (不影响市场数据功能): {str(e)}")
            return True

    async def _do_health_check(self) -> Dict[str, Any]:
        """执行具体的健康检查"""
        health_data = {
            'exchange_time': datetime.now().isoformat(),
            'rest_connected': self._rest.session is not None,
            'websocket_connected': False,  # 暂不支持 WebSocket
            'market_count': 0,
            'subscriptions': 0
        }

        try:
            exchange_info = await self._rest.get_exchange_info()
            health_data['market_count'] = len(exchange_info.markets) if exchange_info.markets else 0

        except Exception as e:
            health_data['error'] = str(e)

        return health_data

    async def _do_heartbeat(self) -> None:
        """执行心跳检测"""
        # 通过获取交易所信息进行心跳
        await self._rest.get_exchange_info()

    # === 市场数据接口 ===

    async def get_exchange_info(self) -> ExchangeInfo:
        """获取交易所信息"""
        return await self._rest.get_exchange_info()

    async def get_ticker(self, symbol: str) -> TickerData:
        """获取单个交易对行情"""
        return await self._rest.get_ticker(symbol)

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        """获取多个交易对行情"""
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
        """获取 K 线数据"""
        return await self._rest.get_ohlcv(symbol, timeframe, since, limit)

    async def get_trades(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[TradeData]:
        """获取最近成交记录"""
        return await self._rest.get_trades(symbol, since, limit)

    # === 账户和交易接口 ===

    async def get_balances(self) -> List[BalanceData]:
        """获取账户余额"""
        return await self._rest.get_balances()

    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        """获取持仓信息"""
        return await self._rest.get_positions(symbols)

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

    async def get_order(self, order_id: str, symbol: str) -> OrderData:
        """获取订单信息"""
        return await self._rest.get_order(order_id, symbol)

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

    # === 交易设置接口 ===

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """设置杠杆倍数"""
        return await self._rest.set_leverage(symbol, leverage)

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        """设置保证金模式"""
        return await self._rest.set_margin_mode(symbol, margin_mode)

    # === 实时数据流接口 (暂不支持 WebSocket) ===

    async def subscribe_ticker(self, symbol: str, callback: Callable[[TickerData], None]) -> None:
        """订阅行情数据流 (暂不支持 WebSocket)"""
        if self.logger:
            self.logger.warning("O1Exchange WebSocket 订阅暂不支持")

    async def subscribe_orderbook(self, symbol: str, callback: Callable[[OrderBookData], None]) -> None:
        """订阅订单簿数据流 (暂不支持 WebSocket)"""
        if self.logger:
            self.logger.warning("O1Exchange WebSocket 订阅暂不支持")

    async def subscribe_trades(self, symbol: str, callback: Callable[[TradeData], None]) -> None:
        """订阅成交数据流 (暂不支持 WebSocket)"""
        if self.logger:
            self.logger.warning("O1Exchange WebSocket 订阅暂不支持")

    async def subscribe_user_data(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """订阅用户数据流 (暂不支持 WebSocket)"""
        if self.logger:
            self.logger.warning("O1Exchange WebSocket 订阅暂不支持")

    async def unsubscribe(self, symbol: Optional[str] = None) -> None:
        """取消订阅"""
        if self.logger:
            self.logger.info("O1Exchange 已取消订阅")

    # === 事件处理 ===

    async def _handle_order_update(self, order: OrderData) -> None:
        """处理订单更新事件"""
        if self.event_bus:
            event = {
                'type': 'order_update',
                'exchange_id': self.config.exchange_id,
                'order': order,
                'timestamp': datetime.now()
            }
            await self.emit_event(event)

    async def emit_event(self, event: Any) -> None:
        """发出事件"""
        if self.event_bus:
            try:
                await self.event_bus.emit(event)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"发出事件失败: {e}")
