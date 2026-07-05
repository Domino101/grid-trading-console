
#!/usr/bin/env python3
"""
测试最小名义价值修复
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from core.adapters.exchanges.adapters.binance_base import BinanceBase
from core.adapters.exchanges.adapters.binance_rest import BinanceRest


async def test_min_notional_fix():
    """测试最小名义价值修复"""
    config = type('Config', (), {
        'exchange_id': 'binance',
        'market_type': 'future',
        'testnet': False,
    })
    
    rest = BinanceRest(config)
    rest.logger = logger
    
    # 初始化
    logger.info("正在初始化币安 REST API...")
    success = await rest.initialize()
    if not success:
        logger.error("初始化失败")
        return
    
    # 测试场景1: 模拟用户遇到的问题 - ETH/USDT 订单
    logger.info("\n=== 测试场景 1: ETH/USDT 小订单 ===")
    mapped_symbol = rest.map_symbol_to_binance('ETH/USDT')
    logger.info(f"映射后的交易对: {mapped_symbol}")
    
    # 测试原始订单（应该会失败的）
    original_amount = Decimal('0.011')
    original_price = Decimal('1800.0')
    logger.info(f"原始订单: 数量 {original_amount}, 价格 {original_price}, 名义价值 {original_amount * original_price}")
    
    # 调用调整函数
    adjusted_amount, adjusted_price = rest._adjust_order_for_min_notional(
        mapped_symbol, original_amount, original_price
    )
    logger.info(f"调整后: 数量 {adjusted_amount}, 价格 {adjusted_price}, 名义价值 {adjusted_amount * adjusted_price}")
    
    # 测试场景2: 更小的订单
    logger.info("\n=== 测试场景 2: 更小的订单 ===")
    tiny_amount = Decimal('0.001')
    tiny_price = Decimal('1800.0')
    logger.info(f"原始订单: 数量 {tiny_amount}, 价格 {tiny_price}, 名义价值 {tiny_amount * tiny_price}")
    
    adjusted_amount2, adjusted_price2 = rest._adjust_order_for_min_notional(
        mapped_symbol, tiny_amount, tiny_price
    )
    logger.info(f"调整后: 数量 {adjusted_amount2}, 价格 {adjusted_price2}, 名义价值 {adjusted_amount2 * adjusted_price2}")
    
    # 测试场景3: 满足最小要求的订单
    logger.info("\n=== 测试场景 3: 已经满足最小要求的订单 ===")
    ok_amount = Decimal('0.1')
    ok_price = Decimal('1800.0')
    logger.info(f"原始订单: 数量 {ok_amount}, 价格 {ok_price}, 名义价值 {ok_amount * ok_price}")
    
    adjusted_amount3, adjusted_price3 = rest._adjust_order_for_min_notional(
        mapped_symbol, ok_amount, ok_price
    )
    logger.info(f"调整后: 数量 {adjusted_amount3}, 价格 {adjusted_price3}, 名义价值 {adjusted_amount3 * adjusted_price3}")
    
    logger.info("\n✅ 测试完成！")


if __name__ == "__main__":
    asyncio.run(test_min_notional_fix())
