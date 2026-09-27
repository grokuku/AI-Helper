"""Routes Media — sauvegarde serveur des médias produits par les nodes AIH.

Permet au node « AIH save media » (pack ComfyUI-AI-Helper) d'envoyer les
médias (image / vidéo / audio) vers le serveur AI-Helper au lieu de les écrire
localement, en upload CHUNKÉ (robuste derrière un reverse-proxy type Caddy).

Flow (calqué sur ``routes/files.py``) :
  1. POST /api/media/init          → crée l'upload (upload_id, chunk_size)
  2. POST /api/media/chunk         → append un chunk au fichier temporaire
  3. POST /api/media/complete      → range le fichier sous media/<user>/… + métadonnées
  4. GET  /api/media/<id>/download → sert le média (propriétaire ou admin)
  5. GET  /api/media/<id>/thumbnail → vignette (cache LOCAL persistant + navigateur)
  6. GET  /api/media/<id>/metadata → dimensions/durée/codec + prompt/workflow
  7. GET  /api/media               → liste paginée + filtres/tri (galerie)
  7bis. GET /api/media/folders      → sous-dossiers + nombre de médias (modale)
  7ter. GET /api/media/tags         → tags + nombre de médias (modale filtres)
  7quater. POST /api/media/<id>/tags → ajout/retrait de tags (manuel) sur UN média
  7quinquies. POST /api/media/tags   → ajout/retrait de tags GROUPÉ (multi-select)
  8. DELETE /api/media/<id>        → corbeille (soft delete, restaurable)
  9. POST /api/media/delete        → corbeille groupée (multi-select UI)
 10. POST /api/media/<id>/restore  → restaure un média corbeillé (variante groupée /restore)
 11. DELETE /api/media/<id>/purge  → suppression DÉFINITIVE (fichier + ligne)
 12. POST /api/media/<id>/auto-tag → auto-tag IA d'UN média image (preset vision)
 13. POST /api/media/auto-tag      → auto-tag IA GROUPÉ borné (≤ 5) → récap

Contraintes :
  - Authentification obligatoire (``_login_required``) : le ``user_id`` est
    DÉRIVÉ DU TOKEN, jamais fourni par le client.
  - Le fichier final est rangé via ``get_storage()`` (jamais de chemin disque
    en dur) → fonctionne avec LocalStorage ET SFTPStorage. Seules les VIGNETTES
    (donnée dérivée, régénérable) vivent dans un cache LOCAL au backend
    (``_thumb_cache_dir``), jamais sur le storage — réactivité de la galerie.
  - Sanitization stricte de ``user_id`` / ``subfolder`` / ``filename`` : tout
    segment ``.`` / ``..`` / absolu est neutralisé (confinement anti
    path-traversal).
  - Collision de nom → suffixe ``_0001`` (miroir du comportement local du node).
  - Le prompt (texte) et le workflow (JSON) sont persistés en colonnes de
    ``media_files`` (récupérables par id, sans dépendance à un format de
    fichier compagnon).
"""

import base64
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
from security.llm_url import (
    LLM_URL_OPTIN_HINT,
    _safe_llm_post,
    _validate_llm_base_url,
)
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
# Une réponse d'ERREUR de vignette (404 « indisponible », 400 taille invalide)
# ne doit JAMAIS être mémorisée durablement : sinon le navigateur (ou un cache
# intermédiaire, le 404 étant « heuristiquement cacheable » par défaut) pourrait
# resservir l'échec — p.ex. après installation de Pillow, la vignette resterait
# cassée. `no-store` est la directive la plus stricte (aucune conservation).
THUMB_ERROR_CACHE_CONTROL = "no-store, max-age=0"
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

# ── Tags média ────────────────────────────────────────────────────────
# Un tag est une chaîne courte, normalisée (trim + espaces réduits), bornée
# en longueur et restreinte à des caractères « raisonnables » (lettres — y
# compris accentuées via \w Unicode —, chiffres, espaces et quelques
# ponctuations usuelles). La VIRGULE est volontairement EXCLUE : elle sert de
# séparateur dans le paramètre de filtre ``?tags=a,b``.
TAG_MAX_LEN = 50
# Borne de sécurité du nombre de tags traités par requête (anti-DoS payload).
MAX_TAGS = 200
_TAG_ALLOWED_RE = re.compile(r"^[\w \-.\+#&/()'@]+$", re.UNICODE)
_TAG_SPACES_RE = re.compile(r"\s+")

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


def _normalize_tag(raw):
    """Normalise un tag saisi (``None`` si invalide).

    Normalisation : trim + réduction des espaces multiples à UN espace.
    Validation : non vide, longueur <= ``TAG_MAX_LEN``, caractères autorisés
    uniquement (``_TAG_ALLOWED_RE``). La CASSE est conservée telle que saisie
    (l'unicité insensible à la casse est assurée en base par ``COLLATE
    NOCASE`` ; c'est la 1re saisie qui reste visible).
    """
    if not isinstance(raw, str):
        return None
    tag = _TAG_SPACES_RE.sub(" ", raw).strip()
    if not tag or len(tag) > TAG_MAX_LEN:
        return None
    if not _TAG_ALLOWED_RE.match(tag):
        return None
    return tag


def _parse_tag_list(value):
    """Valide une liste de tags (``add``/``remove``) → liste normalisée dédupliquée.

    Retourne ``[]`` si ``value`` est ``None`` (champ absent) ; ``None`` si
    ``value`` n'est pas une liste OU si AU MOINS un élément est invalide
    (l'appelant renvoie alors 400). Le dédoublonnage est INSENSIBLE À LA CASSE
    (``casefold``) et préserve l'ordre + la casse de la 1re occurrence. Une
    liste trop volumineuse (> ``MAX_TAGS``) est refusée.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    out = []
    seen = set()
    for raw in value:
        tag = _normalize_tag(raw)
        if tag is None:
            return None
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    if len(out) > MAX_TAGS:
        return None
    return out


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


# ── Tags média : accès base ───────────────────────────────────────────

def _media_tags_rows(media_id):
    """Lignes de tags d'UN média, triées (nom, insensible à la casse)."""
    conn = get_db()
    try:
        return conn.execute(
            "SELECT tag, source FROM media_tags WHERE media_id = ? "
            "ORDER BY tag COLLATE NOCASE ASC, id ASC",
            (media_id,),
        ).fetchall()
    finally:
        conn.close()


def _tags_by_media(media_ids):
    """Tags de PLUSIEURS médias en UNE requête → ``{media_id: [lignes…]}``.

    Évite le N+1 de la liste paginée : ``_media_json`` reçoit alors les lignes
    déjà chargées au lieu de requêter une fois par média.
    """
    ids = list(media_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT media_id, tag, source FROM media_tags "
            f"WHERE media_id IN ({placeholders}) "
            "ORDER BY tag COLLATE NOCASE ASC, id ASC",
            ids,
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["media_id"], []).append(r)
    return out


def _tags_payload(tag_rows):
    """(liste de chaînes, liste ``{tag, source}``) à partir des lignes de tags."""
    tag_rows = tag_rows or []
    return (
        [r["tag"] for r in tag_rows],
        [{"tag": r["tag"], "source": r["source"]} for r in tag_rows],
    )


def _add_tags(conn, user_id, media_id, tags):
    """Ajoute des tags ``manual`` (``INSERT OR IGNORE``) → nombre créé.

    ``INSERT OR IGNORE`` s'appuie sur la contrainte ``UNIQUE(media_id, tag)``
    avec ``COLLATE NOCASE`` : un tag déjà présent dans une AUTRE casse est
    silencieusement ignoré → la casse de la 1re saisie est conservée.
    """
    added = 0
    for tag in tags:
        cur = conn.execute(
            "INSERT OR IGNORE INTO media_tags (user_id, media_id, tag, source) "
            "VALUES (?, ?, ?, 'manual')",
            (user_id, media_id, tag),
        )
        added += cur.rowcount
    return added


