#!/bin/bash

# --- Pi-Web Runner Script ---
# Projet : FR.IA-keywords
# Description : Initialisation et lancement du serveur Flask

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# Valeurs par defaut
PROJECT_ROOT="$SCRIPT_DIR"
FLASK_PORT=5000

echo "🚀 Démarrage de FR.IA-keywords..."

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
DISCORD_GUILD_ID=votre_guild_id_optionnelle
# Hugging Face (Recherche Sémantique)
HF_TOKEN=votre_hf_token_ici
EOF
    echo "✅ Fichier .env généré. ⚠ MERCI DE REMPLIR TES CLÉS Discord et HF dans $ENV_FILE"
fi

# Charger les variables du .env vers l'environnement shell
export $(grep -v '^#' "$ENV_FILE" | xargs)

# Lire PROJECT_ROOT et FLASK_PORT depuis .env (avec fallback)
PROJECT_ROOT="${PROJECT_ROOT:-$SCRIPT_DIR}"
FLASK_PORT="${FLASK_PORT:-5000}"

cd "$PROJECT_ROOT"
LOG_FILE="$PROJECT_ROOT/server.log"

# 2. Gestion de l'environnement virtuel (venv)
VENV_PATH="$PROJECT_ROOT/venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "📦 Création de l'environnement virtuel..."
    python3 -m venv "$VENV_PATH"
fi

# Activer le venv et installer les dependencies
source "$VENV_PATH/bin/activate"
echo "⚙ Installation des dépendances..."
pip install --upgrade pip
pip install -r backend/requirements.txt

# 3. Lancement du serveur
echo "🧹 Nettoyage des anciens processus..."
pkill -f "python3 backend/app.py" || true

echo "🌐 Lancement du serveur sur 0.0.0.0:$FLASK_PORT..."
nohup python3 backend/app.py > "$LOG_FILE" 2>&1 &

# Petit délai pour laisser le temps au serveur de démarrer
sleep 2

# 4. Vérification du statut
if ps aux | grep -v grep | grep "python3 backend/app.py" > /dev/null; then
    echo "✅ Serveur lancé avec succès !"
    echo "Logs disponibles ici : $LOG_FILE"
    echo "Accès : http://0.0.0.0:$FLASK_PORT"
else
    echo "❌ Erreur lors du lancement du serveur. Vérifie les logs : $LOG_FILE"
    exit 1
fi