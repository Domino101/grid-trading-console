"""
AES-256-GCM 本機金鑰保險箱 (AES-256-GCM Vault)
負責 API Key / Secret 之對稱加解密存儲、記憶體解密與日誌遮蔽保護 (P2-2)
"""

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

PREFIX = "enc:gcm:"
NONCE_LENGTH = 12  # 96-bit standard nonce for GCM
TAG_LENGTH = 16    # 128-bit authentication tag


class SecurityFatalError(RuntimeError):
    """重大安全例外基類 - 進程必須拒絕啟動並熔斷，嚴禁以空金鑰或毀損狀態執行"""
    pass


class VaultMissingKeyError(SecurityFatalError):
    """缺少 Master Key 主密鑰例外"""
    pass


class VaultDecryptionError(SecurityFatalError):
    """密文解密失敗或資料被竄改例外"""
    pass


def get_default_master_key() -> Optional[str]:
    """
    從環境變數取得預設 Master Key
    優先級: GRID_MASTER_KEY > CRYPTO_VAULT_MASTER_KEY > MASTER_KEY
    """
    return (
        os.environ.get("GRID_MASTER_KEY")
        or os.environ.get("CRYPTO_VAULT_MASTER_KEY")
        or os.environ.get("MASTER_KEY")
    )


def derive_key(master_key: Union[str, bytes]) -> bytes:
    """
    將使用者提供的主金鑰衍生為標準 32-byte (256-bit) AES-GCM 金鑰
    支援：
      1. 32-byte 原始 bytes
      2. 64-char Hex 字串
      3. 44-char Base64 字串 (代表 32 bytes)
      4. 任意長度字串經 SHA-256 單向衍生
    """
    if isinstance(master_key, bytes):
        if len(master_key) == 32:
            return master_key
        return hashlib.sha256(master_key).digest()

    if not isinstance(master_key, str) or not master_key:
        raise VaultMissingKeyError("Master Key 不能為空字串或無效型別")

    master_key = master_key.strip()
    if not master_key:
        raise VaultMissingKeyError("Master Key 不能為空空白字串")

    # 嘗試 64-char hex
    if len(master_key) == 64:
        try:
            raw = bytes.fromhex(master_key)
            if len(raw) == 32:
                return raw
        except ValueError:
            pass

    # 嘗試 44-char base64
    if len(master_key) == 44 and master_key.endswith("="):
        try:
            raw = base64.b64decode(master_key.encode("ascii"))
            if len(raw) == 32:
                return raw
        except Exception:
            pass

    # 一般 passphrase 使用 SHA-256 衍生 32 bytes
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def encrypt_secret(plaintext: str, master_key: Optional[Union[str, bytes]] = None) -> str:
    """
    使用 AES-256-GCM 加密明文字串
    格式輸出: enc:gcm:<urlsafe_b64(12-byte nonce + ciphertext + 16-byte tag)>
    """
    if not isinstance(plaintext, str):
        raise TypeError(f"Plaintext 必須為字串型別，收到 {type(plaintext)}")

    key_material = master_key or get_default_master_key()
    if not key_material:
        raise VaultMissingKeyError(
            "未指定 master_key 且環境變數 GRID_MASTER_KEY 未設定，無法執行加密"
        )

    key = derive_key(key_material)
    nonce = os.urandom(NONCE_LENGTH)
    aesgcm = AESGCM(key)

    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = nonce + ciphertext_with_tag
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")

    return f"{PREFIX}{encoded}"


def decrypt_secret(ciphertext: str, master_key: Optional[Union[str, bytes]] = None) -> str:
    """
    解密 enc:gcm: 開頭的密文字串
    若非 enc:gcm: 開頭則視為明文原樣返回 (向下相容)
    若 Master Key 缺失或解密失敗，嚴格拋出 SecurityFatalError
    """
    if not isinstance(ciphertext, str):
        return ciphertext

    if not ciphertext.startswith(PREFIX):
        return ciphertext

    key_material = master_key or get_default_master_key()
    if not key_material:
        raise VaultMissingKeyError(
            "檢測到加密金鑰欄位，但系統未配置 Master Key (環境變數 GRID_MASTER_KEY 缺失)！"
            "為保護資金安全，進程拒絕啟動，嚴禁使用空密鑰下單。"
        )

    key = derive_key(key_material)
    raw_b64 = ciphertext[len(PREFIX):]

    try:
        payload = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    except Exception as exc:
        raise VaultDecryptionError(f"Base64 密文格式毀損或無法解析: {exc}") from exc

    if len(payload) < NONCE_LENGTH + TAG_LENGTH:
        raise VaultDecryptionError(
            f"密文長度過短 ({len(payload)} bytes)，不符合 AES-256-GCM 規範"
        )

    nonce = payload[:NONCE_LENGTH]
    ct_with_tag = payload[NONCE_LENGTH:]
    aesgcm = AESGCM(key)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ct_with_tag, None)
        return decrypted_bytes.decode("utf-8")
    except InvalidTag as exc:
        raise VaultDecryptionError(
            "AES-256-GCM 認證標籤不符！密文遭到竄改，或使用了錯誤的 Master Key！"
        ) from exc
    except Exception as exc:
        raise VaultDecryptionError(f"解密過程中遭遇未知錯誤: {exc}") from exc


def mask_secret(secret: Optional[str], show_prefix: int = 4, show_suffix: int = 4) -> str:
    """
    全面遮蔽明文金鑰，供日誌過濾與輸出使用 (Invariant 2)
    例如: "ABCD1234EFGH5678" -> "ABCD...5678"
    """
    if not secret:
        return "***"
    s = str(secret)
    if len(s) <= (show_prefix + show_suffix + 2):
        return "***"
    return f"{s[:show_prefix]}...{s[-show_suffix:]}"


def decrypt_dict_secrets(obj: Any, master_key: Optional[Union[str, bytes]] = None) -> Any:
    """
    遞迴遍歷字典、串列，原地或深層解密所有含有 enc:gcm: 的值
    """
    if isinstance(obj, dict):
        return {
            k: decrypt_dict_secrets(v, master_key=master_key)
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [decrypt_dict_secrets(elem, master_key=master_key) for elem in obj]
    elif isinstance(obj, str):
        if obj.startswith(PREFIX):
            return decrypt_secret(obj, master_key=master_key)
        return obj
    return obj


def load_secure_yaml(
    source: Union[str, Path, Any],
    master_key: Optional[Union[str, bytes]] = None,
) -> Dict[str, Any]:
    """
    安全載入 YAML 設定檔，若內容包含 enc:gcm: 欄位則自動解密
    """
    data = None
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            data = yaml.safe_load(str(source))
    else:
        data = yaml.safe_load(source)

    if not isinstance(data, dict):
        return data or {}

    return decrypt_dict_secrets(data, master_key=master_key)
