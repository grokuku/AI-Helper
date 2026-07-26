"""Tests for the keywords CRUD routes (backend/routes/keywords.py).

Covers:
  - Authentication guards (401 / 403)
  - GET /api/keywords (list)
  - POST /api/keywords (create)
  - PUT /api/keywords/<id> (update)
  - DELETE /api/keywords/<id> (delete)
  - Edge cases: duplicates, filters, nsfw, privacy
"""

import pytest

from db import get_db


# ── Helpers ──────────────────────────────────────────────────────────


def _ensure_user(user_id="test-user-123", role="user"):
    """Insert (or replace) a user row so that role checks work in the DB."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", role),
    )
    conn.commit()
    conn.close()


def _ensure_admin_exists():
    """Insert a dedicated admin so that is_admin() no longer defaults to True."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        ("root-admin-001", "root_admin", "admin"),
    )
    conn.commit()
    conn.close()


def _make_headers(make_token, user_id="test-user-123", role="user"):
    """Return auth headers for a given user/role."""
    token = make_token(user_id, role=role)
    return {"Authorization": f"Bearer {token}"}


def _cleanup_keywords():
    """Delete all keywords to start from a clean slate."""
    conn = get_db()
    conn.execute("DELETE FROM keywords")
    conn.execute("DELETE FROM keyword_embeddings")
    conn.commit()
    conn.close()


VALID_PAYLOAD = {
    "keyword": "TestKeyword",
    "description": "A test keyword description",
    "section_id": "1",
    "section_title": "Section One",
    "subsection_id": "1.1",
    "subsection_title": "Sub One",
    "nsfw": 0,
    "privacy_status": "private",
}


# ── Authentication tests ─────────────────────────────────────────────


class TestAuth:
    """Authentication and authorization guards."""

    def test_list_keywords_without_auth(self, client):
        resp = client.get("/api/keywords")
        assert resp.status_code == 401

    def test_create_keyword_without_auth(self, client):
        resp = client.post("/api/keywords", json=VALID_PAYLOAD)
        assert resp.status_code == 401

    def test_update_keyword_without_auth(self, client):
        resp = client.put("/api/keywords/1", json=VALID_PAYLOAD)
        assert resp.status_code == 401

    def test_delete_keyword_without_auth(self, client):
        resp = client.delete("/api/keywords/1")
        assert resp.status_code == 401

    def test_pending_keywords_without_auth(self, client):
        resp = client.get("/api/keywords/pending")
        assert resp.status_code == 401

    def test_review_keyword_without_auth(self, client):
        resp = client.post("/api/keywords/1/review", json={"action": "approve"})
        assert resp.status_code == 401

    def test_pending_keywords_user_not_editor(self, client, make_token):
        """A regular user (not kw_editor/admin) → 403 on pending."""
        _ensure_admin_exists()
        _ensure_user("regular-user", "user")
        headers = _make_headers(make_token, "regular-user", "user")
        resp = client.get("/api/keywords/pending", headers=headers)
        assert resp.status_code == 403

    def test_review_keyword_user_not_editor(self, client, make_token):
        _ensure_admin_exists()
        _ensure_user("regular-user", "user")
        headers = _make_headers(make_token, "regular-user", "user")
        resp = client.post(
            "/api/keywords/1/review",
            json={"action": "approve"},
            headers=headers,
        )
        assert resp.status_code == 403


# ── CRUD: list ───────────────────────────────────────────────────────


