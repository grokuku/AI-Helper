"""Tests de la CORBEILLE (soft delete) des médias (``routes/media.py``) :

  - DELETE /api/media/<id>       : soft delete (propriétaire/admin, 403/404) ;
  - POST   /api/media/delete     : corbeille GROUPÉE (récap trashed/skipped) ;
  - POST   /api/media/<id>/restore + POST /api/media/restore (groupée) ;
  - DELETE /api/media/<id>/purge  + POST /api/media/purge (groupée, définitive) ;
  - GET    /api/media            : exclusion par défaut + filtre ``status`` ;
  - accès vignette/métadonnées/download d'un média corbeillé au propriétaire.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_soft_delete_cross_user_forbidden`` : retirer la garde d'autorisation
    ferait passer un 403 attendu → échec ;
  - ``test_trashed_excluded_from_default_list`` : oublier d'exclure les
    corbeillés de la liste par défaut → le média ressortirait → échec.
"""

import io

import pytest
import routes.media as media_module
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
    yield st


# ── 1. Auth / autorisation (contrôles NÉGATIFS) ────────────────────────

def test_trash_routes_require_auth(client):
    """NEGATIVE : sans token, toutes les routes corbeille refusent (401)."""
    assert client.delete("/api/media/1").status_code == 401
    assert client.post("/api/media/delete", json={"ids": [1]}).status_code == 401
    assert client.post("/api/media/1/restore").status_code == 401
    assert client.post("/api/media/restore", json={"ids": [1]}).status_code == 401
    assert client.delete("/api/media/1/purge").status_code == 401
    assert client.post("/api/media/purge", json={"ids": [1]}).status_code == 401


def test_soft_delete_cross_user_forbidden(client, make_token, media_storage):
    """NEGATIVE : un autre utilisateur ne peut PAS corbeiller le média d'autrui.

    Garde d'autorisation retirée → ce test renverrait 200 au lieu de 403 → ROUGE.
    """
    headers_a = _headers(make_token, "trash-a")
    headers_b = _headers(make_token, "trash-b")
    mid = _upload(client, headers_a, b"data-a", filename="priv")

    assert client.delete(f"/api/media/{mid}", headers=headers_b).status_code == 403
    assert client.post(f"/api/media/{mid}/restore", headers=headers_b).status_code == 403
    assert client.delete(f"/api/media/{mid}/purge", headers=headers_b).status_code == 403
    # Le média n'a pas bougé (toujours vivant, fichier intact).
    row = _row(mid)
    assert row["status"] == "complete"
    assert media_storage.exists(row["final_path"])


def test_soft_delete_unknown_id_404(client, make_token, media_storage):
    headers = _headers(make_token, "trash-404")
    assert client.delete("/api/media/999999", headers=headers).status_code == 404
    assert client.post("/api/media/999999/restore", headers=headers).status_code == 404
    assert client.delete("/api/media/999999/purge", headers=headers).status_code == 404


def test_admin_can_trash_and_restore_others(client, make_token, media_storage):
    """Un admin peut corbeiller/restaurer/purger le média d'autrui."""
    headers_owner = _headers(make_token, "trash-owner", role="user")
    headers_admin = _headers(make_token, "trash-admin", role="admin")
    mid = _upload(client, headers_owner, b"own", filename="o")

    assert client.delete(f"/api/media/{mid}", headers=headers_admin).status_code == 200
    assert _row(mid)["status"] == "trashed"
    assert client.post(f"/api/media/{mid}/restore", headers=headers_admin).status_code == 200
    assert _row(mid)["status"] == "complete"


# ── 2. Soft delete : fichier conservé + liste par défaut ───────────────

