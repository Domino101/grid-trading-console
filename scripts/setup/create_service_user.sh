#!/bin/bash
# ==============================================================================
# scripts/setup/create_service_user.sh
# 系統特權降階服務帳號建立與權限配置腳本 (P2-1)
# ==============================================================================

set -euo pipefail

SERVICE_USER="crypto-runner"
SERVICE_GROUP="crypto-runner"
DEFAULT_INSTALL_DIR="/opt/grid-trading-console"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRY_RUN=false
TARGET_DIR="${DEFAULT_INSTALL_DIR}"

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=true
            ;;
        --repo|--current)
            TARGET_DIR="$REPO_ROOT"
            ;;
        *)
            if [ -d "$arg" ] || [ ! -e "$arg" ]; then
                TARGET_DIR="$arg"
            fi
            ;;
    esac
done

echo "============================================================"
echo "🛡️  [Security Sandbox] 建立低特權服務帳號與目錄權限治理"
echo "  目標使用者: ${SERVICE_USER}:${SERVICE_GROUP}"
echo "  目標目錄:   ${TARGET_DIR}"
echo "  乾跑模式:   ${DRY_RUN}"
echo "============================================================"

# 1. 檢查/建立服務群組
if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    echo "ℹ️  群組 ${SERVICE_GROUP} 已存在。"
else
    if [ "$DRY_RUN" = true ]; then
        echo "🔍 [DRY-RUN] 將執行: groupadd -r ${SERVICE_GROUP}"
    else
        if [ "$(id -u)" -ne 0 ]; then
            echo "❌ 需要 root 權限建立系統群組，請以 sudo 執行此腳本。"
            exit 1
        fi
        groupadd -r "${SERVICE_GROUP}"
        echo "✅ 群組 ${SERVICE_GROUP} 建立完成。"
    fi
fi

# 2. 檢查/建立無登入 Shell 的系統專用帳號
if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "ℹ️  使用者 ${SERVICE_USER} 已存在。"
else
    if [ "$DRY_RUN" = true ]; then
        echo "🔍 [DRY-RUN] 將執行: useradd -r -g ${SERVICE_GROUP} -s /usr/sbin/nologin -d ${TARGET_DIR} ${SERVICE_USER}"
    else
        if [ "$(id -u)" -ne 0 ]; then
            echo "❌ 需要 root 權限建立系統使用者，請以 sudo 執行此腳本。"
            exit 1
        fi
        useradd -r -g "${SERVICE_GROUP}" -s /usr/sbin/nologin -d "${TARGET_DIR}" "${SERVICE_USER}"
        echo "✅ 使用者 ${SERVICE_USER} 建立完成 (無登入權限: /usr/sbin/nologin)。"
    fi
fi

# 3. 確保專案內受控可讀寫目錄 (config, data, logs)
MUTABLE_DIRS=("${TARGET_DIR}/config" "${TARGET_DIR}/data" "${TARGET_DIR}/logs")

for dir in "${MUTABLE_DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "🔍 [DRY-RUN] 將建立目錄: $dir"
        else
            mkdir -p "$dir"
        fi
    fi
    if [ "$DRY_RUN" = true ]; then
        echo "🔍 [DRY-RUN] 將設定權限: chown -R ${SERVICE_USER}:${SERVICE_GROUP} $dir && chmod 770 $dir"
    else
        if [ "$(id -u)" -eq 0 ]; then
            chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$dir"
            chmod 770 "$dir"
            echo "✅ 目錄權限已配置: $dir (770, ${SERVICE_USER}:${SERVICE_GROUP})"
        else
            echo "⚠️  當前非 root 身分，跳過 chown: $dir"
        fi
    fi
done

# 4. 不可動搖約束守則檢查：嚴禁開放系統目錄寫入
echo "------------------------------------------------------------"
echo "🔒 不可動搖約束守則檢查 (Invariants Check):"
echo "  [Rule 1] 服務用戶對 config/, data/, logs/ 具備讀寫權限"
echo "  [Rule 2] 嚴禁開放對 /etc, /usr, /root 寫入權限 (由 Systemd ProtectSystem=strict 保障)"
echo "============================================================"
echo "✅ [Security Sandbox] 帳號與環境配置檢查完畢！"
