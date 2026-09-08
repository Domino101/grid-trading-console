"""
Unit tests for AES-256-GCM Secret Vault (P2-2)
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from core.security.vault import (
    PREFIX,
    SecurityFatalError,
    VaultDecryptionError,
    VaultMissingKeyError,
    decrypt_dict_secrets,
    decrypt_secret,
    derive_key,
    encrypt_secret,
    load_secure_yaml,
    mask_secret,
)


class TestSecretVault(unittest.TestCase):
    def setUp(self):
        self.master_key = "test_super_secure_master_passphrase_2026"
        self.secret_plaintext = "binance_live_secret_key_998877665544332211"
        # 暫存環境變數
        self._orig_env = os.environ.get("GRID_MASTER_KEY")
        os.environ.pop("GRID_MASTER_KEY", None)
        os.environ.pop("CRYPTO_VAULT_MASTER_KEY", None)
        os.environ.pop("MASTER_KEY", None)

    def tearDown(self):
        if self._orig_env is not None:
            os.environ["GRID_MASTER_KEY"] = self._orig_env
        else:
            os.environ.pop("GRID_MASTER_KEY", None)

    def test_encrypt_decrypt_roundtrip(self):
        """測試加解密往返 100% 正確性"""
        enc = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        self.assertTrue(enc.startswith(PREFIX))
        self.assertNotEqual(enc, self.secret_plaintext)

        dec = decrypt_secret(enc, master_key=self.master_key)
        self.assertEqual(dec, self.secret_plaintext)

    def test_random_nonce_freshness(self):
        """測試相同明文在不同加密呼叫下產生不同密文 (隨機 Nonce)"""
        enc1 = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        enc2 = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        self.assertNotEqual(enc1, enc2)
        # 但解密後兩者均還原為相同明文
        self.assertEqual(decrypt_secret(enc1, self.master_key), self.secret_plaintext)
        self.assertEqual(decrypt_secret(enc2, self.master_key), self.secret_plaintext)

    def test_derive_key_formats(self):
        """測試各種主密鑰輸入格式 (Hex 64, Base64 32B, Raw Bytes, Text)"""
        # 1. 64-char Hex
        hex_key = "a" * 64
        derived_hex = derive_key(hex_key)
        self.assertEqual(len(derived_hex), 32)
        self.assertEqual(derived_hex, bytes.fromhex(hex_key))

        # 2. Raw 32 bytes
        raw_key = b"\x01" * 32
        self.assertEqual(derive_key(raw_key), raw_key)

        # 3. Text Passphrase
        passphrase = "my_custom_passphrase"
        derived_text = derive_key(passphrase)
        self.assertEqual(len(derived_text), 32)

    def test_tampered_ciphertext_fails(self):
        """測試密文或認證標籤遭到竄改時必須拋出 VaultDecryptionError (SecurityFatalError)"""
        enc = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        # 竄改密文字串中段
        tampered = enc[:20] + ("A" if enc[20] != "A" else "B") + enc[21:]

        with self.assertRaises(VaultDecryptionError):
            decrypt_secret(tampered, master_key=self.master_key)

    def test_wrong_key_fails(self):
        """測試使用錯誤的 Master Key 解密必須拋出例外且拒絕返回偽資料"""
        enc = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        with self.assertRaises(VaultDecryptionError):
            decrypt_secret(enc, master_key="completely_wrong_key_password")

    def test_missing_master_key_raises_fatal(self):
        """測試環境缺少 Master Key 且未指定時，拋出 VaultMissingKeyError"""
        enc = encrypt_secret(self.secret_plaintext, master_key=self.master_key)
        # 此時環境無 GRID_MASTER_KEY
        with self.assertRaises(VaultMissingKeyError):
            decrypt_secret(enc, master_key=None)

    def test_env_var_master_key(self):
        """測試透過環境變數 GRID_MASTER_KEY 自動解密"""
        os.environ["GRID_MASTER_KEY"] = self.master_key
        enc = encrypt_secret(self.secret_plaintext)
        dec = decrypt_secret(enc)
        self.assertEqual(dec, self.secret_plaintext)

    def test_plaintext_passthrough(self):
        """測試未加密的純文字字串向下相容原樣返回"""
        plain = "unencrypted_api_key_value"
        self.assertEqual(decrypt_secret(plain, self.master_key), plain)
        self.assertEqual(decrypt_secret(12345, self.master_key), 12345)
        self.assertIsNone(decrypt_secret(None, self.master_key))

    def test_mask_secret(self):
        """測試明文日誌遮蔽保護"""
        self.assertEqual(mask_secret("1234567890abcdef"), "1234...cdef")
        self.assertEqual(mask_secret("short"), "***")
        self.assertEqual(mask_secret(""), "***")
        self.assertEqual(mask_secret(None), "***")

    def test_decrypt_dict_secrets(self):
        """測試遞迴字典與串列解密"""
        enc_sec = encrypt_secret("super_secret", master_key=self.master_key)
        enc_key = encrypt_secret("api_key_123", master_key=self.master_key)

        nested = {
            "exchange": {
                "name": "binance",
                "api_key": enc_key,
                "api_secret": enc_sec,
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "rate_limit": 100,
            },
            "auth_tokens": [enc_sec, "plain_token"],
        }

        decrypted = decrypt_dict_secrets(nested, master_key=self.master_key)
        self.assertEqual(decrypted["exchange"]["api_key"], "api_key_123")
        self.assertEqual(decrypted["exchange"]["api_secret"], "super_secret")
        self.assertEqual(decrypted["exchange"]["name"], "binance")
        self.assertEqual(decrypted["exchange"]["rate_limit"], 100)
        self.assertEqual(decrypted["auth_tokens"], ["super_secret", "plain_token"])

    def test_load_secure_yaml(self):
        """測試 YAML 安全載入與自動解密"""
        os.environ["GRID_MASTER_KEY"] = self.master_key
        enc_sec = encrypt_secret("binance_secret_xyz", master_key=self.master_key)

        yaml_content = f"""
exchange:
  name: binance
  api_key: public_key
  api_secret: "{enc_sec}"
strategy:
  grid_count: 20
"""
        cfg = load_secure_yaml(yaml_content)
        self.assertEqual(cfg["exchange"]["api_secret"], "binance_secret_xyz")
        self.assertEqual(cfg["strategy"]["grid_count"], 20)

    def test_cli_encrypt_tool(self):
        """測試 tools/encrypt_api_key.py CLI 腳本執行"""
        repo_root = Path(__file__).resolve().parent.parent
        cli_path = repo_root / "tools" / "encrypt_api_key.py"

        # 測試 --generate-master-key
        proc = subprocess.run(
            [sys.executable, str(cli_path), "--generate-master-key"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("已生成全新 256-bit 高強度 Master Key", proc.stdout)

        # 測試加密命令
        proc2 = subprocess.run(
            [
                sys.executable,
                str(cli_path),
                "--key",
                "test_key",
                "--secret",
                "test_secret",
                "--master-key",
                self.master_key,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("enc:gcm:", proc2.stdout)
        self.assertIn("金鑰加密完成", proc2.stdout)


if __name__ == "__main__":
    unittest.main()
