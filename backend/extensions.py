"""Flask app instance and shared constants for AI-Helper backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from auth import init_oauth
from flask import Flask, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow overriding the DB path (used by the test suite via AIH_DB_PATH).
DB_PATH = Path(os.environ.get("AIH_DB_PATH", str(BASE_DIR / 'keywords.db')))
MD_PATH = BASE_DIR / 'Keywords-Complete.md'

app = Flask(__name__)
import secrets as _secrets

_secret_file = BASE_DIR / '.secret_key'
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif _secret_file.exists():
    app.secret_key = _secret_file.read_text().strip()
else:
    _generated_key = _secrets.token_hex(32)
    _secret_file.write_text(_generated_key)
    app.secret_key = _generated_key
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Reverse proxy (Caddy + Authentik) ─────────────────────────────────
# TLS terminé au proxy : ProxyFix fait voir les requêtes comme https à
# Flask (request.is_secure == True), indispensable pour les cookies Secure.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ── Cookies de session sécurisés ──────────────────────────────────────
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.before_request
def _set_session_cookie_secure():
    """Active Secure sur le cookie de session quand la requête est en HTTPS.

    Derrière Caddy (via ProxyFix) ``request.is_secure`` vaut True → cookie
    Secure. En HTTP local (dev, sans proxy) → False → le login continue de
    fonctionner. Échappatoire explicite : ``AIH_INSECURE_COOKIES=1``.
    """
    if os.environ.get("AIH_INSECURE_COOKIES", "") == "1":
        app.config["SESSION_COOKIE_SECURE"] = False
    else:
        app.config["SESSION_COOKIE_SECURE"] = request.is_secure


oauth = init_oauth(app)
