#!/bin/bash

# --- Pi-Web Runner Script ---
# Projet : AI-Helper
# Description : Initialisation et lancement des DEUX services :
#   1) serveur PRIVÉ  (backend/app.py, port FLASK_PORT, derrière Authentik) ;
#   2) SERVICE PUBLIC des albums (backend/public_app.py, loopback:8081 par
#      défaut, publié par Caddy sur albums.<domaine> — cf. docs/albums.md).
#
# UNE SEULE COMMANDE : ./run.sh (le service public est DÉMARRÉ par défaut).
#   - ne PAS lancer le public : AIH_ALBUM_ENABLE=0 ./run.sh (ou dans le .env) ;
#   - relancer le public SEUL, sans toucher au privé : ./run_public.sh.
#
# Chaque service garde son process et son log. L'échec de l'un n'empêche pas
# l'autre de tourner. Relancer ce script redémarre proprement les deux (pkill
# ciblé par service, un seul process de chaque à la fin).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
PUBLIC_SCRIPT="$SCRIPT_DIR/run_public.sh"

# Valeurs par defaut
PROJECT_ROOT="$SCRIPT_DIR"
FLASK_PORT=5000
# Bootstrap admin (fail-closed) : IDs Discord séparés par des virgules.
# Si absent/vide, PERSONNE n'est admin — renseigner obligatoirement le .env
AIH_ADMIN_DISCORD_IDS=""

# 0. Décision « service public » — mappage d'une valeur AIH_ALBUM_ENABLE vers :
#    0 = démarrer, 1 = ne pas démarrer, 2 = valeur inconnue.
#    L'appelant traite 2 en FAIL-CLOSED (service public NON lancé) : jamais
#    d'exposition de la surface publique à cause d'une faute de frappe.
public_service_enabled() {
    case "$(printf '%s' "${1:-1}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on|enable|enabled) return 0 ;;
        0|false|no|off|disable|disabled) return 1 ;;
        *) return 2 ;;
    esac
}

# Mode test (réservé à backend/tests/test_albums_public.py) : charge la
# fonction ci-dessus SANS aucun effet de bord (aucun pkill, aucun lancement).
if [ "${AIH_RUN_SH_SOURCE_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

echo "🚀 Démarrage de AI-Helper..."

# Valeur fournie SUR LA LIGNE DE COMMANDE, capturée AVANT l'export du .env :
# elle reste ainsi prioritaire (sinon un AIH_ALBUM_ENABLE=1 du .env écraserait
# « AIH_ALBUM_ENABLE=0 ./run.sh »).
CLI_ALBUM_ENABLE="${AIH_ALBUM_ENABLE:-}"

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
# Service public des albums : 0 pour NE PAS le démarrer avec ./run.sh (défaut : 1)
# AIH_ALBUM_ENABLE=0
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

# Décision du lancement du service public : APRÈS l'export du .env (une valeur
# posée dans le .env compte) mais la ligne de commande reste prioritaire.
# Défaut 1 = DÉMARRÉ : ./run.sh doit tout relancer au reboot (simplicité), et
# le service public reste cantonné au loopback par défaut (AIH_ALBUM_BIND_HOST).
# Un déploiement qui ne veut pas de cette surface passe AIH_ALBUM_ENABLE=0.
# ⚠ AIH_ALBUM_ENABLE ne concerne QUE run.sh : ./run_public.sh lance toujours le
#   public (c'est un choix explicite).
ALBUM_ENABLE_VALUE="$(printf '%s' "${CLI_ALBUM_ENABLE:-${AIH_ALBUM_ENABLE:-1}}" | tr '[:upper:]' '[:lower:]')"
PUBLIC_ENABLED=0
public_service_enabled "$ALBUM_ENABLE_VALUE"
case $? in
    0) PUBLIC_ENABLED=1 ;;
    1) PUBLIC_ENABLED=0 ;;
    *)
        PUBLIC_ENABLED=0
        echo "⚠ AIH_ALBUM_ENABLE='$ALBUM_ENABLE_VALUE' non reconnu → service public NON démarré."
        echo "  Valeurs acceptées : 1/true/yes/on (démarrer) — 0/false/no/off (ne pas démarrer)."
        ;;
esac

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

# 3. Lancement du serveur PRIVÉ
#    pkill CIBLÉ sur « backend/app.py » uniquement : ces patterns ne matchent
#    PAS « backend/public_app.py » → le service public n'est jamais tué ici.
echo "🧹 Nettoyage des anciens processus privés (le public n'est PAS touché)..."
pkill -f "python3 backend/app.py" || true
pkill -f "$VENV_PATH/bin/python backend/app.py" || true

