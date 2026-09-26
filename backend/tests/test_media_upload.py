"""Tests de la sauvegarde serveur des médias (``routes/media.py``).

Couvre le contrat de l'upload média chunké du node « AIH save media » :
  1. auth obligatoire (401 sans token) sur init/chunk/complete/list/download ;
  2. upload complet → fichier présent dans le storage sous ``media/<user>/…`` ;
  3. ISOLATION : deux utilisateurs → dossiers distincts, aucune fuite croisée ;
  4. sanitization stricte (subfolder/filename/user_id) : confinement anti
     path-traversal ;
  5. collision de nom → suffixe ``_0001`` ;
  6. kind/ext invalides → 400 ; taille dépassée → 413 ; chunks manquants → 400 ;
  7. PROMPT et WORKFLOW persistés et relus (colonnes + réponse JSON) ;
  8. téléchargement réservé au propriétaire (403 pour un autre utilisateur).

Contrôles négatifs : les tests marqués « NEGATIVE » échouent si l'auth ou le
confinement étaient retirés (assertions strictes sur 401 / absence de ``..``).
"""

import io

import pytest
import storage as storage_module
from routes.media import _assert_safe_relpath, _build_remote_path
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


def _headers(make_token, user_id="test-user-123"):
    _ensure_user(user_id)
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _init(client, headers, *, kind="image", ext=".png", size=5, filename="clip",
          subfolder="2025-01-01", prompt="", workflow=""):
    return client.post(
        "/api/media/init",
        json={
            "kind": kind, "ext": ext, "size": size, "filename": filename,
            "subfolder": subfolder, "prompt": prompt, "workflow": workflow,
        },
        headers=headers,
    )


def _upload(client, headers, content=b"hello", **kw):
    """Init + chunks + complete ; retourne la réponse ``complete``."""
    r = _init(client, headers, size=len(content), **kw)
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    upload_id = data["upload_id"]
    chunk_size = data["chunk_size"]
    for i in range(data["total_chunks"]):
        chunk = content[i * chunk_size:(i + 1) * chunk_size]
        rc = client.post(
            "/api/media/chunk",
            data={
                "upload_id": upload_id,
                "chunk_index": str(i),
                "data": (io.BytesIO(chunk), f"{kw.get('filename', 'clip')}{kw.get('ext', '.png')}"),
            },
            headers=headers,
        )
        assert rc.status_code == 200, rc.get_data(as_text=True)
    rc = client.post("/api/media/complete", json={"upload_id": upload_id}, headers=headers)
    return rc


# ── Isolation du storage ───────────────────────────────────────────────

@pytest.fixture()
def media_storage(tmp_path, monkeypatch):
    """LocalStorage dans un répertoire temporaire (aucune écriture dans le repo)."""
    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    yield st


# ── 1. Auth obligatoire ────────────────────────────────────────────────

def test_init_requires_auth(client):
    """NEGATIVE : sans token, /api/media/init refuse (401)."""
    r = client.post("/api/media/init", json={"kind": "image", "ext": ".png", "size": 1, "filename": "x"})
    assert r.status_code == 401


def test_chunk_requires_auth(client):
    r = client.post("/api/media/chunk", data={"upload_id": "x", "chunk_index": "0"})
    assert r.status_code == 401


def test_complete_requires_auth(client):
    r = client.post("/api/media/complete", json={"upload_id": "x"})
    assert r.status_code == 401


def test_list_and_download_require_auth(client):
    assert client.get("/api/media").status_code == 401
    assert client.get("/api/media/1/download").status_code == 401


# ── 2. Upload complet + storage ────────────────────────────────────────

def test_full_upload_stored_under_media_user(client, make_token, media_storage):
    headers = _headers(make_token, "media-user-a")
    r = _upload(client, headers, content=b"\x89PNG-fake-bytes", kind="image", ext=".png")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()

    assert body["path"].startswith("media/media-user-a/2025-01-01/")
    assert body["filename"].endswith(".png")
    assert body["subfolder"] == "2025-01-01"
    assert body["size"] == len(b"\x89PNG-fake-bytes")
    assert body["url"] == f"/api/media/{body['id']}/download"
    assert media_storage.exists(body["path"]) is True


