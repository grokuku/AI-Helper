"""Tool-calling (étape 1, backend) : plomberie de /api/keywords/llm-process.

Couvre :
  (a) relais des tool_calls du provider vers le client (content null + tool_calls) ;
      le relais est VERBATIM : {id, type, function:{name, arguments}} (forme API
      OpenAI-compatible), reinjectable telle quelle par le client au tour suivant ;
  (b) content null SANS tool_calls ≠ crash (réponse vide propre, retry historique) ;
  (c) un tour d'outil pur n'est PAS re-tenté (1 requête au provider, pas 3-4) ;
  (d) tools / tool_choice transmis tels quels au provider (assert sur le body) ;
  (e) messages fournie remplace la construction classique (support role:'tool') ;
  (f) non-régression : appel sans tools/messages == requête et réponse historiques ;
  (+) unitaires _call_llm_internal / _relay_tool_calls.

Stratégie de mock : `requests.post` est patché (l'appel HTTP du provider) — le
VRAI _call_llm_internal et la VRAIE route s'exécutent, on teste donc la
plomberie réelle (parsing, retry, pass-through) et pas seulement un mock de
haut niveau. Les presets utilisent context_length manuel => jamais de sonde
requests.get (context_source='manual').
"""

import socket
from unittest import mock

from routes.enhance import _call_llm_internal, _relay_tool_calls

# ── Fixtures / helpers (conventions test_context_window.py) ───────────


class _FakeResp:
    """Réponse requests minimaliste (.ok, .status_code, .text, .json())."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.ok = 200 <= status_code < 300
        self.text = ""

    def json(self):
        return self._json


_PUBLIC_HOSTS = {
    "api.example.com": "8.8.8.8",
}


def _fake_getaddrinfo(host, *args, **kwargs):
    ip = _PUBLIC_HOSTS.get(host.lower())
    if ip is None:
        raise socket.gaierror(-2, "Name or service not known")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def _ensure_user(user_id="test-user-123", role="user"):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", role),
    )
    conn.commit()
    conn.close()


def _create_preset(client, headers, base_url="https://api.example.com",
                   model="deepseek-chat", **extra):
    """Crée un preset via l'API (context_length manuel => pas de sonde)."""
    payload = {
        "name": "ToolCallTest",
        "base_url": base_url,
        "api_key": "sk-TOOLCALL-42",
        "model": model,
        "context_length": 8192,
    }
    payload.update(extra)
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        r = client.post("/api/presets", json=payload, headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


# ── Données de référence ──────────────────────────────────────────────

DEFAULT_SYSTEM_MSG = ("Tu es un assistant specialise dans la gestion de mots-cles "
                      "pour un outil de generation de prompt d'images.")

# Sortie texte > 50 caractères : ne déclenche PAS la boucle de retry.
TEXT_OUTPUT = ("kw1, kw2, kw3, kw4, kw5, kw6, kw7, kw8, kw9, kw10 — liste generee "
               "pour les tests de non-regression tool-calling.")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "blobby_set_input",
            "description": "Charge un workflow dans le graphe ComfyUI",
            "parameters": {
                "type": "object",
                "properties": {"workflow": {"type": "string"}},
                "required": ["workflow"],
            },
        },
    },
]

# tool_call au format provider (OpenAI-compatible) : arguments = STRING brute.
# C'est LA forme que le backend doit relayer VERBATIM (type + wrapper function)
# pour que l'echo du tour assistant suivant soit accepte par DeepSeek/OpenAI.
PROVIDER_TOOL_CALL = {
    "id": "call_abc123",
    "type": "function",
    "function": {"name": "blobby_set_input", "arguments": '{"workflow": "cat.json"}'},
}


def _provider_response(message, usage=None):
    return {"choices": [{"message": message}], "usage": usage or {"total_tokens": 7}}


