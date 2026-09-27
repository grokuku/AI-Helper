"""Tests de l'AUTO-TAGGING IA (vision) des médias (``routes/media.py``) :

  - migration ``ai_presets.supports_vision`` (défaut 0, compat arrière) ;
  - exposition + validation du flag dans l'API presets (GET liste/détail,
    POST/PUT) ;
  - POST /api/media/<id>/auto-tag : image réduite → LLM vision (MOCKÉ) → tags
    ``source='ai'`` ; parsing robuste (JSON, fences, listes à puces, objet) ;
    garantie « ne touche JAMAIS le manuel » ;
  - erreurs LLM (HTTP 4xx/5xx, timeout, réponse non-JSON, image non
    préparable) ; média non-image → ``skipped`` ; lot borné → ``batch_too_large`` ;
  - auth / 403 / 404 ; protection SSRF (hôte privé sans opt-in) ;
  - récap groupé par média.

AUCUN appel réseau réel : ``_safe_llm_post`` est monkeypatché. Contrôles
NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_manual_tag_never_overwritten`` : forcer l'écrasement → échec ;
  - ``test_negative_control_vision_filter`` : retirer le filtre
    ``supports_vision`` ferait partir un appel LLM → échec ;
  - ``test_auto_tag_ssrf_private_blocked`` : retirer la validation SSRF ferait
    émettre la requête → échec.
"""

import io
import socket

import pytest
import storage as storage_module
from PIL import Image
from routes import media as media_module
from storage import LocalStorage

# ── Helpers ────────────────────────────────────────────────────────────

def _ensure_user(user_id="test-user-123", role="user"):
    from routes.helpers import get_db

    conn = get_db()
    # ``ON CONFLICT ... DO UPDATE`` (et NON ``INSERT OR REPLACE``) : ce dernier
    # équivaut à un DELETE+INSERT et déclenche ``ON DELETE SET NULL`` sur
    # ``ai_presets.user_id`` — ce qui annulerait le lien user↔preset des tests.
    conn.execute(
        "INSERT INTO users (id, username, role) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username = excluded.username, role = excluded.role",
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


def _tag_rows(media_id):
    """Liste ``(tag, source)`` d'un média, triée par tag."""
    from routes.helpers import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT tag, source FROM media_tags WHERE media_id = ? ORDER BY tag", (media_id,)
        ).fetchall()
    finally:
        conn.close()
    return [(r["tag"], r["source"]) for r in rows]


def _seed_preset(user_id="test-user-123", name="vision", base_url="https://api.example.com",
                 model="gpt-4o", supports_vision=1, is_global=0):
    """Insère un preset directement en BDD (base_url/flag maîtrisés)."""
    _ensure_user(user_id)
    from routes.helpers import get_db

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, "
        "model, is_global, is_client_side, supports_vision) VALUES (?, ?, 'openai', ?, ?, ?, ?, 0, ?)",
        (None if is_global else user_id, name, base_url, "", model, is_global, supports_vision),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


@pytest.fixture()
def media_storage(tmp_path, monkeypatch):
    """LocalStorage isolé dans un répertoire temporaire (aucune écriture repo)."""
    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    monkeypatch.setenv("AIH_THUMB_CACHE_DIR", str(tmp_path / "thumbs"))
    yield st


# ── Mock LLM (aucun réseau) ───────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, json_data=None, json_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._json


def _llm_payload(text):
    return {"choices": [{"message": {"content": text}}]}


def _patch_llm(monkeypatch, content=None, *, status=200, json_error=False, exc=None,
               capture=None):
    """Monkeypatch ``_safe_llm_post`` (module media) et renvoie le faux POST.

    Simule aussi la résolution DNS publique (``api.example.com`` → adresse
    publique) pour que la VRAIE politique anti-SSRF accepte les presets de test
    (aucun réseau réel : le POST est remplacé). Une IP privée littérale reste
    refusée par la garde (pas de résolution DNS pour un littéral).
    """
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )

    def fake(url, json_payload, headers=None, timeout=None, **kwargs):
        if capture is not None:
            capture.append({"url": url, "payload": json_payload, "headers": headers or {}})
        if exc is not None:
            raise exc
        return _FakeResp(status_code=status, json_data=_llm_payload(content), json_error=json_error)

    monkeypatch.setattr(media_module, "_safe_llm_post", fake)
    return fake


