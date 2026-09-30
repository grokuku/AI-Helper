"""Authentication and authorization helpers.

Provides login guards (session or API token), role checks (admin, kw_editor),
and the privacy filter used by keyword queries.
"""

import contextlib
import hashlib
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
    """Authentifie la requête via un Bearer token (JWT ou clé API ``aih_...``).

    Ordre de résolution :
        1. JWT (via ``verify_jwt``).
        2. Clé API NOMMÉE : lookup par SHA-256 dans ``api_tokens``. Les clés
           révoquées (``revoked_at`` non NULL) sont REFUSÉES et ne retombent
           JAMAIS sur le repli historique (sinon une clé révoquée
           « ressusciterait »). **AUCUNE expiration** sur ces clés.
        3. Repli historique : ``users.api_token_hash`` (clé hashée créée par
           l'ancien endpoint ``/api/auth/token``) — conserve sa règle
           d'expiration pour ne pas casser les clients/tests existants.
        4. Repli historique en clair : ``users.api_token`` → migration
           paresseuse vers le hash (sans invalidation).

    La dernière utilisation d'une clé nommée (date, IP, User-Agent) est tracée
    de façon THROTTLÉE (cf. ``_touch_token_usage``).

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

    # 2) API tokens
    try:
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        conn = get_db()
        try:
            # 2a) Clés nommées (modèle actuel) — AUCUNE condition d'expiration.
            try:
                row = conn.execute(
                    "SELECT id, user_id, revoked_at FROM api_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
            except Exception:
                row = None
            if row:
                # Clé révoquée → refus ferme, SANS repli sur l'historique.
                if row["revoked_at"]:
                    return None
                ip = (request.remote_addr or "")[:128]
                ua = (request.headers.get("User-Agent", "") or "")[:512]
                if _touch_token_usage(conn, row["id"], ip, ua):
                    conn.commit()
                return row["user_id"]

            # 2b) Repli : clé hashée historique (users.api_token_hash)
            row = conn.execute(
                "SELECT id, api_token_created_at FROM users WHERE api_token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row:
                if _api_token_expired(row['api_token_created_at']):
                    return None
                return row['id']

            # 2c) Repli : clé historique en clair → migration paresseuse
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
                return row['id']
            return None
        finally:
            conn.close()
    except Exception:
        return None


# ── Traçage throttlé de l'utilisation des clés nommées ────────────────
#
# ``last_used_at`` / IP / User-Agent sont écrits en BDD au plus une fois
# toutes les ``AIH_TOKEN_USAGE_THROTTLE_S`` secondes (défaut 300 = 5 min) pour
# ne pas dégrader les performances : un client ComfyUI peut émettre des
# dizaines de requêtes par minute. La 1re utilisation d'une clé écrit toujours.
_TOKEN_USAGE_LAST_WRITE: dict[int, float] = {}
_DEFAULT_TOKEN_USAGE_THROTTLE_S = 300


def _token_usage_throttle_s() -> int:
    """Intervalle minimal (s) entre deux écritures de ``last_used`` pour une clé.

    ``AIH_TOKEN_USAGE_THROTTLE_S`` permet de l'ajuster (0 = écrire à chaque
    requête, utile en test). Valeur invalide → défaut (300 s).
    """
    try:
        return max(
            0,
            int(os.environ.get(
                "AIH_TOKEN_USAGE_THROTTLE_S", str(_DEFAULT_TOKEN_USAGE_THROTTLE_S)
            )),
        )
    except (TypeError, ValueError):
        return _DEFAULT_TOKEN_USAGE_THROTTLE_S


def _touch_token_usage(conn, token_id: int, ip: str, user_agent: str,
                       now: float | None = None) -> bool:
    """Trace la dernière utilisation d'une clé, de façon THROTTLÉE.

    Args:
        conn: La connexion SQLite active (ouverture gérée par l'appelant).
        token_id (int): L'ID de la ligne ``api_tokens``.
        ip (str): IP du client (``request.remote_addr``).
        user_agent (str): User-Agent du client (tronqué).
        now (float | None): Horodatage epoch injectable (tests), sinon courant.

    Returns:
        bool: ``True`` si la BDD a été mise à jour (l'appelant doit alors
            ``commit``), ``False`` si l'écriture a été sautée (trop récente).
    """
    now = time.time() if now is None else now
    last = _TOKEN_USAGE_LAST_WRITE.get(token_id, 0.0)
    if now - last < _token_usage_throttle_s():
        return False
    conn.execute(
        "UPDATE api_tokens SET last_used_at = datetime('now'), "
        "last_used_ip = ?, last_used_user_agent = ? WHERE id = ?",
        (ip, user_agent, token_id),
    )
    _TOKEN_USAGE_LAST_WRITE[token_id] = now
    return True


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
