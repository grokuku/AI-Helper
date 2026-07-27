"""Routes presets for AI-Helper backend."""

from context import *


# ── Presets ─────────────────────────────────────────────────────────

@app.route('/api/presets', methods=['GET', 'POST'])
def presets():
    """Liste les presets IA visibles (GET) ou crée un nouveau preset (POST).

    Returns:
        flask.Response: JSON listant les presets (GET) ou l'ID du preset créé (POST).
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'GET':
        try:
            rows = cur.execute("""
                SELECT p.*, u.username, u.display_name
                FROM ai_presets p
                LEFT JOIN users u ON u.id = p.user_id
                WHERE p.is_global = 1 OR p.user_id = ?
                ORDER BY p.is_global DESC, p.name
            """, (user_id,)).fetchall()
        except Exception as e:
            conn.close()
            return jsonify({'error': f'DB error: {e}'}), 500
        conn.close()
        result = []
        for r in rows:
            result.append({
                'id': r['id'],
                'user_id': r['user_id'],
                'name': r['name'],
                'engine': r['engine'],
                'base_url': r['base_url'],
                'model': r['model'],
                'is_global': bool(r['is_global']),
                'is_client_side': bool(_row_get(r, 'is_client_side', 0)),
                'owner_name': r['display_name'] or r['username'] or '',
                'created_at': r['created_at']
            })
        return jsonify(result)

    # POST : creation (admin pour global, tout le monde pour perso)
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    base_url = data.get('base_url', '').strip()
    api_key = data.get('api_key', '').strip()
    model = data.get('model', '').strip()
    is_global = int(data.get('is_global', 0))
    is_client_side = int(data.get('is_client_side', 0))

    if not name or not base_url:
        conn.close()
        return jsonify({'error': 'Nom et URL requis'}), 400

    if is_global:
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard

    enc = encrypt_api_key(api_key)
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, model, is_global, is_client_side) VALUES (?, ?, 'openai', ?, ?, ?, ?, ?)",
        (user_id if not is_global else None, name, base_url, enc, model, is_global, is_client_side)
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return jsonify({'id': pid, 'name': name}), 201


@app.route('/api/presets/<int:preset_id>', methods=['PUT', 'DELETE'])
def single_preset(preset_id):
    """Met à jour (PUT) ou supprime (DELETE) un preset IA.

    Args:
        preset_id: Identifiant du preset à modifier ou supprimer.

    Returns:
        flask.Response: JSON confirmant l'opération ou une erreur 403/404.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    # Verifier propriete : global = admin only, perso = owner or admin
    if row['is_global']:
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard
    elif row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    if request.method == 'DELETE':
        cur.execute("UPDATE generated_prompts SET preset_id = NULL WHERE preset_id = ?", (preset_id,))
        cur.execute("DELETE FROM ai_presets WHERE id = ?", (preset_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})

    # PUT
    data = request.get_json() or {}
    api_key_val = data.get('api_key', None)
    if api_key_val is not None:
        enc = encrypt_api_key(api_key_val.strip()) if api_key_val.strip() else ''
    else:
        enc = row['api_key_encrypted']  # garder l'ancienne

    # Si on tente de passer en global (ou rester global), il faut etre admin
    new_is_global = int(data.get('is_global', row['is_global']))
    if new_is_global and not row['is_global']:
        # Transition perso -> global : admin only
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard
    if new_is_global != int(row['is_global']):
        # Changement d'etat is_global : admin only dans tous les cas
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard

    cur.execute("""
        UPDATE ai_presets
        SET name = ?, base_url = ?, api_key_encrypted = ?, model = ?, is_client_side = ?, is_global = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        data.get('name', row['name']),
        data.get('base_url', row['base_url']),
        enc,
        data.get('model', row['model']),
        int(data.get('is_client_side', _row_get(row, 'is_client_side', 0))),
        new_is_global,
        preset_id
    ))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/presets/<int:preset_id>/duplicate', methods=['POST'])
def duplicate_preset(preset_id):
    """Duplique un preset IA existant vers un preset personnel.

    Args:
        preset_id: Identifiant du preset à dupliquer.

    Returns:
        flask.Response: JSON avec l'ID du nouveau preset créé.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    # Seulement les globaux ou ses propres presets peuvent être dupliques
    if not row['is_global'] and row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, model, is_global, is_client_side)
        VALUES (?, ? || ' (copie)', ?, ?, ?, ?, 0, ?)
    """, (user_id, row['name'], row['engine'], row['base_url'], row['api_key_encrypted'], row['model'], _row_get(row, 'is_client_side', 0)))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return jsonify({'id': pid, 'name': row['name'] + ' (copie)'}), 201


@app.route('/api/presets/<int:preset_id>/models', methods=['GET'])
def list_preset_models(preset_id):
    """Liste les modèles disponibles via l'API d'un preset IA.

    Args:
        preset_id: Identifiant du preset dont on veut lister les modèles.

    Returns:
        flask.Response: JSON listant les modèles ou une erreur 502 si l'API est injoignable.
    """
    guard = _login_required()
    if guard: return guard
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    base_url = row['base_url'].rstrip('/')
    api_key = decrypt_api_key(row['api_key_encrypted'])
    conn.close()

    import requests
    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        r = requests.get(f'{base_url}/models', headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = []
        for m in data.get('data', data.get('models', [])):
            if isinstance(m, dict):
                models.append({'id': m.get('id', ''), 'name': m.get('name', m.get('id', '')), 'owned_by': m.get('owned_by', '')})
            elif isinstance(m, str):
                models.append({'id': m, 'name': m, 'owned_by': ''})
        return jsonify(models)
    except Exception as e:
        return jsonify({'error': f'Impossible de lister les modeles : {e}'}), 502


@app.route('/api/presets/list-models', methods=['POST'])
def list_models_temp():
    """Endpoint temporaire pour lister les modeles sans preset enregistre."""
    guard = _login_required()
    if guard: return guard
    data = request.get_json() or {}
    base_url = (data.get('base_url') or '').rstrip('/')
    api_key = (data.get('api_key') or '').strip()
    if not base_url:
        return jsonify({'error': 'URL requise'}), 400
    import requests
    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        r = requests.get(f'{base_url}/models', headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = []
        raw = data.get('data', data.get('models', []))
        for m in raw:
            if isinstance(m, dict):
                models.append({'id': m.get('id', ''), 'name': m.get('name', m.get('id', '')), 'owned_by': m.get('owned_by', '')})
            elif isinstance(m, str):
                models.append({'id': m, 'name': m, 'owned_by': ''})
        return jsonify(models)
    except Exception as e:
        return jsonify({'error': f'Impossible de lister les modeles : {e}'}), 502


