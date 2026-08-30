"""Tests du stockage hashé des API tokens (aih_...) et de leur expiration."""

import hashlib

from routes.helpers import get_db


def _ensure_user(user_id="token-user-1"):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", "user"),
    )
    conn.commit()
    conn.close()


def _auth_headers_for(token):
    return {"Authorization": f"Bearer {token}"}


# ── POST /api/auth/token : le token est retourné une fois, stocké hashé ──


def test_token_generated_stored_hashed(client, make_token):
    _ensure_user("token-user-1")
    headers = {"Authorization": f"Bearer {make_token('token-user-1')}"}
    resp = client.post("/api/auth/token", headers=headers)
    assert resp.status_code == 200
    token = resp.get_json()["token"]
    assert token.startswith("aih_")

    # La BDD ne contient pas le token en clair, seulement son hash SHA-256
    conn = get_db()
    row = conn.execute(
        "SELECT api_token_hash, api_token, api_token_created_at FROM users WHERE id = ?",
        ("token-user-1",),
    ).fetchone()
    conn.close()
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert row["api_token_hash"] == expected_hash
    assert row["api_token"] is None  # plus de copie en clair
    assert row["api_token_created_at"] is not None


def test_get_token_never_returns_existing_plaintext(client, make_token):
    _ensure_user("token-user-2")
    headers = {"Authorization": f"Bearer {make_token('token-user-2')}"}
    client.post("/api/auth/token", headers=headers)  # création
    resp = client.get("/api/auth/token", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["token"] is None
    assert data["exists"] is True


def test_token_authenticates_via_hash(client, make_token):
    """Le token généré permet ensuite de s'authentifier (Bearer)."""
    _ensure_user("token-user-3")
    headers = {"Authorization": f"Bearer {make_token('token-user-3')}"}
    token = client.post("/api/auth/token", headers=headers).get_json()["token"]

    resp = client.get("/api/auth/me", headers=_auth_headers_for(token))
    assert resp.status_code == 200
    assert resp.get_json()["id"] == "token-user-3"


def test_legacy_plaintext_token_still_works_and_migrates(client, make_token):
    """Un ancien token en clair en BDD reste valide et est migré vers le hash."""
    _ensure_user("token-user-4")
    legacy = "aih_legacy_plaintext_token_1234567890abcdef"
    conn = get_db()
    conn.execute(
        "UPDATE users SET api_token = ? WHERE id = ?", (legacy, "token-user-4")
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/auth/me", headers=_auth_headers_for(legacy))
    assert resp.status_code == 200
    assert resp.get_json()["id"] == "token-user-4"

    # Migration effectuée : le clair a disparu, le hash est en place
    conn = get_db()
    row = conn.execute(
        "SELECT api_token, api_token_hash FROM users WHERE id = ?", ("token-user-4",)
    ).fetchone()
    conn.close()
    assert row["api_token"] is None
    expected = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
    assert row["api_token_hash"] == expected


def test_invalid_token_rejected(client, make_token):
    resp = client.get("/api/auth/me", headers=_auth_headers_for("aih_totally_wrong"))
    assert resp.status_code == 401


# ── Expiration ─────────────────────────────────────────────────────────


def test_expired_token_rejected(client, make_token, monkeypatch):
    """Un token créé il y a plus de AIH_TOKEN_MAX_AGE_DAYS est refusé."""
    monkeypatch.setenv("AIH_TOKEN_MAX_AGE_DAYS", "30")
    _ensure_user("token-user-5")
    headers = {"Authorization": f"Bearer {make_token('token-user-5')}"}
    token = client.post("/api/auth/token", headers=headers).get_json()["token"]

    # Vieillir artificiellement le token en BDD (il y a 31 jours)
    conn = get_db()
    conn.execute(
        "UPDATE users SET api_token_created_at = datetime('now', '-31 days') WHERE id = ?",
        ("token-user-5",),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/auth/me", headers=_auth_headers_for(token))
    assert resp.status_code == 401


def test_token_without_created_at_not_expired(client, make_token):
    """Un token legacy (created_at NULL) n'expire jamais (migration transparente)."""
    _ensure_user("token-user-6")
    legacy = "aih_legacy_no_date_1234567890abcdef"
    conn = get_db()
    conn.execute(
        "UPDATE users SET api_token = ?, api_token_created_at = NULL WHERE id = ?",
        (legacy, "token-user-6"),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/auth/me", headers=_auth_headers_for(legacy))
    assert resp.status_code == 200
