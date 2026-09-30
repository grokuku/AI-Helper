"""Routes auth for AI-Helper backend."""

import hashlib
import secrets

from context import *

# ── Routes d'authentification ────────────────────────────────────────

@app.route('/api/auth/discord/login', methods=['GET'])
def discord_login():
    """Redirige l'utilisateur vers Discord OAuth2."""
    redirect_uri = os.environ.get(
        "DISCORD_REDIRECT_URI",
        request.url_root.rstrip("/") + "/api/auth/discord/callback",
    )
    return oauth.discord.authorize_redirect(redirect_uri)


@app.route('/api/auth/discord/callback', methods=['GET'])
def discord_callback():
    """Callback OAuth2 — vérifie le serveur, crée la session."""
    try:
        token = oauth.discord.authorize_access_token()
    except Exception as e:
        return f"Erreur d'autorisation Discord : {e}", 400

    ses = make_discord_session(token)

    # Infos utilisateur (récupérées AVANT la vérification d'accès)
    discord_user = get_user_info(ses)
    user_id = discord_user["id"]
    display_name = discord_user.get("global_name") or discord_user["username"]

    # Vérification de la whitelist (remplace l'ancien check_guild_access)
    ok, err = check_whitelist_access(user_id)
    if not ok:
        return f"Accès refusé : {err}", 403

    # guild_nickname n'est plus utilisé (plus de guild check) — colonne gardée pour compat
    guild_nickname = None

    # Détermination du rôle + sauvegarde
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        existing = cur.fetchone()
        bootstrap_role = _bootstrap_role(user_id)
        if existing:
            role = existing["role"]  # garde le rôle existant
            if bootstrap_role == "admin":
                role = "admin"  # le bootstrap env peut promouvoir
        else:
            role = bootstrap_role  # admin si dans AIH_ADMIN_DISCORD_IDS, sinon user

        # Sauvegarde / mise à jour dans la BDD
        conn.execute("""
            INSERT INTO users (id, username, display_name, avatar, role, guild_nickname, last_login)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                avatar=excluded.avatar,
                role=CASE WHEN excluded.role = 'admin' THEN 'admin' ELSE users.role END,
                guild_nickname=excluded.guild_nickname,
                last_login=CURRENT_TIMESTAMP
        """, (
            user_id,
            discord_user["username"],
            display_name,
            discord_user.get("avatar"),
            role,
            guild_nickname,
        ))

        # Chargement des settings utilisateur
        cur.execute("SELECT settings FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        user_settings = json.loads(row["settings"]) if row and row["settings"] else {}
    finally:
        conn.close()

    # Chargement de la config Ollama stockée en BDD
    ollama_cfg = _get_ollama_config()
    if ollama_cfg.get("url") or ollama_cfg.get("model"):
        set_config(ollama_url=ollama_cfg.get("url"), ollama_model=ollama_cfg.get("model"))

    # Création de la session Flask
    session["user"] = {
        "id": user_id,
        "username": discord_user["username"],
        "display_name": display_name,
        "avatar": discord_user.get("avatar"),
        "avatar_url": avatar_url(discord_user),
        "role": role,
        "settings": user_settings,
        "guild_nickname": None,
    }
    session.permanent = True

    # Page HTML : se ferme toute seule si popup, redirige sinon
    from flask import Response
    return Response(
        '<!DOCTYPE html><html><body><script>'
        'if(window.opener){'
        'window.opener.postMessage({type:"auth_success"},"*");'
        'window.close();'
        '}else{window.location.href="/";}'
        '</script></body></html>',
        mimetype='text/html'
    )


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Retourne l'utilisateur connecté ou 401. Fonctionne avec session ET Bearer token."""
    # Essayer d'abord la session
    user = get_logged_user()
    if user:
        return jsonify(user)
    # Essayer le Bearer token
    user_id = _authenticate_via_token()
    if user_id:
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT id, username, display_name, avatar, role FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
            conn.close()
            if row:
                d = dict(row)
                # Construire l'URL de l'avatar Discord
                if d.get('avatar') and d.get('id'):
                    d['avatar_url'] = f"https://cdn.discordapp.com/avatars/{d['id']}/{d['avatar']}.png?size=64"
                else:
                    d['avatar_url'] = ''
                return jsonify(d)
        except Exception:
            pass
    return jsonify({"error": "Non connecté"}), 401


@app.route('/api/auth/logout', methods=['GET'])
def discord_logout():
    """Déconnecte l'utilisateur."""
    session.clear()
    return jsonify({"status": "ok"})


@app.route('/api/auth/token', methods=['GET', 'POST'])
def api_token():
    """Gérer la clé API historique de l'utilisateur connecté (DÉPRÉCIÉ).

    ⚠️ **Endpoint déprécié** — conservé pour ne pas casser les clients
    existants. Le modèle actuel est multi-clés nommées : voir
    ``GET/POST/PATCH/DELETE /api/auth/tokens``.

    - ``POST`` régénère LA clé historique (l'ancienne devient invalide) et
      renvoie la clé en clair une seule fois ;
    - ``GET`` ne peut PAS réafficher une clé hashée (renvoie ``token: null`` +
      ``exists: true``), ou en crée une si aucune n'existe.

    Le stockage reste hashé (SHA-256) ; la clé en clair n'est jamais relue.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()

    if request.method == 'POST':
        # Régénérer la clé historique (l'ancienne devient invalide immédiatement)
        new_token = 'aih_' + secrets.token_hex(24)
        token_hash = hashlib.sha256(new_token.encode('utf-8')).hexdigest()
        try:
            # Révoquer, dans api_tokens, l'ancienne clé historique si elle y a
            # été importée (migration) — sinon elle « ressusciterait » via le
            # repli. On ne touche PAS aux clés nommées créées par l'utilisateur.
            _revoke_legacy_token_rows(conn, user_id)
            conn.execute(
                "UPDATE users SET api_token_hash = ?, api_token_created_at = datetime('now'), api_token = NULL WHERE id = ?",
                (token_hash, user_id),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({'token': new_token})

    # GET : impossible de réafficher un token hashé → signaler son existence
    # (la 1re utilisation crée un token, comme avant).
    try:
        cur = conn.execute(
            "SELECT api_token_hash, api_token FROM users WHERE id = ?", (user_id,)
        )
        row = cur.fetchone()
        if row and (row['api_token_hash'] or row['api_token']):
            return jsonify({'token': None, 'exists': True})

        # Pas de token → en créer un (affiché une seule fois)
        new_token = 'aih_' + secrets.token_hex(24)
        token_hash = hashlib.sha256(new_token.encode('utf-8')).hexdigest()
        conn.execute(
            "UPDATE users SET api_token_hash = ?, api_token_created_at = datetime('now') WHERE id = ?",
            (token_hash, user_id),
        )
        conn.commit()
        return jsonify({'token': new_token})
    finally:
        conn.close()


# ── Clés API NOMMÉES (modèle actuel) ──────────────────────────────────
#
# Plusieurs clés par utilisateur, une par machine ComfyUI. Aucune expiration :
# une clé reste valide jusqu'à révocation INDIVIDUELLE explicite. La liste
# n'expose JAMAIS la clé ni son hash (seulement son préfixe). Isolation stricte
# par utilisateur : on ne voit / renomme / révoque que ses propres clés.

API_TOKEN_MAX_ACTIVE = 20
API_TOKEN_NAME_MAX_LEN = 64


def _validate_token_name(raw):
    """Valide le nom d'une clé API.

    Règles : obligatoire, trimé, longueur bornée (1..``API_TOKEN_NAME_MAX_LEN``),
    aucun caractère de contrôle.

    Returns:
        tuple[str | None, str | None]: ``(nom, None)`` si valide, sinon
            ``(None, message_d_erreur)``.
    """
    if raw is None:
        return None, "Le nom de la clé est obligatoire."
    if not isinstance(raw, str):
        return None, "Le nom de la clé doit être du texte."
    name = raw.strip()
    if not name:
        return None, "Le nom de la clé est obligatoire."
    if len(name) > API_TOKEN_NAME_MAX_LEN:
        return None, f"Le nom ne doit pas dépasser {API_TOKEN_NAME_MAX_LEN} caractères."
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None, "Le nom ne doit pas contenir de caractères de contrôle."
    return name, None


def _revoke_legacy_token_rows(conn, user_id):
    """Révoque, pour un utilisateur, les lignes ``api_tokens`` correspondant à
    sa clé historique (``users.api_token_hash`` ou ``users.api_token``).

    Utilisé par l'endpoint déprécié ``POST /api/auth/token`` pour que
    l'ancienne clé régénérée cesse de fonctionner (même si elle a été importée
    dans ``api_tokens`` par la migration). Les clés nommées créées par
    l'utilisateur ne sont PAS touchées.
    """
    try:
        row = conn.execute(
            "SELECT api_token_hash, api_token FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return
        hashes = set()
        if row["api_token_hash"]:
            hashes.add(row["api_token_hash"])
        if row["api_token"]:
            hashes.add(hashlib.sha256(str(row["api_token"]).encode("utf-8")).hexdigest())
        for th in hashes:
            conn.execute(
                "UPDATE api_tokens SET revoked_at = datetime('now') "
                "WHERE user_id = ? AND token_hash = ? AND revoked_at IS NULL",
                (user_id, th),
            )
    except Exception:
        pass  # la révocation best-effort ne doit jamais casser la régénération


def _token_public_dict(row):
    """Représentation publique d'une clé — JAMAIS de clé ni de hash."""
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"] or "",
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "last_used_ip": row["last_used_ip"],
        "last_used_user_agent": row["last_used_user_agent"],
        "revoked": bool(row["revoked_at"]),
        "revoked_at": row["revoked_at"],
    }


