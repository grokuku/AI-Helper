"""Tests des ALBUMS PUBLICS — phase 1 (backend privé, ``routes/albums.py``).

Couvre : clé opaque, création ASYNCHRONE + progression, snapshot FIGÉ, skips
(not_found / not_owned / trashed / unsupported_kind), ré-ajout idempotent,
403/404, révocation (dossier renommé), delete, manifest SANS données privées,
EXIF purgés, PNG transparent conservé en PNG, traversal/noms invalides rejetés,
migration idempotente et propagation de la purge.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_album_cross_user_forbidden`` : sans la garde, on obtiendrait 200 ;
  - ``test_manifest_has_no_private_fields`` : toute fuite (media_id, user_id,
    chemin, nom d'origine) ferait échouer l'assertion ;
  - ``test_revoke_renames_folder_first`` : sans le renommage, le dossier
    resterait accessible sous sa clé publique.
"""

import io
import json
import os
import re
import threading
import time

import pytest
import routes.media as media_module
import storage as storage_module
from PIL import Image
from storage import LocalStorage

ALBUM_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


# ── Helpers ────────────────────────────────────────────────────────────


def _ensure_user(user_id="test-user-123", role="user"):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", role),
    )
    conn.commit()
    conn.close()


def _headers(make_token, user_id="test-user-123", role="user"):
    _ensure_user(user_id, role)
    return {"Authorization": f"Bearer {make_token(user_id, role=role)}"}


