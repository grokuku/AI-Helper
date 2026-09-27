"""Tests des TAGS MANUELS des médias (``routes/media.py``) :

  - table ``media_tags`` (source ``manual``/``ai``, unicité NOCASE, index) ;
  - POST /api/media/<id>/tags : ajout/retrait unitaire (normalisation,
    unicité insensible à la casse, 403 croisé, 404 inexistant, 400 body) ;
  - POST /api/media/tags      : variante GROUPÉE (récap updated/skipped) ;
  - GET  /api/media/tags      : liste + comptes (agrégation, corbeillés exclus) ;
  - GET  /api/media           : filtre ``tags`` (sémantique OU) + combinaison ;
  - exposition des tags dans la liste ET /metadata ;
  - SUPPRESSION EN CASCADE sur purge (les tags disparaissent) ; la corbeille
    (soft delete) les CONSERVE.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_tag_uniqueness_is_case_insensitive`` : oublier ``COLLATE NOCASE``
    ferait apparaître 2 tags → échec ;
  - ``test_tags_route_requires_auth`` : retirer l'auth → 401 attendu → échec ;
  - ``test_tags_cross_user_forbidden`` : retirer la garde → 403 attendu → échec ;
  - ``test_purge_cascades_tags`` : oublier la cascade → tags orphelins → échec ;
  - ``test_list_filter_tags_union`` : oublier d'appliquer le filtre ferait
    ressortir des médias non taggés → échec.
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


def _tags(media_id):
    """Liste des tags (chaînes) d'un média, triée."""
    from routes.helpers import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT tag FROM media_tags WHERE media_id = ? ORDER BY tag", (media_id,)
        ).fetchall()
    finally:
        conn.close()
    return [r["tag"] for r in rows]


def _tag_count(media_id):
    from routes.helpers import get_db

    conn = get_db()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM media_tags WHERE media_id = ?", (media_id,)
        ).fetchone()[0]
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


# ── 1. Schéma / migration ──────────────────────────────────────────────

def test_media_tags_table_schema(client):
    """La table ``media_tags`` existe avec ses colonnes, contraintes et index."""
    from routes.helpers import get_db

    conn = get_db()
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(media_tags)")}
        assert {"id", "user_id", "media_id", "tag", "source", "created_at"} <= set(cols)
        # ``tag`` porte la collation NOCASE (unicité insensible à la casse).
        # PRAGMA table_info ne l'expose pas : on inspecte le DDL.
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_tags'"
        ).fetchone()[0]
        assert "COLLATE NOCASE" in ddl
        assert "UNIQUE (media_id, tag)" in ddl
        # Clé étrangère vers media_files (garde-fou anti-orphelin) — la cascade
        # est EXPLICITE dans ``_purge_media_row`` (pas dans le DDL).
        fks = conn.execute("PRAGMA foreign_key_list(media_tags)").fetchall()
        refs = {(r[2], r[3], r[4]) for r in fks}
        assert ("media_files", "media_id", "id") in refs
        assert ("users", "user_id", "id") in refs
        # Défaut de source = 'manual'.
        assert cols["source"][4] == "'manual'"
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='media_tags'"
        )]
        assert "idx_media_tags_user_tag" in names
        assert "idx_media_tags_media" in names
    finally:
        conn.close()


