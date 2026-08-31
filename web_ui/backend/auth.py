"""
API Token 認證模組

提供 Bearer Token 認證機制，供 /api/v1/ 路由使用。
Token 來源優先順序：
  1. 環境變數 GRID_API_TOKEN
  2. 本地檔案 .grid_api_token
  3. 生成 ephemeral 隨機 token 並寫入 .grid_api_token（不寫入 log）
"""

import os
import secrets
import logging
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger("GridAPI.Auth")

_security = HTTPBearer(auto_error=False)


def _token_file_path() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    return os.path.join(project_root, ".grid_api_token")


def _write_token_file(token: str) -> str:
    path = _token_file_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _resolve_api_token() -> str:
    token = os.getenv("GRID_API_TOKEN")
    if token:
        logger.info("✅ [Auth] 已從環境變數 GRID_API_TOKEN 載入 API Token")
        return token

    token_file = _token_file_path()
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
            if token:
                logger.info("✅ [Auth] 已從 .grid_api_token 檔案載入 API Token")
                return token
        except Exception as exc:
            logger.warning("⚠️ [Auth] 讀取 .grid_api_token 失敗: %s", exc)

    token = secrets.token_hex(32)
    path = _write_token_file(token)
    logger.warning(
        "⚠️ [Auth] GRID_API_TOKEN 未設定！已生成臨時 Token 並寫入 %s（權限 0600）。"
        "此 Token 僅限本機檔案，請設定環境變數 GRID_API_TOKEN 以供生產使用。",
        path,
    )
    return token


_API_TOKEN: str = _resolve_api_token()


def token_matches(value: str) -> bool:
    if not value:
        return False
    return secrets.compare_digest(value, _API_TOKEN)


async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    if credentials is None or not token_matches(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
