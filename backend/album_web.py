"""Génération du contenu « web » d'un album public (service SANS routes).

Ce module fabrique le dossier AUTONOME d'un album public, destiné à être servi
tel quel par le process public de la phase 2 (lecture SEULE du dossier de
l'album) : aucune dépendance à la base ni au storage privé au moment du service.

Structure produite (``<webroot>/<key>/``) ::

    <key>/
      manifest.json      # titre, description, count, items[{i,w,h,ext}], updated_at
      thumb/0001.jpg     # vignettes 512 px (toujours JPG — fond non critique)
      full/0001.jpg      # version « grande » (ou 0001.png si transparence réelle)
      full/0002.png

Choix de duplication (décision utilisateur) :
  - ``thumb/<n>.jpg`` : on COPIE la vignette 512 déjà en cache local si elle
    existe, sinon on la génère depuis l'original puis on la copie ;
  - ``full/<n>.jpg``  : RE-ENCODAGE depuis l'original en JPEG **qualité 90** à
    la **taille originale** → les métadonnées/EXIF sont perdus (vie privée OK) ;
  - EXCEPTION PNG TRANSPARENT : si l'image utilise RÉELLEMENT un canal alpha
    (``_image_uses_alpha``), on la conserve en **PNG** (``full/<n>.png``), sans
    aplatir le fond. La vignette reste JPG (le cache 512 existant est déjà aplati
    en JPG — acceptable pour une vignette).

Confinement : le nom du dossier est la clé publique opaque (alphabet URL-safe,
validé par ``is_safe_album_key``) ; aucune donnée privée (id média, nom d'origine,
chemin storage, user_id) n'apparaît ni dans le dossier ni dans le manifest.

Écritures ATOMIQUES : tout fichier (manifest, thumb, full) est écrit dans un
``*.tmp-<hex>`` du MÊME dossier puis publié par ``os.replace`` — un lecteur
public ne voit jamais un fichier partiel.
"""

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone

from db import get_db
from storage import get_storage

# Variable d'environnement (racine du webroot des albums) ; défaut sous
# ``<BASE_DIR>/.cache/albums`` (même logique que le cache de vignettes : PAS
# /tmp, qui disparaîtrait à chaque redémarrage).
ALBUM_WEB_DIR_ENV = "AIH_ALBUM_WEB_DIR"

# Taille de la vignette d'album : on réutilise la plus grande variante du cache
# de vignettes existant (THUMB_SIZES = 128/256/512).
ALBUM_THUMB_SIZE = 512

# Largeur du numéro d'item dans les noms de fichiers (``0001``, ``0002``, …).
ITEM_NAME_WIDTH = 4

# Alphabet volontairement restreint : ``secrets.token_urlsafe`` produit des
# caractères [A-Za-z0-9_-]; on borne la longueur pour interdire tout chemin
# exotique (pas de ``/``, pas de ``.``, donc aucun path-traversal possible).
_ALBUM_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

# Sous-dossiers canoniques du dossier album.
THUMB_SUBDIR = "thumb"
FULL_SUBDIR = "full"
MANIFEST_NAME = "manifest.json"

# Raisons d'échec par item (chaînes courtes et STABLES, exposées par l'API).
REASON_NOT_FOUND = "not_found"
REASON_NOT_OWNED = "not_owned"
REASON_TRASHED = "trashed"
REASON_UNSUPPORTED_KIND = "unsupported_kind"
REASON_SOURCE_UNAVAILABLE = "source_unavailable"
REASON_NO_TOOLS = "no_tools"
REASON_GENERATION_FAILED = "generation_failed"


# ── Racine web + confinement ──────────────────────────────────────────


def album_web_root():
    """Racine du webroot des albums (``AIH_ALBUM_WEB_DIR`` ou défaut projet)."""
    override = os.environ.get(ALBUM_WEB_DIR_ENV)
    if override:
        return override
    from extensions import BASE_DIR

    return os.path.join(str(BASE_DIR), ".cache", "albums")


def is_safe_album_key(key):
    """La clé est-elle une clé d'album valide (opaque, sans séparateur) ?"""
    return bool(key) and bool(_ALBUM_KEY_RE.match(str(key)))


def album_dir(key):
    """Chemin ABSOLU du dossier d'un album (lève ``ValueError`` si clé invalide).

    La validation de la clé (alphabet restreint, sans ``/`` ni ``.``) garantit
    qu'aucun chemin hors du webroot ne peut être visé.
    """
    if not is_safe_album_key(key):
        raise ValueError(f"clé d'album invalide : {key!r}")
    return os.path.join(album_web_root(), str(key))


def item_filename(item_no, ext):
    """Nom opaque d'un item : ``0001`` + extension (jamais le nom d'origine)."""
    return f"{int(item_no):0{ITEM_NAME_WIDTH}d}{ext}"


# ── Écritures atomiques ───────────────────────────────────────────────