def _post_llm_process(client, headers, pid, **extra):
    payload = {"preset_id": pid}
    payload.update(extra)
    return client.post("/api/keywords/llm-process", headers=headers, json=payload)


# ── (a) Relais des tool_calls provider -> client ──────────────────────


class TestToolCallsRelay:
    def test_pure_tool_call_relayed(self, client, auth_headers):
        """content null + tool_calls => 200 avec tool_calls forme provider VERBATIM."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response(
            {"content": None, "tool_calls": [PROVIDER_TOOL_CALL]}
        )
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)):
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="charge le workflow",
                                  tools=TOOLS, tool_choice="auto")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["tool_calls"] == [PROVIDER_TOOL_CALL]
        # Exigence API DeepSeek/OpenAI : `type` + wrapper `function` presents.
        assert body["tool_calls"][0]["type"] == "function"
        assert body["tool_calls"][0]["function"]["name"] == "blobby_set_input"
        assert body["tool_calls"][0]["function"]["arguments"] == '{"workflow": "cat.json"}'
        # Tour d'outil pur : pas de texte => output null
        assert body["output"] is None
        assert body["usage"] == {"total_tokens": 7}
        assert body["max_context"] == 8192
        assert body["context_source"] == "manual"

    def test_tool_call_with_text_keeps_text(self, client, auth_headers):
        """content non-null + tool_calls => le texte est relayé aussi."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response(
            {"content": TEXT_OUTPUT, "tool_calls": [PROVIDER_TOOL_CALL]}
        )
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)):
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="ok", tools=TOOLS, tool_choice="auto")
        assert r.status_code == 200
        body = r.get_json()
        assert body["output"] == TEXT_OUTPUT
        assert body["tool_calls"] == [PROVIDER_TOOL_CALL]

    def test_pure_tool_call_turn_not_retried(self, client, auth_headers):
        """(c) un tour d'outil pur n'est PAS re-tenté : 1 requête, pas 3-4."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response(
            {"content": None, "tool_calls": [PROVIDER_TOOL_CALL]}
        )
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="ok", tools=TOOLS, tool_choice="auto")
        assert r.status_code == 200
        assert mpost.call_count == 1, (
            f"un tour tool_call doit être transmis sans retry (1 requête, "
            f"pas {mpost.call_count})"
        )


# ── (b) content null SANS tool_calls : réponse vide propre ────────────


class TestContentNullWithoutToolCalls:
    def test_content_null_no_crash_clean_empty(self, client, auth_headers):
        """content null sans tool_calls == comportement historique d'un output vide."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": None})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost, \
             mock.patch("time.sleep"):  # backoff 1s+2s+4s => no-op
            r = _post_llm_process(client, auth_headers, pid, instruction="ok")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        # Réponse vide PROPRE (== comportement content='' d'avant), pas de crash
        assert body["output"] == ""
        assert "tool_calls" not in body
        assert body["max_context"] == 8192
        # Le retry historique sur output vide est préservé : 1 + 3 requêtes
        assert mpost.call_count == 4


# ── (d) tools / tool_choice transmis tels quels au provider ───────────


