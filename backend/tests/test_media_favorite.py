"""Tests du FAVORI / « à exposer » des médias (``routes/media.py``) :

  - colonne ``favorite`` (migration idempotente + défaut 0) ;
  - POST /api/media/<id>/favorite : bascule unitaire (booléen), 403 croisé,
    404 inexistant, 400 body invalide, idempotence, persistance en base ;
  - POST /api/media/favorite     : variante GROUPÉE (récap updated/skipped) ;
  - GET  /api/media              : filtre ``favorite=1|0`` + absence de filtre ;
  - exposition du champ ``favorite`` (booléen) dans la liste ET /metadata.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_favorite_cross_user_forbidden`` : retirer la garde d'autorisation
    ferait passer un 403 attendu → échec ;
  - ``test_list_filter_favorite`` : oublier d'appliquer le filtre ferait
    ressortir un non-favori → échec ;
  - ``test_favorite_route_requires_auth`` : retirer l'auth → 401 attendu → 400.
"""

import io
import sqlite3

import pytest
import storage as storage_module
from PIL import Image
from storage import LocalStorage

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


def _png_bytes(width=320, height=200):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 200)).save(buf, "PNG")
    return buf.getvalue()


def _upload(client, headers, content, *, kind="image", ext=".png",
            filename="clip", subfolder="2025-01-01"):
    """Init + 1 chunk + complete ; retourne l'id du média créé."""
    r = client.post(
        "/api/media/init",
        json={"kind": kind, "ext": ext, "size": len(content), "filename": filename,
              "subfolder": subfolder},
        headers=headers,
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    chunk_size = data["chunk_size"]
    for i in range(data["total_chunks"]):
        chunk = content[i * chunk_size:(i + 1) * chunk_size]
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


def _row(media_id):
    from routes.helpers import get_db

    conn = get_db()
    try:
        return conn.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)).fetchone()
    finally:
        conn.close()


def _list_ids(client, headers, query=""):
    body = client.get(f"/api/media{query}", headers=headers).get_json()
    return [i["id"] for i in body["items"]]


@pytest.fixture()
def media_storage(tmp_path, monkeypatch):
    """LocalStorage isolé dans un répertoire temporaire (aucune écriture repo)."""
    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    monkeypatch.setenv("AIH_THUMB_CACHE_DIR", str(tmp_path / "thumbs"))
    yield st


# ── 1. Migration / schéma ──────────────────────────────────────────────

def test_migration_adds_favorite_idempotent_and_default_zero():
    """La migration ajoute ``favorite NOT NULL DEFAULT 0`` et est idempotente.

    Une base « ancienne » (table sans ``favorite``) reçoit la colonne au 1er
    passage ; le 2e passage ne lève pas (garde ``PRAGMA table_info``). Une ligne
    insérée SANS favorite vaut 0 (compat arrière).
    """
    from db.init import _migrate_media_files

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE media_files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, "
            "status TEXT DEFAULT 'uploading', final_path TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO media_files (user_id) VALUES ('legacy')"
        )
        conn.commit()
        assert "favorite" not in [r[1] for r in conn.execute("PRAGMA table_info(media_files)")]

        _migrate_media_files(conn)  # 1er passage : ajoute la colonne
        _migrate_media_files(conn)  # 2e passage : idempotent (aucune erreur)

        cols = [r[1] for r in conn.execute("PRAGMA table_info(media_files)")]
        assert "favorite" in cols
        # Les lignes pré-existantes valent 0 (défaut).
        assert conn.execute("SELECT favorite FROM media_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_favorite_index_exists(client):
    """Un index (user_id, favorite) est créé pour le filtre de la galerie."""
    from routes.helpers import get_db

    conn = get_db()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='media_files'"
        )]
    finally:
        conn.close()
    assert "idx_media_files_user_favorite" in names


def test_new_upload_is_not_favorite_by_default(client, make_token, media_storage):
    headers = _headers(make_token, "fav-default")
    mid = _upload(client, headers, b"x", filename="d")
    assert _row(mid)["favorite"] == 0

    item = client.get("/api/media", headers=headers).get_json()["items"][0]
    assert item["favorite"] is False  # booléen sérialisé, pas 0


