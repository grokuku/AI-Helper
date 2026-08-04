"""
LLM Helper — Appels LLM unifiés pour les nodes AIH.
Supporte LM Studio (SDK local) et OpenAI-compatible (HTTP).
"""
import json
import logging
import random
import re
import threading
import concurrent.futures

try:
    import lmstudio as lms
except Exception:
    lms = None

try:
    import requests
except Exception:
    requests = None


def call_llm(config, system_prompt, user_prompt, seed=None, image_base64=None):
    """
    Appelle un LLM selon le type de config.
    
    Args:
        config: dict avec "type" = "lmstudio" ou "openai", ou None (fallback)
        system_prompt: str
        user_prompt: str
        seed: int ou None
        image_base64: str ou None — image encodée en base64 JPEG (multimodal)
    
    Returns:
        str (le texte généré) ou None si pas de config (fallback backend)
    """
    if config is None:
        return None  # → l'appelant utilise le backend
    
    if not isinstance(config, dict):
        # Si c'est un string JSON, le parser
        try:
            config = json.loads(config) if isinstance(config, str) else config
        except (json.JSONDecodeError, TypeError):
            return None
    
    llm_type = config.get("type", "")
    
    if llm_type == "lmstudio":
        return _call_lmstudio(config, system_prompt, user_prompt, seed, image_base64)
    elif llm_type == "openai":
        return _call_openai(config, system_prompt, user_prompt, seed, image_base64)
    else:
        logging.warning(f"[AIH LLM] Unknown config type: {llm_type}")
        return None


def _call_lmstudio(config, system_prompt, user_prompt, seed=None, image_base64=None):
    """Appelle LM Studio via le SDK Python, ou via HTTP pour le multimodal."""
    # ── Multimodal : utiliser l'API HTTP OpenAI-compatible de LM Studio ──
    # Le SDK lmstudio ne supporte pas les messages multipart avec images.
    # LM Studio expose un endpoint OpenAI-compatible sur localhost:1234/v1.
    if image_base64:
        base_url = config.get("base_url", "").strip().rstrip("/")
        if not base_url:
            base_url = "http://localhost:1234/v1"
        http_config = {
            "base_url": base_url,
            "api_key": config.get("api_key", ""),
            "model": config.get("model", ""),
            # max_tokens passé tel quel à _call_openai qui l'omettra s'il est <= 0.
            "max_tokens": config.get("max_tokens", 0),
            "temperature": config.get("temperature", 0.7),
            # num_ctx (fenêtre de contexte) — passé à _call_openai qui ne
            # l'enverra QUE si le base_url pointe vers Ollama (spécifique Ollama).
            "num_ctx": config.get("num_ctx", 0),
        }
        return _call_openai(http_config, system_prompt, user_prompt, seed, image_base64)

    # ── Mode texte seul : utiliser le SDK lmstudio comme avant ──
    if lms is None:
        raise Exception("LM Studio SDK (lmstudio) is not installed. Run: pip install lmstudio")
    
    try:
        with lms.Client() as client:
            pass
    except Exception as e:
        raise Exception(f"Cannot connect to LM Studio: {e}")
    
    model_key = config.get("model", "").strip() or None
    max_tokens = int(config.get("max_tokens", 0) or 0)
    temperature = float(config.get("temperature", 0.7))
    auto_unload = config.get("auto_unload", True)
    unload_delay = int(config.get("unload_delay", 0))
    
    if seed is None or seed == -1:
        seed = random.randint(0, 0xFFFFFFFFFFFFFFFF)
    
    def _do_work():
        with lms.Client() as client:
            if model_key:
                if auto_unload and unload_delay > 0:
                    model = client.llm.model(model_key, ttl=unload_delay)
                else:
                    model = client.llm.model(model_key)
            else:
                model = client.llm.model()
            
            chat = lms.Chat(system_prompt)
            chat.add_user_message(user_prompt)
            
            respond_config = {
                "temperature": temperature,
                "seed": int(seed),
            }
            # maxTokens : limite de réponse explicite. 0/missing → omis pour que
            # le modèle utilise son propre défaut de sortie.
            if max_tokens > 0:
                respond_config["maxTokens"] = max_tokens
            result = model.respond(chat, config=respond_config)
            
            text = result.content or ""
            # Strip thinking tags
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
            
            if auto_unload and unload_delay == 0:
                try:
                    model.unload()
                except Exception as e:
                    logging.warning(f"[AIH LLM] Failed to unload model: {e}")
            
            return text
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_work)
        try:
            return future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            raise Exception("LM Studio operation timed out after 300 seconds")


def _build_user_content(user_prompt, image_base64):
    """Construit le contenu du message user : string simple ou multipart avec image.

    Si une image est fournie, ajoute une instruction contextuelle au text part
    (si elle n'est pas déjà présente par l'appelant).
    """
    if image_base64:
        instruction = "[An image is provided as visual reference. Incorporate relevant visual elements from the image into the enhanced prompt.]"
        if instruction not in user_prompt:
            user_prompt = user_prompt + "\n\n" + instruction
        return [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ]
    return user_prompt


def _is_vision_error(error_msg, has_image=False):
    """Détecte si une erreur LLM est liée à l'absence de support multimodal.

    Args:
        error_msg: le message d'erreur (string).
        has_image: si True, une image a été envoyée dans la requête.
                   Dans ce cas, des patterns génériques comme "500",
                   "bad request", "internal server error" sont aussi
                   considérés comme potentiellement liés au manque de
                   support multimodal.
    """
    msg_lower = error_msg.lower()
    # Patterns explicites — toujours pertinents
    indicators = [
        "image", "vision", "multimodal", "multimodal_capability",
        "content type", "unsupported content", "image_url",
        "does not support", "not support image", "not a vision model",
        "no vision", "can only process text",
    ]
    if any(ind in msg_lower for ind in indicators):
        return True
    # Patterns contextuels — uniquement si une image a été envoyée
    if has_image:
        generic_indicators = ["500", "bad request", "internal server error", "ollama"]
        if any(ind in msg_lower for ind in generic_indicators):
            return True
    return False