def _remove_tags(conn, media_id, tags):
    """Retire des tags d'un média (égalité NOCASE via la collation de colonne).

    Retourne le nombre de tags effectivement supprimés.
    """
    removed = 0
    for tag in tags:
        cur = conn.execute(
            "DELETE FROM media_tags WHERE media_id = ? AND tag = ?",
            (media_id, tag),
        )
        removed += cur.rowcount
    return removed


# ── Auto-tagging IA (vision) ───────────────────────────────────────────
# Un modèle « compatible vision » (API OpenAI-compatible) analyse une image
# RÉDUITE (~512 px, JPEG) envoyée en data-URL base64 et renvoie des tags. Les
# tags produits sont stockés avec ``source='ai'`` via ``INSERT OR IGNORE`` :
# ils ne peuvent JAMAIS écraser un tag manuel (unicité ``COLLATE NOCASE``).
#
# Découpage volontaire : UNE image par appel HTTP côté front (pas de lot dans
# un seul appel long). Le front boucle sur la sélection → progression visible
# et ANNULATION naturelle (la boucle s'arrête entre deux médias). Le lot borné
# ``POST /api/media/auto-tag`` (≤ ``AUTO_TAG_MAX_BATCH``) reste disponible pour
# les clients programmatiques (récap par média).
VISION_IMAGE_MAX_PX = 512
VISION_TAG_MAX = 12
# Borne du lot borné serveur : un lot = N appels LLM SÉQUENTIELS. On refuse
# au-delà pour ne jamais immobiliser un worker durablement.
AUTO_TAG_MAX_BATCH = 5
# Délai explicite (connexion, lecture) : un modèle de vision peut être lent.
VISION_TIMEOUT = (5, 60)

# Prompt SYSTÈME (rôle) — texte EXACT envoyé au modèle.
VISION_SYSTEM_PROMPT = (
    "You are an expert image-tagging assistant. You receive one image and must "
    "return a JSON array of concise tags describing its visible content."
)

# Prompt UTILISATEUR (texte EXACT) — l'image suit en partie ``image_url``.
VISION_USER_PROMPT = (
    "Tag this image. Return 5 to 12 tags as a JSON array of strings, ordered "
    "from the most to the least relevant. Use English, lowercase, single words "
    "or very short phrases (e.g. \"sunset\", \"beach\", \"long hair\"). Do not "
    "include generic words like \"image\", \"photo\", \"picture\", \"art\" or "
    "\"artwork\". Output ONLY the JSON array, with no explanation and no "
    "markdown code fence."
)

# Tags génériques/vides écartés systématiquement (bruit d'un modèle de vision).
_VISION_GENERIC_TAGS = frozenset({
    "image", "images", "photo", "photos", "picture", "pictures",
    "photograph", "photography", "art", "artwork", "illustration",
    "render", "rendering", "drawing", "painting", "wallpaper",
    "screenshot", "digital art", "ai", "ai generated", "generated",
    "graphic", "jpeg", "jpg", "png",
})


def _parse_preset_id(value):
    """Valide ``preset_id`` d'un corps JSON → ``(int|None, err)``.

    Absent/``null`` → ``(None, None)`` (choix automatique du preset vision).
    Booléen ou non-entier → ``(None, (response, 400))`` (validation stricte).
    """
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, (jsonify({'error': "preset_id doit être un entier", 'reason': 'bad_preset_id'}), 400)
    return value, None


def _resolve_vision_preset(conn, user_id, preset_id):
    """Résout le preset de vision à utiliser pour l'auto-tagging.

    Returns:
        tuple: ``(row, err)`` où ``err`` est un couple ``(response, status)``
        prêt à être retourné (ou ``None``), et ``row`` la ligne ``ai_presets``.

    - ``preset_id`` fourni : doit être visible (global ou propriétaire) sinon
      404 (anti-énumération), et marqué ``supports_vision=1`` sinon 400 ;
    - absent : premier preset **vision** visible (global d'abord, puis nom) ;
    - aucun preset vision → 400 actionnable (message explicite).
    """
    if preset_id is not None:
        row = conn.execute(
            "SELECT * FROM ai_presets WHERE id = ? AND (user_id = ? OR is_global = 1)",
            (preset_id, user_id),
        ).fetchone()
        if not row:
            return None, (jsonify({'error': 'Preset introuvable', 'reason': 'preset_not_found'}), 404)
        if not _row_get(row, 'supports_vision', 0):
            return None, (jsonify({
                'error': "Ce preset n'est pas marqué « compatible vision ». "
                         "Coche « compatible vision » dans Paramètres > Provider LLM.",
                'reason': 'preset_not_vision',
            }), 400)
        return row, None
    row = conn.execute(
        "SELECT * FROM ai_presets WHERE supports_vision = 1 AND (user_id = ? OR is_global = 1) "
        "ORDER BY is_global DESC, name COLLATE NOCASE ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return None, (jsonify({
            'error': "Aucun preset compatible vision. Coche « compatible vision » sur un "
                     "preset dans Paramètres > Provider LLM.",
            'reason': 'no_vision_preset',
        }), 400)
    return row, None


def _vision_image_b64(row, max_px=VISION_IMAGE_MAX_PX):
    """Prépare une image RÉDUITE (~512 px JPEG) encodée en base64.

    Réutilise la logique de vignette existante (Pillow puis repli ffmpeg, cache
    local) : un seul chemin de génération d'image réduite, déjà éprouvé par la
    galerie. Retourne ``(base64, raison)`` : ``base64`` (SANS le préfixe
    ``data:``) ou ``None`` + une raison courte (``no_tools`` /
    ``source_unavailable`` / ``generation_failed``).
    """
    size = min(THUMB_SIZES, key=lambda s: (abs(s - max_px), s))
    thumb_path = _thumbnail_cache_path(row, size)
    local, reason = _ensure_thumbnail_file(row, size, thumb_path)
    if not local:
        return None, reason
    try:
        with open(local, 'rb') as fh:
            data = fh.read()
    except OSError:
        return None, 'source_unavailable'
    if not data:
        return None, 'generation_failed'
    return base64.b64encode(data).decode('ascii'), None


def _vision_try_json(text):
    """``json.loads`` tolérant (``None`` si invalide)."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _vision_extract_content(content):
    """Texte d'un message assistant (str, ou liste de parts OpenAI)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get('text'), str):
                parts.append(part['text'])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ''


def _vision_parse_tags(raw):
    """Extrait une liste de tags propres de la réponse brute du modèle.

    Tolère : tableau JSON, objet ``{"tags": [...]}`` (ou labels/keywords),
    fences Markdown `````json`````` et listes à puces. Chaque candidat passe par
    ``_normalize_tag`` (autorité de normalisation partagée), les doublons
    (insensibles à la casse) et les tags génériques sont écartés, et le nombre
    est borné à ``VISION_TAG_MAX``.
    """
    text = _vision_extract_content(raw).strip()
    if not text:
        return []
    unfenced = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', text)
    unfenced = re.sub(r'\s*```\s*$', '', unfenced).strip()
    parsed = _vision_try_json(unfenced)
    if parsed is None:
        match = re.search(r'\[.*\]', text, re.S)
        if match:
            parsed = _vision_try_json(match.group(0))
    if isinstance(parsed, dict):
        parsed = parsed.get('tags') or parsed.get('labels') or parsed.get('keywords')
    candidates = []
    if isinstance(parsed, list):
        candidates = [c for c in parsed if isinstance(c, str)]
    else:
        # Repli texte : une ligne par tag, liste à puces tolérée, virgules éclatées.
        for line in re.split(r'[\r\n]+', text):
            line = re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', line)
            for token in re.split(r'[,;]', line):
                token = token.strip().strip('"\'')
                if token:
                    candidates.append(token)
    out = []
    seen = set()
    for cand in candidates:
        tag = _normalize_tag(cand)
        if tag is None:
            continue
        key = tag.casefold()
        if key in seen or key in _VISION_GENERIC_TAGS:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= VISION_TAG_MAX:
            break
    return out


