"""Routes Albums publics — gestion PRIVÉE (phase 1 du chantier « albums »).

Cette phase ne sert QUE le propriétaire authentifié ; le service public (lecture
seule du dossier d'album via un sous-domaine dédié, hors Authentik) est la
phase 2, et le front la phase 3. Ce module :

  - POST   /api/albums                 → crée l'album (snapshot figé) + worker async ;
  - GET    /api/albums                 → liste + compteurs + statut/avancement ;
  - GET    /api/albums/<id>            → détail + items (rapport par item) ;
  - PATCH  /api/albums/<id>            → titre/description (DB + manifest) ;
  - POST   /api/albums/<id>/items      → ajoute une sélection (relance le worker) ;
  - POST   /api/albums/<id>/revoke     → renomme le dossier PUIS passe en revoked ;
  - DELETE /api/albums/<id>            → supprime lignes + dossier ;
  - GET    /api/albums/for-media/<id>  → « figure dans N albums » (avert. purge).

Chaque album sérialisé expose ``public_url`` (``<base>/a/<key>``) construit depuis
la variable d'environnement ``AIH_ALBUM_PUBLIC_BASE_URL`` (posée par l'infra en
phase 5) ; si elle est absente, ``public_url: null`` et le front affiche la clé.

INSTANTANÉ FIGÉ : la liste des images est enregistrée à la création (ou lors
d'un ajout EXPLICITE). Une image uploadée plus tard n'entre jamais automatiquement.

CRÉATION ASYNCHRONE : la requête HTTP rend la main IMMÉDIATEMENT (statut
``building``) ; un thread d'arrière-plan génère les fichiers, met à jour la
progression en base puis passe en ``ready`` (ou ``error``). Le client suit
l'avancement par polling du GET détail.

GARDE-FOU (phase 5) : ``AIH_ALBUM_GUARD`` ∈ off|log|on (défaut off) est branché
DANS LE SERVICE PUBLIC (``backend/public_app.py``) — c'est la surface exposée
qui est protégée. Ce module privé n'en a pas besoin (authentification).
"""

import contextlib
import glob
import logging
import os
import secrets
import shutil
import threading

import album_web
from album_web import (
    _now_iso,
    album_dir,
    generate_album_item,
    write_manifest,
)
from context import *  # noqa: F401,F403

logger = logging.getLogger("ai_helper")

# Bornes de validation (anti-DoS par payload / texte démesuré).
MAX_ALBUM_IDS = 500
TITLE_MAX_LEN = 200
DESCRIPTION_MAX_LEN = 2000

# Statuts d'un album (colonne ``albums.status``).
ALBUM_STATUSES = ("building", "ready", "error", "revoked")

# Phase 5 : garde-fou de débit — il vit dans le SERVICE PUBLIC
# (``backend/public_app.py``, ``AIH_ALBUM_GUARD`` ∈ off|log|on, défaut off) ;
# le privé n'en a pas besoin (accès derrière Authentik). Constante conservée
# pour référence/documentation du chantier.
ALBUM_GUARD = os.environ.get("AIH_ALBUM_GUARD", "off")

# Base publique des albums (phase 5 : sous-domaine Caddy). Lue à CHAQUE
# sérialisation (monkeypatch/env en test) ; absente → ``public_url = null``.
ALBUM_PUBLIC_BASE_URL_ENV = "AIH_ALBUM_PUBLIC_BASE_URL"


def _album_public_base():
    """Base publique sans '/' final ('' si ``AIH_ALBUM_PUBLIC_BASE_URL`` absente)."""
    return (os.environ.get(ALBUM_PUBLIC_BASE_URL_ENV) or "").strip().rstrip("/")


def _album_public_url(key):
    """URL publique ``<base>/a/<key>`` — ``None`` si la base n'est pas configurée."""
    base = _album_public_base()
    if not base or not key:
        return None
    return f"{base}/a/{key}"


# ── Helpers de sérialisation ──────────────────────────────────────────


def _empty_counts():
    return {"total": 0, "ok": 0, "failed": 0, "pending": 0}


def _album_json(album, counts=None, items=None):
    """Sérialise une ligne ``albums`` pour l'API privée (contrat stable)."""
    body = {
        "id": album["id"],
        "key": album["key"],
        "public_url": _album_public_url(album["key"]),
        "title": album["title"] or "",
        "description": album["description"] or "",
        "status": album["status"],
        "progress": {
            "total": album["progress_total"] or 0,
            "done": album["progress_done"] or 0,
        },
        "created_at": album["created_at"] or "",
        "updated_at": album["updated_at"] or "",
        "revoked_at": album["revoked_at"] or "",
    }
    if counts is not None:
        body["counts"] = counts
    if items is not None:
        body["items"] = items
    return body


