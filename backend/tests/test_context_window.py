"""Tests fenêtre de contexte des LLM (détection + réglage manuel).

Couvre le contrat gelé :
  1. formes de réponse provider : OpenRouter (context_length), vLLM
     (max_model_len), llama.cpp (/props → n_ctx), Ollama (/api/show),
     DeepSeek sans champ → famille puis inconnu ;
  2. précédence : manual jamais écrasé par la détection ;
  3. inconnu explicite (plus de 4096 inventé) ;
  4. migration DB ai_presets + idempotence + backfill (NULL = auto) ;
  5. contrat presets : POST/PUT/GET avec context_length ;
  6. SSRF / ownership de detect-context ;
  7. context_source renvoyé par llm-process.
"""

import socket
import sqlite3
from unittest import mock

import pytest

# ── Fixtures / helpers ────────────────────────────────────────────────


class _FakeResp:
    """Réponse requests minimaliste (.ok, .status_code, .headers, .json())."""

    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


_PUBLIC_HOSTS = {
    "api.example.com": "8.8.8.8",
    "llama.example.com": "8.8.4.4",
}


def _fake_getaddrinfo(host, *args, **kwargs):
    ip = _PUBLIC_HOSTS.get(host.lower())
    if ip is None:
        raise socket.gaierror(-2, "Name or service not known")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.fixture(autouse=True)
def _clear_model_context_cache():
    """Isole le cache runtime _get_model_context entre les tests."""
    from routes import enhance

    enhance._model_context_cache.clear()
    yield
    enhance._model_context_cache.clear()


def _ensure_user(user_id="test-user-123", role="user"):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", role),
    )
    conn.commit()
    conn.close()


def _admin_headers(make_token):
    _ensure_user("admin-ctx-1", role="admin")
    return {"Authorization": f"Bearer {make_token('admin-ctx-1', role='admin')}"}


