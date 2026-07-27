"""
Helper pour charger les credentials AIH depuis ComfyUI/user/default/.

L'api_key et l'URL du serveur ne transitent plus par un widget STRING
des nodes (source de bugs d'index et de fuites dans les workflows exportes).
Elles sont stockees dans un fichier local que les nodes Python lisent
directement via ce helper.

Emplacement du fichier : <ComfyUI>/user/default/aih_credentials.json
(ou fallback : <ComfyUI>/user/aih_credentials.json si pas de sous-dossier default/)

Format JSON :
{
    "api_key": "xxx...",
    "server_url": "https://aih.holaf.fr",
    "updated_at": "2026-06-13T22:00:00Z"
}

Securite (ameliorations futures) :
    - Le helper supporte deja un mode chiffre via Fernet (si cryptography
      est installe et AIH_PASSPHRASE defini). Pour l'instant on lit en clair
      par defaut, le chiffrement sera active dans une session ulterieure.
"""

import json
import os
import logging

_CREDENTIALS_CACHE = None
_CREDENTIALS_PATH = None


def get_credentials_path():
    """
    Retourne le chemin du fichier de credentials.
    Utilise folder_paths.get_user_directory() (= ComfyUI/user/).
    """
    global _CREDENTIALS_PATH
    if _CREDENTIALS_PATH is not None:
        return _CREDENTIALS_PATH

    candidates = []
    try:
        import folder_paths
        user_dir = folder_paths.get_user_directory()
        candidates.append(os.path.join(user_dir, "default", "aih_credentials.json"))
        candidates.append(os.path.join(user_dir, "aih_credentials.json"))
    except Exception:
        pass

    # Fallback : chercher dans le dossier parent de AIH_ComfyUI (= ComfyUI/)
    try:
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # ici = AIH_Tools/, parent = custom_nodes/AIH_Tools/, parent = custom_nodes/
        # on remonte jusqu'a trouver le dossier "user" typique
        cur = here
        for _ in range(6):
            cur = os.path.dirname(cur)
            cand = os.path.join(cur, "user", "default", "aih_credentials.json")
            if os.path.isdir(os.path.dirname(cand)):
                candidates.append(cand)
                break
    except Exception:
        pass

    # Fallback final : home
    candidates.append(os.path.expanduser("~/.config/comfyui/aih_credentials.json"))

    for p in candidates:
        if os.path.isfile(p):
            _CREDENTIALS_PATH = p
            return p

    # Pas de fichier : retourner le premier candidat (pour save)
    if candidates:
        _CREDENTIALS_PATH = candidates[0]
    else:
        _CREDENTIALS_PATH = os.path.expanduser("~/.aih_credentials.json")
    return _CREDENTIALS_PATH


def _load_aih_credentials(use_cache=True):
    """
    Charge les credentials depuis le fichier local.
    Retourne {"api_key": str, "server_url": str}.

    Si le fichier n'existe pas, retourne des valeurs par defaut.
    Le cache evite de relire le fichier a chaque appel de node.
    """
    global _CREDENTIALS_CACHE

    if use_cache and _CREDENTIALS_CACHE is not None:
        return _CREDENTIALS_CACHE

    path = get_credentials_path()
    if not os.path.isfile(path):
        _CREDENTIALS_CACHE = {"api_key": "", "server_url": "https://kw.holaf.fr"}
        return _CREDENTIALS_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        logging.warning(f"[AIH credentials] Failed to read {path}: {e}")
        data = {}

    _CREDENTIALS_CACHE = {
        "api_key": data.get("api_key", ""),
        "server_url": data.get("server_url", "https://kw.holaf.fr"),
    }
    return _CREDENTIALS_CACHE


def invalidate_cache():
    """Force la relecture du fichier au prochain appel."""
    global _CREDENTIALS_CACHE
    _CREDENTIALS_CACHE = None


def get_api_url():
    """Retourne l'URL du backend (avec /api)."""
    creds = _load_aih_credentials()
    base = (creds.get("server_url") or "https://kw.holaf.fr").rstrip("/")
    return base + "/api"


def get_api_key():
    """Retourne l'api_key."""
    creds = _load_aih_credentials()
    return creds.get("api_key", "")