def test_upload_preserves_bytes(client, make_token, media_storage):
    headers = _headers(make_token, "media-user-bytes")
    payload = b"0123456789" * 100
    r = _upload(client, headers, content=payload, kind="video", ext=".mp4", filename="vid")
    assert r.status_code == 200, r.get_data(as_text=True)
    path = r.get_json()["path"]

    # Relire le fichier depuis le storage abstrait.
    local = str(media_storage._full_path(path))
    with open(local, "rb") as f:
        assert f.read() == payload


# ── 3. Isolation entre utilisateurs ────────────────────────────────────

def test_users_isolated_directories(client, make_token, media_storage):
    headers_a = _headers(make_token, "media-alice")
    headers_b = _headers(make_token, "media-bob")

    ra = _upload(client, headers_a, content=b"AAAA", filename="same", subfolder="shared")
    rb = _upload(client, headers_b, content=b"BBBB", filename="same", subfolder="shared")
    assert ra.status_code == 200 and rb.status_code == 200

    pa = ra.get_json()["path"]
    pb = rb.get_json()["path"]
    assert pa.startswith("media/media-alice/")
    assert pb.startswith("media/media-bob/")
    assert pa != pb

    # NEGATIVE : le fichier d'Alice n'est pas dans le dossier de Bob.
    assert not pa.startswith("media/media-bob/")
    assert media_storage.exists(pa) and media_storage.exists(pb)


def test_cross_user_upload_chunk_forbidden(client, make_token, media_storage):
    """NEGATIVE : un autre utilisateur ne peut pas pousser de chunk."""
    headers_a = _headers(make_token, "media-owner")
    headers_b = _headers(make_token, "media-intruder")

    r = _init(client, headers_a, size=3, filename="secret")
    upload_id = r.get_json()["upload_id"]

    rc = client.post(
        "/api/media/chunk",
        data={"upload_id": upload_id, "chunk_index": "0", "data": (io.BytesIO(b"abc"), "x.png")},
        headers=headers_b,
    )
    assert rc.status_code == 403


# ── 4. Sanitization / confinement ──────────────────────────────────────

def test_build_remote_path_confines_traversal():
    """NEGATIVE : la construction neutralise ``..`` et garde le chemin relatif."""
    p = _build_remote_path("../../etc/passwd", "../../../tmp", "..\\..\\evil", ".png")
    assert ".." not in p
    assert "\\" not in p
    assert p.startswith("media/")
    assert p.endswith("/evil.png")
    assert p == p.lstrip("/")


def test_assert_safe_relpath_rejects_dangerous_paths():
    """NEGATIVE : le garde-fou refuse les chemins qui s'échappent."""
    for bad in ("/etc/passwd", "a/../../b", "a//b", "", "../x", "a\\b"):
        with pytest.raises(ValueError):
            _assert_safe_relpath(bad)
    assert _assert_safe_relpath("media/u/sub/name.png") is True