def test_soft_delete_keeps_file_and_sets_metadata(client, make_token, media_storage):
    headers = _headers(make_token, "trash-keep")
    mid = _upload(client, headers, _png_bytes(), filename="keep")
    row_before = _row(mid)

    resp = client.delete(f"/api/media/{mid}", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["id"] == mid
    assert body["trashed"] is True
    assert body["status"] == "trashed"
    assert body["trashed_at"]

    row = _row(mid)
    assert row["status"] == "trashed"
    assert row["trashed_at"]
    # Le fichier n'est PAS supprimé du storage (restaurable).
    assert media_storage.exists(row_before["final_path"])
    assert storage_module.get_storage().exists(row["final_path"])


def test_trashed_excluded_from_default_list(client, make_token, media_storage):
    """NEGATIVE : un média corbeillé disparaît de la liste par défaut.

    Si l'exclusion était oubliée, le média corbeillé ressortirait → ROUGE.
    """
    headers = _headers(make_token, "trash-list")
    live = _upload(client, headers, b"live", filename="z-live")
    gone = _upload(client, headers, b"gone", filename="a-gone")
    assert client.delete(f"/api/media/{gone}", headers=headers).status_code == 200

    default_ids = _list_ids(client, headers)
    assert gone not in default_ids
    assert live in default_ids
    # ``status=complete`` explicite ≡ défaut.
    assert gone not in _list_ids(client, headers, "?status=complete")


def test_trashed_reappears_with_status_filter(client, make_token, media_storage):
    headers = _headers(make_token, "trash-filter")
    live = _upload(client, headers, b"live", filename="z-live")
    gone = _upload(client, headers, b"gone", filename="a-gone")
    client.delete(f"/api/media/{gone}", headers=headers)

    trashed_ids = _list_ids(client, headers, "?status=trashed")
    assert trashed_ids == [gone]

    all_ids = _list_ids(client, headers, "?status=all")
    assert set(all_ids) == {live, gone}

    # L'item corbeillé expose son état à l'UI.
    trash_items = client.get("/api/media?status=trashed", headers=headers).get_json()["items"]
    assert trash_items[0]["trashed"] is True
    assert trash_items[0]["status"] == "trashed"
    assert trash_items[0]["trashed_at"]

    # Statut inconnu → 400 (pas de fallback silencieux).
    assert client.get("/api/media?status=nope", headers=headers).status_code == 400


def test_soft_delete_is_idempotent(client, make_token, media_storage):
    """Un 2e delete ne change pas l'horodatage d'origine."""
    headers = _headers(make_token, "trash-idem")
    mid = _upload(client, headers, b"x", filename="i")
    assert client.delete(f"/api/media/{mid}", headers=headers).status_code == 200
    first = _row(mid)["trashed_at"]
    assert client.delete(f"/api/media/{mid}", headers=headers).status_code == 200
    assert _row(mid)["trashed_at"] == first


# ── 3. Restauration ───────────────────────────────────────────────────

def test_restore_puts_back_in_default_list(client, make_token, media_storage):
    headers = _headers(make_token, "restore-1")
    mid = _upload(client, headers, b"r", filename="r")
    client.delete(f"/api/media/{mid}", headers=headers)
    assert mid not in _list_ids(client, headers)

    resp = client.post(f"/api/media/{mid}/restore", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["trashed"] is False
    assert body["status"] == "complete"
    assert body["trashed_at"] == ""

    row = _row(mid)
    assert row["status"] == "complete"
    assert row["trashed_at"] is None
    assert mid in _list_ids(client, headers)


# ── 4. Purge (définitive) ─────────────────────────────────────────────

def test_purge_removes_file_thumb_and_row(client, make_token, media_storage):
    headers = _headers(make_token, "purge-1")
    mid = _upload(client, headers, _png_bytes(400, 250), filename="p")
    row = _row(mid)
    final_path = row["final_path"]

    # Génère et cache la vignette (clé canonique de /thumbnail).
    assert client.get(f"/api/media/{mid}/thumbnail", headers=headers).status_code == 200
    thumb_path = media_module._thumbnail_cache_path(row, 256)
    assert media_storage.exists(thumb_path)

    client.delete(f"/api/media/{mid}", headers=headers)  # corbeille
    resp = client.delete(f"/api/media/{mid}/purge", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["purged"] is True

    # Fichier, vignette et ligne : tout a disparu (aucun orphelin).
    assert not media_storage.exists(final_path)
    assert not media_storage.exists(thumb_path)
    assert _row(mid) is None
    assert mid not in _list_ids(client, headers, "?status=all")
    # Purge d'un id désormais inexistant → 404.
    assert client.delete(f"/api/media/{mid}/purge", headers=headers).status_code == 404


# ── 5. Accès aux médias corbeillés (choix documenté) ──────────────────

def test_trashed_media_still_served_to_owner(client, make_token, media_storage):
    """Le propriétaire voit vignette/métadonnées/download d'un média corbeillé."""
    headers = _headers(make_token, "trash-serve")
    other = _headers(make_token, "trash-other")
    mid = _upload(client, headers, _png_bytes(), filename="srv")
    client.delete(f"/api/media/{mid}", headers=headers)

    assert client.get(f"/api/media/{mid}/thumbnail", headers=headers).status_code == 200
    assert client.get(f"/api/media/{mid}/metadata", headers=headers).status_code == 200
    assert client.get(f"/api/media/{mid}/download", headers=headers).status_code == 200
    # Toujours protégé vis-à-vis d'un tiers (403).
    assert client.get(f"/api/media/{mid}/thumbnail", headers=other).status_code == 403


# ── 6. Variantes groupées ─────────────────────────────────────────────

def test_bulk_delete_recap_and_partial_failure(client, make_token, media_storage):
    headers_a = _headers(make_token, "bulk-a")
    headers_b = _headers(make_token, "bulk-b")
    a1 = _upload(client, headers_a, b"a1", filename="a1")
    a2 = _upload(client, headers_a, b"a2", filename="a2")
    b1 = _upload(client, headers_b, b"b1", filename="b1")

    resp = client.post(
        "/api/media/delete",
        json={"ids": [a1, a2, b1, 999999]},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["trashed"] == 2
    assert body["skipped"] == [b1, 999999]

    # Aucun échec partiel : les médias autorisés sont bien corbeillés, B intact.
    assert _row(a1)["status"] == "trashed"
    assert _row(a2)["status"] == "trashed"
    assert _row(b1)["status"] == "complete"


def test_bulk_restore_and_purge(client, make_token, media_storage):
    headers = _headers(make_token, "bulk-rp")
    m1 = _upload(client, headers, b"m1", filename="m1")
    m2 = _upload(client, headers, b"m2", filename="m2")
    paths = {m1: _row(m1)["final_path"], m2: _row(m2)["final_path"]}

    client.post("/api/media/delete", json={"ids": [m1, m2]}, headers=headers)
    r = client.post("/api/media/restore", json={"ids": [m1, m2, 888888]}, headers=headers)
    assert r.status_code == 200
    assert r.get_json() == {"restored": 2, "skipped": [888888]}
    assert _row(m1)["status"] == "complete" and _row(m1)["trashed_at"] is None

    client.post("/api/media/delete", json={"ids": [m1, m2]}, headers=headers)
    p = client.post("/api/media/purge", json={"ids": [m1, m2]}, headers=headers)
    assert p.status_code == 200
    assert p.get_json() == {"purged": 2, "skipped": []}
    assert _row(m1) is None and _row(m2) is None
    assert not media_storage.exists(paths[m1])
    assert not media_storage.exists(paths[m2])


def test_bulk_invalid_body_400(client, make_token, media_storage):
    headers = _headers(make_token, "bulk-bad")
    assert client.post("/api/media/delete", json={}, headers=headers).status_code == 400
    assert client.post("/api/media/delete", json={"ids": "nope"}, headers=headers).status_code == 400
    assert client.post("/api/media/restore", json={}, headers=headers).status_code == 400
    assert client.post("/api/media/purge", json={}, headers=headers).status_code == 400
    # Liste vide : valide, aucun effet, récap vide.
    r = client.post("/api/media/delete", json={"ids": []}, headers=headers)
    assert r.status_code == 200
    assert r.get_json() == {"trashed": 0, "skipped": []}


# ── 7. Non-régression du contrat de liste ─────────────────────────────

def test_default_list_contract_unchanged(client, make_token, media_storage):
    """Sans paramètre, le contrat de la liste reste inchangé (+ champs corbeille)."""
    headers = _headers(make_token, "trash-reg")
    ids = [_upload(client, headers, f"d{i}".encode(), filename=f"f{i}") for i in range(3)]

    body = client.get("/api/media", headers=headers).get_json()
    assert set(body.keys()) == {"items", "total", "page", "limit"}
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]] == sorted(ids, reverse=True)
    item = body["items"][0]
    assert item["trashed"] is False
    assert item["status"] == "complete"
    assert item["trashed_at"] == ""
