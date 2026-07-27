#!/bin/bash
# ============================================================================
# AI-Helper-keywords — Linting JavaScript (ESLint)
# Lance eslint sur web/js/ et frontend/js/ avec --fix
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔧 [ESLint] web/js/..."
npx eslint web/js/ --fix

echo "🔧 [ESLint] frontend/js/..."
npx eslint frontend/js/ --fix

echo "✅ Linting JS terminé."