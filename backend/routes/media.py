"""Routes Media — sauvegarde serveur des médias produits par les nodes AIH.

Permet au node « AIH save media » (pack ComfyUI-AI-Helper) d'envoyer les
médias (image / vidéo / audio) vers le serveur AI-Helper au lieu de les écrire
localement, en upload CHUNKÉ (robuste derrière un reverse-proxy type Caddy).

Flow (calqué sur ``routes/files.py``) :
  1. POST /api/media/init          → crée l'upload (upload_id, chunk_size)
  2. POST /api/media/chunk         → append un chunk au fichier temporaire
  3. POST /api/media/complete      → range le fichier sous media/<user>/… + métadonnées
  4. GET  /api/media/<id>/download → sert le média (propriétaire ou admin)
  5. GET  /api/media/<id>/thumbnail → vignette cachée (cache storage + navigateur)
  6. GET  /api/media/<id>/metadata → dimensions/durée/codec + prompt/workflow
  7. GET  /api/media               → liste paginée + filtres/tri (galerie)
  8. DELETE /api/media/<id>        → corbeille (soft delete, restaurable)
  9. POST /api/media/delete        → corbeille groupée (multi-select UI)
 10. POST /api/media/<id>/restore  → restaure un média corbeillé (variante groupée /restore)
 11. DELETE /api/media/<id>/purge  → suppression DÉFINITIVE (fichier + ligne)

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
import hashlib
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import timezone

from context import *
from storage import get_storage

CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB par chunk (cohérent avec files.py)
DEFAULT_MAX_MEDIA_SIZE = 50 * 1024 * 1024 * 1024  # 50 GB max par défaut
TEMP_DIR = os.path.join(tempfile.gettempdir(), "aih_media_uploads")
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Vignettes (galerie) ───────────────────────────────────────────────
# Tailles autorisées : la valeur demandée est « snappée » à la plus proche.
THUMB_SIZES = (128, 256, 512)
THUMB_SIZE_DEFAULT = 256
# L'URL d'une vignette est IMMUABLE par (média, taille) : cache long + immutable.
# `private` : pas de cache partagé (réponses liées à la session/cookie).
THUMB_CACHE_CONTROL = "private, max-age=31536000, immutable"
THUMB_MAX_AGE = 31536000
# Bornes temporelles des sous-processus ffmpeg/ffprobe (sécurité DoS).
FFPROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 60

# Types de médias acceptés et extensions autorisées par type.
MEDIA_KINDS = ("image", "video", "audio")

# Statuts d'une ligne ``media_files`` à laquelle un fichier rangé est associé et
# donc servable : ``complete`` (vivant) ou ``trashed`` (corbeille, soft delete).
# Un média corbeillé reste servi à son propriétaire (vignette/aperçu de la
# corbeille) mais est EXCLU de la liste par défaut (voir ``_build_list_filters``).
_MEDIA_STATUSES = ("complete", "trashed")
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
    """Sérialise une ligne ``media_files`` pour l'API (contrat figé).

    Superset rétro-compatible : les clés historiques sont conservées, on
    ajoute ``thumb`` (URL GET de la vignette, immuable → cache navigateur) et
    ``thumb_available`` (la vignette est-elle produisible dans cet
    environnement : image=Pillow|ffmpeg, vidéo=ffmpeg, audio=non).
    """
    media_id = row["id"]
    filename = f"{row['filename']}{row['ext']}" if row["filename"] else ""
    return {
        "id": media_id,
        "path": row["final_path"] or "",
        "filename": filename,
        "subfolder": row["subfolder"] or "",
        "size": row["size"] or 0,
        "url": f"/api/media/{media_id}/download",
        "thumb": f"/api/media/{media_id}/thumbnail",
        "thumb_available": _kind_can_have_thumbnail(row["kind"]),
        "created_at": row["created_at"] or "",
        "has_prompt": bool(row["has_prompt"]),
        "has_workflow": bool(row["has_workflow"]),
        "kind": row["kind"],
        # État corbeille (soft delete) : l'UI peut identifier un média corbeillé.
        "status": row["status"],
        "trashed": row["status"] == "trashed",
        "trashed_at": row["trashed_at"] or "",
    }


# ── Outils d'extraction technique (Pillow / ffmpeg / ffprobe) ─────────

def _pillow_available():
    """Pillow est-il importable ? (dégradation propre si absent)."""
    try:
        import PIL.Image  # noqa: F401
        return True
    except Exception:
        return False


def _ffmpeg_path():
    """Chemin de l'exécutable ffmpeg (env ``AIH_FFMPEG`` ou PATH), ou None."""
    return os.environ.get("AIH_FFMPEG") or shutil.which("ffmpeg")


