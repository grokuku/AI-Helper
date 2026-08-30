"""Routes helpers for AI-Helper backend — backward-compatibility re-exports.

This module re-exports all functions that were originally defined here
so that existing imports (`from routes.helpers import X`) continue to work
after the split into backend/security/ and backend/db/ packages.
"""

# ── DB connection & utilities ─────────────────────────────────────────
from db import _row_get, get_db

# ── Embeddings ────────────────────────────────────────────────────────
from db.embeddings import (
    _generate_all_embeddings,
    _get_ollama_config,
    _regenerate_keyword_embedding,
)

# ── DB init & seeds ───────────────────────────────────────────────────
from db.init import (
    _init_db,
    _insert_default_templates,
)

# ── Auth & authorization ──────────────────────────────────────────────
from security.auth import (
    _admin_required,
    _authenticate_via_token,
    _bootstrap_role,
    _get_current_user_id,
    _kw_editor_required,
    _login_required,
    _privacy_filter,
    _sync_session_user,
    is_admin,
    is_kw_editor,
)

# ── Crypto ────────────────────────────────────────────────────────────
from security.crypto import (
    _get_encryption_key,
    decrypt_api_key,
    encrypt_api_key,
)

# ── Rate limiting ─────────────────────────────────────────────────────
from security.ratelimit import (
    _check_rate_limit,
    _rate_limit,
    _require_json,
)

# Exports publics explicites : ce module est un point de ré-export — les
# noms listés ici sont volontairement « inutilisés » en interne (F401).
__all__ = [
    'get_db', '_row_get',
    'encrypt_api_key', 'decrypt_api_key', '_get_encryption_key',
    '_login_required', '_admin_required', '_kw_editor_required',
    '_authenticate_via_token', '_get_current_user_id', '_sync_session_user',
    '_bootstrap_role', 'is_admin', 'is_kw_editor', '_privacy_filter',
    '_rate_limit', '_check_rate_limit', '_require_json',
    '_init_db', '_insert_default_templates',
    '_regenerate_keyword_embedding', '_generate_all_embeddings', '_get_ollama_config',
]
