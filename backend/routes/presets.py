"""Routes presets for AI-Helper backend."""

import ipaddress
import logging
import os
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from context import *

logger = logging.getLogger('ai_helper')


# ── Protection anti-SSRF (appels LLM sortants) ───────────────────────

_PRIVATE_NETWORKS = (
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),   # CGNAT
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),  # link-local
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('::/128'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('::ffff:0:0/96'),   # IPv4-mapped IPv6
    ipaddress.ip_network('fc00::/7'),        # ULA
    ipaddress.ip_network('fe80::/10'),       # link-local IPv6
)


_MAX_REDIRECTS = 3


def _is_private_ip(ip_str):
    """True si l'adresse est privée/loopback/link-local (ou non parsable)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # non parsable → refuser par défaut
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 → vérifier la forme IPv4
    return any(ip in net for net in _PRIVATE_NETWORKS)


def _opt_in_private_hosts():
    """Hôtes privés explicitement autorisés (LLM locaux type ollama).

    Variable d'environnement ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` : liste de
    hostnames/IP séparés par des virgules. Toujours vide par défaut.
    """
    raw = os.environ.get('AIH_ALLOW_PRIVATE_LLM_HOSTS', '')
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def _host_allowed(host):
    """True si l'hôte peut être ciblé par un appel LLM sortant.

    Vérifie : IP littérale non privée, ou hostname dont TOUTES les IP
    résolues sont publiques (anti DNS-rebinding). Un hôte listé dans
    ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` est toujours accepté (opt-in admin).
    """
    host = host.lower()
    if host in _opt_in_private_hosts():
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return not _is_private_ip(host)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {info[4][0] for info in infos}
    return bool(ips) and all(not _is_private_ip(ip) for ip in ips)


def _validate_llm_base_url(base_url):
    """Valide un base_url d'API LLM avant appel sortant (anti-SSRF).

    Retourne un message d'erreur (str) ou ``None`` si l'URL est acceptée.
    Règles :
    - https:// uniquement (http:// si hôte privé opt-in ou AIH_ALLOW_HTTP_LLM=1)
    - pas de credentials embarquées (user:pass@)
    - port standard (443/80), sauf hôte privé opt-in
    - hôte : pas d'IP privée/loopback/link-local, ni de DNS rebinding
      (toutes les IP résolues sont vérifiées)
    """
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return "URL invalide"
    if parsed.scheme not in ('http', 'https'):
        return "URL non supportée (https requis)"
    if parsed.username or parsed.password:
        return "Credentials embarquées interdites"
    host = (parsed.hostname or '').lower()
    if not host:
        return "Hôte manquant"
    opt_in = host in _opt_in_private_hosts()
    if parsed.scheme == 'http' and not opt_in and os.environ.get('AIH_ALLOW_HTTP_LLM', '') != '1':
        return "URL non supportée (https requis)"
    if not opt_in:
        default_port = 443 if parsed.scheme == 'https' else 80
        if parsed.port is not None and parsed.port != default_port:
            return "Port non autorisé"
        if not _host_allowed(host):
            return "Hôte refusé (adresse privée ou introuvable)"
    return None


def _safe_llm_get(url, headers, timeout=(5, 10)):
    """GET HTTP avec validation anti-SSRF, redirection par redirection.

    Lève ``ValueError`` (message générique, sans détail réseau) si un hôte
    est refusé ou si le nombre maximal de redirections est dépassé.
    """
    import requests
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        err = _validate_llm_base_url(current)
        if err:
            raise ValueError(f"Hôte refusé : {err}")
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get('Location')
            if not loc:
                raise ValueError("Redirection sans destination")
            current = urljoin(current, loc)
            continue
        return resp
    raise ValueError("Trop de redirections")


def _safe_llm_post(url, json_payload, headers, timeout=(5, 10)):
    """POST HTTP avec validation anti-SSRF (pas de suivi de redirection).

    Mêmes règles que ``_safe_llm_get`` pour l'hôte (IP privée interdites sauf
    opt-in ``AIH_ALLOW_PRIVATE_LLM_HOSTS``, anti DNS-rebinding, https requis).
    Une redirection sur un POST est traitée comme un échec (conservateur).
    """
    import requests
    err = _validate_llm_base_url(url)
    if err:
        raise ValueError(f"Hôte refusé : {err}")
    return requests.post(url, json=json_payload, headers=headers,
                         timeout=timeout, allow_redirects=False)


# Champs de fenêtre de contexte exposés par les fournisseurs OpenAI-compat.
#   context_length       : OpenRouter, Together, …
#   max_model_len        : vLLM
#   context_window       : LiteLLM, certains proxies
#   max_context_length   : ancien format (vLLM obsolète, …)
_CONTEXT_FIELDS = ('context_length', 'max_model_len', 'context_window', 'max_context_length')


# Borne haute raisonnable d'un réglage manuel de fenêtre de contexte.
_CONTEXT_LENGTH_MAX = 10_000_000


def _context_from_entry(m):
    """Extrait la fenêtre de contexte d'une entrée /models (4 champs connus).

    Returns:
        int|None: la fenêtre (> 0) ou None si absente/invalide.
    """
    for field in _CONTEXT_FIELDS:
        value = m.get(field)
        if value is None:
            continue
        try:
            ctx = int(value)
        except (TypeError, ValueError):
            continue
        if ctx > 0:
            return ctx
    return None


def _context_from_model_entry(models, model):
    """Extrait la fenêtre de contexte d'une liste de modèles (/models).

    Args:
        models: liste brute renvoyée par le fournisseur.
        model: nom du modèle ciblé (correspondance exacte puis sous-chaîne).

    Returns:
        int|None: la fenêtre (> 0) ou None si absente/invalide.
    """
    target = (model or '').strip().lower()
    if not target:
        return None
    chosen = None
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = (m.get('id') or m.get('name') or '').strip().lower()
        if mid == target:
            chosen = m
            break
        if chosen is None and target in mid:
            chosen = m
    if not isinstance(chosen, dict):
        return None
    return _context_from_entry(chosen)


def _parse_models(data):
    """Normalise la réponse /models d'un fournisseur OpenAI-compatible.

    Le contrat ``{id, name, owned_by}`` est conservé ; si le fournisseur
    expose la fenêtre de contexte du modèle (OpenRouter ``context_length``,
    vLLM ``max_model_len``, ``context_window`` ou ``max_context_length``),
    elle est ajoutée sous la clé ``context_length`` (champ optionnel : rien
    n'est remplacé, les consommateurs existants ne sont pas cassés).
    """
    models = []
    raw = data.get('data', data.get('models', []))
    for m in raw:
        if isinstance(m, dict):
            entry = {'id': m.get('id', ''), 'name': m.get('name', m.get('id', '')), 'owned_by': m.get('owned_by', '')}
            ctx = _context_from_entry(m)
            if ctx is not None:
                entry['context_length'] = ctx
            models.append(entry)
        elif isinstance(m, str):
            models.append({'id': m, 'name': m, 'owned_by': ''})
    return models


def _parse_context_length_input(value):
    """Valide le champ ``context_length`` fourni par un client (POST/PUT).

    Args:
        value: valeur JSON brute du champ (int, str, None, bool, …).

    Returns:
        tuple: ``(context_length, source, checked_at, error)`` :
        - ``(None, None, None, None)``        → remise en auto (null / '' / absent) ;
        - ``(N, 'manual', iso_utc, None)``    → réglage manuel valide ;
        - ``(None, None, None, message)``     → valeur invalide (→ 400).
    """
    if value is None:
        return None, None, None, None
    if isinstance(value, bool):
        return None, None, None, "context_length doit être un entier strictement positif (ex. 8192)"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == '':
            return None, None, None, None
        try:
            value = int(stripped)
        except ValueError:
            return None, None, None, "context_length doit être un entier strictement positif (ex. 8192)"
    if isinstance(value, float):
        if not value.is_integer():
            return None, None, None, "context_length doit être un entier strictement positif (ex. 8192)"
        value = int(value)
    if not isinstance(value, int):
        return None, None, None, "context_length doit être un entier strictement positif (ex. 8192)"
    if value <= 0 or value > _CONTEXT_LENGTH_MAX:
        return None, None, None, f"context_length doit être un entier entre 1 et {_CONTEXT_LENGTH_MAX}"
    return value, 'manual', datetime.now(timezone.utc).isoformat(), None


# ── Presets ─────────────────────────────────────────────────────────

@app.route('/api/presets', methods=['GET', 'POST'])
def presets():
    """Liste les presets IA visibles (GET) ou crée un nouveau preset (POST).

    Returns:
        flask.Response: JSON listant les presets (GET) ou l'ID du preset créé (POST).
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'GET':
        try:
            rows = cur.execute("""
                SELECT p.*, u.username, u.display_name
                FROM ai_presets p
                LEFT JOIN users u ON u.id = p.user_id
                WHERE p.is_global = 1 OR p.user_id = ?
                ORDER BY p.is_global DESC, p.name
            """, (user_id,)).fetchall()
        except Exception as e:
            conn.close()
            return jsonify({'error': f'DB error: {e}'}), 500
        conn.close()
        result = []
        for r in rows:
            result.append({
                'id': r['id'],
                'user_id': r['user_id'],
                'name': r['name'],
                'engine': r['engine'],
                'base_url': r['base_url'],
                'model': r['model'],
                'is_global': bool(r['is_global']),
                'is_client_side': bool(_row_get(r, 'is_client_side', 0)),
                'owner_name': r['display_name'] or r['username'] or '',
                'created_at': r['created_at'],
                # Fenêtre de contexte (None = détection auto à l'exécution).
                'context_length': _row_get(r, 'context_length', None),
                'context_source': _row_get(r, 'context_source', None),
                'context_checked_at': _row_get(r, 'context_checked_at', None),
            })
        return jsonify(result)

    # POST : creation (admin pour global, tout le monde pour perso)
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    base_url = data.get('base_url', '').strip()
    api_key = data.get('api_key', '').strip()
    model = data.get('model', '').strip()
    is_global = int(data.get('is_global', 0))
    is_client_side = int(data.get('is_client_side', 0))

    if not name or not base_url:
        conn.close()
        return jsonify({'error': 'Nom et URL requis'}), 400

    # context_length (optionnel) : entier > 0 → réglage manuel ; null/absent/'' → auto.
    ctx_len, ctx_src, ctx_at, ctx_err = _parse_context_length_input(data.get('context_length'))
    if ctx_err:
        conn.close()
        return jsonify({'error': ctx_err}), 400

    if is_global:
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard

    enc = encrypt_api_key(api_key)
    cur.execute(
        "INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, model, is_global, is_client_side, context_length, context_source, context_checked_at) VALUES (?, ?, 'openai', ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id if not is_global else None, name, base_url, enc, model, is_global, is_client_side, ctx_len, ctx_src, ctx_at)
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return jsonify({'id': pid, 'name': name}), 201


@app.route('/api/presets/<int:preset_id>', methods=['GET', 'PUT', 'DELETE'])
def single_preset(preset_id):
    """Lit (GET), met à jour (PUT) ou supprime (DELETE) un preset IA.

    Args:
        preset_id: Identifiant du preset à consulter, modifier ou supprimer.

    Returns:
        flask.Response: JSON du preset (GET) ou confirmation de l'opération
        (PUT/DELETE) ou une erreur 403/404.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    if request.method == 'GET':
        # Lecture : preset global visible par tous, personnel par son
        # propriétaire (sinon 404 anti-énumération, règle de list-models).
        if not row['is_global'] and row['user_id'] != user_id:
            conn.close()
            return jsonify({'error': 'Not found'}), 404
        result = {
            'id': row['id'],
            'user_id': row['user_id'],
            'name': row['name'],
            'engine': row['engine'],
            'base_url': row['base_url'],
            'model': row['model'],
            'is_global': bool(row['is_global']),
            'is_client_side': bool(_row_get(row, 'is_client_side', 0)),
            'created_at': row['created_at'],
            'context_length': _row_get(row, 'context_length', None),
            'context_source': _row_get(row, 'context_source', None),
            'context_checked_at': _row_get(row, 'context_checked_at', None),
        }
        conn.close()
        return jsonify(result)

    # Verifier propriete : global = admin only, perso = owner or admin
    if row['is_global']:
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard
    elif row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    if request.method == 'DELETE':
        cur.execute("UPDATE generated_prompts SET preset_id = NULL WHERE preset_id = ?", (preset_id,))
        cur.execute("DELETE FROM ai_presets WHERE id = ?", (preset_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})

    # PUT
    data = request.get_json() or {}

    # context_length (optionnel) : entier > 0 → réglage manuel ; null / ''
    # explicites → remise en auto ; clé ABSENTE → valeur existante inchangée
    # (les clients existants qui ne renvoient pas ce champ ne perdent rien).
    context_update = None
    if 'context_length' in data:
        ctx_len, ctx_src, ctx_at, ctx_err = _parse_context_length_input(data.get('context_length'))
        if ctx_err:
            conn.close()
            return jsonify({'error': ctx_err}), 400
        # Valeur reçue IDENTIQUE à la valeur stockée : ne PAS promouvoir en
        # 'manual'. Les fronts pré-remplissent le champ et le renvoient tel quel
        # à chaque sauvegarde : sans ce garde, un simple renommage figerait une
        # source auto/family (plus jamais re-sondée). Une valeur DIFFÉRENTE →
        # manual ; null/vide → auto (inchangé).
        stored_len = _row_get(row, 'context_length', None)
        same_as_stored = ctx_len is not None and stored_len is not None and int(stored_len) == ctx_len
        if same_as_stored:
            context_update = (
                int(stored_len),
                _row_get(row, 'context_source', None),
                _row_get(row, 'context_checked_at', None),
            )
        else:
            context_update = (ctx_len, ctx_src, ctx_at)

    api_key_val = data.get('api_key', None)
    if api_key_val is not None:
        enc = encrypt_api_key(api_key_val.strip()) if api_key_val.strip() else ''
    else:
        enc = row['api_key_encrypted']  # garder l'ancienne

    # Si on tente de passer en global (ou rester global), il faut etre admin
    new_is_global = int(data.get('is_global', row['is_global']))
    if new_is_global and not row['is_global']:
        # Transition perso -> global : admin only
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard
    if new_is_global != int(row['is_global']):
        # Changement d'etat is_global : admin only dans tous les cas
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard

    if context_update is not None:
        cur.execute("""
            UPDATE ai_presets
            SET name = ?, base_url = ?, api_key_encrypted = ?, model = ?, is_client_side = ?, is_global = ?,
                context_length = ?, context_source = ?, context_checked_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            data.get('name', row['name']),
            data.get('base_url', row['base_url']),
            enc,
            data.get('model', row['model']),
            int(data.get('is_client_side', _row_get(row, 'is_client_side', 0))),
            new_is_global,
            context_update[0], context_update[1], context_update[2],
            preset_id
        ))
    else:
        cur.execute("""
            UPDATE ai_presets
            SET name = ?, base_url = ?, api_key_encrypted = ?, model = ?, is_client_side = ?, is_global = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            data.get('name', row['name']),
            data.get('base_url', row['base_url']),
            enc,
            data.get('model', row['model']),
            int(data.get('is_client_side', _row_get(row, 'is_client_side', 0))),
            new_is_global,
            preset_id
        ))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/presets/<int:preset_id>/duplicate', methods=['POST'])
def duplicate_preset(preset_id):
    """Duplique un preset IA existant vers un preset personnel.

    Args:
        preset_id: Identifiant du preset à dupliquer.

    Returns:
        flask.Response: JSON avec l'ID du nouveau preset créé.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    # Seulement les globaux ou ses propres presets peuvent être dupliques
    if not row['is_global'] and row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ai_presets (user_id, name, engine, base_url, api_key_encrypted, model, is_global, is_client_side, context_length, context_source, context_checked_at)
        VALUES (?, ? || ' (copie)', ?, ?, ?, ?, 0, ?, ?, ?, ?)
    """, (user_id, row['name'], row['engine'], row['base_url'], row['api_key_encrypted'], row['model'], _row_get(row, 'is_client_side', 0),
          _row_get(row, 'context_length', None), _row_get(row, 'context_source', None), _row_get(row, 'context_checked_at', None)))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return jsonify({'id': pid, 'name': row['name'] + ' (copie)'}), 201


@app.route('/api/presets/<int:preset_id>/models', methods=['GET'])
def list_preset_models(preset_id):
    """Liste les modèles disponibles via l'API d'un preset IA.

    Args:
        preset_id: Identifiant du preset dont on veut lister les modèles.

    Returns:
        flask.Response: JSON listant les modèles ou une erreur 502 si l'API est injoignable.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    # C5 : propriete obligatoire — preset personnel de l'utilisateur ou global.
    # Sinon, un preset prive d'autrui serait sonde via {base_url}/models avec
    # sa cle API dechiffree. Un preset inaccessible = preset inexistant (404)
    # pour eviter l'enumeration d'ids.
    row = conn.execute(
        "SELECT * FROM ai_presets WHERE id = ? AND (user_id = ? OR is_global = 1)",
        (preset_id, user_id)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    base_url = row['base_url'].rstrip('/')
    api_key = decrypt_api_key(row['api_key_encrypted'])
    conn.close()

    err = _validate_llm_base_url(base_url)
    if err:
        logger.warning("[presets] Appel LLM refusé (preset %s) : %s", preset_id, err)
        return jsonify({'error': 'Impossible de lister les modeles : fournisseur inaccessible'}), 502

    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        r = _safe_llm_get(f'{base_url}/models', headers=headers)
        r.raise_for_status()
        return jsonify(_parse_models(r.json()))
    except Exception as e:
        logger.warning("[presets] Erreur liste modèles (preset %s) : %s", preset_id, e)
        return jsonify({'error': 'Impossible de lister les modeles : fournisseur inaccessible'}), 502


@app.route('/api/presets/list-models', methods=['POST'])
def list_models_temp():
    """Endpoint temporaire pour lister les modeles sans preset enregistre."""
    guard = _login_required()
    if guard: return guard
    data = request.get_json() or {}
    base_url = (data.get('base_url') or '').rstrip('/')
    api_key = (data.get('api_key') or '').strip()
    if not base_url:
        return jsonify({'error': 'URL requise'}), 400

    err = _validate_llm_base_url(base_url)
    if err:
        logger.warning("[presets] Appel LLM refusé (list-models) : %s", err)
        return jsonify({'error': 'Impossible de lister les modeles : fournisseur inaccessible'}), 502

    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        r = _safe_llm_get(f'{base_url}/models', headers=headers)
        r.raise_for_status()
        return jsonify(_parse_models(r.json()))
    except Exception as e:
        logger.warning("[presets] Erreur liste modèles (list-models) : %s", e)
        return jsonify({'error': 'Impossible de lister les modeles : fournisseur inaccessible'}), 502




@app.route('/api/presets/<int:preset_id>/detect-context', methods=['POST'])
def detect_preset_context(preset_id):
    """Sonde la fenêtre de contexte du modèle d'un preset (bouton « Détecter »).

    Enchaîne les sondes, dans l'ordre :
      (a) API OpenAI-compat : GET {base_url}/models — context_length,
          max_model_len, context_window, max_context_length ;
      (b) Ollama : POST {base_url}/api/show (model_info['*.context_length']) —
          même logique que ``_get_model_context`` (routes/enhance.py) ;
      (c) llama.cpp : GET {base_url}/props → ``n_ctx`` (top-level ou
          ``default_generation_settings``) ;
      (d) table de familles (minimum conservateur documenté).

    Garanties :
      - accès : propriétaire, ou admin pour un preset global (la détection
        PERSISTE sur la ligne, donc pas d'écriture partagée non-admin) ;
      - mêmes protections anti-SSRF que list-models (``_validate_llm_base_url``
        + ``_safe_llm_get``/``_safe_llm_post``, opt-in
        ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` inchangé) ;
      - n'écrase JAMAIS une valeur manuelle (context_source='manual') ;
      - si la valeur effective est vide (source NULL) le résultat est persisté
        (context_length + context_source + context_checked_at ; pour
        ``unknown`` : context_length=NULL + source='unknown') ;
      - jamais de clé API ni de secret dans la réponse.
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    is_owner = (row['user_id'] == user_id)
    if row['is_global']:
        # Preset global partagé : la détection persiste → admin only.
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return admin_guard
    elif not is_owner:
        # Preset personnel d'autrui : admin autorisé, sinon 404
        # (anti-énumération, même règle que PUT / list-models).
        admin_guard = _admin_required()
        if admin_guard:
            conn.close()
            return jsonify({'error': 'Not found'}), 404

    # Jamais d'écrasement d'une valeur manuelle : court-circuit sans sonde.
    manual_ctx = _row_get(row, 'context_length', None)
    if manual_ctx is not None:
        conn.close()
        return jsonify({
            'detected_length': int(manual_ctx),
            'source': 'manual',
            'probe': 'none',
            'status': 'ok',
            'detail': "Valeur manuelle existante conservée (aucune sonde exécutée).",
        })

    base_url = (row['base_url'] or '').rstrip('/')
    api_key = decrypt_api_key(row['api_key_encrypted'])
    model = row['model']
    conn.close()

    def _persist(context_length, source):
        """Persiste le résultat de détection sur la ligne du preset."""
        c2 = get_db()
        c2.execute(
            "UPDATE ai_presets SET context_length = ?, context_source = ?, context_checked_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (context_length, source, datetime.now(timezone.utc).isoformat(), preset_id)
        )
        c2.commit()
        c2.close()

    err = _validate_llm_base_url(base_url)
    if err:
        # Hôte refusé (protection SSRF) : aucune sonde émise, rien de persisté.
        logger.warning("[presets] Détection contexte refusée (preset %s) : %s", preset_id, err)
        return jsonify({
            'detected_length': None, 'source': 'unknown', 'probe': 'none',
            'status': 'blocked', 'detail': 'Fournisseur refusé (protection SSRF).',
        })

    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}

    # Meilleur statut d'échec rencontré (le plus actionnable gagne).
    best = {'status': 'unreachable', 'detail': 'Fournisseur inaccessible.'}
    priority = {'unauthorized': 3, 'not_found': 2, 'unreachable': 1}

    def _note(status, detail):
        if priority.get(status, 0) > priority.get(best['status'], 0):
            best['status'], best['detail'] = status, detail

    def _http_note(resp, what):
        if resp.status_code in (401, 403):
            _note('unauthorized', f"{what} : authentification refusée (HTTP {resp.status_code}).")
        elif resp.status_code == 404:
            _note('not_found', f"{what} : introuvable (HTTP 404).")
        else:
            _note('unreachable', f"{what} : HTTP {resp.status_code}.")

    # (a) API OpenAI-compat : GET {base}/models
    try:
        r = _safe_llm_get(f'{base_url}/models', headers=headers)
        if not r.ok:
            _http_note(r, f"{base_url}/models")
        else:
            data = r.json()
            models = data.get('data', data.get('models', [])) if isinstance(data, dict) else []
            ctx = _context_from_model_entry(models, model)
            if ctx:
                _persist(ctx, 'auto')
                return jsonify({'detected_length': ctx, 'source': 'auto', 'probe': 'api',
                                'status': 'ok', 'detail': 'Détecté via /models.'})
            _note('not_found', 'Modèle absent de la réponse /models.')
    except ValueError:
        # Refus anti-SSRF en cours de sonde : on arrête tout (conservateur).
        return jsonify({'detected_length': None, 'source': 'unknown', 'probe': 'none',
                        'status': 'blocked', 'detail': 'Fournisseur refusé (protection SSRF).'})
    except Exception:
        _note('unreachable', 'Fournisseur inaccessible.')

    # (b) Ollama : POST {base}/api/show — même logique de parsing que
    # _get_model_context (routes/enhance.py) mais avec granularité de statut.
    try:
        native_base = base_url[:-3] if base_url.endswith('/v1') else base_url
        r = _safe_llm_post(f'{native_base}/api/show', {'model': model}, headers=headers)
        if not r.ok:
            _http_note(r, f"{native_base}/api/show")
        else:
            info = r.json().get('model_info', {})
            ctx = None
            for k, v in info.items():
                if 'context_length' in k:
                    try:
                        vi = int(v)
                    except (TypeError, ValueError):
                        continue
                    if vi > 0:
                        ctx = vi
                        break
            if ctx:
                _persist(ctx, 'auto')
                return jsonify({'detected_length': ctx, 'source': 'auto', 'probe': 'ollama',
                                'status': 'ok', 'detail': 'Détecté via /api/show (Ollama).'})
    except ValueError:
        return jsonify({'detected_length': None, 'source': 'unknown', 'probe': 'none',
                        'status': 'blocked', 'detail': 'Fournisseur refusé (protection SSRF).'})
    except Exception:
        _note('unreachable', 'Fournisseur inaccessible.')

    # (c) llama.cpp : GET {base}/props → n_ctx
    try:
        r = _safe_llm_get(f'{base_url}/props', headers=headers)
        if not r.ok:
            _http_note(r, f"{base_url}/props")
        else:
            props = r.json()
            n_ctx = None
            if isinstance(props, dict):
                n_ctx = props.get('n_ctx')
                if n_ctx is None and isinstance(props.get('default_generation_settings'), dict):
                    n_ctx = props['default_generation_settings'].get('n_ctx')
            try:
                n_ctx = int(n_ctx) if n_ctx is not None else None
            except (TypeError, ValueError):
                n_ctx = None
            if n_ctx and n_ctx > 0:
                _persist(n_ctx, 'auto')
                return jsonify({'detected_length': n_ctx, 'source': 'auto', 'probe': 'llama',
                                'status': 'ok', 'detail': 'Détecté via /props (llama.cpp).'})
            _note('not_found', 'n_ctx absent de /props.')
    except ValueError:
        return jsonify({'detected_length': None, 'source': 'unknown', 'probe': 'none',
                        'status': 'blocked', 'detail': 'Fournisseur refusé (protection SSRF).'})
    except Exception:
        _note('unreachable', 'Fournisseur inaccessible.')

    # (d) Table de familles (minimum conservateur documenté).
    from routes.enhance import guess_family_context
    family_value, family_key = guess_family_context(model)
    if family_value:
        _persist(family_value, 'family')
        return jsonify({'detected_length': int(family_value), 'source': 'family', 'probe': 'family',
                        'status': 'ok',
                        'detail': f"Famille '{family_key}' : minimum conservateur documenté."})

    # Inconnu explicite : jamais de valeur inventée.
    _persist(None, 'unknown')
    return jsonify({'detected_length': None, 'source': 'unknown', 'probe': 'none',
                    'status': best['status'], 'detail': best['detail']})
