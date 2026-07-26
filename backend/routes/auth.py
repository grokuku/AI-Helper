"""Routes auth for FR.IA backend."""

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

    # Vérification du serveur (si GUILD_ID configuré)
    ok, err = check_guild_access(ses)
    if not ok:
        return f"Accès refusé : {err}", 403

    # Infos utilisateur
    discord_user = get_user_info(ses)
    user_id = discord_user["id"]
    display_name = discord_user.get("global_name") or discord_user["username"]

    # Pseudo sur le serveur Discord (si configuré)
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    guild_nickname = None
    if guild_id:
        member = get_guild_member(ses, guild_id)
        if member:
            guild_nickname = member.get("nick") or member.get("user", {}).get("global_name")

    # Détermination du rôle + sauvegarde
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    admin_count = cur.fetchone()[0]
    cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    existing = cur.fetchone()
    if existing:
        role = existing["role"]  # garde le rôle existant
    elif admin_count == 0:
        role = "admin"  # premier utilisateur ou aucun admin → admin
    else:
        role = "user"

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
    conn.close()

    # Chargement de la config Ollama stockée en BDD
    ollama_cfg = _get_ollama_config()
    if ollama_cfg.get("url") or ollama_cfg.get("model"):
        set_config(ollama_url=ollama_cfg.get("url"), ollama_model=ollama_cfg.get("model"))

    # Création de la session Flask
    session["user"] = {
        "id": user_id,
        "username": discord_user["username"],
        "display_name": guild_nickname or display_name,
        "avatar": discord_user.get("avatar"),
        "avatar_url": avatar_url(discord_user),
        "role": role,
        "settings": user_settings,
        "guild_nickname": guild_nickname,
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
    """Gérer la clé API de l'utilisateur connecté."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()

    if request.method == 'POST':
        # Régénérer le token
        import secrets
        new_token = 'fr_ia_' + secrets.token_hex(24)
        conn.execute("UPDATE users SET api_token = ? WHERE id = ?", (new_token, user_id))
        conn.commit()
        conn.close()
        return jsonify({'token': new_token})

    # GET : retourner le token existant (ou en créer un)
    cur = conn.execute("SELECT api_token FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row and row['api_token']:
        conn.close()
        return jsonify({'token': row['api_token']})

    # Pas de token → en créer un
    import secrets
    new_token = 'fr_ia_' + secrets.token_hex(24)
    conn.execute("UPDATE users SET api_token = ? WHERE id = ?", (new_token, user_id))
    conn.commit()
    conn.close()
    return jsonify({'token': new_token})


