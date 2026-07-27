"""Routes generate for AI-Helper backend."""

import logging
from context import *


@app.route('/api/generate', methods=['POST'])
def generate_prompt():
    guard = _login_required()
    if guard:
        return guard
    rl = _check_rate_limit("generate", max_calls=30, window_seconds=60)
    if rl:
        return rl
    data = request.get_json()
    if not data:
        return jsonify({'error': 'payload requis'}), 400

    elements = data.get('elements', [])
    # Filtrer les entrées marquées visible=False (masquées depuis l'UI ComfyUI)
    elements = [el for el in elements if el.get('visible') is not False]

    # Seed pour reproductibilité — on utilise un Random local pour ne pas
    # polluer l'état global et garantir le déterminisme.
    # IMPORTANT : les requêtes SQL ne DOIVENT PAS utiliser ORDER BY RANDOM()
    # car SQLite a son propre RNG qui ignore random.seed().
    seed = data.get('seed')
    rng = random.Random(seed if seed is not None else None)

    random_count = int(data.get('random_count', 0))
    random_sfw = data.get('random_sfw', True)   # Défaut : SFW autorisé
    random_nsfw = data.get('random_nsfw', False)  # Défaut : NSFW non autorisé

    if not elements and random_count <= 0:
        return jsonify({'prompt': '', 'count': 0, 'elements': [], 'debug': []})

    conn = get_db()
    cur = conn.cursor()
    keywords = []
    debug = []

    for elem in elements:
        kid = None
        kind = ''
        score = 0

        if elem.get('type') == 'filter' and elem.get('id'):
            kind = 'filter'
            finfo = cur.execute(
                "SELECT name, (SELECT COUNT(*) FROM filter_cache WHERE filter_id = ?) as cnt FROM saved_filters WHERE id = ?",
                (elem['id'], elem['id'])
            ).fetchone()
            # Charger TOUS les keyword_ids du filtre, puis choisir en Python (déterministe)
            cur.execute(
                "SELECT keyword_id FROM filter_cache WHERE filter_id = ?",
                (elem['id'],)
            )
            all_kids = [r['keyword_id'] for r in cur.fetchall()]
            if all_kids:
                kid = rng.choice(all_kids)
            if finfo:
                debug.append({'source': f"filtre '{finfo['name']}' (cache: {finfo['cnt']})", 'picked': bool(kid)})

        elif elem.get('type') == 'text' and elem.get('text'):
            kind = 'semantic'
            try:
                from embeddings import generate_embedding, cosine_similarity
                qe = generate_embedding(elem['text'])
                gen_privacy_where, gen_privacy_params = _privacy_filter(user_id)
                cur.execute(
                    f"SELECT k.id, ke.embedding FROM keywords k JOIN keyword_embeddings ke ON ke.keyword_id = k.id WHERE {gen_privacy_where}",
                    gen_privacy_params
                )
                rows = cur.fetchall()
                if rows:
                    scored = []
                    for r in rows:
                        emb = json.loads(r['embedding'])
                        sim = cosine_similarity(qe, emb)
                        if sim >= 0.45:
                            scored.append((r['id'], sim))
                    if scored:
                        scored.sort(key=lambda x: x[1], reverse=True)
                        top = scored[:min(5, len(scored))]
                        kid, score = rng.choice(top)
            except Exception:
                logging.exception("generate: semantic search failed")

        elif elem.get('type') == 'raw' and elem.get('text'):
            # Custom text du node ComfyUI : on l'ajoute TEL QUEL dans le prompt.
            # Pas de recherche semantique, pas de pioche aleatoire.
            kind = 'raw'
            raw_text = elem['text'].strip()
            if raw_text:
                keywords.append(raw_text)
                debug.append({'keyword': raw_text, 'source': 'raw', 'score': 0})
            continue  # pas de kid a chercher en BDD

        if kid:
            cur.execute("SELECT keyword FROM keywords WHERE id = ?", (kid,))
            row = cur.fetchone()
            if row:
                keywords.append(row['keyword'])
                debug.append({'keyword': row['keyword'], 'source': kind, 'score': round(score, 3)})

    # Random elements : piocher depuis des sections non encore utilisées
    if random_count > 0:
        existing_kw_text = ', '.join(keywords).lower()
        existing_words = [w.strip() for w in existing_kw_text.replace(',', ' ').split() if len(w.strip()) >= 3]
        used_sections = set()
        if existing_words:
            placeholders = ','.join('?' for _ in existing_words)
            try:
                # SAFE: placeholders are parameterized '?' marks from a fixed-length list, not user input
                cur.execute(
                    f"SELECT DISTINCT section_id FROM keywords WHERE LOWER(keyword) IN ({placeholders})",
                    existing_words
                )
                used_sections = {r[0] for r in cur.fetchall() if r[0]}
            except Exception:
                logging.exception("generate: used_sections lookup failed")

        # Charger les candidats, puis choisir en Python (déterministe avec rng)
        # Filtrer par SFW/NSFW
        nsfw_filter = None
        if random_sfw and not random_nsfw:
            nsfw_filter = 0  # SFW uniquement
        elif random_nsfw and not random_sfw:
            nsfw_filter = 1  # NSFW uniquement
        # Si les deux ou aucun, pas de filtre

        if used_sections:
            ph = ','.join('?' for _ in used_sections)
            if nsfw_filter is not None:
                cur.execute(
                    f"SELECT keyword FROM keywords WHERE (section_id NOT IN ({ph}) OR section_id IS NULL) AND nsfw = ? AND privacy_status = 'public'",
                    list(used_sections) + [nsfw_filter]
                )
            else:
                cur.execute(
                    f"SELECT keyword FROM keywords WHERE (section_id NOT IN ({ph}) OR section_id IS NULL) AND privacy_status = 'public'",
                    list(used_sections)
                )
        else:
            if nsfw_filter is not None:
                cur.execute("SELECT keyword FROM keywords WHERE nsfw = ? AND privacy_status = 'public'", (nsfw_filter,))
            else:
                cur.execute("SELECT keyword FROM keywords WHERE privacy_status = 'public'")
        candidates = [r[0] for r in cur.fetchall()]
        n = min(random_count, len(candidates))
        rand_keywords = rng.sample(candidates, n) if n > 0 else []
        keywords.extend(rand_keywords)
        for rk in rand_keywords:
            debug.append({'keyword': rk, 'source': 'random', 'score': 0})

    conn.close()
    prompt = ", ".join(keywords) if keywords else ""
    return jsonify({'prompt': prompt, 'count': len(keywords), 'elements': debug, 'debug': debug})


