#!/bin/bash

# --- Public Albums Runner Script ---
# Projet : AI-Helper — chantier « albums publics » (phase 5)
# Description : lancement du SERVICE PUBLIC des albums (backend/public_app.py).
#
# Process SÉPARÉ du serveur privé (backend/app.py) : ce script ne touche
# JAMAIS au privé (le pkill ne cible que « backend/public_app.py »).
# Le service écoute sur 127.0.0.1:${AIH_ALBUM_PORT:-8081} — aucune exposition
# directe : c'est Caddy qui publie le sous-domaine albums.<domaine>
# (cf. docs/albums.md, snippet Caddy à recopier).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
PROJECT_ROOT="$SCRIPT_DIR"

echo "🚀 Démarrage du service PUBLIC des albums (AI-Helper)..."

# 1. Variables d'environnement : le .env est EXPORTÉ dans le shell car
#    public_app.py n'importe pas dotenv (contrairement au privé). Sans .env,
#    les valeurs par défaut du service s'appliquent : webroot
#    <BASE_DIR>/.cache/albums, port 8081, bind 127.0.0.1.
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "⚠ Fichier .env absent : valeurs par défaut utilisées (AIH_ALBUM_* non chargées)."
fi

cd "$PROJECT_ROOT"

# 2. Environnement virtuel — même venv que le privé (créé par ./run.sh).
#    Test de VALIDITÉ (pas simple existence) : un venv cassé est recréé au
#    lieu de taper pip sur le python système (« externally-managed-environment »).
VENV_PATH="$PROJECT_ROOT/venv"
if [ ! -x "$VENV_PATH/bin/pip" ] || [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "📦 venv absent ou cassé — recréation..."
    rm -rf "$VENV_PATH"
    if ! python3 -m venv "$VENV_PATH" 2>/dev/null; then
        echo "❌ python3-venv requis : apt install python3-venv python3-full"
        exit 1
    fi
fi

echo "⚙ Vérification des dépendances..."
"$VENV_PATH/bin/pip" install -r backend/requirements.txt || { echo "❌ Installation des dépendances échouée — voir ci-dessus"; exit 1; }

# 3. Lancement du service public
#    Patterns de pkill VOLONTAIREMENT limités à « backend/public_app.py » :
#    le pattern ne matche ni « python backend/app.py » (privé) ni le process
#    courant (bash run_public.sh).
echo "🧹 Nettoyage de l'ancien process PUBLIC (le privé n'est PAS touché)..."
pkill -f "python3 backend/public_app.py" || true
pkill -f "python backend/public_app.py" || true
pkill -f "$VENV_PATH/bin/python backend/public_app.py" || true

PUBLIC_PORT="${AIH_ALBUM_PORT:-8081}"
LOG_FILE="$PROJECT_ROOT/public_server.log"

echo "🌐 Lancement du service public sur 127.0.0.1:$PUBLIC_PORT..."
nohup "$VENV_PATH/bin/python" backend/public_app.py > "$LOG_FILE" 2>&1 &

# Petit délai pour laisser le temps au serveur de démarrer
sleep 2

# 4. Vérification du statut
if ps aux | grep -v grep | grep "$VENV_PATH/bin/python backend/public_app.py" > /dev/null; then
    echo "✅ Service public lancé avec succès !"
    echo "Logs disponibles ici : $LOG_FILE"
    echo "Écoute locale : http://127.0.0.1:$PUBLIC_PORT (PAS d'exposition directe — Caddy publie le sous-domaine, cf. docs/albums.md)"
else
    echo "❌ Erreur lors du lancement du service public. Vérifie les logs : $LOG_FILE"
    exit 1
fi
