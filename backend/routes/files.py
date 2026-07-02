"""
Routes Files — Upload chunké pour gros fichiers (models, custom nodes).

Tous les fichiers transitent par le backend Flask. Le storage backend
(SFTP, local, etc.) est abstrait derrière storage.py.

Flow :
  1. POST /api/files/init          → crée un upload_id
  2. POST /api/files/chunk         → append un chunk au temp file
  3. POST /api/files/complete      → upload vers storage, supprime temp
  4. GET  /api/files/<id>/status   → progression
  5. GET  /api/files/<id>/download → download depuis storage
  6. DELETE /api/files/<id>        → supprime du storage
"""

import os
import json
import logging
import tempfile
import secrets
from context import *
from storage import get_storage, StorageBackend

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB par chunk
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB max
TEMP_DIR = tempfile.gettempdir() + "/fria_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


@app.route('/api/files/check', methods=['POST'])
def check_file_exists():
    """Vérifie si un fichier a deja ete uploade (deduplication par fingerprint).
    Retourne {exists: true, file_path: ...} si trouve, sinon {exists: false}.
    """
    guard = _login_required()
    if guard:
        return guard
    data = request.get_json() or {}

    size = int(data.get('size', 0))
    head = (data.get('head') or '').strip()
    tail = (data.get('tail') or '').strip()

    if size <= 0 or not head or not tail:
        return jsonify({'exists': False})

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT upload_id, final_path, filename FROM file_uploads "
            "WHERE size = ? AND fingerprint_head = ? AND fingerprint_tail = ? "
            "AND status = 'complete' LIMIT 1",
            (size, head, tail)
        ).fetchone()
        if row:
            return jsonify({
                'exists': True,
                'upload_id': row['upload_id'],
                'file_path': row['final_path'],
                'filename': row['filename'],
            })
        return jsonify({'exists': False})
    finally:
        conn.close()


