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

import contextlib
import logging
import os
import secrets
import tempfile
import threading
import time
import unicodedata
from urllib.parse import quote as _url_quote

from context import *
from flask import Response
from storage import get_storage
from werkzeug.http import dump_options_header

CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB par chunk
MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024  # 50 GB max
TEMP_DIR = tempfile.gettempdir() + "/aih_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

# Taille des morceaux du streaming download (chemin NOMINAL). 4 Mo : chaque
# ``read`` SFTP est DÉJÀ découpé en requêtes de 32 Ko pipelineées par prefetch
# (storage._SFTPReadStream), donc un morceau plus gros ne coûte plus de
# latence ; il réduit les allers-retours Flask/werkzeug et la charge CPU par
# morceau. Le premier octet reste rapide (prefetch : ~1 RTT + débit du lien
# pour 4 Mo) alors qu'avant, 4 Mo non pipelineés = 128 RTT. Mesuré (harnais
# hors dépôt, latence simulée) : 1 Mo→4 Mo = moins d'appels de générateur,
# même débit, premier morceau backend inchangé.
STREAM_CHUNK_SIZE = 4 * 1024 * 1024

# Délai d'INACTIVITÉ du flux de download (secondes sans le moindre morceau).
# Le canal SFTP a déjà ``SFTP_TIMEOUT`` (30 s) comme timeout socket ; ce délai
# est le FILET DE DERNIER RECOURS quand la lecture ne rend rien malgré lui
# (canal/transport mort, stockage local figé) : le flux est fermé et le
# transfert échoue explicitement au lieu d'attendre indéfiniment. Doit rester
# SUPÉRIEUR à ``SFTP_TIMEOUT`` pour laisser le timeout socket agir d'abord.
# Surchargeable via ``AIH_STREAM_IDLE_TIMEOUT`` (secondes).
try:
    STREAM_IDLE_TIMEOUT = max(5.0, float(os.environ.get("AIH_STREAM_IDLE_TIMEOUT", "45")))
except (TypeError, ValueError):
    STREAM_IDLE_TIMEOUT = 45.0

# ── Nettoyage des uploads ABANDONNÉS ─────────────────────────────────
# Un upload laissé en 'uploading' (client fermé, ComfyUI tué, réseau coupé)
# conserve son fichier temporaire COMPLET (jusqu'à 50 Go) dans /tmp
# INDÉFINIMENT, et sa ligne reste 'uploading'. Aucun ordonnanceur ne purge ces
# orphelins → /tmp peut se remplir jusqu'à saturation. On nettoie de façon
# OPPORTUNISTE à chaque /files/init (auto-guérison, sans ordonnanceur).
# Seuil large (24 h par défaut) : un très gros transfert légitime (50 Go sur un
# lien lent) reste en 'uploading' des heures sans jamais être purgé en cours.
STALE_UPLOAD_HOURS = float(os.environ.get("AIH_STALE_UPLOAD_HOURS", "24"))


def _purge_stale_uploads(conn):
    """Supprime les fichiers temporaires des uploads abandonnés.

    Cible les lignes ``status='uploading'`` dont ``created_at`` dépasse
    ``STALE_UPLOAD_HOURS`` : temp supprimé (UNIQUEMENT s'il est bien sous
    ``TEMP_DIR`` — confinement) puis ligne marquée ``'error'``.

    Retourne le nombre de lignes purgées. Best-effort : ne doit JAMAIS faire
    échouer un nouvel upload (exception silencieuse).
    """
    try:
        rows = conn.execute(
            "SELECT upload_id, temp_path FROM file_uploads "
            "WHERE status = 'uploading' AND created_at IS NOT NULL "
            "AND created_at < datetime('now', ?)",
            (f"-{STALE_UPLOAD_HOURS} hours",),
        ).fetchall()
    except Exception as e:  # pragma: no cover — défensif
        logging.warning(f"[files] purge stale: select failed: {e}")
        return 0
    if not rows:
        return 0
    temp_root = os.path.realpath(TEMP_DIR)
    purged = 0
    for row in rows:
        temp_path = row['temp_path'] or ''
        if temp_path:
            real = os.path.realpath(temp_path)
            # Ne supprimer QUE sous TEMP_DIR (jamais un chemin arbitraire venu
            # de la base).
            if real == temp_root or real.startswith(temp_root + os.sep):
                with contextlib.suppress(Exception):
                    os.remove(real)
        conn.execute(
            "UPDATE file_uploads SET status = 'error' WHERE upload_id = ?",
            (row['upload_id'],),
        )
        purged += 1
    if purged:
        logging.info(f"[files] Purge de {purged} upload(s) abandonné(s) (> {STALE_UPLOAD_HOURS:.0f} h)")
    return purged


