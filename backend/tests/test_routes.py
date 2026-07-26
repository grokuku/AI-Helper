"""Tests for the Flask route endpoints.

Covers filters, templates, generate (auth guard), and the 404 handler.
"""

import pytest


# ── /api/filters ─────────────────────────────────────────────────────


class TestFilters:
    """Tests for the GET /api/filters endpoint."""

    def test_get_filters_without_auth(self, client):
        """No auth → 401."""
        resp = client.get("/api/filters")
        assert resp.status_code == 401

    def test_get_filters_with_auth(self, client, auth_headers):
        """Authenticated request → 200 and a JSON list."""
        # Ensure the user exists in the DB (auth_headers uses test-user-123)
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/filters", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)


# ── /api/prompts/templates ────────────────────────────────────────────


class TestTemplates:
    """Tests for the GET /api/prompts/templates endpoint."""

    def test_get_templates_without_auth(self, client):
        """No auth → 401."""
        resp = client.get("/api/prompts/templates")
        assert resp.status_code == 401

    def test_get_templates_with_auth(self, client, auth_headers):
        """Authenticated request → 200 and a JSON list."""
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/prompts/templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)


# ── /api/generate (POST) ──────────────────────────────────────────────


class TestGenerate:
    """Tests for the POST /api/generate endpoint."""

    def test_post_generate_without_auth(self, client):
        """No auth → 401."""
        resp = client.post(
            "/api/generate",
            json={"elements": [], "random_count": 0},
        )
        assert resp.status_code == 401

    def test_post_generate_with_auth_empty(self, client, auth_headers):
        """Authenticated but empty request → 200 with empty prompt."""
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.commit()
        conn.close()

        resp = client.post(
            "/api/generate",
            json={"elements": [], "random_count": 0},
            headers=auth_headers,
        )
        # With no elements and no random_count, should return empty prompt
        assert resp.status_code == 200
        data = resp.get_json()
        assert "prompt" in data
        assert data["prompt"] == ""


# ── 404 handler ──────────────────────────────────────────────────────


class TestErrorHandlers:
    """Tests for global error handlers."""

    def test_404_handler(self, client):
        """Unknown route → 404 JSON response."""
        resp = client.get("/api/nonexistent-route-12345")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data
        assert data["error"] == "Not Found"

    def test_404_returns_json(self, client):
        """The 404 handler must return JSON, not HTML."""
        resp = client.get("/api/does-not-exist")
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data is not None
        assert "status_code" in data
        assert data["status_code"] == 404