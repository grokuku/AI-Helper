"""Routes helpers for FR.IA backend — backward-compatibility re-exports.

This module re-exports all functions that were originally defined here
so that existing imports (`from routes.helpers import X`) continue to work
after the split into backend/security/ and backend/db/ packages.
"""

# ── DB connection & utilities ─────────────────────────────────────────
from db import get_db, _row_get

# ── Crypto ────────────────────────────────────────────────────────────
from security.crypto import (
    encrypt_api_key,
    decrypt_api_key,
    _get_encryption_key,
)

# ── Auth & authorization ──────────────────────────────────────────────
from security.auth import (
    _login_required,
    _admin_required,
    _kw_editor_required,
    _authenticate_via_token,
    _get_current_user_id,
    _sync_session_user,
    is_admin,
    is_kw_editor,
    _privacy_filter,
)

# ── Rate limiting ─────────────────────────────────────────────────────
from security.ratelimit import (
    _rate_limit,
    _check_rate_limit,
    _require_json,
)

# ── DB init & seeds ───────────────────────────────────────────────────
from db.init import (
    _init_db,
    _insert_default_templates,
)

# ── Embeddings ────────────────────────────────────────────────────────
from db.embeddings import (
    _regenerate_keyword_embedding,
    _generate_all_embeddings,
    _get_ollama_config,
)