#!/usr/bin/env bash
# =============================================================================
# scripts/purge_history.sh — 公開前的 git 歷史淨化（git filter-repo）
#
# 背景（詳見 docs/PUBLIC_RELEASE_AUDIT_20261021.md）：
#   * 兩個來自上游木馬 fork 的惡意 zip（luajit loader）從第一個 commit 起就在歷史裡
#   * .env.example 曾含一個真實樣貌的 GRID_API_TOKEN
#   * docs 內含維運者的真實對外 IP
#   * ~110 MB 的 graphify 每日快照與 config/grid/archived/ 轉儲（皆已 gitignore）
#   只刪 HEAD 的檔案無法讓它們從公開歷史消失，必須重寫並 force-push。
#
# 執行條件：
#   * 先把清理分支（arena/8533ffd4-grid-trading-console）合併進 main。
#   * 在「你自己的機器」執行，gh 已以 repo 擁有者身分登入（不是 Arena bot）。
#   * 安裝 git-filter-repo：pip install git-filter-repo（或 brew install git-filter-repo）
#
# 用法：
#   ./scripts/purge_history.sh --dry-run        # 只重寫到暫存目錄並列出驗證結果，不推送
#   ./scripts/purge_history.sh                  # 重寫 + force-push 所有分支與標籤
#   REMOTE_URL=<url> ./scripts/purge_history.sh # 指定其他遠端（測試用）
#
# 本腳本永遠在「全新的暫存 clone」上操作，不會碰你目前的工作目錄。
# =============================================================================
set -euo pipefail

REMOTE_URL="${REMOTE_URL:-https://github.com/Domino101/grid-trading-console.git}"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# ---- 必須從歷史移除的路徑（與 HEAD 清理 commit 一致）---------------------------
PURGE_PATHS=(
  core/services/volume_maker/crypto_open_trading_v2.8.zip
  core/domain/value_objects/crypto-trading-open_3.0.zip
)
PURGE_PATH_REGEX=(
  '^graphify-out/2026-[0-9]{2}-[0-9]{2}/'
  '^graphify-out/cache/'
  '^config/grid/archived/'
)
# ---- 必須從所有 blob 內容抹除的字串 ---------------------------------------------
# 使用「通用 regex」而非具體值，讓本腳本與 docs 不必載明敏感值，且重寫後本腳本內容不變（可重複執行）。
# 格式：regex:<pattern>==><replacement>（git filter-repo --replace-text 語法，Python regex）
#   * 範本中曾出現的真實樣貌 Web UI token（整段歷史僅此一個 64-hex 值）
#   * 維運者的 HiNet 動態對外 IP（整段歷史中 36.224.x.x 僅對應維運者的兩個舊 IP，含已刪除的 reset_and_test.py）
REPLACEMENTS=$(cat <<'EOF_R'
regex:GRID_API_TOKEN=[0-9a-fA-F]{64}==>GRID_API_TOKEN=<REDACTED_GRID_API_TOKEN>
regex:36\.224\.[0-9]{1,3}\.[0-9]{1,3}==><REDACTED_PUBLIC_IP>
EOF_R
)

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少指令：$1" >&2; exit 1; }; }
need git
git filter-repo --version >/dev/null 2>&1 || {
  echo "缺少 git filter-repo。安裝：pip install git-filter-repo（或 brew install git-filter-repo）" >&2
  exit 1
}

if [[ "$REMOTE_URL" == *github.com* && "$DRY_RUN" -eq 0 ]]; then
  need gh
  LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
  if [[ -z "$LOGIN" || "$LOGIN" == *"[bot]"* || "$LOGIN" == arena-ai-coding-agent* ]]; then
    echo "gh 目前登入身分為 '${LOGIN:-<none>}'。請以 repo 擁有者執行 gh auth login 後再試。" >&2
    exit 1
  fi
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/grid-history-purge.XXXXXX")"
echo "▶ 暫存工作目錄：$WORK"
cd "$WORK"

# 只抓 heads + tags（不抓 refs/pull/*，GitHub 不允許回寫那些 ref）
git init -q --bare repo.git
cd repo.git
git remote add origin "$REMOTE_URL"
git fetch -q origin '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'
echo "▶ 已抓取 $(git for-each-ref --format='%(refname)' refs/heads | wc -l | tr -d ' ') 個分支、$(git for-each-ref --format='%(refname)' refs/tags | wc -l | tr -d ' ') 個標籤"

