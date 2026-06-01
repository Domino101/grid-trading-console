#!/usr/bin/env python3
"""
清除 Binance API 熔断状态脚本
用于解决 "API 熔斷器永久開啟 (code=-2015)" 错误
"""

import os
import sys
import json
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.adapters.exchanges.adapters.binance_rest import BinanceRest
from core.services.grid.implementations.grid_engine_impl import (
    clear_persisted_circuit_state,
    GridEngineImpl
)

def main():
    print("=" * 60)
    print("🔧 清除 Binance API 熔断状态")
    print("=" * 60)
    
    # 1. 清除持久化文件
    print("\n1. 清除熔断状态文件...")
    clear_persisted_circuit_state()
    print("✅ 已清除 data/.circuit_breaker_state.json")
    
    # 2. 重置内存中的类变量
    print("\n2. 重置内存中的熔断状态...")
    BinanceRest.clear_adapter_circuit()
    GridEngineImpl._shared_api_circuit_until = 0.0
    GridEngineImpl._shared_last_api_error_code = None
    print("✅ 已重置内存熔断状态")
    
    print("\n" + "=" * 60)
    print("✅ 熔断状态已完全清除！")
    print("=" * 60)
    print("\n接下来请执行以下步骤：")
    print("1. 检查并更新 Binance API 的 IP 白名单（重要！）")
    print("2. 重新启动你的网格交易系统")
    print("\n如果问题仍然存在，请检查：")
    print("- API Key 是否仍然有效")
    print("- API Secret 是否正确")
    print("- Binance 账户状态是否正常")

if __name__ == "__main__":
    main()