# ── Helpers de résolution d'existence (check simple + check par lot) ─────

def _latest_existing_upload(conn, storage, where, params):
    """Dernière ligne ``file_uploads`` complète dont le fichier existe VRAIMENT.

    Le tri est volontairement ``created_at DESC, rowid DESC`` : après un
    ÉCRASEMENT explicite (nouvel upload des mêmes octets), la déduplication
    doit renvoyer le fichier le PLUS RÉCENT — sinon un ancien upload identique
    pourrait être resélectionné et l'écrasement n'aurait aucun effet observable.

    Les lignes « fantômes » (status='complete' mais fichier absent du stockage)
    sont traitées comme dans ``/files/check`` : marquées ``'error'`` pour ne plus
    matcher, et on continue sur la candidate suivante (jamais un faux positif).

    Retourne ``(row|None, [upload_ids_fantomes])``.
    """
    rows = conn.execute(
        "SELECT upload_id, filename, size, final_path, created_at FROM file_uploads "
        f"WHERE {where} AND status = 'complete' "
        "ORDER BY created_at DESC, rowid DESC",
        params
    ).fetchall()
    missing = []
    for row in rows:
        if row['final_path'] and storage.exists(row['final_path']):
            return row, missing
        missing.append(row['upload_id'])
    return None, missing


def _mark_missing_as_error(conn, missing_ids):
    """Marque des uploads fantômes en ``'error'`` (même politique que /files/check)."""
    for upload_id in missing_ids:
        logging.warning(f"[files] Dedup match {upload_id} mais fichier absent — marqué error")
        conn.execute("UPDATE file_uploads SET status = 'error' WHERE upload_id = ?", (upload_id,))


@app.route('/api/files/check', methods=['POST'])
def check_file_exists():
    """Vérifie si un fichier a deja ete uploade (deduplication par fingerprint).
    Retourne {exists: true, file_path: ...} si trouve, sinon {exists: false}.
    """
    guard = _login_required()
    if guard:
        return guard
    data = request.get_json() or {}

    try:
        size = int(data.get('size', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'size invalide'}), 400
    head = (data.get('head') or '').strip()
    tail = (data.get('tail') or '').strip()

    if size <= 0 or not head or not tail:
        return jsonify({'exists': False})

    conn = get_db()
    try:
        storage = get_storage()
        row, missing = _latest_existing_upload(
            conn, storage,
            "size = ? AND fingerprint_head = ? AND fingerprint_tail = ?",
            (size, head, tail),
        )
        if row:
            return jsonify({
                'exists': True,
                'upload_id': row['upload_id'],
                'file_path': row['final_path'],
                'filename': row['filename'],
            })
        _mark_missing_as_error(conn, missing)
        conn.commit()
        return jsonify({'exists': False})
    finally:
        conn.close()