def _item_json(row):
    """Sérialise une ligne ``album_media`` (rapport par item)."""
    return {
        "id": row["id"],
        "media_id": row["media_id"],
        "item_no": row["item_no"],
        "ext": row["ext"] or "",
        "width": row["width"],
        "height": row["height"],
        "status": row["status"],
        "error": row["error"] or "",
        "added_at": row["added_at"] or "",
    }


def _counts_dict(rows):
    """Compteurs dérivés d'une liste de lignes ``album_media``."""
    counts = _empty_counts()
    counts["total"] = len(rows)
    for row in rows:
        if row["status"] == "ok":
            counts["ok"] += 1
        elif row["status"] == "failed":
            counts["failed"] += 1
        elif row["status"] == "pending":
            counts["pending"] += 1
    return counts


# ── Helpers data / DB ─────────────────────────────────────────────────


def _clean_text(value, max_len):
    """Nettoie un champ texte optionnel → ``(valeur, erreur)``.

    ``None`` → chaîne vide (champ absent / effacement). Un type non-str ou un
    texte trop long → ``(None, "raison")`` (l'appelant renvoie 400).
    """
    if value is None:
        return "", None
    if not isinstance(value, str):
        return None, "type invalide"
    cleaned = value.strip()
    if len(cleaned) > max_len:
        return None, "trop long"
    return cleaned, None


def _parse_album_ids(data):
    """Valide ``{ids: [...]}`` → liste d'entiers uniques (ou ``None`` si invalide).

    Les entrées non entières sont ignorées ; le dédoublonnage préserve l'ordre.
    Une valeur trop volumineuse (> ``MAX_ALBUM_IDS``) est refusée.
    """
    if not isinstance(data, dict):
        return None
    ids = data.get("ids")
    if ids is None:
        return []
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
    if len(out) > MAX_ALBUM_IDS:
        return None
    return out


def _new_album_key():
    """Génère une clé publique opaque UNIQUE (``secrets.token_urlsafe(32)``)."""
    conn = get_db()
    try:
        for _ in range(10):
            key = secrets.token_urlsafe(32)
            if not conn.execute("SELECT 1 FROM albums WHERE key = ?", (key,)).fetchone():
                return key
    finally:
        conn.close()
    raise RuntimeError("impossible de générer une clé d'album unique")


def _fetch_album(album_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()


def _fetch_media_row(media_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)).fetchone()
    finally:
        conn.close()


def _album_items(album_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM album_media WHERE album_id = ? ORDER BY item_no", (album_id,)).fetchall()
    finally:
        conn.close()


def _media_counts_by_album(album_ids):
    """Compteurs par album en UNE requête (pas de N+1 sur la liste)."""
    if not album_ids:
        return {}
    placeholders = ",".join("?" for _ in album_ids)
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT album_id, COUNT(*) AS total, "
            f"SUM(status = 'ok') AS ok, SUM(status = 'failed') AS failed, "
            f"SUM(status = 'pending') AS pending "
            f"FROM album_media WHERE album_id IN ({placeholders}) GROUP BY album_id",
            list(album_ids),
        ).fetchall()
    finally:
        conn.close()
    return {
        r["album_id"]: {
            "total": r["total"] or 0,
            "ok": r["ok"] or 0,
            "failed": r["failed"] or 0,
            "pending": r["pending"] or 0,
        }
        for r in rows
    }


def _album_owner_guard(album, user_id):
    """Autorise l'opération sur un album (propriétaire ou admin)."""
    if not album:
        return jsonify({"error": "Album introuvable"}), 404
    if album["user_id"] != user_id and not is_admin(user_id):
        return jsonify({"error": "Accès refusé"}), 403
    return None


