"""
FR.IA Model Manager — List, upload et download de models/loras.

Utilise folder_paths (ComfyUI) pour connaître les chemins des models.
Upload les fichiers directement depuis le filesystem Python (pas de file picker navigateur).

Endpoints ajoutés au serveur ComfyUI (aiohttp) :
  GET  /fria/models/list           → liste des models/loras locaux + tailles
  POST /fria/models/upload         → upload un model vers le serveur FR.IA (chunked)
  POST /fria/models/download       → download un model depuis FR.IA → sauvegarde locale

L'upload se fait en streaming : le Python lit le fichier par chunks et les envoie
au backend FR.IA via /api/files/init + /api/files/chunk + /api/files/complete.
Le fingerprint (hash partiel) est calculé côté Python pour la déduplication.
"""

import os
import json
import logging
import hashlib
import subprocess
import time

try:
    import folder_paths
    _HAS_FOLDER_PATHS = True
except Exception:
    _HAS_FOLDER_PATHS = False

# Chunk size pour l'upload (doit correspondre au backend)
CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB

# Progression des uploads en cours : filepath → {chunk, total, speed_mbs, start}
_upload_progress = {}


# Toutes les categories de models connues par ComfyUI
_ALL_MODEL_CATEGORIES = [
    'checkpoints', 'loras', 'vae', 'clip', 'clip_vision', 'controlnet',
    'unet', 'unet_gguf', 'upscale_models', 'gligen', 'hypernetworks',
    'text_encoders', 'style_models', 'diffusion_models', 'configs',
    'embeddings', 'bbxe/models',
]

def _get_model_dirs():
    """Retourne {type: [paths]} pour toutes les categories de models ComfyUI.
    Fallback : scanne les dossiers courants si folder_paths est vide ou indisponible."""
    result = {}
    if _HAS_FOLDER_PATHS:
        for cat in _ALL_MODEL_CATEGORIES:
            try:
                paths = folder_paths.get_folder_paths(cat)
                if paths:
                    result[cat] = paths
            except Exception:
                pass

    # Fallback : si rien trouve via folder_paths, on scanne les dossiers courants
    if not result:
        # Chercher ComfyUI/models/ et ses sous-dossiers
        for base_dir in [
            "ComfyUI/models",
            os.path.expanduser("~/ComfyUI/models"),
            "../ComfyUI/models",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "models"),
        ]:
            if os.path.isdir(base_dir):
                for name in os.listdir(base_dir):
                    sub = os.path.join(base_dir, name)
                    if os.path.isdir(sub):
                        result[name] = [sub]
                break
    return result


def _list_models_in_dirs(dirs, extensions=None):
    """Liste les fichiers dans une liste de dossiers (scan recursif 1 niveau)."""
    if extensions is None:
        extensions = ['.safetensors', '.ckpt', '.pt', '.pth', '.gguf', '.bin', '.t5', '.fp16', '.fp8', '.bf16']
    results = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            full = os.path.join(d, name)
            if os.path.isfile(full):
                ext = os.path.splitext(name)[1].lower()
                if ext in extensions:
                    results.append({
                        'name': name,
                        'path': full,
                        'size': os.path.getsize(full),
                    })
            elif os.path.isdir(full):
                # Scan 1 niveau de sous-dossier (ex: gguf/, lora/, etc.)
                for sub_name in os.listdir(full):
                    sub_full = os.path.join(full, sub_name)
                    if os.path.isfile(sub_full):
                        ext = os.path.splitext(sub_name)[1].lower()
                        if ext in extensions:
                            results.append({
                                'name': sub_name,
                                'path': sub_full,
                                'size': os.path.getsize(sub_full),
                            })
    return results


def list_local_models():
    """Liste tous les models locaux dans toutes les categories ComfyUI."""
    dirs = _get_model_dirs()
    result = {}
    for cat, cat_dirs in dirs.items():
        result[cat] = _list_models_in_dirs(cat_dirs)
    return result


