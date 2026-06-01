"""
API Token 認證模組

提供 Bearer Token 認證機制，供 /api/v1/ 路由使用。
Token 來源優先順序：
  1. 環境變數 GRID_API_TOKEN
  2. 本地檔案 .grid_api_token
  3. 生成 ephemeral 隨機 token + log 嚴重警告
"""

import os
import secrets
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger("GridAPI.Auth")

_security = HTTPBearer()

# ────────────────────────────────────────────────────────
# Token 解析（模組載入時執行一次）
# ────────────────────────────────────────────────────────

def _resolve_api_token() -> str:
    """
    解析 API Token，優先順序：
      1. 環境變數 GRID_API_TOKEN
      2. 專案根目錄 .grid_api_token 檔案
      3. 生成 ephemeral 隨機 token（僅適用於開發環境）
    """
    # 1. 環境變數
    token = os.getenv("GRID_API_TOKEN")
    if token:
        logger.info("✅ [Auth] 已從環境變數 GRID_API_TOKEN 載入 API Token")
        return token

    # 2. 本地檔案
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    token_file = os.path.join(project_root, ".grid_api_token")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r") as f:
                token = f.read().strip()
            if token:
                logger.info("✅ [Auth] 已從 .grid_api_token 檔案載入 API Token")
                return token
        except Exception as e:
            logger.warning(f"⚠️ [Auth] 讀取 .grid_api_token 失敗: {e}")

    # 3. 生成 ephemeral token（開發用途）
    token = secrets.token_hex(32)
    logger.warning(
        "⚠️ [Auth] GRID_API_TOKEN 未設定！已生成臨時 Token（僅本次有效）。"
        "此 Token 僅限單一實例，不支援水平擴展。"
        f"請設定環境變數 GRID_API_TOKEN 以供生產使用。"
    )
    logger.warning(f"🔑 [Auth] 臨時 Token: {token}")
    return token


_API_TOKEN: str = _resolve_api_token()


# ────────────────────────────────────────────────────────
# FastAPI Dependency
# ────────────────────────────────────────────────────────

async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> str:
    """
    驗證 Bearer Token。

    使用 secrets.compare_digest() 做常數時間比較，防止 timing attack。
    回傳已驗證的 token 字串（可用於審計日誌）。
    """
    if not secrets.compare_digest(credentials.credentials, _API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