def _classify_ids(ids, owner_id):
    """Valide une sélection d'ids média EN UNE requête → ``(ok, skipped)``.

    Un id est accepté s'il existe, appartient à ``owner_id``, est ``complete``
    et de type ``image``. Tout le reste est renvoyé dans ``skipped`` avec une
    raison stable (``not_found`` / ``not_owned`` / ``trashed`` /
    ``unsupported_kind``).
    """
    if not ids:
        return [], []
    placeholders = ",".join("?" for _ in ids)
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT id, user_id, status, kind, final_path FROM media_files WHERE id IN ({placeholders})",
            list(ids),
        ).fetchall()
    finally:
        conn.close()
    by_id = {r["id"]: r for r in rows}
    ok = []
    skipped = []
    for mid in ids:
        row = by_id.get(mid)
        if row is None:
            skipped.append({"id": mid, "reason": "not_found"})
        elif row["user_id"] != owner_id:
            skipped.append({"id": mid, "reason": "not_owned"})
        elif row["status"] == "trashed":
            skipped.append({"id": mid, "reason": "trashed"})
        elif row["status"] != "complete" or not row["final_path"]:
            skipped.append({"id": mid, "reason": "not_found"})
        elif row["kind"] != "image":
            skipped.append({"id": mid, "reason": "unsupported_kind"})
        else:
            ok.append(mid)
    return ok, skipped


# ── Worker asynchrone de préparation ──────────────────────────────────

# Un lock par album sérialise les workers (un ajout d'items pendant une
# préparation ne laisse jamais un item « pending » orphelin).
_worker_locks = {}
_worker_locks_guard = threading.Lock()


def _album_lock(album_id):
    with _worker_locks_guard:
        lock = _worker_locks.get(album_id)
        if lock is None:
            lock = threading.Lock()
            _worker_locks[album_id] = lock
        return lock


def _start_album_worker(album_id):
    """Lance la préparation d'un album en arrière-plan (jamais bloquant)."""
    threading.Thread(target=_run_album_worker, args=(album_id,), daemon=True, name=f"aih-album-{album_id}").start()


def _run_album_worker(album_id):
    lock = _album_lock(album_id)
    lock.acquire()
    try:
        _prepare_album(album_id)
    except Exception:
        logger.exception("[album] préparation échouée album=%s", album_id)
        _set_album_status(album_id, "error")
    finally:
        lock.release()


def _set_album_status(album_id, status):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE albums SET status = ?, updated_at = ? WHERE id = ? AND status != 'revoked'",
            (status, _now_iso(), album_id),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_item_report(link_id, report):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE album_media SET status = ?, error = ?, ext = ?, width = ?, height = ? WHERE id = ?",
            (report["status"], report["error"], report["ext"], report["width"], report["height"], link_id),
        )
        conn.commit()
    finally:
        conn.close()


def _bump_progress(album_id):
    """Recalcule ``progress_done`` = items non ``pending`` (robuste au re-run)."""
    conn = get_db()
    try:
        done = conn.execute(
            "SELECT COUNT(*) FROM album_media WHERE album_id = ? AND status != 'pending'",
            (album_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE albums SET progress_done = ?, updated_at = ? WHERE id = ?",
            (done, _now_iso(), album_id),
        )
        conn.commit()
    finally:
        conn.close()


def _prepare_album(album_id):
    """Traite tous les items ``pending`` puis finalise (manifest + statut)."""
    album = _fetch_album(album_id)
    if not album or album["status"] == "revoked":
        return
    try:
        base = album_dir(album["key"])
    except ValueError:
        logger.error("[album] clé invalide album=%s", album_id)
        _set_album_status(album_id, "error")
        return
    os.makedirs(os.path.join(base, album_web.THUMB_SUBDIR), exist_ok=True)
    os.makedirs(os.path.join(base, album_web.FULL_SUBDIR), exist_ok=True)

    owner = album["user_id"]
    while True:
        conn = get_db()
        try:
            item = conn.execute(
                "SELECT * FROM album_media WHERE album_id = ? AND status = 'pending' ORDER BY item_no LIMIT 1",
                (album_id,),
            ).fetchone()
        finally:
            conn.close()
        if item is None:
            break
        media = _fetch_media_row(item["media_id"])
        report = generate_album_item(media, item["item_no"], base, owner)
        _persist_item_report(item["id"], report)
        _bump_progress(album_id)

    _finalize_album(album_id)


def _finalize_album(album_id):
    """Écrit le manifest final (items ``ok``) et passe l'album en ``ready``."""
    conn = get_db()
    try:
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        rows = conn.execute(
            "SELECT * FROM album_media WHERE album_id = ? AND status = 'ok' ORDER BY item_no",
            (album_id,),
        ).fetchall()
    finally:
        conn.close()
    if not album or album["status"] == "revoked":
        return
    write_manifest(album["key"], album["title"], album["description"], rows)
    _set_album_status(album_id, "ready")


def _rewrite_manifest_if_present(album):
    """Réécrit le manifest si le dossier de l'album existe (PATCH titre/desc)."""
    try:
        if not os.path.isdir(album_dir(album["key"])):
            return
    except ValueError:
        return
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM album_media WHERE album_id = ? AND status = 'ok' ORDER BY item_no",
            (album["id"],),
        ).fetchall()
    finally:
        conn.close()
    write_manifest(album["key"], album["title"], album["description"], rows)