def _ffprobe_path():
    """Chemin de l'exécutable ffprobe (env ``AIH_FFPROBE`` ou PATH), ou None."""
    return os.environ.get("AIH_FFPROBE") or shutil.which("ffprobe")


def _kind_can_have_thumbnail(kind):
    """La vignette est-elle produisible ici pour ce type de média ?

    - image : Pillow OU ffmpeg (repli).
    - vidéo : ffmpeg (extraction d'une frame).
    - audio : jamais (choix documenté : pas de vignette, la route renvoie 404).
    """
    if kind == "image":
        return _pillow_available() or _ffmpeg_path() is not None
    if kind == "video":
        return _ffmpeg_path() is not None
    return False


def _probe_with_ffprobe(path):
    """Sonde un média via ffprobe.

    Retourne ``{width, height, duration_ms, codec}`` (valeurs manquantes à
    ``None``), ou ``None`` si ffprobe est absent ou échoue.
    """
    exe = _ffprobe_path()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=FFPROBE_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        data = json.loads((proc.stdout or b"").decode("utf-8", "replace") or "{}")
    except Exception as e:
        logging.warning(f"[media] ffprobe failed for {path}: {e}")
        return None

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    result = {"width": None, "height": None, "duration_ms": None, "codec": None}
    if video:
        result["width"] = video.get("width")
        result["height"] = video.get("height")
        result["codec"] = video.get("codec_name")
    elif audio:
        result["codec"] = audio.get("codec_name")

    duration = (data.get("format") or {}).get("duration")
    if duration is None and video:
        duration = video.get("duration")
    if duration is None and audio:
        duration = audio.get("duration")
    try:
        if duration is not None:
            result["duration_ms"] = int(round(float(duration) * 1000))
    except (TypeError, ValueError):
        pass
    return result


def _image_dimensions(path):
    """Dimensions d'une image via Pillow, ou ``None`` si Pillow absent/échec."""
    if not _pillow_available():
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            return {
                "width": im.width,
                "height": im.height,
                "duration_ms": None,
                "codec": (im.format or "").lower() or None,
            }
    except Exception as e:
        logging.warning(f"[media] PIL dimensions failed for {path}: {e}")
        return None


def _extract_technical_metadata(kind, path):
    """Extrait {width, height, duration_ms, codec} d'un fichier local.

    Dégradation propre : retourne ``None`` si aucun outil n'est disponible ou
    si l'extraction échoue (jamais d'exception qui remonte).
    """
    if kind == "image":
        meta = _image_dimensions(path)
        if meta:
            return meta
        # Repli ffprobe si Pillow absent (ou image illisible par Pillow).
        return _probe_with_ffprobe(path)
    if kind in ("video", "audio"):
        return _probe_with_ffprobe(path)
    return None


# ── Vignettes : clé de cache + génération ────────────────────────────

def _normalize_thumb_size(raw):
    """Borne ``?size=`` à la taille autorisée la plus proche.

    ``None``/vide → défaut (256). Non numérique → ``None`` (→ 400). Toute
    valeur numérique est snappée puis bornée à ``THUMB_SIZES`` (128/256/512).
    """
    if raw is None or raw == "":
        return THUMB_SIZE_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return min(THUMB_SIZES, key=lambda s: (abs(s - n), s))


def _thumbnail_cache_path(row, size):
    """Chemin storage de la vignette (clé canonique stable).

    ``<prefix>/.thumbs/<user>/<sha1(id:final_path)>_<size>.jpg`` — l'id et le
    ``final_path`` garantissent l'unicité et l'invalidation naturelle si le
    média change de chemin ; la taille fait partie de la clé (une variante par
    couple média/taille).
    """
    key = hashlib.sha1(f"{row['id']}:{row['final_path'] or ''}".encode()).hexdigest()[:24]
    rel = f"{_media_prefix()}/.thumbs/{_sanitize_user_id(row['user_id'])}/{key}_{size}.jpg"
    _assert_safe_relpath(rel)
    return rel