def _call_vision_llm(preset_row, image_b64):
    """Envoie l'image au modèle de vision du preset (OpenAI-compatible).

    Rejoue la politique anti-SSRF PARTAGÉE (``_validate_llm_base_url`` +
    ``_safe_llm_post``), utilise la clé API DÉCHIFFRÉE, et n'expose JAMAIS la
    clé ni l'URL dans la réponse ou les logs.

    Returns:
        tuple: ``(content, status, detail)``. ``status='ok'`` → ``content`` est
        le texte du message assistant ; sinon ``content`` est ``None`` et
        ``status`` ∈ {'blocked','unreachable','llm_error','bad_response'}.
    """
    base_url = (preset_row['base_url'] or '').rstrip('/')
    api_key = decrypt_api_key(preset_row['api_key_encrypted'])
    model = preset_row['model']

    err = _validate_llm_base_url(base_url)
    if err:
        logging.warning("[media] auto-tag LLM refusé (preset %s) : %s", preset_row['id'], err)
        return None, 'blocked', f"{err}. {LLM_URL_OPTIN_HINT}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_USER_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 300,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = _safe_llm_post(
            f"{base_url}/chat/completions", payload, headers=headers, timeout=VISION_TIMEOUT
        )
    except ValueError:
        # Refus anti-SSRF en cours d'appel (redirection, résolution tardive…).
        return None, 'blocked', 'Fournisseur refusé (protection SSRF).'
    except Exception as exc:
        logging.warning("[media] auto-tag LLM injoignable (preset %s) : %s", preset_row['id'], exc)
        return None, 'unreachable', 'Fournisseur LLM injoignable.'

    if not resp.ok:
        logging.warning("[media] auto-tag LLM HTTP %s (preset %s)", resp.status_code, preset_row['id'])
        return None, 'llm_error', f"Le fournisseur LLM a renvoyé HTTP {resp.status_code}."

    try:
        data = resp.json()
    except Exception:
        return None, 'bad_response', 'Réponse non-JSON du fournisseur LLM.'

    choices = data.get('choices') if isinstance(data, dict) else None
    message = choices[0].get('message') if choices and isinstance(choices[0], dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if content is None:
        return None, 'bad_response', 'Réponse du fournisseur LLM sans contenu.'
    return content, 'ok', None


def _add_ai_tags(conn, user_id, media_id, tags):
    """Ajoute des tags ``ai`` (``INSERT OR IGNORE``) → nombre créé.

    La contrainte ``UNIQUE(media_id, tag)`` ``COLLATE NOCASE`` ignore un tag
    déjà présent QUELLE QUE SOIT sa source : un tag manuel identique n'est donc
    JAMAIS converti ni dupliqué (il reste ``manual``).
    """
    added = 0
    for tag in tags:
        cur = conn.execute(
            "INSERT OR IGNORE INTO media_tags (user_id, media_id, tag, source) "
            "VALUES (?, ?, ?, 'ai')",
            (user_id, media_id, tag),
        )
        added += cur.rowcount
    return added


def _auto_tag_media(row, user_id, preset_row):
    """Auto-tag IA d'UN média (image) → dict de récap (écrit les tags ``ai``).

    Statuts : ``tagged`` / ``skipped`` (raison ``not_image`` ou ``no_tags``) /
    ``error`` (raison ``no_tools`` / ``source_unavailable`` / ``generation_failed``
    / ``blocked`` / ``unreachable`` / ``llm_error`` / ``bad_response``).
    """
    if row['kind'] != 'image':
        return {'status': 'skipped', 'reason': 'not_image', 'added': 0, 'ai_tags': [],
                'detail': 'Seules les images sont prises en charge pour le moment.'}
    image_b64, why = _vision_image_b64(row)
    if not image_b64:
        return {'status': 'error', 'reason': why, 'added': 0, 'ai_tags': [],
                'detail': f"Image non préparable ({why})."}
    content, status, detail = _call_vision_llm(preset_row, image_b64)
    if status != 'ok':
        return {'status': 'error', 'reason': status, 'added': 0, 'ai_tags': [],
                'detail': detail}
    tags = _vision_parse_tags(content)
    if not tags:
        return {'status': 'skipped', 'reason': 'no_tags', 'added': 0, 'ai_tags': [],
                'detail': 'Aucun tag exploitable renvoyé par le modèle.'}
    conn = get_db()
    try:
        added = _add_ai_tags(conn, user_id, row['id'], tags)
        conn.commit()
    finally:
        conn.close()
    return {'status': 'tagged', 'added': added, 'ai_tags': tags}


def _media_json(row, tag_rows=None):
    """Sérialise une ligne ``media_files`` pour l'API (contrat figé).

    Superset rétro-compatible : les clés historiques sont conservées, on
    ajoute ``thumb`` (URL GET de la vignette, immuable → cache navigateur),
    ``thumb_available`` (la vignette est-elle produisible dans cet
    environnement : image=Pillow|ffmpeg, vidéo=ffmpeg, audio=non) et les TAGS :
    ``tags`` (liste de chaînes) + ``tags_detail`` (``{tag, source}`` — prépare
    la distinction manuel/IA). ``tag_rows`` (optionnel) permet à la liste
    paginée de fournir un lot déjà chargé (pas de N+1).
    """
    media_id = row["id"]
    if tag_rows is None:
        tag_rows = _media_tags_rows(media_id)
    tags, tags_detail = _tags_payload(tag_rows)
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
        # Favori / « à exposer » : drapeau unique booléen (future galerie
        # publique). Toujours exposé, dans la liste comme dans /metadata.
        "favorite": bool(row["favorite"]),
        # Tags (manuel/IA) : liste de chaînes + détail (source) pour l'UI.
        "tags": tags,
        "tags_detail": tags_detail,
        # État corbeille (soft delete) : l'UI peut identifier un média corbeillé.
        "status": row["status"],
        "trashed": row["status"] == "trashed",
        "trashed_at": row["trashed_at"] or "",
    }


# ── Outils d'extraction technique (Pillow / ffmpeg / ffprobe) ─────────

def _pillow_available():
    """Pillow est-il importable ? (dégradation propre si absent).

    Détection DYNAMIQUE : ``import PIL.Image`` est retenté à CHAQUE appel
    (volontairement AUCUN cache/constante de module) — installer Pillow après
    le démarrage du process prend donc effet sans redémarrage Flask.
    """
    try:
        import PIL.Image  # noqa: F401
        return True
    except Exception:
        return False


def _ffmpeg_path():
    """Chemin de l'exécutable ffmpeg (env ``AIH_FFMPEG`` ou PATH), ou None.

    Résolu à CHAQUE appel (aucun cache) : un ``apt install``/changement d'env
    prend effet sans redémarrage.
    """
    return os.environ.get("AIH_FFMPEG") or shutil.which("ffmpeg")


def _ffprobe_path():
    """Chemin de l'exécutable ffprobe (env ``AIH_FFPROBE`` ou PATH), ou None.

    Résolu à CHAQUE appel (aucun cache), comme ``_ffmpeg_path``.
    """
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