@app.route('/api/files/init', methods=['POST'])
def init_upload():
    """Initialise un upload chunké. Retourne upload_id + chunk_size."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    filename = (data.get('filename') or '').strip()
    size = int(data.get('size', 0))
    file_type = (data.get('type') or '').strip()  # 'model', 'node', 'screenshot'

    if not filename or size <= 0:
        return jsonify({'error': 'filename et size requis'}), 400
    if file_type not in ('model', 'node', 'screenshot', 'checkpoint', 'lora', 'vae', 'clip', 'clip_vision', 'controlnet', 'unet', 'unet_gguf', 'upscale', 'gligen', 'hypernetwork', 'text_encoder', 'style_model'):
        return jsonify({'error': f'type "{file_type}" non reconnu'}), 400
    if size > MAX_FILE_SIZE:
        return jsonify({'error': f'Fichier trop volumineux (max {MAX_FILE_SIZE // (1024**3)} GB)'}), 413

    upload_id = secrets.token_urlsafe(16)
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    # Verifier si le storage backend supporte l'ecriture directe (pas de temp local)
    storage = get_storage()
    supports_direct = type(storage).__name__ == 'SFTPStorage'
    remote_path = ""
    temp_path = os.path.join(TEMP_DIR, f"{upload_id}.tmp")

    if supports_direct:
        # Streaming direct vers le storage (ex: SFTP) — pas de fichier local
        remote_path = f"workflows/{file_type}s/{upload_id}/{filename}"
        storage.create_empty(remote_path)
        temp_path = ""  # pas de fichier local
    else:
        # Fallback: fichier temporaire local
        with open(temp_path, 'wb') as f:
            pass

    conn = get_db()
    try:
        if supports_direct:
            conn.execute("""
                INSERT INTO file_uploads (upload_id, user_id, filename, size, type,
                                           chunk_size, total_chunks, temp_path, final_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (upload_id, user_id, filename, size, file_type,
                  CHUNK_SIZE, total_chunks, temp_path, remote_path))
        else:
            conn.execute("""
                INSERT INTO file_uploads (upload_id, user_id, filename, size, type,
                                           chunk_size, total_chunks, temp_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (upload_id, user_id, filename, size, file_type,
                  CHUNK_SIZE, total_chunks, temp_path))
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[files] Init upload {upload_id}: {filename} ({size} bytes, {total_chunks} chunks, direct={supports_direct})")

    return jsonify({
        'upload_id': upload_id,
        'chunk_size': CHUNK_SIZE,
        'total_chunks': total_chunks,
    })


@app.route('/api/files/chunk', methods=['POST'])
def upload_chunk():
    """Reçoit un chunk et l'append au fichier temporaire."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    upload_id = request.form.get('upload_id', '').strip()
    chunk_index = int(request.form.get('chunk_index', -1))

    if not upload_id or chunk_index < 0:
        return jsonify({'error': 'upload_id et chunk_index requis'}), 400

    if 'data' not in request.files:
        return jsonify({'error': 'data (chunk binaire) requis'}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT temp_path, received_chunks, total_chunks, status, user_id, final_path, filename, type FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404
        if row['user_id'] != user_id:
            return jsonify({'error': 'Accès refusé'}), 403
        if row['status'] != 'uploading':
            return jsonify({'error': f'Upload {row["status"]}, impossible de recevoir des chunks'}), 400

        temp_path = row['temp_path']
        chunk_data = request.files['data'].read()

        if temp_path:
            # Ecriture locale (fallback)
            with open(temp_path, 'ab') as f:
                f.write(chunk_data)
        else:
            # Streaming direct vers le storage (SFTP)
            # Recuperer le remote_path — soit du champ final_path, soit on le construit
            if row['final_path']:
                remote = row['final_path']
            else:
                remote = f"workflows/{row['type']}s/{upload_id}/{row['filename']}"
            storage = get_storage()
            success = storage.append_chunk(remote, chunk_data)
            if not success:
                return jsonify({'error': 'Échec du chunk sur le stockage distant'}), 500

        new_received = row['received_chunks'] + 1
        conn.execute(
            "UPDATE file_uploads SET received_chunks = ? WHERE upload_id = ?",
            (new_received, upload_id)
        )
        conn.commit()

        return jsonify({
            'received': chunk_index,
            'total_received': new_received,
            'total_chunks': row['total_chunks'],
        })
    finally:
        conn.close()


@app.route('/api/files/complete', methods=['POST'])
def complete_upload():
    """Finalise l'upload : vérifie les chunks, upload vers storage."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    upload_id = (data.get('upload_id') or '').strip()
    if not upload_id:
        return jsonify({'error': 'upload_id requis'}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM file_uploads WHERE upload_id = ? AND user_id = ?",
            (upload_id, user_id)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404
        if row['status'] != 'uploading':
            return jsonify({'error': f'Upload déjà {row["status"]}'}), 400
        if row['received_chunks'] != row['total_chunks']:
            return jsonify({
                'error': f'Chunks manquants: {row["received_chunks"]}/{row["total_chunks"]}'
            }), 400

        # Vérifier la taille du temp file
        actual_size = os.path.getsize(row['temp_path'])
        if actual_size != row['size']:
            logging.warning(f"[files] Size mismatch: expected {row['size']}, got {actual_size}")

        # Upload vers le storage
        file_type = row['type']
        filename = row['filename']
        remote_path = f"workflows/{file_type}s/{upload_id}/{filename}"

        storage = get_storage()
        success = storage.upload(row['temp_path'], remote_path)

        # Supprimer le temp file
        try:
            os.remove(row['temp_path'])
        except Exception:
            pass

        if not success:
            conn.execute("UPDATE file_uploads SET status = 'error' WHERE upload_id = ?", (upload_id,))
            conn.commit()
            return jsonify({'error': 'Échec de l\'upload vers le stockage'}), 500

        # Marquer comme complete
        # Store fingerprint for future deduplication
        fp_head = (data.get('fingerprint_head') or '').strip()
        fp_tail = (data.get('fingerprint_tail') or '').strip()
        conn.execute(
            "UPDATE file_uploads SET status = 'complete', final_path = ?, fingerprint_head = ?, fingerprint_tail = ? WHERE upload_id = ?",
            (remote_path, fp_head, fp_tail, upload_id)
        )
        conn.commit()

        logging.info(f"[files] Upload {upload_id} complete → {remote_path}")

        return jsonify({
            'upload_id': upload_id,
            'file_path': remote_path,
            'size': actual_size,
        })
    finally:
        conn.close()


@app.route('/api/files/<upload_id>/status', methods=['GET'])
def upload_status(upload_id):
    """Retourne la progression d'un upload."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT received_chunks, total_chunks, status, final_path, size, filename, type "
            "FROM file_uploads WHERE upload_id = ? AND user_id = ?",
            (upload_id, user_id)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404

        return jsonify({
            'received_chunks': row['received_chunks'],
            'total_chunks': row['total_chunks'],
            'status': row['status'],
            'final_path': row['final_path'],
            'size': row['size'],
            'filename': row['filename'],
            'type': row['type'],
        })
    finally:
        conn.close()


@app.route('/api/files/<upload_id>/download', methods=['GET'])
def download_file(upload_id):
    """Download un fichier depuis le storage → streaming HTTP vers le client."""
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT final_path, filename, status, type FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Fichier introuvable'}), 404
        if row['status'] != 'complete':
            return jsonify({'error': 'Upload pas finalisé'}), 400
        if not row['final_path']:
            return jsonify({'error': 'Chemin de stockage manquant'}), 500
    finally:
        conn.close()

    # Download depuis le storage vers un temp file
    storage = get_storage()
    local_tmp = os.path.join(TEMP_DIR, f"dl_{upload_id}_{row['filename']}")

    if not storage.download(row['final_path'], local_tmp):
        return jsonify({'error': 'Échec du téléchargement depuis le stockage'}), 500

    # Stream vers le client
    return send_file(
        local_tmp,
        as_attachment=True,
        download_name=row['filename'],
        mimetype='application/octet-stream',
    )


@app.route('/api/files/<upload_id>', methods=['DELETE'])
def delete_file(upload_id):
    """Supprime un fichier du stockage."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT final_path, user_id, status FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Fichier introuvable'}), 404
        if row['user_id'] != user_id and not is_admin(user_id):
            return jsonify({'error': 'Accès refusé'}), 403

        # Supprimer du storage
        if row['final_path'] and row['status'] == 'complete':
            storage = get_storage()
            storage.delete(row['final_path'])

        # Supprimer de la BDD
        conn.execute("DELETE FROM file_uploads WHERE upload_id = ?", (upload_id,))
        conn.commit()

        return jsonify({'status': 'ok'})
    finally:
        conn.close()