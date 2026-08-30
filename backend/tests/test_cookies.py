"""Tests des cookies de session sécurisés (Secure/SameSite/HttpOnly) et ProxyFix."""


def test_proxyfix_installed():
    """L'app est enveloppée par ProxyFix (TLS vu par Flask derrière Caddy)."""
    from extensions import app
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert isinstance(app.wsgi_app, ProxyFix)


def test_session_cookie_secure_on_https(client):
    """En https (X-Forwarded-Proto, comme envoyé par Caddy), le cookie de
    session porte Secure + SameSite=Lax + HttpOnly."""
    with client.session_transaction() as sess:
        sess["user"] = {"id": "u1"}
    resp = client.get("/api/auth/logout", environ_base={"HTTP_X_FORWARDED_PROTO": "https"})
    sc = resp.headers.get("Set-Cookie", "")
    assert "Secure" in sc
    assert "SameSite=Lax" in sc
    assert "HttpOnly" in sc


def test_session_cookie_not_secure_on_http(client):
    """En HTTP local (dev), pas de flag Secure → le login continue de marcher."""
    with client.session_transaction() as sess:
        sess["user"] = {"id": "u1"}
    resp = client.get("/api/auth/logout")
    sc = resp.headers.get("Set-Cookie", "")
    assert "Secure" not in sc
    assert "SameSite=Lax" in sc


def test_insecure_cookies_escape_hatch(client, monkeypatch):
    """AIH_INSECURE_COOKIES=1 force Secure off même en https."""
    monkeypatch.setenv("AIH_INSECURE_COOKIES", "1")
    with client.session_transaction() as sess:
        sess["user"] = {"id": "u1"}
    resp = client.get("/api/auth/logout", environ_base={"HTTP_X_FORWARDED_PROTO": "https"})
    assert "Secure" not in resp.headers.get("Set-Cookie", "")


def test_before_request_secure_flag_via_proxyfix(client):
    """X-Forwarded-Proto: https (Caddy) → SESSION_COOKIE_SECURE devient True."""
    from extensions import app

    client.get("/", environ_base={"HTTP_X_FORWARDED_PROTO": "https"})
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_before_request_secure_flag_plain_http(client):
    """Sans proxy → SESSION_COOKIE_SECURE reste False (dev local)."""
    from extensions import app

    client.get("/")
    assert app.config["SESSION_COOKIE_SECURE"] is False