@app.route('/api/auth/tokens', methods=['GET'])
def list_api_tokens():
    """Liste les clés API de l'utilisateur connecté.

    Réponse 200 : ``{ "tokens": [ {id, name, prefix, created_at, last_used_at,
    last_used_ip, last_used_user_agent, revoked, revoked_at}, ... ] }``.
    Ne contient JAMAIS la clé ni son hash.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, prefix, created_at, last_used_at, last_used_ip, "
            "last_used_user_agent, revoked_at FROM api_tokens "
            "WHERE user_id = ? ORDER BY (revoked_at IS NOT NULL) ASC, id DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"tokens": [_token_public_dict(r) for r in rows]})


@app.route('/api/auth/tokens', methods=['POST'])
def create_api_token():
    """Crée une NOUVELLE clé API nommée.

    Payload JSON : ``{ "name": "ComfyUI salon" }``.
    Réponse 201 : ``{token, id, name, prefix, created_at}`` — ``token`` (la clé
    en clair) n'est renvoyée qu'ICI, une seule fois ; elle n'est jamais
    stockée en clair ni relue.
    Erreurs : 400 (nom invalide / limite atteinte).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    payload = request.get_json(silent=True) or {}
    name, err = _validate_token_name(payload.get("name"))
    if err:
        return jsonify({"error": err}), 400

    conn = get_db()
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()[0]
        if active >= API_TOKEN_MAX_ACTIVE:
            return jsonify({
                "error": f"Limite de {API_TOKEN_MAX_ACTIVE} clés actives atteinte. "
                         "Révoque une clé avant d'en créer une nouvelle."
            }), 400

        token = 'aih_' + secrets.token_hex(24)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        prefix = token[:12]
        cur = conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, prefix) VALUES (?, ?, ?, ?)",
            (user_id, name, token_hash, prefix),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, prefix, created_at FROM api_tokens WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    finally:
        conn.close()
    return jsonify({
        "token": token,
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
    }), 201


