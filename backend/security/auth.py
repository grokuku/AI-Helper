"""Authentication and authorization helpers.

Provides login guards (session or API token), role checks (admin, kw_editor),
and the privacy filter used by keyword queries.
"""

import contextlib
import logging
import os
import time
from datetime import datetime as _dt

from auth import verify_jwt
from db import get_db
from flask import g, jsonify, request, session

# ── User identification ────────────────────────────────────────────────

def _get_current_user_id() -> str | None:
    """Retourne l'ID de l'utilisateur connecté.

    Ordre de résolution :
        1. ``Flask g.user_id`` (positionné par ``_login_required``)
        2. Session Flask (connexion Discord)
        3. Bearer token (JWT ou API token legacy)

    Returns:
        str | None: L'ID utilisateur, ou ``None`` si non authentifié.
    """
    gid = getattr(g, 'user_id', None)
    if gid:
        return gid
    user = session.get("user")
    if user:
        return user["id"]
    return _authenticate_via_token()


def _authenticate_via_token() -> str | None:
    """Authentifie la requête via un Bearer token (JWT ou API legacy).

    Accepte deux types de tokens :
        - JWT token (via ``verify_jwt``)
        - API token legacy (``aih_...``), stocké HASHÉ (SHA-256) en BDD.
          Les anciens tokens stockés en clair restent valides : ils sont
          migrés vers le hash à la 1re utilisation (sans invalidation).

    Expiration : les tokens créés depuis le hashage expirent après
    ``AIH_TOKEN_MAX_AGE_DAYS`` jours (défaut 365, 0 = jamais). Les tokens
    legacy (sans date de création) n'expirent pas (migration transparente).

    Returns:
        str | None: L'ID utilisateur si le token est valide, sinon ``None``.
    """
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:]

    # 1) Essayer le JWT
    payload = verify_jwt(token)
    if payload and payload.get('type') == 'access':
        return payload['sub']

    # 2) API token (aih_...)
    try:
        import hashlib
        conn = get_db()

        # 2a) Hash SHA-256 (format actuel)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        row = conn.execute(
            "SELECT id, api_token_created_at FROM users WHERE api_token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row:
            conn.close()
            if _api_token_expired(row['api_token_created_at']):
                return None
            return row['id']

        # 2b) Legacy : token en clair → migration paresseuse vers le hash
        row = conn.execute(
            "SELECT id FROM users WHERE api_token = ?", (token,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET api_token_hash = ?, api_token_created_at = datetime('now'), "
                "api_token = NULL WHERE id = ?",
                (token_hash, row['id']),
            )
            conn.commit()
            conn.close()
            return row['id']
        conn.close()
        return None
    except Exception:
        return None


def _api_token_expired(created_at) -> bool:
    """True si le token a dépassé AIH_TOKEN_MAX_AGE_DAYS jours (0 = jamais)."""
    max_age_days = int(os.environ.get("AIH_TOKEN_MAX_AGE_DAYS", "365"))
    if max_age_days <= 0 or not created_at:
        return False  # pas de date (token legacy) ou expiration désactivée
    try:
        created = _dt.fromisoformat(str(created_at).replace('Z', '+00:00').split('+')[0])
        age_days = (_dt.utcnow() - created).days
    except Exception:
        return False
    return age_days > max_age_days


# ── Admin bootstrap (env AIH_ADMIN_DISCORD_IDS) ──────────────────────

_ADMIN_BOOTSTRAP_WARNED_AT = 0.0
_ADMIN_BOOTSTRAP_WARN_INTERVAL = 3600.0  # 1h — rate-limit du WARNING


def _bootstrap_role(user_id: str) -> str:
    """Détermine le rôle d'un utilisateur via la variable d'environnement.

    ``AIH_ADMIN_DISCORD_IDS`` contient une liste d'IDs Discord séparés par
    des virgules. Si ``user_id`` y figure, l'utilisateur reçoit le rôle
    ``admin``. Si la variable est absente ou vide, personne n'est admin
    (comportement sûr par défaut) et un WARNING explicite est émis, rate-
    limité (au plus une fois par heure) pour ne pas polluer les logs à
    chaque requête.

    Args:
        user_id (str): L'ID Discord de l'utilisateur.

    Returns:
        str: ``"admin"`` si l'utilisateur est dans la liste, sinon ``"user"``.
    """
    global _ADMIN_BOOTSTRAP_WARNED_AT
    raw = os.environ.get("AIH_ADMIN_DISCORD_IDS", "").strip()
    if not raw:
        now = time.time()
        if now - _ADMIN_BOOTSTRAP_WARNED_AT >= _ADMIN_BOOTSTRAP_WARN_INTERVAL:
            _ADMIN_BOOTSTRAP_WARNED_AT = now
            logging.warning(
                "Aucun admin configuré — définir AIH_ADMIN_DISCORD_IDS "
                "(liste d'IDs Discord séparés par des virgules)"
            )
        return "user"
    admin_ids = {i.strip() for i in raw.split(",") if i.strip()}
    if user_id in admin_ids:
        logging.warning("Admin bootstrap: rôle admin accordé à %s", user_id)
        return "admin"
    return "user"


# ── Session synchronisation ───────────────────────────────────────────

def _sync_session_user(user_id: str):
    """Crée ou met à jour l'utilisateur en BDD à partir de la session.

    Le rôle d'un nouvel utilisateur est déterminé par le bootstrap admin
    (env ``AIH_ADMIN_DISCORD_IDS``) : seuls les IDs listés deviennent admin.
    Sans variable configurée, personne n'est admin (fail-closed).

    Args:
        user_id (str): L'ID Discord de l'utilisateur.
    """
    user = session.get("user")
    if not user:
        return
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # Fast path: check if user exists (read-only, no write lock needed in WAL mode)
        cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if cur.fetchone():
            conn.close()
            conn = None
            return
        # User doesn't exist — use INSERT OR IGNORE to avoid race condition
        # Le rôle est déterminé par le bootstrap admin (env AIH_ADMIN_DISCORD_IDS)
        role = _bootstrap_role(user_id)
        cur.execute(
            "INSERT OR IGNORE INTO users (id, username, display_name, avatar, role) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, user.get("username", ""), user.get("display_name", ""),
             user.get("avatar", ""), role),
        )
        conn.commit()
    except Exception:
        logging.exception("_sync_session_user failed")
        if conn:
            with contextlib.suppress(Exception):
                conn.rollback()
    finally:
        if conn:
            with contextlib.suppress(Exception):
                conn.close()


