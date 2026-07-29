"""
#
# ⚠️  CECI EST LE SEUL __init__.py EXECUTE PAR COMFYUI (celui a la racine du dossier custom_nodes/).
# AIH_ComfyUI/__init__.py est un FICHIER MORT — ne pas y mettre de logique.
#
AIH — ComfyUI extension.
ComfyUI charge ce fichier quand le dossier est dans custom_nodes/.
On importe les nodes depuis le sous-dossier AIH_ComfyUI/.
"""
import importlib.util
import os
import sys
import logging

# Installer les requirements du pack au demarrage si besoin
try:
    _req = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'AIH_ComfyUI', 'requirements.txt')
    if os.path.isfile(_req):
        import subprocess as _sp
        _sp.run(['pip', 'install', '-r', _req], capture_output=True, text=True, timeout=60)
except Exception as e:
    logging.warning(f"[AIH] Failed to auto-install requirements: {e}")

# Acces au serveur HTTP de ComfyUI pour enregistrer des routes
try:
    import server
    _routes = server.PromptServer.instance.routes
except Exception:
    _routes = None

_base = os.path.dirname(os.path.abspath(__file__))

# Ajouter _base au sys.path pour permettre `from AIH_ComfyUI import X`
# (le repo est installe dans custom_nodes/<repo>/ donc _base est
# custom_nodes/AIH_Tools/ et AIH_ComfyUI/ est a cote).
if _base not in sys.path:
    sys.path.insert(0, _base)

# ── Helpers pour le stockage local dans user/default/aih/ ──────────

def _migrate_to_aih_subfolder(old_path, new_path):
    """Déplace un fichier vers le sous-dossier aih/ s'il existe à l'ancien emplacement."""
    import os as _os
    import shutil as _shutil
    if _os.path.isfile(old_path) and not _os.path.isfile(new_path):
        _os.makedirs(_os.path.dirname(new_path), exist_ok=True)
        _shutil.move(old_path, new_path)
        logging.info(f"[AIH] Migrated {old_path} → {new_path}")

def _get_aih_user_dir():
    """Retourne le dossier user/default/aih/ de ComfyUI."""
    try:
        import folder_paths
        user_dir = folder_paths.get_user_directory()
    except Exception:
        user_dir = os.path.join(os.path.dirname(_base), "user")
    return os.path.join(user_dir, "default", "aih")

def _get_presets_path():
    """Retourne le chemin du fichier de presets de l'Elements Picker."""
    return os.path.join(_get_aih_user_dir(), "aih_elements_presets.json")

def _load_module(filepath, name):
    """Charge un fichier Python comme module par son chemin absolu.

    Important : on declare les packages parents `AIH_ComfyUI` et
    `AIH_ComfyUI.nodes` dans sys.modules AVANT d'executer le module,
    et on utilise un nom complet (`AIH_ComfyUI.nodes.<name>`) avec
    `__package__` set. Cela permet aux `from . import X` dans les
    modules charges de fonctionner. Sinon : ImportError "attempted
    relative import with no known parent package".
    """
    # Declarer AIH_ComfyUI (grand-parent) dans sys.modules
    grandparent_name = "AIH_ComfyUI"
    if grandparent_name not in sys.modules:
        grandparent_dir = os.path.dirname(_nodes_dir)
        gp_spec = importlib.util.spec_from_file_location(
            grandparent_name,
            os.path.join(grandparent_dir, "__init__.py"),
            submodule_search_locations=[grandparent_dir],
        )
        if gp_spec is not None:
            gp_mod = importlib.util.module_from_spec(gp_spec)
            sys.modules[grandparent_name] = gp_mod

    # Declarer AIH_ComfyUI.nodes (parent) dans sys.modules
    parent_name = f"{grandparent_name}.nodes"
    if parent_name not in sys.modules:
        p_spec = importlib.util.spec_from_file_location(
            parent_name,
            os.path.join(_nodes_dir, "__init__.py"),
            submodule_search_locations=[_nodes_dir],
        )
        if p_spec is not None:
            p_mod = importlib.util.module_from_spec(p_spec)
            sys.modules[parent_name] = p_mod

    # Charger le module avec son nom complet pour que les relative imports
    # (`from . import _credentials`) fonctionnent.
    full_name = f"{parent_name}.{name}"
    spec = importlib.util.spec_from_file_location(full_name, filepath)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = parent_name  # requis pour les relative imports
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod

