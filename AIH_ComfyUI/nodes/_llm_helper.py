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


def call_llm(config, system_prompt, user_prompt, seed=None):
    """
    Appelle un LLM selon le type de config.
    
    Args:
        config: dict avec "type" = "lmstudio" ou "openai", ou None (fallback)
        system_prompt: str
        user_prompt: str
        seed: int ou None
    
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
        return _call_lmstudio(config, system_prompt, user_prompt, seed)
    elif llm_type == "openai":
        return _call_openai(config, system_prompt, user_prompt, seed)
    else:
        logging.warning(f"[AIH LLM] Unknown config type: {llm_type}")
        return None


def _call_lmstudio(config, system_prompt, user_prompt, seed=None):
    """Appelle LM Studio via le SDK Python."""
    if lms is None:
        raise Exception("LM Studio SDK (lmstudio) is not installed. Run: pip install lmstudio")
    
    try:
        with lms.Client() as client:
            pass
    except Exception as e:
        raise Exception(f"Cannot connect to LM Studio: {e}")
    
    model_key = config.get("model", "").strip() or None
    max_tokens = int(config.get("max_tokens", 1000))
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
            
            result = model.respond(chat, config={
                "temperature": temperature,
                "maxTokens": max_tokens,
                "seed": int(seed),
            })
            
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


def _call_openai(config, system_prompt, user_prompt, seed=None):
    """Appelle une API compatible OpenAI via HTTP."""
    if requests is None:
        raise Exception("requests is not installed")
    
    base_url = config.get("base_url", "").strip().rstrip("/")
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "").strip()
    max_tokens = int(config.get("max_tokens", 1000))
    temperature = float(config.get("temperature", 0.7))
    
    if not base_url:
        raise Exception("base_url is required for OpenAI config")
    if not model:
        raise Exception("model is required for OpenAI config")
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    
    # Pas de repeat_penalty (problème avec DeepSeek etc)
    
    url = f"{base_url}/chat/completions"
    resp = requests.post(url, headers=headers, json=payload, timeout=(10, 300))
    
    if not resp.ok:
        body = resp.text
        raise Exception(f"HTTP {resp.status_code}: {body}")
    
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")