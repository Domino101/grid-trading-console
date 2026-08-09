# .agents/hooks/pre-commit.sh
# Run before AI agent commits changes
# Blocks unsafe commits

echo "🔍 Pre-commit hook: Checking for unsafe changes..."

BLOCKED=0

# Check for sensitive files
SENSITIVE_FILES=$(git diff --cached --name-only | grep -E '\.(env|key|pem)$|creds\.(md|yaml|json)|secret')
if [ -n "$SENSITIVE_FILES" ]; then
    echo "❌ BLOCKED: Attempting to commit sensitive files:"
    echo "   $SENSITIVE_FILES"
    BLOCKED=1
fi

# Check for hardcoded passwords in config files
if git diff --cached | grep -E '(config/|\.env)' | grep -qiE '(password|secret_key|api_key|private_key)\s*[:=]'; then
    echo "⚠️  WARNING: Possible hardcoded credentials in config files"
    echo "   Please use environment variables instead"
fi

# Check that paper_trading wasn't silently changed to false
if git diff --cached | grep -qiE 'paper_trading.*false'; then
    echo "⚠️  WARNING: paper_trading changed to false"
    echo "   Confirm this is intentional before committing"
fi

# Check for print() in production code
PRINT_LINES=$(git diff --cached | grep '+print(')
if [ -n "$PRINT_LINES" ]; then
    echo "⚠️  WARNING: print() statements detected in changes"
    echo "   Use logging module instead"
fi

if [ "$BLOCKED" -eq 1 ]; then
    exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') PRE_COMMIT: $GIT_AUTHOR_NAME" >> .agents/hooks/hook.log
echo "✅ Pre-commit check passed"
exit 0