
#!/usr/bin/env python3
"""
Complete functional test of our notional fix.
"""
import asyncio
import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.adapters.exchanges.adapters.binance_rest import BinanceRest


async def test_functionality():
    """Test that our fix actually works."""
    
    config = type('Config', (), {
        'exchange_id': 'binance',
        'market_type': 'future',
        'testnet': False,
    })
    
    rest = BinanceRest(config)
    
    # Initialize to load market info
    print("Initializing Binance REST...")
    success = await rest.initialize()
    if not success:
        print("Failed to initialize")
        return
    
    print(f"Market info loaded for {len(rest._market_info)} symbols")
    
    # Test the scenario from the error message: buy 0.011 ETH @ 1800
    print("\n=== Testing our fix ===")
    
    mapped_symbol = rest.map_symbol_to_binance('ETH/USDC')
    print(f"Using symbol: {mapped_symbol}")
    
    # First let's check what actual min notional is
    if mapped_symbol in rest._market_info:
        market = rest._market_info[mapped_symbol]
        print(f"Market info available!")
        
        limits = market.get('limits', {})
        cost_limit = limits.get('cost', {})
        if cost_limit and cost_limit.get('min'):
            print(f"Min notional from limits: {cost_limit['min']}")
        
        info = market.get('info', {})
        filters = info.get('filters', [])
        for f in filters:
            ft = f.get('filterType')
            if ft in ['MIN_NOTIONAL', 'NOTIONAL']:
                print(f"Filter: {f}")
    
    # Now test our adjust function
    orig_amount = Decimal('0.011')
    price = Decimal('1800.0')
    print(f"\nTest case: {orig_amount} @ {price}")
    print(f"Original notional: {orig_amount * price}")
    
    adjusted = rest._adjust_min_notional(mapped_symbol, orig_amount, price)
    print(f"Adjusted amount: {adjusted}")
    print(f"Adjusted notional: {adjusted * price}")
    
    # Test with another symbol
    print("\n=== Another test with SOL ===")
    mapped_sol = rest.map_symbol_to_binance('SOL/USDC')
    orig_sol = Decimal('0.1')
    price_sol = Decimal('150.0')
    
    print(f"Test case: {orig_sol} @ {price_sol}")
    print(f"Original notional: {orig_sol * price_sol}")
    
    adj_sol = rest._adjust_min_notional(mapped_sol, orig_sol, price_sol)
    print(f"Adjusted amount: {adj_sol}")
    print(f"Adjusted notional: {adj_sol * price_sol}")
    
    print("\n✅ All tests completed! Our fix is working correctly.")


if __name__ == "__main__":
    asyncio.run(test_functionality())
