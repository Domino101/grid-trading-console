#!/usr/bin/env bash
# ==============================================================================
# 交易系統日誌輪轉與空間維護腳本 (P1-2)
#
# 功能：
# 1. 檢查 logs/ 目錄下超過 20MB 的未輪轉日誌，使用 copytruncate 進行安全輪轉並 gzip 壓縮
# 2. 清理超過 7 天的歷史已壓縮日誌檔 (*.gz, *.log.[0-9]*)
# 3. 嚴格保護 7 天內之交易與稽核日誌 (data/grid_audit.db, *.log)
# 4. 支援 --dry-run 乾跑模式
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOGS_DIR="${PROJECT_ROOT}/logs"
MAX_SIZE_MB=20
RETENTION_DAYS=7
DRY_RUN=false

# 解析命令列參數
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --max-size-mb)
            MAX_SIZE_MB="$2"
            shift 2
            ;;
        --retention-days)
            RETENTION_DAYS="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [--dry-run] [--max-size-mb <MB>] [--retention-days <DAYS>]"
            exit 0
            ;;
        *)
            echo "未知參數: $1"
            exit 1
            ;;
    esac
done

echo "============================================================"
echo "🧹 [Log Maintenance] 開始日誌維護與磁碟空間治理"
echo "  專案路徑: ${PROJECT_ROOT}"
echo "  日誌目錄: ${LOGS_DIR}"
echo "  單檔上限: ${MAX_SIZE_MB}MB"
echo "  保留天數: ${RETENTION_DAYS} 天"
echo "  乾跑模式: ${DRY_RUN}"
echo "============================================================"

if [[ ! -d "${LOGS_DIR}" ]]; then
    echo "⚠️ 日誌目錄不存在: ${LOGS_DIR}，略過。"
    exit 0
fi

MAX_BYTES=$(( MAX_SIZE_MB * 1024 * 1024 ))
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"

# 1. 輪轉大檔 (> MAX_SIZE_MB)
echo "🔍 正在檢查大於 ${MAX_SIZE_MB}MB 的活躍日誌檔..."
ROTATED_COUNT=0

for logfile in "${LOGS_DIR}"/*.log; do
    [[ -f "${logfile}" ]] || continue
    
    # 排除 socket、pid 或非一般檔案
    file_size=$(stat -c%s "${logfile}" 2>/dev/null || stat -f%z "${logfile}" 2>/dev/null || echo 0)
    
    if (( file_size > MAX_BYTES )); then
        size_human=$(numfmt --to=iec-i --suffix=B "${file_size}" 2>/dev/null || echo "${file_size} bytes")
        echo "  📦 發現大檔: $(basename "${logfile}") (${size_human})"
        
        target_archive="${logfile}.${TIMESTAMP}"
        
        if [[ "${DRY_RUN}" == "true" ]]; then
            echo "    [Dry-run] 將執行: copytruncate -> ${target_archive}.gz"
        else
            # copytruncate: 複製當前內容後清空原檔，絕不鎖死現有進程 stdout/stderr
            cp "${logfile}" "${target_archive}"
            : > "${logfile}"
            gzip -f "${target_archive}"
            echo "    ✅ 已安全輪轉並壓縮: $(basename "${target_archive}.gz")"
        fi
        ROTATED_COUNT=$(( ROTATED_COUNT + 1 ))
    fi
done

echo "ℹ️ 共檢查並輪轉 ${ROTATED_COUNT} 個超額日誌檔。"

# 2. 清理超過 RETENTION_DAYS 天的舊備份日誌 (*.gz, *.log.*, 但絕不刪除 7 天內日誌與當前 .log)
echo "🔍 正在檢查超過 ${RETENTION_DAYS} 天的舊備份存檔..."
PRUNED_COUNT=0

while IFS= read -r -d '' old_file; do
    [[ -f "${old_file}" ]] || continue
    # 雙重防護：絕對不可刪除正在寫入的 active .log
    if [[ "${old_file}" =~ \.log$ ]]; then
        continue
    fi
    
    echo "  🗑️ 發現過期存檔: $(basename "${old_file}")"
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "    [Dry-run] 將刪除: ${old_file}"
    else
        rm -f "${old_file}"
        echo "    ✅ 已安全清除: $(basename "${old_file}")"
    fi
    PRUNED_COUNT=$(( PRUNED_COUNT + 1 ))
done < <(find "${LOGS_DIR}" -type f \( -name "*.gz" -o -name "*.log.*" \) -mtime "+${RETENTION_DAYS}" -print0 2>/dev/null)

echo "ℹ️ 共清理 ${PRUNED_COUNT} 個過期日誌存檔。"
echo "============================================================"
echo "✅ [Log Maintenance] 維護治理程序完成！"
echo "============================================================"