class TestToolsPassthrough:
    def test_tools_and_tool_choice_forwarded_verbatim(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        tools = [dict(TOOLS[0], extra_marker="verbatim")]  # structure arbitraire
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="ok", tools=tools, tool_choice="auto")
        assert r.status_code == 200
        sent = mpost.call_args.kwargs["json"]
        # Transmis TELS QUELS (aucune transformation, aucun filtrage)
        assert sent["tools"] == tools
        assert sent["tool_choice"] == "auto"
        # Le reste du contrat est intact
        assert sent["model"] == "deepseek-chat"
        assert sent["temperature"] == 0.3
        assert sent["messages"][0]["role"] == "system"

    def test_no_tools_no_tool_choice_injected(self, client, auth_headers):
        """(f) sans tools : AUCUNE clé tools/tool_choice dans le body provider."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid, instruction="ok")
        assert r.status_code == 200
        sent = mpost.call_args.kwargs["json"]
        assert "tools" not in sent
        assert "tool_choice" not in sent

    def test_empty_tools_not_injected(self, client, auth_headers):
        """tools=[] => rien d'injecté (les providers refusent un tableau vide)."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid, instruction="ok", tools=[])
        assert r.status_code == 200
        assert "tools" not in mpost.call_args.kwargs["json"]

    def test_tool_choice_ignored_without_tools(self, client, auth_headers):
        """tool_choice orphelin (sans tools) => ignoré, pas envoyé au provider."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="ok", tool_choice="auto")
        assert r.status_code == 200
        assert "tool_choice" not in mpost.call_args.kwargs["json"]


# ── (e) messages fournie remplace la construction classique ───────────


class TestMessagesPassthrough:
    def _full_tool_loop_messages(self):
        return [
            {"role": "system", "content": "system custom du pack JS"},
            {"role": "user", "content": "charge le workflow chat"},
            {"role": "assistant", "content": None, "tool_calls": [PROVIDER_TOOL_CALL]},
            {"role": "tool", "tool_call_id": "call_abc123",
             "content": '{"status": "ok"}'},
        ]

    def test_messages_replace_classic_construction(self, client, auth_headers):
        """La liste fournie est utilisée verbatim, y compris un message role:'tool'."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        msgs = self._full_tool_loop_messages()
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid,
                                  messages=msgs, tools=TOOLS, tool_choice="auto")
        # instruction absente mais messages fournie => pas de 400
        assert r.status_code == 200, r.get_data(as_text=True)
        sent = mpost.call_args.kwargs["json"]
        assert sent["messages"] == msgs  # ordre + contenu, y compris role:'tool'

    def test_messages_without_system_get_default_prefix(self, client, auth_headers):
        """messages ne commençant pas par 'system' => system par défaut préfixé."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        msgs = [{"role": "user", "content": "reprise de boucle"},
                {"role": "tool", "tool_call_id": "call_x", "content": "{}"}]
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid, messages=msgs)
        assert r.status_code == 200
        sent = mpost.call_args.kwargs["json"]
        assert sent["messages"][0] == {"role": "system", "content": DEFAULT_SYSTEM_MSG}
        assert sent["messages"][1:] == msgs

    def test_messages_starting_with_system_not_duplicated(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        msgs = [{"role": "system", "content": "deja la"},
                {"role": "user", "content": "salut"}]
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid, messages=msgs)
        assert r.status_code == 200
        assert mpost.call_args.kwargs["json"]["messages"] == msgs  # aucun doublon

    def test_empty_messages_falls_back_to_instruction(self, client, auth_headers):
        """messages=[] == non fournie : la construction classique s'applique."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid, instruction="genere")
        assert r.status_code == 200
        sent = mpost.call_args.kwargs["json"]
        assert sent["messages"] == [
            {"role": "system", "content": DEFAULT_SYSTEM_MSG},
            {"role": "user", "content": "genere"},
        ]


# ── (a') Boucle tool-calling bout-en-bout : echo conforme au provider ─


