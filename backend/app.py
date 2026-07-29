import sqlite3
import io
import json
import random
import time
import secrets
import logging
import os
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from threading import Thread

from flask import request, jsonify, send_file, send_from_directory, session, redirect, render_template_string, g, Response

from extensions import app, oauth, DB_PATH, MD_PATH, BASE_DIR

# ── Configuration du logging structuré ────────────────────────────────
# Logs écrits dans backend/logs/app.log avec rotation (10 MB × 5 fichiers).
# Format : timestamp | level | module | message
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

_log_formatter = logging.Formatter(
    fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

_file_handler = RotatingFileHandler(
    filename=os.path.join(LOG_DIR, 'app.log'),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8',
)
_file_handler.setFormatter(_log_formatter)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)
_console_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[_file_handler, _console_handler],
)
logger = logging.getLogger('ai_helper')

# Chargement de la config Ollama stockée en BDD (si présent)
def _load_ollama_config_at_startup():
    try:
        from embeddings import set_config
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM app_settings WHERE key IN ('ollama_url', 'ollama_model')")
        rows = cur.fetchall()
        conn.close()
        cfg = {r[0]: r[1] for r in rows}
        if cfg:
            set_config(ollama_url=cfg.get('ollama_url'), ollama_model=cfg.get('ollama_model'))
    except Exception as e:
        logging.warning(f"Failed to load Ollama config from DB: {e}")

# Import route modules
from routes.helpers import *
from routes.auth import *
from routes.admin import *
from routes.search import *
from routes.keywords import *
from routes.import_export import *
from routes.filters import *
from routes.presets import *
from routes.elements_presets import *
from routes.styles import *
from routes.templates import *
from routes.enhance import *
from routes.generate import *
from routes.export import *
from routes.ideogram import *
from routes.blobby import *
from routes.workflows import *
from routes.files import *

# Initialisation unique de la BDD (schemas + migrations) au demarrage
from routes.helpers import _init_db
_init_db()

# Chargement de la config Ollama stockée en BDD (doit arriver APRES _init_db)
_load_ollama_config_at_startup()

# Démarrer le backup scheduler (lit la config BDD : enabled + interval)
try:
    import sqlite3 as _sqlite3
    _conn = _sqlite3.connect(str(DB_PATH))
    _enabled = _conn.execute("SELECT value FROM app_settings WHERE key = 'backup_enabled'").fetchone()
    _interval = _conn.execute("SELECT value FROM app_settings WHERE key = 'backup_interval'").fetchone()
    _conn.close()
    _backup_on = _enabled and _enabled[0] == '1'
    _backup_interval = int(_interval[0]) if _interval else 24
    if _backup_on:
        from storage import start_backup_scheduler
        start_backup_scheduler(str(DB_PATH), interval_hours=_backup_interval)
        logging.info(f"[backup] Scheduler started (every {_backup_interval}h)")
    else:
        logging.info("[backup] Scheduler disabled (backup_enabled=0)")
except Exception as e:
    logging.warning(f"Failed to start backup scheduler: {e}")

# ── Error handlers globaux ────────────────────────────────────────────

@app.errorhandler(400)
def handle_bad_request(e):
    """Gère les erreurs 400 Bad Request.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 400.
    """
    return jsonify({"error": "Bad Request", "message": str(e.description) if hasattr(e, 'description') else "Requête mal formée", "status_code": 400}), 400


@app.errorhandler(401)
def handle_unauthorized(e):
    """Gère les erreurs 401 Unauthorized.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 401.
    """
    return jsonify({"error": "Unauthorized", "message": str(e.description) if hasattr(e, 'description') else "Authentification requise", "status_code": 401}), 401


@app.errorhandler(403)
def handle_forbidden(e):
    """Gère les erreurs 403 Forbidden.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 403.
    """
    return jsonify({"error": "Forbidden", "message": str(e.description) if hasattr(e, 'description') else "Accès refusé", "status_code": 403}), 403


@app.errorhandler(404)
def handle_not_found(e):
    """Gère les erreurs 404 Not Found.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 404.
    """
    return jsonify({"error": "Not Found", "message": str(e.description) if hasattr(e, 'description') else "Ressource introuvable", "status_code": 404}), 404


@app.errorhandler(429)
def handle_rate_limited(e):
    """Gère les erreurs 429 Too Many Requests.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 429.
    """
    return jsonify({"error": "Too Many Requests", "message": str(e.description) if hasattr(e, 'description') else "Trop de requêtes. Réessayez plus tard.", "status_code": 429}), 429


@app.errorhandler(500)
def handle_internal_server_error(e):
    """Gère les erreurs 500 Internal Server Error.

    Logge l'erreur complète avec traceback pour faciliter le débogage.

    Args:
        e: L'exception ou l'erreur HTTP déclenchée.

    Returns:
        tuple: Une réponse JSON ``(dict, int)`` avec le status code 500.
    """
    logger.error("Erreur 500 — %s\n%s", str(e), traceback.format_exc())
    return jsonify({"error": "Internal Server Error", "message": "Une erreur interne est survenue. Voir les logs pour plus de détails.", "status_code": 500}), 500


# ── Hooks de logging des requêtes ─────────────────────────────────────

@app.before_request
def _log_request_start():
    """Enregistre le timestamp de début de requête pour mesurer la durée."""
    g.request_start_time = time.time()
    logger.debug("→ %s %s", request.method, request.path)


@app.after_request
def _log_request_end(response):
    """Logge la requête terminée (méthode, chemin, status, durée en ms).

    Args:
        response: L'objet Response de Flask à renvoyer au client.

    Returns:
        Response: L'objet Response inchangé (le hook est pass-through).
    """
    duration_ms = 0
    if hasattr(g, 'request_start_time'):
        duration_ms = round((time.time() - g.request_start_time) * 1000, 2)
    logger.info("← %s %s — %d (%.2f ms)", request.method, request.path, response.status_code, duration_ms)
    return response


# ── Fichiers statiques ────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(str(BASE_DIR / 'frontend'), 'index.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(str(BASE_DIR / 'frontend'), path)


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=int(os.environ.get('FLASK_PORT', '5000')), debug=debug)