def _thumbnail_etag(row, size):
    """ETag stable (l'URL est immuable), SANS quotes : version pour invalidation."""
    return hashlib.sha1(
        f"thumb:v1:{row['id']}:{size}:{row['final_path'] or ''}".encode()
    ).hexdigest()


def _ffmpeg_thumbnail(src_path, out_path, size):
    """Génère une vignette JPEG via ffmpeg (frame/scaled). ``False`` si absent."""
    exe = _ffmpeg_path()
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-ss", "0", "-i", src_path,
             "-frames:v", "1",
             "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
             "-q:v", "3", out_path],
            capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        return proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logging.warning(f"[media] ffmpeg thumbnail failed for {src_path}: {e}")
        return False


def _generate_thumbnail(kind, src_path, out_path, size):
    """Génère une vignette JPEG à ``out_path`` depuis ``src_path``.

    Images : Pillow d'abord (EXIF respecté, resize dans la boîte ``size``),
    repli ffmpeg. Vidéos : ffmpeg. Audio : refusé en amont. Retourne ``bool``.
    """
    if kind == "image" and _pillow_available():
        try:
            from PIL import Image, ImageOps
            with Image.open(src_path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((size, size))
                if im.mode != "RGB":
                    im = im.convert("RGB")
                im.save(out_path, "JPEG", quality=82, optimize=True)
            return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
        except Exception as e:
            logging.warning(f"[media] PIL thumbnail failed for {src_path}: {e}")
    # Repli universel (image si Pillow absent, vidéo) via ffmpeg.
    return _ffmpeg_thumbnail(src_path, out_path, size)


def _ensure_thumbnail_file(row, size, thumb_path):
    """Retourne un fichier local de vignette (cache storage ou généré).

    Ordre : cache storage → génération depuis le média source → persistance du
    cache (best-effort). Retourne ``None`` si aucune vignette n'est possible
    (outils absents, source illisible, audio…).
    """
    storage = get_storage()
    local_out = os.path.join(TEMP_DIR, f"thumb_{row['id']}_{size}.jpg")

    # 1) Servir depuis le cache storage si présent.
    if storage.exists(thumb_path) and storage.download(thumb_path, local_out):
        return local_out

    # 2) Aucune vignette pour l'audio, ou aucun outil disponible.
    if not _kind_can_have_thumbnail(row["kind"]):
        return None

    # 3) Générer depuis le média source.
    src_tmp = os.path.join(TEMP_DIR, f"tsrc_{row['id']}{row['ext'] or ''}")
    if not storage.download(row["final_path"], src_tmp):
        return None
    try:
        if not _generate_thumbnail(row["kind"], src_tmp, local_out, size):
            return None
    finally:
        with contextlib.suppress(Exception):
            os.remove(src_tmp)

    # 4) Persister le cache (copie : LocalStorage.upload DÉPLACE le source).
    cache_tmp = local_out + ".up"
    try:
        shutil.copy(local_out, cache_tmp)
        if not storage.upload(cache_tmp, thumb_path):
            logging.warning(f"[media] échec de persistance vignette {thumb_path}")
    except Exception as e:
        logging.warning(f"[media] échec de persistance vignette {thumb_path}: {e}")
    finally:
        with contextlib.suppress(Exception):
            os.remove(cache_tmp)
    return local_out


def _persist_technical_metadata(media_id, tech):
    """Persiste les métadonnées techniques + ``meta_checked=1`` (idempotent)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE media_files SET width = ?, height = ?, duration_ms = ?, "
            "codec = ?, meta_checked = 1 WHERE id = ?",
            (tech.get("width"), tech.get("height"), tech.get("duration_ms"),
             tech.get("codec"), media_id),
        )
        conn.commit()
    finally:
        conn.close()


def _backfill_technical_metadata(row):
    """Backfill paresseux : télécharge le média, extrait, persiste.

    Retourne ``True`` si l'extraction a été tentée et persistée, ``False`` si
    le fichier est inaccessible (on NE marque PAS ``meta_checked`` pour
    réessayer plus tard). Compat arrière : les médias uploadés avant l'ajout
    des colonnes restent servis.
    """
    storage = get_storage()
    local_tmp = os.path.join(TEMP_DIR, f"meta_{row['id']}{row['ext'] or ''}")
    if not storage.download(row["final_path"], local_tmp):
        return False
    try:
        tech = _extract_technical_metadata(row["kind"], local_tmp) or {}
        _persist_technical_metadata(row["id"], tech)
        return True
    finally:
        with contextlib.suppress(Exception):
            os.remove(local_tmp)


def _metadata_json(row):
    """Sérialise les métadonnées détaillées d'un média (route /metadata)."""
    width = row["width"]
    height = row["height"]
    duration_ms = row["duration_ms"]
    ratio = round(width / height, 4) if (width and height) else None
    return {
        "id": row["id"],
        "filename": f"{row['filename']}{row['ext']}" if row["filename"] else "",
        "subfolder": row["subfolder"] or "",
        "kind": row["kind"],
        "ext": row["ext"] or "",
        "size": row["size"] or 0,
        "created_at": row["created_at"] or "",
        "width": width,
        "height": height,
        "ratio": ratio,
        "duration": round(duration_ms / 1000.0, 3) if duration_ms is not None else None,
        "duration_ms": duration_ms,
        "codec": row["codec"],
        "prompt": row["prompt_text"] or "",
        "workflow": row["workflow_json"] or "",
        "has_prompt": bool(row["has_prompt"]),
        "has_workflow": bool(row["has_workflow"]),
    }


# ── Corbeille (soft delete) : helpers ────────────────────────────────

# Borne de sécurité des opérations groupées (multi-select UI) : refuse une
# requête déraisonnable (DoS par payload) plutôt que de la traiter.
MAX_BULK_IDS = 500


def _now_iso():
    """Horodatage ISO-8601 en UTC (colonne ``trashed_at``)."""
    return datetime.now(timezone.utc).isoformat()


def _fetch_media(media_id):
    """Récupère une ligne ``media_files`` par id (ou ``None``)."""
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM media_files WHERE id = ?", (media_id,)
        ).fetchone()
    finally:
        conn.close()


