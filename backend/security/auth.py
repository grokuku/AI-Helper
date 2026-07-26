"""Authentication and authorization helpers.

Provides login guards (session or API token), role checks (admin, kw_editor),
and the privacy filter used by keyword queries.
"""

import logging

from flask import g, request, jsonify, session

from auth import verify_jwt
from db import get_db


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
    # 1) Flask g (positionné par _login_required)
    gid = getattr(g, 'user_id', None)
    if gid:
        return gid
    # 2) Session (connexion Discord)
    user = session.get("user")
    if user:
        return user["id"]
    # 3) Bearer token (API)
    return _authenticate_via_token()


def _authenticate_via_token() -> str | None:
    """Authentifie la requête via un Bearer token (JWT ou API legacy).

    Accepte deux types de tokens :
        - JWT token (via ``verify_jwt``)
        - API token legacy (``fr_ia_...`` stocké en BDD)

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

    # 2) Fallback : API token legacy (fr_ia_...)
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM users WHERE api_token = ?", (token,)
        ).fetchone()
        conn.close()
        return row['id'] if row else None
    except Exception:
        return None


# ── Session synchronisation ───────────────────────────────────────────

def _sync_session_user(user_id: str):
    """Crée ou met à jour l'utilisateur en BDD à partir de la session.

    Si l'utilisateur n'existe pas encore et qu'aucun admin n'est déclaré,
    il devient automatiquement admin.

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
        # If admin_count == 0, the first user becomes admin
        cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_count = cur.fetchone()[0]
        role = "admin" if admin_count == 0 else "user"
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
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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

    Si aucun admin n'est déclaré en BDD, tout le monde est considéré admin.

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
            return True
        cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_count = cur.fetchone()[0]
        if admin_count == 0:
            conn.close()
            return True
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