# 前置檢查：main 的 HEAD 必須已經不含惡意 zip（代表清理 commit 已合併）
for p in "${PURGE_PATHS[@]}"; do
  if git cat-file -e "refs/heads/main:$p" 2>/dev/null; then
    echo "✖ main 的 HEAD 仍含 $p —— 請先把清理分支合併進 main 再執行本腳本。" >&2
    exit 1
  fi
done

BEFORE_SIZE="$(du -sh . | cut -f1)"
BEFORE_COMMITS="$(git rev-list --all | wc -l | tr -d ' ')"

printf '%s\n' "$REPLACEMENTS" > ../replacements.txt
FILTER_ARGS=(--force --invert-paths)
for p in "${PURGE_PATHS[@]}"; do FILTER_ARGS+=(--path "$p"); done
for r in "${PURGE_PATH_REGEX[@]}"; do FILTER_ARGS+=(--path-regex "$r"); done
FILTER_ARGS+=(--replace-text ../replacements.txt)

echo "▶ 執行 git filter-repo ${FILTER_ARGS[*]}"
git filter-repo "${FILTER_ARGS[@]}"
git reflog expire --expire=now --all 2>/dev/null || true
git gc -q --prune=now --aggressive

AFTER_SIZE="$(du -sh . | cut -f1)"
AFTER_COMMITS="$(git rev-list --all | wc -l | tr -d ' ')"

echo
echo "================ 驗證 ================"
echo "repo 大小：$BEFORE_SIZE → $AFTER_SIZE；commit 數：$BEFORE_COMMITS → $AFTER_COMMITS"
FAIL=0
for p in "${PURGE_PATHS[@]}"; do
  if git log --all --format=%H -- "$p" | grep -q .; then echo "✖ 歷史仍含 $p"; FAIL=1; else echo "✔ 歷史已無 $p"; fi
done
for r in "${PURGE_PATH_REGEX[@]}"; do
  if git log --all --name-only --format= | grep -Eq "$r"; then echo "✖ 歷史仍含符合 $r 的路徑"; FAIL=1; else echo "✔ 歷史已無 $r"; fi
done
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  pattern="${line%%==>*}"
  pattern="${pattern#regex:}"
  if git grep -q -E -e "$pattern" $(git rev-list --all) -- . ':!scripts/purge_history.sh' ':!scripts/public_repo_guard.sh' 2>/dev/null; then
    echo "✖ 歷史 blob 仍有符合 /$pattern/ 的內容"; FAIL=1
  else
    echo "✔ 歷史 blob 已無符合 /$pattern/ 的內容"
  fi
done <<< "$REPLACEMENTS"
if [[ "$FAIL" -ne 0 ]]; then echo "驗證失敗，未推送。暫存目錄保留於 $WORK" >&2; exit 1; fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo
  echo "（dry-run）未推送。重寫後的 bare repo 位於 $WORK/repo.git，可自行檢視："
  echo "  git -C $WORK/repo.git log --oneline | head"
  exit 0
fi

echo
echo "▶ force-push 所有分支與標籤到 $REMOTE_URL"
git remote add origin "$REMOTE_URL" 2>/dev/null || git remote set-url origin "$REMOTE_URL"
git push --force origin 'refs/heads/*:refs/heads/*'
git push --force origin 'refs/tags/*:refs/tags/*'

cat <<EOF

✅ 歷史已重寫並推送。接下來務必完成：

1) 每一台既有 clone（含正式機 /root/grid-trading-console）重新對齊，不要 merge/pull：
     cd /root/grid-trading-console
     cp -a . ../grid-trading-console.bak-\$(date +%F)        # 先完整備份（含 config/、data/、.env）
     git fetch origin
     git checkout main
     git reset --hard origin/main
     # 被 gitignore、但 reset 會刪掉的本機檔案，從備份還原：
     cp ../grid-trading-console.bak-*/config/exchanges/*_config.yaml config/exchanges/ 2>/dev/null || true
     cp -a ../grid-trading-console.bak-*/config/grid/archived config/grid/ 2>/dev/null || true
     git status --short   # 應為乾淨（真實金鑰檔已被 ignore）

2) GitHub 端仍可能透過舊 PR ref（refs/pull/1、refs/pull/2）或快取保留舊物件。
   到 https://support.github.com/ 提交請求：「remove cached views and references to
   the sensitive data in pull requests」，附上 repo 名稱與舊 commit SHA（見審查報告）。
   或採取最徹底做法：刪除 repo 後用重寫過的 clone 以同名重新建立。

3) 無論如何，立刻輪換曾出現在 .env.example 的 GRID_API_TOKEN。

4) 以上完成並確認後，再把 repo 切為 public：
     gh repo edit Domino101/grid-trading-console --visibility public --accept-visibility-change-consequences
EOF
