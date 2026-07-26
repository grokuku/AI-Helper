"""Tests for backend/embeddings.py and the semantic search route.

All external calls (Ollama, Gemini API) are mocked — no real HTTP.
"""

import json
from unittest.mock import patch, MagicMock

import pytest


# ── cosine_similarity (pure maths, no network) ────────────────────────


class TestCosineSimilarity:
    """Unit tests for cosine_similarity."""

    def test_identical_vectors(self):
        from embeddings import cosine_similarity

        vec = [1.0, 2.0, 3.0]
        result = cosine_similarity(vec, vec)
        assert result == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        from embeddings import cosine_similarity

        # (1, 0) and (0, 1) are orthogonal → 0.0
        result = cosine_similarity([1.0, 0.0], [0.0, 1.0])
        assert result == pytest.approx(0.0)

    def test_opposite_vectors(self):
        from embeddings import cosine_similarity

        result = cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        assert result == pytest.approx(-1.0)

    def test_different_vectors(self):
        from embeddings import cosine_similarity

        result = cosine_similarity([1.0, 0.0], [1.0, 1.0])
        assert 0.0 < result < 1.0

    def test_zero_vector_returns_zero(self):
        """A zero-norm vector should return 0.0 (guard against div-by-zero)."""
        from embeddings import cosine_similarity

        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0

    def test_empty_vectors_returns_zero(self):
        """Empty lists have norm 0 → should return 0.0, not raise."""
        from embeddings import cosine_similarity

        assert cosine_similarity([], []) == 0.0


# ── is_available (mocked network) ─────────────────────────────────────


class TestIsAvailable:
    """Tests for is_available / is_ollama_available."""

    def test_is_available_with_ollama(self):
        """When Ollama responds with the configured model → True."""
        from embeddings import is_available, set_config

        set_config(provider="ollama")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"models": [{"name": "nomic-embed-text:latest"}]}
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("embeddings.urllib.request.urlopen", return_value=mock_resp):
            assert is_available() is True

    def test_is_available_without_service(self):
        """When urlopen raises an exception → False."""
        from embeddings import is_available, set_config

        set_config(provider="ollama")

        with patch(
            "embeddings.urllib.request.urlopen",
            side_effect=Exception("Connection refused"),
        ):
            assert is_available() is False

    def test_is_available_gemini_with_key(self):
        """Gemini availability only requires an API key to be set."""
        from embeddings import is_available, set_config

        set_config(provider="gemini", gemini_api_key="test-key")
        assert is_available() is True

    def test_is_available_gemini_without_key(self):
        """No Gemini API key → False."""
        from embeddings import is_available, set_config

        set_config(provider="gemini", gemini_api_key="")
        assert is_available() is False


# ── generate_embedding (mocked) ───────────────────────────────────────


class TestGenerateEmbedding:
    """Tests for generate_embedding via mocked Ollama / Gemini."""

    def test_generate_embedding_ollama(self):
        """Mock Ollama API → returns the embedding vector."""
        from embeddings import generate_embedding, set_config

        set_config(provider="ollama")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"model": "nomic-embed-text", "embeddings": [[0.1, 0.2, 0.3]]}
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("embeddings.urllib.request.urlopen", return_value=mock_resp):
            result = generate_embedding("hello world")

        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)
        assert result == [0.1, 0.2, 0.3]

    def test_generate_embedding_gemini(self):
        """Mock Gemini API → returns the embedding vector."""
        from embeddings import generate_embedding, set_config

        set_config(provider="gemini", gemini_api_key="fake-key")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"embedding": {"values": [0.4, 0.5, 0.6]}}
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("embeddings.urllib.request.urlopen", return_value=mock_resp):
            result = generate_embedding("hello world")

        assert isinstance(result, list)
        assert result == [0.4, 0.5, 0.6]

    def test_generate_embedding_ollama_error(self):
        """When urlopen raises URLError → RuntimeError is raised (wrapped)."""
        import urllib.error
        from embeddings import generate_embedding, set_config

        set_config(provider="ollama")

        with patch(
            "embeddings.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with pytest.raises(RuntimeError, match="Impossible de se connecter"):
                generate_embedding("hello")

    def test_generate_embedding_gemini_no_key(self):
        """Gemini provider without API key → RuntimeError."""
        from embeddings import generate_embedding, set_config

        set_config(provider="gemini", gemini_api_key="")

        with pytest.raises(RuntimeError, match="Cle API Gemini manquante"):
            generate_embedding("hello")

    def test_generate_embedding_empty_text(self):
        """Empty string still goes through the (mocked) pipeline."""
        from embeddings import generate_embedding, set_config

        set_config(provider="ollama")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"model": "nomic-embed-text", "embeddings": [[0.0, 0.0, 0.0]]}
        ).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("embeddings.urllib.request.urlopen", return_value=mock_resp):
            result = generate_embedding("")

        assert result == [0.0, 0.0, 0.0]


# ── Semantic search route (GET /api/search/semantic) ──────────────────


class TestSemanticSearchRoute:
    """Tests for the semantic search endpoint."""

    def test_semantic_search_without_auth(self, client):
        """No Authorization header → 401."""
        resp = client.get("/api/search/semantic", query_string={"q": "test"})
        assert resp.status_code == 401

    def test_semantic_search_empty_query(self, client, auth_headers):
        """Empty query string → 200 with empty list (short-circuit)."""
        # Still need a valid user for auth to pass
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) "
            "VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.commit()
        conn.close()

        resp = client.get(
            "/api/search/semantic",
            query_string={"q": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_semantic_search_with_auth(self, client, auth_headers):
        """Authenticated search with mocked embedding → 200 with results."""
        from db import get_db
        from embeddings import set_config

        set_config(provider="ollama")

        # Insert a user + a keyword with an embedding
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) "
            "VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO keywords "
            "(id, keyword, description, section_id, section_title, nsfw, "
            " privacy_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (9001, "test keyword", "a test desc", "s1", "Section 1", 0, "public"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO keyword_embeddings (keyword_id, embedding) "
            "VALUES (?, ?)",
            (9001, json.dumps([1.0, 0.0, 0.0])),
        )
        conn.commit()
        conn.close()

        fake_query_vec = [1.0, 0.0, 0.0]  # identical → similarity 1.0

        with patch("routes.search.is_available", return_value=True), \
             patch(
                 "routes.search.generate_embedding",
                 return_value=fake_query_vec,
             ):
            resp = client.get(
                "/api/search/semantic",
                query_string={"q": "test"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["keyword"] == "test keyword"
        assert data[0]["score"] == pytest.approx(1.0)

    def test_semantic_search_service_unavailable(self, client, auth_headers):
        """When is_available returns False → 400 error."""
        from db import get_db
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (id, username, role) "
            "VALUES (?, ?, ?)",
            ("test-user-123", "testuser", "user"),
        )
        conn.commit()
        conn.close()

        with patch("routes.search.is_available", return_value=False):
            resp = client.get(
                "/api/search/semantic",
                query_string={"q": "test"},
                headers=auth_headers,
            )

        assert resp.status_code == 400
        assert "error" in resp.get_json()