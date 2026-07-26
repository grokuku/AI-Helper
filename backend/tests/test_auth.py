"""Tests for the JWT authentication module (backend/auth.py)."""

import time

import pytest


# ── Token creation & verification ────────────────────────────────────


class TestJWTCreation:
    """Tests for create_jwt / create_refresh_token / verify_jwt."""

    def test_access_token_is_valid(self):
        from auth import create_jwt, verify_jwt

        token = create_jwt("user-abc", role="user")
        assert isinstance(token, str)
        assert len(token) > 0

        payload = verify_jwt(token)
        assert payload is not None
        assert payload["sub"] == "user-abc"
        assert payload["role"] == "user"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_refresh_token_is_valid(self):
        from auth import create_refresh_token, verify_jwt

        token = create_refresh_token("user-xyz")
        assert isinstance(token, str)

        payload = verify_jwt(token)
        assert payload is not None
        assert payload["sub"] == "user-xyz"
        assert payload["type"] == "refresh"
        assert "exp" in payload

    def test_access_token_has_correct_type(self):
        """The access token must carry type='access'."""
        from auth import create_jwt, verify_jwt

        token = create_jwt("u1")
        payload = verify_jwt(token)
        assert payload["type"] == "access"

    def test_refresh_token_has_correct_type(self):
        """The refresh token must carry type='refresh'."""
        from auth import create_refresh_token, verify_jwt

        token = create_refresh_token("u1")
        payload = verify_jwt(token)
        assert payload["type"] == "refresh"

    def test_expired_token_rejected(self):
        from auth import create_jwt, verify_jwt

        # Create a token that expires in 1 second
        import os
        old = os.environ.get("JWT_ACCESS_EXPIRY")
        os.environ["JWT_ACCESS_EXPIRY"] = "1"
        try:
            # Reset the secret cache isn't needed for expiry; just create and sleep
            token = create_jwt("user-exp")
        finally:
            if old is not None:
                os.environ["JWT_ACCESS_EXPIRY"] = old
            else:
                os.environ.pop("JWT_ACCESS_EXPIRY", None)

        time.sleep(2)
        payload = verify_jwt(token)
        assert payload is None

    def test_invalid_token_rejected(self):
        from auth import verify_jwt

        assert verify_jwt("not-a-valid-jwt") is None
        assert verify_jwt("") is None

    def test_token_with_extra_claims(self):
        from auth import create_jwt, verify_jwt

        token = create_jwt("u1", extra_claims={"guild": "1234", "nickname": "Bob"})
        payload = verify_jwt(token)
        assert payload["guild"] == "1234"
        assert payload["nickname"] == "Bob"


# ── /api/auth/me endpoint ────────────────────────────────────────────


class TestAuthMe:
    """Tests for the GET /api/auth/me endpoint."""

    def test_auth_me_without_token(self, client):
        """No Authorization header → 401."""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_auth_me_with_valid_token(self, client, make_token):
        """A valid Bearer token for an existing user → 200 with user info."""
        from db import get_db

        # Insert a user into the test DB
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, display_name, avatar, role) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test-user-123", "testuser", "Test User", "", "user"),
        )
        conn.commit()
        conn.close()

        token = make_token("test-user-123")
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "test-user-123"
        assert data["username"] == "testuser"

    def test_auth_me_with_invalid_token(self, client):
        """An invalid Bearer token → 401."""
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid-token-string"},
        )
        assert resp.status_code == 401


# ── jwt_required decorator ───────────────────────────────────────────


class TestJwtRequiredDecorator:
    """Tests for the @jwt_required decorator logic.

    Instead of registering routes on the app (which Flask forbids after the
    first request), we test the wrapper function directly inside a request
    context.
    """

    def _build_wrapper(self, app):
        """Build a jwt_required-wrapped function for testing."""
        from auth import jwt_required
        from flask import jsonify, g

        @jwt_required
        def _protected():
            return jsonify({"user": g.jwt_user["sub"]})

        return _protected

    def test_jwt_required_blocks_no_token(self, app):
        with app.test_request_context("/"):
            protected = self._build_wrapper(app)
            resp = protected()
            assert resp[1] == 401

    def test_jwt_required_accepts_valid_token(self, app, make_token):
        token = make_token("decorator-user")
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {token}"}
        ):
            protected = self._build_wrapper(app)
            result = protected()
            # On success the wrapper returns the Response directly (status 200)
            resp = result[0] if isinstance(result, tuple) else result
            status = result[1] if isinstance(result, tuple) else resp.status_code
            assert status == 200
            from flask import json
            data = json.loads(resp.data)
            assert data["user"] == "decorator-user"

    def test_jwt_required_rejects_refresh_token(self, app):
        from auth import create_refresh_token

        refresh = create_refresh_token("u1")
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {refresh}"}
        ):
            protected = self._build_wrapper(app)
            resp = protected()
            assert resp[1] == 401

    def test_jwt_required_rejects_bad_scheme(self, app):
        with app.test_request_context(
            "/", headers={"Authorization": "Basic sometoken"}
        ):
            protected = self._build_wrapper(app)
            resp = protected()
            assert resp[1] == 401

    def test_jwt_required_rejects_invalid_token(self, app):
        with app.test_request_context(
            "/", headers={"Authorization": "Bearer invalid-token-string"}
        ):
            protected = self._build_wrapper(app)
            resp = protected()
            assert resp[1] == 401


# ── _login_required / _admin_required helpers ────────────────────────


class TestLoginAdminRequired:
    """Tests for the _login_required and _admin_required helper functions."""

    def test_login_required_without_auth(self, app):
        from security.auth import _login_required

        with app.test_request_context("/"):
            result = _login_required()
            assert result is not None
            assert result[1] == 401

    def test_login_required_with_token(self, app, make_token):
        from security.auth import _login_required

        token = make_token("login-test-user")
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {token}"}
        ):
            # Need to insert the user so _sync_session_user doesn't choke
            from db import get_db
            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
                ("login-test-user", "lt", "user"),
            )
            conn.commit()
            conn.close()

            result = _login_required()
            assert result is None  # None means access allowed

    def test_admin_required_without_auth(self, app):
        from security.auth import _admin_required

        with app.test_request_context("/"):
            result = _admin_required()
            assert result is not None
            assert result[1] == 401

    def test_admin_required_as_admin(self, app, make_token):
        from security.auth import _admin_required

        # Insert an admin user
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("admin-user-1", "admin", "admin"),
        )
        # Ensure there's at least one admin so is_admin checks role
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("another-admin", "admin2", "admin"),
        )
        conn.commit()
        conn.close()

        token = make_token("admin-user-1", role="admin")
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {token}"}
        ):
            result = _admin_required()
            # Should return None (access granted) if user is admin in DB
            # Note: JWT role claim is separate from DB role; _admin_required checks DB
            # So we need the DB to say this user is admin
            assert result is None

    def test_admin_required_as_non_admin(self, app, make_token):
        from security.auth import _admin_required

        # Ensure at least one admin exists so non-admins are actually restricted
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("root-admin", "root", "admin"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
            ("plain-user-1", "plain", "user"),
        )
        conn.commit()
        conn.close()

        token = make_token("plain-user-1", role="user")
        with app.test_request_context(
            "/", headers={"Authorization": f"Bearer {token}"}
        ):
            result = _admin_required()
            assert result is not None
            assert result[1] == 403