def _thumb_cache_dir():
    """Dossier LOCAL persistant du cache de vignettes (survit aux redémarrages).

    Les VIGNETTES (donnée dérivée, régénérable, jetable) vivent sur le disque
    LOCAL du backend — JAMAIS sur le storage (SFTP) : un aller-retour SFTP par
    vignette rendrait la galerie non réactive. Les MÉDIAS, eux, restent sur le
    storage (SFTP si configuré).

    Défaut : ``<BASE_DIR>/.cache/thumbnails`` (sous la racine projet, PAS /tmp :
    un cache sous /tmp disparaîtrait à chaque redémarrage/reboot et serait
    reconstruit à froid — d'où la latence qu'on veut justement éviter).
    Surchargeable par ``AIH_THUMB_CACHE_DIR`` (chemin absolu) — utile en
    déploiement pour poser le cache sur un volume dédié (ex. ``/var/cache/aih``).
    """
    override = os.environ.get("AIH_THUMB_CACHE_DIR")
    if override:
        return override
    return os.path.join(str(BASE_DIR), ".cache", "thumbnails")


def _thumbnail_cache_path(row, size):
    """Chemin ABSOLU LOCAL de la vignette (clé canonique stable).

    ``<cache>/<user>/<sha1(id:final_path)>_<size>.jpg`` — l'id et le
    ``final_path`` garantissent l'unicité et l'invalidation naturelle si le
    média change de chemin ; la taille fait partie de la clé (une variante par
    couple média/taille). Le fichier vit en LOCAL (voir ``_thumb_cache_dir``),
    jamais dans le storage : le lire ne coûte aucun accès SFTP.
    """
    key = hashlib.sha1(f"{row['id']}:{row['final_path'] or ''}".encode()).hexdigest()[:24]
    return os.path.join(
        _thumb_cache_dir(), _sanitize_user_id(row['user_id']), f"{key}_{size}.jpg"
    )


def _thumbnail_etag(row, size):
    """ETag stable (l'URL est immuable), SANS quotes : version pour invalidation.

    L'empreinte couvre ``thumb:v1:<id>:<size>:<final_path>``. Le contenu d'un
    média est IMMUABLE (uploadé une seule fois, ``final_path`` figé pour un id
    donné) : la vignette d'un (média, taille) ne peut donc pas changer sans
    changer d'URL. ``thumb:v1`` est une VERSION DE STRATÉGIE de génération :
    toute modification de ``_generate_thumbnail`` (outil/format/qualité) doit
    incrémenter ce préfixe — sinon un ``304`` pourrait resservir une vignette
    produite par l'ancienne génération.
    """
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
    """Prépare la vignette LOCALE (cache local → génération depuis le storage).

    Ordre : fichier LOCAL déjà présent → sinon téléchargement de la SOURCE
    depuis le storage puis génération (Pillow/ffmpeg) et écriture ATOMIQUE du
    fichier local. AUCUN upload de la vignette vers le storage : elle ne
    quitte jamais le disque local du backend.

    Retourne ``(chemin_local, None)`` en cas de succès, sinon ``(None, raison)``.
    La ``raison`` (chaîne courte, stable) rend l'échec DIAGNOSTIQUABLE et est
    exposée par la route :

      - ``cache_unavailable``  : dossier de cache local non créable / non
                                 inscriptible (permissions, disque plein) ;
      - ``no_tools``           : ni Pillow ni ffmpeg pour ce type de média
                                 (type non couvert : audio, ou dépendance
                                 manquante dans le déploiement) ;
      - ``source_unavailable`` : le média source n'a pas pu être lu du storage ;
      - ``generation_failed``  : l'outil a échoué (format/EXIF/taille illisible).

    Aucune donnée sensible n'est journalisée (id + type + disponibilité des
    outils uniquement).
    """
    # 1) Cache LOCAL déjà présent → servir directement (ZÉRO accès storage).
    try:
        if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path, None
    except OSError:
        pass

    # 2) Préparer le dossier de cache local (création paresseuse des parents)
    #    + sonde d'écriture : distingue « cache inutilisable » d'un simple échec
    #    de génération, pour un ``reason`` exact et une erreur loguée explicite.
    cache_dir = os.path.dirname(thumb_path)
    tmp_out = f"{thumb_path}.tmp-{secrets.token_hex(6)}"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(tmp_out, "wb"):
            pass
    except OSError as e:
        logging.error("[media] cache vignettes local inutilisable (%s) : %s", cache_dir, e)
        return None, "cache_unavailable"

    # 3) Aucune vignette pour l'audio, ou aucun outil disponible.
    if not _kind_can_have_thumbnail(row["kind"]):
        with contextlib.suppress(OSError):
            os.remove(tmp_out)
        logging.warning(
            "[media] vignette indisponible id=%s kind=%s : aucun outil "
            "(Pillow=%s, ffmpeg=%s) — dépendance manquante ?",
            row["id"], row["kind"], _pillow_available(), _ffmpeg_path() is not None,
        )
        return None, "no_tools"

    # 4) Générer depuis le média source (téléchargé du storage).
    storage = get_storage()
    src_tmp = os.path.join(TEMP_DIR, f"tsrc_{row['id']}{row['ext'] or ''}")
    if not storage.download(row["final_path"], src_tmp):
        with contextlib.suppress(OSError):
            os.remove(tmp_out)
        logging.warning(
            "[media] vignette id=%s : média source illisible depuis le storage (%s)",
            row["id"], row["final_path"],
        )
        return None, "source_unavailable"
    try:
        if not _generate_thumbnail(row["kind"], src_tmp, tmp_out, size):
            logging.warning(
                "[media] vignette id=%s : génération échouée (kind=%s, Pillow=%s, ffmpeg=%s)",
                row["id"], row["kind"], _pillow_available(), _ffmpeg_path() is not None,
            )
            return None, "generation_failed"
        # 5) Publication ATOMIQUE dans le cache local (rename sur le même FS).
        os.replace(tmp_out, thumb_path)
    except OSError as e:
        logging.error("[media] écriture vignette locale impossible (%s) : %s", thumb_path, e)
        return None, "cache_unavailable"
    finally:
        with contextlib.suppress(OSError):
            os.remove(src_tmp)
        with contextlib.suppress(OSError):
            os.remove(tmp_out)
    return thumb_path, None


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


def _needs_technical_backfill(row):
    """Faut-il (re)tenter l'extraction technique pour cette ligne ?

    Deux cas :

      - ``meta_checked=0`` : colonnes jamais renseignées (lignes antérieures à
        la migration, ou extraction précédemment impossible) → on tente ;
      - ``meta_checked=1`` mais technique ENTIÈREMENT NULL : l'upload a eu
        lieu alors que l'outil manquait dans le déploiement (Pillow absent côté
        prod pendant l'incident, ffprobe absent pour vidéo/audio). La tentative
        était vacuité → on retente MAINTENANT si un outil adapté au type est
        disponible (réparation « one-shot » des médias anciens). Sans outil, on
        ne relit PAS le fichier pour rien (pas de coût sur chaque requête).

    Un échec persistant (fichier corrompu) peut relancer une tentative à
    chaque appel de /metadata : route déclenchée à la demande (panneau d'infos),
    jamais en boucle automatique — compromis assumé pour l'auto-réparation.
    """
    if not row["meta_checked"]:
        return True
    if (row["width"] is not None or row["height"] is not None
            or row["duration_ms"] is not None or row["codec"] is not None):
        return False
    kind = row["kind"]
    if kind == "image":
        # Repli d'extraction d'image = ffprobe (comme _extract_technical_metadata).
        return _pillow_available() or _ffprobe_path() is not None
    if kind in ("video", "audio"):
        return _ffprobe_path() is not None
    return False


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
    tags, tags_detail = _tags_payload(_media_tags_rows(row["id"]))
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
        "favorite": bool(row["favorite"]),
        # Tags (manuel/IA) : liste de chaînes + détail (source). Utile au
        # panneau d'infos ET à l'auto-tagging IA à venir.
        "tags": tags,
        "tags_detail": tags_detail,
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