def _media_owner_guard(row, user_id):
    """Autorise l'accès à un média rangé (propriétaire ou admin).

    Retourne ``None`` si l'accès est autorisé, sinon un couple
    ``(response, status)`` prêt à être retourné par une route. Un média
    inexistant ou non rangé (upload en cours, erreur) est un 404 ; un média
    appartenant à autrui (hors admin) est un 403 — même règle que
    ``/download`` et ``/thumbnail``.
    """
    if not row or row["status"] not in _MEDIA_STATUSES or not row["final_path"]:
        return jsonify({"error": "Média introuvable"}), 404
    if row["user_id"] != user_id and not is_admin(user_id):
        return jsonify({"error": "Accès refusé"}), 403
    return None


def _set_trashed(media_id, trashed):
    """Applique ou retire l'état corbeille (idempotent).

    Passage en corbeille : ``status='trashed'`` et ``trashed_at`` horodaté une
    seule fois (``COALESCE`` → un re-delete conserve l'horodatage d'origine).
    Restauration : ``status='complete'`` et ``trashed_at=NULL``. Retourne la
    ligne à jour.
    """
    conn = get_db()
    try:
        if trashed:
            conn.execute(
                "UPDATE media_files SET status = 'trashed', "
                "trashed_at = COALESCE(trashed_at, ?) WHERE id = ?",
                (_now_iso(), media_id),
            )
        else:
            conn.execute(
                "UPDATE media_files SET status = 'complete', trashed_at = NULL "
                "WHERE id = ?",
                (media_id,),
            )
        conn.commit()
        return conn.execute(
            "SELECT * FROM media_files WHERE id = ?", (media_id,)
        ).fetchone()
    finally:
        conn.close()


def _parse_bulk_ids(data):
    """Valide ``{ids: [...]}`` → liste d'entiers uniques (ou ``None`` si invalide).

    Les entrées non entières sont ignorées ; le dédoublonnage préserve l'ordre.
    Une valeur trop volumineuse (> ``MAX_BULK_IDS``) est refusée.
    """
    ids = data.get("ids")
    if not isinstance(ids, list):
        return None
    out = []
    seen = set()
    for raw in ids:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    if len(out) > MAX_BULK_IDS:
        return None
    return out