def _atomic_write_bytes(path, data):
    """Écrit ``data`` (bytes) dans ``path`` de façon atomique (tmp → replace)."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp-{secrets.token_hex(6)}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _copy_file_atomic(src, dst):
    """Copie un fichier local vers ``dst`` de façon atomique."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f"{dst}.tmp-{secrets.token_hex(6)}"
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _save_image_atomic(image, out_path, fmt, **kwargs):
    """Sérialise une image Pillow vers ``out_path`` atomiquement."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f"{out_path}.tmp-{secrets.token_hex(6)}"
    try:
        image.save(tmp, fmt, **kwargs)
        os.replace(tmp, out_path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


# ── Détection de transparence réelle ──────────────────────────────────


def _alpha_channel_used(rgba):
    """Le canal alpha contient-il AU MOINS un pixel non opaque ?"""
    try:
        _lo, _hi = rgba.getchannel("A").getextrema()
        return _lo < 255
    except Exception:
        return False


def _image_uses_alpha(im):
    """L'image utilise-t-elle RÉELLEMENT la transparence (alpha non trivial) ?

    Une image RGBA dont tous les pixels sont opaques (alpha = 255 partout) n'est
    PAS considérée comme transparente : elle sera aplatie en JPEG (aucune perte
    visuelle). Le mode palette (``P``) est inspecté via ``info['transparency']``
    ou par conversion. Seule une transparence EFFECTIVEMENT rendue compte.
    """
    info = im.info or {}
    if im.mode == "P":
        if "transparency" in info:
            return True
        return _alpha_channel_used(im.convert("RGBA"))
    if im.mode in ("RGBA", "LA", "PA"):
        return _alpha_channel_used(im.convert("RGBA"))
    return False


# ── Construction des fichiers « full » et « thumb » ───────────────────


def _build_full(src_path, base_no_ext):
    """Re-encode l'original en JPEG q90 taille originale (ou PNG si alpha réel).

    Retourne ``(ext, width, height)``. Le ré-encodage JPEG **perd** les
    métadonnées/EXIF (on n'en réinjecte aucune) → vie privée préservée.
    """
    from PIL import Image, ImageOps

    with Image.open(src_path) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        if _image_uses_alpha(im):
            img = im.convert("RGBA")
            out = base_no_ext + ".png"
            _save_image_atomic(img, out, "PNG", optimize=True)
            return ".png", img.size[0], img.size[1]
        img = im.convert("RGB")
        out = base_no_ext + ".jpg"
        _save_image_atomic(img, out, "JPEG", quality=90, optimize=True)
        return ".jpg", img.size[0], img.size[1]


def _generate_album_thumb(media_mod, media_row, src_path, thumb_out):
    """Copie la vignette 512 du cache si présente, sinon la génère puis la copie.

    Retourne ``True`` si un fichier valide a été publié, ``False`` sinon.
    """
    cache_path = media_mod._thumbnail_cache_path(media_row, ALBUM_THUMB_SIZE)
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        _copy_file_atomic(cache_path, thumb_out)
        return True

    os.makedirs(os.path.dirname(thumb_out), exist_ok=True)
    tmp = f"{thumb_out}.tmp-{secrets.token_hex(6)}"
    try:
        if not media_mod._generate_thumbnail("image", src_path, tmp, ALBUM_THUMB_SIZE):
            return False
        if not (os.path.isfile(tmp) and os.path.getsize(tmp) > 0):
            return False
        os.replace(tmp, thumb_out)
        return True
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


# ── Génération d'UN item (rapport ok / raison) ────────────────────────


def _report(status, error="", ext="", width=None, height=None):
    return {"status": status, "error": error, "ext": ext, "width": width, "height": height}


def generate_album_item(media_row, item_no, album_path, expected_user_id):
    """Génère les fichiers d'UN item d'album et retourne son rapport.

    ``media_row`` est une ligne ``media_files`` (ou ``None``). Le rapport est un
    dict ``{status, error, ext, width, height}`` — ``status`` vaut ``ok`` ou
    ``failed`` ; ``error`` porte la raison STABLE (voir les constantes
    ``REASON_*``). N'élève jamais : tout échec devient un rapport.
    """
    if media_row is None:
        return _report("failed", REASON_NOT_FOUND)
    if media_row["user_id"] != expected_user_id:
        return _report("failed", REASON_NOT_OWNED)
    status = media_row["status"]
    if status == "trashed":
        return _report("failed", REASON_TRASHED)
    if status != "complete" or not media_row["final_path"]:
        return _report("failed", REASON_NOT_FOUND)
    if media_row["kind"] != "image":
        return _report("failed", REASON_UNSUPPORTED_KIND)

    from routes import media as media_mod

    if not media_mod._pillow_available():
        return _report("failed", REASON_NO_TOOLS)

    storage = get_storage()
    tmp_dir = tempfile.mkdtemp(prefix="aih_album_")
    src_tmp = os.path.join(tmp_dir, "src" + (media_row["ext"] or ""))
    full_path = None
    try:
        if not storage.download(media_row["final_path"], src_tmp):
            return _report("failed", REASON_SOURCE_UNAVAILABLE)

        base = os.path.join(album_path, FULL_SUBDIR, f"{int(item_no):0{ITEM_NAME_WIDTH}d}")
        try:
            ext, width, height = _build_full(src_tmp, base)
            full_path = base + ext
        except Exception as e:
            logging.warning("[album] re-encodage full échoué (media=%s) : %s", media_row["id"], e)
            return _report("failed", REASON_GENERATION_FAILED)

        thumb_out = os.path.join(album_path, THUMB_SUBDIR, item_filename(item_no, ".jpg"))
        try:
            if not _generate_album_thumb(media_mod, media_row, src_tmp, thumb_out):
                raise RuntimeError("génération vignette impossible")
        except Exception as e:
            logging.warning("[album] vignette échouée (media=%s) : %s", media_row["id"], e)
            with contextlib.suppress(OSError):
                os.remove(full_path)
            return _report("failed", REASON_GENERATION_FAILED)

        return _report("ok", "", ext, width, height)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Manifest ──────────────────────────────────────────────────────────


def _now_iso():
    """Horodatage ISO-8601 UTC (colonne ``updated_at`` / champ manifest)."""
    return datetime.now(timezone.utc).isoformat()


def manifest_items(rows):
    """Construit la liste d'items du manifest à partir de lignes ``album_media``.

    AUCUNE donnée privée : uniquement ``i`` (numéro opaque), ``w``/``h`` et
    ``ext``. La liste est triée par ``item_no``.
    """
    items = [{"i": int(r["item_no"]), "w": r["width"], "h": r["height"], "ext": r["ext"] or ".jpg"} for r in rows]
    items.sort(key=lambda x: x["i"])
    return items


def write_manifest(key, title, description, rows, updated_at=None):
    """Écrit (atomiquement) le manifest d'un album et retourne son contenu.

    ``rows`` = lignes ``album_media`` avec ``status='ok'``. Le manifest ne porte
    AUCUNE donnée privée (ni user_id, ni media_id, ni chemin, ni nom d'origine).
    """
    items = manifest_items(rows)
    manifest = {
        "title": title or "",
        "description": description or "",
        "count": len(items),
        "items": items,
        "updated_at": updated_at or _now_iso(),
    }
    data = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(os.path.join(album_dir(key), MANIFEST_NAME), data)
    return manifest


# ── Propagation de la purge ───────────────────────────────────────────


def _remove_item_files(key, item_no, ext):
    """Supprime les fichiers d'un item (thumb JPG + full selon ``ext``)."""
    try:
        base = album_dir(key)
    except ValueError:
        return
    with contextlib.suppress(OSError):
        os.remove(os.path.join(base, THUMB_SUBDIR, item_filename(item_no, ".jpg")))
    with contextlib.suppress(OSError):
        os.remove(os.path.join(base, FULL_SUBDIR, item_filename(item_no, ext or ".jpg")))


def remove_media_from_albums(media_id):
    """Retire un média de TOUS les albums qui le référencent (purge définitive).

    Appelé AVANT la suppression de la ligne ``media_files`` (la FK CASCADE
    effacerait sinon les liens sans permettre de réécrire le manifest ni de
    supprimer les fichiers d'album). Pour chaque album concerné : suppression
    des fichiers de l'item, retrait de la ligne ``album_media``, réécriture du
    manifest (hors albums ``revoked`` dont le dossier a été renommé).

    Retourne la liste des ids d'albums modifiés.
    """
    conn = get_db()
    try:
        links = conn.execute(
            "SELECT am.id AS link_id, am.album_id, am.item_no, am.ext, "
            "       a.key, a.title, a.description, a.status AS album_status "
            "FROM album_media am JOIN albums a ON a.id = am.album_id "
            "WHERE am.media_id = ?",
            (media_id,),
        ).fetchall()
        if not links:
            return []
        album_ids = sorted({link["album_id"] for link in links})
        for link in links:
            if link["album_status"] != "revoked":
                _remove_item_files(link["key"], link["item_no"], link["ext"])
            conn.execute("DELETE FROM album_media WHERE id = ?", (link["link_id"],))
        conn.commit()

        affected = []
        for album_id in album_ids:
            album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
            if not album or album["status"] == "revoked":
                continue
            rows = conn.execute(
                "SELECT * FROM album_media WHERE album_id = ? AND status = 'ok' ORDER BY item_no",
                (album_id,),
            ).fetchall()
            write_manifest(album["key"], album["title"], album["description"], rows)
            total = conn.execute("SELECT COUNT(*) FROM album_media WHERE album_id = ?", (album_id,)).fetchone()[0]
            done = conn.execute(
                "SELECT COUNT(*) FROM album_media WHERE album_id = ? AND status != 'pending'",
                (album_id,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE albums SET progress_total = ?, progress_done = ?, updated_at = ? WHERE id = ?",
                (total, done, _now_iso(), album_id),
            )
            affected.append(album_id)
        conn.commit()
        return affected
    finally:
        conn.close()