# ── 2. Auth / autorisation (contrôles NÉGATIFS) ────────────────────────

def test_favorite_route_requires_auth(client):
    """NEGATIVE : sans token, les routes favori refusent (401)."""
    assert client.post("/api/media/1/favorite", json={"favorite": True}).status_code == 401
    assert client.post("/api/media/favorite", json={"ids": [1], "favorite": True}).status_code == 401


def test_favorite_cross_user_forbidden(client, make_token, media_storage):
    """NEGATIVE : un autre utilisateur ne peut PAS favoriser le média d'autrui.

    Garde d'autorisation retirée → ce test renverrait 200 au lieu de 403 → ROUGE.
    """
    headers_a = _headers(make_token, "fav-a")
    headers_b = _headers(make_token, "fav-b")
    mid = _upload(client, headers_a, b"data-a", filename="priv")

    assert client.post(
        f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers_b
    ).status_code == 403
    # Le drapeau n'a pas bougé.
    assert _row(mid)["favorite"] == 0


def test_favorite_unknown_id_404(client, make_token, media_storage):
    headers = _headers(make_token, "fav-404")
    assert client.post(
        "/api/media/999999/favorite", json={"favorite": True}, headers=headers
    ).status_code == 404


def test_admin_can_favorite_others(client, make_token, media_storage):
    headers_owner = _headers(make_token, "fav-owner", role="user")
    headers_admin = _headers(make_token, "fav-admin", role="admin")
    mid = _upload(client, headers_owner, b"own", filename="o")

    assert client.post(
        f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers_admin
    ).status_code == 200
    assert _row(mid)["favorite"] == 1


# ── 3. Bascule unitaire ────────────────────────────────────────────────

