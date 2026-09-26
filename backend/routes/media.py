"""Routes Media — sauvegarde serveur des médias produits par les nodes AIH.

Permet au node « AIH save media » (pack ComfyUI-AI-Helper) d'envoyer les
médias (image / vidéo / audio) vers le serveur AI-Helper au lieu de les écrire
localement, en upload CHUNKÉ (robuste derrière un reverse-proxy type Caddy).

Flow (calqué sur ``routes/files.py``) :
  1. POST /api/media/init          → crée l'upload (upload_id, chunk_size)
  2. POST /api/media/chunk         → append un chunk au fichier temporaire
  3. POST /api/media/complete      → range le fichier sous media/<user>/… + métadonnées
  4. GET  /api/media/<id>/download → sert le média (propriétaire ou admin)
  5. GET  /api/media               → liste paginée des médias de l'utilisateur

Contraintes :
  - Authentification obligatoire (``_login_required``) : le ``user_id`` est
    DÉRIVÉ DU TOKEN, jamais fourni par le client.
  - Le fichier final est rangé via ``get_storage()`` (jamais de chemin disque
    en dur) → fonctionne avec LocalStorage ET SFTPStorage.
  - Sanitization stricte de ``user_id`` / ``subfolder`` / ``filename`` : tout
    segment ``.`` / ``..`` / absolu est neutralisé (confinement anti
    path-traversal).
  - Collision de nom → suffixe ``_0001`` (miroir du comportement local du node).
  - Le prompt (texte) et le workflow (JSON) sont persistés en colonnes de
    ``media_files`` (récupérables par id, sans dépendance à un format de
    fichier compagnon).
"""

import contextlib
import logging
import os
import re
import secrets
import tempfile

from context import *
from storage import get_storage

CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB par chunk (cohérent avec files.py)
DEFAULT_MAX_MEDIA_SIZE = 50 * 1024 * 1024 * 1024  # 50 GB max par défaut
TEMP_DIR = os.path.join(tempfile.gettempdir(), "aih_media_uploads")
os.makedirs(TEMP_DIR, exist_ok=True)

# Types de médias acceptés et extensions autorisées par type.
MEDIA_KINDS = ("image", "video", "audio")
_EXT_BY_KIND = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".webm", ".gif"},
    "audio": {".wav", ".mp3", ".flac"},
}
_SAFE_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
}


# ── Configuration (préfixe + taille max) ──────────────────────────────

def _media_prefix():
    """Préfixe de rangement dans le storage (défaut ``media``).

    Surchargeable par l'env ``AIH_MEDIA_DIR`` (une seule composante, sans
    slash). Le chemin reste relatif à la racine du storage abstrait : aucune
    racine disque n'est codée en dur (Local ET SFTP fonctionnent).
    """
    prefix = (os.environ.get("AIH_MEDIA_DIR") or "media").strip()
    return prefix.strip("/") or "media"


def _get_max_media_size():
    """Taille maximale d'un média en octets.

    Priorité : env ``AIH_MEDIA_MAX_SIZE`` → réglage ``app_settings``
    ``media_max_size`` → défaut 50 GB (aligné sur ``files.py``).
    """
    try:
        n = int(os.environ.get("AIH_MEDIA_MAX_SIZE", "0"))
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'media_max_size'"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            n = int(row[0])
            if n > 0:
                return n
    except Exception:
        pass
    return DEFAULT_MAX_MEDIA_SIZE


# ── Sanitization / construction de chemin ─────────────────────────────

