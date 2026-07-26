"""Shared pytest fixtures for the FR.IA backend test suite."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── Environment setup (must happen before ANY backend import) ──────────

# Generate a stable Fernet key for the test session
_TEST_FERNET_KEY = "ZmDfcTF7_60GrrY167zsiPc4z_R0GfV9nJWz3z4YqXc="

# Create a temp DB file BEFORE importing any backend module, because
# extensions.py reads FRIA_DB_PATH at import time.
_TEMP_DB_DIR = tempfile.mkdtemp(prefix="fria_test_")
_TEST_DB_PATH = os.path.join(_TEMP_DB_DIR, "test.db")

os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-for-pytest-0123456789"
os.environ["SECRET_KEY"] = "test-flask-secret-key-for-pytest"
os.environ["ENCRYPTION_KEY"] = _TEST_FERNET_KEY
os.environ["DISCORD_CLIENT_ID"] = "test-discord-id"
os.environ["DISCORD_CLIENT_SECRET"] = "test-discord-secret"
os.environ["FRIA_DB_PATH"] = _TEST_DB_PATH
os.environ["DISCORD_GUILD_ID"] = ""  # no guild restriction in tests

# Make the backend directory importable
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def app():
    """Create and configure the Flask app for testing."""
    # Import app.py — this registers all routes and error handlers.
    # Env vars are already set above so DB_PATH / JWT secrets are correct.
    import app as app_module  # noqa: F401 — side-effect: registers routes
    flask_app = app_module.app

    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

    # Initialize the database schema + default data (idempotent)
    from db.init import _init_db
    _init_db()

    yield flask_app


@pytest.fixture()
def client(app):
    """A Flask test client backed by the configured test app."""
    return app.test_client()


@pytest.fixture()
def app_ctx(app):
    """Push an application context for functions that need it."""
    with app.app_context():
        yield


@pytest.fixture()
def make_token(app):
    """Factory that creates valid JWT access tokens for tests.

    Returns:
        callable: ``make_token(user_id, role='user')`` -> str token
    """
    from auth import create_jwt

    def _make(user_id="test-user-123", role="user"):
        return create_jwt(user_id, role=role)

    return _make


@pytest.fixture()
def auth_headers(make_token):
    """Convenience: returns a dict with a valid Authorization header."""
    token = make_token()
    return {"Authorization": f"Bearer {token}"}