def _remove_album_folders(key):
    """Supprime le dossier de l'album et toute variante ``.revoked*``."""
    try:
        base = album_dir(key)
    except ValueError:
        return
    for path in [base, *glob.glob(base + ".revoked*")]:
        shutil.rmtree(path, ignore_errors=True)


# ── Routes ────────────────────────────────────────────────────────────


@app.route("/api/albums", methods=["POST"])
def albums_create():
    """Crée un album (snapshot figé) et lance sa préparation en arrière-plan."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    data = request.get_json(silent=True) or {}

    ids = _parse_album_ids(data)
    if ids is None:
        return jsonify({"error": f"ids doit être une liste d'entiers (max {MAX_ALBUM_IDS})"}), 400
    title, err = _clean_text(data.get("title"), TITLE_MAX_LEN)
    if err:
        return jsonify({"error": f"titre invalide ({err})"}), 400
    description, err = _clean_text(data.get("description"), DESCRIPTION_MAX_LEN)
    if err:
        return jsonify({"error": f"description invalide ({err})"}), 400

    ok_ids, skipped = _classify_ids(ids, user_id)
    key = _new_album_key()
    now = _now_iso()

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO albums (user_id, key, title, description, status, "
            "progress_total, progress_done, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'building', ?, 0, ?, ?)",
            (user_id, key, title, description, len(ok_ids), now, now),
        )
        album_id = cur.lastrowid
        for item_no, mid in enumerate(ok_ids, start=1):
            conn.execute(
                "INSERT INTO album_media (album_id, media_id, item_no, status, added_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (album_id, mid, item_no, now),
            )
        conn.commit()
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()

    _start_album_worker(album_id)
    logger.info("[album] créé id=%s (items=%d, skipped=%d)", album_id, len(ok_ids), len(skipped))
    return jsonify({"album": _album_json(album), "skipped": skipped}), 201


@app.route("/api/albums", methods=["GET"])
def albums_list():
    """Liste les albums du propriétaire courant + compteurs + avancement."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM albums WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        counts = _media_counts_by_album([r["id"] for r in rows])
    finally:
        conn.close()

    items = [_album_json(r, counts=counts.get(r["id"], _empty_counts())) for r in rows]
    return jsonify({"items": items, "total": len(items)})


@app.route("/api/albums/for-media/<int:media_id>", methods=["GET"])
def albums_for_media(media_id):
    """« Figure dans N albums » — pour l'avertissement avant purge définitive.

    Ne compte QUE les albums non révoqués du propriétaire courant.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT a.* FROM albums a JOIN album_media am ON am.album_id = a.id "
            "WHERE am.media_id = ? AND a.user_id = ? AND a.status != 'revoked' "
            "ORDER BY a.id DESC",
            (media_id, user_id),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"count": len(rows), "albums": [_album_json(r) for r in rows]})


@app.route("/api/albums/<int:album_id>", methods=["GET"])
def albums_detail(album_id):
    """Détail d'un album + items (rapport par item) — sert aussi au polling."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    album = _fetch_album(album_id)
    err = _album_owner_guard(album, user_id)
    if err:
        return err

    rows = _album_items(album_id)
    return jsonify(_album_json(album, counts=_counts_dict(rows), items=[_item_json(r) for r in rows]))


