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
_diag_mod = _load_module(
    os.path.join(_nodes_dir, "diagnostic_node.py"),
    "AIHDiagnosticNode"
)
_keywords_mod = _load_module(
    os.path.join(_nodes_dir, "keywords_node.py"),
    "AIHKeywordsNode"
)
_preview_mod = _load_module(
    os.path.join(_nodes_dir, "preview_node.py"),
    "AIHPreviewNode"
)
_refimgprep_mod = _load_module(
    os.path.join(_nodes_dir, "ref_image_prep_node.py"),
    "AIHRefImagePrepNode"
)
_music_mod = _load_module(
    os.path.join(_nodes_dir, "music_node.py"),
    "AIHMusicNode"
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

# Charger _llm_helper (helper partagé pour appels LLM unifiés)
_llm_helper_mod = _load_module(
    os.path.join(_nodes_dir, "_llm_helper.py"),
    "_llm_helper"
)

# Charger les nodes de configuration LLM
_lmstudio_settings_mod = _load_module(
    os.path.join(_nodes_dir, "lmstudio_settings_node.py"),
    "AIHLMStudioSettingsNode"
)
_openai_settings_mod = _load_module(
    os.path.join(_nodes_dir, "openai_settings_node.py"),
    "AIHOpenAISettingsNode"
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

# ── Store SQLite local + moteur de synchronisation (mode local) ────
# Chargement défensif : store.py et sync_engine.py ne déclarent AUCUNE
# node ComfyUI. On les importe par package quand c'est possible (le
# package AIH_ComfyUI est pré-enregistré dans sys.modules par
# _load_module, donc `from AIH_ComfyUI import X` n'exécute PAS le
# __init__.py mort), sinon par chemin absolu via importlib. En cas
# d'échec, _aih_store_mod/_aih_sync_mod restent None et l'extension
# continue de fonctionner (juste sans le mode local).

def _load_local_modules():
    """Importe store.py, sync_engine.py et embedding_engine.py de façon robuste.

    Retourne:
        tuple: (store_mod, sync_engine_mod, embedding_engine_mod), chacun None
        si indisponible.
    """
    store_mod = None
    sync_mod = None
    emb_mod = None

    # 1) Import par package (AIH_ComfyUI est déjà dans sys.modules).
    try:
        from AIH_ComfyUI import store as _s
        store_mod = _s
    except Exception:
        store_mod = None
    try:
        from AIH_ComfyUI import sync_engine as _se
        sync_mod = _se
    except Exception:
        sync_mod = None
    try:
        from AIH_ComfyUI import embedding_engine as _ee
        emb_mod = _ee
    except Exception:
        emb_mod = None

    # 2) Fallback par chemin absolu (tests / runtime non-ComfyUI).
    if store_mod is None:
        try:
            _spath = os.path.join(_base, "AIH_ComfyUI", "store.py")
            _spec = importlib.util.spec_from_file_location("aih_store", _spath)
            if _spec is not None and _spec.loader is not None:
                store_mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(store_mod)
        except Exception:
            store_mod = None
    if sync_mod is None:
        try:
            _spath = os.path.join(_base, "AIH_ComfyUI", "sync_engine.py")
            _spec = importlib.util.spec_from_file_location("aih_sync_engine", _spath)
            if _spec is not None and _spec.loader is not None:
                sync_mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(sync_mod)
        except Exception:
            sync_mod = None
    if emb_mod is None:
        try:
            _spath = os.path.join(_base, "AIH_ComfyUI", "embedding_engine.py")
            _spec = importlib.util.spec_from_file_location("aih_embedding_engine", _spath)
            if _spec is not None and _spec.loader is not None:
                emb_mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(emb_mod)
        except Exception:
            emb_mod = None

    return store_mod, sync_mod, emb_mod


_aih_store_mod, _aih_sync_mod, _aih_emb_mod = _load_local_modules()

# Dernier filet : si store a échoué mais que sync_engine est chargé, ce
# dernier embarque déjà sa propre référence à store (sync_engine.store).
if _aih_store_mod is None and _aih_sync_mod is not None:
    _aih_store_mod = getattr(_aih_sync_mod, "store", None)

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

if _diag_mod and hasattr(_diag_mod, "AIHDiagnosticNode"):
    cls = _diag_mod.AIHDiagnosticNode
    NODE_CLASS_MAPPINGS["AIHDiagnosticNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHDiagnosticNode"] = "AIH Diagnostic"

if _keywords_mod and hasattr(_keywords_mod, "AIHKeywordsNode"):
    cls = _keywords_mod.AIHKeywordsNode
    NODE_CLASS_MAPPINGS["AIHKeywordsNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHKeywordsNode"] = "AIH Keywords"

if _preview_mod and hasattr(_preview_mod, "AIHPreviewNode"):
    cls = _preview_mod.AIHPreviewNode
    NODE_CLASS_MAPPINGS["AIHPreviewNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHPreviewNode"] = "AIH Preview"

if _refimgprep_mod and hasattr(_refimgprep_mod, "AIHRefImagePrepNode"):
    cls = _refimgprep_mod.AIHRefImagePrepNode
    NODE_CLASS_MAPPINGS["AIH Ref Image Prep"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIH Ref Image Prep"] = "AIH Ref Image Prep"

if _lmstudio_settings_mod and hasattr(_lmstudio_settings_mod, "AIHLMStudioSettingsNode"):
    cls = _lmstudio_settings_mod.AIHLMStudioSettingsNode
    NODE_CLASS_MAPPINGS["AIHLMStudioSettingsNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHLMStudioSettingsNode"] = "AIH LMStudio Settings"

if _openai_settings_mod and hasattr(_openai_settings_mod, "AIHOpenAISettingsNode"):
    cls = _openai_settings_mod.AIHOpenAISettingsNode
    NODE_CLASS_MAPPINGS["AIHOpenAISettingsNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHOpenAISettingsNode"] = "AIH OpenAI Settings"

if _music_mod and hasattr(_music_mod, "AIHMusicNode"):
    cls = _music_mod.AIHMusicNode
    NODE_CLASS_MAPPINGS["AIHMusicNode"] = cls
    NODE_DISPLAY_NAME_MAPPINGS["AIHMusicNode"] = "AIH Music"

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
        """Sauvegarde ou met à jour un preset, ou action cleanup."""
        import os as _os
        import json as _json
        from datetime import datetime as _dt
        try:
            body = await request.json()

            # ── Action cleanup : vide le fichier local (après migration distante) ──
            if body.get("action") == "cleanup":
                presets_path = _get_presets_path()
                if _os.path.isfile(presets_path):
                    with open(presets_path, "w", encoding="utf-8") as f:
                        _json.dump({"presets": []}, f)
                return _aio_web.json_response({"status": "ok", "cleaned": True})

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

    # ── Routes OpenAI API Keys (stockage local par base_url) ───
    @_routes.get("/aih/openai/keys")
    async def _aih_get_openai_keys(request):
        """Retourne les clés API stockées, optionnellement filtrées par base_url."""
        import os as _os, json as _json
        try:
            keys_path = _os.path.join(_get_aih_user_dir(), "openai_keys.json")
            if _os.path.isfile(keys_path):
                with open(keys_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
            else:
                data = {}
            # Filtrer par base_url si demandé
            base_url = request.query.get("base_url", "").rstrip("/")
            if base_url:
                return _aio_web.json_response({"status": "ok", "key": data.get(base_url, "")})
            return _aio_web.json_response({"status": "ok", "keys": data})
        except Exception as e:
            return _aio_web.json_response({"status": "error", "message": str(e)}, status=500)

    @_routes.post("/aih/openai/keys")
    async def _aih_save_openai_key(request):
        """Sauvegarde une clé API pour un base_url donné."""
        import os as _os, json as _json
        try:
            body = await request.json()
            base_url = (body.get("base_url") or "").strip().rstrip("/")
            api_key = (body.get("api_key") or "").strip()
            if not base_url:
                return _aio_web.json_response({"status": "error", "message": "base_url required"}, status=400)

            keys_path = _os.path.join(_get_aih_user_dir(), "openai_keys.json")
            _os.makedirs(_os.path.dirname(keys_path), exist_ok=True)

            # Lire les clés existantes
            data = {}
            if _os.path.isfile(keys_path):
                with open(keys_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)

            if api_key:
                data[base_url] = api_key
            elif base_url in data:
                del data[base_url]  # Supprimer si clé vide

            with open(keys_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)

            return _aio_web.json_response({"status": "ok", "base_url": base_url})
        except Exception as e:
            return _aio_web.json_response({"status": "error", "message": str(e)}, status=500)

    print("[AIH] OpenAI keys routes registered")

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

    # ── Route statut local (store SQLite + sync engine) ───────────────
    # GET /aih/local/status → état du mode local. La route doit être
    # robuste et ne JAMAIS bloquer l'event loop : lectures avec timeout
    # court, try/except large, et valeurs par défaut si le store échoue.
    if _aih_store_mod is not None:

        @_routes.get("/aih/local/status")
        async def _aih_local_status_route(request):
            """État du mode local (store + sync engine + music3).

            Quand sync_engine.get_sync_status() est dispo (via _aih_sync_mod),
            on fusionne son JSON (server_reachable, last_sync, pending_sync,
            conflicts, store_version, music3_last_updated) avec le contrat
            historique de la route (mode, etc.). Sinon fallback sur le store
            direct. Ne lève JAMAIS : try/except large + état minimal en
            dernier recours.
            """
            conn = None
            try:
                status = {}
                if _aih_sync_mod is not None and hasattr(
                    _aih_sync_mod, "get_sync_status"
                ):
                    status = _aih_sync_mod.get_sync_status() or {}

                if not status:
                    # Fallback store direct (sync_engine absent ou à vide).
                    conn = _aih_store_mod.get_conn()
                    # Timeout court : la base peut être verrouillée par le
                    # thread de sync — on ne veut pas bloquer ici.
                    conn.execute("PRAGMA busy_timeout=1000")
                    _aih_store_mod.init_store(conn)

                    last_updated = _aih_store_mod.get_meta(conn, "sync.last_updated")
                    reachable_raw = _aih_store_mod.get_meta(conn, "sync.server_reachable")
                    schema_raw = _aih_store_mod.get_meta(conn, "schema_version")

                    store_version = 1
                    if schema_raw:
                        try:
                            store_version = int(str(schema_raw).strip())
                        except (ValueError, TypeError):
                            store_version = 1

                    if reachable_raw is not None:
                        server_reachable = str(reachable_raw).strip().lower() in (
                            "1", "true", "ok", "yes", "reachable"
                        )
                    else:
                        # Pas de flag explicite : une sync réussie
                        # (sync.last_updated renseigné) implique que le
                        # serveur a été joignable à ce moment-là.
                        server_reachable = bool(last_updated)

                    status = {
                        "server_reachable": server_reachable,
                        "last_sync": last_updated or None,
                        "pending_sync": 0,
                        "conflicts": 0,
                        "store_version": store_version,
                        "music3_last_updated": None,
                    }

                # Fusion avec le contrat JSON historique de la route.
                return _aio_web.json_response({
                    "mode": "local",
                    "server_reachable": bool(status.get("server_reachable", False)),
                    "last_sync": status.get("last_sync") or None,
                    "pending_sync": int(status.get("pending_sync") or 0),
                    "conflicts": int(status.get("conflicts") or 0),
                    "store_version": int(status.get("store_version") or 1),
                    "music3_last_updated": status.get("music3_last_updated") or None,
                })
            except Exception as e:
                # Ne jamais casser la route : retour d'un état minimal.
                return _aio_web.json_response({
                    "mode": "local",
                    "server_reachable": False,
                    "last_sync": None,
                    "pending_sync": 0,
                    "conflicts": 0,
                    "store_version": 1,
                    "error": str(e),
                })
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # ── Routes lecture locale music3 (miroir écrit par sync_engine) ──
        # GET /aih/local/api/music3/manifest            → manifest.json local
        # GET /aih/local/api/music3/reference/{path:.*} → contenu texte d'une
        # référence locale (anti path-traversal). Chemin cohérent avec
        # sync_engine.sync_music3_local() :
        #   user_dir = store.get_store_path().parent.parent.parent, puis
        #   user_dir/aihelper/data/music3/...

        def _aih_music3_paths():
            """Retourne (music3_dir, refs_dir) ou (None, None) si indispo."""
            try:
                store_path = _aih_store_mod.get_store_path()
                user_dir = store_path.parent.parent.parent
                music3_dir = os.path.join(user_dir, "aihelper", "data", "music3")
                return music3_dir, os.path.join(music3_dir, "references")
            except Exception:
                return None, None

        @_routes.get("/aih/local/api/music3/manifest")
        async def _aih_music3_manifest_route(request):
            """JSON du manifest local music3, ou 404 si absent."""
            import json as _json
            try:
                music3_dir, _refs = _aih_music3_paths()
                if not music3_dir:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                manifest_path = os.path.join(music3_dir, "manifest.json")
                if not os.path.isfile(manifest_path):
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                return _aio_web.json_response(data)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/api/music3/reference/{path:.*}")
        async def _aih_music3_reference_route(request):
            """Contenu texte d'une référence music3 locale (anti path-traversal).

            Le chemin résolu doit rester sous base.resolve() (sinon 403), et
            le fichier doit exister (sinon 404). mimetype text/plain.
            """
            from pathlib import Path as _Path
            try:
                relpath = request.match_info.get("path", "")
                _music3_dir, refs_dir = _aih_music3_paths()
                if not refs_dir:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                base = _Path(refs_dir).resolve()
                target = (base / relpath).resolve()
                # Anti path-traversal : le fichier doit rester sous refs_dir.
                if os.path.commonpath([str(base), str(target)]) != str(base):
                    return _aio_web.json_response({"error": "forbidden"}, status=403)
                if not target.is_file():
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(target, "r", encoding="utf-8") as f:
                    content = f.read()
                return _aio_web.Response(text=content, content_type="text/plain")
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        # ── Recherche sémantique locale (embedding_engine) ────────────
        # GET  /aih/local/api/search/semantic    → recherche sémantique keywords
        # GET  /aih/local/api/embeddings/status  → état du moteur (config meta)
        # POST /aih/local/api/embeddings/build   → lance compute_all en thread
        # GET  /aih/local/api/embeddings/progress→ progression du build
        # Le moteur (_aih_emb_mod) est chargé comme store/sync (package d'abord,
        # chemin absolu en fallback, None si absent) : s'il est absent ou non
        # prêt, la recherche retombe sur du LIKE SQL et le build renvoie total=0.

        def _aih_embedding_rows(rows):
            """Rows [{"id", "text"}] pour compute_all depuis le miroir keywords."""
            out = []
            for r in rows:
                if r.get("id") is None:
                    continue
                text = " ".join(
                    filter(None, [r.get("keyword"), r.get("description")])
                ).strip()
                out.append({"id": r.get("id"), "text": text})
            return out

        def _aih_keyword_result(r, score):
            """Dictionnaire résultat ({id, keyword, description, ..., score})."""
            return {
                "id": r.get("id"),
                "keyword": r.get("keyword") or "",
                "description": r.get("description") or "",
                "section_title": r.get("section_title") or "",
                "subsection_title": r.get("subsection_title") or "",
                "nsfw": int(r.get("nsfw") or 0),
                "score": round(float(score or 0.0), 4),
            }

        def _aih_building_flag(raw):
            return str(raw or "").strip().lower() in ("1", "true", "ok", "yes")

        def _aih_start_embedding_build(emb_rows):
            """Lance compute_all('keyword', emb_rows) en thread daemon.

            Marque meta 'embedding.building'='1' et enregistre la progression
            dans meta 'embedding.progress' (JSON {done,total}). Retourne le
            nombre de rows à traiter (0 si le moteur est indisponible).
            """
            import json as _json
            import threading as _threading
            if _aih_store_mod is None or _aih_emb_mod is None:
                return 0
            emb_rows = [r for r in (emb_rows or []) if r.get("id") is not None]
            total = len(emb_rows)
            if total == 0:
                return 0

            def _write_progress(conn, done, total):
                _aih_store_mod.set_meta(
                    conn, "embedding.progress",
                    _json.dumps({"done": int(done), "total": int(total)}),
                )

            def _progress_cb(done, total):
                try:
                    conn = _aih_store_mod.get_conn()
                    try:
                        _write_progress(conn, done, total)
                    finally:
                        conn.close()
                except Exception:
                    pass

            def _worker():
                done = 0
                try:
                    if _aih_emb_mod is not None:
                        done = _aih_emb_mod.compute_all(
                            "keyword", emb_rows, progress_cb=_progress_cb
                        ) or 0
                except Exception:
                    done = 0
                finally:
                    # Toujours lever le flag building + progress final.
                    try:
                        conn = _aih_store_mod.get_conn()
                        try:
                            _aih_store_mod.set_meta(conn, "embedding.building", "0")
                            _write_progress(conn, done, total)
                        finally:
                            conn.close()
                    except Exception:
                        pass

            # Statut "building" immédiat (avant le démarrage du thread).
            try:
                conn = _aih_store_mod.get_conn()
                try:
                    _aih_store_mod.set_meta(conn, "embedding.building", "1")
                    _write_progress(conn, 0, total)
                finally:
                    conn.close()
            except Exception:
                pass

            _t = _threading.Thread(
                target=_worker, name="aih-embedding-build", daemon=True
            )
            _t.start()
            return total

        @_routes.get("/aih/local/api/search/semantic")
        async def _aih_local_search_semantic_route(request):
            """Recherche sémantique locale dans le miroir keywords.

            q obligatoire (400 si vide). Moteur prêt → embedding_engine.search()
            avec lazy build en arrière-plan ; sinon fallback LIKE SQL (score 0).
            Filtres nsfw / section appliqués après coup, tri par score desc.
            """
            try:
                q = (request.query.get("q") or "").strip()
                if not q:
                    return _aio_web.json_response(
                        {"error": "q required"}, status=400
                    )
                try:
                    limit = int(request.query.get("limit", 50))
                except (TypeError, ValueError):
                    return _aio_web.json_response(
                        {"error": "limit invalide"}, status=400
                    )
                if limit < 0:
                    limit = 50
                nsfw = (request.query.get("nsfw") or "").strip()
                section = (request.query.get("section") or "").strip()
                try:
                    min_score = float(
                        request.query.get("min_confidence")
                        or request.query.get("confidence")
                        or 0
                    )
                except (TypeError, ValueError):
                    return _aio_web.json_response(
                        {"error": "confidence invalide"}, status=400
                    )

                conn = _aih_store_mod.get_conn()
                try:
                    rows = _aih_store_mod.list_mirror(conn, "keywords", {})
                finally:
                    conn.close()

                by_id = {}
                for r in rows:
                    try:
                        by_id[int(r.get("id"))] = r
                    except (TypeError, ValueError):
                        continue

                results = []
                eng_ready = bool(
                    _aih_emb_mod is not None
                    and getattr(_aih_emb_mod, "is_ready", lambda: False)()
                )

                if eng_ready:
                    hits = _aih_emb_mod.search("keyword", q, limit, min_score)
                    if not hits:
                        # Build paresseux : aucun embedding pour ce fingerprint ?
                        fp = _aih_emb_mod.get_fingerprint()
                        conn = _aih_store_mod.get_conn()
                        try:
                            count = conn.execute(
                                "SELECT COUNT(*) AS c FROM local_embeddings "
                                "WHERE entity_type = 'keyword' "
                                "AND model_fingerprint = ?",
                                (fp,),
                            ).fetchone()["c"]
                        finally:
                            conn.close()
                        emb_rows = _aih_embedding_rows(rows)
                        if int(count or 0) == 0 and emb_rows:
                            _aih_start_embedding_build(emb_rows)
                            return _aio_web.json_response(
                                {"building": True, "results": []}
                            )
                    for h in hits:
                        r = by_id.get(h.get("id"))
                        if r is None:
                            continue
                        results.append(_aih_keyword_result(r, h.get("score")))
                else:
                    # Fallback : recherche LIKE simple sur le store, score 0.
                    like = q.lower()
                    for r in rows:
                        hay = " ".join(
                            str(r.get(k) or "")
                            for k in (
                                "keyword", "description",
                                "section_title", "subsection_title",
                            )
                        ).lower()
                        if like in hay:
                            results.append(_aih_keyword_result(r, 0.0))

                # Filtres nsfw / section (si fournis).
                if nsfw in ("0", "1"):
                    target = int(nsfw)
                    results = [
                        x for x in results if int(x.get("nsfw") or 0) == target
                    ]
                if section:
                    sections = [s.strip() for s in section.split(",") if s.strip()]
                    if sections:
                        results = [
                            x for x in results
                            if str(x.get("section_title") or "").strip() in sections
                            or str(by_id.get(x.get("id"), {}).get("section_id") or "").strip() in sections
                        ]

                results.sort(key=lambda x: x.get("score") or 0, reverse=True)
                return _aio_web.json_response(results[:limit])
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/api/embeddings/status")
        async def _aih_local_embeddings_status_route(request):
            """État du moteur d'embeddings local (config meta + compteur)."""
            import json as _json
            try:
                conn = _aih_store_mod.get_conn()
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) AS c FROM local_embeddings"
                    ).fetchone()["c"]
                    cfg_raw = _aih_store_mod.get_meta(conn, "embedding.config")
                    building_raw = _aih_store_mod.get_meta(
                        conn, "embedding.building", "0"
                    )
                finally:
                    conn.close()
                cfg = {}
                if cfg_raw:
                    try:
                        cfg = _json.loads(cfg_raw)
                    except (TypeError, ValueError):
                        cfg = {}
                ready = False
                if _aih_emb_mod is not None:
                    try:
                        ready = bool(_aih_emb_mod.is_ready())
                    except Exception:
                        ready = False
                return _aio_web.json_response({
                    "source": cfg.get("source") if cfg else None,
                    "model_name": cfg.get("model_name") if cfg else None,
                    "dim": int(cfg.get("dim") or 0) if cfg else 0,
                    "fingerprint": cfg.get("fingerprint") if cfg else None,
                    "ready": ready,
                    "total": int(total or 0),
                    "building": _aih_building_flag(building_raw),
                })
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.post("/aih/local/api/embeddings/build")
        async def _aih_local_embeddings_build_route(request):
            """Lance compute_all('keyword', rows) en thread daemon (non bloquant)."""
            try:
                conn = _aih_store_mod.get_conn()
                try:
                    rows = _aih_store_mod.list_mirror(conn, "keywords", {})
                finally:
                    conn.close()
                total = _aih_start_embedding_build(_aih_embedding_rows(rows))
                return _aio_web.json_response({"started": True, "total": total})
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/api/embeddings/progress")
        async def _aih_local_embeddings_progress_route(request):
            """Progression du build en cours (meta embedding.progress)."""
            import json as _json
            try:
                conn = _aih_store_mod.get_conn()
                try:
                    building_raw = _aih_store_mod.get_meta(
                        conn, "embedding.building", "0"
                    )
                    prog_raw = _aih_store_mod.get_meta(conn, "embedding.progress")
                finally:
                    conn.close()
                building = _aih_building_flag(building_raw)
                done = 0
                total = 0
                if prog_raw:
                    try:
                        prog = _json.loads(prog_raw)
                        done = int(prog.get("done") or 0)
                        total = int(prog.get("total") or 0)
                    except (TypeError, ValueError):
                        pass
                return _aio_web.json_response({
                    "building": building,
                    "done": done,
                    "total": total,
                })
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        # ── P4 : Frontend local (routes statiques /aih/local/*) ─────────
        # Le frontend web (frontend/) est servi depuis ComfyUI en mode local :
        # index + assets css/js + favicon. Anti path-traversal via realpath
        # containment, try/except large, style aiohttp existant.

        _aih_frontend_dir = os.path.join(_base, "frontend")
        _aih_frontend_base = os.path.realpath(_aih_frontend_dir)

        def _aih_frontend_file(relpath):
            """Résout <frontend>/<relpath> (None si absent ou hors base)."""
            from pathlib import Path as _P
            try:
                base = _P(_aih_frontend_base)
                target = (base / relpath).resolve()
                if os.path.commonpath([str(base), str(target)]) != str(base):
                    return None
                if not target.is_file():
                    return None
                return str(target)
            except Exception:
                return None

        @_routes.get("/aih/local/")
        async def _aih_local_index_route(request):
            """Sert frontend/index.html (assets absolus réécrits en /aih/local/)."""
            try:
                idx = os.path.join(_aih_frontend_dir, "index.html")
                if not os.path.isfile(idx):
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(idx, "r", encoding="utf-8") as f:
                    html = f.read()
                # Le backend Flask sert le frontend à la racine (assets en
                # absolu /css/... et /js/...) ; ici on les préfixe.
                html = html.replace('href="/css/', 'href="/aih/local/css/')
                html = html.replace('src="/js/', 'src="/aih/local/js/')
                return _aio_web.Response(text=html, content_type="text/html")
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/css/{path:.*}")
        async def _aih_local_css_route(request):
            """Sert un fichier CSS du frontend (mime text/css)."""
            try:
                relpath = request.match_info.get("path", "")
                if not relpath:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                path = _aih_frontend_file(os.path.join("css", relpath))
                if path is None:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                return _aio_web.Response(text=content, content_type="text/css")
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/js/{path:.*}")
        async def _aih_local_js_route(request):
            """Sert un fichier JS du frontend (mime application/javascript)."""
            try:
                relpath = request.match_info.get("path", "")
                if not relpath:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                path = _aih_frontend_file(os.path.join("js", relpath))
                if path is None:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                return _aio_web.Response(text=content, content_type="application/javascript")
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        @_routes.get("/aih/local/favicon{rest:.*}")
        async def _aih_local_favicon_route(request):
            """Sert le favicon du frontend s'il existe (ico/png/svg)."""
            try:
                rest = request.match_info.get("rest", "")
                name = ("favicon" + rest).strip("/") or "favicon.ico"
                path = _aih_frontend_file(name)
                if path is None:
                    return _aio_web.json_response({"error": "not found"}, status=404)
                with open(path, "rb") as f:
                    content = f.read()
                low = name.lower()
                if low.endswith(".png"):
                    ctype = "image/png"
                elif low.endswith(".svg"):
                    ctype = "image/svg+xml"
                else:
                    ctype = "image/x-icon"
                return _aio_web.Response(body=content, content_type=ctype)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        # ── P4 : Proxy JSON local (lecture du store) ────────────────────
        # Endpoints /aih/local/api/* : mêmes contrats JSON que le backend,
        # mais lus depuis les tables miroirs du store SQLite local.

        def _aih_local_conn():
            """Ouvre une connexion store (init_store défensif)."""
            conn = _aih_store_mod.get_conn()
            try:
                _aih_store_mod.init_store(conn)
            except Exception:
                pass
            return conn

        def _aih_local_decode(v):
            """Réhydrate un JSON dict/list stocké en texte (best-effort)."""
            import json as _json
            if not isinstance(v, str):
                return v
            s = v.strip()
            if not s or s[0] not in "[{":
                return v
            try:
                d = _json.loads(s)
            except (TypeError, ValueError):
                return v
            return d if isinstance(d, (dict, list)) else v

        def _aih_local_decode_row(row):
            return {k: _aih_local_decode(v) for k, v in row.items()}

        @_routes.get("/aih/local/api/sections")
        async def _aih_local_api_sections_route(request):
            """Liste des sections (dédupliquée sur section_id)."""
            conn = None
            try:
                conn = _aih_local_conn()
                seen = {}
                for r in _aih_store_mod.list_mirror(conn, "keywords", {}):
                    sid = r.get("section_id")
                    if sid is None or str(sid).strip() == "":
                        continue
                    sid = str(sid)
                    if sid in seen:
                        seen[sid]["total"] = seen[sid]["total"] + 1
                        seen[sid]["nsfw_count"] = seen[sid]["nsfw_count"] + int(r.get("nsfw") or 0)
                    else:
                        seen[sid] = {
                            "section_id": sid,
                            "section_title": r.get("section_title") or "",
                            "total": 1,
                            "nsfw_count": int(r.get("nsfw") or 0),
                        }
                items = [seen[k] for k in sorted(seen)]
                return _aio_web.json_response(items)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/subsections")
        async def _aih_local_api_subsections_route(request):
            """Liste des sous-sections dédupliquées (filtre ?section=)."""
            conn = None
            try:
                section = (request.query.get("section") or "").strip()
                sections = [s.strip() for s in section.split(",") if s.strip()] if section else None
                conn = _aih_local_conn()
                seen = {}
                for r in _aih_store_mod.list_mirror(conn, "keywords", {}):
                    sid = r.get("subsection_id")
                    if sid is None or str(sid).strip() == "":
                        continue
                    if sections and str(r.get("section_id") or "").strip() not in sections:
                        continue
                    sid = str(sid)
                    if sid in seen:
                        seen[sid]["total"] = seen[sid]["total"] + 1
                    else:
                        seen[sid] = {
                            "subsection_id": sid,
                            "subsection_title": r.get("subsection_title") or "",
                            "total": 1,
                        }
                items = [seen[k] for k in sorted(seen)]
                return _aio_web.json_response(items)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/stats")
        async def _aih_local_api_stats_route(request):
            """Statistiques globales (total / nsfw / public)."""
            conn = None
            try:
                conn = _aih_local_conn()
                rows = _aih_store_mod.list_mirror(conn, "keywords", {})
                total = len(rows)
                nsfw = sum(1 for r in rows if int(r.get("nsfw") or 0) == 1)
                public = sum(1 for r in rows if str(r.get("privacy_status") or "").strip() == "public")
                sections = set(
                    str(r.get("section_id") or "").strip()
                    for r in rows if r.get("section_id") not in (None, "")
                )
                subsections = set(
                    str(r.get("subsection_id") or "").strip()
                    for r in rows if r.get("subsection_id") not in (None, "")
                )
                return _aio_web.json_response({
                    "total": total,
                    "nsfw": nsfw,
                    "nsfw_total": nsfw,
                    "public": public,
                    "section_count": len(sections),
                    "subsection_count": len(subsections),
                    "generated_total": 0,
                })
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/keywords")
        async def _aih_local_api_keywords_route(request):
            """Liste des keywords filtrés (q / q_neg / section / subsection / nsfw / limit)."""
            conn = None
            try:
                q = (request.query.get("q") or "").strip().lower()
                q_neg = (request.query.get("q_neg") or "").strip().lower()
                nsfw = (request.query.get("nsfw") or "").strip()
                section = (request.query.get("section") or "").strip()
                subsection = (request.query.get("subsection") or "").strip()
                limit_raw = (request.query.get("limit") or "").strip()
                limit = None
                if limit_raw:
                    try:
                        limit = int(limit_raw)
                    except (TypeError, ValueError):
                        return _aio_web.json_response({"error": "limit invalide"}, status=400)
                    if limit < 0:
                        limit = None
                sections = [s.strip() for s in section.split(",") if s.strip()] if section else None
                subsections = [s.strip() for s in subsection.split(",") if s.strip()] if subsection else None

                conn = _aih_local_conn()
                out = []
                for r in _aih_store_mod.list_mirror(conn, "keywords", {}):
                    kw = r.get("keyword") or ""
                    desc = r.get("description") or ""
                    sec_id = str(r.get("section_id") or "").strip()
                    sec_title = str(r.get("section_title") or "").strip()
                    sub_id = str(r.get("subsection_id") or "").strip()
                    sub_title = str(r.get("subsection_title") or "").strip()
                    kw_nsfw = int(r.get("nsfw") or 0)
                    hay = " ".join([kw, desc, sec_title, sub_title]).lower()
                    if q and q not in hay:
                        continue
                    if q_neg and q_neg in hay:
                        continue
                    if nsfw in ("0", "1") and kw_nsfw != int(nsfw):
                        continue
                    if sections and sec_id not in sections and sec_title not in sections:
                        continue
                    if subsections and sub_id not in subsections and sub_title not in subsections:
                        continue
                    out.append({
                        "id": r.get("id"),
                        "keyword": kw,
                        "description": desc,
                        "section_id": sec_id,
                        "section_title": sec_title,
                        "subsection_id": sub_id,
                        "subsection_title": sub_title,
                        "nsfw": kw_nsfw,
                        "privacy_status": r.get("privacy_status") or "public",
                        "user_id": r.get("user_id"),
                    })
                out.sort(key=lambda x: (x["section_id"], x["subsection_id"], x["keyword"]))
                if limit is not None:
                    out = out[:limit]
                return _aio_web.json_response(out)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/filters")
        async def _aih_local_api_filters_route(request):
            """Liste des saved_filters (exclut les deleted)."""
            conn = None
            try:
                conn = _aih_local_conn()
                rows = _aih_store_mod.list_mirror(conn, "saved_filters", {})
                return _aio_web.json_response([_aih_local_decode_row(r) for r in rows])
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/elements-presets")
        async def _aih_local_api_elements_presets_route(request):
            """Liste des elements_presets."""
            conn = None
            try:
                conn = _aih_local_conn()
                rows = _aih_store_mod.list_mirror(conn, "elements_presets", {})
                return _aio_web.json_response([_aih_local_decode_row(r) for r in rows])
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/styles")
        async def _aih_local_api_styles_route(request):
            """Liste des styles."""
            conn = None
            try:
                conn = _aih_local_conn()
                rows = _aih_store_mod.list_mirror(conn, "styles", {})
                return _aio_web.json_response([_aih_local_decode_row(r) for r in rows])
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/prompts/templates")
        async def _aih_local_api_prompts_templates_route(request):
            """Liste des prompt_templates."""
            conn = None
            try:
                conn = _aih_local_conn()
                rows = _aih_store_mod.list_mirror(conn, "prompt_templates", {})
                return _aio_web.json_response([_aih_local_decode_row(r) for r in rows])
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # ── P5 : Écritures en attente / conflits (outbox + miroirs) ────
        # GET /aih/local/api/sync/outbox    → ops locales (pending/conflict/error)
        # GET /aih/local/api/sync/conflicts → lignes miroir en conflit
        # GET /aih/local/api/sync/retry     → flush manuel immédiat de l'outbox

        @_routes.get("/aih/local/api/sync/outbox")
        async def _aih_local_sync_outbox_route(request):
            """Liste les écritures locales en attente (outbox, max 200).

            Contrat : {"items": [{id, entity_type, entity_client_id, op, status,
            attempts, last_error, created_at, client_updated_at}], "count": N}.
            Lecture directe de la table outbox via get_conn (pas list_mirror,
            qui ne connaît pas cette table).
            """
            conn = None
            try:
                conn = _aih_local_conn()
                rows = conn.execute(
                    "SELECT id, entity_type, entity_client_id, op, status, "
                    "attempts, last_error, created_at, client_updated_at "
                    "FROM outbox ORDER BY created_at DESC, id DESC LIMIT 200"
                ).fetchall()
                items = [dict(r) for r in rows]
                return _aio_web.json_response({"items": items, "count": len(items)})
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/sync/conflicts")
        async def _aih_local_sync_conflicts_route(request):
            """Liste les lignes miroir en conflit (toutes tables, agrégées).

            Contrat : {"items": [{table, client_id, id, sync_state,
            updated_at}], "count": N}. Chaque table est traitée en try/except :
            une table absente ou sans colonnes n'arrête pas la route.
            """
            conn = None
            try:
                conn = _aih_local_conn()
                items = []
                tables = getattr(_aih_store_mod, "MIRROR_TABLES", ()) or ()
                for table in tables:
                    try:
                        rows = conn.execute(
                            f"SELECT client_id, id, sync_state, updated_at "
                            f"FROM {table} WHERE sync_state = 'conflict'"
                        ).fetchall()
                    except Exception:
                        continue  # table absente / schéma différent
                    for r in rows:
                        items.append({
                            "table": table,
                            "client_id": r["client_id"],
                            "id": r["id"],
                            "sync_state": r["sync_state"],
                            "updated_at": r["updated_at"],
                        })
                return _aio_web.json_response({"items": items, "count": len(items)})
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        @_routes.get("/aih/local/api/sync/retry")
        async def _aih_local_sync_retry_route(request):
            """Force un flush manuel immédiat de l'outbox (sync_engine).

            Appelle sync_engine.flush_outbox(api_url, api_key) dans un executor
            (le flush est synchrone/urllib → on ne bloque pas l'event loop).
            Retourne {"sent", "applied", "conflicts", "errors"} (+ auth/error).
            """
            try:
                if _aih_sync_mod is None:
                    return _aio_web.json_response(
                        {"error": "sync_engine indisponible"}, status=500
                    )
                api_url = api_key = None
                if hasattr(_aih_sync_mod, "_load_credentials"):
                    try:
                        api_url, api_key = _aih_sync_mod._load_credentials()
                    except Exception:
                        api_url = api_key = None
                if not api_url or not api_key:
                    return _aio_web.json_response(
                        {"error": "credentials absentes (api_key / api_url non configurés)"},
                        status=400,
                    )
                import asyncio as _aio
                import functools as _ft
                loop = _aio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    _ft.partial(_aih_sync_mod.flush_outbox, api_url, api_key, 200),
                )
                if not isinstance(result, dict):
                    result = {"sent": 0, "applied": 0, "conflicts": 0, "errors": 0}
                return _aio_web.json_response(result)
            except Exception as e:
                return _aio_web.json_response({"error": str(e)}, status=500)

        print("[AIH] Local status route registered: GET /aih/local/status")
        print("[AIH] Local sync routes registered: GET /aih/local/api/sync/outbox, GET /aih/local/api/sync/conflicts, GET /aih/local/api/sync/retry")
        print("[AIH] Music3 local routes registered: GET /aih/local/api/music3/manifest, GET /aih/local/api/music3/reference/*")
        print("[AIH] Local frontend routes registered: GET /aih/local/, GET /aih/local/css/*, GET /aih/local/js/*, GET /aih/local/favicon*")
        print("[AIH] Local proxy routes registered: GET /aih/local/api/sections, /aih/local/api/subsections, /aih/local/api/stats, /aih/local/api/keywords, /aih/local/api/filters, /aih/local/api/elements-presets, /aih/local/api/styles, /aih/local/api/prompts/templates")
        print("[AIH] Local semantic routes registered: GET /aih/local/api/search/semantic, GET /aih/local/api/embeddings/status, POST /aih/local/api/embeddings/build, GET /aih/local/api/embeddings/progress")
else:
    # Si les routes ne sont pas enregistrees, on ne fait rien de plus
    # (l'item "Update" du menu ne fonctionnera pas, mais l'extension
    # reste chargee pour les nodes)
    pass

# ── Démarrage du moteur de sync (mode local) ───────────────────────
# Thread daemon lancé en fin de chargement du module (le serveur aiohttp
# est déjà initialisé à ce stade dans ComfyUI). Non bloquant : en cas
# d'échec on logge simplement un warning, l'extension reste chargée.
if _aih_sync_mod is not None:
    try:
        _aih_sync_mod.start_sync_engine()
        logging.info("[AIH] Sync engine started (local store mode)")
    except Exception as _e:
        logging.warning(f"[AIH] Sync engine start failed: {_e}")


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