class TestList:
    """GET /api/keywords."""

    def test_list_keywords_with_auth(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.get("/api/keywords", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_list_keywords_empty(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.get("/api/keywords", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == []


# ── CRUD: create ─────────────────────────────────────────────────────


class TestCreate:
    """POST /api/keywords."""

    def test_create_keyword_valid(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.post("/api/keywords", json=VALID_PAYLOAD, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data
        assert data["privacy_status"] == "private"

    def test_create_keyword_missing_fields(self, client, auth_headers):
        _ensure_user()
        resp = client.post(
            "/api/keywords",
            json={"keyword": "NoDesc"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_create_keyword_empty_string(self, client, auth_headers):
        _ensure_user()
        resp = client.post(
            "/api/keywords",
            json={"keyword": "   ", "description": "   "},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_create_keyword_no_body(self, client, auth_headers):
        _ensure_user()
        resp = client.post(
            "/api/keywords", data=None, content_type="application/json",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_create_keyword_nsfw_flag(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        payload = {**VALID_PAYLOAD, "keyword": "NSFWTest", "nsfw": 1}
        resp = client.post("/api/keywords", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        kw_id = resp.get_json()["id"]

        # Verify via GET list that nsfw is 1
        resp = client.get("/api/keywords", headers=auth_headers)
        rows = resp.get_json()
        created = [r for r in rows if r["id"] == kw_id]
        assert len(created) == 1
        assert created[0]["nsfw"] == 1

    def test_create_keyword_duplicate(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.post("/api/keywords", json=VALID_PAYLOAD, headers=auth_headers)
        assert resp.status_code == 201

        # Same keyword (case-insensitive) → 409
        payload2 = {**VALID_PAYLOAD, "keyword": "testkeyword"}
        resp2 = client.post("/api/keywords", json=payload2, headers=auth_headers)
        assert resp2.status_code == 409

    def test_create_keyword_public_downgraded_for_non_editor(self, client, make_token):
        """A non-editor requesting 'public' gets 'public_pending'."""
        _ensure_admin_exists()
        _ensure_user("regular-user", "user")
        headers = _make_headers(make_token, "regular-user", "user")
        _cleanup_keywords()
        payload = {**VALID_PAYLOAD, "keyword": "PubKw", "privacy_status": "public"}
        resp = client.post("/api/keywords", json=payload, headers=headers)
        assert resp.status_code == 201
        assert resp.get_json()["privacy_status"] == "public_pending"


# ── CRUD: get by list filtering ──────────────────────────────────────


class TestGetAndUpdate:
    """PUT /api/keywords/<id> and verification via GET."""

    def _create_one(self, client, auth_headers, **overrides):
        _ensure_user()
        _cleanup_keywords()
        payload = {**VALID_PAYLOAD, **overrides}
        resp = client.post("/api/keywords", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_get_keyword_via_list(self, client, auth_headers):
        kw_id = self._create_one(client, auth_headers)
        resp = client.get("/api/keywords", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.get_json()
        ids = [r["id"] for r in rows]
        assert kw_id in ids

    def test_update_keyword(self, client, auth_headers):
        kw_id = self._create_one(client, auth_headers)
        update_data = {
            "keyword": "UpdatedKw",
            "description": "Updated description",
            "nsfw": 1,
        }
        resp = client.put(
            f"/api/keywords/{kw_id}", json=update_data, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.get_json()["id"] == kw_id

        # Verify the change
        resp = client.get("/api/keywords", headers=auth_headers)
        rows = resp.get_json()
        kw = [r for r in rows if r["id"] == kw_id][0]
        assert kw["keyword"] == "UpdatedKw"
        assert kw["description"] == "Updated description"
        assert kw["nsfw"] == 1

    def test_update_keyword_nonexistent(self, client, auth_headers):
        _ensure_user()
        resp = client.put(
            "/api/keywords/999999",
            json={"keyword": "X", "description": "Y"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_keyword_missing_fields(self, client, auth_headers):
        kw_id = self._create_one(client, auth_headers)
        resp = client.put(
            f"/api/keywords/{kw_id}",
            json={"keyword": "", "description": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_update_keyword_duplicate_name(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        # Create two keywords
        r1 = client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "KwAlpha"},
            headers=auth_headers,
        )
        assert r1.status_code == 201
        r2 = client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "KwBeta"},
            headers=auth_headers,
        )
        assert r2.status_code == 201
        id2 = r2.get_json()["id"]

        # Try to rename id2 to "KwAlpha" → 409
        resp = client.put(
            f"/api/keywords/{id2}",
            json={"keyword": "KwAlpha", "description": "desc"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_update_keyword_not_owner(self, client, make_token):
        """A user who is not the owner or editor → 403."""
        _ensure_admin_exists()
        _ensure_user("owner-user", "user")
        _ensure_user("other-user", "user")
        _cleanup_keywords()

        owner_headers = _make_headers(make_token, "owner-user", "user")
        other_headers = _make_headers(make_token, "other-user", "user")

        # Owner creates
        resp = client.post(
            "/api/keywords", json=VALID_PAYLOAD, headers=owner_headers
        )
        assert resp.status_code == 201
        kw_id = resp.get_json()["id"]

        # Other user tries to update
        resp = client.put(
            f"/api/keywords/{kw_id}",
            json={"keyword": "Hacked", "description": "nope"},
            headers=other_headers,
        )
        assert resp.status_code == 403


# ── CRUD: delete ────────────────────────────────────────────────────


class TestDelete:
    """DELETE /api/keywords/<id>."""

    def _create_one(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.post("/api/keywords", json=VALID_PAYLOAD, headers=auth_headers)
        assert resp.status_code == 201
        return resp.get_json()["id"]

    def test_delete_keyword(self, client, auth_headers):
        kw_id = self._create_one(client, auth_headers)
        resp = client.delete(f"/api/keywords/{kw_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_delete_keyword_nonexistent(self, client, auth_headers):
        _ensure_user()
        resp = client.delete("/api/keywords/999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_then_get(self, client, auth_headers):
        kw_id = self._create_one(client, auth_headers)
        # Delete
        resp = client.delete(f"/api/keywords/{kw_id}", headers=auth_headers)
        assert resp.status_code == 200
        # Verify it's gone from the list
        resp = client.get("/api/keywords", headers=auth_headers)
        rows = resp.get_json()
        ids = [r["id"] for r in rows]
        assert kw_id not in ids

    def test_delete_keyword_not_owner(self, client, make_token):
        """A user who is not the owner or admin → 403."""
        _ensure_admin_exists()
        _ensure_user("owner-user", "user")
        _ensure_user("attacker-user", "user")
        _cleanup_keywords()

        owner_headers = _make_headers(make_token, "owner-user", "user")
        attacker_headers = _make_headers(make_token, "attacker-user", "user")

        resp = client.post(
            "/api/keywords", json=VALID_PAYLOAD, headers=owner_headers
        )
        assert resp.status_code == 201
        kw_id = resp.get_json()["id"]

        resp = client.delete(f"/api/keywords/{kw_id}", headers=attacker_headers)
        assert resp.status_code == 403


# ── Edge cases: filters ─────────────────────────────────────────────


class TestFilters:
    """Query-parameter filtering on GET /api/keywords."""

    def test_list_keywords_nsfw_filter(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()

        # Create one SFW and one NSFW
        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "SfwOne", "nsfw": 0},
            headers=auth_headers,
        )
        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "NsfwOne", "nsfw": 1},
            headers=auth_headers,
        )

        # Filter nsfw=1
        resp = client.get("/api/keywords?nsfw=1", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.get_json()
        assert all(r["nsfw"] == 1 for r in rows)
        assert len(rows) == 1

        # Filter nsfw=0
        resp = client.get("/api/keywords?nsfw=0", headers=auth_headers)
        rows = resp.get_json()
        assert all(r["nsfw"] == 0 for r in rows)
        assert len(rows) == 1

    def test_list_keywords_q_search(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()

        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "Apple", "description": "A fruit"},
            headers=auth_headers,
        )
        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "Banana", "description": "Yellow"},
            headers=auth_headers,
        )

        resp = client.get("/api/keywords?q=apple", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.get_json()
        assert len(rows) == 1
        assert rows[0]["keyword"] == "Apple"

    def test_list_keywords_mine_only(self, client, make_token):
        """mine=1 filters to only the current user's keywords."""
        _ensure_admin_exists()
        _ensure_user("user-a", "user")
        _ensure_user("user-b", "user")
        _cleanup_keywords()

        headers_a = _make_headers(make_token, "user-a", "user")
        headers_b = _make_headers(make_token, "user-b", "user")

        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "KwA", "privacy_status": "private"},
            headers=headers_a,
        )
        client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "KwB", "privacy_status": "private"},
            headers=headers_b,
        )

        resp = client.get("/api/keywords?mine=1", headers=headers_a)
        assert resp.status_code == 200
        rows = resp.get_json()
        assert len(rows) == 1
        assert rows[0]["keyword"] == "KwA"


# ── list_or_create_keywords: combined endpoint ──────────────────────


class TestListOrCreate:
    """The combined GET/POST /api/keywords endpoint."""

    def test_list_or_create_empty(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.get("/api/keywords", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_or_create_create(self, client, auth_headers):
        _ensure_user()
        _cleanup_keywords()
        resp = client.post("/api/keywords", json=VALID_PAYLOAD, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data

        # Now list should have 1
        resp = client.get("/api/keywords", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1


# ── Moderation: pending / review ────────────────────────────────────


class TestModeration:
    """Pending list and review (kw_editor only)."""

    def test_pending_keywords_as_editor(self, client, make_token):
        """An admin/kw_editor can access pending list."""
        _ensure_user("test-editor", "kw_editor")
        _ensure_admin_exists()
        headers = _make_headers(make_token, "test-editor", "kw_editor")
        resp = client.get("/api/keywords/pending", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_review_keyword_approve(self, client, make_token):
        _ensure_admin_exists()
        _ensure_user("test-editor", "kw_editor")
        _ensure_user("regular-user", "user")
        _cleanup_keywords()

        editor_headers = _make_headers(make_token, "test-editor", "kw_editor")
        user_headers = _make_headers(make_token, "regular-user", "user")

        # User creates a keyword with public_pending status
        resp = client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "PendingKw", "privacy_status": "public_pending"},
            headers=user_headers,
        )
        assert resp.status_code == 201
        kw_id = resp.get_json()["id"]

        # Editor approves
        resp = client.post(
            f"/api/keywords/{kw_id}/review",
            json={"action": "approve"},
            headers=editor_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()["new_privacy_status"] == "public"

    def test_review_keyword_reject(self, client, make_token):
        _ensure_admin_exists()
        _ensure_user("test-editor", "kw_editor")
        _ensure_user("regular-user", "user")
        _cleanup_keywords()

        editor_headers = _make_headers(make_token, "test-editor", "kw_editor")
        user_headers = _make_headers(make_token, "regular-user", "user")

        resp = client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "RejectKw", "privacy_status": "public_pending"},
            headers=user_headers,
        )
        kw_id = resp.get_json()["id"]

        resp = client.post(
            f"/api/keywords/{kw_id}/review",
            json={"action": "reject"},
            headers=editor_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()["new_privacy_status"] == "private"

    def test_review_keyword_not_pending(self, client, make_token):
        """Reviewing a keyword that is not public_pending → 400."""
        _ensure_admin_exists()
        _ensure_user("test-editor", "kw_editor")
        _cleanup_keywords()

        editor_headers = _make_headers(make_token, "test-editor", "kw_editor")

        # Create a private keyword (not pending)
        resp = client.post("/api/keywords", json=VALID_PAYLOAD, headers=editor_headers)
        kw_id = resp.get_json()["id"]

        resp = client.post(
            f"/api/keywords/{kw_id}/review",
            json={"action": "approve"},
            headers=editor_headers,
        )
        assert resp.status_code == 400

    def test_review_keyword_nonexistent(self, client, make_token):
        _ensure_admin_exists()
        _ensure_user("test-editor", "kw_editor")
        editor_headers = _make_headers(make_token, "test-editor", "kw_editor")

        resp = client.post(
            "/api/keywords/999999/review",
            json={"action": "approve"},
            headers=editor_headers,
        )
        assert resp.status_code == 404

    def test_review_keyword_invalid_action(self, client, make_token):
        _ensure_admin_exists()
        _ensure_user("test-editor", "kw_editor")
        _ensure_user("regular-user", "user")
        _cleanup_keywords()

        editor_headers = _make_headers(make_token, "test-editor", "kw_editor")
        user_headers = _make_headers(make_token, "regular-user", "user")

        resp = client.post(
            "/api/keywords",
            json={**VALID_PAYLOAD, "keyword": "ActKw", "privacy_status": "public_pending"},
            headers=user_headers,
        )
        kw_id = resp.get_json()["id"]

        resp = client.post(
            f"/api/keywords/{kw_id}/review",
            json={"action": "invalid"},
            headers=editor_headers,
        )
        assert resp.status_code == 400