echo "🌐 Lancement du serveur privé sur 0.0.0.0:$FLASK_PORT..."
nohup "$VENV_PATH/bin/python" backend/app.py > "$LOG_FILE" 2>&1 &

# Petit délai pour laisser le temps au serveur de démarrer
sleep 2

# 4. Statut du PRIVÉ — un échec n'interrompt PAS le script : le service public
#    est quand même tenté, et le résumé final rapporte les deux états.
PRIVATE_OK=0
if ps aux | grep -v grep | grep "$VENV_PATH/bin/python backend/app.py" > /dev/null; then
    PRIVATE_OK=1
    echo "✅ Serveur privé lancé avec succès !"
    echo "Logs disponibles ici : $LOG_FILE"
else
    echo "❌ Échec du lancement du serveur privé. Vérifie les logs : $LOG_FILE"
fi

# 5. Service PUBLIC des albums — délégation à ./run_public.sh.
#    Choix : run_public.sh reste LE lanceur du public (source UNIQUE : venv,
#    export .env, pkill du public seul, nohup, écoute effective). run.sh
#    l'APPELLE en sous-process → aucune logique dupliquée, aucune divergence.
#    Appel dans un « if » : un code retour non nul est CAPTURÉ (PUBLIC_OK=0),
#    jamais propagé — le privé déjà lancé n'est pas affecté.
PUBLIC_OK=0
PUBLIC_LOG_FILE="$PROJECT_ROOT/public_server.log"
if [ "$PUBLIC_ENABLED" -ne 1 ]; then
    echo "⏸  Service public des albums NON lancé (AIH_ALBUM_ENABLE=$ALBUM_ENABLE_VALUE)."
    echo "   → Le stopper s'il tourne déjà : pkill -f backend/public_app.py"
    echo "   → Le lancer malgré tout : ./run_public.sh"
elif [ ! -f "$PUBLIC_SCRIPT" ]; then
    echo "⚠ $PUBLIC_SCRIPT introuvable → service public non lancé (le privé continue)."
else
    echo "↪  Lancement du service public via run_public.sh..."
    if bash "$PUBLIC_SCRIPT"; then
        PUBLIC_OK=1
    else
        echo "❌ Service public en échec — le privé reste UP ; détail : $PUBLIC_LOG_FILE"
        echo "   → Relancer le public seul : ./run_public.sh"
    fi
fi

# 6. RÉSUMÉ — URLs/ports effectifs + logs.
#    L'URL publique effective est relue dans public_server.log (public_app.py
#    retombe sur 127.0.0.1 si AIH_ALBUM_BIND_HOST est invalide) : même
#    extraction que dans run_public.sh → jamais de message trompeur.
PUBLIC_URL=""
if [ "$PUBLIC_OK" -eq 1 ] && [ -f "$PUBLIC_LOG_FILE" ]; then
    PUBLIC_URL="$(grep -o '\[public\] écoute http://[^ ]*' "$PUBLIC_LOG_FILE" | tail -1)"
    PUBLIC_URL="${PUBLIC_URL#*écoute }"
fi

echo
echo "───────────────────────────── RÉSUMÉ ─────────────────────────────"
if [ "$PRIVATE_OK" -eq 1 ]; then
    echo "✅ Privé  : http://127.0.0.1:$FLASK_PORT (bind 0.0.0.0:$FLASK_PORT) — log : $LOG_FILE"
else
    echo "❌ Privé  : ÉCHEC au démarrage — log : $LOG_FILE"
fi
if [ "$PUBLIC_ENABLED" -ne 1 ]; then
    echo "⏸  Public : NON lancé (AIH_ALBUM_ENABLE=$ALBUM_ENABLE_VALUE) — lanceur dédié : ./run_public.sh"
elif [ "$PUBLIC_OK" -eq 1 ]; then
    echo "✅ Public : ${PUBLIC_URL:-http://127.0.0.1:${AIH_ALBUM_PORT:-8081}} — log : $PUBLIC_LOG_FILE"
    echo "            → publié par Caddy sur albums.<domaine> (docs/albums.md)"
else
    echo "❌ Public : ÉCHEC au démarrage — log : $PUBLIC_LOG_FILE (relancer : ./run_public.sh)"
fi
echo "Relancer ./run.sh redémarre les services demandés (un seul process de chaque)."
echo "──────────────────────────────────────────────────────────────────"

# Code retour : 0 seulement si tous les services DEMANDÉS tournent.
if [ "$PRIVATE_OK" -eq 1 ] && { [ "$PUBLIC_ENABLED" -ne 1 ] || [ "$PUBLIC_OK" -eq 1 ]; }; then
    exit 0
fi
exit 1