def _png_bytes(width=320, height=200, color=(120, 30, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def _png_alpha_bytes(width=40, height=30, *, opaque=False):
    """PNG RGBA : transparent (alpha min = 0) ou entièrement opaque."""
    img = Image.new("RGBA", (width, height), (255, 0, 0, 255 if opaque else 0))
    if not opaque:
        img.putpixel((0, 0), (0, 255, 0, 255))  # un pixel opaque mais alpha min = 0
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _jpeg_with_exif(width=64, height=48):
    img = Image.new("RGB", (width, height), (10, 20, 30))
    exif = img.getexif()
    exif[271] = "AIH-Test-Make"  # Make
    exif[272] = "AIH-Test-Model"  # Model
    exif[306] = "2020:01:01 00:00:00"  # DateTime
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _upload(client, headers, content, *, kind="image", ext=".png", filename="clip", subfolder="2025-01-01"):
    """Init + 1 chunk + complete ; retourne l'id du média créé."""
    r = client.post(
        "/api/media/init",
        json={"kind": kind, "ext": ext, "size": len(content), "filename": filename, "subfolder": subfolder},
        headers=headers,
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    chunk_size = data["chunk_size"]
    for i in range(data["total_chunks"]):
        chunk = content[i * chunk_size : (i + 1) * chunk_size]
        rc = client.post(
            "/api/media/chunk",
            data={
                "upload_id": data["upload_id"],
                "chunk_index": str(i),
                "data": (io.BytesIO(chunk), f"{filename}{ext}"),
            },
            headers=headers,
        )
        assert rc.status_code == 200, rc.get_data(as_text=True)
    r = client.post("/api/media/complete", json={"upload_id": data["upload_id"]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["id"]


def _wait_album(client, headers, album_id, timeout=10.0):
    """Poll le détail jusqu'à sortir de ``building`` (ou timeout)."""
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        r = client.get(f"/api/albums/{album_id}", headers=headers)
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        if body["status"] != "building":
            return body
        time.sleep(0.02)
    return body


def _create_album(client, headers, ids, **kwargs):
    payload = {"ids": ids, **kwargs}
    r = client.post("/api/albums", json=payload, headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


@pytest.fixture()
def album_env(tmp_path, monkeypatch):
    """Isole storage local + cache vignettes + webroot album (aucune écriture repo)."""
    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    monkeypatch.setenv("AIH_THUMB_CACHE_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("AIH_ALBUM_WEB_DIR", str(tmp_path / "albums"))
    yield {"storage": st, "albums": tmp_path / "albums", "thumbs": tmp_path / "thumbs"}


def _drain_album_workers(timeout=15.0):
    """Attend la fin des workers d'album en cours (évite toute fuite de verrou)."""
    import routes.albums as albums_module

    with albums_module._worker_locks_guard:
        locks = list(albums_module._worker_locks.values())
    deadline = time.time() + timeout
    for lock in locks:
        remaining = max(0.0, deadline - time.time())
        if lock.acquire(timeout=remaining):
            lock.release()


@pytest.fixture(autouse=True)
def _album_isolation():
    """Nettoie albums/médias après CHAQUE test du module.

    La base de test est partagée (fixture ``app`` session) : sans ce nettoyage,
    les lignes ``albums``/``media_files`` (qui référencent ``users``) feraient
    échouer les tests ultérieurs qui vident la table ``users`` (FK).
    """
    yield
    _drain_album_workers()
    from routes.helpers import get_db

    conn = get_db()
    try:
        conn.execute("DELETE FROM album_media")
        conn.execute("DELETE FROM albums")
        conn.execute("DELETE FROM media_tags")
        conn.execute("DELETE FROM media_files")
        conn.commit()
    finally:
        conn.close()


# ── 1. Auth (contrôles NÉGATIFS) ──────────────────────────────────────


def test_album_routes_require_auth(client):
    assert client.post("/api/albums", json={"ids": []}).status_code == 401
    assert client.get("/api/albums").status_code == 401
    assert client.get("/api/albums/1").status_code == 401
    assert client.patch("/api/albums/1", json={"title": "x"}).status_code == 401
    assert client.post("/api/albums/1/items", json={"ids": []}).status_code == 401
    assert client.post("/api/albums/1/revoke").status_code == 401
    assert client.delete("/api/albums/1").status_code == 401
    assert client.get("/api/albums/for-media/1").status_code == 401


# ── 2. Clé opaque ─────────────────────────────────────────────────────


def test_album_key_opaque_and_unique(client, make_token, album_env):
    headers = _headers(make_token, "key-user")
    mid = _upload(client, headers, _png_bytes(), filename="k")
    first = _create_album(client, headers, [mid])["album"]["key"]
    second = _create_album(client, headers, [mid])["album"]["key"]

    assert ALBUM_KEY_RE.match(first), first
    assert ALBUM_KEY_RE.match(second)
    assert first != second
    # Aucun id séquentiel / libellé privé dans la clé.
    assert not first.isdigit()


# ── 3. Création ASYNCHRONE + progression ──────────────────────────────


def test_create_is_non_blocking_and_progresses(client, make_token, album_env, monkeypatch):
    """``POST`` rend la main pendant que le worker tourne (jamais bloquant)."""
    import routes.albums as albums_module

    headers = _headers(make_token, "async-user")
    mid = _upload(client, headers, _png_bytes(), filename="async")

    started = threading.Event()
    release = threading.Event()

    def slow_prepare(album_id):
        started.set()
        release.wait(5)

    monkeypatch.setattr(albums_module, "_prepare_album", slow_prepare)

    t0 = time.time()
    body = _create_album(client, headers, [mid])
    elapsed = time.time() - t0
    assert elapsed < 1.0  # la requête n'attend PAS le worker
    assert body["album"]["status"] == "building"
    assert body["album"]["progress"]["total"] == 1
    assert started.wait(5)  # le worker a bien démarré en arrière-plan
    release.set()


def test_album_reaches_ready_with_generated_files(client, make_token, album_env):
    headers = _headers(make_token, "ready-user")
    mid = _upload(client, headers, _png_bytes(640, 480), filename="ready")
    created = _create_album(client, headers, [mid])["album"]
    album_id = created["id"]
    assert created["status"] == "building"

    detail = _wait_album(client, headers, album_id)
    assert detail["status"] == "ready"
    assert detail["progress"] == {"total": 1, "done": 1}
    assert detail["counts"] == {"total": 1, "ok": 1, "failed": 0, "pending": 0}
    item = detail["items"][0]
    assert item["status"] == "ok"
    assert item["media_id"] == mid

    base = album_env["albums"] / detail["key"]
    assert (base / "manifest.json").is_file()
    assert (base / "thumb" / "0001.jpg").is_file()
    assert (base / "full" / f"0001{item['ext']}").is_file()
    # La vignette est un vrai JPEG de taille <= 512.
    with Image.open(base / "thumb" / "0001.jpg") as thumb:
        assert thumb.format == "JPEG"
        assert max(thumb.size) <= 512


def test_thumb_copied_from_existing_cache(client, make_token, album_env):
    """La vignette 512 DÉJÀ en cache est copiée telle quelle dans l'album."""
    headers = _headers(make_token, "cache-user")
    mid = _upload(client, headers, _png_bytes(500, 400), filename="cached")
    # Génère le cache local 512 via la route existante.
    assert client.get(f"/api/media/{mid}/thumbnail?size=512", headers=headers).status_code == 200
    from routes.helpers import get_db

    conn = get_db()
    row = conn.execute("SELECT * FROM media_files WHERE id = ?", (mid,)).fetchone()
    conn.close()
    cache_path = media_module._thumbnail_cache_path(row, 512)
    assert os.path.isfile(cache_path)

    created = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, created["id"])
    album_thumb = album_env["albums"] / detail["key"] / "thumb" / "0001.jpg"
    with open(cache_path, "rb") as fh:
        cache_bytes = fh.read()
    assert album_thumb.read_bytes() == cache_bytes


# ── 4. Snapshot FIGÉ ──────────────────────────────────────────────────


def test_snapshot_is_frozen(client, make_token, album_env):
    """Une image ajoutée APRÈS la création n'entre PAS automatiquement."""
    headers = _headers(make_token, "snap-user")
    a = _upload(client, headers, _png_bytes(), filename="a")
    b = _upload(client, headers, _png_bytes(), filename="b")

    created = _create_album(client, headers, [a, b])["album"]
    detail = _wait_album(client, headers, created["id"])
    assert {i["media_id"] for i in detail["items"]} == {a, b}

    # Nouvelle image uploadée plus tard : hors album.
    c = _upload(client, headers, _png_bytes(), filename="c")
    detail2 = client.get(f"/api/albums/{created['id']}", headers=headers).get_json()
    assert {i["media_id"] for i in detail2["items"]} == {a, b}
    assert c not in {i["media_id"] for i in detail2["items"]}


# ── 5. Validation par ids : skips avec raisons ────────────────────────


def test_create_skips_invalid_ids_with_reasons(client, make_token, album_env):
    headers_a = _headers(make_token, "skip-a")
    headers_b = _headers(make_token, "skip-b")

    good = _upload(client, headers_a, _png_bytes(), filename="good")
    video = _upload(client, headers_a, b"vid", kind="video", ext=".mp4", filename="vid")
    trashed = _upload(client, headers_a, _png_bytes(), filename="trash")
    assert client.delete(f"/api/media/{trashed}", headers=headers_a).status_code == 200
    foreign = _upload(client, headers_b, _png_bytes(), filename="foreign")

    body = _create_album(client, headers_a, [good, video, trashed, foreign, 999999])
    album = body["album"]
    assert album["progress"]["total"] == 1  # seul ``good`` est retenu
    reasons = {s["id"]: s["reason"] for s in body["skipped"]}
    assert reasons[video] == "unsupported_kind"
    assert reasons[trashed] == "trashed"
    assert reasons[foreign] == "not_owned"
    assert reasons[999999] == "not_found"

    detail = _wait_album(client, headers_a, album["id"])
    assert {i["media_id"] for i in detail["items"]} == {good}


def test_add_items_requires_valid_ids(client, make_token, album_env):
    headers = _headers(make_token, "add-user")
    mid = _upload(client, headers, _png_bytes(), filename="x")
    album = _create_album(client, headers, [mid])["album"]

    r = client.post(f"/api/albums/{album['id']}/items", json={"ids": "nope"}, headers=headers)
    assert r.status_code == 400


# ── 6. Ré-ajout idempotent ────────────────────────────────────────────


def test_readd_existing_item_is_duplicate(client, make_token, album_env):
    headers = _headers(make_token, "dupe-user")
    a = _upload(client, headers, _png_bytes(), filename="a")
    b = _upload(client, headers, _png_bytes(), filename="b")
    album = _create_album(client, headers, [a])["album"]
    _wait_album(client, headers, album["id"])

    r = client.post(f"/api/albums/{album['id']}/items", json={"ids": [a, b]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["added"] == 1
    assert body["skipped"] == [{"id": a, "reason": "duplicate"}]

    detail = _wait_album(client, headers, album["id"])
    assert {i["media_id"] for i in detail["items"]} == {a, b}
    assert detail["progress"]["total"] == 2


# ── 7. 403 / 404 (contrôle NÉGATIF) ───────────────────────────────────


def test_album_cross_user_forbidden(client, make_token, album_env):
    """NEGATIVE : la garde retirée ferait passer un 403 attendu → ROUGE."""
    headers_owner = _headers(make_token, "own-a")
    headers_other = _headers(make_token, "own-b")
    mid = _upload(client, headers_owner, _png_bytes(), filename="o")
    album = _create_album(client, headers_owner, [mid])["album"]
    aid = album["id"]

    assert client.get(f"/api/albums/{aid}", headers=headers_other).status_code == 403
    assert client.patch(f"/api/albums/{aid}", json={"title": "hack"}, headers=headers_other).status_code == 403
    assert client.post(f"/api/albums/{aid}/items", json={"ids": [mid]}, headers=headers_other).status_code == 403
    assert client.post(f"/api/albums/{aid}/revoke", headers=headers_other).status_code == 403
    assert client.delete(f"/api/albums/{aid}", headers=headers_other).status_code == 403

    # L'album n'a pas bougé.
    assert client.get(f"/api/albums/{aid}", headers=headers_owner).status_code == 200


def test_album_unknown_id_404(client, make_token, album_env):
    headers = _headers(make_token, "miss-user")
    assert client.get("/api/albums/999999", headers=headers).status_code == 404
    assert client.patch("/api/albums/999999", json={"title": "x"}, headers=headers).status_code == 404
    assert client.post("/api/albums/999999/revoke", headers=headers).status_code == 404
    assert client.delete("/api/albums/999999", headers=headers).status_code == 404


def test_admin_can_read_other_album(client, make_token, album_env):
    headers_owner = _headers(make_token, "ad-own", role="user")
    headers_admin = _headers(make_token, "ad-admin", role="admin")
    mid = _upload(client, headers_owner, _png_bytes(), filename="o")
    album = _create_album(client, headers_owner, [mid])["album"]
    assert client.get(f"/api/albums/{album['id']}", headers=headers_admin).status_code == 200


# ── 8. PATCH titre/description (DB + manifest) ────────────────────────


def test_patch_updates_db_and_manifest(client, make_token, album_env):
    headers = _headers(make_token, "patch-user")
    mid = _upload(client, headers, _png_bytes(), filename="p")
    album = _create_album(client, headers, [mid], title="T1", description="D1")["album"]
    detail = _wait_album(client, headers, album["id"])

    r = client.patch(
        f"/api/albums/{album['id']}",
        json={"title": "Nouveau", "description": "Desc"},
        headers=headers,
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["title"] == "Nouveau"

    manifest = json.loads((album_env["albums"] / detail["key"] / "manifest.json").read_text())
    assert manifest["title"] == "Nouveau"
    assert manifest["description"] == "Desc"

    # Type invalide → 400.
    assert client.patch(f"/api/albums/{album['id']}", json={"title": 42}, headers=headers).status_code == 400


# ── 9. Révocation ─────────────────────────────────────────────────────


def test_revoke_renames_folder_first(client, make_token, album_env):
    """NEGATIVE : sans renommage, le dossier resterait sous sa clé publique."""
    headers = _headers(make_token, "revoke-user")
    mid = _upload(client, headers, _png_bytes(), filename="r")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    key = detail["key"]
    base = album_env["albums"] / key
    assert base.is_dir()

    r = client.post(f"/api/albums/{album['id']}/revoke", headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["status"] == "revoked"
    assert r.get_json()["revoked_at"]

    assert not base.exists()  # dossier renommé
    assert (album_env["albums"] / f"{key}.revoked").is_dir()

    # Idempotent : un 2e revoke ne casse rien.
    assert client.post(f"/api/albums/{album['id']}/revoke", headers=headers).status_code == 200
    # PATCH refusé sur un album révoqué.
    assert client.patch(f"/api/albums/{album['id']}", json={"title": "x"}, headers=headers).status_code == 409


# ── 10. Suppression ───────────────────────────────────────────────────


def test_delete_removes_rows_and_folder(client, make_token, album_env):
    headers = _headers(make_token, "del-user")
    mid = _upload(client, headers, _png_bytes(), filename="d")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    base = album_env["albums"] / detail["key"]
    assert base.is_dir()

    assert client.delete(f"/api/albums/{album['id']}", headers=headers).status_code == 200
    assert not base.exists()
    assert client.get(f"/api/albums/{album['id']}", headers=headers).status_code == 404

    from routes.helpers import get_db

    conn = get_db()
    rows = conn.execute("SELECT COUNT(*) FROM album_media WHERE album_id = ?", (album["id"],)).fetchone()[0]
    conn.close()
    assert rows == 0


# ── 11. Manifest : AUCUNE donnée privée (contrôle NÉGATIF) ────────────


def test_manifest_has_no_private_fields(client, make_token, album_env):
    headers = _headers(make_token, "man-user")
    mid = _upload(client, headers, _png_bytes(), filename="secret-name")
    album = _create_album(client, headers, [mid], title="Titre", description="Desc")["album"]
    detail = _wait_album(client, headers, album["id"])

    raw = (album_env["albums"] / detail["key"] / "manifest.json").read_text()
    manifest = json.loads(raw)
    assert set(manifest.keys()) == {"title", "description", "count", "items", "updated_at"}
    assert manifest["count"] == 1
    assert set(manifest["items"][0].keys()) == {"i", "w", "h", "ext"}

    # Aucune fuite : ni id média, ni user_id, ni nom d'origine, ni chemin.
    for forbidden in ("media_id", "user_id", "secret-name", "final_path", "uploads"):
        assert forbidden not in raw, f"fuite manifest: {forbidden}"
    # Ni l'id média ni l'id de ligne ne doivent apparaître comme clés d'item.
    assert "id" not in manifest["items"][0]


# ── 12. EXIF purgés ───────────────────────────────────────────────────


def test_exif_stripped_by_reencoding(client, make_token, album_env):
    headers = _headers(make_token, "exif-user")
    data = _jpeg_with_exif()
    with Image.open(io.BytesIO(data)) as src:
        assert len(src.getexif()) > 0  # le source porte bien des EXIF

    mid = _upload(client, headers, data, ext=".jpg", filename="exif")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    item = detail["items"][0]
    assert item["status"] == "ok"
    assert item["ext"] == ".jpg"

    full = album_env["albums"] / detail["key"] / "full" / f"0001{item['ext']}"
    with Image.open(full) as out:
        assert len(out.getexif()) == 0
    # Et le re-encodage conserve la TAILLE ORIGINALE.
    assert (item["width"], item["height"]) == (64, 48)


# ── 13. PNG transparent conservé en PNG ───────────────────────────────


def test_transparent_png_kept_as_png(client, make_token, album_env):
    headers = _headers(make_token, "alpha-user")
    mid = _upload(client, headers, _png_alpha_bytes(opaque=False), filename="alpha")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    item = detail["items"][0]
    assert item["status"] == "ok"
    assert item["ext"] == ".png"
    assert (album_env["albums"] / detail["key"] / "full" / "0001.png").is_file()


def test_opaque_png_is_flattened_to_jpg(client, make_token, album_env):
    headers = _headers(make_token, "opaque-user")
    mid = _upload(client, headers, _png_alpha_bytes(opaque=True), filename="opaque")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    assert detail["items"][0]["ext"] == ".jpg"


# ── 14. Traversal / noms invalides rejetés ────────────────────────────


def test_invalid_album_keys_rejected():
    import album_web

    for bad in ("../etc", "a/b", "..", "", ".", "x" * 100, "key with space", "short", "0001.jpg"):
        assert not album_web.is_safe_album_key(bad), bad
        with pytest.raises(ValueError):
            album_web.album_dir(bad)

    good = "A" * 43
    assert album_web.is_safe_album_key(good)
    assert album_web.album_dir(good).endswith(good)


# ── 15. Migration idempotente ─────────────────────────────────────────


def test_album_tables_migration_idempotent(app):
    import sqlite3

    from db.init import _init_db
    from extensions import DB_PATH

    _init_db()
    _init_db()  # ne doit pas lever (idempotent)

    conn = sqlite3.connect(str(DB_PATH))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "albums" in tables and "album_media" in tables

    album_cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)")}
    assert {
        "id",
        "user_id",
        "key",
        "title",
        "description",
        "status",
        "progress_total",
        "progress_done",
        "created_at",
        "updated_at",
        "revoked_at",
    } <= album_cols

    item_cols = {r[1] for r in conn.execute("PRAGMA table_info(album_media)")}
    assert {
        "id",
        "album_id",
        "media_id",
        "item_no",
        "ext",
        "width",
        "height",
        "status",
        "error",
        "added_at",
    } <= item_cols
    conn.close()


# ── 16. Propagation de la purge + « figure dans N albums » ────────────


def test_purge_propagates_to_albums(client, make_token, album_env):
    headers = _headers(make_token, "purge-user")
    mid = _upload(client, headers, _png_bytes(), filename="purgeme")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])
    base = album_env["albums"] / detail["key"]

    # Avant purge : l'image figure dans 1 album.
    assert client.get(f"/api/albums/for-media/{mid}", headers=headers).get_json()["count"] == 1
    assert (base / "full" / "0001.jpg").is_file()
    assert (base / "thumb" / "0001.jpg").is_file()

    # Purge DÉFINITIVE du média.
    assert client.delete(f"/api/media/{mid}/purge", headers=headers).status_code == 200

    # L'item a disparu de l'album, le manifest est réécrit et les fichiers purgés.
    after = client.get(f"/api/albums/{album['id']}", headers=headers).get_json()
    assert after["items"] == []
    assert after["counts"]["total"] == 0
    assert after["progress"]["total"] == 0
    manifest = json.loads((base / "manifest.json").read_text())
    assert manifest["count"] == 0
    assert not (base / "full" / "0001.jpg").exists()
    assert not (base / "thumb" / "0001.jpg").exists()

    # Après purge : plus aucun album concerné.
    assert client.get(f"/api/albums/for-media/{mid}", headers=headers).get_json()["count"] == 0


def test_trash_does_not_touch_albums(client, make_token, album_env):
    """La corbeille (soft delete) ne retire JAMAIS l'image des albums."""
    headers = _headers(make_token, "soft-user")
    mid = _upload(client, headers, _png_bytes(), filename="soft")
    album = _create_album(client, headers, [mid])["album"]
    detail = _wait_album(client, headers, album["id"])

    assert client.delete(f"/api/media/{mid}", headers=headers).status_code == 200

    after = client.get(f"/api/albums/{album['id']}", headers=headers).get_json()
    assert {i["media_id"] for i in after["items"]} == {mid}
    assert (album_env["albums"] / detail["key"] / "full" / "0001.jpg").is_file()


# ── 17. Liste + compteurs ─────────────────────────────────────────────


def test_list_albums_with_counts(client, make_token, album_env):
    headers = _headers(make_token, "list-user")
    a = _upload(client, headers, _png_bytes(), filename="a")
    b = _upload(client, headers, _png_bytes(), filename="b")
    _create_album(client, headers, [a, b], title="Deux")

    body = client.get("/api/albums", headers=headers).get_json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Deux"
    assert item["counts"]["total"] == 2

    # Isolation : un autre utilisateur ne voit rien.
    other = _headers(make_token, "list-other")
    assert client.get("/api/albums", headers=other).get_json()["total"] == 0


# ── 18. URL publique (AIH_ALBUM_PUBLIC_BASE_URL) ──────────────────────


def test_album_public_url_null_when_unset(client, make_token, album_env, monkeypatch):
    """Sans base configurée (ou base vide) : ``public_url = null`` PARTOUT."""
    monkeypatch.delenv("AIH_ALBUM_PUBLIC_BASE_URL", raising=False)
    headers = _headers(make_token, "url-unset")
    mid = _upload(client, headers, _png_bytes(), filename="u")
    album = _create_album(client, headers, [mid], title="Sans URL")["album"]
    assert album["public_url"] is None

    detail = client.get(f"/api/albums/{album['id']}", headers=headers).get_json()
    assert detail["public_url"] is None
    listed = client.get("/api/albums", headers=headers).get_json()["items"][0]
    assert listed["public_url"] is None
    for_media = client.get(f"/api/albums/for-media/{mid}", headers=headers).get_json()
    assert for_media["albums"][0]["public_url"] is None

    # Contrôle négatif : une base réduite à des espaces n'est PAS configurée.
    monkeypatch.setenv("AIH_ALBUM_PUBLIC_BASE_URL", "   ")
    blank = _create_album(client, headers, [mid], title="Blanc")["album"]
    assert blank["public_url"] is None


def test_album_public_url_built_from_env(client, make_token, album_env, monkeypatch):
    """Base définie : ``<base>/a/<key>``, '/' final normalisé, clé intacte."""
    monkeypatch.setenv("AIH_ALBUM_PUBLIC_BASE_URL", "https://albums.example.tld/")
    headers = _headers(make_token, "url-set")
    mid = _upload(client, headers, _png_bytes(), filename="s")
    album = _create_album(client, headers, [mid])["album"]
    assert album["public_url"].endswith("/a/" + album["key"])
    assert album["public_url"] == f"https://albums.example.tld/a/{album['key']}"

    detail = client.get(f"/api/albums/{album['id']}", headers=headers).get_json()
    assert detail["public_url"] == album["public_url"]
    listed = client.get("/api/albums", headers=headers).get_json()["items"][0]
    assert listed["public_url"] == album["public_url"]
    for_media = client.get(f"/api/albums/for-media/{mid}", headers=headers).get_json()
    assert for_media["albums"][0]["public_url"] == album["public_url"]

    # PATCH et révocation ré-émettent le contrat complet (URL incluse).
    patched = client.patch(f"/api/albums/{album['id']}", json={"title": "T"}, headers=headers).get_json()
    assert patched["public_url"] == album["public_url"]
    revoked = client.post(f"/api/albums/{album['id']}/revoke", headers=headers).get_json()
    assert revoked["public_url"] == album["public_url"]