def test_upload_with_traversal_payload_is_confined(client, make_token, media_storage):
    headers = _headers(make_token, "media-trav")
    r = _upload(
        client, headers, content=b"data",
        kind="image", ext=".png", filename="..\\..\\evil", subfolder="../../etc",
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    path = r.get_json()["path"]
    assert ".." not in path
    assert path.startswith("media/media-trav/")
    assert path.endswith("/evil.png")
    assert media_storage.exists(path)


# ── 5. Collision → _0001 ───────────────────────────────────────────────

def test_collision_suffix(client, make_token, media_storage):
    headers = _headers(make_token, "media-collide")
    r1 = _upload(client, headers, content=b"first", filename="pic", subfolder="d", ext=".png")
    r2 = _upload(client, headers, content=b"second", filename="pic", subfolder="d", ext=".png")
    assert r1.status_code == 200 and r2.status_code == 200

    p1 = r1.get_json()["path"]
    p2 = r2.get_json()["path"]
    assert p1.endswith("/pic.png")
    assert p2.endswith("/pic_0001.png")
    assert media_storage.exists(p1) and media_storage.exists(p2)


# ── 6. Validations d'entrée ────────────────────────────────────────────

def test_invalid_kind_rejected(client, make_token):
    headers = _headers(make_token, "media-val")
    r = _init(client, headers, kind="exe")
    assert r.status_code == 400


def test_invalid_ext_for_kind_rejected(client, make_token):
    headers = _headers(make_token, "media-val")
    r = _init(client, headers, kind="image", ext=".mp4")
    assert r.status_code == 400


def test_too_large_rejected(client, make_token, monkeypatch):
    monkeypatch.setenv("AIH_MEDIA_MAX_SIZE", "10")
    headers = _headers(make_token, "media-val")
    r = _init(client, headers, size=100, kind="image", ext=".png")
    assert r.status_code == 413


def test_missing_chunks_rejected(client, make_token, media_storage):
    headers = _headers(make_token, "media-val")
    r = _init(client, headers, size=3, filename="x")
    upload_id = r.get_json()["upload_id"]
    rc = client.post("/api/media/complete", json={"upload_id": upload_id}, headers=headers)
    assert rc.status_code == 400


# ── 7. Prompt + workflow persistés ─────────────────────────────────────

def test_prompt_and_workflow_persisted(client, make_token, media_storage):
    headers = _headers(make_token, "media-meta")
    r = _upload(
        client, headers, content=b"media",
        kind="image", ext=".png", filename="meta",
        prompt="a cat on a mat", workflow='{"nodes": [1, 2]}',
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["has_prompt"] is True
    assert body["has_workflow"] is True

    from routes.helpers import get_db

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT prompt_text, workflow_json, has_prompt, has_workflow FROM media_files WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["prompt_text"] == "a cat on a mat"
    assert row["workflow_json"] == '{"nodes": [1, 2]}'
    assert row["has_prompt"] == 1
    assert row["has_workflow"] == 1


def test_missing_metadata_flags_false(client, make_token, media_storage):
    headers = _headers(make_token, "media-meta2")
    r = _upload(client, headers, content=b"media", kind="audio", ext=".wav", filename="snd")
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_prompt"] is False
    assert body["has_workflow"] is False


# ── 8. Téléchargement / propriété ──────────────────────────────────────

def test_download_owner_only(client, make_token, media_storage):
    headers_a = _headers(make_token, "media-dl-a")
    headers_b = _headers(make_token, "media-dl-b")
    r = _upload(client, headers_a, content=b"secret-media", kind="image", ext=".png", filename="dl")
    media_id = r.get_json()["id"]

    # NEGATIVE : un autre utilisateur reçoit 403 (pas de fuite).
    rb = client.get(f"/api/media/{media_id}/download", headers=headers_b)
    assert rb.status_code == 403

    ra = client.get(f"/api/media/{media_id}/download", headers=headers_a)
    assert ra.status_code == 200
    assert ra.data == b"secret-media"


def test_list_returns_only_own_media(client, make_token, media_storage):
    headers_a = _headers(make_token, "media-list-a")
    headers_b = _headers(make_token, "media-list-b")
    _upload(client, headers_a, content=b"a", filename="only-a", subfolder="s")
    _upload(client, headers_b, content=b"b", filename="only-b", subfolder="s")

    ra = client.get("/api/media", headers=headers_a)
    assert ra.status_code == 200
    items = ra.get_json()["items"]
    assert len(items) == 1
    assert items[0]["filename"].startswith("only-a")


def test_download_unknown_id_404(client, make_token, media_storage):
    headers = _headers(make_token, "media-404")
    assert client.get("/api/media/999999/download", headers=headers).status_code == 404
