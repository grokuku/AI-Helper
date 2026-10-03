"""Chat Blobby — streaming + watchdog d'INACTIVITÉ (pas de durée totale).

Contexte : les réponses du chat étaient coupées « en plein milieu » par un
plafond de DURÉE TOTALE (timeout de la brique HolafFetch = 30 s côté front,
read timeout de ``requests`` côté backend sur une réponse non streamée). Le
correctif remplace ce plafond par un watchdog d'INACTIVITÉ RÉARMÉ à chaque
morceau reçu du LLM (SSE).

Ce fichier verrouille, avec preuves, la sémantique :
  (a) réponse LENTE mais ACTIVE (durée totale >> seuil) → ABOUTIT ;
  (b) flux SILENCIEUX (aucun morceau > seuil) → coupé, message explicite ;
  (c) réarmement : activité régulière pendant longtemps → jamais coupé ;
  (d) l'appel sortant n'a AUCUN plafond de durée totale : timeout=(10, idle)
      et ``stream=True`` (le read est PAR LECTURE = inactivité) ;
  (e) la route NDJSON relaie start/delta/keepalive/done et les erreurs typées ;
  (f) non-régression : mêmes erreurs de validation que la route JSON.

Stratégie de mock : un faux ``requests.post`` dont ``iter_lines`` MODÉLISE un
read timeout par lecture (lève ``ReadTimeout`` quand un morceau met plus de
``idle`` à arriver). C'est exactement le comportement réel de requests sur un
flux SSE : jamais un plafond sur la durée totale.
"""

import socket
import time
from unittest import mock

import pytest
import requests
from routes.enhance import (
    _call_llm_stream_internal,
    _llm_idle_timeout,
    _LLMIdleTimeoutError,
)

# ── Fixtures / helpers (conventions test_llm_tool_calls.py) ───────────

_PUBLIC_HOSTS = {"api.example.com": "8.8.8.8"}


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
    payload = {
        "name": "IdleTimeoutTest",
        "base_url": base_url,
        "api_key": "sk-IDLE-42",
        "model": model,
        "context_length": 8192,  # manuel => aucune sonde réseau
    }
    payload.update(extra)
    with mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        r = client.post("/api/presets", json=payload, headers=headers)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


LLM_REQ = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}],
           "temperature": 0.3}
LLM_CONF = {"base_url": "https://api.example.com", "api_key": "sk-x"}


def _sse(*contents):
    """Construit des lignes SSE OpenAI-compatibles (delta.content)."""
    lines = []
    for c in contents:
        import json
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}))
    lines.append("data: [DONE]")
    return lines


class _FakeStreamResp:
    """Faux ``requests`` streamé : ``iter_lines`` modélise un read timeout PAR
    LECTURE (lève ReadTimeout si un morceau met plus de ``idle`` à arriver).
    La durée TOTALE peut donc dépasser ``idle`` sans provoquer d'erreur :
    c'est la preuve que le watchdog est un seuil d'inactivité, pas un plafond.
    """

    def __init__(self, lines, ctype="text/event-stream", status=200,
                 delay=0.0, idle=None, fail_after=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._lines = list(lines)
        self._delay = delay
        self._idle = idle
        self._fail_after = fail_after
        self.headers = {"Content-Type": ctype}
        self.text = ""
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for i, line in enumerate(self._lines):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.exceptions.ReadTimeout("read timed out")
            if self._delay:
                time.sleep(self._delay)
                # Modèle du read timeout requests : si le morceau a mis plus
                # que le seuil d'inactivité à arriver, la lecture échoue.
                if self._idle is not None and self._delay > self._idle:
                    raise requests.exceptions.ReadTimeout("read timed out")
            yield line if decode_unicode else line.encode("utf-8")

    def close(self):
        self.closed = True

    def json(self):
        return {"choices": [{"message": {"content": "json-fallback"}}]}


# ── (a)(c) LENT mais ACTIF : aboutit malgré une durée totale > seuil ──


class TestSlowButActive:

    def test_total_duration_exceeds_idle_but_each_chunk_is_active(self):
        """6 morceaux toutes les 0,05 s (total 0,30 s) avec idle=0,15 s → OK."""
        resp = _FakeStreamResp(_sse("Bon", "jour", " ", "le", " monde", "!"),
                               delay=0.05, idle=0.15)
        with mock.patch("requests.post", return_value=resp):
            result = _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=0.15)
        out = result["choices"][0]["message"]["content"]
        assert out == "Bonjour le monde!"
        assert resp.closed is True

    def test_rearm_regular_activity_for_a_long_time(self):
        """20 morceaux réguliers (total ~1,0 s ≫ idle 0,15 s) → jamais coupé."""
        resp = _FakeStreamResp(_sse(*["x"] * 20), delay=0.03, idle=0.15)
        with mock.patch("requests.post", return_value=resp) as mpost:
            result = _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=0.15)
        assert result["choices"][0]["message"]["content"] == "x" * 20
        # Un SEUL appel : aucun retry/abandon sur la durée totale.
        assert mpost.call_count == 1


# ── (b) SILENCIEUX : coupé avec un message explicite ──────────────────


class TestSilentStream:

    def test_silence_longer_than_idle_raises_explicit_error(self):
        resp = _FakeStreamResp(_sse("début..."), delay=0.30, idle=0.05)
        with mock.patch("requests.post", return_value=resp), pytest.raises(_LLMIdleTimeoutError) as ei:
            _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=0.05)
        assert ei.value.idle_timeout == 0.05
        assert "ne renvoie plus rien depuis" in str(ei.value)

    def test_idle_translated_even_when_requests_wraps_it(self):
        """Un ReadTimeout enveloppé (ConnectionError) est aussi traduit."""
        resp = _FakeStreamResp(_sse("a"), fail_after=1)
        with mock.patch("requests.post", return_value=resp), pytest.raises(_LLMIdleTimeoutError):
            _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=7)