class TestToolCallEchoRoundTrip:
    """Reproduit la regression DeepSeek "messages[i]: missing field type" :
    le tour 1 relaie les tool_calls du provider, le tour 2 les re-injecte
    (message assistant) et le body envoye au provider doit conserver
    `type:'function'` + le wrapper `function` (forme API)."""

    def test_second_turn_assistant_echo_keeps_provider_shape(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)

        # ── Tour 1 : le provider demande un outil ──
        turn1_resp = _provider_response({"content": None, "tool_calls": [PROVIDER_TOOL_CALL]})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=turn1_resp)):
            r1 = _post_llm_process(client, auth_headers, pid,
                                   instruction="charge le workflow",
                                   tools=TOOLS, tool_choice="auto")
        assert r1.status_code == 200, r1.get_data(as_text=True)
        relayed = r1.get_json()["tool_calls"]
        assert relayed[0]["type"] == "function"
        assert "function" in relayed[0]

        # ── Tour 2 : le client echo le tour assistant + les resultats d'outil ──
        messages = [
            {"role": "system", "content": DEFAULT_SYSTEM_MSG},
            {"role": "user", "content": "charge le workflow"},
            {"role": "assistant", "content": None, "tool_calls": relayed},
            {"role": "tool", "tool_call_id": relayed[0]["id"], "content": '{"ok": true}'},
        ]
        with mock.patch("requests.post", return_value=_FakeResp(json_data=_provider_response({"content": TEXT_OUTPUT}))) as mpost:
            r2 = _post_llm_process(client, auth_headers, pid,
                                   messages=messages, tools=TOOLS, tool_choice="auto")
        assert r2.status_code == 200, r2.get_data(as_text=True)
        sent_assistant = mpost.call_args.kwargs["json"]["messages"][2]
        assert sent_assistant["role"] == "assistant"
        # L'echo doit etre accepte par DeepSeek : `type` + `function` presents.
        assert sent_assistant["tool_calls"][0]["type"] == "function"
        assert sent_assistant["tool_calls"][0]["function"]["name"] == "blobby_set_input"
        assert sent_assistant["tool_calls"][0]["function"]["arguments"] == '{"workflow": "cat.json"}'


# ── Validation des entrées ────────────────────────────────────────────


class TestInputValidation:
    def test_tools_not_a_list_rejected(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        r = _post_llm_process(client, auth_headers, pid,
                              instruction="ok", tools={"type": "function"})
        assert r.status_code == 400

    def test_messages_not_a_list_rejected(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        r = _post_llm_process(client, auth_headers, pid, messages="oops")
        assert r.status_code == 400

    def test_messages_of_non_dicts_rejected(self, client, auth_headers):
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        r = _post_llm_process(client, auth_headers, pid, messages=["hello"])
        assert r.status_code == 400

    def test_instruction_still_required_without_messages(self, client, auth_headers):
        """Non-régression : sans messages, l'instruction reste requise (400)."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        r = _post_llm_process(client, auth_headers, pid)
        assert r.status_code == 400
        assert r.get_json()["error"] == "instruction requise"


# ── (f) Non-régression : appel sans tools/messages identique à avant ──


class TestBackwardCompatibility:
    def test_request_and_response_unchanged_without_tools(self, client, auth_headers):
        """La requête provider et la réponse HTTP sont EXACTEMENT celles d'avant."""
        _ensure_user()
        pid = _create_preset(client, auth_headers)
        resp_json = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=resp_json)) as mpost:
            r = _post_llm_process(client, auth_headers, pid,
                                  instruction="generate keywords")
        assert r.status_code == 200
        # Réponse HTTP : mêmes champs qu'avant, aucun champ tool_calls
        assert r.get_json() == {
            "output": TEXT_OUTPUT,
            "usage": {"total_tokens": 7},
            "max_context": 8192,
            "context_source": "manual",
        }
        # Requête provider : construction classique system+user, rien d'ajouté
        sent = mpost.call_args.kwargs["json"]
        assert sent == {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": DEFAULT_SYSTEM_MSG},
                {"role": "user", "content": "generate keywords"},
            ],
            "temperature": 0.3,
        }
        assert mpost.call_count == 1  # output > 50 => pas de retry


# ── Unitaires _call_llm_internal / _relay_tool_calls ──────────────


