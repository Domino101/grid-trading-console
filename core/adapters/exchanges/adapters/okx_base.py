"""
OKX交易所基础模块 - 重构版

包含OKX交易所的基础配置、数据解析等公共功能
重构：遵循MESA架构，简化符号映射，支持统一符号转换服务
"""

import time
import decimal
from typing import Dict, List, Optional, Any, Union
from decimal import Decimal
from datetime import datetime
from enum import Enum

from ..models import (
    TickerData, OrderBookData, TradeData, BalanceData, OrderData, 
    OrderSide, OrderType, OrderStatus, PositionData, PositionSide,
    MarginMode, OrderBookLevel, ExchangeInfo, ExchangeType
)


class OKXMarketType(Enum):
    """OKX市场类型"""
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    SWAP = "SWAP"          # 永续合约
    FUTURES = "FUTURES"    # 交割合约
    OPTION = "OPTION"      # 期权


class OKXInstType(Enum):
    """OKX合约类型"""
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    SWAP = "SWAP"
    FUTURES = "FUTURES"
    OPTION = "OPTION"


class OKXSymbolInfo:
    """OKX交易对信息类"""
    
    def __init__(self, symbol: str, base: str, quote: str, inst_type: str = "SWAP"):
        self.symbol = symbol
        self.base = base
        self.quote = quote
        self.inst_type = inst_type
        self.min_qty = Decimal('0.001')
        self.max_qty = Decimal('1000000')
        self.min_price = Decimal('0.01')
        self.max_price = Decimal('1000000')
        self.price_precision = 8
        self.qty_precision = 8
        self.step_size = Decimal('0.001')
        self.tick_size = Decimal('0.01')