# ── (d) Aucun plafond de durée totale sur l'appel sortant ─────────────


class TestNoTotalCap:

    def test_requests_post_uses_stream_and_idle_read_timeout(self):
        resp = _FakeStreamResp(_sse("ok"))
        with mock.patch("requests.post", return_value=resp) as mpost:
            _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=0.2)
        _, kwargs = mpost.call_args
        assert kwargs.get("stream") is True, "le corps doit être lu en flux (SSE)"
        assert kwargs.get("timeout") == (10, 0.2), (
            "timeout=(connect, idle) : le read est PAR LECTURE (inactivité), "
            "jamais une durée totale"
        )

    def test_default_idle_timeout_env_configurable(self, monkeypatch):
        monkeypatch.setenv("AIH_LLM_IDLE_TIMEOUT", "77")
        assert _llm_idle_timeout() == 77
        monkeypatch.setenv("AIH_LLM_IDLE_TIMEOUT", "0")
        assert _llm_idle_timeout() == 1  # borné à >0, jamais 0 (aucun watchdog)
        monkeypatch.setenv("AIH_LLM_IDLE_TIMEOUT", "n/a")
        assert _llm_idle_timeout() == 120  # repli sûr


# ── (d bis) tool_calls streamés : accumulation correcte ───────────────


def test_tool_calls_streamed_are_accumulated_provider_shaped():
    import json
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "blobby_set_input", "arguments": '{"workflow":'}}]}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ' "cat.json"}'}}]}}]}),
        "data: [DONE]",
    ]
    resp = _FakeStreamResp(lines)
    with mock.patch("requests.post", return_value=resp):
        result = _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=5)
    assert result["tool_calls"] == [{
        "id": "call_1", "type": "function",
        "function": {"name": "blobby_set_input", "arguments": '{"workflow": "cat.json"}'},
    }]


def test_non_sse_content_type_falls_back_to_non_streaming():
    resp = _FakeStreamResp(_sse("x"), ctype="application/json")
    with mock.patch("requests.post", return_value=resp), \
         mock.patch("routes.enhance._call_llm_internal",
                    return_value={"choices": [{"message": {"content": "fallback"}}]}) as mfallback:
        result = _call_llm_stream_internal(LLM_REQ, LLM_CONF, idle_timeout=5)
    assert result["choices"][0]["message"]["content"] == "fallback"
    assert mfallback.call_count == 1


# ── (e) Route NDJSON /api/keywords/llm-process/stream ─────────────────


def _lines(response):
    body = response.get_data(as_text=True)
    import json
    return [json.loads(ln) for ln in body.split("\n") if ln.strip()]


class TestStreamRoute:

    @pytest.fixture(autouse=True)
    def _setup(self, app):
        _ensure_user()

    def test_validation_identical_to_json_route(self, client, auth_headers):
        r = client.post("/api/keywords/llm-process/stream",
                        headers=auth_headers, json={})
        assert r.status_code == 400
        assert r.get_json()["error"] == "preset_id requis"

    def test_stream_delta_keepalive_done(self, client, auth_headers, monkeypatch):
        pid = _create_preset(client, auth_headers)

        def fake_stream(llm_request, llm_config, on_delta=None, idle_timeout=None):
            if on_delta:
                on_delta("Bon")
                on_delta("jour")
            time.sleep(0.7)  # force au moins un keepalive (env ci-dessous, plancher 0,5 s)
            return {"choices": [{"message": {"content": "Bonjour"}}],
                    "usage": {"total_tokens": 3}}

        monkeypatch.setenv("AIH_LLM_STREAM_KEEPALIVE", "0.5")
        monkeypatch.setenv("AIH_LLM_IDLE_TIMEOUT", "120")
        monkeypatch.setattr("routes.enhance._call_llm_stream_internal", fake_stream)

        r = client.post("/api/keywords/llm-process/stream", headers=auth_headers,
                        json={"preset_id": pid, "instruction": "salut"})
        assert r.status_code == 200
        assert r.mimetype == "application/x-ndjson"
        evts = _lines(r)
        assert evts[0]["status"] == "start"
        assert evts[0]["idle_timeout"] == 120
        assert any(e["status"] == "keepalive" for e in evts), "keepalive attendu"
        assert any(e["status"] == "delta" and e["text"] == "Bon" for e in evts)
        done = [e for e in evts if e["status"] == "done"][-1]
        assert done["output"] == "Bonjour"
        assert done["max_context"] == 8192  # manuel, aucun réseau
        assert done["context_source"] == "manual"

    def test_stream_idle_error_is_explicit_and_typed(self, client, auth_headers, monkeypatch):
        pid = _create_preset(client, auth_headers)

        def fake_stream(llm_request, llm_config, on_delta=None, idle_timeout=None):
            raise _LLMIdleTimeoutError(120)

        monkeypatch.setattr("routes.enhance._call_llm_stream_internal", fake_stream)
        r = client.post("/api/keywords/llm-process/stream", headers=auth_headers,
                        json={"preset_id": pid, "instruction": "salut"})
        err = [e for e in _lines(r) if e["status"] == "error"][-1]
        assert err["code"] == "llm_idle_timeout"
        assert "ne renvoie plus rien depuis 120 s" in err["error"]
