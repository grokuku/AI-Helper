"""Tests anti-SSRF pour les endpoints presets (list-models / <id>/models)."""

import socket
from unittest import mock

import pytest


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None, raise_error=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data if json_data is not None else {"data": [{"id": "m1", "name": "Modèle 1"}]}
        self.headers = headers or {}
        self._raise_error = raise_error

    def raise_for_status(self):
        if self._raise_error or self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """Simule la résolution DNS : hôtes publics vs privés / inconnus."""
    host = host.lower()
    if host == "api.example.com":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    if host == "rebind.example.com":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
    if host == "ollama.local":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 11434))]
    raise socket.gaierror(-2, "Name or service not known")


# ── IP littérale privée ──────────────────────────────────────────────


def test_private_ip_literal_blocked(client, auth_headers):
    """https:// vers une IP privée : refusé, aucun fetch émis."""
    with mock.patch("requests.get") as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://192.168.1.10"},
            headers=auth_headers,
        )
    assert resp.status_code == 502
    assert "192.168.1.10" not in resp.get_json()["error"]  # pas de reflet
    mock_get.assert_not_called()


def test_localhost_hostname_blocked(client, auth_headers):
    """Hostname résolvant vers loopback : refusé."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get") as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://localhost"},
            headers=auth_headers,
        )
    assert resp.status_code == 502
    mock_get.assert_not_called()


# ── DNS rebinding ────────────────────────────────────────────────────


def test_dns_rebinding_blocked(client, auth_headers):
    """Hostname résolvant vers une IP privée (10.0.0.5) : refusé."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get") as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://rebind.example.com"},
            headers=auth_headers,
        )
    assert resp.status_code == 502
    mock_get.assert_not_called()


# ── URL valide publique ──────────────────────────────────────────────