@app.route("/api/albums/<int:album_id>", methods=["PATCH"])
def albums_update(album_id):
    """Met à jour titre/description (DB puis manifest si le dossier existe)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    album = _fetch_album(album_id)
    err = _album_owner_guard(album, user_id)
    if err:
        return err
    if album["status"] == "revoked":
        return jsonify({"error": "Album révoqué"}), 409

    data = request.get_json(silent=True) or {}
    title = album["title"] or ""
    description = album["description"] or ""
    if "title" in data:
        title, cerr = _clean_text(data.get("title"), TITLE_MAX_LEN)
        if cerr:
            return jsonify({"error": f"titre invalide ({cerr})"}), 400
    if "description" in data:
        description, cerr = _clean_text(data.get("description"), DESCRIPTION_MAX_LEN)
        if cerr:
            return jsonify({"error": f"description invalide ({cerr})"}), 400

    conn = get_db()
    try:
        conn.execute(
            "UPDATE albums SET title = ?, description = ?, updated_at = ? WHERE id = ?",
            (title, description, _now_iso(), album_id),
        )
        conn.commit()
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()

    if album["status"] == "ready":
        _rewrite_manifest_if_present(album)
    return jsonify(_album_json(album))


@app.route("/api/albums/<int:album_id>/items", methods=["POST"])
def albums_add_items(album_id):
    """Ajoute une sélection à un album existant puis relance le worker."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    album = _fetch_album(album_id)
    err = _album_owner_guard(album, user_id)
    if err:
        return err
    if album["status"] == "revoked":
        return jsonify({"error": "Album révoqué"}), 409

    data = request.get_json(silent=True) or {}
    ids = _parse_album_ids(data)
    if ids is None:
        return jsonify({"error": f"ids doit être une liste d'entiers (max {MAX_ALBUM_IDS})"}), 400

    ok_ids, skipped = _classify_ids(ids, album["user_id"])
    now = _now_iso()
    added = 0
    conn = get_db()
    try:
        existing = {
            r["media_id"]
            for r in conn.execute("SELECT media_id FROM album_media WHERE album_id = ?", (album_id,)).fetchall()
        }
        next_no = conn.execute(
            "SELECT COALESCE(MAX(item_no), 0) FROM album_media WHERE album_id = ?", (album_id,)
        ).fetchone()[0]
        for mid in ok_ids:
            if mid in existing:
                skipped.append({"id": mid, "reason": "duplicate"})
                continue
            existing.add(mid)
            next_no += 1
            conn.execute(
                "INSERT INTO album_media (album_id, media_id, item_no, status, added_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (album_id, mid, next_no, now),
            )
            added += 1
        if added:
            conn.execute(
                "UPDATE albums SET progress_total = progress_total + ?, status = 'building', "
                "updated_at = ? WHERE id = ?",
                (added, now, album_id),
            )
        conn.commit()
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()

    if added:
        _start_album_worker(album_id)
    rows = _album_items(album_id)
    logger.info("[album] ajout id=%s (+%d, skipped=%d)", album_id, added, len(skipped))
    return jsonify(
        {
            "album": _album_json(album, counts=_counts_dict(rows), items=[_item_json(r) for r in rows]),
            "added": added,
            "skipped": skipped,
        }
    )


@app.route("/api/albums/<int:album_id>/revoke", methods=["POST"])
def albums_revoke(album_id):
    """Révoque un album : RENOMME le dossier d'abord, puis passe en ``revoked``."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    album = _fetch_album(album_id)
    err = _album_owner_guard(album, user_id)
    if err:
        return err
    if album["status"] == "revoked":
        return jsonify(_album_json(album))

    try:
        src = album_dir(album["key"])
    except ValueError:
        return jsonify({"error": "Album introuvable"}), 404

    if os.path.isdir(src):
        dst = src + ".revoked"
        if os.path.exists(dst):
            dst = f"{src}.revoked-{secrets.token_hex(4)}"
        try:
            os.replace(src, dst)
        except OSError as e:
            logger.error("[album] révocation : renommage impossible album=%s : %s", album_id, e)
            return jsonify({"error": "Révocation impossible"}), 500

    now = _now_iso()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE albums SET status = 'revoked', revoked_at = ?, updated_at = ? WHERE id = ?",
            (now, now, album_id),
        )
        conn.commit()
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()
    logger.info("[album] révoqué id=%s", album_id)
    return jsonify(_album_json(album))


@app.route("/api/albums/<int:album_id>", methods=["DELETE"])
def albums_delete(album_id):
    """Supprime un album : dossier(s) d'abord, puis lignes (CASCADE items)."""
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    album = _fetch_album(album_id)
    err = _album_owner_guard(album, user_id)
    if err:
        return err

    with contextlib.suppress(Exception):
        _remove_album_folders(album["key"])

    conn = get_db()
    try:
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info("[album] supprimé id=%s", album_id)
    return jsonify({"deleted": True, "id": album_id})
