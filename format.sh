#!/bin/bash
# ============================================================================
# AI-Helper — Formatage Python (Ruff)
# Lance: ruff check --fix (auto-fix des problèmes) puis ruff format
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔧 [Ruff] Vérification + auto-fix backend/..."
ruff check --fix backend/

echo "🎨 [Ruff] Formatage backend/..."
ruff format backend/

echo "✅ Formatage Python terminé."