def _compute_fingerprint(filepath):
    """Calcule le fingerprint (hash premier/dernier Mo + taille)."""
    try:
        size = os.path.getsize(filepath)
        head_size = min(1024 * 1024, size)
        with open(filepath, 'rb') as f:
            head = f.read(head_size)
            f.seek(max(0, size - head_size))
            tail = f.read(head_size)
        head_hash = hashlib.sha256(head).hexdigest()
        tail_hash = hashlib.sha256(tail).hexdigest()
        return {'size': size, 'head': head_hash, 'tail': tail_hash}
    except Exception as e:
        logging.warning(f"[FR.IA] Fingerprint failed: {e}")
        return None


def _get_fria_credentials():
    """Lit les credentials FR.IA pour connaître l'URL du serveur + API key."""
    try:
        from . import _credentials
        return _credentials.get_api_url(), _credentials.get_api_key()
    except Exception:
        try:
            import folder_paths
            user_dir = folder_paths.get_user_directory()
            cred_file = os.path.join(user_dir, "default", "fria_credentials.json")
            with open(cred_file, 'r') as f:
                data = json.load(f)
            base = (data.get("server_url") or "https://kw.holaf.fr").rstrip("/")
            return base + "/api", data.get("api_key", "")
        except Exception:
            return "https://kw.holaf.fr/api", ""


def upload_model_to_server(filepath, file_type="model", on_progress=None):
    """
    Upload un fichier model vers le serveur FR.IA via chunked upload.
    Retourne {success, upload_id, file_path} ou {success: False, error}.
    """
    import requests

    api_url, api_key = _get_fria_credentials()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    filename = os.path.basename(filepath)
    size = os.path.getsize(filepath)

    # Headers pour JSON (check/init/complete)
    # Headers pour multipart (chunk) — sans Content-Type, requests le met automatiquement
    auth_headers = {}
    if api_key:
        auth_headers["Authorization"] = f"Bearer {api_key}"

    # 1. Fingerprint pour déduplication
    fp = _compute_fingerprint(filepath)
    if fp:
        try:
            resp = requests.post(f"{api_url}/files/check", json={
                'size': fp['size'], 'head': fp['head'], 'tail': fp['tail']
            }, headers={**auth_headers, 'Content-Type': 'application/json'}, timeout=10)
            if resp.ok:
                data = resp.json()
                if data.get('exists'):
                    logging.info(f"[FR.IA] Model {filename} already on server, skipping upload")
                    return {'success': True, 'upload_id': data['upload_id'],
                            'file_path': data['file_path'], 'deduplicated': True}
        except Exception as e:
            logging.warning(f"[FR.IA] Fingerprint check failed: {e}")

    # 2. Init upload — mapper le type vers 'model' pour compatibilite backend
    backend_type = 'model' if file_type in ('checkpoint','lora','vae','clip','clip_vision','controlnet','unet','unet_gguf','upscale','gligen','hypernetwork','text_encoder','style_model') else file_type
    try:
        resp = requests.post(f"{api_url}/files/init", json={
            'filename': filename, 'size': size, 'type': backend_type
        }, headers={**auth_headers, 'Content-Type': 'application/json'}, timeout=30)
        if not resp.ok:
            try:
                err = resp.json().get('error', resp.text)
            except Exception:
                err = resp.text[:300] or f'HTTP {resp.status_code} (body empty)'
            return {'success': False, 'error': f'Init failed: HTTP {resp.status_code} {err}'}
        init_data = resp.json()
    except Exception as e:
        return {'success': False, 'error': f'Init failed: {e}'}

    upload_id = init_data['upload_id']
    chunk_size = init_data['chunk_size']
    total_chunks = init_data['total_chunks']

    # 3. Upload chunks
    _upload_progress[filepath] = {'chunk': 0, 'total': total_chunks, 'speed_mbs': 0.0, 'start': time.time(), 'last_chunk_time': time.time()}
    try:
        with open(filepath, 'rb') as f:
            for i in range(total_chunks):
                chunk = f.read(chunk_size)
                resp = requests.post(f"{api_url}/files/chunk", data={
                    'upload_id': upload_id,
                    'chunk_index': str(i),
                }, files={'data': (filename, chunk)}, headers=auth_headers, timeout=300)
                if not resp.ok:
                    _upload_progress.pop(filepath, None)
                    return {'success': False, 'error': f'Chunk {i} failed: HTTP {resp.status_code} {resp.text[:200]}'}
                # Mettre a jour la progression (vitesse du dernier chunk)
                now = time.time()
                chunk_elapsed = now - _upload_progress[filepath].get('last_chunk_time', now)
                chunk_mb = chunk_size / 1048576
                speed = chunk_mb / chunk_elapsed if chunk_elapsed > 0 else 0
                _upload_progress[filepath].update({'chunk': i + 1, 'speed_mbs': round(speed, 1), 'last_chunk_time': now})
                if on_progress:
                    on_progress(i + 1, total_chunks)
    except Exception as e:
        _upload_progress.pop(filepath, None)
        return {'success': False, 'error': f'Chunk upload failed: {e}'}

    # 4. Complete
    _upload_progress.pop(filepath, None)
    try:
        complete_data = {'upload_id': upload_id}
        if fp:
            complete_data['fingerprint_head'] = fp['head']
            complete_data['fingerprint_tail'] = fp['tail']
        resp = requests.post(f"{api_url}/files/complete", json=complete_data,
                             headers={**auth_headers, 'Content-Type': 'application/json'}, timeout=60)
        if not resp.ok:
            err = resp.json().get('error', resp.text)
            return {'success': False, 'error': f'Complete failed: {err}'}
        result = resp.json()
        return {'success': True, 'upload_id': upload_id,
                'file_path': result.get('file_path', '')}
    except Exception as e:
        return {'success': False, 'error': f'Complete failed: {e}'}


