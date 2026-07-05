
#!/usr/bin/env python3
"""
检查币安市场信息，查看是否包含最小名义价值要求
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.adapters.exchanges.adapters.binance_base import BinanceBase
from core.adapters.exchanges.adapters.binance_rest import BinanceRest
import ccxt


async def check_market_info():
    """检查市场信息"""
    config = type('Config', (), {
        'exchange_id': 'binance',
        'market_type': 'future',
        'testnet': False,
    })
    
    base = BinanceBase(config)
    
    # 直接用 ccxt 测试
    exchange = ccxt.binance(base.ccxt_config)
    
    try:
        print("加载市场信息...")
        markets = exchange.load_markets()
        
        # 查看 ETH/USDT 的市场信息
        print("\n=== 查看 ETH/USDT 市场信息 ===")
        eth_usdt = markets.get('ETH/USDT:USDT') or markets.get('ETH/USDT')
        if eth_usdt:
            print(f"ETH/USDT 信息:")
            for key, value in eth_usdt.items():
                print(f"  {key}: {value}")
        
        # 查看其他几个交易对的信息
        symbols_to_check = ['BTC/USDT:USDT', 'SOL/USDT:USDT', 'ETH/USDT']
        print("\n=== 最小名义价值检查 ===")
        for symbol in symbols_to_check:
            market = markets.get(symbol)
            if market:
                print(f"\n{symbol}:")
                print(f"  limits: {market.get('limits')}")
                print(f"  info: {market.get('info')}")
    
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        exchange.close()


if __name__ == "__main__":
    asyncio.run(check_market_info())
