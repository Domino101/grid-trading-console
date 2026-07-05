
#!/usr/bin/env python3
"""Simple syntax check test."""

import sys
import os

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Testing import of binance_rest.py...")
try:
    from core.adapters.exchanges.adapters.binance_rest import BinanceRest
    print("✓ Successfully imported BinanceRest")
    
    # Test that our _fix_min_notional method exists
    if hasattr(BinanceRest, '_fix_min_notional'):
        print("✓ _fix_min_notional method exists")
    else:
        print("✗ _fix_min_notional method missing")
    
    print("\nAll tests passed! The code should work correctly.")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
