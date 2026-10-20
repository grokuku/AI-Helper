"""Transport VISION de /api/keywords/llm-process — verrouillé ici.

Pourquoi ce test existe : la node ComfyUI expérimentale « AIH Mask Anything »
(nodes/holaf_mask_anything.py du pack) n'envoie PAS d'image via un champ dédié.
Elle construit la conversation OpenAI-compatible elle-même (clé ``messages`` :
un contenu ``user`` multipart ``[{type:text}, {type:image_url, url:data:…}]``,
même forme que ``aih/llm_helper.build_image_content``) et compte sur le RELAIS
VERBATIM de ``/api/keywords/llm-process``. Si un jour la route reconstruisait
les messages (ou filtrait les parts inconnues), la node cesserait SILENCIEUSEMENT
de voir l'image (masque vide, aucune erreur). Ce test rend cette dépendance
explicite et bloquante :

  (a) le contenu multipart (texte + image data-URL base64) arrive TEL QUEL au
      provider (assert sur le corps JSON réellement posté) ;
  (b) le message ``system`` fourni par le client n'est PAS remplacé ;
  (c) l'image n'est JAMAIS interprétée comme une URL à aller chercher (aucune
      requête autre que le POST /chat/completions du provider) ;
  (d) non-régression : sans ``messages``, la construction historique
      system+instruction reste identique.

Stratégie : ``requests.post`` patché (aucune sortie réseau), la VRAIE route et
le VRAI ``_call_llm_internal`` s'exécutent. Preset avec ``context_length``
manuel → pas de sonde de contexte (aucun ``requests.get``).
"""

import socket
from unittest import mock

# ── Fixtures / helpers (conventions test_llm_tool_calls.py) ───────────

_PUBLIC_HOSTS = {"api.example.com": "8.8.8.8"}


def _fake_getaddrinfo(host, *args, **kwargs):
    ip = _PUBLIC_HOSTS.get(host.lower())
    if ip is None:
        raise socket.gaierror(-2, "Name or service not known")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


class _FakeResp:
    """Réponse requests minimaliste (.ok, .status_code, .text, .json())."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.ok = 200 <= status_code < 300
        self.text = ""

    def json(self):
        return self._json


def _ensure_user(user_id="test-user-123", role="user"):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", role),
    )
    conn.commit()
    conn.close()


def _create_vision_preset(client, headers, name="VisionTest"):
    """Preset OpenAI-compatible (base_url publique connue du stub DNS)."""
    payload = {
        "name": name,
        "base_url": "https://api.example.com",
        "api_key": "sk-VISION-7",
        "model": "qwen2.5-vl-7b",
        "context_length": 32768,
        "supports_vision": True,
    }
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        response = client.post("/api/presets", json=payload, headers=headers)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["id"]


# Sortie > 50 caractères : évite la boucle de retry « réponse trop courte ».
VISION_OUTPUT = (
    '{"boxes":[{"x1":0.1,"y1":0.2,"x2":0.4,"y2":0.5,"label":"cat"}]} — fin de reponse.'
)

SYSTEM_PROMPT = "You are a precise visual grounding assistant. Return ONLY JSON."
USER_TEXT = "Image size: 1024x768 pixels (width x height).\nElements to locate: the cat"

# Image 1×1 PNG réelle (base64) : sert de data-URL sans dépendance réseau.
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _vision_messages():
    """Conversation EXACTEMENT telle que la node ComfyUI la construit."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_TEXT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{PNG_B64}"},
                },
            ],
        },
    ]


def _provider_response(content=VISION_OUTPUT):
    return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 42}}


class TestVisionMessagesRelay:
    def test_multimodal_parts_reach_the_provider_verbatim(self, client, auth_headers):
        """(a) + (b) : texte + image data-URL et system client transmis tels quels."""
        _ensure_user()
        preset_id = _create_vision_preset(client, auth_headers)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers or {}, body=json)
            return _FakeResp(json_data=_provider_response())

        with mock.patch("requests.post", side_effect=fake_post):
            response = client.post(
                "/api/keywords/llm-process",
                headers=auth_headers,
                json={"preset_id": preset_id, "messages": _vision_messages()},
            )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["output"].startswith('{"boxes"')

        body = captured["body"]
        assert captured["url"].endswith("/chat/completions")
        assert body["model"] == "qwen2.5-vl-7b"
        messages = body["messages"]
        assert [m["role"] for m in messages] == ["system", "user"], (
            "le system fourni par le client doit être le PREMIER message (pas de préfixe)"
        )
        assert messages[0]["content"] == SYSTEM_PROMPT, "system client préservé mot pour mot"
        parts = messages[1]["content"]
        assert isinstance(parts, list) and len(parts) == 2, "contenu multipart intact"
        assert parts[0] == {"type": "text", "text": USER_TEXT}
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{PNG_B64}"

    def test_image_is_not_fetched_by_the_backend(self, client, auth_headers):
        """(c) : le backend ne « résout » jamais l'image (aucun GET sortant)."""
        _ensure_user()
        preset_id = _create_vision_preset(client, auth_headers, name="VisionNoFetch")

        with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo), \
                mock.patch("requests.post", return_value=_FakeResp(json_data=_provider_response())) as post, \
                mock.patch("requests.get", side_effect=AssertionError("GET interdit")) as get:
            response = client.post(
                "/api/keywords/llm-process",
                headers=auth_headers,
                json={"preset_id": preset_id, "messages": _vision_messages()},
            )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert post.call_count == 1, "un seul appel sortant : le provider"
        assert get.call_count == 0, "aucune requête GET (data-URL locale uniquement)"

    def test_text_only_construction_is_unchanged(self, client, auth_headers):
        """(d) non-régression : sans ``messages``, la conversation historique reste."""
        _ensure_user()
        preset_id = _create_vision_preset(client, auth_headers, name="VisionTextOnly")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(body=json)
            return _FakeResp(json_data=_provider_response("kw1, kw2, kw3 — liste generee."))

        with mock.patch("requests.post", side_effect=fake_post):
            response = client.post(
                "/api/keywords/llm-process",
                headers=auth_headers,
                json={"preset_id": preset_id, "instruction": "genere des mots-cles",
                      "input_text": "un chat sur un canape"},
            )

        assert response.status_code == 200, response.get_data(as_text=True)
        messages = captured["body"]["messages"]
        assert messages[0]["role"] == "system"
        assert "assistant specialise dans la gestion de mots-cles" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert isinstance(messages[1]["content"], str), "mode texte : contenu simple (pas de multipart)"
        assert "genere des mots-cles" in messages[1]["content"]
        assert "un chat sur un canape" in messages[1]["content"]

    def test_messages_must_be_a_list_of_dicts(self, client, auth_headers):
        """Validation inchangée : un contenu non conforme est refusé en 400."""
        _ensure_user()
        preset_id = _create_vision_preset(client, auth_headers, name="VisionBadBody")
        response = client.post(
            "/api/keywords/llm-process",
            headers=auth_headers,
            json={"preset_id": preset_id, "messages": ["nope"]},
        )
        assert response.status_code == 400
        assert "messages" in response.get_json()["error"]

    def test_preset_requires_auth(self, client, auth_headers):
        """Authentification Bearer toujours exigée (aucun anonymat)."""
        preset_id = _create_vision_preset(client, auth_headers, name="VisionAuth")
        response = client.post(
            "/api/keywords/llm-process",
            json={"preset_id": preset_id, "messages": _vision_messages()},
        )
        assert response.status_code in (401, 403)
