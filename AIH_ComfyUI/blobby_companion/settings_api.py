"""
Blobby Companion — Settings API for ComfyUI.

Ajoute deux endpoints au serveur ComfyUI pour stocker/lire
les parametres de Blobby dans le dossier utilisateur.

⚠️  L'import du module 'server' est fait DANS les handlers (pas au niveau module)
    pour eviter les ralentissements au demarrage de ComfyUI.

Stockage : ComfyUI/user/default/aih_blobby.json
"""

import os
import json
import logging

try:
    import folder_paths
    USER_DIR = folder_paths.get_user_directory()
except Exception:
    USER_DIR = os.path.expanduser("~")

BLOBBY_FILE = os.path.join(USER_DIR, "default", "aih_blobby.json")

def _ensure_dir():
    d = os.path.dirname(BLOBBY_FILE)
    if not os.path.isdir(d):
        try: os.makedirs(d, exist_ok=True)
        except Exception as e: logging.warning(f"[Blobby] Cannot create dir {d}: {e}")

def _load_all():
    if not os.path.isfile(BLOBBY_FILE): return {}
    try:
        with open(BLOBBY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        logging.warning(f"[Blobby] Failed to read {BLOBBY_FILE}: {e}")
        return {}

def _save_all(data):
    _ensure_dir()
    try:
        with open(BLOBBY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"[Blobby] Failed to write {BLOBBY_FILE}: {e}")


# ── Routes ──
# L'import du module 'server' est fait en INTERNE pour ne pas ralentir ComfyUI
# au demarrage (import circulaire possible sinon).

try:
    from aiohttp import web

    def _register_routes():
        import server
        srv = server.PromptServer.instance

        @srv.routes.post("/aih/blobby/save")
        async def blobby_save(request):
            try:
                body = await request.json()
                key = body.get("key")
                data = body.get("data")
                if not key: return web.json_response({"error": "key required"}, status=400)
                all_data = _load_all()
                all_data[key] = data
                _save_all(all_data)
                return web.json_response({"status": "ok"})
            except Exception as e:
                logging.error(f"[Blobby] save error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        @srv.routes.get("/aih/blobby/load")
        async def blobby_load(request):
            try:
                key = request.query.get("key")
                if not key: return web.json_response({"error": "key required"}, status=400)
                all_data = _load_all()
                return web.json_response({"data": all_data.get(key, None)})
            except Exception as e:
                logging.error(f"[Blobby] load error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        logging.info("[Blobby] Settings API ready (/aih/blobby/save, /aih/blobby/load)")

    # Enregistrer les routes au premier appel, pas a l'import
    _registered = False
    def ensure_routes():
        global _registered
        if not _registered:
            _register_routes()
            _registered = True

    ensure_routes()

except Exception as e:
    logging.warning(f"[Blobby] Failed to register routes: {e}")
