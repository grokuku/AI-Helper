"""
Module d'authentification Discord OAuth2 + JWT.
Limite les accès aux utilisateurs dont l'UID Discord est présent dans la whitelist.

Configuration via variables d'environnement (ou .env) :
  DISCORD_CLIENT_ID      requis
  DISCORD_CLIENT_SECRET  requis
  DISCORD_REDIRECT_URI   optionnel — défaut: http://localhost:5000/api/auth/discord/callback
  SECRET_KEY             requis — pour les sessions Flask et la signature JWT
  JWT_SECRET_KEY         optionnel — pour la signature JWT (défaut: SECRET_KEY)
  JWT_ACCESS_EXPIRY      optionnel — secondes de validité du token d'accès (défaut: 86400 = 24h)
  JWT_REFRESH_EXPIRY     optionnel — secondes de validité du refresh token (défaut: 2592000 = 30j)
"""

import os
import time
from functools import wraps

import jwt as pyjwt
import requests
from authlib.integrations.flask_client import OAuth
from flask import session, current_app

DISCORD_API = "https://discord.com/api"


def init_oauth(app):
    """Configure OAuth Discord sur l'app Flask.

    Args:
        app (Flask): L'instance Flask sur laquelle enregistrer OAuth.

    Returns:
        OAuth: L'instance OAuth configurée avec le provider Discord.
    """
    oauth = OAuth(app)
    oauth.register(
        name="discord",
        client_id=os.environ["DISCORD_CLIENT_ID"],
        client_secret=os.environ["DISCORD_CLIENT_SECRET"],
        authorize_url="https://discord.com/api/oauth2/authorize",
        access_token_url="https://discord.com/api/oauth2/token",
        client_kwargs={"scope": "identify"},
    )
    return oauth


def make_discord_session(token: dict) -> requests.Session:
    """Crée une session requests authentifiée avec un token Discord.

    Args:
        token (dict): Dictionnaire contenant au moins ``access_token``.

    Returns:
        requests.Session: Une session avec l'en-tête Authorization Bearer.
    """
    ses = requests.Session()
    ses.headers.update({"Authorization": f"Bearer {token['access_token']}"})
    return ses


def get_user_guilds(ses: requests.Session) -> list[dict]:
    """Récupère la liste des serveurs (guilds) de l'utilisateur.

    NOTE : Cette fonction et ``get_guild_member`` ci-dessous sont conservées
    pour la compatibilité API, mais ne sont plus appelées par le flow d'auth
    depuis le passage au système de whitelist d'UIDs Discord.

    Args:
        ses (requests.Session): La session Discord authentifiée.

    Returns:
        list[dict]: La liste des guilds (chaque guild est un dict avec ``id``,
            ``name``, etc.).

    Raises:
        requests.HTTPError: Si l'API Discord renvoie une erreur.
    """
    resp = ses.get(f"{DISCORD_API}/users/@me/guilds")
    resp.raise_for_status()
    return resp.json()


def check_whitelist_access(user_id: str) -> tuple[bool, str | None]:
    """Vérifie si l'UID Discord de l'utilisateur est présent dans la whitelist.

    Args:
        user_id (str): L'UID Discord de l'utilisateur.

    Returns:
        tuple: ``(ok, error_message)`` où ``ok`` est un booléen et
            ``error_message`` est ``None`` si l'accès est autorisé.
    """
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT 1 FROM discord_whitelist WHERE discord_uid = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return True, None
        return False, (
            "Ton UID Discord n'est pas dans la whitelist. "
            "Demande à un administrateur de t'ajouter."
        )
    except Exception as e:
        return False, f"Erreur lors de la vérification de la whitelist : {e}"


def get_guild_member(ses: requests.Session, guild_id: str) -> dict | None:
    """Récupère les infos du membre dans un serveur (nickname, rôles…).

    Args:
        ses (requests.Session): La session Discord authentifiée.
        guild_id (str): L'ID du serveur Discord.

    Returns:
        dict | None: Les infos du membre si trouvé, sinon ``None``.
    """
    resp = ses.get(f"{DISCORD_API}/users/@me/guilds/{guild_id}/member")
    if resp.ok:
        return resp.json()
    return None


def get_user_info(ses: requests.Session) -> dict:
    """Récupère les infos Discord de l'utilisateur connecté.

    Args:
        ses (requests.Session): La session Discord authentifiée.

    Returns:
        dict: Les infos utilisateur (``id``, ``username``, ``avatar``, etc.).

    Raises:
        requests.HTTPError: Si l'API Discord renvoie une erreur.
    """
    resp = ses.get(f"{DISCORD_API}/users/@me")
    resp.raise_for_status()
    return resp.json()


