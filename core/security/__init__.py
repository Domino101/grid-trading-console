"""
Security and Secrets Management Package (P2-2)
"""

from .vault import (
    SecurityFatalError,
    VaultDecryptionError,
    VaultMissingKeyError,
    decrypt_dict_secrets,
    decrypt_secret,
    derive_key,
    encrypt_secret,
    get_default_master_key,
    load_secure_yaml,
    mask_secret,
)

__all__ = [
    "SecurityFatalError",
    "VaultMissingKeyError",
    "VaultDecryptionError",
    "encrypt_secret",
    "decrypt_secret",
    "mask_secret",
    "decrypt_dict_secrets",
    "load_secure_yaml",
    "derive_key",
    "get_default_master_key",
]