def _purge_media_row(row):
    """Suppression DÉFINITIVE : fichier + vignettes en cache (toutes tailles) + ligne.

    Le fichier et ses vignettes sont retirés via ``get_storage()`` (Local ET
    SFTP). Les clés de vignettes sont recalculées avec la clé canonique de
    ``/thumbnail`` (une variante par taille de ``THUMB_SIZES``) : aucune donnée
    n'est laissée orpheline. Best-effort : un fichier déjà absent ne fait pas
    échouer l'opération (on supprime quand même la ligne).
    """
    storage = get_storage()
    removed_file = False
    if row["final_path"]:
        with contextlib.suppress(Exception):
            removed_file = bool(storage.delete(row["final_path"]))
    for size in THUMB_SIZES:
        with contextlib.suppress(Exception):
            storage.delete(_thumbnail_cache_path(row, size))
    conn = get_db()
    try:
        conn.execute("DELETE FROM media_files WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    return removed_file


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

        # Métadonnées techniques : extraites AVANT l'upload, tant que le fichier
        # temporaire local est encore disponible (Pillow pour les images,
        # ffprobe pour vidéo/audio si présent). Dégradation propre : null si
        # l'extraction échoue ou si l'outil est absent. ``meta_checked=1``
        # évite toute relecture ultérieure du fichier.
        tech = _extract_technical_metadata(row['kind'], temp_path) or {}

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
            "UPDATE media_files SET status = 'complete', final_path = ?, filename = ?, size = ?, "
            "width = ?, height = ?, duration_ms = ?, codec = ?, meta_checked = 1 "
            "WHERE upload_id = ?",
            (remote_path, final_base, actual_size,
             tech.get('width'), tech.get('height'), tech.get('duration_ms'),
             tech.get('codec'), upload_id),
        )
        conn.commit()

        full_row = conn.execute(
            "SELECT * FROM media_files WHERE upload_id = ?", (upload_id,)
        ).fetchone()

        logging.info(f"[media] Upload {upload_id} complete → {remote_path}")
        return jsonify(_media_json(full_row))
    finally:
        conn.close()


# Tri autorisé → clause ORDER BY (whitelist : jamais de SQL client).
_SORT_SQL = {
    "created_at_desc": "id DESC",
    "created_at_asc": "id ASC",
    "name_asc": "filename COLLATE NOCASE ASC, id DESC",
    "name_desc": "filename COLLATE NOCASE DESC, id DESC",
    "size_desc": "size DESC, id DESC",
    "size_asc": "size ASC, id DESC",
}