# ── Login guards ───────────────────────────────────────────────────────

def _login_required():
    """Vérifie que l'utilisateur est connecté (session OU token API).

    Positionne ``g.user_id`` pour que ``_get_current_user_id()`` le retrouve.

    Returns:
        tuple | None: Un tuple ``(Response, int)`` 401 si non connecté,
            sinon ``None`` (accès autorisé).
    """
    user_id = _get_current_user_id()
    if not user_id:
        return jsonify({
            "error": "Connexion requise. Utilisez le bouton 'Connexion Discord' ou un token API."
        }), 401
    g.user_id = user_id
    _sync_session_user(user_id)
    return None


# ── Role checks ────────────────────────────────────────────────────────

def is_admin(user_id: str) -> bool:
    """Vérifie si un utilisateur est administrateur.

    Modèle fail-closed : un utilisateur n'est admin que si son rôle en BDD
    est explicitement ``admin``. S'il n'existe aucun admin en BDD, personne
    n'est admin (retourne ``False``).

    Args:
        user_id (str): L'ID de l'utilisateur à vérifier.

    Returns:
        bool: ``True`` si l'utilisateur est admin, ``False`` sinon.
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "role" not in cols:
            conn.close()
            return False
        cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None and row["role"] == "admin"
    except Exception as e:
        print(f"[is_admin] Erreur: {e}")
        return False  # Fail secure : refuser admin en cas d'erreur


def is_kw_editor(user_id: str) -> bool:
    """Vérifie si un utilisateur est éditeur de mots-clés (ou admin).

    Args:
        user_id (str): L'ID de l'utilisateur à vérifier.

    Returns:
        bool: ``True`` si l'utilisateur est ``admin`` ou ``kw_editor``,
            ``False`` sinon.
    """
    try:
        if is_admin(user_id):
            return True
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None and row["role"] == "kw_editor"
    except Exception as e:
        print(f"[is_kw_editor] Erreur: {e}")
        return False


def _admin_required():
    """Vérifie que l'utilisateur courant est administrateur.

    Returns:
        tuple | None: Un tuple ``(Response, int)`` 401/403 si non autorisé,
            sinon ``None`` (accès autorisé).
    """
    try:
        guard = _login_required()
        if guard:
            return guard
        if not is_admin(_get_current_user_id()):
            return jsonify({"error": "Accès réservé aux administrateurs."}), 403
        return None
    except Exception as e:
        return jsonify({"error": f"Erreur vérification admin: {e}"}), 500


def _kw_editor_required():
    """Vérifie que l'utilisateur courant est éditeur de mots-clés (ou admin).

    Returns:
        tuple | None: Un tuple ``(Response, int)`` 401/403 si non autorisé,
            sinon ``None`` (accès autorisé).
    """
    try:
        guard = _login_required()
        if guard:
            return guard
        if not is_kw_editor(_get_current_user_id()):
            return jsonify({"error": "Accès réservé aux éditeurs de mots-clés."}), 403
        return None
    except Exception as e:
        return jsonify({"error": f"Erreur vérification kw_editor: {e}"}), 500


# ── Privacy filter ─────────────────────────────────────────────────────

def _privacy_filter(user_id: str) -> (str, list):
    """Construit une clause WHERE de filtrage des keywords selon le rôle.

    Règles de visibilité :
        - Un user normal voit ses propres keywords (tous statuts) + les ``public``.
        - Un kw_editor/admin voit en plus les ``public_pending`` de tous.

    Args:
        user_id (str): L'ID de l'utilisateur courant.

    Returns:
        tuple: ``(clause_where, params)`` où ``clause_where`` est une
            chaîne SQL et ``params`` une liste de paramètres de liaison.
    """
    if is_kw_editor(user_id):
        return ("(k.privacy_status != 'private' OR k.user_id = ?)", [user_id])
    else:
        return ("(k.privacy_status = 'public' OR k.user_id = ?)", [user_id])
