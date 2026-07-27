"""Routes ideogram for AI-Helper backend."""

import logging
from context import *
from routes.enhance import _prepare_enhance, convert_bboxes_to_normalized


# ── Endpoints decoupled pour Ideogram 4 ──────────────────────────
# Ces 2 endpoints sont utilises par les nodes ComfyUI :
#   - AI-Helper Ideogram Prep   → POST /api/ideogram/prep
#   - AI-Helper Ideogram Parse  → POST /api/ideogram/parse
# Le Prep construit les 2 prompts passe 1 + un context JSON pour la passe 2.
# Le Parse extrait/valide le JSON du LLM, convertit les bboxes, et (si pass=1)
# construit le prompt de validation pour la passe 2.


@app.route('/api/ideogram/prep', methods=['POST'])
def ideogram_prep():
    """
    Preparation decouplee pour Ideogram 4 (passe 1).

    Retourne 3 strings pretes a etre injectees dans n'importe quel node LLM
    compatible OpenAI :
      - llm_prompt     : le user prompt passe 1 (GENERAL DESCRIPTION, ELEMENTS TO
                         PLACE, IMAGE DIMENSIONS, STYLE, ADDITIONAL INSTRUCTIONS)
      - system_prompt  : le system prompt passe 1 (regles JSON Ideogram 4)
      - context        : JSON bundle pour la passe 2 : {original_input, width,
                         height, style_text, model}. Le Parse s'en sert pour
                         construire le validation_prompt.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    # Forcer validation_passes=0 (le client gere la passe 2)
    # template_id reste celui envoye par le client (template selectionne)
    data['validation_passes'] = 0

    import logging
    logging.warning(
        f"[ideogram/prep] REQUEST user={user_id} "
        f"keys={list(data.keys())} width={data.get('width')} height={data.get('height')}"
    )

    try:
        prepared = _prepare_enhance(user_id, data)
        if isinstance(prepared, tuple):  # erreur (jsonify, status)
            return prepared

        # Extraire system + user depuis llm_request['messages']
        messages = prepared.get('llm_request', {}).get('messages', [])
        system_prompt = ''
        llm_prompt = ''
        for m in messages:
            if m.get('role') == 'system':
                system_prompt = m.get('content', '')
            elif m.get('role') == 'user':
                llm_prompt = m.get('content', '')

        # Context : tout ce qu'il faut pour construire le prompt de validation
        # en passe 2. width/height/style_text viennent du payload Prep.
        # NB : on ne met PAS api_url/api_key ici pour eviter de fuiter la cle
        # API quand l'user exporte son workflow. La Parse node a son propre
        # widget _api_config cache, sync avec localStorage.
        context = json.dumps({
            'original_input': prepared.get('merged_text', ''),
            'width': int(data.get('width') or 0),
            'height': int(data.get('height') or 0),
            'style_text': prepared.get('style_text', ''),
            'model': prepared.get('model', ''),
        }, ensure_ascii=False)

        logging.warning(
            f"[ideogram/prep] user={user_id} preset='{prepared['preset_name']}' "
            f"model='{prepared['model']}' sys_len={len(system_prompt)} user_len={len(llm_prompt)}"
        )

        return jsonify({
            'llm_prompt': llm_prompt,
            'system_prompt': system_prompt,
            'context': context,
            'model': prepared['model'],
        })
    except Exception as e:
        logging.exception(f"[ideogram/prep] ERROR user={user_id} template_id={data.get('template_id')} width={data.get('width')} height={data.get('height')}")
        return jsonify({'error': f'Erreur serveur: {e}'}), 500


@app.route('/api/ideogram/parse', methods=['POST'])
def ideogram_parse():
    """
    Parse decouple pour Ideogram 4.

    Reçoit {llm_response, context} :
      - llm_response : la string brute sortie par le LLM
      - context      : le JSON bundle retourne par /api/ideogram/prep
                      (contient original_input, width, height, style_text, model)

    Retourne :
      - prompt            : JSON propre (bboxes en 0-1000) ou message d'erreur
      - validation_prompt : prompt de validation pour passe 2 (toujours construit)
      - validation_system : system prompt pour passe 2 (toujours construit)
      - debug             : markdown de debug
      - is_valid_json     : True si le LLM a sorti du JSON valide
      - error             : message d'erreur si pas du JSON valide

    Le user decide d'utiliser ou pas le validation_prompt selon son branchement.
    Le construire systematiquement ne coute rien et simplifie l'API.
    """
    import logging
    import re as _re

    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    llm_response_raw = (data.get('llm_response') or '').strip()
    context_str = data.get('context') or '{}'

    # 1) Parser le context (source de verite pour width/height)
    try:
        ctx = json.loads(context_str) if isinstance(context_str, str) else context_str
    except json.JSONDecodeError:
        ctx = {}

    width = int(ctx.get('width') or 0)
    height = int(ctx.get('height') or 0)

    # 2) Extraire le JSON du LLM response
    s = llm_response_raw
    m = _re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", s)
    if m:
        s = m.group(1)
    s = s.strip()

    is_valid = False
    parsed = None
    error_msg = ''
    try:
        parsed = json.loads(s)
        is_valid = True
    except Exception as e:
        error_msg = f"JSON invalide : {e}"

    # 3) Si valide, convertir les bboxes pixels -> 0-1000
    prompt_out = llm_response_raw  # fallback : la string brute
    if is_valid and width and height:
        converted = convert_bboxes_to_normalized(s, width, height)
        if isinstance(converted, str):
            try:
                json.loads(converted)
                prompt_out = converted
            except Exception:
                logging.exception("ideogram: bbox-converted JSON parse failed")
                prompt_out = s
        else:
            prompt_out = json.dumps(converted, ensure_ascii=False, indent=2)

    # 4) Construire le validation_prompt (optionnel, si validation_template_id fourni)
    #    Le client decide s'il veut l'utiliser ou pas.
    validation_prompt = ''
    validation_system = ''
    if is_valid:
        val_tmpl_id = ctx.get('validation_template_id') or data.get('validation_template_id')
        if val_tmpl_id:
            try:
                val_conn = get_db()
                val_row = val_conn.execute(
                    "SELECT system_prompt FROM prompt_templates WHERE id = ?",
                    (int(val_tmpl_id),)
                ).fetchone()
                if val_row:
                    validation_system = val_row['system_prompt'] or ''
                val_conn.close()
            except Exception:
                logging.exception("ideogram: validation template lookup failed")
                validation_system = ''

        if not validation_system:
            validation_system = 'You are a spatial composition expert. You output ONLY corrected JSON with properly placed bounding boxes.'

        # User prompt = la sortie brute du LLM (passe 1)
        validation_prompt = llm_response_raw

    # 5) Debug
    debug_md = f"### Ideogram Parse\n\n"
    debug_md += f"- LLM response length: {len(llm_response_raw)} chars\n"
    debug_md += f"- JSON valide: {is_valid}\n"
    if not is_valid:
        debug_md += f"- Erreur: {error_msg}\n"
    debug_md += f"- Bboxes converties: {bool(width and height)} (width={width}, height={height})\n"
    debug_md += f"- validation_prompt length: {len(validation_prompt)} chars\n"

    logging.warning(
        f"[ideogram/parse] user={user_id} "
        f"is_valid={is_valid} llm_len={len(llm_response_raw)} "
        f"validation_prompt_len={len(validation_prompt)}"
    )

    return jsonify({
        'prompt': prompt_out,
        'validation_prompt': validation_prompt,
        'validation_system': validation_system,
        'is_valid_json': is_valid,
        'error': error_msg,
        'debug': debug_md,
    })


