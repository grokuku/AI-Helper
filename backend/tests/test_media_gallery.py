"""Tests des routes « galerie » des médias (``routes/media.py``) :

  - GET /api/media/<id>/thumbnail : auth, propriété, cache serveur (génération
    unique + persistance storage), cache navigateur (ETag/Cache-Control/304),
    ``?size=`` borné, dégradation propre sans Pillow/ffmpeg ;
  - GET /api/media/<id>/metadata  : valeurs images (Pillow), prompt/workflow,
    backfill paresseux persistant ;
  - GET /api/media (enrichie)     : filtres kind/subfolder/q/from-to + tri,
    ET non-régression en l'absence de paramètres.

Contrôles NÉGATIFS : les tests d'auth (401), de propriété (403 croisé) et de
dégradation échouent si l'auth, le confinement ou la garde d'outils étaient
retirés.
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
            filename="clip", subfolder="2025-01-01", prompt="", workflow=""):
    """Init + 1 chunk + complete ; retourne la réponse ``complete``."""
    r = client.post(
        "/api/media/init",
        json={"kind": kind, "ext": ext, "size": len(content), "filename": filename,
              "subfolder": subfolder, "prompt": prompt, "workflow": workflow},
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
    return client.post("/api/media/complete", json={"upload_id": data["upload_id"]}, headers=headers)


def _row(media_id):
    from routes.helpers import get_db

    conn = get_db()
    try:
        return conn.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)).fetchone()
    finally:
        conn.close()


@pytest.fixture()
def media_storage(tmp_path, monkeypatch):
    """LocalStorage isolé dans un répertoire temporaire (aucune écriture repo)."""
    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    yield st


# ── 1. Auth / propriété (contrôles NÉGATIFS) ───────────────────────────

def test_thumbnail_and_metadata_require_auth(client):
    """NEGATIVE : sans token, les deux routes refusent (401)."""
    assert client.get("/api/media/1/thumbnail").status_code == 401
    assert client.get("/api/media/1/metadata").status_code == 401


def test_thumbnail_unknown_id_404(client, make_token, media_storage):
    headers = _headers(make_token, "gal-404")
    assert client.get("/api/media/999999/thumbnail", headers=headers).status_code == 404
    assert client.get("/api/media/999999/metadata", headers=headers).status_code == 404


def test_thumbnail_cross_user_forbidden(client, make_token, media_storage):
    """NEGATIVE : la vignette d'un média d'autrui est refusée (403)."""
    headers_a = _headers(make_token, "gal-a")
    headers_b = _headers(make_token, "gal-b")
    r = _upload(client, headers_a, _png_bytes(), filename="priv")
    mid = r.get_json()["id"]

    assert client.get(f"/api/media/{mid}/thumbnail", headers=headers_b).status_code == 403
    assert client.get(f"/api/media/{mid}/metadata", headers=headers_b).status_code == 403
    assert client.get(f"/api/media/{mid}/thumbnail", headers=headers_a).status_code == 200


def test_thumbnail_admin_allowed(client, make_token, media_storage):
    """Un admin voit la vignette d'un média d'autrui (même règle que download)."""
    headers_owner = _headers(make_token, "gal-owner", role="user")
    headers_admin = _headers(make_token, "gal-admin", role="admin")
    r = _upload(client, headers_owner, _png_bytes(), filename="adm")
    mid = r.get_json()["id"]
    assert client.get(f"/api/media/{mid}/thumbnail", headers=headers_admin).status_code == 200


# ── 2. Vignette : génération + cache serveur ──────────────────────────

def test_thumbnail_generated_once_then_served_from_cache(client, make_token, media_storage, monkeypatch):
    calls = {"n": 0}
    real = media_module._generate_thumbnail

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(media_module, "_generate_thumbnail", counting)

    headers = _headers(make_token, "thumb-cache")
    r = _upload(client, headers, _png_bytes(400, 250), filename="pic")
    mid = r.get_json()["id"]

    r1 = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert r1.status_code == 200, r1.get_data(as_text=True)
    assert r1.mimetype == "image/jpeg"
    assert calls["n"] == 1

    # Persistée dans le storage sous media/.thumbs/<user>/…
    thumb_path = media_module._thumbnail_cache_path(_row(mid), 256)
    assert thumb_path.startswith("media/.thumbs/")
    assert media_storage.exists(thumb_path)
    assert thumb_path.endswith("_256.jpg")

    # 2e appel : servie depuis le cache → aucune régénération.
    r2 = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert r2.status_code == 200
    assert r2.mimetype == "image/jpeg"
    assert calls["n"] == 1


def test_thumbnail_cache_headers_and_conditional_304(client, make_token, media_storage):
    headers = _headers(make_token, "thumb-http")
    r = _upload(client, headers, _png_bytes(), filename="http")
    mid = r.get_json()["id"]

    r1 = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert r1.status_code == 200
    assert r1.headers.get("ETag")
    cache_control = r1.headers.get("Cache-Control", "")
    assert "private" in cache_control
    assert "immutable" in cache_control
    assert "max-age" in cache_control

    # Revalidation : ETag identique → 304 sans corps.
    r2 = client.get(
        f"/api/media/{mid}/thumbnail",
        headers={**headers, "If-None-Match": r1.headers["ETag"]},
    )
    assert r2.status_code == 304
    assert r2.data == b""


def test_thumbnail_size_snapped_and_cached_per_size(client, make_token, media_storage):
    headers = _headers(make_token, "thumb-size")
    r = _upload(client, headers, _png_bytes(800, 600), filename="sz")
    mid = r.get_json()["id"]
    row = _row(mid)

    # 999 → snapé à 512 ; cache distinct de la taille par défaut.
    assert client.get(f"/api/media/{mid}/thumbnail?size=999", headers=headers).status_code == 200
    assert media_storage.exists(media_module._thumbnail_cache_path(row, 512))
    assert not media_storage.exists(media_module._thumbnail_cache_path(row, 256))

    assert client.get(f"/api/media/{mid}/thumbnail?size=128", headers=headers).status_code == 200
    assert media_storage.exists(media_module._thumbnail_cache_path(row, 128))

    # Taille non numérique → 400 (bornage strict).
    assert client.get(f"/api/media/{mid}/thumbnail?size=abc", headers=headers).status_code == 400


# ── 3. Vignette : dégradation propre ──────────────────────────────────

def test_thumbnail_degradation_without_tools(client, make_token, media_storage, monkeypatch):
    """NEGATIVE : sans Pillow ni ffmpeg, 404 structuré (jamais de crash).

    Vérifie aussi le champ ``reason`` (diagnostic) et que la liste expose
    ``thumb_available=False`` (le front affiche alors un état d'erreur).
    """
    monkeypatch.setattr(media_module, "_pillow_available", lambda: False)
    monkeypatch.setattr(media_module, "_ffmpeg_path", lambda: None)

    headers = _headers(make_token, "thumb-deg")
    r = _upload(client, headers, _png_bytes(), filename="deg")
    mid = r.get_json()["id"]

    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == "thumbnail_unavailable"
    assert body["reason"] == "no_tools"
    assert body["kind"] == "image"
    # La liste annonce l'indisponibilité (le front n'invente pas d'URL).
    item = client.get("/api/media", headers=headers).get_json()["items"][0]
    assert item["thumb_available"] is False
    # Rien n'a été mis en cache.
    assert not media_storage.exists(media_module._thumbnail_cache_path(_row(mid), 256))


def test_thumbnail_error_headers_forbid_durable_cache(client, make_token, media_storage, monkeypatch):
    """ANTI-CACHE NÉGATIF : les ERREURS de vignette ne sont PAS cacheables.

    Un 404 est « heuristiquement cacheable » (RFC 7234) : sans ``no-store`` le
    navigateur (ou un cache intermédiaire) pourrait resservir le 404 APRÈS
    correction (Pillow installé) → vignette définitivement cassée. On impose
    donc ``no-store`` et l'absence d'ETag/entête de cache longue.
    """
    monkeypatch.setattr(media_module, "_pillow_available", lambda: False)
    monkeypatch.setattr(media_module, "_ffmpeg_path", lambda: None)
    headers = _headers(make_token, "thumb-nocache")
    r = _upload(client, headers, _png_bytes(), filename="nc")
    mid = r.get_json()["id"]

    # 404 « thumbnail_unavailable » : no-store, pas d'ETag, pas de cache long.
    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc
    assert "immutable" not in cc and "31536000" not in cc
    assert resp.headers.get("ETag") is None

    # 400 taille invalide : idem.
    resp400 = client.get(f"/api/media/{mid}/thumbnail?size=abc", headers=headers)
    assert resp400.status_code == 400
    assert "no-store" in resp400.headers.get("Cache-Control", "")

    # 404 id inconnu (garde d'accès de la route) : idem.
    resp404 = client.get("/api/media/999999/thumbnail", headers=headers)
    assert resp404.status_code == 404
    assert "no-store" in resp404.headers.get("Cache-Control", "")


def test_thumbnail_regenerated_after_tool_recovery(client, make_token, media_storage, monkeypatch):
    """PREUVE « est-ce qu'elle va se recalculer ? » : OUI, aucune mémoire d'échec.

    Échec (Pillow/ffmpeg masqués → 404 ``no_tools``, rien de persisté) PUIS
    retour des outils → la vignette est GÉNÉRÉE et PERSISTÉE au prochain appel.
    Contrôle négatif : juste après l'échec, le cache storage est vide (donc
    aucun « pas de vignette » mémorisé).
    """
    headers = _headers(make_token, "thumb-regen")
    r = _upload(client, headers, _png_bytes(400, 250), filename="rg")
    mid = r.get_json()["id"]
    thumb_path = media_module._thumbnail_cache_path(_row(mid), 256)

    # 1) Outils masqués → 404 structuré, AUCUN cache négatif écrit.
    monkeypatch.setattr(media_module, "_pillow_available", lambda: False)
    monkeypatch.setattr(media_module, "_ffmpeg_path", lambda: None)
    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    assert resp.get_json()["reason"] == "no_tools"
    assert not media_storage.exists(thumb_path)

    # 2) Outils « revenus » → régénération immédiate + persistance du cache.
    def _pillow_real():
        try:
            import PIL.Image  # noqa: F401
            return True
        except Exception:
            return False

    monkeypatch.setattr(media_module, "_pillow_available", _pillow_real)
    resp2 = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp2.status_code == 200
    assert resp2.mimetype == "image/jpeg"
    assert resp2.data[:3] == b"\xff\xd8\xff"  # magic bytes JPEG (SOI)
    assert media_storage.exists(thumb_path)


def test_thumbnail_nominal_returns_real_jpeg(client, make_token, media_storage):
    """Cas NOMINAL : une vignette JPEG RÉELLE est renvoyée (Content-Type + bytes)."""
    headers = _headers(make_token, "thumb-nominal")
    r = _upload(client, headers, _png_bytes(500, 320), filename="nom")
    mid = r.get_json()["id"]

    resp = client.get(f"/api/media/{mid}/thumbnail?size=256", headers=headers)
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.headers["Content-Type"].startswith("image/")
    assert resp.data[:3] == b"\xff\xd8\xff"  # magic bytes JPEG (SOI)
    assert len(resp.data) > 100


def test_thumbnail_reason_source_unavailable(client, make_token, media_storage):
    """reason=source_unavailable : le média source n'est pas lisible du storage."""
    headers = _headers(make_token, "thumb-src")
    r = _upload(client, headers, _png_bytes(), filename="src")
    mid = r.get_json()["id"]
    assert media_storage.delete(_row(mid)["final_path"])  # source retirée du storage

    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == "thumbnail_unavailable"
    assert body["reason"] == "source_unavailable"