def _is_ollama_base_url(base_url):
    """Détecte si l'URL de base pointe vers Ollama (local ou cloud).

    num_ctx est un paramètre SPÉCIFIQUE à Ollama (taille de la fenêtre de
    contexte). On ne doit l'envoyer QUE si l'URL contient "ollama" ou le
    port 1143x d'Ollama (ex: http://localhost:11434/v1).
    """
    return "ollama" in (base_url or "").lower() or ":1143" in (base_url or "")


_model_context_cache = {}  # key: (base_url, model) -> (context_length, fetched_at)


def _get_model_context(base_url, api_key, model, cache_ttl=3600):
    """Interroge l'API pour récupérer la fenêtre de contexte du modèle.
    Retourne un int (tokens) ou 0 si indisponible. Cache par (base_url, model)."""
    import time as _time
    key = (base_url, model)
    now = _time.time()
    cached = _model_context_cache.get(key)
    if cached and now - cached[1] < cache_ttl:
        return cached[0]
    ctx = 0
    try:
        if requests is None:
            return 0
        base = (base_url or "").rstrip("/")
        is_ollama = "ollama" in base.lower() or ":1143" in base
        if is_ollama:
            native_base = base[:-3] if base.endswith("/v1") else base
            resp = requests.post(f"{native_base}/api/show",
                                 json={"model": model},
                                 headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                                 timeout=10)
            if resp.ok:
                info = resp.json().get("model_info", {})
                for k, v in info.items():
                    if "context_length" in k:
                        ctx = int(v)
                        break
        else:
            resp = requests.get(f"{base}/models",
                                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                                timeout=10)
            if resp.ok:
                data = resp.json()
                models = data.get("data", []) if isinstance(data, dict) else []
                for m in models:
                    if m.get("id") == model:
                        for k in ("context_length", "max_model_len", "context_window", "max_context_length"):
                            if m.get(k):
                                ctx = int(m[k])
                                break
                        break
    except Exception:
        ctx = 0
    if ctx > 0:
        _model_context_cache[key] = (ctx, now)
    return ctx


def _call_openai(config, system_prompt, user_prompt, seed=None, image_base64=None):
    """Appelle une API compatible OpenAI via HTTP."""
    if requests is None:
        raise Exception("requests is not installed")
    
    base_url = config.get("base_url", "").strip().rstrip("/")
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "").strip()
    max_tokens = int(config.get("max_tokens", 0) or 0)
    temperature = float(config.get("temperature", 0.7))
    requested_num_ctx = int(config.get("num_ctx", 0) or 0)
    
    if not base_url:
        raise Exception("base_url is required for OpenAI config")
    if not model:
        raise Exception("model is required for OpenAI config")
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    user_content = _build_user_content(user_prompt, image_base64)
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }
    # max_tokens : limite de réponse explicite. 0/missing → OMIS de la requête
    # pour que le modèle utilise son propre défaut (certaines APIs rejettent
    # max_tokens: 0).
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    
    # Pas de repeat_penalty (problème avec DeepSeek etc)
    
    # num_ctx (taille de la fenêtre de contexte) — paramètre SPÉCIFIQUE Ollama.
    # Ne PAS l'envoyer aux APIs OpenAI / DeepSeek / LM Studio (400 Bad Request).
    # max_tokens (limite de réponse) est envoyé ci-dessus uniquement si > 0 — les deux coexistent.
    #
    # Règle :
    #   - requested_num_ctx > 0 → override utilisateur explicite (widget num_ctx)
    #   - sinon (Ollama)        → auto-détection depuis /api/show avec marge de
    #                             sécurité (ctx - 512, min 4096)
    #   - sinon                 → pas de num_ctx du tout
    num_ctx = 0
    if requested_num_ctx > 0:
        num_ctx = requested_num_ctx
    elif _is_ollama_base_url(base_url):
        ctx = _get_model_context(base_url, api_key, model)
        if ctx > 0:
            num_ctx = max(ctx - 512, 4096)
    if num_ctx > 0 and _is_ollama_base_url(base_url):
        payload["num_ctx"] = num_ctx
    
    url = f"{base_url}/chat/completions"
    resp = requests.post(url, headers=headers, json=payload, timeout=(10, 300))
    
    if not resp.ok:
        body = resp.text
        # Détecter les erreurs liées au support multimodal
        if image_base64 and _is_vision_error(body, has_image=True):
            raise Exception(
                "Le modèle ne semble pas supporter les images (multimodal). "
                "Utilisez un modèle vision comme GPT-4o, Claude 3.5 Sonnet, LLaVA, etc.\n\n"
                f"Détail: {body[:500]}"
            )
        # Si une image a été envoyée et que l'erreur est 500/bad request/internal server error,
        # suggérer le problème multimodal même si le message n'est pas explicite
        if image_base64 and ("500" in str(resp.status_code) or "bad request" in body.lower() or "internal server error" in body.lower()):
            raise Exception(
                f"Erreur: Le modèle ne semble pas supporter les images (multimodal). "
                f"Utilisez un modèle vision comme GPT-4o, Claude 3.5 Sonnet, LLaVA, etc. "
                f"Détail: {str(body)[:200]}"
            )
        raise Exception(f"HTTP {resp.status_code}: {body}")
    
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")