def _sanitize_user_id(user_id):
    """Ne garde que des caractères filesystem-safe ([A-Za-z0-9_-])."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id or ""))
    return safe or "unknown"


def _sanitize_segment(segment):
    """Neutralise un segment : séparateurs, ``..``, caractères réservés."""
    segment = str(segment or "").replace("\\", "/")
    segment = segment.replace("..", "")
    segment = re.sub(r'[<>:"|?*\x00-\x1f]', "", segment)
    segment = segment.strip(" /.")
    # Un nom/sous-dossier est mono-segment : « / » devient « _ » (pas de sous-chemin).
    return segment.replace("/", "_")


def _sanitize_subfolder(subfolder):
    """Réduit un sous-dossier à une suite de segments sûrs (``a/b``)."""
    raw = str(subfolder or "").replace("\\", "/")
    parts = []
    for seg in raw.split("/"):
        s = _sanitize_segment(seg)
        if s and s != ".":
            parts.append(s)
    return "/".join(parts)


def _sanitize_filename(name):
    """Nom de base sans extension, sans séparateur ni ``..``."""
    return _sanitize_segment(name) or "untitled"


def _normalize_ext(ext):
    ext = str(ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext


def _validate_ext(kind, ext):
    """L'extension doit être syntaxiquement sûre ET cohérente avec le type."""
    if not _SAFE_EXT_RE.match(ext):
        return False
    return ext in _EXT_BY_KIND.get(kind, set())


def _assert_safe_relpath(rel):
    """Confine le chemin : relatif, sans ``..`` ni segment vide."""
    if not rel or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"Chemin de stockage invalide : {rel!r}")
    for seg in rel.split("/"):
        if seg in ("", ".", ".."):
            raise ValueError(f"Chemin de stockage invalide : {rel!r}")
    return True


def _build_remote_path(user_id, subfolder, filename, ext):
    """Construit ``<prefix>/<user_id>/<subfolder>/<filename><ext>`` (relatif)."""
    parts = [_media_prefix(), _sanitize_user_id(user_id)]
    safe_sub = _sanitize_subfolder(subfolder)
    if safe_sub:
        parts.append(safe_sub)
    rel = "/".join(parts) + "/" + _sanitize_filename(filename) + ext
    _assert_safe_relpath(rel)
    return rel


def _resolve_collision(storage, remote_path):
    """Retourne un chemin libre en suffixant ``_0001`` en cas de collision.

    Miroir du comportement local du node (``_NNNN``).
    """
    if not storage.exists(remote_path):
        return remote_path
    base, ext = os.path.splitext(remote_path)
    counter = 1
    while counter < 100000:
        candidate = f"{base}_{counter:04d}{ext}"
        if not storage.exists(candidate):
            return candidate
        counter += 1
    raise RuntimeError("Impossible de trouver un nom de fichier libre sur le stockage")


def _media_json(row):
    """Sérialise une ligne ``media_files`` pour l'API (contrat figé)."""
    media_id = row["id"]
    filename = f"{row['filename']}{row['ext']}" if row["filename"] else ""
    return {
        "id": media_id,
        "path": row["final_path"] or "",
        "filename": filename,
        "subfolder": row["subfolder"] or "",
        "size": row["size"] or 0,
        "url": f"/api/media/{media_id}/download",
        "created_at": row["created_at"] or "",
        "has_prompt": bool(row["has_prompt"]),
        "has_workflow": bool(row["has_workflow"]),
        "kind": row["kind"],
    }


# ── Routes ────────────────────────────────────────────────────────────

