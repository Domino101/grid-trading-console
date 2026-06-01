#!/usr/bin/env python3
"""
全面清除熔断状态并测试 Binance API 连接
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 先清除所有熔断相关文件
print("=" * 60)
print("🔧 全面清除熔断状态")
print("=" * 60)

data_dir = project_root / "data"
circuit_file = data_dir / ".circuit_breaker_state.json"
circuit_file_bak = data_dir / ".circuit_breaker_state.json.bak"

# 清除文件
for f in [circuit_file, circuit_file_bak]:
    if f.exists():
        f.unlink()
        print(f"✅ 已删除: {f}")

print("\n✅ 文件清除完成！")

# 现在测试 API 连接
print("\n" + "=" * 60)
print("🔗 测试 Binance API 连接")
print("=" * 60)

from core.adapters.exchanges import ExchangeFactory, ExchangeConfig
from run_grid_trading import create_exchange_adapter
import yaml

# 读取一个配置文件
config_path = project_root / "config/grid/binance-neutral-perp-sol-usdc-100-160.yaml"
if not config_path.exists():
    print(f"❌ 配置文件不存在: {config_path}")
    sys.exit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    config_data = yaml.safe_load(f)

async def test_connection():
    try:
        print("\n正在创建交易所适配器...")
        adapter = await create_exchange_adapter(config_data)
        
        print("✅ 适配器创建成功！")
        
        print("\n正在测试连接...")
        health = await adapter.health_check()
        print(f"✅ 健康检查: {health}")
        
        print("\n正在获取余额...")
        balances = await adapter.get_balances()
        print(f"✅ 成功获取 {len(balances)} 个余额记录")
        for b in balances[:3]:  # 只显示前3个
            print(f"   - {b.currency}: {b.total}")
        
        print("\n" + "=" * 60)
        print("🎉 API 连接测试成功！现在可以启动网格交易了！")
        print("=" * 60)
        
        await adapter.disconnect()
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        print("\n请确保：")
        print("1. Binance API Key 的 IP 白名单已更新为: 36.224.0.100")
        print("2. API Key 和 Secret 正确")
        print("3. 网络连接正常")

if __name__ == "__main__":
    asyncio.run(test_connection())