def avatar_url(user: dict) -> str:
    """Construit l'URL de l'avatar Discord.

    Args:
        user (dict): Les infos utilisateur Discord (doit contenir ``id`` et
            optionnellement ``avatar``).

    Returns:
        str: L'URL de l'avatar (avatar par défaut si pas d'avatar custom).
    """
    uid = user["id"]
    hash_ = user.get("avatar")
    if not hash_:
        # Avatar par défaut
        default = int(uid) % 5
        return f"https://cdn.discordapp.com/embed/avatars/{default}.png"
    anim = "gif" if hash_.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{uid}/{hash_}.{anim}?size=64"


def get_logged_user() -> dict | None:
    """Retourne l'utilisateur connecté depuis la session, ou None.

    Returns:
        dict | None: Les infos utilisateur avec ``avatar_url`` à jour,
            ou ``None`` si non connecté.
    """
    user = session.get("user")
    if not user:
        return None
    # Ajoute l'avatar à jour
    user["avatar_url"] = avatar_url(user)
    return user


# ── JWT helpers ──────────────────────────────────────────────────────


# Cache de la clé JWT lue au démarrage (évite current_app qui nécessite un contexte Flask)
_JWT_SECRET_CACHE = None

def _get_jwt_secret() -> str:
    """Retourne la clé secrète JWT, lue une seule fois au démarrage.

    Returns:
        str: La clé secrète (depuis ``JWT_SECRET_KEY`` ou ``SECRET_KEY``).
    """
    global _JWT_SECRET_CACHE
    if _JWT_SECRET_CACHE is not None:
        return _JWT_SECRET_CACHE
    _JWT_SECRET_CACHE = os.environ.get("JWT_SECRET_KEY",
                         os.environ.get("SECRET_KEY",
                         "fallback-insecure-key-change-me"))
    return _JWT_SECRET_CACHE


def _get_jwt_algorithm() -> str:
    """Retourne l'algorithme de signature JWT utilisé.

    Returns:
        str: L'algorithme (actuellement ``"HS256"``).
    """
    return "HS256"

def create_jwt(user_id: str, role: str = "user", extra_claims: dict | None = None) -> str:
    """Crée un JWT token d'accès.

    Durée de validité : ``JWT_ACCESS_EXPIRY`` (défaut 24 h).

    Args:
        user_id (str): L'ID de l'utilisateur.
        role (str, optional): Le rôle de l'utilisateur. Défaut: ``"user"``.
        extra_claims (dict | None, optional): Claims supplémentaires à inclure.

    Returns:
        str: Le token JWT encodé.
    """
    expiry = int(os.environ.get("JWT_ACCESS_EXPIRY", "86400"))
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + expiry,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, _get_jwt_secret(), algorithm=_get_jwt_algorithm())


def create_refresh_token(user_id: str) -> str:
    """Crée un JWT refresh token (longue durée).

    Durée de validité : ``JWT_REFRESH_EXPIRY`` (défaut 30 jours).

    Args:
        user_id (str): L'ID de l'utilisateur.

    Returns:
        str: Le refresh token JWT encodé.
    """
    expiry = int(os.environ.get("JWT_REFRESH_EXPIRY", "2592000"))
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + expiry,
        "type": "refresh",
    }
    return pyjwt.encode(payload, _get_jwt_secret(), algorithm=_get_jwt_algorithm())


def verify_jwt(token: str) -> dict | None:
    """Vérifie et décode un JWT.

    Args:
        token (str): Le token JWT à vérifier.

    Returns:
        dict | None: Le payload (dict) si le token est valide, sinon ``None``.
    """
    try:
        payload = pyjwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[_get_jwt_algorithm()],
            options={"require": ["sub", "exp", "iat"]},
        )
        return payload
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return None


def _make_jwt_decorator():
    """Fabrique le décorateur JWT (logique commune aux deux syntaxes).

    Returns:
        callable: Un décorateur qui vérifie le Bearer token JWT sur la route.
    """
    from flask import g, request, jsonify

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Token JWT requis. En-tête Authorization: Bearer <token>"}), 401
            token = auth[7:]
            payload = verify_jwt(token)
            if not payload:
                return jsonify({"error": "Token JWT invalide ou expiré"}), 401
            if payload.get("type") != "access":
                return jsonify({"error": "Le token fourni n'est pas un token d'accès"}), 401
            g.jwt_user = payload
            return f(*args, **kwargs)
        return wrapper
    return decorator


def jwt_required(f=None):
    """
    Décorateur pour protéger une route avec un JWT Bearer token.
    Supporte les deux syntaxes : @jwt_required et @jwt_required().
    Usage :
        @app.route('/api/protected')
        @jwt_required
        def my_route():
            user_id = g.jwt_user['sub']
            ...
    """
    from flask import g, request, jsonify

    if f is not None:
        # Utilisé comme @jwt_required (sans parenthèses)
        decorator = _make_jwt_decorator()
        return decorator(f)
    # Utilisé comme @jwt_required() (avec parenthèses)
    return _make_jwt_decorator()