def _escape_like(value):
    """Échappe ``%`` / ``_`` / ``\\`` pour un ``LIKE … ESCAPE '\\'``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_dt(value, end):
    """Normalise ``from``/``to`` en horodatage comparable (``YYYY-MM-DD HH:MM:SS``).

    Une date seule (10 car.) est étendue à 00:00:00 (from) ou 23:59:59 (to).
    """
    val = str(value).strip().replace("T", " ")
    if len(val) == 10:
        return val + (" 23:59:59" if end else " 00:00:00")
    return val


def _build_list_filters(user_id, args):
    """Construit (where_sql, params) pour la liste, d'après les filtres optionnels.

    Tous les filtres sont OPTIONNELS : absents → comportement historique
    (médias ``complete`` de l'utilisateur, SANS les corbeillés). Retourne
    ``None`` si un filtre est invalide (type/sous-dossier/statut).
    """
    where = ["user_id = ?"]
    params = [user_id]

    # Statut : par défaut (absent ou ``complete``) les médias VIVANTS → les
    # corbeillés sont EXCLUS (non-régression du comportement historique).
    # ``status=trashed`` expose la corbeille ; ``status=all`` lève le filtre.
    status = (args.get("status") or "").strip().lower()
    if status == "all":
        pass
    elif status == "trashed":
        where.append("status = 'trashed'")
    elif status in ("", "complete"):
        where.append("status = 'complete'")
    else:
        return None

    kind = (args.get("kind") or "").strip().lower()
    if kind:
        if kind not in MEDIA_KINDS:
            return None
        where.append("kind = ?")
        params.append(kind)

    subfolder = (args.get("subfolder") or "").strip()
    if subfolder:
        safe = _sanitize_subfolder(subfolder)
        if not safe:
            return None
        # Préfixe au niveau du segment : le dossier exact OU ses descendants.
        where.append("(subfolder = ? OR subfolder LIKE ? ESCAPE '\\')")
        params.append(safe)
        params.append(_escape_like(safe) + "/%")

    q = (args.get("q") or "").strip()
    if q:
        where.append("filename LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(q) + "%")

    frm = (args.get("from") or "").strip()
    if frm:
        where.append("created_at >= ?")
        params.append(_normalize_dt(frm, end=False))

    to = (args.get("to") or "").strip()
    if to:
        where.append("created_at <= ?")
        params.append(_normalize_dt(to, end=True))

    return " AND ".join(where), params


@app.route('/api/media', methods=['GET'])
def media_list():
    """Liste paginée des médias complets de l'utilisateur courant (galerie).

    Paramètres optionnels (galerie) — tous rétro-compatibles :
      - ``page`` (déf. 1), ``limit`` (déf. 50, max 200) ;
      - ``kind`` ∈ image|video|audio ;
      - ``subfolder`` : dossier exact OU préfixe de segment (``a/b``) ;
      - ``q`` : recherche sur ``filename`` (LIKE, insensible aux jokers) ;
      - ``from`` / ``to`` : plage ``created_at`` (date ou date-heure) ;
      - ``status`` : ``complete`` (déf.) | ``trashed`` (corbeille) | ``all`` ;
      - ``sort`` : created_at_desc | created_at_asc | name_asc | name_desc |
        size_desc | size_asc (déf. created_at_desc ≡ comportement historique).

    Sans aucun paramètre, la réponse est IDENTIQUE à l'historique
    (mêmes clés, même ordre ``id DESC``), avec en plus ``thumb`` /
    ``thumb_available`` et les champs corbeille ``status``/``trashed`` /
    ``trashed_at``. Les médias corbeillés sont EXCLUS par défaut.
    """
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

    sort = (request.args.get('sort') or 'created_at_desc').strip()
    order = _SORT_SQL.get(sort)
    if order is None:
        return jsonify({'error': f'sort invalide: {sort}'}), 400

    built = _build_list_filters(user_id, request.args)
    if built is None:
        return jsonify({'error': 'filtre invalide (kind/subfolder/status)'}), 400
    where_sql, params = built

    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM media_files WHERE {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM media_files WHERE {where_sql} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, (page - 1) * limit],
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
    """Sert un média depuis le storage (propriétaire ou admin).

    Un média CORBEILLÉ (``status='trashed'``) reste téléchargeable par son
    propriétaire (et par un admin) : la corbeille doit pouvoir prévisualiser /
    re-télécharger avant restauration ou purge. Il est en revanche exclu de la
    liste par défaut (voir ``_build_list_filters``).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

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


@app.route('/api/media/<int:media_id>/thumbnail', methods=['GET'])
def media_thumbnail(media_id):
    """Sert la vignette d'un média (propriétaire ou admin).

    GET pur (COOKIE de session accepté, aucun header requis) → utilisable
    directement dans un ``<img src>``.

    Cache navigateur : l'URL est IMMUABLE par (média, taille), donc
    ``Cache-Control: private, max-age=31536000, immutable`` + un ``ETag``
    stable. La revalidation (``If-None-Match``) renvoie 304 SANS toucher au
    storage.

    Cache serveur : la vignette est générée UNE fois puis persistée dans le
    storage sous ``media/.thumbs/<user>/<sha1>_<size>.jpg`` ; les appels
    suivants la servent depuis ce cache.

    ``?size=`` borné à {128, 256, 512} (défaut 256) — toute valeur numérique
    est snappée à la plus proche ; non numérique → 400. Le cache est indexé par
    couple (média, taille), donc « la sélection de la taille des miniatures »
    côté UI ne régénère rien pour les tailles déjà demandées.

    Dégradation : si aucun outil (Pillow/ffmpeg) n'est disponible, ou pour un
    média audio, la route renvoie 404 avec ``code: "thumbnail_unavailable"``
    (l'UI affiche un placeholder) — jamais de crash.

    Corbeille : la vignette d'un média corbeillé reste servie au propriétaire
    (et à un admin), afin que la corbeille puisse afficher une miniature ; les
    médias corbeillés sont toutefois exclus de la liste par défaut.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    size = _normalize_thumb_size(request.args.get('size'))
    if size is None:
        return jsonify({'error': 'size invalide (128/256/512)'}), 400

    etag = _thumbnail_etag(row, size)
    etag_header = f'"{etag}"'

    # Revalidation navigateur : 304 sans aucun accès au storage.
    if request.headers.get('If-None-Match', '') in (etag_header, '*'):
        resp = Response(status=304)
        resp.headers['ETag'] = etag_header
        resp.headers['Cache-Control'] = THUMB_CACHE_CONTROL
        return resp

    thumb_path = _thumbnail_cache_path(row, size)
    local_thumb = _ensure_thumbnail_file(row, size, thumb_path)
    if not local_thumb:
        return jsonify({
            'error': 'Vignette indisponible pour ce média',
            'code': 'thumbnail_unavailable',
            'kind': row['kind'],
        }), 404

    def _cleanup_thumb():
        with contextlib.suppress(Exception):
            os.remove(local_thumb)

    try:
        response = send_file(
            local_thumb,
            mimetype='image/jpeg',
            conditional=True,
            etag=etag,
            max_age=THUMB_MAX_AGE,
        )
    except Exception:
        _cleanup_thumb()
        raise

    response.headers['Cache-Control'] = THUMB_CACHE_CONTROL
    response.call_on_close(_cleanup_thumb)
    return response


@app.route('/api/media/<int:media_id>/metadata', methods=['GET'])
def media_metadata(media_id):
    """Métadonnées détaillées d'un média (propriétaire ou admin).

    Retourne ``id, filename, subfolder, kind, ext, size, created_at, width,
    height, ratio, duration, duration_ms, codec, prompt, workflow, has_prompt,
    has_workflow``. ``width/height/ratio`` viennent de Pillow (images),
    ``duration/duration_ms/codec`` (+ width/height) de ffprobe (vidéo/audio) si
    présent, sinon ``null`` (dégradation propre).

    Backfill paresseux : pour un média ancien (colonnes NULL,
    ``meta_checked=0``), on relit le fichier UNE fois, on persiste le résultat,
    puis on ne recalcule plus (compat arrière : l'image uploadée par
    l'utilisateur avant cette migration est servie).

    Corbeille : les métadonnées d'un média corbeillé restent accessibles au
    propriétaire (et à un admin) — le fichier existe toujours (soft delete) ;
    l'exclusion de la liste par défaut est le seul effet de la corbeille.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    # Backfill unique pour les lignes créées avant l'ajout des colonnes.
    if not row['meta_checked'] and _backfill_technical_metadata(row):
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM media_files WHERE id = ?", (media_id,)
            ).fetchone()
        finally:
            conn.close()

    return jsonify(_metadata_json(row))


# ── Corbeille (soft delete) : routes ─────────────────────────────────

@app.route('/api/media/<int:media_id>', methods=['DELETE'])
def media_delete(media_id):
    """Corbeille d'un média — soft delete (propriétaire ou admin).

    Marque le média ``status='trashed'`` + ``trashed_at`` ISO-8601. Le fichier
    n'est PAS supprimé du storage (restaurable via ``/restore``). 404 si le
    média n'existe pas (ou n'est pas rangé), 403 s'il appartient à autrui.
    Idempotent : un 2e appel conserve l'horodatage d'origine.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    updated = _set_trashed(media_id, True)
    logging.info(f"[media] Corbeille {media_id} (user={user_id})")
    return jsonify(_media_json(updated))


@app.route('/api/media/delete', methods=['POST'])
def media_delete_bulk():
    """Corbeille GROUPÉE (multi-select UI) : ``{ids: [...]}`` → soft delete.

    Traite chaque id autorisé (propriétaire ou admin, média rangé existant) et
    IGNORE les autres SANS échouer l'appel. Retourne un récap
    ``{trashed: <n>, skipped: [<ids>]}`` (les ``skipped`` regroupent les ids
    inexistants ou non autorisés).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {MAX_BULK_IDS})"}), 400

    admin = is_admin(user_id)
    trashed = []
    skipped = []
    conn = get_db()
    try:
        now = _now_iso()
        for mid in ids:
            row = conn.execute(
                "SELECT * FROM media_files WHERE id = ?", (mid,)
            ).fetchone()
            if (not row or row['status'] not in _MEDIA_STATUSES
                    or not row['final_path']
                    or (row['user_id'] != user_id and not admin)):
                skipped.append(mid)
                continue
            conn.execute(
                "UPDATE media_files SET status = 'trashed', "
                "trashed_at = COALESCE(trashed_at, ?) WHERE id = ?",
                (now, mid),
            )
            trashed.append(mid)
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[media] Corbeille groupée : {len(trashed)} ok, {len(skipped)} ignorés")
    return jsonify({'trashed': len(trashed), 'skipped': skipped})


