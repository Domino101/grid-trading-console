#!/usr/bin/env bash
# =============================================================================
# scripts/public_repo_guard.sh — 公開 repo 衛生守門（CI 與本機皆可執行）
#
# 攔截「公開前安全審查」(docs/PUBLIC_RELEASE_AUDIT_20261021.md) 發現過的所有缺陷類型：
#   G1  已追蹤檔案卻符合 .gitignore（例如 config/grid/archived/、graphify-out/2026-*/）
#   G2  真實憑證檔被追蹤（config/exchanges/*_config.yaml、.env、.grid_api_token、.secrets/）
#   G3  二進位 payload / 壓縮檔 / Windows 執行檔進入原始碼樹
#   G4  高熵 64-hex 權杖、0x 私鑰、常見雲端/平台金鑰格式、PEM 私鑰
#   G5  .env.example 內出現非佔位符的 GRID_API_TOKEN
#   G6  維運者真實對外 IP 殘留（已知值）
#
# 用法：scripts/public_repo_guard.sh            # 檢查 git 追蹤檔案（HEAD + index）
# 回傳 0 = 通過；1 = 有違規（逐條列出）。
# =============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAIL=0
say_fail() { echo "✖ $1"; FAIL=1; }
say_ok()   { echo "✔ $1"; }

# 不納入文字掃描的檔案（鎖檔 / 產生物件 / 二進位）
TEXT_EXCLUDES=(
  ':!web_ui/frontend/package-lock.json'
  ':!trading-core-go/go.sum'
  ':!*.pb.go'
  ':!graphify-out/**'
  ':!*.png' ':!*.jpg' ':!*.svg' ':!*.ico'
)

# ---- G1 ----------------------------------------------------------------------
IGNORED_TRACKED="$(git ls-files -i -c --exclude-standard || true)"
if [[ -n "$IGNORED_TRACKED" ]]; then
  say_fail "G1 以下檔案已追蹤卻符合 .gitignore（請 git rm --cached）："; echo "$IGNORED_TRACKED" | sed 's/^/     /'
else say_ok "G1 無「已追蹤但被 ignore」的檔案"; fi

# ---- G2 ----------------------------------------------------------------------
CRED_FILES="$(git ls-files | grep -E '^(\.env|\.env\.[^e].*|\.grid_api_token|\.secrets/.*)$|^config/exchanges/[^/]+_config\.yaml$|^config/(active_grids|active_dca)\.json$|\.(pem|p12|pfx|key)$|id_rsa|id_ed25519' || true)"
if [[ -n "$CRED_FILES" ]]; then
  say_fail "G2 疑似憑證/狀態檔被追蹤："; echo "$CRED_FILES" | sed 's/^/     /'
else say_ok "G2 無憑證檔被追蹤（config/exchanges 只有 *.example.yaml）"; fi

# ---- G3 ----------------------------------------------------------------------
BIN_FILES="$(git ls-files | grep -iE '\.(zip|7z|rar|tar|gz|tgz|exe|dll|msi|scr|bat|cmd|com|pif|jar|so|dylib|pyc|whl)$' || true)"
if [[ -n "$BIN_FILES" ]]; then
  say_fail "G3 原始碼樹含壓縮檔/執行檔（上游木馬 fork 曾以此散佈 luajit loader）："; echo "$BIN_FILES" | sed 's/^/     /'
else say_ok "G3 無壓縮檔 / 執行檔"; fi
# 另外攔截沒有副檔名但帶 PE / ZIP 魔術數字的追蹤檔
MAGIC_HITS=""
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  sig="$(head -c 4 "$f" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
  case "$sig" in
    4d5a*|504b0304|504b0506|7f454c46) MAGIC_HITS+="     $f ($sig)"$'\n' ;;
  esac
done < <(git ls-files | grep -vE '\.(png|jpg|jpeg|gif|ico|woff2?|ttf|otf|svg)$')
if [[ -n "$MAGIC_HITS" ]]; then say_fail "G3b 含 PE/ZIP/ELF 魔術數字的追蹤檔："; printf '%s' "$MAGIC_HITS"; else say_ok "G3b 無隱藏的 PE/ZIP/ELF 二進位"; fi

