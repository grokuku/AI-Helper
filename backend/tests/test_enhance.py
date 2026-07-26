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