def test_favorite_toggle_persists_and_returns_item(client, make_token, media_storage):
    headers = _headers(make_token, "fav-toggle")
    mid = _upload(client, headers, b"x", filename="t")

    resp = client.post(f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["id"] == mid
    assert body["favorite"] is True
    assert _row(mid)["favorite"] == 1

    # Retrait.
    resp = client.post(f"/api/media/{mid}/favorite", json={"favorite": False}, headers=headers)
    assert resp.get_json()["favorite"] is False
    assert _row(mid)["favorite"] == 0


def test_favorite_is_idempotent(client, make_token, media_storage):
    headers = _headers(make_token, "fav-idem")
    mid = _upload(client, headers, b"x", filename="i")
    client.post(f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers)
    client.post(f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers)
    assert _row(mid)["favorite"] == 1


def test_favorite_rejects_non_boolean_body(client, make_token, media_storage):
    headers = _headers(make_token, "fav-bad-body")
    mid = _upload(client, headers, b"x", filename="b")
    for bad in ("true", 2, None, [], {}):
        r = client.post(f"/api/media/{mid}/favorite", json={"favorite": bad}, headers=headers)
        assert r.status_code == 400, (bad, r.get_data(as_text=True))
    # Absent aussi.
    assert client.post(f"/api/media/{mid}/favorite", json={}, headers=headers).status_code == 400
    # Entiers 0/1 tolérés (tolerance API).
    assert client.post(
        f"/api/media/{mid}/favorite", json={"favorite": 1}, headers=headers
    ).status_code == 200
    assert _row(mid)["favorite"] == 1


def test_favorite_exposed_in_metadata(client, make_token, media_storage):
    headers = _headers(make_token, "fav-meta")
    mid = _upload(client, headers, b"x", filename="m")
    client.post(f"/api/media/{mid}/favorite", json={"favorite": True}, headers=headers)
    meta = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert meta["favorite"] is True


# ── 4. Variante groupée ────────────────────────────────────────────────

def test_bulk_favorite_recap_and_skipped(client, make_token, media_storage):
    headers_a = _headers(make_token, "favbulk-a")
    headers_b = _headers(make_token, "favbulk-b")
    a1 = _upload(client, headers_a, b"a1", filename="a1")
    a2 = _upload(client, headers_a, b"a2", filename="a2")
    b1 = _upload(client, headers_b, b"b1", filename="b1")

    resp = client.post(
        "/api/media/favorite",
        json={"ids": [a1, a2, b1, 999999], "favorite": True},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() == {"updated": 2, "skipped": [b1, 999999]}

    # Aucun échec partiel : seuls les médias autorisés changent.
    assert _row(a1)["favorite"] == 1
    assert _row(a2)["favorite"] == 1
    assert _row(b1)["favorite"] == 0

    # Démarquage groupé.
    resp = client.post(
        "/api/media/favorite",
        json={"ids": [a1, a2], "favorite": False},
        headers=headers_a,
    )
    assert resp.get_json() == {"updated": 2, "skipped": []}
    assert _row(a1)["favorite"] == 0 and _row(a2)["favorite"] == 0


def test_bulk_favorite_invalid_body_400(client, make_token, media_storage):
    headers = _headers(make_token, "favbulk-bad")
    assert client.post("/api/media/favorite", json={}, headers=headers).status_code == 400
    assert client.post(
        "/api/media/favorite", json={"ids": "nope", "favorite": True}, headers=headers
    ).status_code == 400
    # ids valide mais favorite manquant → 400.
    assert client.post(
        "/api/media/favorite", json={"ids": [1]}, headers=headers
    ).status_code == 400
    # Liste vide : valide, aucun effet, récap vide.
    r = client.post("/api/media/favorite", json={"ids": [], "favorite": True}, headers=headers)
    assert r.status_code == 200
    assert r.get_json() == {"updated": 0, "skipped": []}


# ── 5. Filtre de la liste ──────────────────────────────────────────────

def test_list_filter_favorite(client, make_token, media_storage):
    """NEGATIVE : ``favorite=1`` n'expose QUE les favoris ; ``favorite=0`` QUE
    les autres ; absent = pas de filtre (contrôle négatif du filtre oublié)."""
    headers = _headers(make_token, "fav-filter")
    fav = _upload(client, headers, b"f1", filename="fav")
    other = _upload(client, headers, b"f2", filename="other")
    trashed_fav = _upload(client, headers, b"f3", filename="gone")
    client.post(f"/api/media/{fav}/favorite", json={"favorite": True}, headers=headers)
    client.post(f"/api/media/{trashed_fav}/favorite", json={"favorite": True}, headers=headers)
    client.delete(f"/api/media/{trashed_fav}", headers=headers)  # corbeillé exclu

    assert _list_ids(client, headers, "?favorite=1") == [fav]
    assert _list_ids(client, headers, "?favorite=0") == [other]
    # Absent = pas de filtre → les deux vivants ressortent.
    assert set(_list_ids(client, headers)) == {fav, other}
    # Tolérance true/false.
    assert _list_ids(client, headers, "?favorite=true") == [fav]
    assert _list_ids(client, headers, "?favorite=false") == [other]
    # Valeur invalide → 400 (pas de fallback silencieux).
    assert client.get("/api/media?favorite=maybe", headers=headers).status_code == 400


def test_list_filter_favorite_combines_with_others(client, make_token, media_storage):
    headers = _headers(make_token, "fav-combi")
    a = _upload(client, headers, b"a", filename="a", subfolder="x")
    b = _upload(client, headers, b"b", filename="b", subfolder="y")
    client.post(f"/api/media/{a}/favorite", json={"favorite": True}, headers=headers)
    client.post(f"/api/media/{b}/favorite", json={"favorite": True}, headers=headers)

    assert _list_ids(client, headers, "?favorite=1&subfolder=x") == [a]
    assert _list_ids(client, headers, "?favorite=1&subfolder=y") == [b]


def test_default_list_contract_unchanged(client, make_token, media_storage):
    """Sans paramètre, le contrat de la liste reste inchangé (+ champ favorite)."""
    headers = _headers(make_token, "fav-reg")
    ids = [_upload(client, headers, f"d{i}".encode(), filename=f"f{i}") for i in range(3)]

    body = client.get("/api/media", headers=headers).get_json()
    assert set(body.keys()) == {"items", "total", "page", "limit"}
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]] == sorted(ids, reverse=True)
    assert body["items"][0]["favorite"] is False
