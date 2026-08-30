"""CLI entry point to refresh the MiniMax Music3 reference cache.

Usage:
    python backend/music3_refresh.py

Imports the Flask app (registers all routes incl. routes.music3), then calls
sync_music3_cache(force=True) and prints the result. Intended to be run by a
daily cron to keep the ~1000 templates cache in sync with GitHub.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: F401  (registration des routes)
from routes.music3 import sync_music3_cache  # noqa: F401

if __name__ == '__main__':
    with app.app_context():
        info = sync_music3_cache(force=True)
        print(info)
