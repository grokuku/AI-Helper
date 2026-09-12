#!/bin/bash
# ============================================================================
# AI-Helper-keywords — Linting JavaScript (ESLint)
# Lance eslint sur frontend/js/ avec --fix
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔧 [ESLint] frontend/js/..."
npx eslint frontend/js/ --fix

echo "✅ Linting JS terminé."