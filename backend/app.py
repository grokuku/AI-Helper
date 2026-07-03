import sqlite3
import io
import json
import random
import time
import secrets
import logging
from datetime import datetime, timedelta
from threading import Thread

from flask import request, jsonify, send_file, send_from_directory, session, redirect, render_template_string, g, Response

from extensions import app, oauth, DB_PATH, MD_PATH, BASE_DIR

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