@app.route('/api/media/init', methods=['POST'])
def media_init():
    """Initialise un upload média chunké (auth obligatoire)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json(silent=True) or {}

    kind = (data.get('kind') or '').strip().lower()
    if kind not in MEDIA_KINDS:
        return jsonify({'error': f'kind "{kind}" non reconnu (image/video/audio)'}), 400

    ext = _normalize_ext(data.get('ext'))
    if not _validate_ext(kind, ext):
        return jsonify({'error': f'extension "{ext}" invalide pour kind "{kind}"'}), 400

    filename = _sanitize_filename(data.get('filename'))
    subfolder = _sanitize_subfolder(data.get('subfolder'))

    try:
        size = int(data.get('size', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'size invalide'}), 400
    if size <= 0:
        return jsonify({'error': 'size requis'}), 400

    max_size = _get_max_media_size()
    if size > max_size:
        return jsonify({'error': f'Fichier trop volumineux (max {max_size // (1024 ** 3)} GB)'}), 413

    prompt = data.get('prompt') or ''
    workflow = data.get('workflow') or ''
    if not isinstance(prompt, str) or not isinstance(workflow, str):
        return jsonify({'error': 'prompt/workflow doivent être des chaînes'}), 400

    upload_id = secrets.token_urlsafe(16)
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    # Les octets transitent TOUJOURS par le backend Flask (Local comme SFTP) :
    # on écrit un fichier temporaire local, puis complete_upload pousse vers le
    # storage abstrait. Le client n'a jamais besoin des credentials SFTP.
    temp_path = os.path.join(TEMP_DIR, f"{upload_id}.tmp")
    with open(temp_path, 'wb'):
        pass

    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO media_files
                (upload_id, user_id, subfolder, filename, ext, kind, size,
                 status, received_chunks, total_chunks, temp_path, final_path,
                 prompt_text, workflow_json, has_prompt, has_workflow)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'uploading', 0, ?, ?, '', ?, ?, ?, ?)
        """, (upload_id, user_id, subfolder, filename, ext, kind, size,
              total_chunks, temp_path, prompt, workflow,
              1 if prompt else 0, 1 if workflow else 0))
        media_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[media] Init upload {upload_id}: {filename}{ext} ({size} bytes, "
                 f"{total_chunks} chunks, kind={kind}, user={user_id})")

    return jsonify({
        'upload_id': upload_id,
        'media_id': media_id,
        'chunk_size': CHUNK_SIZE,
        'total_chunks': total_chunks,
    })