def _create_preset(client, headers, base_url="https://api.example.com",
                   model="deepseek-chat", api_key="sk-VERYSECRET-42", **extra):
    payload = {"name": "CtxTest", "base_url": base_url, "api_key": api_key, "model": model}
    payload.update(extra)
    # La création valide désormais l'URL (anti-SSRF) : on simule une résolution
    # DNS publique pour les hôtes de test (un hôte privé opt-in court-circuite
    # la résolution).
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        r = client.post("/api/presets", json=payload, headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


def _seed_preset(base_url, model="deepseek-chat", user_id="test-user-123", **extra):
    """Insère un preset directement en BDD (pré-existant, ex. LLM interne).

    Contourne volontairement la validation d'écriture : un preset enregistré
    AVANT le durcissement doit rester utilisable et éditable (pas d'invalidation
    rétroactive).
    """
    from routes.helpers import get_db

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, "
        "model, is_global, is_client_side, context_length, context_source, context_checked_at) "
        "VALUES (?, ?, 'openai', ?, '', ?, 0, 0, ?, ?, ?)",
        (user_id, 'SeededPreset', base_url, model,
         extra.get('context_length'), extra.get('context_source'), extra.get('context_checked_at')),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def _preset_row(pid):
    from routes.helpers import get_db

    conn = get_db()
    row = conn.execute("SELECT * FROM ai_presets WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return row


# ── 4. Migration DB : colonnes + idempotence + backfill ───────────────


def _old_schema_presets(conn):
    """Crée ai_presets avec le schéma historique (sans colonnes contexte)."""
    conn.execute("""
        CREATE TABLE ai_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            name TEXT NOT NULL,
            engine TEXT DEFAULT 'openai',
            base_url TEXT NOT NULL DEFAULT '',
            api_key_encrypted TEXT DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            is_global INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def test_migrate_presets_adds_context_columns():
    conn = sqlite3.connect(":memory:")
    _old_schema_presets(conn)
    from db.init import _migrate_presets

    _migrate_presets(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_presets)").fetchall()]
    assert {"context_length", "context_source", "context_checked_at"} <= set(cols)


def test_migrate_presets_idempotent():
    conn = sqlite3.connect(":memory:")
    _old_schema_presets(conn)
    from db.init import _migrate_presets

    _migrate_presets(conn)
    _migrate_presets(conn)  # base déjà migrée → pas d'erreur, pas de doublon
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_presets)").fetchall()]
    assert cols.count("context_length") == 1
    assert cols.count("context_source") == 1
    assert cols.count("context_checked_at") == 1


def test_migrate_presets_backfill_null_means_auto():
    conn = sqlite3.connect(":memory:")
    _old_schema_presets(conn)
    from db.init import _migrate_presets

    _migrate_presets(conn)
    conn.execute("INSERT INTO ai_presets (name, model) VALUES ('ancien', 'deepseek-chat')")
    row = conn.execute(
        "SELECT context_length, context_source, context_checked_at FROM ai_presets"
    ).fetchone()
    # Backfill : NULL partout = détection auto à l'exécution (aucune valeur écrite).
    assert row == (None, None, None)


def test_ai_presets_columns_in_app_db(app_ctx):
    from routes.helpers import get_db

    conn = get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_presets)").fetchall()]
    conn.close()
    assert "context_length" in cols
    assert "context_source" in cols
    assert "context_checked_at" in cols


# ── 1a. _parse_models : formes de réponse provider ────────────────────


def test_parse_models_openrouter_context_length():
    from routes.presets import _parse_models

    data = {"data": [{"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat",
                      "owned_by": "deepseek", "context_length": 65536}]}
    out = _parse_models(data)
    assert out[0]["id"] == "deepseek/deepseek-chat"
    assert out[0]["name"] == "DeepSeek Chat"
    assert out[0]["owned_by"] == "deepseek"
    assert out[0]["context_length"] == 65536


def test_parse_models_vllm_max_model_len():
    from routes.presets import _parse_models

    data = {"data": [{"id": "mistral-7b-instruct", "max_model_len": 32768}]}
    out = _parse_models(data)
    assert out[0]["context_length"] == 32768
    assert out[0]["name"] == "mistral-7b-instruct"  # fallback name=id, contrat conservé


def test_parse_models_context_window_and_max_context_length():
    from routes.presets import _parse_models

    assert _parse_models({"data": [{"id": "m1", "context_window": 8192}]})[0]["context_length"] == 8192
    assert _parse_models({"data": [{"id": "m2", "max_context_length": 4096}]})[0]["context_length"] == 4096


def test_parse_models_without_context_field_no_key_added():
    from routes.presets import _parse_models

    out = _parse_models({"data": [{"id": "m1", "name": "M", "owned_by": "org"}]})
    assert "context_length" not in out[0]


def test_parse_models_contract_preserved_string_entries():
    from routes.presets import _parse_models

    out = _parse_models({"data": ["plain-model-id"]})
    assert out == [{"id": "plain-model-id", "name": "plain-model-id", "owned_by": ""}]


def test_parse_models_invalid_context_value_ignored():
    from routes.presets import _parse_models

    out = _parse_models({"data": [{"id": "m1", "context_length": "pas-un-nombre"}]})
    assert "context_length" not in out[0]


# ── 1b. detect-context : sondes api / ollama / llama / family ─────────


def test_detect_via_openrouter_style_api(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(json_data={
             "data": [{"id": "deepseek-chat", "name": "DeepSeek Chat",
                       "owned_by": "deepseek", "context_length": 65536}]})), \
         mock.patch("requests.post") as mpost:
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["detected_length"] == 65536
    assert body["source"] == "auto"
    assert body["probe"] == "api"
    assert body["status"] == "ok"
    assert "sk-VERYSECRET-42" not in r.get_data(as_text=True)
    mpost.assert_not_called()  # sonde (a) suffisante, pas d'enchaînement inutile
    # Persistance sur ai_presets
    row = _preset_row(pid)
    assert row["context_length"] == 65536
    assert row["context_source"] == "auto"
    assert row["context_checked_at"]  # ISO 8601 UTC renseigné


def test_detect_via_vllm_max_model_len(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="mistral-7b-instruct")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(json_data={
             "data": [{"id": "mistral-7b-instruct", "max_model_len": 32768}]})), \
         mock.patch("requests.post"):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 32768
    assert body["probe"] == "api"
    assert body["source"] == "auto"


def test_detect_via_ollama_show(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "ollama.local")
    _ensure_user()
    pid = _create_preset(client, auth_headers, base_url="http://ollama.local:11434",
                         model="qwen2.5:7b")
    with mock.patch("requests.get", return_value=_FakeResp(status_code=404)), \
         mock.patch("requests.post", return_value=_FakeResp(json_data={
             "model_info": {"general.architecture": "qwen2",
                            "qwen2.context_length": 131072}})) as mpost:
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 131072
    assert body["probe"] == "ollama"
    assert body["source"] == "auto"
    assert body["status"] == "ok"
    assert mpost.call_count == 1  # POST /api/show
    assert "/api/show" in mpost.call_args.args[0]
    row = _preset_row(pid)
    assert row["context_length"] == 131072
    assert row["context_source"] == "auto"


def test_detect_via_llama_cpp_props(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, base_url="https://llama.example.com",
                         model="local-model")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=[
             _FakeResp(status_code=404),                                # /models
             _FakeResp(json_data={"default_generation_settings":
                                  {"n_ctx": 4096}}),                   # /props
         ]), \
         mock.patch("requests.post", return_value=_FakeResp(status_code=404)):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 4096
    assert body["probe"] == "llama"
    assert body["source"] == "auto"
    row = _preset_row(pid)
    assert row["context_length"] == 4096


def test_detect_via_llama_cpp_props_top_level_nctx(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, base_url="https://llama.example.com",
                         model="local-model")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=[
             _FakeResp(status_code=404),
             _FakeResp(json_data={"n_ctx": 8192}),
         ]), \
         mock.patch("requests.post", return_value=_FakeResp(status_code=404)):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 8192
    assert body["probe"] == "llama"


def test_detect_family_deepseek_when_probes_fail(client, auth_headers):
    """DeepSeek SANS champ exposé → table de familles (64K, pas 4096)."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(status_code=404)), \
         mock.patch("requests.post", return_value=_FakeResp(status_code=404)):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 65536  # 64K, pas l'ancien faux 4096
    assert body["source"] == "family"
    assert body["probe"] == "family"
    assert body["status"] == "ok"
    row = _preset_row(pid)
    assert row["context_length"] == 65536
    assert row["context_source"] == "family"


def test_detect_unknown_explicit_no_invented_value(client, auth_headers):
    """Modèle inconnu : unknown explicite (NULL), jamais 4096 inventé."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="zzx9q-mystery-99")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(status_code=404)), \
         mock.patch("requests.post", return_value=_FakeResp(status_code=404)):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] is None
    assert body["detected_length"] != 4096
    assert body["source"] == "unknown"
    assert body["probe"] == "none"
    assert body["status"] == "not_found"
    row = _preset_row(pid)
    assert row["context_length"] is None
    assert row["context_source"] == "unknown"
    assert row["context_checked_at"]


def test_detect_unreachable(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="zzx9q-mystery-99")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=ConnectionError("secret internal detail")), \
         mock.patch("requests.post", side_effect=ConnectionError("secret internal detail")):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["status"] == "unreachable"
    assert body["source"] == "unknown"
    assert "secret internal detail" not in r.get_data(as_text=True)
    row = _preset_row(pid)
    assert row["context_source"] == "unknown"


def test_detect_unauthorized(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="zzx9q-mystery-99")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=[_FakeResp(status_code=401),
                                                 _FakeResp(status_code=401)]), \
         mock.patch("requests.post", return_value=_FakeResp(status_code=401)):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["status"] == "unauthorized"
    assert body["source"] == "unknown"
    row = _preset_row(pid)
    assert row["context_source"] == "unknown"


def test_detect_never_overwrites_manual(client, auth_headers):
    """Précédence : une valeur manual n'est jamais écrasée (aucune sonde émise)."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat", context_length=4096)
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get") as mget, \
         mock.patch("requests.post") as mpost:
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["detected_length"] == 4096
    assert body["source"] == "manual"
    assert body["probe"] == "none"
    assert body["status"] == "ok"
    mget.assert_not_called()
    mpost.assert_not_called()
    row = _preset_row(pid)
    assert row["context_length"] == 4096
    assert row["context_source"] == "manual"


# ── 6. SSRF / ownership de detect-context ─────────────────────────────


def test_detect_ssrf_blocked_no_probe(client, auth_headers):
    _ensure_user()
    # Preset pré-existant (interne) : seedé en BDD car la création refuse
    # désormais une URL privée sans opt-in (pas d'invalidation rétroactive).
    pid = _seed_preset(base_url="https://192.168.1.10")
    with mock.patch("requests.get") as mget, mock.patch("requests.post") as mpost:
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["status"] == "blocked"
    assert body["source"] == "unknown"
    assert "192.168.1.10" not in r.get_data(as_text=True)  # pas de reflet
    mget.assert_not_called()
    mpost.assert_not_called()
    row = _preset_row(pid)
    assert row["context_source"] is None  # rien de persisté (pas de sonde)


def test_detect_redirect_to_private_blocked(client, auth_headers):
    """Redirection 302 vers une IP privée : refusée, aucune sonde suivante."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="zzx9q-mystery-99")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=[
             _FakeResp(status_code=302, headers={"Location": "https://192.168.1.10/models"}),
         ]) as mget, \
         mock.patch("requests.post") as mpost:
        r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    body = r.get_json()
    assert body["status"] == "blocked"
    assert body["source"] == "unknown"
    assert "192.168.1.10" not in r.get_data(as_text=True)
    assert mget.call_count == 1   # jamais de fetch vers l'IP privée
    mpost.assert_not_called()
    row = _preset_row(pid)
    assert row["context_source"] is None  # rien de persisté


def test_detect_not_owner_404(client, auth_headers, make_token):
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    _ensure_user("someone-else-1")
    other_headers = {"Authorization": f"Bearer {make_token('someone-else-1')}"}
    r = client.post(f"/api/presets/{pid}/detect-context", headers=other_headers)
    assert r.status_code == 404


def test_detect_global_requires_admin(client, auth_headers, make_token):
    admin_h = _admin_headers(make_token)
    pid = _create_preset(client, admin_h, is_global=1)
    # Utilisateur non admin : 403 (écriture partagée interdite).
    r = client.post(f"/api/presets/{pid}/detect-context", headers=auth_headers)
    assert r.status_code == 403
    # Admin : OK.
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(json_data={
             "data": [{"id": "deepseek-chat", "context_length": 65536}]})):
        r = client.post(f"/api/presets/{pid}/detect-context", headers=admin_h)
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


# ── 5. Contrat presets : POST/PUT/GET context_length ──────────────────


def test_post_preset_with_manual_context_length(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, context_length=8192)
    # GET liste
    r = client.get("/api/presets", headers=auth_headers)
    items = [p for p in r.get_json() if p["id"] == pid]
    assert len(items) == 1
    item = items[0]
    assert item["context_length"] == 8192
    assert item["context_source"] == "manual"
    assert item["context_checked_at"]
    # GET unitaire
    r2 = client.get(f"/api/presets/{pid}", headers=auth_headers)
    assert r2.status_code == 200
    single = r2.get_json()
    assert single["context_length"] == 8192
    assert single["context_source"] == "manual"
    assert single["context_checked_at"] == item["context_checked_at"]
    # Le reste du contrat liste est intact
    for key in ("id", "user_id", "name", "engine", "base_url", "model",
                "is_global", "is_client_side", "owner_name", "created_at"):
        assert key in item


def test_post_preset_context_length_null_or_empty_is_auto(client, auth_headers):
    _ensure_user()
    # absent
    pid1 = _create_preset(client, auth_headers, model="m-a")
    # null explicite
    pid2 = _create_preset(client, auth_headers, model="m-b", context_length=None)
    # chaîne vide
    pid3 = _create_preset(client, auth_headers, model="m-c", context_length="   ")
    r = client.get("/api/presets", headers=auth_headers)
    by_id = {p["id"]: p for p in r.get_json()}
    for pid in (pid1, pid2, pid3):
        assert by_id[pid]["context_length"] is None
        assert by_id[pid]["context_source"] is None


@pytest.mark.parametrize("bad", [0, -5, "abc", 10_000_001, True, 1.5])
def test_post_invalid_context_length_400(client, auth_headers, bad):
    _ensure_user()
    r = client.post("/api/presets", json={
        "name": "bad", "base_url": "https://api.example.com", "model": "m",
        "context_length": bad,
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "context_length" in r.get_json()["error"]


def test_put_context_length_lifecycle(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    # 1. PUT manuel
    r = client.put(f"/api/presets/{pid}", json={"context_length": 16384}, headers=auth_headers)
    assert r.status_code == 200
    row = _preset_row(pid)
    assert row["context_length"] == 16384
    assert row["context_source"] == "manual"
    # 2. PUT sans le champ → valeur conservée (clients existants intacts)
    client.put(f"/api/presets/{pid}", json={"name": "renommé"}, headers=auth_headers)
    row = _preset_row(pid)
    assert row["context_length"] == 16384
    assert row["context_source"] == "manual"
    assert row["name"] == "renommé"
    # 3. PUT null explicite → remise en auto
    client.put(f"/api/presets/{pid}", json={"context_length": None}, headers=auth_headers)
    row = _preset_row(pid)
    assert row["context_length"] is None
    assert row["context_source"] is None
    # 4. PUT chaîne vide → auto
    client.put(f"/api/presets/{pid}", json={"context_length": 65536}, headers=auth_headers)
    client.put(f"/api/presets/{pid}", json={"context_length": ""}, headers=auth_headers)
    row = _preset_row(pid)
    assert row["context_length"] is None
    assert row["context_source"] is None


def test_put_identical_context_length_preserves_source(client, auth_headers):
    """PUT avec la MÊME valeur : source/checked_at conservés (pas de promotion
    silencieuse en 'manual' lors d'un simple renommage)."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "UPDATE ai_presets SET context_length = ?, context_source = 'family', "
        "context_checked_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
        (65536, pid),
    )
    conn.commit()
    conn.close()
    # Renommage seul qui renvoie la valeur pré-remplie à l'identique.
    r = client.put(f"/api/presets/{pid}",
                   json={"name": "renommé", "context_length": 65536},
                   headers=auth_headers)
    assert r.status_code == 200
    row = _preset_row(pid)
    assert row["name"] == "renommé"
    assert row["context_length"] == 65536
    assert row["context_source"] == "family"  # conservée, PAS 'manual'
    assert row["context_checked_at"] == "2026-01-01T00:00:00+00:00"  # inchangé

    # Forme texte équivalente acceptée (clients hétérogènes) : même résultat.
    r = client.put(f"/api/presets/{pid}", json={"context_length": "65536"},
                   headers=auth_headers)
    assert r.status_code == 200
    row = _preset_row(pid)
    assert row["context_source"] == "family"
    assert row["context_checked_at"] == "2026-01-01T00:00:00+00:00"

    # Idem pour une source 'auto' : conservée à valeur identique.
    conn = get_db()
    conn.execute("UPDATE ai_presets SET context_source = 'auto' WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    client.put(f"/api/presets/{pid}", json={"context_length": 65536}, headers=auth_headers)
    row = _preset_row(pid)
    assert row["context_source"] == "auto"


def test_put_different_context_length_promotes_manual(client, auth_headers):
    """PUT avec une valeur DIFFÉRENTE : promotion en 'manual' comme avant."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "UPDATE ai_presets SET context_length = ?, context_source = 'auto', "
        "context_checked_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
        (65536, pid),
    )
    conn.commit()
    conn.close()
    r = client.put(f"/api/presets/{pid}", json={"context_length": 32768},
                   headers=auth_headers)
    assert r.status_code == 200
    row = _preset_row(pid)
    assert row["context_length"] == 32768
    assert row["context_source"] == "manual"
    assert row["context_checked_at"] != "2026-01-01T00:00:00+00:00"


def test_put_null_context_length_resets_auto(client, auth_headers):
    """PUT null : remise en auto (source + checked_at effacés), comme avant."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "UPDATE ai_presets SET context_length = ?, context_source = 'family', "
        "context_checked_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
        (65536, pid),
    )
    conn.commit()
    conn.close()
    r = client.put(f"/api/presets/{pid}", json={"context_length": None},
                   headers=auth_headers)
    assert r.status_code == 200
    row = _preset_row(pid)
    assert row["context_length"] is None
    assert row["context_source"] is None
    assert row["context_checked_at"] is None


@pytest.mark.parametrize("bad", [0, -5, "douze", True, 10_000_001])
def test_put_invalid_context_length_400(client, auth_headers, bad):
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    r = client.put(f"/api/presets/{pid}", json={"context_length": bad}, headers=auth_headers)
    assert r.status_code == 400
    assert "context_length" in r.get_json()["error"]
    row = _preset_row(pid)
    assert row["context_source"] is None  # rien d'écrit


def test_get_preset_by_id_not_owned_404(client, auth_headers, make_token):
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    other = {"Authorization": f"Bearer {make_token('someone-else-1')}"}
    _ensure_user("someone-else-1")
    r = client.get(f"/api/presets/{pid}", headers=other)
    assert r.status_code == 404


# ── 7. llm-process : précédence + context_source ──────────────────────


def _run_llm_process(client, auth_headers, pid):
    with mock.patch("routes.enhance._call_llm_internal") as mllm:
        mllm.return_value = {
            "choices": [{"message": {"content": "kw1, kw2"}}],
            "usage": {"total_tokens": 12},
        }
        r = client.post("/api/keywords/llm-process", headers=auth_headers,
                        json={"preset_id": pid, "instruction": "generate"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_llm_process_manual_value_no_probe(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, context_length=65536)
    with mock.patch("requests.get") as mget, mock.patch("requests.post") as mpost:
        body = _run_llm_process(client, auth_headers, pid)
    assert body["max_context"] == 65536
    assert body["context_source"] == "manual"
    mget.assert_not_called()  # jamais de sonde live pour une valeur manuelle
    mpost.assert_not_called()
    # Le contrat existant est intact
    assert body["output"] == "kw1, kw2"
    assert "usage" in body


def test_llm_process_auto_cached_probe(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="ctx-model-7b")
    probe = _FakeResp(json_data={"data": [{"id": "ctx-model-7b", "context_length": 24576}]})
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=probe) as mget:
        body = _run_llm_process(client, auth_headers, pid)
    assert body["max_context"] == 24576
    assert body["context_source"] == "auto"
    assert mget.call_count == 1
    # 2e appel → servi par le cache (TTL 3600), aucune requête réseau.
    with mock.patch("requests.get") as mget2:
        body2 = _run_llm_process(client, auth_headers, pid)
    assert body2["max_context"] == 24576
    assert body2["context_source"] == "auto"
    mget2.assert_not_called()


def test_llm_process_family_deepseek(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    with mock.patch("requests.get", return_value=_FakeResp(status_code=404)):
        body = _run_llm_process(client, auth_headers, pid)
    assert body["max_context"] == 65536  # 64K, pas le faux 4096 historique
    assert body["context_source"] == "family"


def test_llm_process_unknown_explicit_not_4096(client, auth_headers):
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="zzx9q-mystery-99")
    with mock.patch("requests.get", return_value=_FakeResp(status_code=404)):
        body = _run_llm_process(client, auth_headers, pid)
    assert body["max_context"] is None  # inconnu explicite, plus de 4096 inventé
    assert body["context_source"] == "unknown"
    assert body["output"] == "kw1, kw2"


def test_llm_process_detect_persisted_family_value_used(client, auth_headers):
    """Une valeur persistée par detect-context (family) est réutilisée telle quelle."""
    _ensure_user()
    pid = _create_preset(client, auth_headers, model="deepseek-chat")
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "UPDATE ai_presets SET context_length = ?, context_source = 'family' WHERE id = ?",
        (65536, pid),
    )
    conn.commit()
    conn.close()
    with mock.patch("requests.get") as mget:
        body = _run_llm_process(client, auth_headers, pid)
    assert body["max_context"] == 65536
    assert body["context_source"] == "family"
    mget.assert_not_called()


# ── Compléments : sonde cachée Ollama (branche /api/show) ─────────────


def test_get_model_context_ollama_branch_cached(app_ctx, monkeypatch):
    from routes import enhance

    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "127.0.0.1")
    with mock.patch("requests.post", return_value=_FakeResp(json_data={
            "model_info": {"llama.context_length": 131072}})) as mpost:
        ctx = enhance._get_model_context("http://127.0.0.1:11434", "", "llama3.1:8b")
    assert ctx == 131072
    assert mpost.call_count == 1
    # Cache (TTL 3600) : 2e appel sans requête.
    with mock.patch("requests.post") as mpost2:
        ctx2 = enhance._get_model_context("http://127.0.0.1:11434", "", "llama3.1:8b")
    assert ctx2 == 131072
    mpost2.assert_not_called()


def test_get_model_context_unknown_returns_zero(app_ctx):
    from routes import enhance

    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(status_code=404)):
        ctx = enhance._get_model_context("https://api.example.com", "k", "zzx9q-mystery-99")
    assert ctx == 0


# ── Table de familles : valeurs corrigées ─────────────────────────────


def test_family_table_corrected_values():
    from routes.enhance import guess_family_context

    cases = {
        "deepseek-chat": 65536,            # 64K (pas 4096)
        "deepseek-reasoner": 65536,
        "gpt-4o-2024-11-20": 128000,       # 128K (pas 8192)
        "gpt-4o-mini": 128000,
        "gpt-3.5-turbo": 16384,            # 16K (pas 4096)
        "claude-3-5-sonnet": 200000,       # 200K (pas 100000)
        "qwen2.5-7b-instruct": 131072,     # 128K
        "llama3-8b": 8192,                 # 8K (pas 4096)
        "mistral-small-latest": 32768,     # 32K
        "mixtral-8x7b": 32768,
        "gpt-4": 8192,                     # gpt-4 legacy : 8192
        "gpt-4-turbo": 128000,
    }
    for model, expected in cases.items():
        value, key = guess_family_context(model)
        assert value == expected, f"{model}: attendu {expected}, obtenu {value} ({key})"
        assert key is not None


def test_family_table_specific_before_generic():
    """Les clés spécifiques sont testées avant les génériques (dict ordonné)."""
    from routes.enhance import guess_family_context

    assert guess_family_context("gpt-4o-mini")[0] == 128000   # pas 8192
    assert guess_family_context("gpt-4.1-mini")[0] == 1047576
    assert guess_family_context("gpt-4-0613")[0] == 8192


def test_family_unknown_model_returns_none():
    from routes.enhance import guess_family_context

    assert guess_family_context("zzx9q-mystery-99") == (None, None)