def test_public_https_accepted(client, auth_headers):
    """https:// public avec résolution publique : le fetch part."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp()) as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://api.example.com", "api_key": "k"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    models = resp.get_json()
    assert models[0]["id"] == "m1"
    mock_get.assert_called_once()


def test_http_blocked_without_optin(client, auth_headers, monkeypatch):
    """http:// sans opt-in AIH_ALLOW_HTTP_LLM : refusé."""
    monkeypatch.delenv("AIH_ALLOW_HTTP_LLM", raising=False)
    resp = client.post(
        "/api/presets/list-models",
        json={"base_url": "http://api.example.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 502


def test_userinfo_rejected(client, auth_headers):
    """Credentials embarquées (user:pass@) : refusé."""
    resp = client.post(
        "/api/presets/list-models",
        json={"base_url": "https://user:pass@api.example.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 502


# ── Redirections ─────────────────────────────────────────────────────


def test_redirect_to_private_blocked(client, auth_headers):
    """Redirection 302 vers une IP privée : refusée, pas de 2e fetch."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=[
             _FakeResp(status_code=302, headers={"Location": "https://192.168.1.10/models"}),
         ]) as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://api.example.com"},
            headers=auth_headers,
        )
    assert resp.status_code == 502
    assert mock_get.call_count == 1  # jamais de fetch vers l'IP privée


# ── Opt-in hôtes privés (LLM locaux) ─────────────────────────────────


def test_optin_private_host_allowed(client, auth_headers, monkeypatch):
    """Hôte privé listé dans AIH_ALLOW_PRIVATE_LLM_HOSTS : autorisé."""
    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "ollama.local")
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp()) as mock_get:
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "http://ollama.local:11434", "api_key": "k"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    mock_get.assert_called_once()


# ── Erreurs réseau non reflétées ─────────────────────────────────────


def test_network_error_not_reflected(client, auth_headers):
    """Les erreurs réseau internes ne fuient pas dans la réponse JSON."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", side_effect=ConnectionError("secret internal detail")):
        resp = client.post(
            "/api/presets/list-models",
            json={"base_url": "https://api.example.com"},
            headers=auth_headers,
        )
    assert resp.status_code == 502
    body = resp.get_json()["error"]
    assert "secret internal detail" not in body
    assert "inaccessible" in body


# ── Endpoint /api/presets/<id>/models (preset en BDD) ────────────────


def _ensure_user(user_id="test-user-123"):
    """Insère l'utilisateur (FK users.id requise par ai_presets)."""
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", "user"),
    )
    conn.commit()
    conn.close()


def test_preset_models_private_blocked(client, auth_headers):
    """Un preset BDD avec base_url privée est refusé à l'appel.

    Le preset est seedé directement en BDD (pré-existant) : la création refuse
    désormais une URL privée sans opt-in (pas d'invalidation rétroactive).
    """
    pid = _seed_preset("https://10.0.0.7")
    with mock.patch("requests.get") as mock_get:
        resp = client.get(f"/api/presets/{pid}/models", headers=auth_headers)
    assert resp.status_code == 502
    mock_get.assert_not_called()


def _seed_preset(base_url, model="m", user_id="test-user-123"):
    """Insère un preset directement en BDD (pré-existant, contourne la garde).

    Simule un preset interne enregistré AVANT le durcissement : il doit rester
    utilisable et éditable (pas d'invalidation rétroactive).
    """
    _ensure_user(user_id)
    from routes.helpers import get_db

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, "
        "model, is_global, is_client_side) VALUES (?, 'interne', 'openai', ?, '', ?, 0, 0)",
        (user_id, base_url, model),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def _create_preset(client, headers, base_url="https://api.example.com", name="cloud"):
    """Crée un preset via POST (résolution DNS publique simulée)."""
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        r = client.post(
            "/api/presets",
            json={"name": name, "base_url": base_url, "api_key": "k", "model": "m"},
            headers=headers,
        )
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


# ── Durcissement : validation de base_url à la CRÉATION (POST) ───────


def test_create_preset_private_url_rejected(client, auth_headers, monkeypatch):
    """POST avec base_url privée : 400 actionnable, aucune ligne créée."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "local", "base_url": "https://192.168.1.10", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    body = r.get_json()["error"]
    assert "AIH_ALLOW_PRIVATE_LLM_HOSTS" in body   # message actionnable
    assert "192.168.1.10" not in body              # pas de reflet de l'URL
    listing = client.get("/api/presets", headers=auth_headers).get_json()
    assert all(p["name"] != "local" for p in listing)  # aucun preset fantôme


def test_create_preset_private_url_allowed_with_optin(client, auth_headers, monkeypatch):
    """Même base_url privée, mais opt-in activé : 201."""
    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "192.168.1.10")
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "local-ok", "base_url": "https://192.168.1.10", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 201


def test_create_preset_public_url_accepted(client, auth_headers, monkeypatch):
    """URL publique (résolution publique) : 201."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    _ensure_user()
    _create_preset(client, auth_headers)


def test_create_preset_http_public_rejected_without_http_optin(client, auth_headers, monkeypatch):
    """http:// sans AIH_ALLOW_HTTP_LLM : refusé avec indication de l'opt-in."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    monkeypatch.delenv("AIH_ALLOW_HTTP_LLM", raising=False)
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "http", "base_url": "http://api.example.com", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "AIH_ALLOW_HTTP_LLM" in r.get_json()["error"]


def test_create_preset_http_private_optin_accepted(client, auth_headers, monkeypatch):
    """LLM local http (Ollama/LM Studio) : accepté dès que l'hôte est opt-in."""
    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "localhost")
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "ollama", "base_url": "http://localhost:11434", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 201


# ── Durcissement : PUT ne revalide QUE si base_url change ────────────


def test_put_rename_private_preset_not_blocked(client, auth_headers, monkeypatch):
    """Renommer un preset interne existant ne revalide PAS base_url."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    pid = _seed_preset("https://10.0.0.7")
    r = client.put(f"/api/presets/{pid}", json={"name": "renommé"}, headers=auth_headers)
    assert r.status_code == 200
    single = client.get(f"/api/presets/{pid}", headers=auth_headers).get_json()
    assert single["name"] == "renommé"
    assert single["base_url"] == "https://10.0.0.7"  # inchangée


def test_put_same_private_base_url_not_revalidated(client, auth_headers, monkeypatch):
    """PUT qui renvoie base_url à l'identique (champ pré-rempli) : non bloqué."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    pid = _seed_preset("https://10.0.0.7")
    r = client.put(
        f"/api/presets/{pid}",
        json={"name": "maj", "base_url": "https://10.0.0.7"},
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_put_change_base_url_to_private_rejected(client, auth_headers, monkeypatch):
    """PUT modifiant base_url vers une cible interne : 400 actionnable."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    r = client.put(
        f"/api/presets/{pid}",
        json={"base_url": "https://192.168.1.10"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    body = r.get_json()["error"]
    assert "AIH_ALLOW_PRIVATE_LLM_HOSTS" in body
    assert "192.168.1.10" not in body
    single = client.get(f"/api/presets/{pid}", headers=auth_headers).get_json()
    assert single["base_url"] == "https://api.example.com"  # non modifiée


def test_put_change_base_url_to_public_accepted(client, auth_headers):
    """PUT modifiant base_url vers une cible publique : 200."""
    _ensure_user()
    pid = _create_preset(client, auth_headers)
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        r = client.put(
            f"/api/presets/{pid}",
            json={"base_url": "https://api.example.com"},
            headers=auth_headers,
        )
    assert r.status_code == 200


# ── Durcissement : _get_model_context (routes/enhance.py) validé ─────


def _clear_ctx_cache():
    from routes import enhance

    enhance._model_context_cache.clear()


def test_get_model_context_private_ollama_no_optin_no_request(monkeypatch):
    """Hôte interne Ollama sans opt-in : aucune requête émise, retour 0."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    monkeypatch.delenv("AIH_ALLOW_HTTP_LLM", raising=False)
    from routes import enhance

    _clear_ctx_cache()
    with mock.patch("requests.post") as mpost, mock.patch("requests.get") as mget:
        ctx = enhance._get_model_context("http://ollama.local:11434", "", "qwen2.5:7b")
    assert ctx == 0
    mpost.assert_not_called()
    mget.assert_not_called()


def test_get_model_context_private_ollama_optin_works(monkeypatch):
    """Hôte interne Ollama opt-in : POST /api/show part et est parsé."""
    monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", "ollama.local")
    from routes import enhance

    _clear_ctx_cache()
    with mock.patch("requests.post", return_value=_FakeResp(json_data={
            "model_info": {"qwen2.context_length": 131072}})) as mpost:
        ctx = enhance._get_model_context("http://ollama.local:11434", "", "qwen2.5:7b")
    assert ctx == 131072
    assert mpost.call_count == 1
    assert "/api/show" in mpost.call_args.args[0]


def test_get_model_context_private_openai_no_optin_no_request(monkeypatch):
    """Hôte interne non-Ollama (branche GET /models) sans opt-in : aucune requête."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    from routes import enhance

    _clear_ctx_cache()
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get") as mget:
        ctx = enhance._get_model_context("https://rebind.example.com", "k", "m1")
    assert ctx == 0
    mget.assert_not_called()


def test_get_model_context_public_openai_works(monkeypatch):
    """Hôte public : branche GET /models fonctionne (non-régression)."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    from routes import enhance

    _clear_ctx_cache()
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
         mock.patch("requests.get", return_value=_FakeResp(json_data={
             "data": [{"id": "m1", "context_length": 32768}]})) as mget:
        ctx = enhance._get_model_context("https://api.example.com", "k", "m1")
    assert ctx == 32768
    mget.assert_called_once()


# ── Contrôle négatif : la garde DOIT être invoquée ───────────────────


def test_negative_control_guard_invoked_by_get_model_context(monkeypatch):
    """Contrôle négatif : _get_model_context DOIT appeler la validation SSRF.

    Retirer/casser la garde (ne plus invoquer _validate_llm_base_url) ferait
    échouer ce test : c'est le filet anti-régression de la protection.
    """
    from routes import enhance

    _clear_ctx_cache()
    seen = []
    real_validate = enhance._validate_llm_base_url

    def spy(url):
        seen.append(url)
        return real_validate(url)

    monkeypatch.setattr(enhance, "_validate_llm_base_url", spy)
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    with mock.patch("requests.post") as mpost, mock.patch("requests.get") as mget:
        ctx = enhance._get_model_context("http://ollama.local:11434", "", "m1")
    assert ctx == 0
    assert seen, "la garde anti-SSRF doit être invoquée avant tout appel réseau"
    mpost.assert_not_called()
    mget.assert_not_called()


def test_negative_control_guard_invoked_on_create(client, auth_headers, monkeypatch):
    """Contrôle négatif : POST /api/presets DOIT valider base_url.

    Sans la garde, cette création retournerait 201 : le test échouerait.
    """
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "x", "base_url": "https://192.168.1.10", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 400


# ── Matrice : publique/privée × opt-in activé/désactivé ─────────────


@pytest.mark.parametrize("base_url, optin, http_optin, expected", [
    # 1. URL publique + opt-in off → acceptée
    ("https://8.8.8.8", "", "", 201),
    # 2. URL publique + opt-in on (hôte différent, sans effet) → acceptée
    ("https://8.8.8.8", "example.org", "", 201),
    # 3. URL privée + opt-in off → refusée (400)
    ("https://192.168.1.10", "", "", 400),
    # 4. URL privée + opt-in on (hôte listé) → acceptée
    ("https://192.168.1.10", "192.168.1.10", "", 201),
    # 5. LLM local http + opt-in hôte → accepté (http autorisé pour l'opt-in)
    ("http://192.168.1.10", "192.168.1.10", "", 201),
    # 6. http seul (AIH_ALLOW_HTTP_LLM) ne suffit PAS pour un hôte privé
    ("http://192.168.1.10", "", "1", 400),
])
def test_create_preset_matrix_public_private_optin(
    client, auth_headers, monkeypatch, base_url, optin, http_optin, expected
):
    """Matrice de décision création : (publique|privée) × (opt-in on|off)."""
    monkeypatch.delenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", raising=False)
    monkeypatch.delenv("AIH_ALLOW_HTTP_LLM", raising=False)
    if optin:
        monkeypatch.setenv("AIH_ALLOW_PRIVATE_LLM_HOSTS", optin)
    if http_optin:
        monkeypatch.setenv("AIH_ALLOW_HTTP_LLM", http_optin)
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "matrice", "base_url": base_url, "api_key": "k", "model": "m"},
        headers=auth_headers,
    )
    assert r.status_code == expected, r.get_data(as_text=True)
