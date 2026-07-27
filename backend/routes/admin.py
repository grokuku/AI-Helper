"""Routes admin for AI-Helper backend."""

from context import *


# ── API keywords ─────────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET', 'POST'])
def user_settings():
    """Lit (GET) ou met à jour (POST) les paramètres de l'utilisateur courant.

    Returns:
        flask.Response: JSON des paramètres utilisateur (GET) ou confirmation (POST).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()
    if request.method == 'POST':
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid JSON'}), 400
        conn.execute('UPDATE users SET settings = ? WHERE id = ?', (json.dumps(data), user_id))
        conn.commit()
        conn.close()
        session['user']['settings'] = data
        return jsonify({'status': 'ok'})
    cur = conn.execute('SELECT settings FROM users WHERE id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    settings = json.loads(row['settings']) if row and row['settings'] else {}
    return jsonify(settings)


@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    """Liste tous les utilisateurs (admin seulement)."""
    try:
        guard = _admin_required()
        if guard:
            return guard
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT id, username, display_name, avatar, role, guild_nickname, created_at, last_login FROM users ORDER BY role, username')
        users = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify(users)
    except Exception as e:
        return jsonify({'error': f'Erreur serveur: {e}'}), 500


@app.route('/api/members', methods=['GET'])
def list_members():
    """Liste tous les utilisateurs avec leurs stats (accessible aux membres connectés)."""
    try:
        guard = _login_required()
        if guard:
            return guard
        conn = get_db()
        cur = conn.cursor()
        # Infos de base + avatar
        cur.execute('SELECT id, username, display_name, avatar, role FROM users ORDER BY role, username')
        users = [dict(r) for r in cur.fetchall()]
        # Stats par utilisateur
        for u in users:
            uid = u['id']
            # Nombre de filtres sauvegardés
            cur.execute('SELECT COUNT(*) FROM saved_filters WHERE user_id = ?', (uid,))
            u['filter_count'] = cur.fetchone()[0]
            # Nombre de prompts générés
            cur.execute('SELECT COUNT(*) FROM generated_prompts WHERE user_id = ?', (uid,))
            u['prompt_count'] = cur.fetchone()[0]
            # Avatar URL
            if u.get('avatar') and u.get('id'):
                u['avatar_url'] = f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png?size=64"
            else:
                u['avatar_url'] = ''
        conn.close()
        return jsonify(users)
    except Exception as e:
        return jsonify({'error': f'Erreur serveur: {e}'}), 500


@app.route('/api/members/<user_id>', methods=['GET'])
def member_detail(user_id):
    """Détails d'un membre : stats + historique des prompts."""
    guard = _login_required()
    if guard: return guard
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT id, username, display_name, avatar, role FROM users WHERE id = ?', (user_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Membre introuvable'}), 404
        user = dict(row)
        if user.get('avatar') and user.get('id'):
            user['avatar_url'] = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=256"
        else:
            user['avatar_url'] = ''
        cur.execute('SELECT COUNT(*) FROM saved_filters WHERE user_id = ?', (user_id,))
        user['filter_count'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM generated_prompts WHERE user_id = ?', (user_id,))
        user['prompt_count'] = cur.fetchone()[0]
        cur.execute("""SELECT t.name, COUNT(*) as cnt FROM generated_prompts gp JOIN prompt_templates t ON t.id = gp.template_id WHERE gp.user_id = ? AND gp.template_id IS NOT NULL GROUP BY gp.template_id ORDER BY cnt DESC LIMIT 1""", (user_id,))
        pt = cur.fetchone()
        user['favorite_type'] = pt['name'] if pt else None
        cur.execute("""SELECT s.name, COUNT(*) as cnt FROM generated_prompts gp JOIN styles s ON s.id = gp.style_id WHERE gp.user_id = ? AND gp.style_id IS NOT NULL GROUP BY gp.style_id ORDER BY cnt DESC LIMIT 1""", (user_id,))
        st = cur.fetchone()
        user['favorite_style'] = st['name'] if st else None
        cur.execute("""SELECT t.name as template_name, gp.output_text, gp.style_id, gp.created_at FROM generated_prompts gp LEFT JOIN prompt_templates t ON t.id = gp.template_id WHERE gp.user_id = ? ORDER BY gp.created_at DESC LIMIT 15""", (user_id,))
        user['recent_prompts'] = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify(user)
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/<user_id>/role', methods=['POST'])
def admin_set_role(user_id):
    """Modifie le rôle d'un utilisateur (admin seulement).

    Args:
        user_id: Identifiant de l'utilisateur dont le rôle doit être modifié.

    Returns:
        flask.Response: JSON confirmant la modification ou une erreur 400.
    """
    guard = _admin_required()
    if guard:
        return guard
    data = request.get_json()
    new_role = data.get('role')
    if new_role not in ('admin', 'kw_editor', 'user'):
        return jsonify({'error': 'Role invalide. Valeurs acceptées: admin, kw_editor, user'}), 400
    conn = get_db()
    conn.execute('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/admin/users/<user_id>', methods=['DELETE'])
def admin_delete_user(user_id):
    """Supprime un utilisateur et toutes ses données associées (admin seulement).

    Args:
        user_id: Identifiant de l'utilisateur à supprimer.

    Returns:
        flask.Response: JSON confirmant la suppression ou une erreur 400.
    """
    guard = _admin_required()
    if guard:
        return guard
    current_id = _get_current_user_id()
    if user_id == current_id:
        return jsonify({'error': 'Tu ne peux pas te supprimer.'}), 400
    conn = get_db()
    conn.execute('DELETE FROM keyword_embeddings WHERE keyword_id IN (SELECT id FROM keywords WHERE user_id = ?)', (user_id,))
    conn.execute('DELETE FROM keywords WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/admin/settings/ollama', methods=['GET', 'POST'])
def admin_ollama_settings():
    """Lire / définir la config Ollama (admin seulement)."""
    try:
        guard = _admin_required()
        if guard:
            return guard
        if request.method == 'POST':
            data = request.get_json()
            url = data.get('url', '').strip()
            model = data.get('model', '').strip()
            if not url or not model:
                return jsonify({'error': 'URL et modèle requis'}), 400
            conn = get_db()
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('ollama_url', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (url,))
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('ollama_model', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (model,))
            conn.commit()
            conn.close()
            from embeddings import set_config
            set_config(ollama_url=url, ollama_model=model)
            return jsonify({'status': 'ok'})
        cfg = _get_ollama_config()
        return jsonify(cfg)
    except Exception as e:
        return jsonify({'error': f'Erreur serveur: {e}'}), 500


# ── SFTP config ───────────────────────────────────────────────────────

@app.route('/api/admin/settings/sftp', methods=['GET', 'POST'])
def admin_sftp_settings():
    """Lire / définir la config SFTP (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard

    conn = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            for key, default in [('sftp_host', ''), ('sftp_port', '22'), ('sftp_user', ''), ('sftp_password', ''), ('sftp_base_path', '/aih')]:
                val = data.get(key.replace('sftp_', ''), default)
                if val == '' and key == 'sftp_password':
                    continue  # ne pas écraser le mot de passe si vide
                if val != '':
                    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(val)))
            conn.commit()
            # Recharger le storage
            from storage import reload_storage
            reload_storage()
            return jsonify({'status': 'ok'})

        # GET
        result = {}
        for key in ('sftp_host', 'sftp_port', 'sftp_user', 'sftp_base_path'):
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            result[key.replace('sftp_', '')] = row[0] if row else ''
        # Ne pas retourner le mot de passe
        return jsonify(result)
    finally:
        conn.close()


@app.route('/api/admin/settings/sftp/test', methods=['POST'])
def admin_sftp_test():
    """Teste la connexion SFTP avec la config actuelle."""
    guard = _admin_required()
    if guard:
        return guard
    try:
        from storage import get_storage
        storage = get_storage()
        backend = storage.get_backend_name()
        # Test simple : lister le dossier racine
        files = storage.list_dir('')
        return jsonify({'ok': True, 'backend': backend, 'files_count': len(files)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Backup config ─────────────────────────────────────────────────────

@app.route('/api/admin/settings/backup', methods=['GET', 'POST'])
def admin_backup_settings():
    """Lire / définir la config backup (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard

    conn = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            enabled = 1 if data.get('enabled') else 0
            interval = int(data.get('interval_hours', 24))
            max_backups = int(data.get('max_backups', 7))
            for key, val in [('backup_enabled', str(enabled)), ('backup_interval', str(interval)), ('backup_max_backups', str(max_backups))]:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, val))
            conn.commit()
            return jsonify({'status': 'ok'})

        # GET
        result = {}
        for key, default in [('backup_enabled', '0'), ('backup_interval', '24'), ('backup_max_backups', '7')]:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            result[key.replace('backup_', '')] = row[0] if row else default
        result['enabled'] = result.pop('enabled') == '1'
        result['interval_hours'] = int(result.pop('interval'))
        result['max_backups'] = int(result.pop('max_backups'))
        # Dernier backup
        from storage import get_storage
        try:
            files = get_storage().list_dir('backups')
            backups = sorted([f for f in files if f.startswith('keywords_') and f.endswith('.db')])
            result['last_backup'] = backups[-1] if backups else None
        except Exception:
            result['last_backup'] = None
        return jsonify(result)
    finally:
        conn.close()