@app.route('/api/files/check-batch', methods=['POST'])
def check_files_batch():
    """Vérifie l'existence de PLUSIEURS fichiers en UNE requête (pré-upload).

    Utilisée par l'onglet 📤 Partager du pack ComfyUI-AI-Helper AVANT l'envoi :
    l'utilisateur voit quels modèles sont déjà sur le serveur et choisit ceux à
    écraser (l'upload explicite passe alors par /files/init sans passage par la
    déduplication).

    Requête :
        {"items": [{"filename": str, "size": int, "head": str, "tail": str}, ...]}
        (head/tail = sha256 hex du premier/dernier Mo — mêmes valeurs que
         /files/check ; size/head/tail absents → seule la correspondance par
         NOM peut être évaluée)

    Réponse 200 :
        {"items": [{"filename": str,
                     "status": "identical"|"different"|"absent",
                     "remote": {"upload_id", "filename", "size",
                                "file_path", "created_at"} | null}, ...]}
      - "identical" : mêmes size+head+tail trouvés ET fichier réellement
        présent sur le stockage (déduplication possible, aucun octet à envoyer) ;
      - "different" : un upload COMPLET du même nom existe mais le contenu
        diffère (taille ou empreinte) — un envoi écraserait la version actuelle ;
      - "absent"    : rien de complet ne correspond.

    Aucun identifiant interne (user_id) n'est exposé. Maximum 200 items.
    """
    guard = _login_required()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    items = data.get('items')
    if not isinstance(items, list) or not items:
        return jsonify({'error': 'items (liste non vide) requis'}), 400
    if len(items) > 200:
        return jsonify({'error': 'Maximum 200 items par requête'}), 400

    conn = get_db()
    try:
        storage = get_storage()
        results = []
        for raw in items:
            if not isinstance(raw, dict):
                return jsonify({'error': 'chaque item doit être un objet'}), 400
            # Même normalisation que /files/init : basename UNIQUEMENT (le nom
            # stocké en base est un basename — cf. init_upload).
            filename = os.path.basename(str(raw.get('filename') or '').replace('\\', '/'))
            try:
                size = int(raw.get('size') or 0)
            except (TypeError, ValueError):
                size = 0
            head = str(raw.get('head') or '').strip()
            tail = str(raw.get('tail') or '').strip()

            entry = {'filename': filename, 'status': 'absent', 'remote': None}
            if filename:
                row = None
                if size > 0 and head and tail:
                    row, missing = _latest_existing_upload(
                        conn, storage,
                        "size = ? AND fingerprint_head = ? AND fingerprint_tail = ?",
                        (size, head, tail),
                    )
                    _mark_missing_as_error(conn, missing)
                if row is None:
                    row, missing = _latest_existing_upload(
                        conn, storage, "filename = ?", (filename,)
                    )
                    _mark_missing_as_error(conn, missing)
                    if row is not None:
                        entry['status'] = 'different'
                if row is not None:
                    if entry['status'] != 'different':
                        entry['status'] = 'identical'
                    entry['remote'] = {
                        'upload_id': row['upload_id'],
                        'filename': row['filename'],
                        'size': row['size'],
                        'file_path': row['final_path'] or '',
                        'created_at': row['created_at'] or '',
                    }
            results.append(entry)
        conn.commit()
        return jsonify({'items': results})
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
    # Sécurité : ne garder que le basename pour éviter le path traversal
    filename = os.path.basename(filename.replace('\\', '/'))
    try:
        size = int(data.get('size', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'size invalide'}), 400
    file_type = (data.get('type') or '').strip()  # 'model', 'node', 'screenshot'

    if not filename or size <= 0:
        return jsonify({'error': 'filename et size requis'}), 400
    if file_type not in ('model', 'node', 'screenshot', 'checkpoint', 'lora', 'vae', 'clip', 'clip_vision', 'controlnet', 'unet', 'unet_gguf', 'upscale', 'gligen', 'hypernetwork', 'text_encoder', 'style_model'):
        return jsonify({'error': f'type "{file_type}" non reconnu'}), 400
    if size > MAX_FILE_SIZE:
        return jsonify({'error': f'Fichier trop volumineux (max {MAX_FILE_SIZE // (1024**3)} GB)'}), 413

    upload_id = secrets.token_urlsafe(16)
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    # Les octets transitent TOUJOURS par le backend Flask (quel que soit le
    # backend de stockage, local ou SFTP). On écrit dans un fichier temporaire
    # local, puis complete_upload pousse le fichier vers le storage via
    # SFTPStorage. Le client n'a jamais besoin des credentials SFTP.
    temp_path = os.path.join(TEMP_DIR, f"{upload_id}.tmp")
    with open(temp_path, 'wb'):
        pass

    conn = get_db()
    try:
        # Nettoyage OPPORTUNISTE des uploads abandonnés (temp orphelins) :
        # évite que /tmp se remplisse de gros fichiers partiels.
        _purge_stale_uploads(conn)
        conn.execute("""
            INSERT INTO file_uploads (upload_id, user_id, filename, size, type,
                                       chunk_size, total_chunks, temp_path, final_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (upload_id, user_id, filename, size, file_type,
              CHUNK_SIZE, total_chunks, temp_path, ""))
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[files] Init upload {upload_id}: {filename} ({size} bytes, {total_chunks} chunks)")

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
        chunk_stream = request.files['data'].stream

        if temp_path:
            # Mode local (fallback)
            with open(temp_path, 'ab') as f:
                while True:
                    buf = chunk_stream.read(65536)
                    if not buf:
                        break
                    f.write(buf)
        else:
            # Streaming direct SFTP (pas de fichier local)
            if row['final_path']:
                remote = row['final_path']
            else:
                remote = f"workflows/{row['type']}s/{upload_id}/{row['filename']}"
            storage = get_storage()
            success = storage.append_chunk_stream(remote, chunk_stream)
            if not success:
                return jsonify({'error': 'Echec du chunk sur le stockage distant'}), 500

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

        file_type = row['type']
        filename = row['filename']
        remote_path = row['final_path'] or f"workflows/{file_type}s/{upload_id}/{filename}"
        temp_path = row['temp_path'] or ""

        if temp_path:
            # Mode chunked : verifier que tous les chunks sont la
            if row['received_chunks'] != row['total_chunks']:
                return jsonify({
                    'error': f'Chunks manquants: {row["received_chunks"]}/{row["total_chunks"]}'
                }), 400
            # Mode fichier local : uploader vers le storage
            storage = get_storage()
            if not os.path.isfile(temp_path):
                conn.execute("UPDATE file_uploads SET status = 'error' WHERE upload_id = ?", (upload_id,))
                conn.commit()
                return jsonify({'error': 'Fichier temporaire introuvable'}), 500

            actual_size = os.path.getsize(temp_path)
            if actual_size != row['size']:
                logging.warning(f"[files] Size mismatch: expected {row['size']}, got {actual_size}")

            success = storage.upload(temp_path, remote_path)
            with contextlib.suppress(Exception):
                os.remove(temp_path)

            if not success:
                # Échec RÉEL de la recopie vers le stockage : la ligne est
                # marquée 'error' (elle ne matchera plus la déduplication) et
                # le temp est déjà nettoyé ci-dessus → l'utilisateur peut
                # RELANCER l'upload proprement, sans chunks orphelins.
                logging.error(
                    f"[files] Storage upload FAILED pour {upload_id} "
                    f"({row['filename']}, {row['size']} octets) → temp supprimé, statut 'error'"
                )
                conn.execute("UPDATE file_uploads SET status = 'error', temp_path = '' WHERE upload_id = ?", (upload_id,))
                conn.commit()
                return jsonify({'error': 'Échec de l\'upload vers le stockage'}), 500
        else:
            # Mode direct SFTP : le fichier est deja sur le storage (upload direct via paramiko)
            actual_size = row["size"]
            # Marquer tous les chunks comme recus (l'upload direct ne passe pas par /chunk)
            conn.execute("UPDATE file_uploads SET received_chunks = total_chunks WHERE upload_id = ?", (upload_id,))
            conn.commit()

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


@app.route('/api/files/<upload_id>/download-info', methods=['GET'])
def download_info(upload_id):
    """Retourne la config SFTP + chemin distant pour download direct."""
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT final_path, filename, status, type, size FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Fichier introuvable'}), 404
        if row['status'] != 'complete':
            return jsonify({'error': 'Upload pas finalisé'}), 400
        if not row['final_path']:
            return jsonify({'error': 'Chemin de stockage manquant'}), 500
        # Jamais de credentials SFTP exposés : le download passe par le backend
        # (endpoint /api/files/<id>/download) qui stream via le storage abstrait.
        return jsonify({
            'filename': row['filename'],
            'size': row['size'],
            'file_path': row['final_path'],
        })
    finally:
        conn.close()


@app.route('/api/files/<upload_id>/fingerprint', methods=['GET'])
def get_fingerprint(upload_id):
    """Retourne le fingerprint (head/tail/size) d'un fichier uploadé."""
    guard = _login_required()
    if guard:
        return guard
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT size, fingerprint_head, fingerprint_tail FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Upload introuvable'}), 404
        return jsonify({
            'size': row['size'],
            'head': row['fingerprint_head'] or '',
            'tail': row['fingerprint_tail'] or '',
        })
    finally:
        conn.close()


class StreamStalledError(RuntimeError):
    """Flux de download abandonné : aucun morceau reçu dans le délai imparti."""


def _storage_name(storage):
    """Nom lisible du stockage pour les messages (jamais d'exception)."""
    try:
        return storage.get_backend_name()
    except Exception:
        return "stockage inconnu"


class _StreamIdleWatchdog:
    """Ferme un flux qui ne rend AUCUN morceau pendant ``timeout`` secondes.

    Un thread daemon surveille ``last_activity`` (remis à zéro à chaque
    morceau) ; au dépassement il ferme le flux (ce qui débloque une lecture
    en cours) et marque ``stalled``. La boucle de streaming consulte
    ``stalled`` et échoue avec :class:`StreamStalledError` au lieu de laisser
    le client attendre des minutes sans comprendre.

    ``stop()`` est idempotent et sans attente : appelable depuis le ``finally``
    du générateur, y compris après une fermeture par le watchdog.
    """

    def __init__(self, stream, timeout):
        self._stream = stream
        self._timeout = max(0.1, float(timeout))
        # Sondage plus fin que le délai en test (délais courts → tests rapides).
        self._interval = max(0.02, min(1.0, self._timeout / 4.0))
        self._last = time.monotonic()
        self._stop = threading.Event()
        self._stalled = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run, name="aih-stream-watchdog", daemon=True
        )
        self._thread.start()
        return self

    def touch(self):
        """Signale un morceau (progression réelle du flux)."""
        self._last = time.monotonic()

    @property
    def stalled(self):
        return self._stalled.is_set()

    def _run(self):
        while not self._stop.wait(self._interval):
            if time.monotonic() - self._last >= self._timeout:
                self._stalled.set()
                # Fermer le flux débloque la lecture en cours (SFTP : le canal
                # est rendu/fermé ; local : la lecture rend la main).
                with contextlib.suppress(Exception):
                    self._stream.close()
                return

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def _attachment_disposition(filename):
    """Content-Disposition d'attachement (même encodage RFC 5987 que send_file)."""
    name = os.path.basename((filename or 'download').replace('\\', '/')) or 'download'
    try:
        name.encode('ascii')
        names = {'filename': name}
    except UnicodeEncodeError:
        simple = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        names = {'filename': simple,
                 'filename*': "UTF-8''" + _url_quote(name, safe="!#$&+-.^_`|~")}
    return dump_options_header('attachment', names)