class TestCallLlmInternal:
    """Le helper doit rester compatible avec enhance/music3 (réponse texte)."""

    LLM_REQ = {"model": "deepseek-chat", "messages": [], "temperature": 0.3}
    LLM_CONF = {"base_url": "https://api.example.com", "api_key": "sk-x"}

    def test_text_response_returned_verbatim_no_added_key(self):
        """Réponse texte : dict provider retourné à l'identique (aucune clé ajoutée)."""
        provider = _provider_response({"content": TEXT_OUTPUT})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=provider)) as mpost:
            result = _call_llm_internal(self.LLM_REQ, self.LLM_CONF)
        assert result == provider
        assert "tool_calls" not in result
        assert mpost.call_count == 1

    def test_tool_calls_response_normalized_and_not_retried(self):
        """Tour tool_call : clé normalisée attachée + 1 seule requête provider."""
        provider = _provider_response({"content": None, "tool_calls": [PROVIDER_TOOL_CALL]})
        with mock.patch("requests.post", return_value=_FakeResp(json_data=provider)) as mpost:
            result = _call_llm_internal(self.LLM_REQ, self.LLM_CONF)
        assert result["tool_calls"] == [PROVIDER_TOOL_CALL]
        assert result["choices"][0]["message"]["tool_calls"] == [PROVIDER_TOOL_CALL]
        assert mpost.call_count == 1  # pas de retry sur un tour d'outil

    def test_tool_calls_after_empty_retry_not_retried_again(self):
        """Retry d'un output vide qui tombe sur un tool_call => arrêt immédiat."""
        first = _provider_response({"content": None})  # vide => déclenche le retry
        second = _provider_response({"content": None, "tool_calls": [PROVIDER_TOOL_CALL]})
        with mock.patch("requests.post", side_effect=[_FakeResp(json_data=first),
                                                      _FakeResp(json_data=second)]) as mpost, \
             mock.patch("time.sleep"):
            result = _call_llm_internal(self.LLM_REQ, self.LLM_CONF)
        assert result["tool_calls"] == [PROVIDER_TOOL_CALL]
        assert mpost.call_count == 2  # initial + 1 retry, pas 4


class TestRelayToolCalls:
    """Le relais conserve la forme provider (type + function) pour l'echo."""

    def test_none_for_text_message(self):
        assert _relay_tool_calls({"content": "salut"}) is None
        assert _relay_tool_calls({"content": "salut", "tool_calls": []}) is None
        assert _relay_tool_calls({"content": None}) is None

    def test_relay_is_verbatim_provider_form(self):
        out = _relay_tool_calls({"tool_calls": [PROVIDER_TOOL_CALL]})
        assert out == [PROVIDER_TOOL_CALL]
        assert out[0]["type"] == "function"
        assert out[0]["function"]["name"] == "blobby_set_input"
        # Copie defensive : le body provider d'origine n'est pas partage.
        assert out[0] is not PROVIDER_TOOL_CALL
        assert out[0]["function"] is not PROVIDER_TOOL_CALL["function"]

    def test_type_defaulted_to_function_when_missing(self):
        """Un tool_call sans `type` est complete a 'function' (seul type supporte)."""
        out = _relay_tool_calls(
            {"tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]}
        )
        assert out == [{"id": "c1", "type": "function",
                        "function": {"name": "f", "arguments": "{}"}}]

    def test_non_string_arguments_preserved_verbatim(self):
        """Le backend ne reecrit PAS arguments : le provider garde sa valeur brute."""
        tc = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": 42}}
        out = _relay_tool_calls({"tool_calls": [tc]})
        assert out == [tc]

    def test_entry_without_function_skipped(self):
        """Une entree sans wrapper function est inexploitable -> ignoree."""
        out = _relay_tool_calls({"tool_calls": [{"id": "c", "type": "function"}]})
        assert out is None

    def test_non_dict_entries_skipped(self):
        out = _relay_tool_calls({"tool_calls": ["junk", PROVIDER_TOOL_CALL]})
        assert out == [PROVIDER_TOOL_CALL]

    def test_non_dict_message_safe(self):
        assert _relay_tool_calls("not-a-dict") is None
        assert _relay_tool_calls(None) is None
