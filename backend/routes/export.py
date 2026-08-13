"""Routes export for AI-Helper backend."""

from context import *


@app.route('/api/export', methods=['GET'])
def export_md():
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    if not (is_admin(user_id) or is_kw_editor(user_id)):
        return jsonify({'error': 'Accès refusé'}), 403

    if not DB_PATH.exists():
        return jsonify({'error': 'Base de données vide'}), 400

    content = export_to_markdown(str(DB_PATH))
    buf = io.BytesIO(content.encode('utf-8'))
    buf.seek(0)
    return send_file(
        buf,
        mimetype='text/markdown',
        as_attachment=True,
        download_name='Keywords-Export.md'
    )


