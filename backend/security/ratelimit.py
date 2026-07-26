"""Simple sliding-window rate limiter (in-memory token bucket)."""

import time
from collections import defaultdict

from flask import request, jsonify

from security.auth import _get_current_user_id


_rate_buckets = defaultdict(list)


def _rate_limit(key, max_calls, window_seconds):
    """Limiteur de débit à fenêtre glissante (sliding-window).

    Vérifie si le nombre d'appels pour la clé donnée dépasse la limite
    dans la fenêtre temporelle spécifiée.

    Args:
        key (str): Clé d'identification du bucket (ex: ``"endpoint:user_id"``).
        max_calls (int): Nombre maximum d'appels autorisés dans la fenêtre.
        window_seconds (float): Durée de la fenêtre en secondes.

    Returns:
        bool: ``True`` si l'appel est autorisé, ``False`` si le débit est dépassé.
    """
    now = time.time()
    bucket = _rate_buckets[key]
    # Remove expired entries
    while bucket and bucket[0] < now - window_seconds:
        bucket.pop(0)
    if len(bucket) >= max_calls:
        return False
    bucket.append(now)
    return True


def _check_rate_limit(endpoint, max_calls=30, window_seconds=60):
    """Vérifie la limite de débit pour un endpoint et renvoie une 429 si dépassée.

    Utilise l'ID utilisateur courant (ou l'IP) comme clé de limitation.

    Args:
        endpoint (str): Nom de l'endpoint à limiter.
        max_calls (int, optional): Nombre max d'appels par fenêtre. Défaut: 30.
        window_seconds (float, optional): Fenêtre temporelle en secondes. Défaut: 60.

    Returns:
        tuple | None: Un tuple ``(Response, int)`` 429 si limité, sinon ``None``.
    """
    user_id = _get_current_user_id() or request.remote_addr
    if not _rate_limit(f"{endpoint}:{user_id}", max_calls, window_seconds):
        return jsonify({
            "error": "Trop de requêtes. Réessayez dans quelques minutes."
        }), 429
    return None


def _require_json():
    """Vérifie que le Content-Type de la requête est ``application/json``.

    Returns:
        tuple | None: Un tuple ``(Response, int)`` 415 si le type est incorrect,
            sinon ``None``.
    """
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 415
    return None