def _set_favorite(media_id, favorite):
    """Applique le drapeau favori (idempotent) et retourne la ligne à jour.

    ``favorite`` est un booléen Python → stocké 0/1. Aucune autre colonne n'est
    touchée (le média reste ``complete``/``trashed``, la corbeille est
    indépendante du favori).
    """
    conn = get_db()
    try:
        conn.execute(
            "UPDATE media_files SET favorite = ? WHERE id = ?",
            (1 if favorite else 0, media_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM media_files WHERE id = ?", (media_id,)
        ).fetchone()
    finally:
        conn.close()


def _parse_favorite(value):
    """Valide le booléen ``favorite`` d'un body JSON (``None`` si invalide).

    Accepte un booléen JSON (``true``/``false``) ou les entiers 0/1 par
    tolérance API ; toute autre valeur (chaîne, null, absent) est refusée.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


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
    """Suppression DÉFINITIVE : fichier + vignettes locales + ligne.

    Le FICHIER média est retiré via ``get_storage()`` (Local ET SFTP). Les
    vignettes vivent dans le CACHE LOCAL (``_thumbnail_cache_path``, une variante
    par taille de ``THUMB_SIZES``) : elles sont retirées du disque local — aucun
    orphelin local. Les vignettes éventuellement restées sur le storage (écrites
    AVANT migration, inoffensives) ne sont PAS touchées ici. Best-effort : un
    fichier déjà absent ne fait pas échouer l'opération (on supprime quand même
    la ligne).
    """
    storage = get_storage()
    removed_file = False
    if row["final_path"]:
        with contextlib.suppress(Exception):
            removed_file = bool(storage.delete(row["final_path"]))
    for size in THUMB_SIZES:
        with contextlib.suppress(OSError):
            os.remove(_thumbnail_cache_path(row, size))
    conn = get_db()
    try:
        # SUPPRESSION DES TAGS : EXPLICITE (la cascade demandée vit ICI). Elle
        # doit précéder la suppression de la ligne média, car la clé étrangère
        # ``media_tags.media_id`` (sans cascade) REFUSE de laisser un média
        # référencé → aucune ligne de tag orpheline possible. La corbeille
        # (soft delete) NE passe PAS par ici → elle conserve les tags : seule
        # la purge les efface avec le média.
        conn.execute("DELETE FROM media_tags WHERE media_id = ?", (row["id"],))
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


def _status_clause(args):
    """Clause WHERE de statut, PARTAGÉE par la liste et la liste des dossiers.

    - absent / ``complete`` → médias VIVANTS (``status='complete'``), les
      corbeillés sont donc EXCLUS par défaut ;
    - ``trashed`` → uniquement la corbeille ;
    - ``all`` → aucune clause (tous les statuts).

    Retourne ``""`` (aucune clause), la clause SQL, ou ``None`` si la valeur
    est invalide (l'appelant renvoie alors 400).
    """
    status = (args.get("status") or "").strip().lower()
    if status == "all":
        return ""
    if status == "trashed":
        return "status = 'trashed'"
    if status in ("", "complete"):
        return "status = 'complete'"
    return None


def _favorite_clause(args):
    """Clause WHERE du filtre favori (``favorite=1`` / ``favorite=0``).

    - absent / vide → aucune clause (pas de filtre, comportement historique) ;
    - ``1``/``true`` → médias favoris ; ``0``/``false`` → médias non favoris ;
    - toute autre valeur → ``None`` (l'appelant renvoie alors 400).

    Retourne ``""`` (aucun filtre), la clause SQL, ou ``None`` si invalide.
    """
    raw = (args.get("favorite") or "").strip().lower()
    if raw == "":
        return ""
    if raw in ("1", "true"):
        return "favorite = 1"
    if raw in ("0", "false"):
        return "favorite = 0"
    return None


def _subfolder_param_values(args):
    """Valeurs BRUTES du paramètre ``subfolders`` (liste), tolérant un dict.

    ``request.args`` est un MultiDict (``getlist``) ; on accepte aussi un dict
    simple pour les appels internes/tests.
    """
    getlist = getattr(args, "getlist", None)
    if callable(getlist):
        return list(getlist("subfolders"))
    value = args.get("subfolders") if hasattr(args, "get") else None
    return [value] if value else []


def _parse_subfolders(args):
    """Liste dédupliquée des sous-dossiers du filtre MULTI ``subfolders``.

    Accepte le paramètre RÉPÉTÉ (``?subfolders=a&subfolders=b``) ET/OU séparé
    par des virgules (``?subfolders=a,b``). Chaque valeur est sanitizée (mêmes
    règles que l'upload) ; une valeur NON vide qui se réduit à du vide est
    invalide → ``None`` (l'appelant renvoie 400).

    Convention RACINE : une valeur PRÉSENTE mais VIDE (``?subfolders=``) désigne
    le dossier racine (``subfolder = ''``) ; l'ABSENCE du paramètre = aucun
    filtre. L'ordre des valeurs est conservé (déduplication stable).
    """
    values = _subfolder_param_values(args)
    if not values:
        return []  # paramètre absent → aucun filtre
    out = []
    seen = set()
    for value in values:
        for raw in str(value).split(","):
            if raw == "":
                safe = ""  # valeur présente mais vide = dossier RACINE
            else:
                raw = raw.strip()
                if raw == "":
                    continue  # espaces parasites → ignorés
                safe = _sanitize_subfolder(raw)
                if not safe:
                    return None  # valeur invalide (ex. « .. ») → 400
            if safe not in seen:
                seen.add(safe)
                out.append(safe)
    return out


def _tags_param_values(args):
    """Valeurs BRUTES du paramètre ``tags`` (liste), tolérant un dict.

    Même mécanique que ``_subfolder_param_values`` : ``request.args`` est un
    MultiDict (``getlist``) ; on accepte aussi un dict simple pour les appels
    internes/tests.
    """
    getlist = getattr(args, "getlist", None)
    if callable(getlist):
        return list(getlist("tags"))
    value = args.get("tags") if hasattr(args, "get") else None
    return [value] if value else []


def _parse_tags_filter(args):
    """Liste dédupliquée des tags du filtre MULTI ``tags`` (sémantique OU).

    Accepte le paramètre RÉPÉTÉ (``?tags=a&tags=b``) ET/OU séparé par des
    virgules (``?tags=a,b``). Chaque valeur est normalisée (``_normalize_tag``) ;
    une valeur NON vide invalide → ``None`` (l'appelant renvoie 400).

    Convention : l'ABSENCE du paramètre = aucun filtre ; un segment vide
    (``?tags=`` ou ``?tags=a,,b``) est simplement IGNORÉ (contrairement à
    ``subfolders`` où le vide signifie « racine », un tag vide n'a pas de sens).
    L'ordre est conservé (déduplication stable, insensible à la casse).
    """
    values = _tags_param_values(args)
    if not values:
        return []
    out = []
    seen = set()
    for value in values:
        for raw in str(value).split(","):
            raw = raw.strip()
            if raw == "":
                continue
            tag = _normalize_tag(raw)
            if tag is None:
                return None
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tag)
    return out


def _build_list_filters(user_id, args):
    """Construit (where_sql, params) pour la liste, d'après les filtres optionnels.

    Tous les filtres sont OPTIONNELS : absents → comportement historique
    (médias ``complete`` de l'utilisateur, SANS les corbeillés). Retourne
    ``None`` si un filtre est invalide (type/sous-dossier(s)/statut/tags).
    """
    where = ["user_id = ?"]
    params = [user_id]

    # Statut : par défaut (absent ou ``complete``) les médias VIVANTS → les
    # corbeillés sont EXCLUS (non-régression du comportement historique).
    # ``status=trashed`` expose la corbeille ; ``status=all`` lève le filtre.
    status_clause = _status_clause(args)
    if status_clause is None:
        return None
    if status_clause:
        where.append(status_clause)

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

    # Filtre MULTI-dossiers : sémantique OU, correspondance EXACTE (chaque
    # dossier sélectionné dans la modale), cohérente avec les comptes exacts de
    # ``GET /api/media/folders``. Combiné EN PLUS de ``subfolder`` (ET) quand
    # les deux sont présents. Une liste vide = pas de filtre.
    subfolders = _parse_subfolders(args)
    if subfolders is None:
        return None
    if subfolders:
        placeholders = ",".join("?" for _ in subfolders)
        where.append("subfolder IN (" + placeholders + ")")
        params.extend(subfolders)

    # Filtre TAGS : sémantique OU (UNION) — un média ressort dès qu'il porte AU
    # MOINS un des tags demandés. Choix justifié dans ``media_list`` (cohérent
    # avec ``subfolders`` : un filtre multi-sélection élargit la vue, ne la
    # rétrécit pas). Sous-requête sur ``media_tags`` (colonne ``tag`` COLLATE
    # NOCASE → correspondance insensible à la casse). Absent / vide = pas de
    # filtre.
    tags = _parse_tags_filter(args)
    if tags is None:
        return None
    if tags:
        placeholders = ",".join("?" for _ in tags)
        where.append(
            "id IN (SELECT media_id FROM media_tags WHERE tag IN ("
            + placeholders + "))"
        )
        params.extend(tags)

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

    # Filtre FAVORI (flag unique « à exposer ») : ``favorite=1`` n'expose que
    # les favoris, ``favorite=0`` que les non-favoris ; absent = pas de filtre.
    favorite_clause = _favorite_clause(args)
    if favorite_clause is None:
        return None
    if favorite_clause:
        where.append(favorite_clause)

    return " AND ".join(where), params


@app.route('/api/media', methods=['GET'])
def media_list():
    """Liste paginée des médias complets de l'utilisateur courant (galerie).

    Paramètres optionnels (galerie) — tous rétro-compatibles :
      - ``page`` (déf. 1), ``limit`` (déf. 50, max 200) ;
      - ``kind`` ∈ image|video|audio ;
      - ``subfolder`` : dossier exact OU préfixe de segment (``a/b``) ;
      - ``subfolders`` : filtre MULTI-dossiers (OU, correspondance exacte).
        Paramètre RÉPÉTÉ (``?subfolders=a&subfolders=b``) et/ou séparé par des
        virgules (``?subfolders=a,b``). Une valeur vide (``?subfolders=``)
        désigne le dossier RACINE. Absent = aucun filtre ; valeur invalide → 400.
        Voir aussi ``GET /api/media/folders`` (liste + comptes pour la modale) ;
      - ``q`` : recherche sur ``filename`` (LIKE, insensible aux jokers) ;
      - ``from`` / ``to`` : plage ``created_at`` (date ou date-heure) ;
      - ``status`` : ``complete`` (déf.) | ``trashed`` (corbeille) | ``all`` ;
      - ``favorite`` : ``1``/``true`` (favoris) | ``0``/``false`` (non favoris).
        Absent = pas de filtre ; valeur invalide → 400 ;
      - ``tags`` : filtre MULTI-tags, SÉMANTIQUE OU (union) — un média ressort
        dès qu'il porte AU MOINS un des tags demandés. Paramètre RÉPÉTÉ
        (``?tags=a&tags=b``) et/ou séparé par des virgules (``?tags=a,b``).
        Absent = aucun filtre ; tag invalide → 400. Voir aussi
        ``GET /api/media/tags`` (liste + comptes pour la modale) ;
      - ``sort`` : created_at_desc | created_at_asc | name_asc | name_desc |
        size_desc | size_asc (déf. created_at_desc ≡ comportement historique).

    Pourquoi OU (et non ET) pour ``tags`` ? Un filtre multi-sélection à cases
    à cocher ÉLARGIT naturellement la vue quand on coche un élément de plus
    (symétrie avec ``subfolders``) ; un ET ferait au contraire disparaître les
    médias dès qu'un tag coché n'est pas présent sur eux (vue vide dès le 2e
    tag sans co-occurrence), ce qui surprend pour un simple parcours. Le ET
    reste exprimable en enchaînant ``tags`` ET un autre filtre (p.ex. ``q``).

    Sans aucun paramètre, la réponse est IDENTIQUE à l'historique
    (mêmes clés, même ordre ``id DESC``), avec en plus ``thumb`` /
    ``thumb_available``, les champs corbeille ``status``/``trashed`` /
    ``trashed_at`` et les tags (``tags``/``tags_detail``). Les médias corbeillés
    sont EXCLUS par défaut.
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
        return jsonify({'error': 'filtre invalide (kind/subfolder/subfolders/status/favorite/tags)'}), 400
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

    # Tags : UN chargement groupé pour toute la page (pas de N+1).
    tags_map = _tags_by_media([r["id"] for r in rows])
    return jsonify({
        'items': [_media_json(r, tags_map.get(r["id"])) for r in rows],
        'total': total,
        'page': page,
        'limit': limit,
    })


@app.route('/api/media/folders', methods=['GET'])
def media_folders():
    """Liste des sous-dossiers de l'utilisateur courant, avec le NOMBRE de médias.

    Alimente la MODALE de sélection des dossiers de la galerie (le datalist
    « sous-dossier » ne voyait que les items déjà chargés).

    Agrégation en BASE (``GROUP BY subfolder``), jamais de parcours Python des
    médias. Mêmes règles d'accès que la liste : ``user_id`` DÉRIVÉ du token
    (aucun paramètre client), corbeillés EXCLUS par défaut.

    Paramètre optionnel :
      - ``status`` : ``complete`` (déf., SANS les corbeillés) | ``trashed``
        (comptes de la corbeille) | ``all`` (tous statuts) — mêmes valeurs que
        la liste, pour rester cohérent avec la vue courante.

    Réponse STABLE et TRIÉE (nom, insensible à la casse) :
      ``{"folders": [{"subfolder": "a/b", "count": 2}, …], "total": 5}``
    où ``subfolder`` est la chaîne brute du dossier (``""`` = racine) et
    ``total`` la somme des comptes (nombre de médias représentés).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    status_clause = _status_clause(request.args)
    if status_clause is None:
        return jsonify({'error': 'filtre invalide (status)'}), 400

    where = ["user_id = ?"]
    params = [user_id]
    if status_clause:
        where.append(status_clause)
    where_sql = " AND ".join(where)

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT COALESCE(subfolder, '') AS sf, COUNT(*) AS n "
            f"FROM media_files WHERE {where_sql} "
            "GROUP BY sf ORDER BY sf COLLATE NOCASE ASC",
            params,
        ).fetchall()
    finally:
        conn.close()

    folders = [{'subfolder': r['sf'], 'count': r['n']} for r in rows]
    total = sum(f['count'] for f in folders)
    return jsonify({'folders': folders, 'total': total})


@app.route('/api/media/tags', methods=['GET'])
def media_tags_list():
    """Liste des tags de l'utilisateur courant, avec le NOMBRE de médias.

    Alimente la MODALE de filtrage par tags de la galerie (pendant de
    ``GET /api/media/folders`` pour les dossiers). Agrégation en BASE
    (``GROUP BY tag``), jamais de parcours Python des médias. ``user_id``
    DÉRIVÉ du token (aucun paramètre client) ; corbeillés EXCLUS par défaut.

    Paramètre optionnel :
      - ``status`` : ``complete`` (déf., SANS les corbeillés) | ``trashed``
        (comptes de la corbeille) | ``all`` (tous statuts) — mêmes valeurs que
        la liste, pour rester cohérent avec la vue courante.

    Réponse TRIÉE (nom, insensible à la casse) :
      ``{"tags": [{"tag": "ciel", "count": 3}, …], "total": 3}``
    où ``count`` = nombre de MÉDIAS DISTINCTS portant le tag et ``total`` la
    somme des comptes. Le regroupement est INSENSIBLE À LA CASSE (la colonne
    est ``COLLATE NOCASE``) : les variantes de casse d'un même tag fusionnent,
    la représentation retenue étant la plus petite au sens NOCASE.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    status_clause = _status_clause(request.args)
    if status_clause is None:
        return jsonify({'error': 'filtre invalide (status)'}), 400

    where = ["t.user_id = ?"]
    params = [user_id]
    if status_clause:
        # ``_status_clause`` produit une clause sur les colonnes de media_files
        # → on la qualifie avec l'alias ``m`` de la jointure.
        where.append("m." + status_clause)
    where_sql = " AND ".join(where)

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT MIN(t.tag) AS tag, COUNT(DISTINCT t.media_id) AS n "
            "FROM media_tags t JOIN media_files m ON m.id = t.media_id "
            f"WHERE {where_sql} "
            "GROUP BY t.tag ORDER BY t.tag COLLATE NOCASE ASC",
            params,
        ).fetchall()
    finally:
        conn.close()

    tags = [{'tag': r['tag'], 'count': r['n']} for r in rows]
    total = sum(t['count'] for t in tags)
    return jsonify({'tags': tags, 'total': total})


@app.route('/api/media/<int:media_id>/tags', methods=['POST'])
def media_tags_update(media_id):
    """Ajoute/retire des tags sur UN média (propriétaire ou admin).

    Body ``{"add": [...], "remove": [...]}`` (au moins l'un des deux) :
      - ``add`` : tags à ajouter (source ``manual``) ;
      - ``remove`` : tags à retirer.

    Normalisation + unicité INSENSIBLE À LA CASSE : « Sunset » puis « sunset »
    ne créent PAS de doublon (la casse de la 1re saisie est conservée).
    ``remove`` s'applique AVANT ``add`` (un tag présent dans les deux listes
    est donc finalement AJOUTÉ). 400 si les listes sont invalides ou toutes deux
    vides, 404 si le média n'existe pas, 403 s'il appartient à autrui (hors
    admin). Retourne l'item sérialisé (``_media_json``, tags inclus).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    add = _parse_tag_list(data.get("add"))
    remove = _parse_tag_list(data.get("remove"))
    if add is None or remove is None:
        return jsonify({'error': 'tags invalides (add/remove : listes de chaînes, 1..50 caractères)'}), 400
    if not add and not remove:
        return jsonify({'error': 'add ou remove requis'}), 400

    conn = get_db()
    try:
        _remove_tags(conn, media_id, remove)
        _add_tags(conn, user_id, media_id, add)
        conn.commit()
    finally:
        conn.close()

    updated = _fetch_media(media_id)
    logging.info(f"[media] Tags {media_id} : +{len(add)} / -{len(remove)} (user={user_id})")
    return jsonify(_media_json(updated))


@app.route('/api/media/tags', methods=['POST'])
def media_tags_bulk():
    """Tags GROUPÉS (multi-select UI) : ``{ids, add, remove}`` → récap.

    Traite chaque id autorisé (propriétaire ou admin, média rangé existant) et
    IGNORE les autres SANS échouer l'appel. Retourne
    ``{updated: <n>, skipped: [<ids>]}`` (échec partiel impossible). Au moins
    l'un de ``add``/``remove`` est requis (sinon 400). Indispensable pour
    l'auto-tagging IA à venir (poser un lot de tags sur une sélection).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {MAX_BULK_IDS})"}), 400
    add = _parse_tag_list(data.get("add"))
    remove = _parse_tag_list(data.get("remove"))
    if add is None or remove is None:
        return jsonify({'error': 'tags invalides (add/remove : listes de chaînes, 1..50 caractères)'}), 400
    if not add and not remove:
        return jsonify({'error': 'add ou remove requis'}), 400

    admin = is_admin(user_id)
    updated = []
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
            _remove_tags(conn, mid, remove)
            _add_tags(conn, user_id, mid, add)
            updated.append(mid)
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[media] Tags groupés : {len(updated)} ok, {len(skipped)} ignorés")
    return jsonify({'updated': len(updated), 'skipped': skipped})


@app.route('/api/media/<int:media_id>/auto-tag', methods=['POST'])
def media_auto_tag(media_id):
    """Auto-tag IA d'UN média image via un preset « compatible vision ».

    Body ``{"preset_id": <int>}`` (optionnel : sans ``preset_id``, le premier
    preset vision visible est utilisé). L'image est RÉDUITE (~512 px JPEG) puis
    envoyée en data-URL base64 à ``{base_url}/chat/completions`` du preset ; la
    réponse est parsée en liste de tags, normalisés et écrits en ``source='ai'``
    via ``INSERT OR IGNORE`` (JAMAIS d'écrasement d'un tag manuel).

    Un seul média par appel → le front boucle sur la sélection (progression +
    annulation naturelle). Réponses :
      - 200 ``{media, auto_tag:{status:'tagged', added, ai_tags}}`` ;
      - 200 ``{media, auto_tag:{status:'skipped', reason:'not_image'|'no_tags'}}`` ;
      - 400 ``preset_id`` invalide / preset non-vision / aucun preset vision ;
      - 403 média d'autrui (hors admin), 404 média ou preset introuvable ;
      - 502 fournisseur refusé (SSRF) / injoignable / HTTP d'erreur / réponse
        illisible, ou image non préparable. La clé API n'est jamais renvoyée.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    preset_id, perr = _parse_preset_id(data.get('preset_id'))
    if perr:
        return perr

    conn = get_db()
    try:
        preset_row, preset_err = _resolve_vision_preset(conn, user_id, preset_id)
    finally:
        conn.close()
    if preset_err:
        return preset_err

    recap = _auto_tag_media(row, user_id, preset_row)

    if recap['status'] == 'tagged':
        updated = _fetch_media(media_id)
        logging.info(
            "[media] auto-tag %s : +%s tag(s) IA (preset=%s)", media_id, recap['added'], preset_row['id']
        )
        return jsonify({'media': _media_json(updated), 'auto_tag': recap})
    if recap['status'] == 'skipped':
        return jsonify({'media': _media_json(row), 'auto_tag': recap})
    return jsonify({
        'error': recap.get('detail') or 'Auto-tag impossible',
        'reason': recap['reason'],
        'auto_tag': recap,
    }), 502


@app.route('/api/media/auto-tag', methods=['POST'])
def media_auto_tag_bulk():
    """Auto-tag IA GROUPÉ et BORNÉ (≤ ``AUTO_TAG_MAX_BATCH``) → récap par média.

    Body ``{"ids": [...], "preset_id": <int>?}``. Chaque id est auto-taggé
    indépendamment (même logique que la variante unitaire) ; un id inaccessible
    est « ignoré » sans faire échouer l'appel. Retourne
    ``{results:[{id,status,added,ai_tags,reason?}], tagged, skipped, errors}``.
    Au-delà de la borne, 400 ``batch_too_large`` (jamais d'appel LLM long).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {AUTO_TAG_MAX_BATCH})"}), 400
    if len(ids) > AUTO_TAG_MAX_BATCH:
        return jsonify({
            'error': f"auto-tag groupé limité à {AUTO_TAG_MAX_BATCH} médias par requête",
            'reason': 'batch_too_large',
        }), 400
    preset_id, perr = _parse_preset_id(data.get('preset_id'))
    if perr:
        return perr

    conn = get_db()
    try:
        preset_row, preset_err = _resolve_vision_preset(conn, user_id, preset_id)
    finally:
        conn.close()
    if preset_err:
        return preset_err

    results = []
    tagged = skipped = errors = 0
    for mid in ids:
        row = _fetch_media(mid)
        if _media_owner_guard(row, user_id):
            results.append({'id': mid, 'status': 'skipped', 'reason': 'not_accessible'})
            skipped += 1
            continue
        recap = _auto_tag_media(row, user_id, preset_row)
        results.append({'id': mid, **recap})
        if recap['status'] == 'tagged':
            tagged += 1
        elif recap['status'] == 'skipped':
            skipped += 1
        else:
            errors += 1

    logging.info(
        "[media] auto-tag groupé : %s taggés, %s ignorés, %s erreurs", tagged, skipped, errors
    )
    return jsonify({'results': results, 'tagged': tagged, 'skipped': skipped, 'errors': errors})


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

    Cache serveur : la vignette est générée UNE fois puis écrite dans un cache
    LOCAL persistant (``AIH_THUMB_CACHE_DIR`` ou ``<BASE_DIR>/.cache/thumbnails``)
    sous ``<user>/<sha1>_<size>.jpg`` ; les appels suivants la servent depuis ce
    fichier local, SANS aucun accès au storage (aucun aller-retour SFTP). Les
    MÉDIAS sources, eux, restent sur le storage.

    ``?size=`` borné à {128, 256, 512} (défaut 256) — toute valeur numérique
    est snappée à la plus proche ; non numérique → 400. Le cache est indexé par
    couple (média, taille), donc « la sélection de la taille des miniatures »
    côté UI ne régénère rien pour les tailles déjà demandées.

    Dégradation : si aucun outil (Pillow/ffmpeg) n'est disponible, ou pour un
    média audio, la route renvoie 404 avec ``code: "thumbnail_unavailable"``
    (l'UI affiche un état d'erreur distinct du placeholder) — jamais de crash.
    Le champ ``reason`` précise la cause (``no_tools`` / ``source_unavailable``
    / ``generation_failed``) pour un diagnostic immédiat. Pillow est une
    dépendance RUNTIME déclarée (``requirements.txt``) : son absence est la
    cause n°1 d'un ``reason: no_tools`` en production.

    Pas de CACHE NÉGATIF : une génération en échec n'écrit RIEN (ni fichier de
    cache storage, ni marqueur, ni colonne) ; ``thumb_available`` est recalculé
    à chaque appel. Les réponses d'ERREUR portent ``Cache-Control: no-store``
    pour qu'aucun cache (navigateur/intermédiaire — un 404 est « heuristiquement
    cacheable ») ne fige l'échec : après correction (ex. Pillow installé) un
    simple appel régénère la vignette.

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
        # 403/404 d'accès : jamais mis en cache durablement non plus.
        err_resp, err_status = err
        err_resp.headers['Cache-Control'] = THUMB_ERROR_CACHE_CONTROL
        return err_resp, err_status

    size = _normalize_thumb_size(request.args.get('size'))
    if size is None:
        resp = jsonify({'error': 'size invalide (128/256/512)'})
        resp.status_code = 400
        resp.headers['Cache-Control'] = THUMB_ERROR_CACHE_CONTROL
        return resp

    etag = _thumbnail_etag(row, size)
    etag_header = f'"{etag}"'

    # Revalidation navigateur : 304 sans aucun accès au storage.
    if request.headers.get('If-None-Match', '') in (etag_header, '*'):
        resp = Response(status=304)
        resp.headers['ETag'] = etag_header
        resp.headers['Cache-Control'] = THUMB_CACHE_CONTROL
        return resp

    thumb_path = _thumbnail_cache_path(row, size)
    local_thumb, reason = _ensure_thumbnail_file(row, size, thumb_path)
    if not local_thumb:
        # Diagnostic explicite : l'UI/front peut distinguer « pas d'outil »
        # (dépendance manquante au déploiement) d'une source illisible.
        logging.warning(
            "[media] thumbnail 404 id=%s kind=%s size=%s reason=%s",
            media_id, row['kind'], size, reason,
        )
        # Aucune mise en cache (même heuristique) : la vignette DOIT pouvoir
        # être régénérée au prochain appel dès que la cause est levée.
        resp = jsonify({
            'error': 'Vignette indisponible pour ce média',
            'code': 'thumbnail_unavailable',
            'reason': reason,
            'kind': row['kind'],
        })
        resp.status_code = 404
        resp.headers['Cache-Control'] = THUMB_ERROR_CACHE_CONTROL
        return resp

    response = send_file(
        local_thumb,
        mimetype='image/jpeg',
        conditional=True,
        etag=etag,
        max_age=THUMB_MAX_AGE,
    )
    response.headers['Cache-Control'] = THUMB_CACHE_CONTROL
    return response


@app.route('/api/media/<int:media_id>/metadata', methods=['GET'])
def media_metadata(media_id):
    """Métadonnées détaillées d'un média (propriétaire ou admin).

    Retourne ``id, filename, subfolder, kind, ext, size, created_at, width,
    height, ratio, duration, duration_ms, codec, prompt, workflow, has_prompt,
    has_workflow``. ``width/height/ratio`` viennent de Pillow (images),
    ``duration/duration_ms/codec`` (+ width/height) de ffprobe (vidéo/audio) si
    présent, sinon ``null`` (dégradation propre).

    Backfill paresseux : pour un média ancien, on relit le fichier UNE fois, on
    persiste le résultat, puis on ne recalcule plus (compat arrière : l'image
    uploadée par l'utilisateur avant cette migration est servie). Sont couvertes
    les lignes ``meta_checked=0`` (colonnes jamais renseignées) ET les lignes
    « tentative faite sans outil » (``meta_checked=1`` + technique entièrement
    NULL : médias uploadés pendant l'absence de Pillow en prod) — ces dernières
    sont réparées dès qu'un outil adapté est disponible (cf.
    ``_needs_technical_backfill``).

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

    # Backfill paresseux : lignes jamais renseignées (migration) OU lignes
    # « tentative sans outil » des médias uploadés avant l'installation de
    # Pillow/ffprobe (réparation dès que l'outil est disponible).
    if _needs_technical_backfill(row) and _backfill_technical_metadata(row):
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


@app.route('/api/media/<int:media_id>/favorite', methods=['POST'])
def media_favorite(media_id):
    """Bascule le FAVORI d'un média (propriétaire ou admin).

    Body ``{"favorite": true|false}``. Le flag est un booléen simple (0/1 en
    base) ; il est exposé par ``_media_json`` (liste) et ``_metadata_json``.
    404 si le média n'existe pas (ou n'est pas rangé), 403 s'il appartient à
    autrui (hors admin), 400 si ``favorite`` n'est pas un booléen.
    Idempotent : rejouer la même valeur ne change rien.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    row = _fetch_media(media_id)
    err = _media_owner_guard(row, user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    favorite = _parse_favorite(data.get('favorite'))
    if favorite is None:
        return jsonify({'error': 'favorite doit être un booléen'}), 400

    updated = _set_favorite(media_id, favorite)
    logging.info(f"[media] Favori {media_id} = {favorite} (user={user_id})")
    return jsonify(_media_json(updated))


@app.route('/api/media/favorite', methods=['POST'])
def media_favorite_bulk():
    """Favori GROUPÉ (multi-select UI) : ``{ids: [...], favorite: bool}``.

    Traite chaque id autorisé (propriétaire ou admin, média rangé existant) et
    IGNORE les autres SANS échouer l'appel. Retourne un récap
    ``{updated: <n>, skipped: [<ids>]}`` (échec partiel impossible).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    data = request.get_json(silent=True) or {}
    ids = _parse_bulk_ids(data)
    if ids is None:
        return jsonify({'error': f"ids doit être une liste d'entiers (max {MAX_BULK_IDS})"}), 400
    favorite = _parse_favorite(data.get('favorite'))
    if favorite is None:
        return jsonify({'error': 'favorite doit être un booléen'}), 400

    admin = is_admin(user_id)
    updated = []
    skipped = []
    conn = get_db()
    try:
        value = 1 if favorite else 0
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
                "UPDATE media_files SET favorite = ? WHERE id = ?",
                (value, mid),
            )
            updated.append(mid)
        conn.commit()
    finally:
        conn.close()

    logging.info(f"[media] Favori groupé ({favorite}) : {len(updated)} ok, {len(skipped)} ignorés")
    return jsonify({'updated': len(updated), 'skipped': skipped})


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
