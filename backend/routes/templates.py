"""Routes templates for AI-Helper backend."""

from context import *


# ── Prompt Templates ────────────────────────────────────────────────

@app.route('/api/prompts/templates', methods=['GET', 'POST'])
def prompt_templates():
    """Lister / Créer un template (personnalisé ou par défaut)."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()

    if request.method == 'GET':
        fmt = request.args.get('output_format')
        # Lister les templates visibles : ceux de l'utilisateur + publics + défauts
        query = """
            SELECT pt.*, u.username as owner_name
            FROM prompt_templates pt
            LEFT JOIN users u ON pt.user_id = u.id
            WHERE (pt.user_id = ? OR pt.is_public = 1 OR pt.is_default = 1)
        """
        params = [user_id]
        if fmt:
            query += " AND pt.output_format = ?"
            params.append(fmt)
        query += " ORDER BY pt.is_default DESC, pt.is_public DESC, pt.updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        result = []
        is_user_admin = is_admin(user_id)
        for r in rows:
            d = dict(r)
            d['examples'] = json.loads(d.get('examples', '[]')) if isinstance(d.get('examples'), str) else d.get('examples', [])
            d['editable'] = (d['user_id'] == user_id) or (is_user_admin and not d['user_id'])
            result.append(d)
        return jsonify(result)

    data = request.get_json()
    if not data or not data.get('name'):
        conn.close()
        return jsonify({'error': 'name requis'}), 400

    name = data['name'].strip()
    fmt = data.get('output_format', 'text').strip()
    system_prompt = data.get('system_prompt', '').strip()
    examples = json.dumps(data.get('examples', []))
    is_public = 1 if data.get('is_public') else 0

    conn.execute("""
        INSERT INTO prompt_templates (user_id, name, output_format, system_prompt, examples, is_public, is_default)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (user_id, name, fmt, system_prompt, examples, is_public))
    conn.commit()
    template_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({'id': template_id}), 201


@app.route('/api/prompts/templates/<int:template_id>', methods=['PUT', 'DELETE'])
def single_template(template_id):
    """Met à jour (PUT) ou supprime (DELETE) un template de prompt.

    Args:
        template_id: Identifiant du template à modifier ou supprimer.

    Returns:
        flask.Response: JSON confirmant l'opération ou une erreur 403/404.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    # Admin peut editer/supprimer n'importe quel template
    can_edit = (row['user_id'] == user_id) or is_admin(user_id)
    if not can_edit:
        conn.close()
        return jsonify({'error': 'Not editable'}), 403
    if request.method == 'PUT':
        data = request.get_json()
        if 'name' in data:
            name = data['name'].strip()
            conn.execute("UPDATE prompt_templates SET name = ? WHERE id = ?", (name, template_id))
        if 'output_format' in data:
            conn.execute("UPDATE prompt_templates SET output_format = ? WHERE id = ?", (data['output_format'].strip(), template_id))
        if 'system_prompt' in data:
            conn.execute("UPDATE prompt_templates SET system_prompt = ? WHERE id = ?", (data['system_prompt'], template_id))
        if 'examples' in data:
            conn.execute("UPDATE prompt_templates SET examples = ? WHERE id = ?", (json.dumps(data['examples']), template_id))
        if 'is_public' in data:
            conn.execute("UPDATE prompt_templates SET is_public = ? WHERE id = ?", (1 if data['is_public'] else 0, template_id))
        conn.execute("UPDATE prompt_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (template_id,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    conn.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/prompts/templates/defaults', methods=['GET'])
def get_default_templates():
    """Liste tous les templates de prompt par défaut.

    Returns:
        flask.Response: JSON listant les templates par défaut (non éditables).
    """
    guard = _login_required()
    if guard: return guard
    conn = get_db()
    rows = conn.execute("SELECT * FROM prompt_templates WHERE is_default = 1 ORDER BY name, output_format").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['examples'] = json.loads(d.get('examples', '[]')) if isinstance(d.get('examples'), str) else d.get('examples', [])
        d['editable'] = False
        result.append(d)
    return jsonify(result)


