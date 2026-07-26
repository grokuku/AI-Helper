"""Flask app instance and shared constants for FR.IA backend."""

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from flask_cors import CORS

from auth import init_oauth

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow overriding the DB path (used by the test suite via FRIA_DB_PATH).
DB_PATH = Path(os.environ.get("FRIA_DB_PATH", str(BASE_DIR / 'keywords.db')))
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

oauth = init_oauth(app)