@app.route('/api/media/chunk', methods=['POST'])
def media_chunk():
    """Reçoit un chunk et l'append au fichier temporaire de l'upload."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    upload_id = (request.form.get('upload_id') or '').strip()
    try:
        chunk_index = int(request.form.get('chunk_index', -1))
    except (TypeError, ValueError):
        return jsonify({'error': 'chunk_index invalide'}), 400

    if not upload_id or chunk_index < 0:
        return jsonify({'error': 'upload_id et chunk_index requis'}), 400
    if 'data' not in request.files:
        return jsonify({'error': 'data (chunk binaire) requis'}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM media_files WHERE upload_id = ?", (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404
        if row['user_id'] != user_id:
            return jsonify({'error': 'Accès refusé'}), 403
        if row['status'] != 'uploading':
            return jsonify({'error': f'Upload {row["status"]}, impossible de recevoir des chunks'}), 400

        temp_path = row['temp_path']
        if not temp_path or not os.path.isfile(temp_path):
            return jsonify({'error': 'Fichier temporaire introuvable'}), 500

        chunk_stream = request.files['data'].stream
        with open(temp_path, 'ab') as f:
            while True:
                buf = chunk_stream.read(65536)
                if not buf:
                    break
                f.write(buf)

        new_received = row['received_chunks'] + 1
        conn.execute(
            "UPDATE media_files SET received_chunks = ? WHERE upload_id = ?",
            (new_received, upload_id),
        )
        conn.commit()

        return jsonify({
            'received': chunk_index,
            'total_received': new_received,
            'total_chunks': row['total_chunks'],
        })
    finally:
        conn.close()


@app.route('/api/media/complete', methods=['POST'])
def media_complete():
    """Finalise l'upload : range le fichier sous media/<user>/… + métadonnées."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json(silent=True) or {}

    upload_id = (data.get('upload_id') or '').strip()
    if not upload_id:
        return jsonify({'error': 'upload_id requis'}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM media_files WHERE upload_id = ? AND user_id = ?",
            (upload_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404
        if row['status'] != 'uploading':
            return jsonify({'error': f'Upload déjà {row["status"]}'}), 400
        if row['received_chunks'] != row['total_chunks']:
            return jsonify({
                'error': f'Chunks manquants: {row["received_chunks"]}/{row["total_chunks"]}'
            }), 400

        temp_path = row['temp_path'] or ''
        if not os.path.isfile(temp_path):
            conn.execute("UPDATE media_files SET status = 'error' WHERE upload_id = ?", (upload_id,))
            conn.commit()
            return jsonify({'error': 'Fichier temporaire introuvable'}), 500

        actual_size = os.path.getsize(temp_path)
        if actual_size != row['size']:
            logging.warning(f"[media] Size mismatch {upload_id}: expected {row['size']}, got {actual_size}")

        try:
            remote_path = _build_remote_path(user_id, row['subfolder'], row['filename'], row['ext'])
        except ValueError:
            conn.execute("UPDATE media_files SET status = 'error' WHERE upload_id = ?", (upload_id,))
            conn.commit()
            return jsonify({'error': 'Nom de fichier invalide'}), 400

        storage = get_storage()
        remote_path = _resolve_collision(storage, remote_path)

        if not storage.upload(temp_path, remote_path):
            conn.execute("UPDATE media_files SET status = 'error' WHERE upload_id = ?", (upload_id,))
            conn.commit()
            return jsonify({'error': "Échec de l'upload vers le stockage"}), 500
        with contextlib.suppress(Exception):
            os.remove(temp_path)

        # Nom de base après résolution de collision (le chemin finit par ext).
        final_name = os.path.basename(remote_path)
        final_base = final_name[: -len(row['ext'])] if row['ext'] else final_name

        conn.execute(
            "UPDATE media_files SET status = 'complete', final_path = ?, filename = ?, size = ? "
            "WHERE upload_id = ?",
            (remote_path, final_base, actual_size, upload_id),
        )
        conn.commit()

        full_row = conn.execute(
            "SELECT * FROM media_files WHERE upload_id = ?", (upload_id,)
        ).fetchone()

        logging.info(f"[media] Upload {upload_id} complete → {remote_path}")
        return jsonify(_media_json(full_row))
    finally:
        conn.close()


@app.route('/api/media', methods=['GET'])
def media_list():
    """Liste paginée des médias complets de l'utilisateur courant (galerie)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    try:
        page = max(int(request.args.get('page', 1)), 1)
    except (TypeError, ValueError):
        return jsonify({'error': 'page invalide'}), 400
    try:
        limit = min(max(int(request.args.get('limit', 50)), 1), 200)
    except (TypeError, ValueError):
        return jsonify({'error': 'limit invalide'}), 400

    conn = get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM media_files WHERE user_id = ? AND status = 'complete'",
            (user_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM media_files WHERE user_id = ? AND status = 'complete' "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, (page - 1) * limit),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({
        'items': [_media_json(r) for r in rows],
        'total': total,
        'page': page,
        'limit': limit,
    })


@app.route('/api/media/<int:media_id>/download', methods=['GET'])
def media_download(media_id):
    """Sert un média depuis le storage (propriétaire ou admin)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)).fetchone()
    finally:
        conn.close()

    if not row or row['status'] != 'complete' or not row['final_path']:
        return jsonify({'error': 'Média introuvable'}), 404
    if row['user_id'] != user_id and not is_admin(user_id):
        return jsonify({'error': 'Accès refusé'}), 403

    storage = get_storage()
    ext = row['ext'] or ''
    local_tmp = os.path.join(TEMP_DIR, f"dl_{media_id}_{os.path.basename(row['filename'])}{ext}")

    if not storage.download(row['final_path'], local_tmp):
        return jsonify({'error': 'Échec du téléchargement depuis le stockage'}), 500

    def _cleanup_local_tmp():
        with contextlib.suppress(Exception):
            os.remove(local_tmp)

    try:
        response = send_file(
            local_tmp,
            as_attachment=False,
            download_name=f"{row['filename']}{ext}",
            mimetype=_MIME_BY_EXT.get(ext, 'application/octet-stream'),
        )
    except Exception:
        _cleanup_local_tmp()
        raise

    response.call_on_close(_cleanup_local_tmp)
    return response
