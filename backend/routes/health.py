"""Santé + version du backend AI-Helper — marqueur anti-obsolescence.

But : permettre de vérifier en 5 SECONDES qu'un backend DÉPLOYÉ exécute bien
la version qui STREAME les téléchargements (``storage.open_stream`` +
``_iter_stream`` dans ``routes/files.py``). Le correctif de streaming est un
changement PYTHON de ce service — distinct de ComfyUI : redémarrer ComfyUI ne
suffit pas, il faut redémarrer/déployer le backend. Sans marqueur, l'utilisateur
ne peut pas distinguer « backend ancien qui précharge 13,5 Go » d'un vrai
blocage.

Contrat public (aucune authentification : c'est un marqueur de diagnostic, sans
donnée sensible) :

  GET /api/health → 200
  {
    "ok": true,
    "service": "ai-helper-backend",
    "build": "backend-2026-10-01-r1",
    "git": "d197929",                      # si disponible
    "features": {
      "download_streaming": true,          # ← LA clé à vérifier
      "download_stream_chunk_size": 1048576,
      "download_stream_idle_timeout_s": 45
    }
  }

Un backend ANTÉRIEUR au streaming ne sert pas cette route (404) : le pack
Model Browser le détecte AVANT de lancer un transfert et refuse avec un message
actionnable, au lieu de laisser « Préparation côté serveur… » durer des minutes.
"""

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from extensions import BASE_DIR, app
from flask import jsonify

# Marqueur de build du backend : à incrémenter quand les capacités exposées
# changent (même esprit que les marqueurs AIH_MB_BUILD / AIH_DLW_BUILD du front).
AIH_BACKEND_BUILD = "backend-2026-10-01-r1"

# Export explicite : app.py fait ``from routes.health import *`` — sans __all__,
# les imports internes (os, logging, app…) pollueraient son namespace.
__all__ = ["AIH_BACKEND_BUILD", "api_health"]


@lru_cache(maxsize=1)
def _git_revision():
    """Révision git courte du backend (diagnostic), jamais bloquante.

    ``AIH_BACKEND_GIT_REV`` permet de l'épingler dans un déploiement sans
    dépôt git ; sinon on tente ``git rev-parse --short HEAD`` une seule fois.
    """
    override = os.environ.get("AIH_BACKEND_GIT_REV")
    if override:
        return override.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(BASE_DIR)),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # pragma: no cover — git absent/erreur : jamais bloquant
        pass
    return None


@app.route('/api/health', methods=['GET'])
def api_health():
    """Marqueur de version + capacités du backend (dont le streaming download)."""
    # Import paresseux : évite toute dépendance d'ordre d'import entre modules
    # de routes (app.py importe files puis health, mais le routeur doit rester
    # importable seul dans les tests).
    from routes.files import STREAM_CHUNK_SIZE, STREAM_IDLE_TIMEOUT

    payload = {
        'ok': True,
        'service': 'ai-helper-backend',
        'build': AIH_BACKEND_BUILD,
        'features': {
            'download_streaming': True,
            'download_stream_chunk_size': STREAM_CHUNK_SIZE,
            'download_stream_idle_timeout_s': STREAM_IDLE_TIMEOUT,
        },
    }
    revision = _git_revision()
    if revision:
        payload['git'] = revision
    logging.debug("[health] %s (%s) streaming=%s",
                  AIH_BACKEND_BUILD, revision or "rev inconnue", True)
    return jsonify(payload)
