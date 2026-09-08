#!/bin/bash

# --- Pi-Web Runner Script ---
# Projet : AI-Helper
# Description : Initialisation et lancement du serveur Flask

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# Valeurs par defaut
PROJECT_ROOT="$SCRIPT_DIR"
FLASK_PORT=5000
# Bootstrap admin (fail-closed) : IDs Discord séparés par des virgules.
# Si absent/vide, PERSONNE n'est admin — renseigner obligatoirement le .env
AIH_ADMIN_DISCORD_IDS=""

echo "🚀 Démarrage de AI-Helper..."

# 1. Gestion des variables d'environnement
if [ ! -f "$ENV_FILE" ]; then
    echo "⚠ Fichier .env manquant. Création d'un nouveau fichier..."
    cat <<EOF > "$ENV_FILE"
# Configuration Flask
SECRET_KEY=$(openssl rand -hex 24)
FLASK_PORT=5000
# Discord OAuth2 (À remplir dans le fichier .env après création de l'app)
DISCORD_CLIENT_ID=votre_client_id_ici
DISCORD_CLIENT_SECRET=votre_client_secret_ici
# Hugging Face (Recherche Sémantique)
HF_TOKEN=votre_hf_token_ici
# Admin Discord IDs (séparés par des virgules) — si vide/absente, PERSONNE n'est admin
# Exemple : AIH_ADMIN_DISCORD_IDS=123456789012345678,987654321098765432
AIH_ADMIN_DISCORD_IDS=votre_id_discord_ici
EOF
    echo "✅ Fichier .env généré. ⚠ MERCI DE REMPLIR TES CLÉS Discord, HF et ton ID Discord (AIH_ADMIN_DISCORD_IDS) dans $ENV_FILE"
fi

# Charger les variables du .env vers l'environnement shell
export $(grep -v '^#' "$ENV_FILE" | xargs)

# Lire PROJECT_ROOT et FLASK_PORT depuis .env (avec fallback)
PROJECT_ROOT="${PROJECT_ROOT:-$SCRIPT_DIR}"
FLASK_PORT="${FLASK_PORT:-5000}"

# S'assurer que la variable admin est définie (vide si absente du .env = fail-closed)
AIH_ADMIN_DISCORD_IDS="${AIH_ADMIN_DISCORD_IDS:-}"
export AIH_ADMIN_DISCORD_IDS

cd "$PROJECT_ROOT"
LOG_FILE="$PROJECT_ROOT/server.log"

# 2. Gestion de l'environnement virtuel (venv)
# Test de VALIDITÉ (pas simple existence) : un venv cassé — ex. créé sans pip
# car python3-venv absent au moment de la création — est supprimé puis recréé
# au lieu d'être réutilisé silencieusement (ce qui faisait taper pip sur le pip
# système -> « externally-managed-environment »).
VENV_PATH="$PROJECT_ROOT/venv"
if [ ! -x "$VENV_PATH/bin/pip" ] || [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "📦 venv absent ou cassé — recréation..."
    rm -rf "$VENV_PATH"
    if ! python3 -m venv "$VENV_PATH" 2>/dev/null; then
        echo "❌ python3-venv requis : apt install python3-venv python3-full"
        exit 1
    fi
fi

# Installation des dépendances : chemins EXPLICITES vers le pip du venv
# (aucune dépendance à l'activation du venv)
echo "⚙ Installation des dépendances..."
"$VENV_PATH/bin/pip" install --upgrade pip
"$VENV_PATH/bin/pip" install -r backend/requirements.txt || { echo "❌ Installation des dépendances échouée — voir ci-dessus"; exit 1; }

# 3. Lancement du serveur
echo "🧹 Nettoyage des anciens processus..."
pkill -f "python3 backend/app.py" || true
pkill -f "$VENV_PATH/bin/python backend/app.py" || true

echo "🌐 Lancement du serveur sur 0.0.0.0:$FLASK_PORT..."
nohup "$VENV_PATH/bin/python" backend/app.py > "$LOG_FILE" 2>&1 &

# Petit délai pour laisser le temps au serveur de démarrer
sleep 2

# 4. Vérification du statut
if ps aux | grep -v grep | grep "$VENV_PATH/bin/python backend/app.py" > /dev/null; then
    echo "✅ Serveur lancé avec succès !"
    echo "Logs disponibles ici : $LOG_FILE"
    echo "Accès : http://0.0.0.0:$FLASK_PORT"
else
    echo "❌ Erreur lors du lancement du serveur. Vérifie les logs : $LOG_FILE"
    exit 1
fi