@app.route('/api/media/<int:media_id>/restore', methods=['POST'])
def media_restore(media_id):
    """Restaure un média corbeillé (propriétaire ou admin).

    ``status='complete'`` + ``trashed_at=NULL``. 404 si inexistant, 403 si
    média d'autrui (hors admin). Idempotent : un média déjà vivant est renvoyé
    tel quel.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    updated = _set_trashed(media_id, False)
    logging.info(f"[media] Restauration {media_id} (user={user_id})")
    return jsonify(_media_json(updated))


@app.route('/api/media/restore', methods=['POST'])
def media_restore_bulk():
    """Restauration GROUPÉE : ``{ids: [...]}`` → récap ``{restored, skipped}``.

    Même contrat que la corbeille groupée : ids non autorisés/inexistants
    ignorés et listés, sans faire échouer l'appel.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {MAX_BULK_IDS})"}), 400

    admin = is_admin(user_id)
    restored = []
    skipped = []
    conn = get_db()
    try:
        for mid in ids:
            row = conn.execute(
                "SELECT * FROM media_files WHERE id = ?", (mid,)
            ).fetchone()
            if (not row or row['status'] not in _MEDIA_STATUSES
                    or not row['final_path']
                    or (row['user_id'] != user_id and not admin)):
                skipped.append(mid)
                continue
            conn.execute(
                "UPDATE media_files SET status = 'complete', trashed_at = NULL "
                "WHERE id = ?",
                (mid,),
            )
            restored.append(mid)
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[media] Restauration groupée : {len(restored)} ok, {len(skipped)} ignorés")
    return jsonify({'restored': len(restored), 'skipped': skipped})