def test_source_default_is_manual_and_ai_accepted():
    """Une insertion sans ``source`` vaut ``manual`` ; ``ai`` est accepté."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE media_files (id INTEGER PRIMARY KEY, user_id TEXT)")
        conn.execute("""
            CREATE TABLE media_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, media_id INTEGER,
                tag TEXT NOT NULL COLLATE NOCASE,
                source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','ai')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (media_id, tag))
        """)
        conn.execute("INSERT INTO media_files (id, user_id) VALUES (1, 'u')")
        conn.execute("INSERT INTO media_tags (user_id, media_id, tag) VALUES ('u', 1, 'a')")
        conn.execute("INSERT INTO media_tags (user_id, media_id, tag, source) VALUES ('u', 1, 'b', 'ai')")
        assert conn.execute("SELECT source FROM media_tags WHERE tag='a'").fetchone()[0] == "manual"
        assert conn.execute("SELECT source FROM media_tags WHERE tag='b'").fetchone()[0] == "ai"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_tags (user_id, media_id, tag, source) VALUES ('u', 1, 'c', 'nope')")
    finally:
        conn.close()


# ── 2. Normalisation + unicité insensible à la casse ───────────────────

def test_tag_uniqueness_is_case_insensitive(client, make_token, media_storage):
    """NEGATIVE : ajouter le même tag dans une autre casse ne crée PAS de doublon.

    Oublier ``COLLATE NOCASE`` (ou l'INSERT OR IGNORE) ferait apparaître 2 tags.
    """
    headers = _headers(make_token, "tag-case")
    mid = _upload(client, headers, b"x", filename="c")

    r = client.post(f"/api/media/{mid}/tags", json={"add": [" Sunset "]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["tags"] == ["Sunset"]

    for variant in ("sunset", "SUNSET", "  sunset  "):
        r = client.post(f"/api/media/{mid}/tags", json={"add": [variant]}, headers=headers)
        assert r.status_code == 200, r.get_data(as_text=True)
    assert _tags(mid) == ["Sunset"]  # casse de la 1re saisie conservée
    assert _tag_count(mid) == 1


def test_tag_normalization_collapses_spaces(client, make_token, media_storage):
    headers = _headers(make_token, "tag-norm")
    mid = _upload(client, headers, b"x", filename="n")
    r = client.post(f"/api/media/{mid}/tags", json={"add": ["  a   b  "]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["tags"] == ["a b"]


def test_tag_rejects_invalid_values(client, make_token, media_storage):
    headers = _headers(make_token, "tag-bad")
    mid = _upload(client, headers, b"x", filename="bad")
    bad_payloads = [
        {"add": [""]},
        {"add": ["   "]},
        {"add": ["x" * 51]},          # trop long
        {"add": ["a,b"]},             # virgule interdite (séparateur de filtre)
        {"add": ["a<b"]},             # caractère interdit
        {"add": "notalist"},
        {"add": [42]},                # non chaîne
        {"remove": [""]},
    ]
    for payload in bad_payloads:
        r = client.post(f"/api/media/{mid}/tags", json=payload, headers=headers)
        assert r.status_code == 400, (payload, r.get_data(as_text=True))
    assert _tag_count(mid) == 0


def test_add_remove_requires_at_least_one(client, make_token, media_storage):
    headers = _headers(make_token, "tag-empty")
    mid = _upload(client, headers, b"x", filename="e")
    assert client.post(f"/api/media/{mid}/tags", json={}, headers=headers).status_code == 400
    assert client.post(
        f"/api/media/{mid}/tags", json={"add": [], "remove": []}, headers=headers
    ).status_code == 400


# ── 3. Ajout / retrait unitaire ────────────────────────────────────────

def test_add_and_remove_unit_tags(client, make_token, media_storage):
    headers = _headers(make_token, "tag-unit")
    mid = _upload(client, headers, b"x", filename="u")

    r = client.post(
        f"/api/media/{mid}/tags", json={"add": ["ciel", "mer"]}, headers=headers
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["id"] == mid
    assert body["tags"] == ["ciel", "mer"]
    assert body["tags_detail"] == [
        {"tag": "ciel", "source": "manual"},
        {"tag": "mer", "source": "manual"},
    ]
    assert _tags(mid) == ["ciel", "mer"]

    # Retrait (insensible à la casse).
    r = client.post(f"/api/media/{mid}/tags", json={"remove": ["CIEL"]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["tags"] == ["mer"]
    assert _tags(mid) == ["mer"]

    # Retrait d'un tag absent = no-op silencieux (pas d'erreur).
    r = client.post(f"/api/media/{mid}/tags", json={"remove": ["absent"]}, headers=headers)
    assert r.status_code == 200
    assert _tags(mid) == ["mer"]


def test_add_wins_over_remove_in_same_call(client, make_token, media_storage):
    headers = _headers(make_token, "tag-order")
    mid = _upload(client, headers, b"x", filename="o")
    client.post(f"/api/media/{mid}/tags", json={"add": ["x"]}, headers=headers)
    r = client.post(
        f"/api/media/{mid}/tags", json={"add": ["x"], "remove": ["x"]}, headers=headers
    )
    assert r.get_json()["tags"] == ["x"]  # remove puis add → ajouté


def test_tags_route_requires_auth(client):
    """NEGATIVE : sans token, les routes tags refusent (401)."""
    assert client.get("/api/media/tags").status_code == 401
    assert client.post("/api/media/1/tags", json={"add": ["x"]}).status_code == 401
    assert client.post("/api/media/tags", json={"ids": [1], "add": ["x"]}).status_code == 401


def test_tags_cross_user_forbidden(client, make_token, media_storage):
    """NEGATIVE : un autre utilisateur ne peut PAS tagger le média d'autrui.

    Garde retirée → 200 au lieu de 403 → ROUGE.
    """
    headers_a = _headers(make_token, "tag-a")
    headers_b = _headers(make_token, "tag-b")
    mid = _upload(client, headers_a, b"data-a", filename="priv")

    assert client.post(
        f"/api/media/{mid}/tags", json={"add": ["intrus"]}, headers=headers_b
    ).status_code == 403
    assert _tag_count(mid) == 0


def test_tags_unknown_id_404(client, make_token, media_storage):
    headers = _headers(make_token, "tag-404")
    assert client.post(
        "/api/media/999999/tags", json={"add": ["x"]}, headers=headers
    ).status_code == 404


def test_admin_can_tag_others(client, make_token, media_storage):
    headers_owner = _headers(make_token, "tag-owner", role="user")
    headers_admin = _headers(make_token, "tag-admin", role="admin")
    mid = _upload(client, headers_owner, b"own", filename="o")
    assert client.post(
        f"/api/media/{mid}/tags", json={"add": ["admin-tag"]}, headers=headers_admin
    ).status_code == 200
    assert _tags(mid) == ["admin-tag"]


# ── 4. Variante groupée ────────────────────────────────────────────────

def test_bulk_tags_recap_and_skipped(client, make_token, media_storage):
    headers_a = _headers(make_token, "tagbulk-a")
    headers_b = _headers(make_token, "tagbulk-b")
    a1 = _upload(client, headers_a, b"a1", filename="a1")
    a2 = _upload(client, headers_a, b"a2", filename="a2")
    b1 = _upload(client, headers_b, b"b1", filename="b1")

    resp = client.post(
        "/api/media/tags",
        json={"ids": [a1, a2, b1, 999999], "add": ["lot"]},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() == {"updated": 2, "skipped": [b1, 999999]}
    assert _tags(a1) == ["lot"] and _tags(a2) == ["lot"]
    assert _tag_count(b1) == 0  # aucun échec partiel

    # Retrait groupé.
    resp = client.post(
        "/api/media/tags", json={"ids": [a1, a2], "remove": ["lot"]}, headers=headers_a
    )
    assert resp.get_json() == {"updated": 2, "skipped": []}
    assert _tag_count(a1) == 0 and _tag_count(a2) == 0


def test_bulk_tags_invalid_body_400(client, make_token, media_storage):
    headers = _headers(make_token, "tagbulk-bad")
    assert client.post("/api/media/tags", json={}, headers=headers).status_code == 400
    assert client.post(
        "/api/media/tags", json={"ids": "nope", "add": ["x"]}, headers=headers
    ).status_code == 400
    # ids valide mais ni add ni remove → 400.
    assert client.post(
        "/api/media/tags", json={"ids": [1]}, headers=headers
    ).status_code == 400
    # add invalide → 400.
    assert client.post(
        "/api/media/tags", json={"ids": [1], "add": ["a,b"]}, headers=headers
    ).status_code == 400
    # Liste d'ids vide : valide, aucun effet, récap vide.
    r = client.post("/api/media/tags", json={"ids": [], "add": ["x"]}, headers=headers)
    assert r.status_code == 200
    assert r.get_json() == {"updated": 0, "skipped": []}


# ── 5. GET /api/media/tags : liste + comptes ───────────────────────────

def test_tags_list_counts_and_trash_exclusion(client, make_token, media_storage):
    headers = _headers(make_token, "tag-list")
    a = _upload(client, headers, b"a", filename="a", subfolder="x")
    b = _upload(client, headers, b"b", filename="b", subfolder="y")
    c = _upload(client, headers, b"c", filename="c", subfolder="z")
    client.post(f"/api/media/{a}/tags", json={"add": ["ciel", "mer"]}, headers=headers)
    client.post(f"/api/media/{b}/tags", json={"add": ["ciel"]}, headers=headers)
    client.post(f"/api/media/{c}/tags", json={"add": ["montagne"]}, headers=headers)
    client.delete(f"/api/media/{b}", headers=headers)  # corbeillé → exclu par défaut

    body = client.get("/api/media/tags", headers=headers).get_json()
    got = {t["tag"]: t["count"] for t in body["tags"]}
    # b corbeillé → son « ciel » exclu ; a (ciel+mer) et c (montagne) restent.
    assert got == {"ciel": 1, "mer": 1, "montagne": 1}
    assert body["total"] == 3
    # Tri stable (NOCASE).
    assert [t["tag"] for t in body["tags"]] == ["ciel", "mer", "montagne"]

    trashed = client.get("/api/media/tags?status=trashed", headers=headers).get_json()
    assert {t["tag"]: t["count"] for t in trashed["tags"]} == {"ciel": 1}

    allst = client.get("/api/media/tags?status=all", headers=headers).get_json()
    assert {t["tag"]: t["count"] for t in allst["tags"]} == {"ciel": 2, "mer": 1, "montagne": 1}

    # status invalide → 400.
    assert client.get("/api/media/tags?status=bogus", headers=headers).status_code == 400


def test_tags_list_merges_case_variants(client, make_token, media_storage):
    headers = _headers(make_token, "tag-merge")
    a = _upload(client, headers, b"a", filename="a")
    b = _upload(client, headers, b"b", filename="b")
    client.post(f"/api/media/{a}/tags", json={"add": ["Ciel"]}, headers=headers)
    client.post(f"/api/media/{b}/tags", json={"add": ["ciel"]}, headers=headers)
    body = client.get("/api/media/tags", headers=headers).get_json()
    assert len(body["tags"]) == 1
    assert body["tags"][0]["count"] == 2


def test_tags_list_isolated_per_user(client, make_token, media_storage):
    headers_a = _headers(make_token, "tag-list-a")
    headers_b = _headers(make_token, "tag-list-b")
    a = _upload(client, headers_a, b"a", filename="a")
    client.post(f"/api/media/{a}/tags", json={"add": ["a-only"]}, headers=headers_a)
    assert client.get("/api/media/tags", headers=headers_b).get_json()["tags"] == []


# ── 6. Filtre de la liste (sémantique OU) ──────────────────────────────

def test_list_filter_tags_union(client, make_token, media_storage):
    """Le filtre ``tags`` est une UNION : un média ressort s'il a AU MOINS un tag.

    Oublier d'appliquer le filtre ferait ressortir des médias non taggés → ROUGE.
    """
    headers = _headers(make_token, "tag-filter")
    a = _upload(client, headers, b"a", filename="a", kind="image", subfolder="s1")
    b = _upload(client, headers, b"b", filename="b", kind="video", ext=".mp4", subfolder="s2")
    c = _upload(client, headers, b"c", filename="c", kind="image", subfolder="s3")
    client.post(f"/api/media/{a}/tags", json={"add": ["ciel", "mer"]}, headers=headers)
    client.post(f"/api/media/{b}/tags", json={"add": ["mer"]}, headers=headers)
    client.post(f"/api/media/{c}/tags", json={"add": ["montagne"]}, headers=headers)

    assert _list_ids(client, headers, "?tags=ciel") == [a]
    assert sorted(_list_ids(client, headers, "?tags=mer")) == [a, b]
    # Union (virgules).
    assert sorted(_list_ids(client, headers, "?tags=ciel,mer")) == [a, b]
    # Union (répété).
    assert sorted(_list_ids(client, headers, "?tags=ciel&tags=montagne")) == [a, c]
    # Insensible à la casse.
    assert _list_ids(client, headers, "?tags=CIEL") == [a]
    # Un média SANS tag demandé ne ressort pas (contrôle négatif).
    assert c not in _list_ids(client, headers, "?tags=ciel")


def test_list_filter_tags_combines_with_other_filters(client, make_token, media_storage):
    headers = _headers(make_token, "tag-combi")
    a = _upload(client, headers, b"a", filename="a", kind="image", subfolder="s1")
    b = _upload(client, headers, b"b", filename="b", kind="video", ext=".mp4", subfolder="s2")
    client.post(f"/api/media/{a}/tags", json={"add": ["mer"]}, headers=headers)
    client.post(f"/api/media/{b}/tags", json={"add": ["mer"]}, headers=headers)
    client.post(f"/api/media/{a}/favorite", json={"favorite": True}, headers=headers)

    assert _list_ids(client, headers, "?tags=mer&kind=video") == [b]
    assert _list_ids(client, headers, "?tags=mer&subfolders=s1") == [a]
    assert _list_ids(client, headers, "?tags=mer&favorite=1") == [a]


def test_list_filter_tags_absent_and_vide_no_filter(client, make_token, media_storage):
    headers = _headers(make_token, "tag-empty-filter")
    a = _upload(client, headers, b"a", filename="a")
    b = _upload(client, headers, b"b", filename="b")
    client.post(f"/api/media/{a}/tags", json={"add": ["x"]}, headers=headers)
    # Absent → pas de filtre ; vide → pas de filtre.
    assert set(_list_ids(client, headers)) == {a, b}
    assert set(_list_ids(client, headers, "?tags=")) == {a, b}
    # Segments vides ignorés : « x,, » = filtre sur « x » uniquement.
    assert set(_list_ids(client, headers, "?tags=x,,")) == {a}


def test_list_filter_tags_invalid_400(client, make_token, media_storage):
    headers = _headers(make_token, "tag-bad-filter")
    _upload(client, headers, b"a", filename="a")
    assert client.get("/api/media?tags=" + "x" * 51, headers=headers).status_code == 400
    assert client.get("/api/media?tags=bad%3Ctag", headers=headers).status_code == 400


# ── 7. Exposition liste + /metadata ────────────────────────────────────

def test_tags_exposed_in_list_and_metadata(client, make_token, media_storage):
    headers = _headers(make_token, "tag-expose")
    mid = _upload(client, headers, b"x", filename="x")
    client.post(f"/api/media/{mid}/tags", json={"add": ["alpha", "beta"]}, headers=headers)

    item = client.get("/api/media", headers=headers).get_json()["items"][0]
    assert item["tags"] == ["alpha", "beta"]
    assert item["tags_detail"] == [
        {"tag": "alpha", "source": "manual"},
        {"tag": "beta", "source": "manual"},
    ]
    meta = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert meta["tags"] == ["alpha", "beta"]
    assert meta["tags_detail"][0]["source"] == "manual"


def test_media_without_tags_exposes_empty_list(client, make_token, media_storage):
    headers = _headers(make_token, "tag-none")
    _upload(client, headers, b"x", filename="x")
    item = client.get("/api/media", headers=headers).get_json()["items"][0]
    assert item["tags"] == [] and item["tags_detail"] == []


# ── 8. Cascade sur purge / conservation en corbeille ───────────────────

def test_purge_cascades_tags(client, make_token, media_storage):
    """NEGATIVE : purger un média supprime ses tags (sinon lignes orphelines).

    Oublier la cascade dans ``_purge_media_row`` → ce test ROUGIT.
    """
    headers = _headers(make_token, "tag-cascade")
    mid = _upload(client, headers, b"x", filename="x")
    client.post(f"/api/media/{mid}/tags", json={"add": ["a", "b"]}, headers=headers)
    assert _tag_count(mid) == 2

    r = client.delete(f"/api/media/{mid}/purge", headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _tag_count(mid) == 0  # tags disparus avec la ligne média


def test_soft_delete_keeps_tags(client, make_token, media_storage):
    """La corbeille (soft delete) CONSERVE les tags (restaurables)."""
    headers = _headers(make_token, "tag-soft")
    mid = _upload(client, headers, b"x", filename="x")
    client.post(f"/api/media/{mid}/tags", json={"add": ["keep"]}, headers=headers)
    client.delete(f"/api/media/{mid}", headers=headers)
    assert _tags(mid) == ["keep"]
    # Restauration → tags toujours là.
    client.post(f"/api/media/{mid}/restore", headers=headers)
    assert _tags(mid) == ["keep"]


# ── 9. Non-régression du contrat ───────────────────────────────────────

def test_default_list_contract_unchanged(client, make_token, media_storage):
    """Sans paramètre, le contrat de la liste reste inchangé (+ champ tags)."""
    headers = _headers(make_token, "tag-reg")
    ids = [_upload(client, headers, f"d{i}".encode(), filename=f"f{i}") for i in range(3)]
    body = client.get("/api/media", headers=headers).get_json()
    assert set(body.keys()) == {"items", "total", "page", "limit"}
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]] == sorted(ids, reverse=True)
    assert body["items"][0]["tags"] == []


def test_folder_and_favorite_filters_still_work(client, make_token, media_storage):
    """Non-régression : les filtres dossiers et favoris ne sont pas perturbés."""
    headers = _headers(make_token, "tag-nonreg")
    a = _upload(client, headers, b"a", filename="a", subfolder="x")
    b = _upload(client, headers, b"b", filename="b", subfolder="y")
    client.post(f"/api/media/{a}/favorite", json={"favorite": True}, headers=headers)

    assert _list_ids(client, headers, "?favorite=1") == [a]
    assert _list_ids(client, headers, "?subfolders=x") == [a]
    assert set(_list_ids(client, headers, "?subfolders=x&subfolders=y")) == {a, b}
    folders = client.get("/api/media/folders", headers=headers).get_json()["folders"]
    assert {f["subfolder"] for f in folders} == {"x", "y"}
