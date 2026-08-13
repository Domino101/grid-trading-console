#!/usr/bin/env bash
# Create a brand-new private GitHub repo with a single fresh commit.
#
# This sandbox cannot run the create/push step: it is logged in as the
# Arena GitHub App, which has no createRepository permission and is
# installed only on crypto-trading-open.
#
# Run on a machine where `gh auth status` shows your user (Domino101):
#   ./scripts/publish_private_repo.sh
#   ./scripts/publish_private_repo.sh grid-trading-console Domino101
set -euo pipefail

NAME="${1:-grid-trading-console}"
OWNER="${2:-Domino101}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v gh >/dev/null || ! command -v rsync >/dev/null || ! command -v git >/dev/null; then
  echo "Need git, gh, and rsync on PATH." >&2
  exit 1
fi

LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
if [[ -z "${LOGIN}" ]]; then
  echo "gh cannot read the current user. Run: gh auth login" >&2
  exit 1
fi
if [[ "${LOGIN}" == *"[bot]"* || "${LOGIN}" == "arena-ai-coding-agent"* ]]; then
  echo "Refusing to publish with the Arena bot token (${LOGIN})." >&2
  echo "Log in as ${OWNER} first: gh auth login" >&2
  exit 1
fi

DEST="$(mktemp -d "/tmp/${NAME}.XXXXXX")"
cleanup() { rm -rf "${DEST}"; }
trap cleanup EXIT

echo "Staging a clean tree at ${DEST}"
rsync -a \
  --exclude '.git/' \
  --exclude '.arena/' \
  --exclude 'venv/' \
  --exclude '.venv/' \
  --exclude 'node_modules/' \
  --exclude 'web_ui/frontend/node_modules/' \
  --exclude 'web_ui/frontend/dist/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude '.grid_api_token' \
  --exclude '.env' \
  --exclude 'config/exchanges/' \
  --exclude 'config/active_grids.json' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  "${SRC}/" "${DEST}/"

mkdir -p "${DEST}/config/exchanges" "${DEST}/data" "${DEST}/logs"
cat > "${DEST}/config/exchanges/README.md" <<'EOF'
# Exchange credentials

Do not copy secrets from the old repository. Put untracked YAML or
environment variables here. This directory is excluded from the fresh
private-repo publish script.
EOF

cd "${DEST}"
git init -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-${LOGIN}}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-${LOGIN}@users.noreply.github.com}" \
    commit -m "Initial commit: ${NAME}"

if gh repo view "${OWNER}/${NAME}" >/dev/null 2>&1; then
  echo "Repo ${OWNER}/${NAME} already exists. Add a remote and push manually:" >&2
  echo "  git -C ${DEST} remote add origin git@github.com:${OWNER}/${NAME}.git" >&2
  echo "  git -C ${DEST} push -u origin main" >&2
  trap - EXIT
  echo "Left the staged tree at ${DEST}"
  exit 1
fi

gh repo create "${OWNER}/${NAME}" \
  --private \
  --source=. \
  --remote=origin \
  --push \
  --description "Multi-exchange grid trading engine and control console"

trap - EXIT
echo "Published https://github.com/${OWNER}/${NAME}"
echo "Local clone of the fresh history: ${DEST}"
echo "Secrets were not copied. Recreate config/exchanges and GRID_API_TOKEN on the new host."