def test_thumbnail_reason_generation_failed(client, make_token, media_storage, monkeypatch):
    """reason=generation_failed : outil présent mais génération en échec."""
    headers = _headers(make_token, "thumb-genfail")
    r = _upload(client, headers, _png_bytes(), filename="gf")
    mid = r.get_json()["id"]
    monkeypatch.setattr(media_module, "_generate_thumbnail", lambda *a, **k: False)

    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    assert resp.get_json()["reason"] == "generation_failed"


def test_requirements_declare_pillow():
    """Régression : Pillow est une dépendance RUNTIME déclarée.

    En production ``run.sh`` installe ``requirements.txt`` : sans Pillow, la
    vignette renvoie 404 (``no_tools``) alors que ``/download`` fonctionne →
    toutes les vignettes de la galerie restent des placeholders.
    """
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    req = (backend / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pillow" in req
    lock = (backend / "requirements.lock.txt").read_text(encoding="utf-8").lower()
    assert "pillow" in lock


def test_audio_thumbnail_unavailable(client, make_token, media_storage):
    """Audio : pas de vignette (choix documenté) → 404 structuré."""
    headers = _headers(make_token, "thumb-audio")
    r = _upload(client, headers, b"RIFFfake", kind="audio", ext=".wav", filename="snd")
    mid = r.get_json()["id"]
    resp = client.get(f"/api/media/{mid}/thumbnail", headers=headers)
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "thumbnail_unavailable"


def test_list_exposes_thumb_and_availability(client, make_token, media_storage):
    headers = _headers(make_token, "thumb-lst")
    r = _upload(client, headers, _png_bytes(), kind="image", ext=".png", filename="ok")
    item = client.get("/api/media", headers=headers).get_json()["items"][0]
    assert item["id"] == r.get_json()["id"]
    assert item["thumb"] == f"/api/media/{item['id']}/thumbnail"
    assert item["thumb_available"] is True  # Pillow présent dans l'environnement


# ── 4. Métadonnées ────────────────────────────────────────────────────

def test_metadata_image_values_and_prompt(client, make_token, media_storage):
    headers = _headers(make_token, "meta-img")
    r = _upload(
        client, headers, _png_bytes(640, 360), kind="image", ext=".png", filename="m",
        prompt="a cat on a mat", workflow='{"nodes": [1, 2]}',
    )
    mid = r.get_json()["id"]

    body = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert body["id"] == mid
    assert body["kind"] == "image"
    assert body["filename"] == "m.png"
    assert body["width"] == 640
    assert body["height"] == 360
    assert body["ratio"] == round(640 / 360, 4)
    assert body["duration"] is None
    assert body["duration_ms"] is None
    assert body["prompt"] == "a cat on a mat"
    assert body["workflow"] == '{"nodes": [1, 2]}'
    assert body["has_prompt"] is True
    assert body["has_workflow"] is True


def test_metadata_audio_degrades_to_null(client, make_token, media_storage):
    """Sans ffprobe, les champs techniques audio restent null (pas de crash)."""
    headers = _headers(make_token, "meta-aud")
    r = _upload(client, headers, b"audio-bytes", kind="audio", ext=".wav", filename="snd")
    mid = r.get_json()["id"]
    body = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert body["kind"] == "audio"
    assert body["duration"] is None
    assert body["codec"] is None


def test_metadata_backfill_persists_once(client, make_token, media_storage, monkeypatch):
    """Un média « ancien » (colonnes NULL, meta_checked=0) est backfillé UNE fois."""
    headers = _headers(make_token, "meta-bf")
    r = _upload(client, headers, _png_bytes(300, 150), kind="image", ext=".png", filename="bf")
    mid = r.get_json()["id"]

    # Simuler une ligne héritée d'avant la migration.
    from routes.helpers import get_db
    conn = get_db()
    conn.execute(
        "UPDATE media_files SET width = NULL, height = NULL, codec = NULL, meta_checked = 0 WHERE id = ?",
        (mid,),
    )
    conn.commit()
    conn.close()

    calls = {"n": 0}
    real = media_module._extract_technical_metadata

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(media_module, "_extract_technical_metadata", counting)

    b1 = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert b1["width"] == 300 and b1["height"] == 150
    assert calls["n"] == 1
    # Persisté en base (pas seulement renvoyé).
    row = _row(mid)
    assert row["meta_checked"] == 1
    assert row["width"] == 300 and row["height"] == 150

    b2 = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert b2["width"] == 300
    assert calls["n"] == 1  # 2e appel : aucun recalcul (backfill déjà fait)


def test_metadata_backfill_repairs_legacy_row_after_tool_install(client, make_token, media_storage, monkeypatch):
    """Média « ère Pillow absent » (meta_checked=1 + technique NULL) → réparé.

    État RÉEL des uploads faits pendant l'incident : ``/complete`` marquait la
    tentative (``meta_checked=1``) mais les colonnes techniques restaient NULL.
    L'ancien déclencheur de backfill (``not meta_checked``) ne couvrait PAS ces
    lignes → dimensions manquantes À VIE dans le panneau d'infos.

    Contrôle NÉGATIF : sans outil adapté au type, on ne relit pas le fichier
    pour rien (aucun coût par requête tant que l'outil manque).
    """
    headers = _headers(make_token, "meta-legacy")
    r = _upload(client, headers, _png_bytes(320, 200), kind="image", ext=".png", filename="leg")
    mid = r.get_json()["id"]

    # Simule une ligne héritée de l'ère « Pillow absent ».
    from routes.helpers import get_db
    conn = get_db()
    conn.execute(
        "UPDATE media_files SET width = NULL, height = NULL, duration_ms = NULL, "
        "codec = NULL, meta_checked = 1 WHERE id = ?", (mid,))
    conn.commit()
    conn.close()

    calls = {"n": 0}
    real = media_module._extract_technical_metadata

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(media_module, "_extract_technical_metadata", counting)

    # NEGATIVE : outil toujours absent → aucune relecture, champs null inchangés.
    monkeypatch.setattr(media_module, "_pillow_available", lambda: False)
    monkeypatch.setattr(media_module, "_ffprobe_path", lambda: None)
    body = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert body["width"] is None and body["height"] is None
    assert calls["n"] == 0
    assert _row(mid)["meta_checked"] == 1

    # Outil « installé » (Pillow) → réparation one-shot, persistée en base.
    monkeypatch.setattr(media_module, "_pillow_available", lambda: True)
    b1 = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert b1["width"] == 320 and b1["height"] == 200
    assert calls["n"] == 1
    assert _row(mid)["width"] == 320 and _row(mid)["height"] == 200

    # 2e appel : technique désormais renseignée → aucun recalcul.
    b2 = client.get(f"/api/media/{mid}/metadata", headers=headers).get_json()
    assert b2["width"] == 320
    assert calls["n"] == 1


# ── 5. Liste enrichie : filtres + tri ─────────────────────────────────

def _seed_list(client, headers):
    _upload(client, headers, b"img-bytes", kind="image", ext=".png", filename="alpha", subfolder="a")
    _upload(client, headers, b"vid-bytes", kind="video", ext=".mp4", filename="beta", subfolder="a/b")
    _upload(client, headers, b"aud-bytes", kind="audio", ext=".wav", filename="gamma", subfolder="c")


def test_list_filters_kind_subfolder_q_range(client, make_token, media_storage):
    headers = _headers(make_token, "list-filters")
    _seed_list(client, headers)

    def names(query=""):
        body = client.get(f"/api/media{query}", headers=headers).get_json()
        return body, [i["filename"] for i in body["items"]]

    body, ns = names("?kind=video")
    assert ns == ["beta.mp4"] and body["total"] == 1

    _, ns = names("?subfolder=a")  # préfixe de segment : "a" + "a/b"
    assert sorted(ns) == ["alpha.png", "beta.mp4"]

    _, ns = names("?subfolder=a/b")  # sous-dossier exact
    assert ns == ["beta.mp4"]

    _, ns = names("?q=alp")
    assert ns == ["alpha.png"]

    _, ns = names("?q=ga")
    assert ns == ["gamma.wav"]

    # Plage created_at (tout est « maintenant ») : bornes larges/mordantes.
    assert names("?from=2099-01-01")[0]["total"] == 0
    assert names("?to=2000-01-01")[0]["total"] == 0
    assert names("?from=2000-01-01&to=2099-12-31")[0]["total"] == 3

    # Filtres invalides → 400 (pas de fallback silencieux).
    assert client.get("/api/media?kind=exe", headers=headers).status_code == 400
    assert client.get("/api/media?sort=nope", headers=headers).status_code == 400


def test_list_sort_orders(client, make_token, media_storage):
    headers = _headers(make_token, "list-sort")
    _seed_list(client, headers)

    asc = [i["filename"] for i in client.get("/api/media?sort=name_asc", headers=headers).get_json()["items"]]
    assert asc == ["alpha.png", "beta.mp4", "gamma.wav"]

    desc = [i["filename"] for i in client.get("/api/media?sort=name_desc", headers=headers).get_json()["items"]]
    assert desc == ["gamma.wav", "beta.mp4", "alpha.png"]

    # size_desc : img(9) > vid(9) … on vérifie seulement la décroissance.
    sizes = [i["size"] for i in client.get("/api/media?sort=size_desc", headers=headers).get_json()["items"]]
    assert sizes == sorted(sizes, reverse=True)


def test_list_without_params_is_backward_compatible(client, make_token, media_storage):
    """Non-régression : sans paramètres, contrat inchangé (+ champs galerie)."""
    headers = _headers(make_token, "list-reg")
    ids = []
    for i in range(3):
        r = _upload(client, headers, f"data{i}".encode(), kind="image", ext=".png", filename=f"f{i}")
        ids.append(r.get_json()["id"])

    body = client.get("/api/media", headers=headers).get_json()
    assert set(body.keys()) == {"items", "total", "page", "limit"}
    assert body["total"] == 3
    assert body["page"] == 1 and body["limit"] == 50
    assert [i["id"] for i in body["items"]] == sorted(ids, reverse=True)  # id DESC historique

    item = body["items"][0]
    for key in ("id", "path", "filename", "subfolder", "size", "url", "created_at",
                "has_prompt", "has_workflow", "kind"):
        assert key in item
    # Champs galerie ajoutés (superset).
    assert item["thumb"] == f"/api/media/{item['id']}/thumbnail"
    assert "thumb_available" in item
