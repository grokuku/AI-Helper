"""Tests des clés API MULTIPLES ET NOMMÉES (table ``api_tokens``).

Couvre :
  - création de plusieurs clés → toutes s'authentifient ;
  - révocation INDIVIDUELLE (seule la clé révoquée cesse de fonctionner) ;
  - renommage ;
  - la liste n'expose JAMAIS la clé ni son hash ;
  - migration de la clé historique → nom générique « Clé d'origine » +
    rétrocompatibilité (la clé existante s'authentifie toujours) ;
  - ``last_used_at`` / IP / User-Agent tracés, de façon THROTTLÉE ;
  - limite de clés actives ;
  - isolation stricte entre utilisateurs ;
  - AUCUNE expiration sur les clés nommées ;
  - les anciennes routes ``/api/auth/token`` restent fonctionnelles.

Contrôles négatifs par mutation documentés dans le test lui-même (retirer
``revoked_at IS NULL`` au lookup → la révocation ne coupe plus rien ; exposer
le hash dans la liste → rouge ; révoquer toutes les clés → rouge).
"""

import hashlib
import sqlite3

from routes.helpers import get_db

# ── Helpers ────────────────────────────────────────────────────────────


def _ensure_user(user_id):
    conn = get_db()
    # INSERT OR IGNORE (et NON OR REPLACE) : un REPLACE supprimerait puis
    # recréerait la ligne users, ce qui déclencherait le ON DELETE CASCADE de
    # api_tokens et effacerait les clés déjà créées pour cet utilisateur.
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, role) VALUES (?, ?, 'user')",
        (user_id, f"user_{user_id[-8:]}"),
    )
    conn.commit()
    conn.close()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create(client, user_id, name):
    _ensure_user(user_id)
    headers = {"Authorization": f"Bearer {_jwt(user_id)}"}
    resp = client.post("/api/auth/tokens", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _jwt(user_id):
    from auth import create_jwt

    return create_jwt(user_id, role="user")


def _list(client, user_id):
    headers = {"Authorization": f"Bearer {_jwt(user_id)}"}
    resp = client.get("/api/auth/tokens", headers=headers)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["tokens"]


# ── Création + authentification de PLUSIEURS clés ─────────────────────


def test_multiple_named_keys_all_authenticate(client):
    user = "tokens-multi-user"
    k1 = _create(client, user, "ComfyUI salon")
    k2 = _create(client, user, "ComfyUI bureau")
    k3 = _create(client, user, "Serveur CI")

    for k in (k1, k2, k3):
        assert k["token"].startswith("aih_")
        assert k["prefix"] == k["token"][:12]
        resp = client.get("/api/auth/me", headers=_bearer(k["token"]))
        assert resp.status_code == 200, k["name"]
        assert resp.get_json()["id"] == user


def test_created_keys_are_stored_hashed_only(client):
    user = "tokens-hash-user"
    k = _create(client, user, "Hash check")
    conn = get_db()
    row = conn.execute(
        "SELECT token_hash, prefix FROM api_tokens WHERE user_id = ?", (user,)
    ).fetchone()
    conn.close()
    assert row["token_hash"] == hashlib.sha256(k["token"].encode("utf-8")).hexdigest()
    # La clé en clair n'apparaît nulle part en BDD.
    assert k["token"] not in (row["token_hash"] or "")


# ── Révocation INDIVIDUELLE ───────────────────────────────────────────


def test_revoke_one_key_keeps_others_working(client):
    user = "tokens-revoke-user"
    k1 = _create(client, user, "A garder")
    k2 = _create(client, user, "A révoquer")
    k3 = _create(client, user, "A garder aussi")

    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    resp = client.delete(f"/api/auth/tokens/{k2['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["revoked"] is True

    # La clé révoquée ne s'authentifie PLUS...
    assert client.get("/api/auth/me", headers=_bearer(k2["token"])).status_code == 401
    # ...mais les autres continuent.
    assert client.get("/api/auth/me", headers=_bearer(k1["token"])).status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(k3["token"])).status_code == 200


def test_revoke_is_idempotent_and_preserves_row(client):
    user = "tokens-revoke-idem"
    k = _create(client, user, "Idempotente")
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    assert client.delete(f"/api/auth/tokens/{k['id']}", headers=headers).status_code == 200
    assert client.delete(f"/api/auth/tokens/{k['id']}", headers=headers).status_code == 200
    rows = [t for t in _list(client, user) if t["id"] == k["id"]]
    assert len(rows) == 1 and rows[0]["revoked"] is True


# ── Renommage ─────────────────────────────────────────────────────────


def test_rename_key(client):
    user = "tokens-rename-user"
    k = _create(client, user, "Ancien nom")
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    resp = client.patch(
        f"/api/auth/tokens/{k['id']}", json={"name": "  Nouveau nom  "}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "Nouveau nom"
    rows = [t for t in _list(client, user) if t["id"] == k["id"]]
    assert rows[0]["name"] == "Nouveau nom"
    # Le renommage ne change PAS la clé.
    assert client.get("/api/auth/me", headers=_bearer(k["token"])).status_code == 200


# ── La liste n'expose NI clé NI hash ──────────────────────────────────


def test_list_never_exposes_token_or_hash(client):
    user = "tokens-list-user"
    k = _create(client, user, "Visible")
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    resp = client.get("/api/auth/tokens", headers=headers)
    raw = resp.get_data(as_text=True)
    assert k["token"] not in raw, "la clé en clair ne doit JAMAIS apparaître"
    token_hash = hashlib.sha256(k["token"].encode("utf-8")).hexdigest()
    assert token_hash not in raw, "le hash ne doit JAMAIS apparaître"
    allowed = {
        "id", "name", "prefix", "created_at", "last_used_at",
        "last_used_ip", "last_used_user_agent", "revoked", "revoked_at",
    }
    for t in resp.get_json()["tokens"]:
        assert set(t.keys()) <= allowed, t.keys()
        assert "token" not in t and "token_hash" not in t


# ── Validation du nom ─────────────────────────────────────────────────


def test_name_validation(client):
    user = "tokens-validate-user"
    _ensure_user(user)
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    for payload in ({}, {"name": ""}, {"name": "   "}, {"name": None},
                    {"name": "x" * 65}, {"name": "ligne\nnewline"}):
        resp = client.post("/api/auth/tokens", json=payload, headers=headers)
        assert resp.status_code == 400, payload
        assert resp.get_json().get("error")
    # Nom valide (espaces trimés, 64 chars max).
    ok = client.post("/api/auth/tokens", json={"name": "  ok  "}, headers=headers)
    assert ok.status_code == 201
    assert ok.get_json()["name"] == "ok"
    assert client.post(
        "/api/auth/tokens", json={"name": "y" * 64}, headers=headers
    ).status_code == 201


# ── Limite de clés actives ────────────────────────────────────────────


def test_active_keys_limit(client):
    user = "tokens-limit-user"
    _ensure_user(user)
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    from routes.auth import API_TOKEN_MAX_ACTIVE

    for i in range(API_TOKEN_MAX_ACTIVE):
        r = client.post("/api/auth/tokens", json={"name": f"clé {i}"}, headers=headers)
        assert r.status_code == 201, (i, r.get_json())
    over = client.post("/api/auth/tokens", json={"name": "trop"}, headers=headers)
    assert over.status_code == 400
    assert str(API_TOKEN_MAX_ACTIVE) in over.get_json()["error"]

    # Révoquer une clé libère un slot.
    first_id = _list(client, user)[-1]["id"]
    client.delete(f"/api/auth/tokens/{first_id}", headers=headers)
    assert client.post(
        "/api/auth/tokens", json={"name": "après révocation"}, headers=headers
    ).status_code == 201


# ── Isolation stricte entre utilisateurs ──────────────────────────────


def test_cross_user_isolation(client):
    alice = "tokens-iso-alice"
    bob = "tokens-iso-bob"
    k = _create(client, alice, "À Alice")
    bob_headers = {"Authorization": f"Bearer {_jwt(bob)}"}

    # Bob ne voit pas la clé d'Alice.
    assert all(t["id"] != k["id"] for t in _list(client, bob))
    # Bob ne peut ni renommer ni révoquer la clé d'Alice → 404.
    assert client.patch(
        f"/api/auth/tokens/{k['id']}", json={"name": "pirate"}, headers=bob_headers
    ).status_code == 404
    assert client.delete(
        f"/api/auth/tokens/{k['id']}", headers=bob_headers
    ).status_code == 404
    # La clé d'Alice est intacte et fonctionne.
    assert client.get("/api/auth/me", headers=_bearer(k["token"])).status_code == 200


def test_tokens_endpoints_require_auth(client):
    assert client.get("/api/auth/tokens").status_code == 401
    assert client.post("/api/auth/tokens", json={"name": "x"}).status_code == 401
    assert client.patch("/api/auth/tokens/1", json={"name": "x"}).status_code == 401
    assert client.delete("/api/auth/tokens/1").status_code == 401


# ── AUCUNE expiration ─────────────────────────────────────────────────


def test_named_key_never_expires(client, monkeypatch):
    """Même avec AIH_TOKEN_MAX_AGE_DAYS=1 et une clé vieille de 10 ans, elle
    reste valide : AUCUNE logique d'expiration sur les clés nommées."""
    monkeypatch.setenv("AIH_TOKEN_MAX_AGE_DAYS", "1")
    user = "tokens-no-expiry"
    k = _create(client, user, "Imortelle")
    conn = get_db()
    conn.execute(
        "UPDATE api_tokens SET created_at = datetime('now', '-3650 days') WHERE id = ?",
        (k["id"],),
    )
    conn.commit()
    conn.close()
    assert client.get("/api/auth/me", headers=_bearer(k["token"])).status_code == 200


# ── last_used_at + IP/UA (throttlé) ───────────────────────────────────


def test_last_used_recorded(client, monkeypatch):
    monkeypatch.setenv("AIH_TOKEN_USAGE_THROTTLE_S", "0")  # écrire à chaque requête
    from security import auth as secauth

    secauth._TOKEN_USAGE_LAST_WRITE.clear()
    user = "tokens-lastused-user"
    k = _create(client, user, "Tracée")

    resp = client.get(
        "/api/auth/me",
        headers={**_bearer(k["token"]), "User-Agent": "ComfyUI-Test/1.0"},
    )
    assert resp.status_code == 200

    conn = get_db()
    row = conn.execute(
        "SELECT last_used_at, last_used_ip, last_used_user_agent FROM api_tokens WHERE id = ?",
        (k["id"],),
    ).fetchone()
    conn.close()
    assert row["last_used_at"] is not None
    assert row["last_used_user_agent"] == "ComfyUI-Test/1.0"
    assert row["last_used_ip"] is not None


def test_last_used_write_is_throttled(client, monkeypatch):
    user = "tokens-throttle-user"
    k = _create(client, user, "Throttle")

    # Throttle large : la 1re utilisation écrit, la 2e NON.
    monkeypatch.setenv("AIH_TOKEN_USAGE_THROTTLE_S", "3600")
    from security import auth as secauth

    secauth._TOKEN_USAGE_LAST_WRITE.clear()

    assert client.get("/api/auth/me", headers=_bearer(k["token"])).status_code == 200
    # Sentinel : si le throttle échoue, la 2e requête l'écrasera.
    conn = get_db()
    conn.execute(
        "UPDATE api_tokens SET last_used_ip = 'SENTINEL' WHERE id = ?", (k["id"],)
    )
    conn.commit()
    conn.close()

    assert client.get("/api/auth/me", headers=_bearer(k["token"])).status_code == 200
    conn = get_db()
    row = conn.execute(
        "SELECT last_used_ip FROM api_tokens WHERE id = ?", (k["id"],)
    ).fetchone()
    conn.close()
    assert row["last_used_ip"] == "SENTINEL", "l'écriture doit être throttlée"


# ── Migration : clé historique → « Clé d'origine » ────────────────────


def _temp_db_with_historic(api_token_hash=None, api_token=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, api_token_hash TEXT, api_token TEXT)"
    )
    conn.execute(
        "CREATE TABLE api_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, "
        "name TEXT, token_hash TEXT UNIQUE, prefix TEXT, revoked_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id, api_token_hash, api_token) VALUES ('u1', ?, ?)",
        (api_token_hash, api_token),
    )
    return conn


def test_migrate_imports_historic_hash_with_generic_name():
    from db.init import DEFAULT_API_TOKEN_NAME, _migrate_api_tokens

    h = hashlib.sha256(b"aih_historic_hash").hexdigest()
    conn = _temp_db_with_historic(api_token_hash=h)
    _migrate_api_tokens(conn)
    rows = conn.execute("SELECT user_id, name, token_hash FROM api_tokens").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == DEFAULT_API_TOKEN_NAME == "Clé d'origine"
    assert rows[0]["token_hash"] == h

    # Idempotent.
    _migrate_api_tokens(conn)
    assert conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 1


def test_migrate_imports_historic_plaintext_hashed():
    from db.init import _migrate_api_tokens

    plain = "aih_legacy_plain_abcdef0123456789"
    conn = _temp_db_with_historic(api_token=plain)
    _migrate_api_tokens(conn)
    row = conn.execute("SELECT token_hash, prefix FROM api_tokens").fetchone()
    assert row["token_hash"] == hashlib.sha256(plain.encode("utf-8")).hexdigest()
    assert row["prefix"] == plain[:12]


def test_migrate_does_not_resurrect_revoked_origin():
    from db.init import _migrate_api_tokens

    h = hashlib.sha256(b"aih_revoked_origin").hexdigest()
    conn = _temp_db_with_historic(api_token_hash=h)
    _migrate_api_tokens(conn)
    conn.execute("UPDATE api_tokens SET revoked_at = datetime('now')")
    conn.commit()
    # Un redémarrage ne réimporte PAS une clé révoquée.
    _migrate_api_tokens(conn)
    assert conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 1


def test_historic_imported_key_still_authenticates(client):
    """RÉTROCOMPAT : la clé existante (importée en « Clé d'origine »)
    s'authentifie toujours — aucune config ComfyUI en place n'est cassée."""
    from db.init import _migrate_api_tokens

    user = "tokens-retro-user"
    _ensure_user(user)
    legacy = "aih_retrocompat_0123456789abcdef0123456789abcdef"
    h = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
    conn = get_db()
    conn.execute(
        "UPDATE users SET api_token_hash = ?, api_token = NULL WHERE id = ?", (h, user)
    )
    conn.commit()
    _migrate_api_tokens(conn)
    conn.commit()
    conn.close()

    # La clé historique s'authentifie toujours (via la ligne importée).
    resp = client.get("/api/auth/me", headers=_bearer(legacy))
    assert resp.status_code == 200
    assert resp.get_json()["id"] == user

    # ...et elle apparaît dans la liste sous le nom générique, sans fuite.
    assert any(t["name"] == "Clé d'origine" for t in _list(client, user))


def test_revoking_imported_origin_blocks_legacy_fallback(client):
    """Révoquer la clé d'origine importée coupe AUSSI le repli historique."""
    from db.init import _migrate_api_tokens

    user = "tokens-revoke-origin"
    _ensure_user(user)
    legacy = "aih_origin_revoke_0123456789abcdef0123456789abcdef"
    h = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
    conn = get_db()
    conn.execute(
        "UPDATE users SET api_token_hash = ?, api_token = NULL WHERE id = ?", (h, user)
    )
    conn.commit()
    _migrate_api_tokens(conn)
    conn.commit()
    origin = conn.execute(
        "SELECT id FROM api_tokens WHERE user_id = ? AND token_hash = ?", (user, h)
    ).fetchone()
    conn.close()

    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    assert client.delete(f"/api/auth/tokens/{origin['id']}", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 401


# ── Les anciennes routes restent fonctionnelles ───────────────────────


def test_legacy_token_route_still_works(client):
    user = "tokens-legacy-route"
    _ensure_user(user)
    headers = {"Authorization": f"Bearer {_jwt(user)}"}
    token = client.post("/api/auth/token", headers=headers).get_json()["token"]
    assert token.startswith("aih_")
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200
    # GET ne réaffiche pas la clé (hashée).
    got = client.get("/api/auth/token", headers=headers).get_json()
    assert got["token"] is None and got["exists"] is True