@app.route('/api/files/<upload_id>/download', methods=['GET'])
def download_file(upload_id):
    """Download un fichier depuis le storage → streaming HTTP vers le client.

    Chemin NOMINAL (``storage.open_stream``) : les octets sont lus par
    morceaux (``STREAM_CHUNK_SIZE``) et écrits DIRECTEMENT dans la réponse.
    Conséquences mesurées : aucun fichier temporaire complet côté backend (ni
    double occupation disque, ni écriture de secours), un seul transfert
    (stockage → client), le PREMIER octet part dès la première lecture, et le
    débit est celui du maillon unique (au lieu de phase1 + phase2 séquentielles).

    Repli SÛR (stockage sans streaming, ex. canal SFTP dédié indisponible) :
    ancien chemin ``storage.download`` → temp → ``send_file``. Le temp est
    supprimé par un générateur qui enveloppe la réponse : ``call_on_close`` ne
    se déclenche JAMAIS sur une réponse ``direct_passthrough`` (send_file), ce
    qui laissait le temp COMPLET sur le disque après chaque téléchargement.
    """
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT final_path, filename, status, type, size FROM file_uploads WHERE upload_id = ?",
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

    storage = get_storage()
    filename = row['filename']
    size = row['size'] or 0
    t0 = time.monotonic()

    # ── Chemin NOMINAL : flux direct storage → réponse HTTP ──────────────
    # ``size`` (connue de la BDD) est transmise au stockage : le backend SFTP
    # en borne sa fenêtre de prefetch (jamais « jusqu'à la fin » d'un fichier
    # de taille inconnue, ce qui bufferiserait 13 Go en RAM).
    stream = None
    try:
        stream = storage.open_stream(row['final_path'], size=size)
    except Exception as e:  # pragma: no cover — défensif (open_stream ne lève pas)
        logging.warning(f"[files] open_stream a échoué pour {upload_id} : {e}")
        stream = None

    if stream is not None:
        # Watchdog armé AVANT la 1re lecture : si le stockage ne rend rien,
        # l'échec est borné et le flux est fermé.
        watchdog = _StreamIdleWatchdog(stream, STREAM_IDLE_TIMEOUT).start()

        def _stalled_reason(sent):
            return (
                f"download {upload_id} : aucun morceau reçu pendant "
                f"{STREAM_IDLE_TIMEOUT:.0f} s — {_storage_name(storage)} muet "
                f"(transfert abandonné après {sent} octets envoyés). "
                f"Vérifier le stockage/les canaux SFTP côté backend, puis réessayer."
            )

        def _stream_abort_response(reason):
            """Erreur PROPRE (JSON 500) quand RIEN n'a été envoyé au client.

            Sans cela, l'exception avant le 1er morceau laissait le serveur
            produire une page d'erreur générique — le pack affichait un
            « HTTP 500 » illisible au lieu de la cause. Le flux est fermé ici.
            """
            watchdog.stop()
            with contextlib.suppress(Exception):
                stream.close()
            logging.error(f"[files] {reason}")
            return jsonify({'error': reason}), 500

        # Premier morceau lu AVANT de répondre : le client n'attend jamais la
        # fin du fichier pour son 1er octet ET un stockage muet produit une
        # erreur JSON explicite au lieu d'une réponse tronquée silencieuse.
        try:
            first_chunk = stream.read(STREAM_CHUNK_SIZE)
        except Exception as e:
            if watchdog.stalled:
                return _stream_abort_response(_stalled_reason(0))
            return _stream_abort_response(f"Lecture du stockage impossible : {e}")
        if watchdog.stalled:
            return _stream_abort_response(_stalled_reason(0))
        watchdog.touch()

        def _iter_stream():
            sent = 0
            first_at = None

            def _stalled_error():
                return StreamStalledError(_stalled_reason(sent))

            try:
                if first_chunk:
                    first_at = time.monotonic()
                    sent += len(first_chunk)
                    yield first_chunk
                while True:
                    try:
                        chunk = stream.read(STREAM_CHUNK_SIZE)
                    except Exception:
                        # La fermeture par le watchdog peut faire échouer la
                        # lecture en cours : on retraduit en échec EXPLICITE.
                        if watchdog.stalled:
                            raise _stalled_error() from None
                        raise
                    if watchdog.stalled:
                        # Le délai d'inactivité a été dépassé : le flux a été
                        # fermé. On échoue EXPLICITEMENT (le client ne peut pas
                        # confondre avec une fin de fichier).
                        raise _stalled_error()
                    if not chunk:
                        break
                    watchdog.touch()
                    if first_at is None:
                        first_at = time.monotonic()
                    sent += len(chunk)
                    yield chunk
                logging.info(
                    f"[files] download {upload_id} STREAMÉ : {sent} octets, "
                    f"1er octet {1000 * ((first_at or t0) - t0):.0f} ms, "
                    f"total {time.monotonic() - t0:.2f} s (aucun temp)"
                )
            except StreamStalledError as e:
                logging.error(f"[files] {e}")
                raise
            finally:
                watchdog.stop()
                with contextlib.suppress(Exception):
                    stream.close()

        response = Response(_iter_stream(), mimetype='application/octet-stream')
        response.headers['Content-Disposition'] = _attachment_disposition(filename)
        if size > 0:
            response.headers['Content-Length'] = str(size)
        return response

    # ── Repli : temp complet depuis le storage puis send_file ────────────
    # Visible dans les logs : pendant TOUTE la copie, le client ne reçoit AUCUN
    # octet (c'est le symptôme « Préparation côté serveur… » qui dure). Si ce
    # WARNING apparaît pour un gros fichier, le streaming direct était
    # indisponible (canal SFTP du pool occupé/indisponible) → vérifier le pool.
    logging.warning(
        f"[files] download {upload_id} : flux direct indisponible "
        f"(open_stream → None) — repli PRÉCHARGEMENT de {size} octets depuis "
        f"{_storage_name(storage)} : le client ne recevra rien avant la "
        f"copie complète"
    )
    local_tmp = os.path.join(
        TEMP_DIR,
        f"dl_{upload_id}_{os.path.basename((filename or '').replace(chr(92), '/'))}",
    )
    if not storage.download(row['final_path'], local_tmp):
        return jsonify({'error': 'Échec du téléchargement depuis le stockage'}), 500

    phase1_s = time.monotonic() - t0

    def _cleanup_local_tmp():
        with contextlib.suppress(Exception):
            os.remove(local_tmp)

    try:
        response = send_file(
            local_tmp,
            as_attachment=True,
            download_name=filename,
            mimetype='application/octet-stream',
        )
    except Exception:
        _cleanup_local_tmp()
        raise

    # Nettoyage FIABLE du temp : send_file renvoie une réponse
    # ``direct_passthrough`` pour laquelle Flask n'appelle PAS ``call_on_close``
    # (pas de ClosingIterator) → le temp restait indéfiniment sur le disque
    # (constaté au harnais : fichier dl_* complet conservé après la réponse). Un
    # générateur qui enveloppe le corps garantit la suppression à la fin du
    # transfert OU à la déconnexion client (GeneratorExit → finally).
    original_body = response.response

    def _iter_and_cleanup():
        sent = 0
        try:
            for chunk in original_body:
                sent += len(chunk)
                yield chunk
            logging.info(
                f"[files] download {upload_id} via TEMP : {sent} octets, "
                f"phase stockage→temp {phase1_s:.2f} s, "
                f"total {time.monotonic() - t0:.2f} s"
            )
        finally:
            _cleanup_local_tmp()

    response.response = _iter_and_cleanup()
    return response