# Charger les nodes depuis AIH_ComfyUI/nodes/
_nodes_dir = os.path.join(_base, "AIH_ComfyUI", "nodes")

_elements_mod = _load_module(
    os.path.join(_nodes_dir, "elements_node.py"),
    "AIHElementsNode"
)
_enhance_mod = _load_module(
    os.path.join(_nodes_dir, "enhance_node.py"),
    "AIHEnhanceNode"
)
_ideogram4_mod = _load_module(
    os.path.join(_nodes_dir, "ideogram4_node.py"),
    "AIHIdeogram4Node"
)
_ideogram_prep_mod = _load_module(
    os.path.join(_nodes_dir, "ideogram_prep_node.py"),
    "AIHIdeogramPrepNode"
)
_ideogram_parse_mod = _load_module(
    os.path.join(_nodes_dir, "ideogram_parse_node.py"),
    "AIHIdeogramParseNode"
)
_prep_mod = _load_module(
    os.path.join(_nodes_dir, "prep_node.py"),
    "AIHPromptPrepNode"
)
_diag_mod = _load_module(
    os.path.join(_nodes_dir, "diagnostic_node.py"),
    "AIHDiagnosticNode"
)
_keywords_mod = _load_module(
    os.path.join(_nodes_dir, "keywords_node.py"),
    "AIHKeywordsNode"
)

# Charger le module Terminal (utilise par la route WebSocket ci-dessous)
# NB : ce module ne declare AUCUNE node ComfyUI — le terminal est un
# panel flottant JS, pas une node (voir web/js/aih_terminal_widget.js).
_terminal_mod = _load_module(
    os.path.join(_base, "AIH_ComfyUI", "terminal.py"),
    "AIHTerminal"
)

# Charger le module update_manager (utilise par les routes HTTP ci-dessous)
_update_manager_mod = _load_module(
    os.path.join(_base, "AIH_ComfyUI", "update_manager.py"),
    "AIHUpdateManager"
)

# Charger _credentials (requis par model_manager pour l'auth API)
_credentials_mod = _load_module(
    os.path.join(_nodes_dir, "_credentials.py"),
    "_credentials"
)

# Migrer les credentials vers le sous-dossier aih/
try:
    _aih_dir = _get_aih_user_dir()
    os.makedirs(_aih_dir, exist_ok=True)
    _old_creds = os.path.join(os.path.dirname(_aih_dir), "aih_credentials.json")
    _new_creds = os.path.join(_aih_dir, "credentials.json")
    _migrate_to_aih_subfolder(_old_creds, _new_creds)
except Exception as _e:
    logging.warning(f"[AIH] Credentials migration failed: {_e}")

# Charger les modules workflow sharing
_custom_nodes_mgr_mod = _load_module(
    os.path.join(_nodes_dir, "custom_nodes_manager.py"),
    "AIHCustomNodesManager"
)
_model_mgr_mod = _load_module(
    os.path.join(_nodes_dir, "model_manager.py"),
    "AIHModelManager"
)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "web"

if _elements_mod and hasattr(_elements_mod, "AIHElementsNode"):
    cls = _elements_mod.AIHElementsNode
    NODE_CLASS_MAPPINGS["AIHElementsNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHElementsNode"] = "AIH Elements Picker"

if _enhance_mod and hasattr(_enhance_mod, "AIHEnhanceNode"):
    cls = _enhance_mod.AIHEnhanceNode
    NODE_CLASS_MAPPINGS["AIHEnhanceNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHEnhanceNode"] = "AIH Prompt Enhancer"