# ── 1. Migration / colonne ─────────────────────────────────────────────

def test_supports_vision_column_default_zero(client):
    """La colonne existe, vaut 0 par défaut (compat arrière), non nulle."""
    from routes.helpers import get_db

    conn = get_db()
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(ai_presets)")}
        assert "supports_vision" in cols
        col = cols["supports_vision"]
        # notnull=1 et default "0".
        assert col[3] == 1
        assert str(col[4]) == "0"
    finally:
        conn.close()


# ── 2. Exposition du flag dans l'API presets ──────────────────────────

def test_presets_flag_exposed_default_false(client, make_token):
    headers = _headers(make_token, "vision-api")
    pid = _seed_preset("vision-api", name="classique", supports_vision=0)
    listing = client.get("/api/presets", headers=headers).get_json()
    row = next(p for p in listing if p["id"] == pid)
    assert row["supports_vision"] is False
    detail = client.get(f"/api/presets/{pid}", headers=headers).get_json()
    assert detail["supports_vision"] is False


def test_presets_create_accepts_supports_vision_true(client, make_token, monkeypatch):
    headers = _headers(make_token, "vision-create")
    with monkeypatch.context() as m:
        import socket
        m.setattr(socket, "getaddrinfo",
                  lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
        r = client.post("/api/presets", json={
            "name": "vision", "base_url": "https://api.example.com", "api_key": "k",
            "model": "gpt-4o", "supports_vision": True,
        }, headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    pid = r.get_json()["id"]
    detail = client.get(f"/api/presets/{pid}", headers=headers).get_json()
    assert detail["supports_vision"] is True


def test_presets_update_toggles_flag(client, make_token):
    headers = _headers(make_token, "vision-put")
    pid = _seed_preset("vision-put", supports_vision=0)
    # Absence de clé → inchangé (0).
    assert client.put(f"/api/presets/{pid}", json={"name": "x"}, headers=headers).status_code == 200
    assert client.get(f"/api/presets/{pid}", headers=headers).get_json()["supports_vision"] is False
    # Activation.
    r = client.put(f"/api/presets/{pid}", json={"supports_vision": 1}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert client.get(f"/api/presets/{pid}", headers=headers).get_json()["supports_vision"] is True
    # Désactivation.
    client.put(f"/api/presets/{pid}", json={"supports_vision": False}, headers=headers)
    assert client.get(f"/api/presets/{pid}", headers=headers).get_json()["supports_vision"] is False


@pytest.mark.parametrize("bad", ["yes", 2, -1, 1.5, []])
def test_presets_invalid_supports_vision_400(client, make_token, bad):
    """Validation STRICTE : toute valeur hors booléen/0/1 → 400 (POST et PUT)."""
    headers = _headers(make_token, "vision-bad")
    pid = _seed_preset("vision-bad", supports_vision=0)
    r = client.put(f"/api/presets/{pid}", json={"supports_vision": bad}, headers=headers)
    assert r.status_code == 400, (bad, r.get_data(as_text=True))
    # POST aussi.
    r2 = client.post("/api/presets", json={
        "name": "b", "base_url": "https://api.example.com", "supports_vision": bad,
    }, headers=headers)
    assert r2.status_code == 400, (bad, r2.get_data(as_text=True))


# ── 3. Parsing robuste (fonction pure) ────────────────────────────────

def test_parse_tags_json_array():
    assert media_module._vision_parse_tags('["sunset", "beach", "ocean"]') == ["sunset", "beach", "ocean"]


def test_parse_tags_markdown_fence():
    raw = '```json\n["forest", "mist", "pine trees"]\n```'
    assert media_module._vision_parse_tags(raw) == ["forest", "mist", "pine trees"]


def test_parse_tags_bullet_list():
    raw = "- sunset\n- beach\n* ocean\n1. palm tree"
    assert media_module._vision_parse_tags(raw) == ["sunset", "beach", "ocean", "palm tree"]


def test_parse_tags_object_with_tags_key():
    raw = '{"tags": ["a", "b"]}'
    assert media_module._vision_parse_tags(raw) == ["a", "b"]


def test_parse_tags_dedupes_and_drops_generic():
    raw = '["Sunset", "sunset", "image", "photo", "beach"]'
    assert media_module._vision_parse_tags(raw) == ["Sunset", "beach"]


def test_parse_tags_bounded_to_max():
    raw = '["t0","t1","t2","t3","t4","t5","t6","t7","t8","t9","t10","t11","t12","t13"]'
    assert len(media_module._vision_parse_tags(raw)) == media_module.VISION_TAG_MAX


def test_parse_tags_empty_and_invalid():
    assert media_module._vision_parse_tags("") == []
    assert media_module._vision_parse_tags("   ") == []
    assert media_module._vision_parse_tags(None) == []


def test_parse_tags_content_parts():
    """Le contenu peut être une liste de parts (format OpenAI)."""
    raw = [{"type": "text", "text": '["a", "b"]'}]
    assert media_module._vision_parse_tags(raw) == ["a", "b"]


# ── 4. Auto-tag unitaire : succès + prompt + image ────────────────────

def test_auto_tag_success_stores_ai_tags(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-ok")
    pid = _seed_preset("autotag-ok")
    mid = _upload(client, headers, _png_bytes(), filename="shot")

    capture = []
    _patch_llm(monkeypatch, '["sunset", "beach", "ocean"]', capture=capture)

    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["auto_tag"]["status"] == "tagged"
    assert body["auto_tag"]["added"] == 3
    assert body["auto_tag"]["ai_tags"] == ["sunset", "beach", "ocean"]
    assert _tag_rows(mid) == [("beach", "ai"), ("ocean", "ai"), ("sunset", "ai")]
    # Le média sérialisé reflète les tags.
    sources = {d["tag"]: d["source"] for d in body["media"]["tags_detail"]}
    assert sources == {"sunset": "ai", "beach": "ai", "ocean": "ai"}

    # Prompt EXACT + image en data-URL base64.
    assert len(capture) == 1
    sent = capture[0]["payload"]
    assert sent["messages"][0]["content"] == media_module.VISION_SYSTEM_PROMPT
    user_content = sent["messages"][1]["content"]
    assert user_content[0]["text"] == media_module.VISION_USER_PROMPT
    img_url = user_content[1]["image_url"]["url"]
    assert img_url.startswith("data:image/jpeg;base64,")
    assert len(img_url) > len("data:image/jpeg;base64,")
    assert capture[0]["url"].endswith("/chat/completions")


def test_auto_tag_api_key_used_but_never_leaked(client, make_token, media_storage,
                                                monkeypatch):
    headers = _headers(make_token, "autotag-key")
    from routes.helpers import encrypt_api_key, get_db

    _ensure_user("autotag-key")
    conn = get_db()
    enc = encrypt_api_key("sk-super-secret")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, model, "
        "is_global, supports_vision) VALUES (?, 'vk', 'openai', ?, ?, 'gpt-4o', 0, 1)",
        ("autotag-key", "https://api.example.com", enc),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()

    mid = _upload(client, headers, _png_bytes(), filename="s")
    capture = []
    _patch_llm(monkeypatch, '["a"]', capture=capture)
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    # La clé PART en Authorization (déchiffrée)…
    assert capture[0]["headers"].get("Authorization") == "Bearer sk-super-secret"
    # …mais NE fuit JAMAIS dans la réponse.
    assert "sk-super-secret" not in r.get_data(as_text=True)


# ── 5. Garantie « ne touche jamais le manuel » ────────────────────────

def test_manual_tag_never_overwritten(client, make_token, media_storage, monkeypatch):
    """NEGATIVE : un tag IA identique à un tag MANUEL reste manuel, sans doublon.

    Forcer l'écrasement (source passe à 'ai' ou doublon) ferait ROUGIR ce test.
    """
    headers = _headers(make_token, "autotag-manual")
    pid = _seed_preset("autotag-manual")
    mid = _upload(client, headers, _png_bytes(), filename="m")
    # Tag manuel préexistant (casse différente pour prouver l'unicité NOCASE).
    client.post(f"/api/media/{mid}/tags", json={"add": ["Sunset"]}, headers=headers)
    assert _tag_rows(mid) == [("Sunset", "manual")]

    _patch_llm(monkeypatch, '["sunset", "beach"]')
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = _tag_rows(mid)
    # Le tag manuel « Sunset » reste manual ; « beach » est ajouté en ai.
    assert ("Sunset", "manual") in rows
    assert ("beach", "ai") in rows
    assert sum(1 for t, _ in rows if t.lower() == "sunset") == 1  # aucun doublon


def test_auto_tag_added_counts_only_new(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-count")
    pid = _seed_preset("autotag-count")
    mid = _upload(client, headers, _png_bytes(), filename="c")
    client.post(f"/api/media/{mid}/tags", json={"add": ["beach"]}, headers=headers)
    _patch_llm(monkeypatch, '["beach", "ocean"]')
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.get_json()["auto_tag"]["added"] == 1  # « beach » déjà présent


# ── 6. Erreurs LLM ────────────────────────────────────────────────────

def test_auto_tag_llm_http_error_502(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-500")
    pid = _seed_preset("autotag-500")
    mid = _upload(client, headers, _png_bytes(), filename="e")
    _patch_llm(monkeypatch, None, status=500)
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 502
    assert r.get_json()["reason"] == "llm_error"
    assert _tag_rows(mid) == []


def test_auto_tag_llm_timeout_502(client, make_token, media_storage, monkeypatch):
    import requests

    headers = _headers(make_token, "autotag-timeout")
    pid = _seed_preset("autotag-timeout")
    mid = _upload(client, headers, _png_bytes(), filename="t")
    _patch_llm(monkeypatch, exc=requests.Timeout("slow"))
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 502
    assert r.get_json()["reason"] == "unreachable"
    assert _tag_rows(mid) == []


def test_auto_tag_non_json_response_502(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-nojson")
    pid = _seed_preset("autotag-nojson")
    mid = _upload(client, headers, _png_bytes(), filename="n")
    _patch_llm(monkeypatch, None, json_error=True)
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 502
    assert r.get_json()["reason"] == "bad_response"


def test_auto_tag_response_without_tags_skipped(client, make_token, media_storage, monkeypatch):
    """Modèle qui ne renvoie que des génériques → skipped ``no_tags`` (pas d'erreur)."""
    headers = _headers(make_token, "autotag-empty")
    pid = _seed_preset("autotag-empty")
    mid = _upload(client, headers, _png_bytes(), filename="z")
    _patch_llm(monkeypatch, '["image", "photo"]')
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["auto_tag"]["status"] == "skipped"
    assert r.get_json()["auto_tag"]["reason"] == "no_tags"
    assert _tag_rows(mid) == []


def test_auto_tag_image_prep_failure_502(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-noimg")
    pid = _seed_preset("autotag-noimg")
    mid = _upload(client, headers, _png_bytes(), filename="p")
    monkeypatch.setattr(media_module, "_vision_image_b64", lambda row, max_px=512: (None, "generation_failed"))
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 502
    assert r.get_json()["reason"] == "generation_failed"


# ── 7. Média non-image → skipped ──────────────────────────────────────

def test_auto_tag_non_image_skipped(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-video")
    pid = _seed_preset("autotag-video")
    mid = _upload(client, headers, b"vid", kind="video", ext=".mp4", filename="v")
    called = []
    _patch_llm(monkeypatch, '["x"]', capture=called)
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["auto_tag"]["status"] == "skipped"
    assert r.get_json()["auto_tag"]["reason"] == "not_image"
    assert called == []  # aucun appel LLM pour un non-image


# ── 8. Sélection du preset / erreurs de preset ────────────────────────

def test_auto_tag_no_vision_preset_400(client, make_token, media_storage, monkeypatch):
    """NEGATIVE : sans preset vision → 400 actionnable, aucun appel LLM.

    Retirer le filtre ``supports_vision`` ferait partir un appel → ROUGE.
    """
    headers = _headers(make_token, "autotag-novision")
    _seed_preset("autotag-novision", name="classique", supports_vision=0)
    mid = _upload(client, headers, _png_bytes(), filename="q")
    called = []
    _patch_llm(monkeypatch, '["x"]', capture=called)
    r = client.post(f"/api/media/{mid}/auto-tag", json={}, headers=headers)
    assert r.status_code == 400
    assert r.get_json()["reason"] == "no_vision_preset"
    assert "compatible vision" in r.get_json()["error"]
    assert called == []


def test_auto_tag_default_picks_first_vision_preset(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-default")
    _seed_preset("autotag-default", name="classique", supports_vision=0)
    pid = _seed_preset("autotag-default", name="vision", supports_vision=1)
    mid = _upload(client, headers, _png_bytes(), filename="d")
    capture = []
    _patch_llm(monkeypatch, '["a"]', capture=capture)
    r = client.post(f"/api/media/{mid}/auto-tag", json={}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(capture) == 1  # le preset vision a bien été choisi automatiquement
    assert pid  # (le preset vision existe)


def test_auto_tag_preset_not_vision_400(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-nv")
    pid = _seed_preset("autotag-nv", name="classique", supports_vision=0)
    mid = _upload(client, headers, _png_bytes(), filename="nv")
    _patch_llm(monkeypatch, '["a"]')
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 400
    assert r.get_json()["reason"] == "preset_not_vision"


def test_auto_tag_preset_not_found_404(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-404p")
    _seed_preset("autotag-404p")
    mid = _upload(client, headers, _png_bytes(), filename="nf")
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": 999999}, headers=headers)
    assert r.status_code == 404
    assert r.get_json()["reason"] == "preset_not_found"


def test_auto_tag_bad_preset_id_400(client, make_token, media_storage):
    headers = _headers(make_token, "autotag-badpid")
    mid = _upload(client, headers, _png_bytes(), filename="b")
    for bad in ["x", 1.5, True]:
        r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": bad}, headers=headers)
        assert r.status_code == 400, (bad, r.get_data(as_text=True))


# ── 9. Auth / 403 / 404 ───────────────────────────────────────────────

def test_auto_tag_requires_auth(client):
    assert client.post("/api/media/1/auto-tag", json={}).status_code == 401
    assert client.post("/api/media/auto-tag", json={"ids": [1]}).status_code == 401


def test_auto_tag_cross_user_forbidden(client, make_token, media_storage, monkeypatch):
    headers_a = _headers(make_token, "autotag-a")
    headers_b = _headers(make_token, "autotag-b")
    _seed_preset("autotag-a")
    mid = _upload(client, headers_a, _png_bytes(), filename="priv")
    _patch_llm(monkeypatch, '["x"]')
    assert client.post(
        f"/api/media/{mid}/auto-tag", json={}, headers=headers_b
    ).status_code == 403


def test_auto_tag_unknown_media_404(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-404m")
    _seed_preset("autotag-404m")
    _patch_llm(monkeypatch, '["x"]')
    assert client.post(
        "/api/media/999999/auto-tag", json={}, headers=headers
    ).status_code == 404


def test_auto_tag_admin_can_tag_others(client, make_token, media_storage, monkeypatch):
    headers_owner = _headers(make_token, "autotag-owner", role="user")
    mid = _upload(client, headers_owner, _png_bytes(), filename="o")
    # Preset PERSO de l'admin (visible par son propriétaire) — pas de preset
    # global : évite toute fuite d'état vers les autres tests (DB partagée).
    _seed_preset("autotag-admin")
    # En dernier : garantit que l'utilisateur admin garde bien le rôle admin.
    headers_admin = _headers(make_token, "autotag-admin", role="admin")
    _patch_llm(monkeypatch, '["shared"]')
    r = client.post(f"/api/media/{mid}/auto-tag", json={}, headers=headers_admin)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _tag_rows(mid) == [("shared", "ai")]


# ── 10. Protection SSRF ───────────────────────────────────────────────

def test_auto_tag_ssrf_private_blocked(client, make_token, media_storage, monkeypatch):
    """NEGATIVE : base_url privée sans opt-in → 502, AUCUN appel émis.

    Retirer la validation SSRF ferait partir le POST → ROUGE.
    """
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    headers = _headers(make_token, "autotag-ssrf")
    pid = _seed_preset("autotag-ssrf", base_url="https://192.168.1.10")
    mid = _upload(client, headers, _png_bytes(), filename="s")
    called = []
    _patch_llm(monkeypatch, '["x"]', capture=called)
    r = client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    assert r.status_code == 502
    assert r.get_json()["reason"] == "blocked"
    assert called == []  # jamais d'appel vers l'hôte privé
    assert "192.168.1.10" not in r.get_data(as_text=True)  # pas de reflet


# ── 11. Lot borné + récap ─────────────────────────────────────────────

def test_auto_tag_bulk_recap(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-bulk")
    pid = _seed_preset("autotag-bulk")
    img = _upload(client, headers, _png_bytes(), filename="i1")
    vid = _upload(client, headers, b"v", kind="video", ext=".mp4", filename="v1")
    _patch_llm(monkeypatch, '["sunset", "beach"]')
    r = client.post(
        "/api/media/auto-tag",
        json={"ids": [img, vid, 999999], "preset_id": pid},
        headers=headers,
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["tagged"] == 1
    assert body["skipped"] == 2
    assert body["errors"] == 0
    by_id = {res["id"]: res for res in body["results"]}
    assert by_id[img]["status"] == "tagged"
    assert by_id[vid]["reason"] == "not_image"
    assert by_id[999999]["reason"] == "not_accessible"
    assert _tag_rows(img) == [("beach", "ai"), ("sunset", "ai")]


def test_auto_tag_bulk_too_large_400(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-big")
    _seed_preset("autotag-big")
    _patch_llm(monkeypatch, '["x"]')
    r = client.post(
        "/api/media/auto-tag",
        json={"ids": [1, 2, 3, 4, 5, 6]},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.get_json()["reason"] == "batch_too_large"


def test_auto_tag_bulk_no_vision_preset_400(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-bulk-nv")
    _seed_preset("autotag-bulk-nv", supports_vision=0)
    img = _upload(client, headers, _png_bytes(), filename="i")
    called = []
    _patch_llm(monkeypatch, '["x"]', capture=called)
    r = client.post("/api/media/auto-tag", json={"ids": [img]}, headers=headers)
    assert r.status_code == 400
    assert r.get_json()["reason"] == "no_vision_preset"
    assert called == []


def test_auto_tag_bulk_invalid_ids_400(client, make_token, media_storage, monkeypatch):
    headers = _headers(make_token, "autotag-bulk-bad")
    _seed_preset("autotag-bulk-bad")
    _patch_llm(monkeypatch, '["x"]')
    assert client.post("/api/media/auto-tag", json={"ids": "nope"}, headers=headers).status_code == 400
    assert client.post("/api/media/auto-tag", json={}, headers=headers).status_code == 400


# ── 12. Non-régression ────────────────────────────────────────────────

def test_auto_tag_does_not_disturb_manual_tags_route(client, make_token, media_storage, monkeypatch):
    """Ajouter/retirer un tag manuel après auto-tag reste possible et isolé."""
    headers = _headers(make_token, "autotag-nonreg")
    pid = _seed_preset("autotag-nonreg")
    mid = _upload(client, headers, _png_bytes(), filename="r")
    _patch_llm(monkeypatch, '["ai1", "ai2"]')
    client.post(f"/api/media/{mid}/auto-tag", json={"preset_id": pid}, headers=headers)
    # Retrait d'un tag IA via la route manuelle (le front le permet).
    r = client.post(f"/api/media/{mid}/tags", json={"remove": ["ai1"]}, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _tag_rows(mid) == [("ai2", "ai")]
    # Ajout manuel : coexiste avec l'IA.
    client.post(f"/api/media/{mid}/tags", json={"add": ["manuel"]}, headers=headers)
    rows = _tag_rows(mid)
    assert ("ai2", "ai") in rows and ("manuel", "manual") in rows
