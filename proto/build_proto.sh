#!/usr/bin/env bash
set -euo pipefail

# 導出 Go 二進位工具路徑 (含 protoc-gen-go)
export PATH="${PATH}:/root/go/bin:$(go env GOPATH 2>/dev/null || echo '/root/go')/bin"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_SRC="${PROJECT_ROOT}/proto/trading_core.proto"
PY_OUT="${PROJECT_ROOT}/core/ipc/proto"
GO_OUT="${PROJECT_ROOT}/trading-core-go/pb"

echo "🔨 [Proto Builder] 開始編譯 IPC 通訊協議 Stub..."
echo "   Proto 來源: ${PROTO_SRC}"

# 1. 確保輸出目錄就緒
mkdir -p "${PY_OUT}"
mkdir -p "${GO_OUT}"

# 2. 產生 Python Stub
echo "🐍 [Proto Builder] 正在產生 Python Stub -> ${PY_OUT}..."
protoc --proto_path="${PROJECT_ROOT}/proto" \
       --python_out="${PY_OUT}" \
       "${PROTO_SRC}"

touch "${PY_OUT}/__init__.py"
echo "   ✅ Python Stub 產生成功: ${PY_OUT}/trading_core_pb2.py"

# 3. 產生 Go Stub
echo "🐹 [Proto Builder] 正在產生 Go Stub -> ${GO_OUT}..."
protoc --proto_path="${PROJECT_ROOT}/proto" \
       --go_out="${GO_OUT}" \
       --go_opt=paths=source_relative \
       "${PROTO_SRC}"

echo "   ✅ Go Stub 產生成功: ${GO_OUT}/trading_core.pb.go"

echo "🎉 [Proto Builder] 所有語言通訊 Stub 編譯完成！"