if _ideogram4_mod and hasattr(_ideogram4_mod, "AIHIdeogram4Node"):
    cls = _ideogram4_mod.AIHIdeogram4Node
    NODE_CLASS_MAPPINGS["AIHIdeogram4Node"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHIdeogram4Node"] = "AIH Ideogram 4 Builder"

if _ideogram_prep_mod and hasattr(_ideogram_prep_mod, "AIHIdeogramPrepNode"):
    cls = _ideogram_prep_mod.AIHIdeogramPrepNode
    NODE_CLASS_MAPPINGS["AIHIdeogramPrepNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHIdeogramPrepNode"] = "AIH Ideogram Prep"

if _ideogram_parse_mod and hasattr(_ideogram_parse_mod, "AIHIdeogramParseNode"):
    cls = _ideogram_parse_mod.AIHIdeogramParseNode
    NODE_CLASS_MAPPINGS["AIHIdeogramParseNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHIdeogramParseNode"] = "AIH Ideogram Parse"

if _prep_mod and hasattr(_prep_mod, "AIHPromptPrepNode"):
    cls = _prep_mod.AIHPromptPrepNode
    NODE_CLASS_MAPPINGS["AIHPromptPrepNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHPromptPrepNode"] = "AIH Prompt Prep"

if _diag_mod and hasattr(_diag_mod, "AIHDiagnosticNode"):
    cls = _diag_mod.AIHDiagnosticNode
    NODE_CLASS_MAPPINGS["AIHDiagnosticNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHDiagnosticNode"] = "AIH Diagnostic"

if _keywords_mod and hasattr(_keywords_mod, "AIHKeywordsNode"):
    cls = _keywords_mod.AIHKeywordsNode
    NODE_CLASS_MAPPINGS["AIHKeywordsNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHKeywordsNode"] = "AIH Keywords"

# ── Routes HTTP (update + restart) ──────────────────────────────────
# Ces routes sont appelees par le menu ComfyUI (aih_menu.js) pour
# mettre a jour le repo Git local. Elles n'interagissent PAS avec le
# backend distant — tout reste sur la machine ComfyUI.

if _routes is not None and _update_manager_mod is not None:
    from aiohttp import web as _aio_web

    @_routes.post("/aih/update")
    async def _aih_update_route(request):
        try:
            result = _update_manager_mod.update_repo()
            return _aio_web.json_response(result)
        except Exception as e:
            import traceback
            return _aio_web.json_response({
                "status": "error",
                "message": f"Exception: {e}",
                "log": traceback.format_exc(),
                "updated": False,
            }, status=500)

    @_routes.post("/aih/restart")
    async def _aih_restart_route(request):
        try:
            result = _update_manager_mod.restart_server()
            return _aio_web.json_response(result)
        except Exception as e:
            import traceback
            return _aio_web.json_response({
                "status": "error",
                "message": f"Exception: {e}",
                "log": traceback.format_exc(),
            }, status=500)

    # ── Routes credentials (lecture / ecriture du fichier local) ───
    # Le menu AIH → Compte appelle ces routes pour lire/ecrire
    # ComfyUI/user/default/aih/credentials.json (api_key + server_url).
    # Les nodes Python lisent ce fichier via le helper _credentials.

    @_routes.get("/aih/credentials")
    async def _aih_get_credentials_route(request):
        import os as _os  # import local pour eviter les problemes de scope
        try:
            # Charger _credentials par chemin absolu (comme les nodes)
            # pour eviter les problemes de relative import dans le contexte
            # des routes ComfyUI.
            _load_module(
                _os.path.join(_nodes_dir, "_credentials.py"),
                "_credentials",
            )
            import AIH_ComfyUI.nodes._credentials as _creds_mod
            creds = _creds_mod._load_aih_credentials(use_cache=False)
            return _aio_web.json_response({
                "status": "ok",
                "api_key": creds.get("api_key", ""),
                "server_url": creds.get("server_url", "https://kw.holaf.fr"),
                "path": _creds_mod.get_credentials_path(),
                "exists": _os.path.isfile(_creds_mod.get_credentials_path()),
            })
        except Exception as e:
            return _aio_web.json_response({
                "status": "error",
                "message": f"Exception: {e}",
            }, status=500)

    @_routes.post("/aih/credentials")
    async def _aih_save_credentials_route(request):
        import os as _os  # import local pour eviter les problemes de scope
        import json as _json
        from datetime import datetime as _dt
        try:
            # Charger _credentials par chemin absolu (cf. GET route)
            _load_module(
                _os.path.join(_nodes_dir, "_credentials.py"),
                "_credentials",
            )
            import AIH_ComfyUI.nodes._credentials as _creds_mod
            data = await request.json()
            api_key = (data.get("api_key") or "").strip()
            server_url = (data.get("server_url") or "https://kw.holaf.fr").strip()

            creds_path = _creds_mod.get_credentials_path()
            _os.makedirs(_os.path.dirname(creds_path), exist_ok=True)

            # Permissions restrictives (Linux)
            if _os.name != 'nt':
                old_umask = _os.umask(0o077)
            try:
                with open(creds_path, "w", encoding="utf-8") as f:
                    _json.dump({
                        "api_key": api_key,
                        "server_url": server_url,
                        "updated_at": _dt.utcnow().isoformat() + "Z",
                    }, f, indent=2)
            finally:
                if _os.name != 'nt':
                    _os.umask(old_umask)

            # Invalider le cache pour que les nodes lisent la nouvelle valeur
            _creds_mod.invalidate_cache()

            return _aio_web.json_response({
                "status": "ok",
                "path": creds_path,
                "api_key_len": len(api_key),
            })
        except Exception as e:
            import traceback
            return _aio_web.json_response({
                "status": "error",
                "message": f"Exception: {e}",
                "log": traceback.format_exc(),
            }, status=500)

    print("[AIH] Update routes registered: POST /aih/update, /aih/restart")
    print("[AIH] Credentials routes registered: GET/POST /aih/credentials")

    # ── Routes Elements Presets (sauvegarde locale dans user/default/) ───
    # Les presets de l'Elements Picker sont stockés dans un fichier JSON local
    # pour ne pas saturer le workflow et ne pas être partagés avec d'autres.

    @_routes.get("/aih/elements/presets")
    async def _aih_get_elements_presets(request):
        """Liste tous les presets sauvegardés."""
        import os as _os
        import json as _json
        try:
            presets_path = _get_presets_path()
            if _os.path.isfile(presets_path):
                with open(presets_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                return _aio_web.json_response({"status": "ok", "presets": data.get("presets", [])})
            return _aio_web.json_response({"status": "ok", "presets": []})
        except Exception as e:
            return _aio_web.json_response({"status": "error", "message": str(e)}, status=500)

    @_routes.post("/aih/elements/presets")
    async def _aih_save_elements_preset(request):
        """Sauvegarde ou met à jour un preset."""
        import os as _os
        import json as _json
        from datetime import datetime as _dt
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            preset_data = body.get("data", {})
            if not name:
                return _aio_web.json_response({"status": "error", "message": "Name required"}, status=400)

            presets_path = _get_presets_path()
            _os.makedirs(_os.path.dirname(presets_path), exist_ok=True)

            # Lire les presets existants
            presets = []
            if _os.path.isfile(presets_path):
                with open(presets_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                    presets = data.get("presets", [])

            # Chercher si un preset avec ce nom existe déjà (update)
            existing_idx = None
            for i, p in enumerate(presets):
                if p.get("name") == name:
                    existing_idx = i
                    break

            preset_obj = {
                "name": name,
                "data": preset_data,
                "updated_at": _dt.utcnow().isoformat() + "Z"
            }

            if existing_idx is not None:
                presets[existing_idx] = preset_obj
            else:
                presets.append(preset_obj)

            with open(presets_path, "w", encoding="utf-8") as f:
                _json.dump({"presets": presets}, f, indent=2, ensure_ascii=False)

            return _aio_web.json_response({"status": "ok", "name": name, "count": len(presets)})
        except Exception as e:
            import traceback
            return _aio_web.json_response({"status": "error", "message": str(e), "log": traceback.format_exc()}, status=500)

    @_routes.post("/aih/elements/presets/delete")
    async def _aih_delete_elements_preset(request):
        """Supprime un preset par son nom."""
        import os as _os
        import json as _json
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            if not name:
                return _aio_web.json_response({"status": "error", "message": "Name required"}, status=400)

            presets_path = _get_presets_path()
            if not _os.path.isfile(presets_path):
                return _aio_web.json_response({"status": "ok", "deleted": False})

            with open(presets_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                presets = data.get("presets", [])

            new_presets = [p for p in presets if p.get("name") != name]

            with open(presets_path, "w", encoding="utf-8") as f:
                _json.dump({"presets": new_presets}, f, indent=2, ensure_ascii=False)

            return _aio_web.json_response({"status": "ok", "deleted": len(presets) != len(new_presets)})
        except Exception as e:
            return _aio_web.json_response({"status": "error", "message": str(e)}, status=500)

    print("[AIH] Elements presets routes registered")

    # ── Route Blobby Exec (commandes git locales) ─────────────
    @_routes.post("/aih/blobby/exec")
    async def _aih_blobby_exec_route(request):
        """Execute une commande git locale sur la machine ComfyUI."""
        import os as _os
        import subprocess as _sp
        try:
            data = await request.json()
            action = (data.get("action") or "").strip()
            cmd = (data.get("command") or "").strip()

            if action == "shell":
                """Execute n'importe quelle commande shell (Windows + Linux).
                Blobby a un acces terminal complet : ls, dir, git, python, pip, cat, etc.
                """
                cmd = (data.get("command") or "").strip()
                if not cmd:
                    return _aio_web.json_response({"ok": False, "output": "⚠️ Commande vide"}, status=400)
                # Limiter la durée des commandes shell
                # Utiliser /bin/bash si disponible (support des boucles for, etc.)
                _shell = _os.environ.get('SHELL', '/bin/sh')
                if _os.path.exists('/bin/bash'):
                    _shell = '/bin/bash'
                try:
                    r = _sp.run(cmd, shell=True, executable=_shell, capture_output=True, text=True, timeout=15)
                    out = r.stdout.strip()
                    if r.stderr: out += "\n" + r.stderr.strip()
                    if r.returncode != 0:
                        out += f"\n❌ Code: {r.returncode}"
                    if not out:
                        out = "✅ Commande exécutée (pas de sortie)"
                    return _aio_web.json_response({"ok": True, "output": out})
                except _sp.TimeoutExpired:
                    return _aio_web.json_response({"ok": False, "output": "⏱️ Commande trop longue (>15s)"})
                except Exception as e:
                    return _aio_web.json_response({"ok": False, "output": f"❌ Erreur: {e}"})

            else:
                return _aio_web.json_response({"ok": False, "output": f"Action '{action}' inconnue"}, status=400)

        except Exception as e:
            import traceback
            return _aio_web.json_response({"ok": False, "output": f"❌ Erreur: {e}", "log": traceback.format_exc()}, status=500)

    print("[AIH] Blobby exec route registered: POST /aih/blobby/exec")

# ── Routes workflow sharing ─────────────────────────────────────

if _routes is not None:
    _aio_web = web if 'web' in dir() else __import__('aiohttp').web

    if _custom_nodes_mgr_mod is not None:
        @_routes.get("/api/aih/custom-nodes")
        async def _aih_list_custom_nodes(request):
            try:
                nodes = _custom_nodes_mgr_mod._get_installed_custom_nodes()
                return _aio_web.json_response({"nodes": nodes})
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] custom-nodes error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.post("/api/aih/custom-nodes/install")
        async def _aih_install_node(request):
            try:
                body = await request.json()
                git_url = body.get("git_url", "").strip()
                name = body.get("name", "").strip()
                if not git_url:
                    return _aio_web.json_response({"error": "git_url required"}, status=400)
                result = _custom_nodes_mgr_mod._install_custom_node(git_url, name)
                status = 200 if result["success"] else 400
                return _aio_web.json_response(result, status=status)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] install-node error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        print("[AIH] Custom nodes routes registered: GET /api/aih/custom-nodes")

    if _model_mgr_mod is not None:
        @_routes.get("/api/aih/models/list")
        async def _aih_list_models(request):
            try:
                models = _model_mgr_mod.list_local_models()
                return _aio_web.json_response(models)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] models-list error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/api/aih/models/remote")
        async def _aih_list_remote_models(request):
            """Proxy : liste les modèles distants depuis le backend AIH."""
            try:
                page = int(request.query.get('page', 1))
                limit = int(request.query.get('limit', 50))
                type_filter = request.query.get('type', '') or None
                search = request.query.get('search', '') or None
                sort = request.query.get('sort', 'created_at')
                order = request.query.get('order', 'desc')
            except (ValueError, TypeError):
                return _aio_web.json_response({'error': 'Paramètres invalides'}, status=400)

            import asyncio as _aio
            import functools as _ft
            loop = _aio.get_event_loop()
            data = await loop.run_in_executor(
                None, _ft.partial(
                    _model_mgr_mod.list_remote_models,
                    page, limit, type_filter, search, sort, order
                )
            )
            return _aio_web.json_response(data)

        @_routes.get("/api/aih/models/local")
        async def _aih_list_local_models(request):
            """Liste les modèles locaux (scan du dossier models/ de ComfyUI)."""
            try:
                type_filter = request.query.get('type', '') or None
                search = request.query.get('search', '') or None
            except (ValueError, TypeError):
                return _aio_web.json_response({'error': 'Paramètres invalides'}, status=400)

            import asyncio as _aio
            import functools as _ft
            loop = _aio.get_event_loop()
            models = await loop.run_in_executor(
                None, _ft.partial(
                    _model_mgr_mod.list_local_models,
                    type_filter=type_filter, search=search
                )
            )
            return _aio_web.json_response({'items': models, 'total': len(models)})

        @_routes.post("/api/aih/models/upload")
        async def _aih_upload_model(request):
            try:
                body = await request.json()
                filepath = body.get("path", "")
                file_type = body.get("type", "model")
                import os as _os
                if not filepath or not _os.path.isfile(filepath):
                    return _aio_web.json_response({"error": "path required and must exist"}, status=400)
                # Lancer l'upload dans un thread pour ne pas bloquer l'event loop
                import asyncio as _aio
                import functools as _ft
                loop = _aio.get_event_loop()
                result = await loop.run_in_executor(
                    None, _ft.partial(_model_mgr_mod.upload_model_to_server, filepath, file_type)
                )
                status = 200 if result["success"] else 400
                return _aio_web.json_response(result, status=status)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] upload-model error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/api/aih/models/upload/progress")
        async def _aih_upload_progress(request):
            try:
                filepath = request.query.get("path", "")
                if not filepath:
                    return _aio_web.json_response({"error": "path required"}, status=400)
                p = _model_mgr_mod.get_upload_progress(filepath)
                if p is None:
                    return _aio_web.json_response(None, status=200)
                return _aio_web.json_response(p)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] upload-progress error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.post("/api/aih/models/fingerprint")
        async def _aih_fingerprint_model(request):
            try:
                body = await request.json()
                filepath = body.get("path", "")
                import os as _os
                if not filepath or not _os.path.isfile(filepath):
                    return _aio_web.json_response({"error": "path required and must exist"}, status=400)
                fp = _model_mgr_mod._compute_fingerprint(filepath)
                if fp:
                    return _aio_web.json_response(fp)
                return _aio_web.json_response({"error": "fingerprint failed"}, status=500)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] fingerprint error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/api/aih/models/download/progress")
        async def _aih_download_progress(request):
            try:
                upload_id = request.query.get("upload_id", "")
                if not upload_id:
                    return _aio_web.json_response({"error": "upload_id required"}, status=400)
                p = _model_mgr_mod.get_download_progress(upload_id)
                if p is None:
                    return _aio_web.json_response(None, status=200)
                return _aio_web.json_response(p)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] download-progress error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.post("/api/aih/models/download")
        async def _aih_download_model(request):
            try:
                body = await request.json()
                upload_id = body.get("upload_id", "")
                filename = body.get("filename", "")
                file_type = body.get("type", "model")
                dest_path = body.get("dest_path", None)
                if not upload_id or not filename:
                    return _aio_web.json_response({"error": "upload_id and filename required"}, status=400)
                import asyncio as _aio2
                import functools as _ft2
                loop = _aio2.get_event_loop()
                result = await loop.run_in_executor(
                    None, _ft2.partial(_model_mgr_mod.download_model_from_server, upload_id, filename, file_type, dest_path)
                )
                status = 200 if result["success"] else 400
                return _aio_web.json_response(result, status=status)
            except Exception as e:
                import logging as _log
                _log.exception(f"[AIH] download-model error: {e}")
                return _aio_web.json_response({"error": str(e)}, status=500)

        print("[AIH] Model manager routes registered: GET /api/aih/models/list, /api/aih/models/remote, /api/aih/models/local")

    # ── Route WebSocket Terminal (PAS DE MOT DE PASSE) ──────────────
    # Le widget AIH Terminal (aih_terminal_widget.js) ouvre un
    # WebSocket sur /aih/terminal pour piloter un PTY distant.
    # Cette route est sans authentification : elle donne un shell à
    # quiconque peut atteindre le serveur ComfyUI. À n'utiliser que
    # sur localhost ou derrière un reverse proxy authentifié.
    # NB : on utilise @_routes.get() (et non add_get) car aiohttp
    # detecte le WebSocket via l'upgrade request — cf. comment
    # CUI-Holaf-Utils declare sa route /holaf/terminal.
    if _terminal_mod and hasattr(_terminal_mod, "websocket_handler"):

        @_routes.get("/aih/terminal")  # WebSocket
        async def _aih_terminal_ws_route(request):
            return await _terminal_mod.websocket_handler(request)

        print("[AIH] Terminal WebSocket route registered: GET /aih/terminal (NO PASSWORD)")
else:
    # Si les routes ne sont pas enregistrees, on ne fait rien de plus
    # (l'item "Update" du menu ne fonctionnera pas, mais l'extension
    # reste chargee pour les nodes)
    pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
