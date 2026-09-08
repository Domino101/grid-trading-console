#!/usr/bin/env python3
"""
API 金鑰本機對稱加密輔助工具 (AES-256-GCM Vault CLI)
使用範例：
  python3 tools/encrypt_api_key.py --secret "my_exchange_api_secret"
  python3 tools/encrypt_api_key.py --key "my_api_key" --secret "my_api_secret" --master-key "super_secret_master_key"
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

# 將專案根目錄加入路徑
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.security.vault import encrypt_secret, mask_secret, derive_key


def main():
    parser = argparse.ArgumentParser(
        description="AES-256-GCM API 金鑰加密工具 (P2-2)"
    )
    parser.add_argument(
        "--key",
        "-k",
        type=str,
        default=None,
        help="待加密的 API Key (選填，若需要加密 API Key 欄位)",
    )
    parser.add_argument(
        "--secret",
        "-s",
        type=str,
        default=None,
        help="待加密的 API Secret / Private Key",
    )
    parser.add_argument(
        "--master-key",
        "-m",
        type=str,
        default=None,
        help="加密使用的 Master Key (若未指定將自 GRID_MASTER_KEY 環境變數讀取)",
    )
    parser.add_argument(
        "--generate-master-key",
        action="store_true",
        help="生成一組全新的 256-bit 高強度隨機 Master Key",
    )

    args = parser.parse_args()

    if args.generate_master_key:
        new_key = os.urandom(32).hex()
        print("============================================================")
        print("🔑 已生成全新 256-bit 高強度 Master Key (Hex 格式):")
        print(f"   {new_key}")
        print("\n請將此密鑰配置至環境變數或 .env 中：")
        print(f'   export GRID_MASTER_KEY="{new_key}"')
        print("============================================================")
        return

    master_key = args.master_key or os.environ.get("GRID_MASTER_KEY")
    if not master_key:
        # 互動式請求
        print("⚠️ 未偵測到 GRID_MASTER_KEY 環境變數。")
        master_key = getpass.getpass("請輸入 Master Key (主加密金鑰): ")
        if not master_key.strip():
            print("❌ 錯誤: Master Key 不能為空！", file=sys.stderr)
            sys.exit(1)

    secret = args.secret
    if secret is None and args.key is None:
        secret = getpass.getpass("請輸入待加密的 API Secret: ")
        if not secret.strip():
            print("❌ 錯誤: API Secret 不能為空！", file=sys.stderr)
            sys.exit(1)

    print("\n============================================================")
    print("🔒 [AES-256-GCM Vault] 金鑰加密完成")
    print("============================================================")

    if args.key:
        enc_key = encrypt_secret(args.key, master_key=master_key)
        print(f"原始 Key 遮蔽:   {mask_secret(args.key)}")
        print(f"加密後 Key:      {enc_key}\n")

    if secret:
        enc_secret = encrypt_secret(secret, master_key=master_key)
        print(f"原始 Secret 遮蔽: {mask_secret(secret)}")
        print(f"加密後 Secret:   {enc_secret}\n")

    print("📝 請將密文字串複製至 YAML 設定檔對應欄位，例如：")
    print("---")
    print("exchange:")
    print("  name: binance")
    if args.key:
        print(f"  api_key: \"{enc_key}\"")
    else:
        print("  api_key: \"your_public_api_key\"")
    if secret:
        print(f"  api_secret: \"{enc_secret}\"")
    print("============================================================")


if __name__ == "__main__":
    main()