@app.route('/api/aih/models/remote', methods=['GET'])
def list_remote_models():
    """
    Liste les modèles distants disponibles (uploadés par les utilisateurs).
    Filtre : status='complete', type dans MODEL_TYPES (tous types sauf screenshots/workflows).
    Paramètres optionnels (query string) :
      page (int, defaut 1), limit (int, defaut 50, max 200)
      type (string, optionnel) — filtre par type exact
      search (string, optionnel) — recherche textuelle dans filename
      sort (string, defaut 'created_at') — created_at | filename | size | downloads
      order (string, defaut 'desc') — asc | desc

    Retour JSON :
    {
      "items": [{ upload_id, filename, display_name, type, size,
                  sha256_head, sha256_tail, uploaded_by, uploaded_by_id,
                  created_at, downloads }, ...],
      "total": int,
      "page": int,
      "limit": int
    }
    """
    guard = _login_required()
    if guard:
        return guard

    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        return jsonify({'error': 'page invalide'}), 400
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        return jsonify({'error': 'limit invalide'}), 400
    limit = min(limit, 200)
    type_filter = request.args.get('type', '').strip()
    search = request.args.get('search', '').strip()
    sort = request.args.get('sort', 'created_at')
    order = request.args.get('order', 'desc')

    # Types valides pour les modèles (exclure screenshots, workflows, etc.)
    model_types = ['model', 'checkpoint', 'lora', 'vae', 'clip', 'clip_vision',
                   'controlnet', 'unet', 'unet_gguf', 'upscale', 'gligen',
                   'hypernetwork', 'text_encoder', 'style_model', 'diffusion_model',
                   'embedding']

    conn = get_db()

    try:
        # Construire la requête
        where = "WHERE u.status = 'complete' AND u.type IN ({})".format(
            ','.join('?' for _ in model_types))
        params = list(model_types)

        if type_filter and type_filter in model_types:
            where += " AND u.type = ?"
            params.append(type_filter)

        if search:
            where += " AND (u.filename LIKE ? OR u.display_name LIKE ?)"
            params.extend([f'%{search}%', f'%{search}%'])

        # Compter le total
        total = conn.execute(
            "SELECT COUNT(*) FROM file_uploads u " + where, params
        ).fetchone()[0]

        # Trier
        allowed_sorts = {'created_at', 'filename', 'size', 'downloads'}
        if sort not in allowed_sorts:
            sort = 'created_at'
        order_sql = "DESC" if order == 'desc' else "ASC"

        offset = (page - 1) * limit
        rows = conn.execute(
            f"SELECT u.upload_id, u.filename, u.display_name, u.type, u.size, "
            f"u.fingerprint_head as sha256_head, u.fingerprint_tail as sha256_tail, "
            f"COALESCE(us.display_name, 'utilisateur inconnu') as uploaded_by, "
            f"u.user_id as uploaded_by_id, u.created_at, u.downloads "
            f"FROM file_uploads u "
            f"LEFT JOIN users us ON u.user_id = us.id "
            f"{where} ORDER BY u.{sort} {order_sql} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

        items = []
        for r in rows:
            items.append({
                'upload_id': r['upload_id'],
                'filename': r['filename'],
                'display_name': r['display_name'] or '',
                'type': r['type'],
                'size': r['size'],
                'sha256_head': r['sha256_head'] or '',
                'sha256_tail': r['sha256_tail'] or '',
                'uploaded_by': r['uploaded_by'],
                'uploaded_by_id': r['uploaded_by_id'],
                'created_at': r['created_at'] or '',
                'downloads': r['downloads'] or 0,
            })

        return jsonify({'items': items, 'total': total, 'page': page, 'limit': limit})
    finally:
        conn.close()


@app.route('/api/aih/models/remote/<upload_id>', methods=['GET'])
def get_remote_model_detail(upload_id):
    """Détail d'un modèle distant spécifique."""
    guard = _login_required()
    if guard:
        return guard
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT u.*, COALESCE(us.display_name, 'utilisateur inconnu') as uploaded_by "
            "FROM file_uploads u LEFT JOIN users us ON u.user_id = us.id "
            "WHERE u.upload_id = ? AND u.status = 'complete'",
            [upload_id]
        ).fetchone()

        if not row:
            return jsonify({'error': 'Modèle introuvable'}), 404

        return jsonify({
            'upload_id': row['upload_id'],
            'filename': row['filename'],
            'display_name': row['display_name'] or '',
            'type': row['type'],
            'size': row['size'],
            'sha256_head': row['fingerprint_head'] or '',
            'sha256_tail': row['fingerprint_tail'] or '',
            'uploaded_by': row['uploaded_by'],
            'uploaded_by_id': row['user_id'],
            'created_at': row['created_at'] or '',
            'downloads': row['downloads'] or 0,
            'status': row['status'],
        })
    finally:
        conn.close()


