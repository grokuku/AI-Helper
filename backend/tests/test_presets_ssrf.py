"""Tests anti-SSRF pour les endpoints presets (list-models / <id>/models)."""

import socket
from unittest import mock


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None, raise_error=False):
        self.status_code = status_code
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
    """Un preset BDD avec base_url privée est refusé à l'appel."""
    _ensure_user()
    r = client.post(
        "/api/presets",
        json={"name": "evil", "base_url": "https://10.0.0.7", "api_key": "k"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    pid = r.get_json()["id"]
    with mock.patch("requests.get") as mock_get:
        resp = client.get(f"/api/presets/{pid}/models", headers=auth_headers)
    assert resp.status_code == 502
    mock_get.assert_not_called()