class OKXBase:
    """OKX基础功能类 - 重构版"""
    
    # 默认配置
    DEFAULT_BASE_URL = "https://www.okx.com"  # 生产环境
    DEFAULT_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"  # 公共数据流
    DEFAULT_PRIVATE_WS_URL = "wss://ws.okx.com:8443/ws/v5/private"  # 私有数据流
    
    # 测试网地址
    TESTNET_BASE_URL = "https://www.okx.com"  # OKX没有独立测试网，使用模拟交易
    TESTNET_WS_URL = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
    TESTNET_PRIVATE_WS_URL = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
    
    # 支持的时间周期
    SUPPORTED_TIMEFRAMES = {
        '1m': '1m',
        '3m': '3m',
        '5m': '5m',
        '15m': '15m',
        '30m': '30m',
        '1h': '1H',
        '2h': '2H',
        '4h': '4H',
        '6h': '6H',
        '12h': '12H',
        '1d': '1D',
        '1w': '1W',
        '1M': '1M',
        '3M': '3M'
    }
    
    # 订单类型映射
    ORDER_TYPE_MAPPING = {
        OrderType.MARKET: 'market',
        OrderType.LIMIT: 'limit',
        OrderType.POST_ONLY: 'post_only',
        OrderType.FOK: 'fok',
        OrderType.IOC: 'ioc',
        OrderType.STOP_LIMIT: 'conditional',  # OKX的条件单
        OrderType.TAKE_PROFIT_LIMIT: 'oco'     # OKX的OCO订单
    }
    
    # 订单状态映射
    ORDER_STATUS_MAPPING = {
        'live': OrderStatus.OPEN,
        'partially_filled': OrderStatus.OPEN,  # 部分成交仍然是OPEN状态
        'filled': OrderStatus.FILLED,
        'canceled': OrderStatus.CANCELED,
        'mmp_canceled': OrderStatus.CANCELED,  # 做市商保护取消
        'expired': OrderStatus.EXPIRED
    }
    
    # 订单方向映射
    ORDER_SIDE_MAPPING = {
        OrderSide.BUY: 'buy',
        OrderSide.SELL: 'sell'
    }
    
    # 持仓方向映射
    POSITION_SIDE_MAPPING = {
        'long': PositionSide.LONG,
        'short': PositionSide.SHORT,
        'net': PositionSide.BOTH
    }
    
    def __init__(self, config=None):
        self.config = config
        self.logger = None
        
        # 基础配置
        self._setup_urls()
        
        # 市场类型配置
        self.inst_type = getattr(config, 'inst_type', OKXInstType.SWAP.value)
        self.testnet = getattr(config, 'testnet', False)
        
        # 🔥 重构：简化符号映射
        self._setup_legacy_symbol_mapping()
        
        # 支持的交易对和映射
        self._supported_symbols = []
        self._market_info = {}
        
        # CCXT兼容配置
        self.ccxt_config = self._setup_ccxt_config()
        
    def _setup_urls(self):
        """设置API URL"""
        if self.config:
            # 优先使用配置中的URL
            self.base_url = getattr(self.config, 'base_url', None)
            self.ws_url = getattr(self.config, 'ws_url', None)
            self.private_ws_url = getattr(self.config, 'private_ws_url', None)
            
            # 根据测试网配置调整
            if getattr(self.config, 'testnet', False):
                self.base_url = self.base_url or self.TESTNET_BASE_URL
                self.ws_url = self.ws_url or self.TESTNET_WS_URL
                self.private_ws_url = self.private_ws_url or self.TESTNET_PRIVATE_WS_URL
            else:
                self.base_url = self.base_url or self.DEFAULT_BASE_URL
                self.ws_url = self.ws_url or self.DEFAULT_WS_URL
                self.private_ws_url = self.private_ws_url or self.DEFAULT_PRIVATE_WS_URL
        else:
            self.base_url = self.DEFAULT_BASE_URL
            self.ws_url = self.DEFAULT_WS_URL
            self.private_ws_url = self.DEFAULT_PRIVATE_WS_URL
    
    def _setup_legacy_symbol_mapping(self):
        """
        设置遗留符号映射（已弃用）
        
        @deprecated: 建议使用统一的符号转换服务
        """
        # 默认符号映射（通用格式 -> OKX格式）
        self._symbol_mapping = {
            "BTC/USDC:PERP": "BTC-USDT-SWAP",
            "ETH/USDC:PERP": "ETH-USDT-SWAP", 
            "SOL/USDC:PERP": "SOL-USDT-SWAP",
            "AVAX/USDC:PERP": "AVAX-USDT-SWAP"
        }
        
        # 只保留配置中明确定义的映射
        if self.config and hasattr(self.config, 'symbol_mapping') and self.config.symbol_mapping:
            self._symbol_mapping.update(self.config.symbol_mapping)
    
    def _setup_ccxt_config(self) -> Dict[str, Any]:
        """设置CCXT配置"""
        config = {
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # 默认使用永续合约
                'adjustForTimeDifference': True
            }
        }
        
        if self.config:
            config['apiKey'] = getattr(self.config, 'api_key', '')
            config['secret'] = getattr(self.config, 'api_secret', '')
            config['password'] = getattr(self.config, 'api_passphrase', '') or getattr(self.config, 'passphrase', '')  # OKX需要passphrase
            
            if self.testnet:
                config['sandbox'] = True
                config['urls'] = {'api': self.base_url}
        
        return config
    
    def set_logger(self, logger):
        """设置日志器"""
        self.logger = logger
    
    def _normalize_okx_symbol(self, symbol: str) -> str:
        """标准化OKX符号格式"""
        # OKX符号格式通常是 BTC-USDT-SWAP, ETH-USDT-SWAP 等
        return symbol.upper()
    
    def map_symbol_to_okx(self, symbol: str) -> str:
        """映射通用符号到OKX格式"""
        return self._symbol_mapping.get(symbol, symbol)
    
    def map_symbol_from_okx(self, okx_symbol: str) -> str:
        """反向映射OKX符号到通用格式"""
        reverse_mapping = {v: k for k, v in self._symbol_mapping.items()}
        return reverse_mapping.get(okx_symbol, okx_symbol)
    
    def _safe_decimal(self, value: Any) -> Optional[Decimal]:
        """安全转换为Decimal类型"""
        if value is None:
            return None
        try:
            if isinstance(value, str) and value == '':
                return None
            return Decimal(str(value))
        except (decimal.InvalidOperation, ValueError, TypeError):
            if self.logger:
                self.logger.warning(f"无法转换为Decimal: {value}")
            return None
    
    def _safe_int(self, value: Any) -> Optional[int]:
        """安全转换为int类型"""
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            if self.logger:
                self.logger.warning(f"无法转换为int: {value}")
            return None
    
    def _safe_float(self, value: Any) -> Optional[float]:
        """安全转换为float类型"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            if self.logger:
                self.logger.warning(f"无法转换为float: {value}")
            return None
    
    def parse_ticker(self, ticker_data: Dict[str, Any], symbol: str) -> TickerData:
        """解析行情数据"""
        return TickerData(
            symbol=symbol,
            bid=self._safe_decimal(ticker_data.get('bidPx')),
            ask=self._safe_decimal(ticker_data.get('askPx')),
            last=self._safe_decimal(ticker_data.get('last')),
            open=self._safe_decimal(ticker_data.get('open24h')),
            high=self._safe_decimal(ticker_data.get('high24h')),
            low=self._safe_decimal(ticker_data.get('low24h')),
            close=self._safe_decimal(ticker_data.get('last')),
            volume=self._safe_decimal(ticker_data.get('vol24h')),
            quote_volume=self._safe_decimal(ticker_data.get('volCcy24h')),
            change=None,  # 需要计算
            percentage=None,  # 需要计算
            timestamp=datetime.fromtimestamp(int(ticker_data.get('ts', 0)) / 1000),
            raw_data=ticker_data
        )
    
    def parse_orderbook(self, orderbook_data: Dict[str, Any], symbol: str) -> OrderBookData:
        """解析订单簿数据"""
        data = orderbook_data.get('data', [{}])[0] if orderbook_data.get('data') else {}
        
        bids = [
            OrderBookLevel(
                price=self._safe_decimal(bid[0]),
                size=self._safe_decimal(bid[1])
            )
            for bid in data.get('bids', [])
        ]
        
        asks = [
            OrderBookLevel(
                price=self._safe_decimal(ask[0]),
                size=self._safe_decimal(ask[1])
            )
            for ask in data.get('asks', [])
        ]
        
        return OrderBookData(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=datetime.fromtimestamp(int(data.get('ts', 0)) / 1000),
            nonce=None,
            raw_data=orderbook_data
        )
    
    def parse_trade(self, trade_data: Dict[str, Any], symbol: str) -> TradeData:
        """解析成交数据"""
        return TradeData(
            id=str(trade_data.get('tradeId', '')),
            symbol=symbol,
            side=OrderSide.BUY if trade_data.get('side') == 'buy' else OrderSide.SELL,
            amount=self._safe_decimal(trade_data.get('sz')),
            price=self._safe_decimal(trade_data.get('px')),
            cost=self._safe_decimal(float(trade_data.get('px', 0)) * float(trade_data.get('sz', 0))),
            fee=None,  # OKX trade数据中不包含fee
            timestamp=datetime.fromtimestamp(int(trade_data.get('ts', 0)) / 1000),
            order_id=None,
            raw_data=trade_data
        )
    
    def parse_balance(self, currency: str, balance_info: Dict[str, Any]) -> BalanceData:
        """解析余额数据"""
        return BalanceData(
            currency=currency,
            free=self._safe_decimal(balance_info.get('availBal')),
            used=self._safe_decimal(balance_info.get('frozenBal')),
            total=self._safe_decimal(balance_info.get('bal')),
            usd_value=self._safe_decimal(balance_info.get('usdBal')),
            timestamp=datetime.now(),
            raw_data=balance_info
        )
    
    def parse_position(self, position_info: Dict[str, Any]) -> PositionData:
        """解析持仓数据"""
        symbol = self.map_symbol_from_okx(position_info.get('instId', ''))
        
        # OKX的持仓方向映射
        pos_side = position_info.get('posSide', 'net')
        side = self.POSITION_SIDE_MAPPING.get(pos_side, PositionSide.BOTH)
        
        # 如果是净持仓模式，根据持仓数量判断方向
        if side == PositionSide.BOTH:
            pos_size = float(position_info.get('pos', 0))
            if pos_size > 0:
                side = PositionSide.LONG
            elif pos_size < 0:
                side = PositionSide.SHORT
        
        return PositionData(
            symbol=symbol,
            side=side,
            size=self._safe_decimal(abs(float(position_info.get('pos', 0)))),
            entry_price=self._safe_decimal(position_info.get('avgPx')),
            mark_price=self._safe_decimal(position_info.get('markPx')),
            current_price=self._safe_decimal(position_info.get('last')),
            unrealized_pnl=self._safe_decimal(position_info.get('upl')),
            realized_pnl=self._safe_decimal(position_info.get('realizedPnl')),
            percentage=self._safe_decimal(position_info.get('uplRatio')),
            leverage=self._safe_int(position_info.get('lever', 1)),
            margin_mode=MarginMode.CROSS if position_info.get('mgnMode') == 'cross' else MarginMode.ISOLATED,
            margin=self._safe_decimal(position_info.get('margin')),
            liquidation_price=self._safe_decimal(position_info.get('liqPx')),
            timestamp=datetime.now(),
            raw_data=position_info
        )
    
    def parse_order(self, order_data: Dict[str, Any], symbol: str) -> OrderData:
        """解析订单数据"""
        # 状态映射
        status = self.ORDER_STATUS_MAPPING.get(
            order_data.get('state'), OrderStatus.UNKNOWN)
        
        # 类型映射
        order_type_str = order_data.get('ordType', 'limit')
        order_type = OrderType.LIMIT
        for ot, okx_type in self.ORDER_TYPE_MAPPING.items():
            if okx_type == order_type_str:
                order_type = ot
                break
        
        # 计算剩余数量
        orig_qty = float(order_data.get('sz', 0))
        filled_qty = float(order_data.get('fillSz', 0))
        remaining = orig_qty - filled_qty
        
        return OrderData(
            id=str(order_data.get('ordId', '')),
            client_id=order_data.get('clOrdId'),
            symbol=symbol,
            side=OrderSide.BUY if order_data.get('side') == 'buy' else OrderSide.SELL,
            type=order_type,
            amount=self._safe_decimal(order_data.get('sz')),
            price=self._safe_decimal(order_data.get('px')),
            filled=self._safe_decimal(order_data.get('fillSz')),
            remaining=self._safe_decimal(remaining),
            cost=self._safe_decimal(order_data.get('fillNotionalUsd')),
            average=self._safe_decimal(order_data.get('avgPx')),
            status=status,
            timestamp=datetime.fromtimestamp(int(order_data.get('cTime', 0)) / 1000),
            updated=datetime.fromtimestamp(int(order_data.get('uTime', 0)) / 1000),
            fee=None,  # 需要单独查询
            trades=[],
            params={},
            raw_data=order_data
        )
    
    def validate_symbol(self, symbol: str) -> bool:
        """验证交易对格式"""
        if not symbol:
            return False
        
        # OKX格式检查：BTC-USDT-SWAP
        parts = symbol.split('-')
        if len(parts) < 2:  # 至少包含基础货币和计价货币
            return False
        
        return True
    
    def get_precision_info(self, symbol: str) -> Dict[str, int]:
        """获取交易对精度信息"""
        # 从市场信息中获取精度，或使用默认值
        if symbol in self._market_info:
            market = self._market_info[symbol]
            return {
                'price_precision': market.get('precision', {}).get('price', 8),
                'amount_precision': market.get('precision', {}).get('amount', 8)
            }
        
        # 默认精度
        return {
            'price_precision': 8,
            'amount_precision': 8
        }
    
    def format_price(self, price: Decimal, symbol: str) -> str:
        """格式化价格"""
        precision = self.get_precision_info(symbol)['price_precision']
        return f"{price:.{precision}f}"
    
    def format_amount(self, amount: Decimal, symbol: str) -> str:
        """格式化数量"""
        precision = self.get_precision_info(symbol)['amount_precision']
        return f"{amount:.{precision}f}"
    
    def get_inst_type_for_symbol(self, symbol: str) -> str:
        """根据符号获取合约类型"""
        if '-SWAP' in symbol:
            return OKXInstType.SWAP.value
        elif '-FUTURES' in symbol:
            return OKXInstType.FUTURES.value
        elif 'MARGIN' in symbol:
            return OKXInstType.MARGIN.value
        else:
            return OKXInstType.SPOT.value
