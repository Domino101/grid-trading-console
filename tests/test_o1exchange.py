"""
O1Exchange 适配器测试

测试 O1Exchange 交易所适配器的基本功能
"""

import os
import sys
import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure project root is in sys.path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.adapters.exchanges.interface import ExchangeConfig
from core.adapters.exchanges.models import ExchangeType, OrderSide, OrderType
from core.adapters.exchanges.adapters.o1exchange import O1ExchangeAdapter
from core.adapters.exchanges.adapters.o1exchange_base import O1ExchangeBase


class TestO1ExchangeBase(unittest.TestCase):
    """测试 O1Exchange 基础模块"""

    def test_network_mapping(self):
        """测试网络 ID 映射"""
        base = O1ExchangeBase()
        
        # 测试网络名称转 ID
        self.assertEqual(base.get_network_id("base"), 8453)
        self.assertEqual(base.get_network_id("bsc"), 56)
        self.assertEqual(base.get_network_id("solana"), 1399811149)
        
        # 测试网络 ID 转名称
        self.assertEqual(base.get_network_name(8453), "base")
        self.assertEqual(base.get_network_name(56), "bsc")
        self.assertEqual(base.get_network_name(1399811149), "solana")

    def test_invalid_network(self):
        """测试无效网络"""
        base = O1ExchangeBase()
        
        with self.assertRaises(Exception):
            base.get_network_id("invalid_network")
        
        with self.assertRaises(Exception):
            base.get_network_name(999999)


class TestO1ExchangeAdapter(unittest.IsolatedAsyncioTestCase):
    """测试 O1Exchange 适配器"""

    async def asyncSetUp(self):
        """设置测试环境"""
        self.config = ExchangeConfig(
            exchange_id="o1exchange",
            name="O1Exchange",
            exchange_type=ExchangeType.SPOT,
            api_key="test_api_key",
            api_secret="test_private_key",  # 这需要是一个有效的 ECDSA 私钥格式
            wallet_address="0x1234567890123456789012345678901234567890",
            testnet=False,
            enable_websocket=False
        )

    async def test_adapter_initialization(self):
        """测试适配器初始化"""
        adapter = O1ExchangeAdapter(self.config)
        
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.config.exchange_id, "o1exchange")
        self.assertEqual(adapter.config.name, "O1Exchange")

    @patch('core.adapters.exchanges.adapters.o1exchange_rest.O1ExchangeRest.connect')
    async def test_connect(self, mock_connect):
        """测试连接功能"""
        mock_connect.return_value = True
        
        adapter = O1ExchangeAdapter(self.config)
        result = await adapter.connect()
        
        self.assertTrue(result)
        mock_connect.assert_called_once()

    @patch('core.adapters.exchanges.adapters.o1exchange_rest.O1ExchangeRest.get_exchange_info')
    async def test_get_exchange_info(self, mock_get_info):
        """测试获取交易所信息"""
        from datetime import datetime
        from decimal import Decimal
        from core.adapters.exchanges.models import ExchangeInfo
        
        mock_get_info.return_value = ExchangeInfo(
            name="O1Exchange",
            id="o1exchange",
            type=ExchangeType.SPOT,
            supported_features=["spot_trading", "mev_protection"],
            rate_limits={},
            precision={},
            fees={},
            markets={},
            status="operational",
            timestamp=datetime.now()
        )
        
        adapter = O1ExchangeAdapter(self.config)
        info = await adapter.get_exchange_info()
        
        self.assertEqual(info.name, "O1Exchange")
        self.assertEqual(info.id, "o1exchange")


if __name__ == "__main__":
    unittest.main()