def get_upload_progress(filepath):
    """Retourne la progression d'un upload en cours."""
    p = _upload_progress.get(filepath)
    if not p:
        return None
    pct = round(p['chunk'] / p['total'] * 100, 1) if p['total'] > 0 else 0
    return {
        'chunk': p['chunk'],
        'total': p['total'],
        'percent': pct,
        'speed_mbs': p['speed_mbs'],
    }


def download_model_from_server(upload_id, filename, file_type="model"):
    """
    Download un model depuis le serveur FR.IA et le sauvegarde dans le dossier local.
    Retourne {success, path} ou {success: False, error}.
    """
    import requests

    api_url, api_key = _get_fria_credentials()
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Déterminer le dossier de destination selon le type
    dirs = _get_model_dirs()

    # Mapper les categories de detection vers les dossiers ComfyUI
    type_to_cat = {
        'checkpoint': 'checkpoints',
        'lora': 'loras',
        'vae': 'vae',
        'clip': 'clip',
        'clip_vision': 'clip_vision',
        'controlnet': 'controlnet',
        'unet': 'unet',
        'unet_gguf': 'unet_gguf',
        'upscale': 'upscale_models',
        'gligen': 'gligen',
        'hypernetwork': 'hypernetworks',
        'text_encoder': 'text_encoders',
        'style_model': 'style_models',
        'model': 'checkpoints',  # fallback
    }

    cat = type_to_cat.get(file_type, 'checkpoints')
    dest_dirs = dirs.get(cat, dirs.get('checkpoints', []))

    if not dest_dirs:
        return {'success': False, 'error': f'No model directory for type {file_type}'}

    dest_dir = dest_dirs[0]
    dest_path = os.path.join(dest_dir, filename)

    try:
        resp = requests.get(f"{api_url}/files/{upload_id}/download",
                           headers=auth_headers, stream=True, timeout=600)
        if not resp.ok:
            return {'success': False, 'error': f'HTTP {resp.status_code}'}

        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

        logging.info(f"[FR.IA] Downloaded {filename} → {dest_path}")
        return {'success': True, 'path': dest_path}
    except Exception as e:
        return {'success': False, 'error': str(e)}
