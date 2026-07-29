"""Routes elements_presets for AI-Helper backend.

Gère les presets du sélecteur d'éléments (Elements Picker).
Chaque utilisateur peut créer, lister et supprimer ses propres presets.
Le nom d'un preset est unique par utilisateur (contrainte DB UNIQUE(user_id, name)).
"""

from context import *


@app.route('/api/elements-presets', methods=['GET'])
def list_elements_presets():
    """Liste tous les presets Elements Picker de l'utilisateur connecté.

    Returns:
        flask.Response: JSON listant les presets (id, name, data, updated_at),
            triés par nom.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    rows = conn.execute(
        'SELECT id, name, data, updated_at FROM elements_presets WHERE user_id = ? ORDER BY name',
        (user_id,)
    ).fetchall()
    result = []
    for r in rows:
        d = {
            'id': r['id'],
            'name': r['name'],
            'data': json.loads(r['data']) if isinstance(r['data'], str) else r['data'],
            'updated_at': r['updated_at'],
        }
        result.append(d)
    conn.close()
    return jsonify(result)


@app.route('/api/elements-presets', methods=['POST'])
def save_elements_preset():
    """Crée ou met à jour un preset Elements Picker (upsert par nom).

    Si un preset portant le même nom existe déjà pour cet utilisateur,
    il est mis à jour ; sinon un nouveau preset est créé.

    Returns:
        flask.Response: JSON confirmant l'opération (``{'status': 'ok', 'name': ...}``)
            ou une erreur 400 si le nom est manquant.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    preset_data = data.get('data', {})

    if not name:
        return jsonify({'error': 'Name required'}), 400

    conn = get_db()
    cur = conn.cursor()

    # Upsert : si le preset existe déjà pour cet utilisateur, on le met à jour
    existing = cur.execute(
        'SELECT id FROM elements_presets WHERE user_id = ? AND name = ?',
        (user_id, name)
    ).fetchone()

    if existing:
        cur.execute(
            'UPDATE elements_presets SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (json.dumps(preset_data), existing['id'])
        )
    else:
        cur.execute(
            'INSERT INTO elements_presets (user_id, name, data) VALUES (?, ?, ?)',
            (user_id, name, json.dumps(preset_data))
        )

    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'name': name})


@app.route('/api/elements-presets/<name>', methods=['DELETE'])
def delete_elements_preset(name):
    """Supprime un preset Elements Picker par son nom.

    Args:
        name: Le nom du preset à supprimer.

    Returns:
        flask.Response: JSON confirmant la suppression (``{'status': 'ok'}``).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    conn.execute(
        'DELETE FROM elements_presets WHERE user_id = ? AND name = ?',
        (user_id, name)
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})