"""
FR.IA Custom Nodes Manager — Détection et installation de custom nodes.

Endpoints ajoutés au serveur ComfyUI (aiohttp) :
  GET  /fria/custom-nodes          → liste des nodes installés + URLs git
  POST /fria/custom-nodes/install  → git clone d'un node manquant

Usage côté JS (fria_workflow_share.js) :
  - Au partage : fetch('/fria/custom-nodes') pour enrichir required_nodes avec les URLs
  - À l'install : fetch('/fria/custom-nodes/install', {url, name}) pour cloner
"""

import os
import json
import logging
import subprocess
import configparser
import re

try:
    import folder_paths
    _BASE_DIR = os.path.dirname(folder_paths.__file__)
    _CUSTOM_NODES_DIR = os.path.join(_BASE_DIR, "custom_nodes")
except Exception:
    _CUSTOM_NODES_DIR = None


def _read_git_url(node_dir):
    """Lit l'URL du remote origin depuis .git/config."""
    git_config = os.path.join(node_dir, ".git", "config")
    if not os.path.isfile(git_config):
        return ""
    try:
        config = configparser.ConfigParser()
        config.read(git_config)
        if 'remote "origin"' in config:
            return config['remote "origin"'].get('url', '')
    except Exception:
        pass
    return ""


def _get_node_types_from_sys_modules():
    """
    Parcourt sys.modules pour trouver les modules charges dans
    custom_nodes/ qui ont un attribut NODE_CLASS_MAPPINGS.
    Retourne {folder_name: [node_type1, ...]}.
    Cette approche ne depend pas de inspect.getmodule() qui echoue
    sur certaines classes ComfyUI (built-in, wrappers, etc.).
    """
    result = {}
    try:
        import sys as _sys

        for mod_name, mod in _sys.modules.items():
            try:
                if not hasattr(mod, 'NODE_CLASS_MAPPINGS'):
                    continue
                filepath = getattr(mod, '__file__', None)
                if not filepath:
                    continue
                # Normaliser
                filepath = os.path.normpath(filepath)
                # Chercher custom_nodes dans le chemin
                parts = filepath.split('custom_nodes')
                if len(parts) < 2:
                    continue
                # Le nom du dossier
                sub = parts[1].lstrip(os.sep).split(os.sep)[0]
                if not sub or sub.startswith('.'):
                    continue
                # Extraire les types
                mapping = mod.NODE_CLASS_MAPPINGS
                if not isinstance(mapping, dict):
                    continue
                if sub not in result:
                    result[sub] = []
                for node_type in mapping.keys():
                    if isinstance(node_type, str) and node_type not in result[sub]:
                        result[sub].append(node_type)
            except Exception:
                continue
    except Exception as e:
        logging.warning(f"[FR.IA] _get_node_types_from_sys_modules error: {e}")

    return result


def _get_global_node_type_mapping():
    """
    Utilise sys.modules pour trouver les node types de chaque pack
    custom_nodes. Si ComfyUI n'est pas disponible, retourne {}.
    Le fallback parsing __init__.py est utilise si sys.modules ne contient pas
    le pack recherche.
    """
    import sys as _sys2
    # Verifier si ComfyUI est charge (presence de key modules)
    is_comfy = any(m.startswith('ComfyUI') or 'nodes' in m or 'custom_nodes' in m
                   for m in _sys2.modules)
    if not is_comfy:
        return {}
    return _get_node_types_from_sys_modules()


# Cache pour le mapping
_NODE_TYPE_MAP_CACHE = None

def _get_node_type_map():
    global _NODE_TYPE_MAP_CACHE
    if _NODE_TYPE_MAP_CACHE is None:
        _NODE_TYPE_MAP_CACHE = _get_global_node_type_mapping()
    return _NODE_TYPE_MAP_CACHE


def _extract_node_types(node_dir):
    """Retourne les node types fournis par ce dossier custom_nodes.
    Priorite 1: NODE_CLASS_MAPPINGS global (sys.modules scan).
    Priorite 2: ast.parse() du __init__.py (fiable, gere les dicts imbriques).
    """
    folder_name = os.path.basename(node_dir)
    type_map = _get_node_type_map()

    if folder_name in type_map:
        return type_map[folder_name]

    # Fallback: parsing AST du __init__.py
    init_file = os.path.join(node_dir, "__init__.py")
    if not os.path.isfile(init_file):
        return []
    try:
        import ast
        with open(init_file, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == 'NODE_CLASS_MAPPINGS':
                        if isinstance(node.value, ast.Dict):
                            keys = []
                            for k in node.value.keys:
                                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                    keys.append(k.value)
                            return keys
        return []
    except Exception as e:
        logging.warning(f"[FR.IA] AST parse failed for {folder_name}: {e}")
        return []
    except Exception:
        return []


def _get_installed_custom_nodes():
    """Scanne custom_nodes/ et retourne [{name, git_url, has_git}]."""
    if not _CUSTOM_NODES_DIR or not os.path.isdir(_CUSTOM_NODES_DIR):
        return []

    results = []
    for name in os.listdir(_CUSTOM_NODES_DIR):
        node_dir = os.path.join(_CUSTOM_NODES_DIR, name)
        if not os.path.isdir(node_dir) or name.startswith('.'):
            continue
        git_url = _read_git_url(node_dir)
        has_git = os.path.isdir(os.path.join(node_dir, ".git"))
        if has_git or git_url:
            node_types = _extract_node_types(node_dir)
            results.append({
                "name": name,
                "git_url": git_url,
                "has_git": has_git,
                "node_types": node_types,
            })
    return results


def _install_custom_node(git_url, name=""):
    """Clone un repo git dans custom_nodes/. Retourne {success, message}."""
    if not _CUSTOM_NODES_DIR:
        return {"success": False, "message": "custom_nodes directory not found"}
    if not git_url:
        return {"success": False, "message": "git_url required"}

    # Déduire le nom depuis l'URL si non fourni
    if not name:
        name = git_url.rstrip('/').split('/')[-1]
        if name.endswith('.git'):
            name = name[:-4]

    target = os.path.join(_CUSTOM_NODES_DIR, name)
    if os.path.isdir(target):
        return {"success": False, "message": f"Node '{name}' already installed"}

    try:
        result = subprocess.run(
            ['git', 'clone', git_url, target],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return {"success": True, "message": f"Installed {name}", "path": target}
        else:
            return {"success": False, "message": f"git clone failed: {result.stderr[:200]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "git clone timed out (120s)"}
    except Exception as e:
        return {"success": False, "message": str(e)}
