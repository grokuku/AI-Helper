"""
Module d'embeddings.

Deux providers supportés :
  - "ollama"  : local via Ollama API (defaut)
  - "gemini"  : Google AI Studio (API key) - gemini-embedding-001 ou gemini-embedding-2

Configurez dans le panneau Admin : provider + paramètres.
Fallback sur vars d'env si la BDD ne contient pas la clé.

OLLAMA_URL       (defaut: http://localhost:11434)
OLLAMA_MODEL     (defaut: nomic-embed-text)
EMBEDDING_PROVIDER  (defaut: ollama)
GEMINI_API_KEY   (optionnel, defaut: vide)
GEMINI_MODEL     (defaut: gemini-embedding-001)
"""

import os
import json
import urllib.request
import urllib.error


# === Configuration par defaut ===
DEFAULT_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama")

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "nomic-embed-text")

DEFAULT_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-embedding-001")

# Configuration surchargeable dynamiquement (depuis la BDD admin)
_OVERRIDE_PROVIDER = None
_OVERRIDE_OLLAMA_URL = None
_OVERRIDE_OLLAMA_MODEL = None
_OVERRIDE_GEMINI_API_KEY = None
_OVERRIDE_GEMINI_MODEL = None


def set_config(
    provider: str | None = None,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
):
    """Surcharge la config (appele depuis l'app avec les valeurs stockees en BDD)."""
    global _OVERRIDE_PROVIDER, _OVERRIDE_OLLAMA_URL, _OVERRIDE_OLLAMA_MODEL
    global _OVERRIDE_GEMINI_API_KEY, _OVERRIDE_GEMINI_MODEL
    if provider is not None:
        _OVERRIDE_PROVIDER = provider.strip().lower() or "ollama"
    if ollama_url:
        _OVERRIDE_OLLAMA_URL = ollama_url
    if ollama_model:
        _OVERRIDE_OLLAMA_MODEL = ollama_model
    # Accepter la string vide pour la cle API (permet de la supprimer)
    if gemini_api_key is not None:
        _OVERRIDE_GEMINI_API_KEY = gemini_api_key
    if gemini_model:
        _OVERRIDE_GEMINI_MODEL = gemini_model


def _get_provider() -> str:
    """Retourne le nom du provider d'embedding actif (ollama ou gemini).

    Returns:
        str: Le provider actif, priorisant la surcharge BDD sur la valeur par défaut.
    """
    return _OVERRIDE_PROVIDER or DEFAULT_PROVIDER


def _get_ollama_url() -> str:
    """Retourne l'URL du serveur Ollama configuré.

    Returns:
        str: L'URL de base d'Ollama, priorisant la surcharge BDD sur la valeur par défaut.
    """
    return _OVERRIDE_OLLAMA_URL or DEFAULT_OLLAMA_URL


def _get_ollama_model() -> str:
    """Retourne le nom du modèle Ollama utilisé pour les embeddings.

    Returns:
        str: Le nom du modèle Ollama, priorisant la surcharge BDD sur la valeur par défaut.
    """
    return _OVERRIDE_OLLAMA_MODEL or DEFAULT_OLLAMA_MODEL


def _get_gemini_api_key() -> str:
    """Retourne la clé API Gemini configurée.

    Returns:
        str: La clé API Gemini, priorisant la surcharge BDD sur la variable d'environnement.
    """
    return _OVERRIDE_GEMINI_API_KEY or DEFAULT_GEMINI_API_KEY


def _get_gemini_model() -> str:
    """Retourne le nom du modèle Gemini utilisé pour les embeddings.

    Returns:
        str: Le nom du modèle Gemini, priorisant la surcharge BDD sur la valeur par défaut.
    """
    return _OVERRIDE_GEMINI_MODEL or DEFAULT_GEMINI_MODEL


# === Generation d'embeddings ===

def _generate_ollama(text: str) -> list[float]:
    """Appelle l'API Ollama et retourne un vecteur d'embedding."""
    url = _get_ollama_url().rstrip("/") + "/api/embed"
    model = _get_ollama_model()

    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")[:300]
        raise RuntimeError(f"Erreur Ollama ({e.code}): {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Impossible de se connecter a Ollama ({url}): {e.reason}\n"
            "Verifie que le serveur Ollama est bien lance."
        )

    # Reponse : {"model":"...","embeddings":[[0.1, 0.2, ...]]}
    if isinstance(result, dict):
        embeddings = result.get("embeddings")
        if embeddings and len(embeddings) > 0 and isinstance(embeddings[0], list):
            return [float(x) for x in embeddings[0]]

    raise RuntimeError(f"Reponse Ollama inattendue: {json.dumps(result)[:200]}")