# ---- G4 ----------------------------------------------------------------------
HEX64="$(git grep -nIE '\b[0-9a-fA-F]{64}\b' -- . "${TEXT_EXCLUDES[@]}" \
  | grep -vE '(sha256|SHA256|sha-256|hash|digest|checksum|integrity|hashes|zip sha|PUBLIC_RELEASE_AUDIT|purge_history\.sh|public_repo_guard\.sh)' || true)"
if [[ -n "$HEX64" ]]; then say_fail "G4a 出現 64-hex 高熵字串（可能是 token / 私鑰）："; echo "$HEX64" | cut -c1-160 | sed 's/^/     /'; else say_ok "G4a 無未標註的 64-hex 權杖"; fi

PK="$(git grep -nIE '0x[0-9a-fA-F]{64}\b|-----BEGIN (RSA |EC |OPENSSH |PGP |)PRIVATE KEY-----' -- . "${TEXT_EXCLUDES[@]}" || true)"
if [[ -n "$PK" ]]; then say_fail "G4b 出現私鑰格式："; echo "$PK" | cut -c1-160 | sed 's/^/     /'; else say_ok "G4b 無 0x 私鑰 / PEM 私鑰"; fi

PLATFORM="$(git grep -nIE '\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|[0-9]{8,10}:[A-Za-z0-9_-]{35})\b' -- . "${TEXT_EXCLUDES[@]}" || true)"
if [[ -n "$PLATFORM" ]]; then say_fail "G4c 出現平台金鑰格式（OpenAI/GitHub/AWS/Slack/Google/Telegram）："; echo "$PLATFORM" | cut -c1-160 | sed 's/^/     /'; else say_ok "G4c 無平台金鑰格式"; fi

# ---- G5 ----------------------------------------------------------------------
if [[ -f .env.example ]]; then
  TOKEN_LINE="$(grep -E '^GRID_API_TOKEN=' .env.example || true)"
  if echo "$TOKEN_LINE" | grep -qE '^GRID_API_TOKEN=[0-9a-fA-F]{32,}$'; then
    say_fail "G5 .env.example 的 GRID_API_TOKEN 看起來是真實值：$TOKEN_LINE"
  else say_ok "G5 .env.example 的 GRID_API_TOKEN 為佔位符"; fi
  if git grep -nIE '^[A-Z0-9_]*(KEY|SECRET|PASSPHRASE|TOKEN|PASSWORD)=' -- .env.example \
       | grep -vE '=(\s*$|your_|replace_|<|xxx|changeme|example)' | grep -vE 'GRID_API_TOKEN=replace_' | grep -q .; then
    say_fail "G5b .env.example 內有非佔位符的金鑰值："
    git grep -nIE '^[A-Z0-9_]*(KEY|SECRET|PASSPHRASE|TOKEN|PASSWORD)=' -- .env.example | grep -vE '=(\s*$|your_|replace_|<|xxx|changeme|example)' | sed 's/^/     /'
  fi
fi

# ---- G6 ----------------------------------------------------------------------
# 維運者的 HiNet 動態 IP 全落在同一個 /16 網段（歷史中該網段沒有其他位址），以網段 regex 檢查，避免在守門腳本中載明真實值。
IPHIT="$(git grep -nIE '36\.224\.[0-9]{1,3}\.[0-9]{1,3}' -- . ':!scripts/purge_history.sh' ':!scripts/public_repo_guard.sh' || true)"
if [[ -n "$IPHIT" ]]; then
  say_fail "G6 維運者真實對外 IP 殘留："; echo "$IPHIT" | cut -c1-160 | sed 's/^/     /'
else say_ok "G6 無已知真實 IP 殘留"; fi

echo
if [[ "$FAIL" -ne 0 ]]; then
  echo "public_repo_guard: 發現違規，請依 docs/PUBLIC_RELEASE_AUDIT_20261021.md 處理。" >&2
  exit 1
fi
echo "public_repo_guard: 全部通過"
