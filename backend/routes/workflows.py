"""
Routes Workflows partagés pour AI-Helper backend.
Partage de workflows ComfyUI entre utilisateurs avec compression gzip.

Endpoints :
  POST   /api/workflows          — Publier ou mettre à jour
  GET    /api/workflows          — Lister (métadonnées)
  GET    /api/workflows/<id>     — Détail
  GET    /api/workflows/<id>/download — Télécharger (décompressé)
  PUT    /api/workflows/<id>     — Modifier métadonnées
  DELETE /api/workflows/<id>     — Supprimer
"""

import gzip
import logging
from context import *


def _compress(data: str) -> bytes:
    """Compresse une string JSON en BLOB gzip."""
    return gzip.compress(data.encode('utf-8'))


def _decompress(blob: bytes) -> str:
    """Décompresse un BLOB gzip en string."""
    return gzip.decompress(blob).decode('utf-8')


def _workflow_to_dict(row) -> dict:
    """Convertit une ligne BDD en dict public (sans le BLOB)."""
    return {
        'id': row['id'],
        'user_id': row['user_id'],
        'name': row['name'],
        'description': row['description'],
        'version': row['version'],
        'downloads': row['downloads'],
        'likes': row['likes'],
        'tags': (row['tags'] or '').split(',') if row['tags'] else [],
        'required_nodes': json.loads(row['required_nodes'] or '[]'),
        'required_models': json.loads(row['required_models'] or '[]'),
        'required_loras': json.loads(row['required_loras'] or '[]'),
        'comfyui_version': row['comfyui_version'],
        'thumbnail': row['thumbnail'] or '',
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


@app.route('/api/workflows', methods=['POST'])
def create_or_update_workflow():
    """Publie un nouveau workflow ou met à jour un existant (version+1)."""
    guard = _login_required()
    if guard:
        return guard
    guard = _require_json()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    name = (data.get('name') or '').strip()
    workflow_json = data.get('workflow_json') or ''
    existing_id = data.get('existing_id')

    if not name:
        return jsonify({'error': 'name requis'}), 400
    if not workflow_json:
        return jsonify({'error': 'workflow_json requis'}), 400

    # Limite de taille pour éviter l'épuisement du disque
    MAX_WORKFLOW_SIZE = 5 * 1024 * 1024  # 5 MB
    if len(workflow_json.encode('utf-8')) > MAX_WORKFLOW_SIZE:
        return jsonify({'error': 'Workflow trop volumineux (max 5 MB)'}), 413

    description = (data.get('description') or '').strip()
    tags = (data.get('tags') or '').strip()
    required_nodes = json.dumps(data.get('required_nodes', []), ensure_ascii=False)
    required_models = json.dumps(data.get('required_models', []), ensure_ascii=False)
    required_loras = json.dumps(data.get('required_loras', []), ensure_ascii=False)
    comfyui_version = (data.get('comfyui_version') or '').strip()
    thumbnail = (data.get('thumbnail') or '').strip()  # base64 JPEG

    blob = _compress(workflow_json)
    decompressed_size = len(workflow_json.encode('utf-8'))

    conn = get_db()
    try:
        if existing_id:
            # Mise à jour atomique : UPDATE avec vérification propriété dans le WHERE
            cur = conn.execute("""
                UPDATE shared_workflows SET
                    name = ?, description = ?, workflow_data = ?,
                    decompressed_size = ?, required_nodes = ?,
                    required_models = ?, required_loras = ?,
                    tags = ?, comfyui_version = ?, thumbnail = ?,
                    version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND (user_id = ? OR ?)
            """, (name, description, blob, decompressed_size,
                  required_nodes, required_models, required_loras,
                  tags, comfyui_version, thumbnail, existing_id, user_id, 1 if is_admin(user_id) else 0))
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT id FROM shared_workflows WHERE id = ?",
                    (existing_id,)
                ).fetchone()
                if not row:
                    return jsonify({'error': 'Workflow introuvable'}), 404
                return jsonify({'error': 'Vous ne pouvez modifier que vos propres workflows'}), 403
            conn.commit()
            return jsonify({'id': existing_id, 'version': None, 'updated': True})
        else:
            # Nouveau
            cur = conn.execute("""
                INSERT INTO shared_workflows
                    (user_id, name, description, workflow_data, decompressed_size,
                     required_nodes, required_models, required_loras,
                     tags, comfyui_version, thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, name, description, blob, decompressed_size,
                  required_nodes, required_models, required_loras,
                  tags, comfyui_version, thumbnail))
            new_id = cur.lastrowid
            conn.commit()
            return jsonify({'id': new_id, 'version': 1, 'updated': False}), 201
    finally:
        conn.close()


@app.route('/api/workflows', methods=['GET'])
def list_workflows():
    """Liste les workflows publics (métadonnées sans le JSON)."""
    guard = _login_required()
    if guard:
        return guard

    q = request.args.get('q', '').strip().lower()
    tags_filter = request.args.get('tags', '').strip().lower()
    sort = request.args.get('sort', 'created_at').strip()
    page = int(request.args.get('page', 1))
    limit = min(int(request.args.get('limit', 20)), 100)

    SORT_MAP = {
        'created_at': 'created_at', 'downloads': 'downloads',
        'likes': 'likes', 'updated_at': 'updated_at', 'name': 'name',
    }
    sort_col = SORT_MAP.get(sort, 'created_at')

    conditions = ["is_public = 1"]
    params = []

    if q:
        conditions.append("(LOWER(name) LIKE ? OR LOWER(description) LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])

    if tags_filter:
        for tag in tags_filter.split(','):
            tag = tag.strip()
            if tag:
                conditions.append("LOWER(tags) LIKE ?")
                params.append(f"%{tag}%")

    where = " AND ".join(conditions)
    offset = (page - 1) * limit

    conn = get_db()
    try:
        # Total
        total = conn.execute(
            f"SELECT COUNT(*) FROM shared_workflows WHERE {where}", params
        ).fetchone()[0]

        # Items
        rows = conn.execute(
            f"SELECT id, user_id, name, description, version, downloads, likes, "
            f"tags, required_nodes, required_models, required_loras, "
            f"comfyui_version, thumbnail, created_at, updated_at "
            f"FROM shared_workflows WHERE {where} "
            f"ORDER BY {sort_col} DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

        items = []
        for r in rows:
            author = conn.execute(
                "SELECT username, display_name FROM users WHERE id = ?",
                (r['user_id'],)
            ).fetchone()
            d = {
                'id': r['id'],
                'name': r['name'],
                'description': r['description'],
                'version': r['version'],
                'downloads': r['downloads'],
                'likes': r['likes'],
                'author': author['display_name'] or author['username'] if author else '?',
                'tags': (r['tags'] or '').split(',') if r['tags'] else [],
                'required_nodes': json.loads(r['required_nodes'] or '[]'),
                'required_models': json.loads(r['required_models'] or '[]'),
                'required_loras': json.loads(r['required_loras'] or '[]'),
                'comfyui_version': r['comfyui_version'],
                'thumbnail': r['thumbnail'] or '',
                'created_at': r['created_at'],
                'updated_at': r['updated_at'],
            }
            items.append(d)
    finally:
        conn.close()

    return jsonify({
        'total': total,
        'page': page,
        'limit': limit,
        'items': items,
    })


@app.route('/api/workflows/<int:workflow_id>', methods=['GET'])
def get_workflow(workflow_id):
    """Détail d'un workflow (sans le JSON brut)."""
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, user_id, name, description, version, downloads, likes, "
            "tags, required_nodes, required_models, required_loras, "
            "comfyui_version, thumbnail, created_at, updated_at "
            "FROM shared_workflows WHERE id = ? AND is_public = 1",
            (workflow_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Workflow introuvable'}), 404

        d = _workflow_to_dict(row)
        author = conn.execute(
            "SELECT username, display_name FROM users WHERE id = ?",
            (d['user_id'],)
        ).fetchone()
        d['author'] = author['display_name'] or author['username'] if author else '?'
        return jsonify(d)
    finally:
        conn.close()


@app.route('/api/workflows/<int:workflow_id>/download', methods=['GET'])
def download_workflow(workflow_id):
    """Télécharge un workflow : décompresse le BLOB, incrémente le compteur."""
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT workflow_data, required_nodes, required_models, required_loras, "
            "name, version FROM shared_workflows WHERE id = ? AND is_public = 1",
            (workflow_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Workflow introuvable'}), 404

        # Incrémenter le compteur
        conn.execute(
            "UPDATE shared_workflows SET downloads = downloads + 1 WHERE id = ?",
            (workflow_id,)
        )
        conn.commit()

        # Décompresser
        workflow_json = _decompress(row['workflow_data'])

        return jsonify({
            'workflow_json': workflow_json,
            'required_nodes': json.loads(row['required_nodes'] or '[]'),
            'required_models': json.loads(row['required_models'] or '[]'),
            'required_loras': json.loads(row['required_loras'] or '[]'),
            'name': row['name'],
            'version': row['version'],
        })
    finally:
        conn.close()


@app.route('/api/workflows/<int:workflow_id>', methods=['PUT'])
def update_workflow_metadata(workflow_id):
    """Modifie les métadonnées d'un workflow (nom, description, tags)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM shared_workflows WHERE id = ?",
            (workflow_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Workflow introuvable'}), 404
        if row['user_id'] != user_id and not is_admin(user_id):
            return jsonify({'error': 'Accès refusé'}), 403

        updates = []
        params = []
        if 'name' in data:
            updates.append("name = ?")
            params.append(data['name'].strip())
        if 'description' in data:
            updates.append("description = ?")
            params.append(data['description'].strip())
        if 'tags' in data:
            updates.append("tags = ?")
            params.append(data['tags'].strip())

        if not updates:
            return jsonify({'error': 'Aucun champ à modifier'}), 400

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(workflow_id)

        conn.execute(
            f"UPDATE shared_workflows SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
        return jsonify({'status': 'ok'})
    finally:
        conn.close()


@app.route('/api/workflows/<int:workflow_id>', methods=['DELETE'])
def delete_workflow(workflow_id):
    """Supprime un workflow (propriétaire ou admin)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM shared_workflows WHERE id = ?",
            (workflow_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Workflow introuvable'}), 404
        if row['user_id'] != user_id and not is_admin(user_id):
            return jsonify({'error': 'Accès refusé'}), 403

        # Recuperer les models/loras du workflow avant suppression
        wf = conn.execute(
            "SELECT required_models, required_loras FROM shared_workflows WHERE id = ?",
            (workflow_id,)
        ).fetchone()
        uploaded_ids = []
        if wf:
            import json as _json
            for field in ['required_models', 'required_loras']:
                try:
                    items = _json.loads(wf[field] or '[]')
                    for item in items:
                        if isinstance(item, dict) and item.get('upload_id'):
                            uploaded_ids.append(item['upload_id'])
                except Exception:
                    pass

        conn.execute("DELETE FROM shared_workflows WHERE id = ?", (workflow_id,))

        # Supprimer les fichiers uploades orphelins (non utilises par d'autres workflows)
        deleted_files = []
        for uid in uploaded_ids:
            still_used = False
            all_wfs = conn.execute("SELECT required_models, required_loras FROM shared_workflows").fetchall()
            for other_wf in all_wfs:
                for field in ['required_models', 'required_loras']:
                    try:
                        other_items = _json.loads(other_wf[field] or '[]')
                        for item in other_items:
                            if isinstance(item, dict) and item.get('upload_id') == uid:
                                still_used = True
                                break
                    except Exception:
                        pass
                    if still_used:
                        break
                if still_used:
                    break
            if not still_used:
                try:
                    from storage import get_storage
                    storage = get_storage()
                    file_row = conn.execute(
                        "SELECT file_path FROM file_uploads WHERE id = ?",
                        (uid,)
                    ).fetchone()
                    if file_row:
                        storage.delete(file_row["file_path"])
                        deleted_files.append(file_row['file_path'])
                    conn.execute("DELETE FROM file_uploads WHERE id = ?", (uid,))
                except Exception:
                    pass

        conn.commit()
        return jsonify({'status': 'ok', 'deleted_files': deleted_files})
    finally:
        conn.close()