@app.route('/api/media/<int:media_id>/purge', methods=['DELETE'])
def media_purge(media_id):
    """Suppression DÉFINITIVE d'un média (propriétaire ou admin).

    Supprime le fichier via ``get_storage()`` (Local ET SFTP), ses vignettes en
    cache (clé canonique de ``/thumbnail``, toutes tailles) puis la ligne en
    base : aucune donnée orpheline. 404 si inexistant, 403 si média d'autrui.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    removed_file = _purge_media_row(row)
    logging.info(f"[media] Purge {media_id} (user={user_id}, file_removed={removed_file})")
    return jsonify({'purged': True, 'id': media_id})


@app.route('/api/media/purge', methods=['POST'])
def media_purge_bulk():
    """Purge GROUPÉE (vider la corbeille) : ``{ids: [...]}`` → récap.

    Retourne ``{purged: <n>, skipped: [<ids>]}`` ; ids non autorisés /
    inexistants ignorés sans faire échouer l'appel.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {MAX_BULK_IDS})"}), 400

    admin = is_admin(user_id)
    purged = []
    skipped = []
    for mid in ids:
        row = _fetch_media(mid)
        if (not row or row['status'] not in _MEDIA_STATUSES
                or not row['final_path']
                or (row['user_id'] != user_id and not admin)):
            skipped.append(mid)
            continue
        _purge_media_row(row)
        purged.append(mid)

    logging.info(f"[media] Purge groupée : {len(purged)} ok, {len(skipped)} ignorés")
    return jsonify({'purged': len(purged), 'skipped': skipped})
