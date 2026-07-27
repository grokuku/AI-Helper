"""Routes styles for AI-Helper backend."""

from context import *


# ── Styles ──────────────────────────────────────────────────────────

@app.route('/api/styles', methods=['GET', 'POST'])
def styles():
    """Liste les styles visibles (GET) ou crée un nouveau style (POST).

    Returns:
        flask.Response: JSON listant les styles (GET) ou l'ID du style créé (POST).
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'GET':
        try:
            # Admin voit aussi les styles sans proprietaire
            if is_admin(user_id):
                rows = cur.execute("""
                    SELECT s.*, u.username, u.display_name
                    FROM styles s
                    LEFT JOIN users u ON u.id = s.user_id
                    WHERE s.is_public = 1 OR s.user_id = ? OR s.user_id IS NULL
                    ORDER BY s.is_public DESC, s.name
                """, (user_id,)).fetchall()
            else:
                rows = cur.execute("""
                    SELECT s.*, u.username, u.display_name
                    FROM styles s
                    LEFT JOIN users u ON u.id = s.user_id
                    WHERE s.is_public = 1 OR s.user_id = ?
                    ORDER BY s.is_public DESC, s.name
                """, (user_id,)).fetchall()
        except Exception as e:
            conn.close()
            return jsonify({'error': f'DB error: {e}'}), 500
        conn.close()
        result = []
        for r in rows:
            result.append({
                'id': r['id'],
                'name': r['name'],
                'style_text': r['style_text'],
                'negative_prompt': _row_get(r, 'negative_prompt', ''),
                'is_public': bool(r['is_public']),
                'user_id': r['user_id'],
                'owner_name': r['display_name'] or r['username'] or ''
            })
        return jsonify(result)

    data = request.get_json() or {}
    name = data.get('name', '').strip()
    style_text = data.get('style_text', '').strip()
    negative_prompt = data.get('negative_prompt', '').strip()
    is_public = int(data.get('is_public', 0))
    if not name or not style_text:
        conn.close()
        return jsonify({'error': 'Nom et texte requis'}), 400

    cur.execute(
        "INSERT INTO styles (user_id, name, style_text, negative_prompt, is_public) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, style_text, negative_prompt, is_public)
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return jsonify({'id': sid, 'name': name}), 201


@app.route('/api/styles/<int:style_id>', methods=['PUT', 'DELETE'])
def single_style(style_id):
    """Met à jour (PUT) ou supprime (DELETE) un style existant.

    Args:
        style_id: Identifiant du style à modifier ou supprimer.

    Returns:
        flask.Response: JSON confirmant l'opération ou une erreur 403/404.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM styles WHERE id = ?", (style_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    # Admin peut editer les styles sans proprietaire
    can_edit = (row['user_id'] == user_id) or is_admin(user_id)
    if not can_edit:
        conn.close()
        return jsonify({'error': 'Not editable'}), 403

    if request.method == 'DELETE':
        # Détacher les prompts générés qui référencent ce style (FK constraint)
        conn.execute("UPDATE generated_prompts SET style_id = NULL WHERE style_id = ?", (style_id,))
        conn.execute("DELETE FROM styles WHERE id = ?", (style_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})

    data = request.get_json() or {}
    conn.execute("""
        UPDATE styles SET name = ?, style_text = ?, negative_prompt = ?, is_public = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        data.get('name', row['name']),
        data.get('style_text', row['style_text']),
        data.get('negative_prompt', _row_get(row, 'negative_prompt', '')),
        int(data.get('is_public', row['is_public'])),
        style_id
    ))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


