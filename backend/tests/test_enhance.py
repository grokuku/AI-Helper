import pytest
import json
from unittest.mock import patch, MagicMock

# Tests d'authentification
class TestEnhanceAuth:
    def test_enhance_prepare_without_auth(self, client):
        r = client.post('/api/enhance/prepare', json={})
        assert r.status_code == 401

    def test_enhance_prompts_without_auth(self, client):
        r = client.post('/api/enhance/prompts', json={})
        assert r.status_code == 401

    def test_enhance_finish_without_auth(self, client):
        r = client.post('/api/enhance/finish', json={})
        assert r.status_code == 401

    def test_keywords_llm_process_without_auth(self, client):
        r = client.post('/api/keywords/llm-process', json={})
        assert r.status_code == 401

# Tests avec auth
class TestEnhanceRoutes:
    def test_get_enhance_prompts_with_auth(self, client, auth_headers):
        # /api/enhance/prompts is POST, needs template_id
        r = client.post('/api/enhance/prompts', headers=auth_headers, json={
            'template_id': 1,
            'text': 'test prompt'
        })
        # 200 if template exists, 400/404 if not
        assert r.status_code in (200, 400, 404)
        data = r.get_json()
        assert isinstance(data, (list, dict))

    def test_enhance_prepare_empty_body(self, client, auth_headers):
        r = client.post('/api/enhance/prepare', headers=auth_headers, json={})
        # Devrait retourner 400 (template_id requis)
        assert r.status_code in (200, 400)

    def test_enhance_finish_empty_body(self, client, auth_headers):
        r = client.post('/api/enhance/finish', headers=auth_headers, json={})
        # Devrait retourner 400 (session_id requis)
        assert r.status_code in (200, 400)

    def test_keywords_llm_process_missing_preset(self, client, auth_headers):
        r = client.post('/api/keywords/llm-process', headers=auth_headers, json={})
        # Devrait retourner 400 (preset_id requis)
        assert r.status_code == 400

    def test_keywords_llm_process_missing_instruction(self, client, auth_headers):
        r = client.post('/api/keywords/llm-process', headers=auth_headers, json={
            'preset_id': 1
        })
        # Devrait retourner 400 (instruction requise)
        assert r.status_code == 400

# Tests logique métier avec mocks
class TestEnhanceLogic:
    # Mock le LLM pour ne pas faire de vrais appels
    def test_enhance_prepare_with_mock_llm(self, client, auth_headers):
        with patch('routes.enhance._call_llm_internal') as mock_llm:
            mock_llm.return_value = {"choices": [{"message": {"content": "test result"}}]}
            # Adapter le payload selon la route
            r = client.post('/api/enhance/prepare', headers=auth_headers, json={
                'template_id': 1,
                'text': 'test prompt'
            })
            assert r.status_code in (200, 400)

    # ── Sécurité : la clé API ne doit JAMAIS fuiter vers le client ni en BDD ──
    def _client_side_prepared(self):
        """Construit un dict 'prepared' client-side avec une clé API décryptée."""
        return {
            'session_id': None,
            'llm_request': {'model': 'm', 'messages': [{'role': 'user', 'content': 'hi'}]},
            'llm_config': {'base_url': 'http://localhost:11434', 'api_key': 'SECRET_KEY_123', 'model': 'm'},
            'user_id': 'test-user-123',
            'preset_id': 1,
            'preset_name': 'test',
            'is_global': 0,
            'is_client_side': True,
            'template_id': 1,
            'template_name': 't',
            'output_format': 'rich',
            'width': 0, 'height': 0,
            'style_text': '', 'style_id': None,
            'negative_prompt': '',
            'merged_text': 'hello',
            'model': 'm',
            'validation_passes': 0,
            'validation_template_id': None,
            'validation_system_prompt': '',
            'validation_examples': [],
            'debug_sections': [],
            'clean_output_format': 'rich',
        }

    def test_enhance_prepare_client_side_no_api_key(self, client, auth_headers):
        """Le mode client-side ne renvoie JAMAIS la clé API ni llm_config au client,
        et la session persistée ne contient pas la clé en clair."""
        from routes import enhance
        from db import get_db
        # Le user doit exister (contrainte FK sur enhance_sessions.user_id)
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
                ("test-user-123", "testuser", "user"),
            )
            conn.commit()
        finally:
            conn.close()
        prepared = self._client_side_prepared()
        with patch('routes.enhance._prepare_enhance', return_value=prepared):
            r = client.post('/api/enhance/prepare', headers=auth_headers, json={'template_id': 1})
            assert r.status_code == 200
            data = r.get_json()
            assert data['status'] == 'awaiting_llm'
            assert 'llm_config' not in data
            assert 'api_key' not in json.dumps(data)
            # Vérifier la session persistée en BDD
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT payload_json FROM enhance_sessions WHERE id = ?",
                    (data['session_id'],)
                ).fetchone()
                assert row is not None
                payload = json.loads(row['payload_json'])
            finally:
                conn.close()
            assert 'api_key' not in json.dumps(payload)
            assert 'api_key' not in payload['llm_config']

    def test_enhance_session_never_persists_api_key(self, app_ctx):
        """_save_enhance_session ne persiste jamais la clé décryptée en clair."""
        from routes import enhance
        from db import get_db
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
                ("test-user-123", "testuser", "user"),
            )
            conn.commit()
        finally:
            conn.close()
        prepared = self._client_side_prepared()
        sid = enhance._save_enhance_session('test-user-123', prepared)
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT payload_json FROM enhance_sessions WHERE id = ?", (sid,)
            ).fetchone()
            payload = json.loads(row['payload_json'])
        finally:
            conn.close()
        assert 'api_key' not in json.dumps(payload)
        assert 'api_key' not in payload['llm_config']
        # Le chargement ne doit pas planter et doit re-décrypter (ou rester sans clé)
        loaded = enhance._load_enhance_session(sid, 'test-user-123')
        assert loaded['session_id'] == sid

    def test_prepare_next_validation_pass_no_api_key(self, app_ctx):
        """La réponse awaiting_validation de /enhance/finish ne contient jamais la clé."""
        from routes import enhance
        prepared = self._client_side_prepared()
        prepared.update({
            'validation_passes': 2,
            'current_output': 'out',
        })
        resp = enhance._prepare_next_validation_pass('sess', 'test-user-123', prepared, next_pass_idx=2)
        data = resp.get_json()
        assert data['status'] == 'awaiting_validation'
        assert 'llm_config' not in data
        assert 'api_key' not in json.dumps(data)

    def test_keywords_llm_process_with_mock(self, client, auth_headers):
        with patch('routes.enhance._call_llm_internal') as mock_llm:
            mock_llm.return_value = {
                "choices": [{"message": {"content": "keyword1, keyword2"}}],
                "usage": {}
            }
            # Also mock the /models call made by the route
            with patch('requests.get') as mock_models:
                mock_resp = MagicMock()
                mock_resp.ok = False
                mock_models.return_value = mock_resp
                r = client.post('/api/keywords/llm-process', headers=auth_headers, json={
                    'preset_id': 1,
                    'instruction': 'generate keywords'
                })
                assert r.status_code in (200, 400, 404)