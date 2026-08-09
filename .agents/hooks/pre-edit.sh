#!/bin/bash
# .agents/hooks/pre-edit.sh
# Run before AI agent modifies any file
# Validates that the agent has proper context before editing

echo "🔍 Pre-edit hook: Validating agent context..."

# Check if agent is working on a financial/trading file
if echo "$1" | grep -qE "(core/services|core/adapters|run_|grid_|volume_maker)"; then
    echo "⚠️  TRADING FILE DETECTED: $1"
    echo "   Agent MUST verify paper_trading status before any edits"
    echo "   Agent MUST ensure risk checks are not bypassed"
fi

# Check for config file edits
if echo "$1" | grep -qE "(config/|\.env)"; then
    echo "⚠️  CONFIG FILE DETECTED: $1"
    echo "   Agent MUST NOT hardcode credentials"
    echo "   Agent MUST NOT change paper_trading without user confirmation"
fi

# Log the edit
echo "$(date '+%Y-%m-%d %H:%M:%S') PRE_EDIT: $1" >> .agents/hooks/hook.log
echo "✅ Pre-edit check passed"