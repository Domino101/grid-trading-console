#!/usr/bin/env python3
"""
直接测试 API Key（暴力搜索 account_index）

如果您有 API Key Private Key，我们可以通过尝试不同的 account_index
直到找到正确的为止
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

__test__ = False  # Tells pytest this is a CLI tool, not a test suite

try:
    import lighter
    LIGHTER_AVAILABLE = True
except ImportError:
    print("❌ Lighter SDK 未安装")
    sys.exit(1)


async def test_account_index(base_url, api_key_private_key, account_index, api_key_index):
    """测试特定的 account_index"""
    try:
        client = lighter.SignerClient(
            url=base_url,
            private_key=api_key_private_key,
            account_index=account_index,
            api_key_index=api_key_index,
        )

        err = client.check_client()
        await client.close()

        if err is None:
            return True
        return False
    except Exception:
        return False


async def main():
    print("\n" + "=" * 70)
    print("  API Key 快速测试工具")
    print("=" * 70)

    print("\n这个工具会自动尝试不同的 account_index")
    print("直到找到正确的配置\n")

    # 获取输入
    print("请输入 API Key Private Key:")
    api_key = input("Private Key: ").strip()

    if not api_key.startswith("0x"):
        api_key = "0x" + api_key

    print("\n请输入 API Key Index (从前端看到的，通常是 0 或 1):")
    api_key_index = int(input("API Key Index [0]: ").strip() or "0")

    print("\n请选择网络:")
    print("  1. 测试网")
    print("  2. 主网")
    choice = input("选择 [2]: ").strip() or "2"

    testnet = choice == "1"
    base_url = "https://testnet.zklighter.elliot.ai" if testnet else "https://mainnet.zklighter.elliot.ai"

    print(f"\n开始搜索 account_index...")
    print(f"网络: {'测试网' if testnet else '主网'}")
    print(f"API Key Index: {api_key_index}\n")

    # 搜索范围
    for account_index in range(0, 10000):
        if account_index % 100 == 0:
            print(f"尝试: {account_index}...", end='\r')

        success = await test_account_index(base_url, api_key, account_index, api_key_index)

        if success:
            print(f"\n\n🎉 找到了！")
            print(f"\n您的配置:")
            print(f"  testnet: {'true' if testnet else 'false'}")
            print(f"  account_index: {account_index}")
            print(f"  api_key_index: {api_key_index}")
            print(f"  api_key_private_key: {api_key}")

            print(f"\n请复制以下内容到 config/exchanges/lighter_config.yaml:")
            print(f"\napi_config:")
            print(f"  testnet: {'true' if testnet else 'false'}")
            print(f"  auth:")
            print(f"    account_index: {account_index}")
            print(f"    api_key_private_key: \"{api_key}\"")
            print(f"    api_key_index: {api_key_index}")

            return

    print("\n\n⚠️  在 0-10000 范围内未找到匹配的 account_index")
    print("可能原因:")
    print("  1. API Key Private Key 不正确")
    print("  2. API Key Index 不正确")
    print("  3. 网络选择错误")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n已取消")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
