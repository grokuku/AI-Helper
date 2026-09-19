"""Hint optionnel des elements EP (Elements Picker).

Le hint est un PRÉFIXE DE RÉSOLUTION pour tous les types :

* ``filter`` : préfixe appliqué au mot-clé pioché (comportement historique) ;
* ``text``   : préfixe appliqué au texte (nouveau) ;
* ``raw``    : préfixe appliqué au texte verbatim (nouveau) ; sans hint, le
  texte est ajouté TEL QUEL (contrat préservé).

Aucun « : » n'est inséré quand le hint est absent ou vide.
"""


def _ensure_user(uid="test-user-123"):
    """Garantit l'existence de l'utilisateur (contraintes FK)."""
    from db import get_db
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            (uid, "testuser", "user"),
        )
        conn.commit()
    finally:
        conn.close()


def _make_filter(conn, uid, keyword):
    """Insère un keyword + un filtre public + son cache. Retourne (kw_id, fid)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO keywords (keyword, description) VALUES (?, ?)",
        (keyword, "test"),
    )
    kw_id = cur.lastrowid
    cur.execute(
        "INSERT INTO saved_filters (user_id, name, config, is_public) VALUES (?, ?, '{}', 1)",
        (uid, "zzz_ep_hint_filter"),
    )
    fid = cur.lastrowid
    cur.execute(
        "INSERT INTO filter_cache (filter_id, keyword_id) VALUES (?, ?)",
        (fid, kw_id),
    )
    conn.commit()
    return kw_id, fid


def _drop_filter(conn, kw_id, fid):
    """Supprime les lignes de test (ordre respectant les FK)."""
    conn.execute("DELETE FROM filter_cache WHERE filter_id = ?", (fid,))
    conn.execute("DELETE FROM saved_filters WHERE id = ?", (fid,))
    conn.execute("DELETE FROM keywords WHERE id = ?", (kw_id,))
    conn.commit()


class TestResolveEpKeywordsHint:
    """``routes.enhance._resolve_ep_keywords`` (branche text)."""

    def test_text_with_hint(self, app_ctx):
        from db import get_db
        from routes.enhance import _resolve_ep_keywords
        conn = get_db()
        try:
            out = _resolve_ep_keywords(conn, "test-user-123", [
                {"type": "text", "text": "x", "hint": "hair"},
            ])
        finally:
            conn.close()
        assert out == ["hair: x"]

    def test_text_without_hint(self, app_ctx):
        from db import get_db
        from routes.enhance import _resolve_ep_keywords
        conn = get_db()
        try:
            out = _resolve_ep_keywords(conn, "test-user-123", [
                {"type": "text", "text": "x"},
            ])
        finally:
            conn.close()
        assert out == ["x"]

    def test_text_empty_hint_no_colon(self, app_ctx):
        from db import get_db
        from routes.enhance import _resolve_ep_keywords
        conn = get_db()
        try:
            out = _resolve_ep_keywords(conn, "test-user-123", [
                {"type": "text", "text": "x", "hint": ""},
                {"type": "text", "text": "y", "hint": "   "},
            ])
        finally:
            conn.close()
        assert out == ["x", "y"]

    def test_text_order_and_mixed_hints(self, app_ctx):
        from db import get_db
        from routes.enhance import _resolve_ep_keywords
        conn = get_db()
        try:
            out = _resolve_ep_keywords(conn, "test-user-123", [
                {"type": "text", "text": "a", "hint": "h1"},
                {"type": "text", "text": "b"},
                {"type": "text", "text": "c", "hint": "h2"},
            ])
        finally:
            conn.close()
        assert out == ["h1: a", "b", "h2: c"]


class TestResolveEpKeywordsFilterHint:
    """Non-régression : la branche filter applique toujours le hint."""

    def test_filter_with_and_without_hint(self, app_ctx):
        from db import get_db
        from routes.enhance import _resolve_ep_keywords
        uid = "test-user-123"
        _ensure_user(uid)
        conn = get_db()
        kw_id, fid = _make_filter(conn, uid, "zzz_ep_hint_kw")
        try:
            out_hint = _resolve_ep_keywords(conn, uid, [
                {"type": "filter", "id": fid, "hint": "hair"},
            ])
            out_none = _resolve_ep_keywords(conn, uid, [
                {"type": "filter", "id": fid},
            ])
            out_empty = _resolve_ep_keywords(conn, uid, [
                {"type": "filter", "id": fid, "hint": ""},
            ])
        finally:
            _drop_filter(conn, kw_id, fid)
            conn.close()
        assert out_hint == ["hair: zzz_ep_hint_kw"]
        assert out_none == ["zzz_ep_hint_kw"]
        assert out_empty == ["zzz_ep_hint_kw"]


class TestGenerateRouteHint:
    """``POST /api/generate`` : branches raw et filter."""

    def test_raw_with_hint(self, client, auth_headers):
        r = client.post(
            "/api/generate",
            headers=auth_headers,
            json={"elements": [{"type": "raw", "text": "x", "hint": "hair"}]},
        )
        assert r.status_code == 200
        assert r.get_json()["prompt"] == "hair: x"

    def test_raw_without_hint_is_verbatim(self, client, auth_headers):
        r = client.post(
            "/api/generate",
            headers=auth_headers,
            json={"elements": [{"type": "raw", "text": "x"}]},
        )
        assert r.status_code == 200
        assert r.get_json()["prompt"] == "x"

    def test_raw_empty_hint_is_verbatim(self, client, auth_headers):
        r = client.post(
            "/api/generate",
            headers=auth_headers,
            json={"elements": [{"type": "raw", "text": "x", "hint": ""}]},
        )
        assert r.status_code == 200
        assert r.get_json()["prompt"] == "x"

    def test_filter_with_and_without_hint(self, client, auth_headers):
        uid = "test-user-123"
        _ensure_user(uid)
        from db import get_db
        conn = get_db()
        kw_id, fid = _make_filter(conn, uid, "zzz_ep_hint_kw")
        try:
            r_hint = client.post(
                "/api/generate",
                headers=auth_headers,
                json={"elements": [{"type": "filter", "id": fid, "hint": "hair"}]},
            )
            r_none = client.post(
                "/api/generate",
                headers=auth_headers,
                json={"elements": [{"type": "filter", "id": fid}]},
            )
        finally:
            _drop_filter(conn, kw_id, fid)
            conn.close()
        assert r_hint.status_code == 200
        assert r_hint.get_json()["prompt"] == "hair: zzz_ep_hint_kw"
        assert r_none.status_code == 200
        assert r_none.get_json()["prompt"] == "zzz_ep_hint_kw"
