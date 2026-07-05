"""
O1Exchange REST API 模块

实现 O1Exchange 的 REST API 功能，包括市场数据、账户管理、交易操作等
"""

import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Any
from decimal import Decimal

from .o1exchange_base import O1ExchangeBase
from ..models import (
    TickerData, OrderBookData, TradeData, BalanceData, PositionData,
    OrderData, OHLCVData, ExchangeInfo, OrderBookLevel,
    OrderSide, OrderType, OrderStatus, PositionSide, MarginMode, ExchangeType
)


class O1ExchangeRest(O1ExchangeBase):
    """O1Exchange REST API 类"""

    def __init__(self, config=None, logger=None):
        super().__init__(config)
        self.logger = logger
        self.session: Optional[aiohttp.ClientSession] = None
        self.api_token: Optional[str] = None
        self.max_retries = 3
        self.retry_delay = 1.0

    async def connect(self) -> bool:
        """建立连接"""
        try:
            # 创建 aiohttp session
            self.session = aiohttp.ClientSession()

            # 设置 API token
            if self.config and self.config.api_key:
                self.api_token = self.config.api_key

            # 初始化钱包账户
            if self.config and self.config.api_secret:
                self.initialize_account(self.config.api_secret)

            if self.logger:
                auth_mode = "认证模式" if self.api_token else "公共访问模式"
                self.logger.info(f"O1Exchange REST 连接成功 ({auth_mode})")

            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"O1Exchange REST 连接失败: {str(e)}")
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        if self.session:
            await self.session.close()
            self.session = None
            if self.logger:
                self.logger.info("O1Exchange REST 连接已断开")

    async def _request(self, method: str, endpoint: str, data: Optional[Dict] = None,
                      auth_required: bool = False) -> Dict[str, Any]:
        """
        发送 API 请求

        Args:
            method: HTTP 方法 (GET, POST, etc.)
            endpoint: API 端点
            data: 请求数据
            auth_required: 是否需要认证

        Returns:
            API 响应
        """
        if not self.session:
            raise RuntimeError("Session not connected")

        url = f"{self.api_base}/{endpoint.lstrip('/')}"

        headers = {
            "Content-Type": "application/json"
        }

        if auth_required and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with self.session.request(
                    method=method,
                    url=url,
                    json=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response_data = await response.json()

                    if response.status == 200:
                        return response_data
                    else:
                        raise Exception(f"API 请求失败: {response.status} - {response_data}")

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    if self.logger:
                        self.logger.warning(
                            f"API 请求失败 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}"
                        )
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    if self.logger:
                        self.logger.error(f"API 请求最终失败: {str(e)}")

        raise last_error

    # === 市场数据 API ===

    async def get_exchange_info(self) -> ExchangeInfo:
        """获取交易所信息"""
        return ExchangeInfo(
            name="O1Exchange",
            id="o1exchange",
            type=ExchangeType.SPOT,
            supported_features=[
                "spot_trading", "mev_protection", "permit2", "orderbook", "ticker"
            ],
            rate_limits=self.config.rate_limits if self.config else {},
            precision=self.config.precision if self.config else {},
            fees={},
            markets={},
            status="operational",
            timestamp=datetime.now()
        )

    async def get_ticker(self, symbol: str) -> TickerData:
        """获取单个交易对行情"""
        # TODO: 实现实际的 ticker API 调用
        # 暂时返回模拟数据
        return TickerData(
            symbol=symbol,
            timestamp=datetime.now(),
            last=Decimal("0"),
            bid=Decimal("0"),
            ask=Decimal("0"),
            high=Decimal("0"),
            low=Decimal("0"),
            volume=Decimal("0"),
            quote_volume=Decimal("0"),
            change=Decimal("0"),
            change_percent=Decimal("0")
        )

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        """获取多个交易对行情"""
        if symbols:
            tasks = [self.get_ticker(symbol) for symbol in symbols]
            return await asyncio.gather(*tasks)
        return []

    async def get_orderbook(self, symbol: str, limit: Optional[int] = None) -> OrderBookData:
        """获取订单簿"""
        # TODO: 实现实际的 orderbook API 调用
        return OrderBookData(
            symbol=symbol,
            timestamp=datetime.now(),
            bids=[],
            asks=[]
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OHLCVData]:
        """获取 K 线数据"""
        # TODO: 实现实际的 OHLCV API 调用
        return []

    async def get_trades(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[TradeData]:
        """获取最近成交记录"""
        # TODO: 实现实际的 trades API 调用
        return []

    # === 账户 API ===

    async def get_balances(self) -> List[BalanceData]:
        """获取账户余额"""
        # TODO: 实现实际的余额 API 调用
        return []

    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        """获取持仓信息"""
        # O1Exchange 主要是现货交易，暂时返回空列表
        return []

    # === 交易 API ===

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> OrderData:
        """
        创建订单

        实现完整的 O1Exchange 交易流程：
        1. 创建交易批次
        2. 签名 Permit2 (如需要)
        3. 签名交易
        4. 提交交易
        """
        try:
            # 解析交易对
            base_token, quote_token = self._parse_symbol(symbol)

            # 获取网络配置
            network = (params or {}).get('network', self.default_network)
            network_id = self.get_network_id(network)

            # 获取滑点配置
            slippage_bps = (params or {}).get('slippage_bps', 300)
            mev_protection = (params or {}).get('mev_protection', True)

            # 步骤 1: 创建交易批次
            create_order_data = {
                "networkId": network_id,
                "signerAddress": self._wallet_address,
                "tokenAddress": base_token,  # TODO: 需要获取实际的 token 地址
                "uiAmount": str(amount),
                "direction": "buy" if side == OrderSide.BUY else "sell",
                "slippageBps": slippage_bps,
                "mevProtection": mev_protection
            }

            # 添加 quoteTokenAddress (可选)
            if quote_token:
                create_order_data["quoteTokenAddress"] = quote_token  # TODO: 需要获取实际地址

            batch_response = await self._request(
                "POST",
                "order",
                create_order_data,
                auth_required=True
            )

            if not batch_response.get("success"):
                raise Exception(f"创建交易批次失败: {batch_response}")

            batch_id = batch_response["id"]
            transactions = batch_response["transactions"]

            if not transactions:
                raise Exception("未返回交易数据")

            tx_data = transactions[0]
            unsigned_tx = tx_data["unsigned"]
            permit2_data = tx_data.get("permit2")

            # 步骤 2: 签名 Permit2 (如需要)
            permit2_signature = None
            if permit2_data and permit2_data.get("eip712"):
                permit2_signature = self.sign_permit2(permit2_data["eip712"])

            # 步骤 3: 签名交易
            signed_tx = self.sign_transaction(unsigned_tx)

            # 步骤 4: 提交交易
            submit_data = {
                "id": batch_id,
                "transactions": [
                    {
                        "id": tx_data["id"],
                        "signed": signed_tx,
                        "permit2": {
                            "eip712": {
                                "signature": permit2_signature
                            }
                        } if permit2_signature else None
                    }
                ]
            }

            submit_response = await self._request(
                "POST",
                "order/complete",
                submit_data,
                auth_required=True
            )

            if not submit_response.get("success"):
                raise Exception(f"提交交易失败: {submit_response}")

            # 构造订单数据
            order = OrderData(
                id=batch_id,
                symbol=symbol,
                side=side,
                type=order_type,
                amount=amount,
                price=price or Decimal("0"),
                status=OrderStatus.FILLED,  # 假设立即成交
                filled=amount,
                remaining=Decimal("0"),
                timestamp=datetime.now(),
                fee=Decimal("0"),
                fee_currency=quote_token or "USDC"
            )

            if self.logger:
                self.logger.info(f"✅ 订单创建成功: {order.id}")

            return order

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 创建订单失败: {e}")
            raise

    async def cancel_order(self, order_id: str, symbol: str) -> OrderData:
        """取消订单"""
        # TODO: O1Exchange 是否支持取消订单？需要确认 API
        raise NotImplementedError("取消订单功能暂未实现")

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """取消所有订单"""
        # TODO: 实现
        raise NotImplementedError("批量取消订单功能暂未实现")

    async def get_order(self, order_id: str, symbol: str) -> OrderData:
        """获取订单信息"""
        # TODO: 实现
        raise NotImplementedError("获取订单信息功能暂未实现")

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """获取开放订单"""
        # TODO: 实现
        return []

    async def get_order_history(
        self,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OrderData]:
        """获取历史订单"""
        # TODO: 实现
        return []

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """设置杠杆倍数 (O1Exchange 主要是现货，暂不支持)"""
        return {"success": True, "message": "O1Exchange 不支持杠杆交易"}

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        """设置保证金模式 (O1Exchange 主要是现货，暂不支持)"""
        return {"success": True, "message": "O1Exchange 不支持保证金交易"}

    # === 辅助方法 ===

    def _parse_symbol(self, symbol: str) -> tuple:
        """解析交易对"""
        if '/' in symbol:
            parts = symbol.split('/')
            return parts[0], parts[1] if len(parts) > 1 else None
        return symbol, None