@app.route('/api/auth/tokens/<int:token_id>', methods=['PATCH'])
def rename_api_token(token_id):
    """Renomme une clé API de l'utilisateur connecté.

    Isolation stricte : la clé d'un AUTRE utilisateur → 404 (jamais 403, pour
    ne pas révéler son existence).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    payload = request.get_json(silent=True) or {}
    name, err = _validate_token_name(payload.get("name"))
    if err:
        return jsonify({"error": err}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM api_tokens WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Clé API introuvable."}), 404
        conn.execute(
            "UPDATE api_tokens SET name = ? WHERE id = ? AND user_id = ?",
            (name, token_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok", "id": token_id, "name": name})


@app.route('/api/auth/tokens/<int:token_id>', methods=['DELETE'])
def revoke_api_token(token_id):
    """Révoque (soft-delete) une clé API de l'utilisateur connecté.

    Révoquer une clé n'affecte QUE celle-ci : les autres clés continuent de
    fonctionner. La ligne est conservée (horodatée), donc visible dans la liste
    comme « révoquée ». Isolation stricte → 404 pour la clé d'autrui.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, revoked_at FROM api_tokens WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Clé API introuvable."}), 404
        if not row["revoked_at"]:
            conn.execute(
                "UPDATE api_tokens SET revoked_at = datetime('now') "
                "WHERE id = ? AND user_id = ?",
                (token_id, user_id),
            )
            conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok", "id": token_id, "revoked": True})