def _generate_gemini(text: str) -> list[float]:
    """Appelle l'API Google AI Studio (Gemini embeddings) et retourne un vecteur."""
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError(
            "Cle API Gemini manquante. Configure-la dans Admin > Embeddings "
            "ou via la variable d'environnement GEMINI_API_KEY."
        )

    model = _get_gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
    # gemini-embedding-001 supporte taskType=RETRIEVAL_QUERY|RETRIEVAL_DOCUMENT, etc.
    # gemini-embedding-2 n'accepte PAS taskType - les instructions vont dans le prompt.
    # Pour rester compatible avec les deux, on omet taskType (defaut = generic).
    # Note : pour de la recherche semantique de keywords, on pourrait passer
    # "taskType": "RETRIEVAL_DOCUMENT" pour les indexations et
    # "RETRIEVAL_QUERY" pour les requetes, mais ca oblige a differencier les
    # deux cas dans le code appelant. On reste sur le defaut pour simplifier.
    payload = json.dumps({
        "content": {"parts": [{"text": text}]},
        "output_dimensionality": 768,  # MRL: tronque a 768 (coherent avec Ollama)
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")[:500]
        # Messages d'erreur plus parlants pour les cas courants
        if e.code == 400 and "API key" in body:
            raise RuntimeError(f"Cle API Gemini invalide. Verifie-la sur https://aistudio.google.com/apikey")
        if e.code == 403:
            raise RuntimeError(f"Acces refuse par Gemini API: {body}")
        if e.code == 404:
            raise RuntimeError(f"Modele Gemini '{model}' introuvable. Verifie le nom (ex: gemini-embedding-001, gemini-embedding-2).")
        if e.code == 429:
            raise RuntimeError(f"Quota Gemini depasse. Verifie tes limites sur https://aistudio.google.com/")
        raise RuntimeError(f"Erreur Gemini API ({e.code}): {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Impossible de joindre Google AI Studio: {e.reason}\n"
            "Verifie ta connexion internet."
        )

    # Reponse : {"embedding": {"values": [0.1, 0.2, ...]}}
    if isinstance(result, dict):
        embedding = result.get("embedding")
        if isinstance(embedding, dict) and "values" in embedding:
            return [float(x) for x in embedding["values"]]
        # Variante : {"embeddings": [{"values": [...]}]}
        embeddings = result.get("embeddings")
        if isinstance(embeddings, list) and len(embeddings) > 0:
            first = embeddings[0]
            if isinstance(first, dict) and "values" in first:
                return [float(x) for x in first["values"]]
            if isinstance(first, list):
                return [float(x) for x in first]

    raise RuntimeError(f"Reponse Gemini inattendue: {json.dumps(result)[:300]}")


def generate_embedding(text: str) -> list[float]:
    """Genere un embedding en utilisant le provider actif."""
    provider = _get_provider()
    if provider == "gemini":
        return _generate_gemini(text)
    # defaut = ollama
    return _generate_ollama(text)


# === Maths ===

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity entre deux vecteurs."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# === Verification de disponibilite ===

def is_available() -> bool:
    """Verifie si le provider actif est joignable."""
    provider = _get_provider()
    if provider == "gemini":
        return is_gemini_available()
    return is_ollama_available()


def is_ollama_available() -> bool:
    """Verifie si Ollama est joignable ET a le modele configure."""
    try:
        url = _get_ollama_url().rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            model = _get_ollama_model()
            models = data.get("models", [])
            return any(m["name"].startswith(model) for m in models)
    except Exception:
        return False


def is_gemini_available() -> bool:
    """Verifie si la cle API Gemini est configuree (et fait un ping leger)."""
    api_key = _get_gemini_api_key()
    if not api_key:
        return False
    # Pas de ping cote serveur ici - on se contente de verifier la presence
    # de la cle. Un test reel (embedContent) sera fait au premier appel.
    # Ca evite de generer du trafic/quotas sur le endpoint admin.
    return True


# === Helpers admin ===

def get_active_config() -> dict:
    """Retourne la config active (utile pour l'admin UI)."""
    return {
        "provider": _get_provider(),
        "ollama_url": _get_ollama_url(),
        "ollama_model": _get_ollama_model(),
        "gemini_model": _get_gemini_model(),
        "gemini_api_key_set": bool(_get_gemini_api_key()),
    }