@app.route('/api/aih/models/remote/<upload_id>/download', methods=['POST'])
def increment_model_download(upload_id):
    """Incrémente le compteur de téléchargements d'un modèle."""
    guard = _login_required()
    if guard:
        return guard
    conn = get_db()
    try:
        conn.execute(
            "UPDATE file_uploads SET downloads = COALESCE(downloads, 0) + 1 "
            "WHERE upload_id = ?", [upload_id]
        )
        conn.commit()
        return jsonify({'status': 'ok'})
    finally:
        conn.close()


@app.route('/api/aih/models/remote/<upload_id>', methods=['DELETE'])
def delete_remote_model(upload_id):
    """
    Supprime un modèle distant (admin uniquement).
    - Vérifie que l'utilisateur est admin
    - Supprime le fichier du storage (si final_path existe)
    - Supprime l'enregistrement en base
    """
    guard = _admin_required()
    if guard:
        return guard

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT final_path, filename, status FROM file_uploads WHERE upload_id = ?",
            (upload_id,)
        ).fetchone()

        if not row:
            return jsonify({'error': 'Modèle introuvable'}), 404

        # Supprimer le fichier physique si présent et complet
        if row['final_path'] and row['status'] == 'complete':
            try:
                storage = get_storage()
                storage.delete(row['final_path'])
            except Exception:
                # Log l'erreur mais ne pas bloquer la suppression BDD
                pass

        # Supprimer l'enregistrement
        conn.execute("DELETE FROM file_uploads WHERE upload_id = ?", (upload_id,))
        conn.commit()

        return jsonify({'status': 'ok', 'deleted': row['filename']})
    finally:
        conn.close()


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
