"""Routes Preview — Upload et consultation d'images de preview.

Permet à un utilisateur authentifié (session ou API key Bearer) d'uploader
une image PNG, de lister ses 10 dernières images et de récupérer une image
par son ID. Les images sont stockées sur le filesystem et leurs métadonnées
en BDD (table ``preview_images``).

Routes :
  POST /api/preview            — Upload une image PNG
  GET  /api/preview/recent     — Liste les 10 dernières images
  GET  /api/preview/image/<id> — Sert une image par son ID
"""

import logging
import uuid

from context import *

# ── Configuration du stockage ─────────────────────────────────────────

# Dossier racine des uploads (à côté du dossier backend)
PREVIEW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads', 'previews')

# Extensions autorisées (on force le PNG selon le contrat, mais on accepte
# aussi les variantes classiques pour ne pas casser sur un faux négatif).
ALLOWED_EXTENSIONS = {'.png'}

# Taille maximale du fichier uploadé : 20 MB
MAX_PREVIEW_SIZE = 20 * 1024 * 1024


def _ensure_preview_dir():
    """Crée le dossier d'upload des previews s'il n'existe pas."""
    os.makedirs(PREVIEW_DIR, exist_ok=True)


def _is_allowed_filename(filename):
    """Vérifie que le fichier a une extension autorisée (.png).

    Args:
        filename (str): Nom du fichier original.

    Returns:
        bool: ``True`` si l'extension est autorisée.
    """
    if not filename:
        return False
    _, ext = os.path.splitext(filename)
    return ext.lower() in ALLOWED_EXTENSIONS


def _generate_unique_filename(user_id):
    """Génère un nom de fichier unique pour une image de preview.

    Format : ``{user_id}_{timestamp}_{uuid}.png``

    Args:
        user_id (str): ID de l'utilisateur (pour préfixer le nom).

    Returns:
        str: Nom de fichier unique.
    """
    timestamp = int(time.time())
    unique = uuid.uuid4().hex[:12]
    # Sanitize user_id (peut contenir des caractères non-filesystem-safe)
    safe_uid = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(user_id))
    return f"{safe_uid}_{timestamp}_{unique}.png"


# ── Routes ────────────────────────────────────────────────────────────

@app.route('/api/preview', methods=['POST'])
def preview_upload():
    """Upload une image PNG de preview.

    Content-Type: multipart/form-data
    Body: ``file`` (image PNG)

    Auth: API key (Bearer token) ou session — identifie l'utilisateur.
    Stocke l'image sur le filesystem et enregistre les métadonnées en BDD.

    Response:
        ``{ "id": int, "filename": "string", "created_at": "iso8601" }``
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    # Vérifier la présence d'un fichier dans la requête
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni (champ "file" requis).'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'error': 'Fichier vide ou nom manquant.'}), 400

    original_filename = file.filename
    if not _is_allowed_filename(original_filename):
        return jsonify({'error': 'Format de fichier non supporté. Utilisez une image PNG.'}), 400

    # Vérifier la taille du fichier (Flask lit le contenu en mémoire via
    # request.files, donc on contrôle la taille du stream avant de sauvegarder)
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_PREVIEW_SIZE:
        return jsonify({'error': f'Fichier trop volumineux (max {MAX_PREVIEW_SIZE // (1024 * 1024)} MB).'}), 413
    if size == 0:
        return jsonify({'error': 'Fichier vide.'}), 400

    # Générer un nom unique et sauvegarder
    _ensure_preview_dir()
    stored_filename = _generate_unique_filename(user_id)
    filepath = os.path.join(PREVIEW_DIR, stored_filename)

    try:
        file.save(filepath)
    except Exception as e:
        logging.exception("[preview] Échec de sauvegarde du fichier")
        return jsonify({'error': f'Erreur lors de la sauvegarde : {e}'}), 500

    # Enregistrer les métadonnées en BDD
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO preview_images (user_id, filename) VALUES (?, ?)",
            (user_id, stored_filename),
        )
        image_id = cur.lastrowid
        conn.commit()

        # Récupérer le created_at généré par la BDD
        row = conn.execute(
            "SELECT created_at FROM preview_images WHERE id = ?",
            (image_id,),
        ).fetchone()
        created_at = row['created_at'] if row else None
        # Normaliser en ISO 8601
        if created_at:
            created_at_iso = created_at if 'T' in str(created_at) else str(created_at).replace(' ', 'T')
        else:
            created_at_iso = datetime.utcnow().isoformat()
    except Exception as e:
        logging.exception("[preview] Échec d'enregistrement en BDD")
        # Nettoyer le fichier si l'insertion BDD a échoué
        try:
            os.remove(filepath)
        except OSError:
            pass
        return jsonify({'error': f'Erreur base de données : {e}'}), 500
    finally:
        conn.close()

    logging.info(f"[preview] Upload user={user_id} image_id={image_id} filename={stored_filename}")

    return jsonify({
        'id': image_id,
        'filename': stored_filename,
        'created_at': created_at_iso,
    }), 201


@app.route('/api/preview/recent', methods=['GET'])
def preview_recent():
    """Liste les 10 dernières images de preview de l'utilisateur courant.

    Auth: API key (Bearer token) ou session.
    Tri: plus récent en premier, max 10.

    Response:
        ``{ "images": [{ "id": int, "url": "/api/preview/image/{id}", "filename": "string", "created_at": "iso8601" }] }``
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, filename, created_at FROM preview_images "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    images = []
    for row in rows:
        created_at = row['created_at']
        if created_at:
            created_at_iso = created_at if 'T' in str(created_at) else str(created_at).replace(' ', 'T')
        else:
            created_at_iso = None
        images.append({
            'id': row['id'],
            'url': f"/api/preview/image/{row['id']}",
            'filename': row['filename'],
            'created_at': created_at_iso,
        })

    return jsonify({'images': images})


@app.route('/api/preview/image/<int:image_id>', methods=['GET'])
def preview_get_image(image_id):
    """Sert une image de preview par son ID.

    Auth: API key (Bearer token) ou session — vérifie que l'image appartient
    bien à l'utilisateur courant.

    Response: fichier image (image/png), ou 404 si introuvable / 403 si
    l'image n'appartient pas à l'utilisateur.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, user_id, filename FROM preview_images WHERE id = ?",
            (image_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({'error': 'Image introuvable.'}), 404

    # Vérifier que l'image appartient à l'utilisateur
    if row['user_id'] != user_id:
        return jsonify({'error': 'Accès refusé : cette image ne vous appartient pas.'}), 403

    filepath = os.path.join(PREVIEW_DIR, row['filename'])
    if not os.path.exists(filepath):
        logging.warning(f"[preview] Fichier manquant sur le disque: {filepath} (image_id={image_id})")
        return jsonify({'error': 'Fichier image introuvable sur le serveur.'}), 404

    return send_file(filepath, mimetype='image/png')