@app.route('/api/admin/settings/backup/now', methods=['POST'])
def admin_backup_now():
    """Déclenche un backup immédiat."""
    guard = _admin_required()
    if guard:
        return guard
    try:
        from storage import backup_database, get_storage
        # Lire max_backups depuis la config
        conn = get_db()
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'backup_max_backups'").fetchone()
        conn.close()
        max_b = int(row[0]) if row else 7
        success = backup_database(str(DB_PATH), max_backups=max_b)
        if success:
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'Échec du backup'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/admin/db/clear', methods=['POST'])
def admin_db_clear():
    """Supprime tous les mots-clés et embeddings de la base (admin seulement).

    Returns:
        flask.Response: JSON confirmant la suppression.
    """
    guard = _admin_required()
    if guard:
        return guard
    conn = get_db()
    conn.execute('DELETE FROM keyword_embeddings')
    conn.execute('DELETE FROM keywords')
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


# ── Whitelist Discord ────────────────────────────────────────────────

@app.route('/api/admin/whitelist', methods=['GET'])
def admin_whitelist_list():
    """Liste tous les UIDs whitelistés (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard
    conn = get_db()
    rows = conn.execute('SELECT discord_uid, added_by, added_at FROM discord_whitelist ORDER BY added_at DESC').fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return jsonify(result)


@app.route('/api/admin/whitelist', methods=['POST'])
def admin_whitelist_add():
    """Ajoute un UID Discord à la whitelist (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard
    data = request.get_json()
    uid = data.get('discord_uid', '').strip()
    if not uid:
        return jsonify({'error': 'UID Discord requis'}), 400
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO discord_whitelist (discord_uid, added_by) VALUES (?, ?)', (uid, _get_current_user_id()))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/admin/whitelist/<discord_uid>', methods=['DELETE'])
def admin_whitelist_remove(discord_uid):
    """Retire un UID Discord de la whitelist (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard
    conn = get_db()
    conn.execute('DELETE FROM discord_whitelist WHERE discord_uid = ?', (discord_uid,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

