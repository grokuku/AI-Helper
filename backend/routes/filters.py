"""Routes filters for AI-Helper backend."""

from context import *


@app.route('/api/filters', methods=['GET', 'POST'])
def filters():
    """Liste ou crée un filtre sauvegardé pour l'utilisateur courant.

    Returns:
        flask.Response: JSON listant les filtres (GET) ou l'ID du filtre créé (POST).
    """
    guard = _login_required()
    if guard:
        return guard
    user_id = _get_current_user_id()

    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'error': 'Nom requis'}), 400

        conn = get_db()
        cur = conn.cursor()

        filter_type = data.get('filter_type', 'simple')

        cur.execute(
            "INSERT INTO saved_filters (user_id, name, category, nsfw, is_public, config, filter_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, data['name'].strip(), data.get('category', '').strip(), int(data.get('nsfw', 0)), int(data.get('is_public', 0)), json.dumps(data.get('config', {})), filter_type)
        )
        filter_id = cur.lastrowid

        # Si c'est une union, enregistrer les membres dans filter_unions
        if filter_type == 'union':
            member_ids = data.get('union_member_ids', [])
            for mid in member_ids:
                cur.execute("INSERT OR IGNORE INTO filter_unions (union_filter_id, member_filter_id) VALUES (?, ?)", (filter_id, mid))

        conn.commit()
        config = data.get('config', {})
        if isinstance(config, dict):
            # Pour les unions, on ajoute les infos nécessaires à la config pour rebuild
            if filter_type == 'union':
                config['filter_type'] = 'union'
                config['union_member_ids'] = data.get('union_member_ids', [])
            _rebuild_filter_cache(cur, filter_id, config, user_id)
        conn.commit()
        conn.close()
        return jsonify({'id': filter_id, 'count': _count_filter_cache(filter_id)}), 201

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT f.id, f.user_id, f.name, f.category, f.nsfw, f.is_public, f.config,
               u.display_name, u.username
        FROM saved_filters f
        LEFT JOIN users u ON u.id = f.user_id
        WHERE f.user_id = ? OR f.is_public = 1
        ORDER BY f.name
    """, (user_id,))
    rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['config'] = json.loads(d['config']) if isinstance(d['config'], str) else d['config']
        # Ajouter owner_name pour l'affichage dans l'UI
        d['owner_name'] = d.pop('display_name', None) or d.pop('username', None) or d['user_id']
        # Ajouter filter_type (avec défaut pour les anciens filtres)
        d['filter_type'] = d.get('filter_type', 'simple')
        # Si c'est une union, charger les membres
        if d['filter_type'] == 'union':
            cur2 = conn.cursor()
            cur2.execute("""
                SELECT fu.member_filter_id, sf.name
                FROM filter_unions fu
                JOIN saved_filters sf ON sf.id = fu.member_filter_id
                WHERE fu.union_filter_id = ?
            """, (d['id'],))
            d['union_members'] = [dict(m) for m in cur2.fetchall()]
        else:
            d['union_members'] = []
        result.append(d)
    conn.close()
    return jsonify(result)


@app.route('/api/filters/<int:filter_id>', methods=['PUT', 'DELETE'])
def single_filter(filter_id):
    """Met à jour ou supprime un filtre sauvegardé appartenant à l'utilisateur.

    Args:
        filter_id: L'identifiant du filtre à modifier ou supprimer.

    Returns:
        flask.Response: JSON confirmant l'opération ou une erreur 404.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM saved_filters WHERE id = ?", (filter_id,))
    row = cur.fetchone()
    if not row or row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    if request.method == 'DELETE':
        cur.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))
        conn.commit(); conn.close()
        return jsonify({'status': 'ok'})
    data = request.get_json() or {}
    vals = (
        data.get('name', row['name']),
        data.get('category', row['category'] or ''),
        int(data.get('nsfw', row['nsfw'])),
        int(data.get('is_public', row['is_public'])),
        filter_id
    )
    cur.execute("UPDATE saved_filters SET name=?, category=?, nsfw=?, is_public=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)

    # Gérer les membres d'une union
    if 'union_member_ids' in data:
        cur.execute("DELETE FROM filter_unions WHERE union_filter_id = ?", (filter_id,))
        for mid in data['union_member_ids']:
            cur.execute("INSERT OR IGNORE INTO filter_unions (union_filter_id, member_filter_id) VALUES (?, ?)", (filter_id, mid))
        # Mettre à jour le filter_type selon la nouvelle config
        if data.get('filter_type') == 'union':
            cur.execute("UPDATE saved_filters SET filter_type = 'union', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (filter_id,))
        else:
            # L'utilisateur a retiré les membres → repasser en filtre simple
            cur.execute("UPDATE saved_filters SET filter_type = 'simple', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (filter_id,))

    config = data.get('config')
    if config and isinstance(config, dict):
        if data.get('filter_type') == 'union':
            config['filter_type'] = 'union'
            config['union_member_ids'] = data.get('union_member_ids', [])
        cur.execute("UPDATE saved_filters SET config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(config), filter_id))
        cur.execute("DELETE FROM filter_cache WHERE filter_id = ?", (filter_id,))
        _rebuild_filter_cache(cur, filter_id, config, user_id)
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/filters/<int:filter_id>/refresh', methods=['POST'])
def refresh_filter_cache(filter_id):
    """Reconstruit le cache des mots-clés d'un filtre à partir de sa config.

    Args:
        filter_id: L'identifiant du filtre dont le cache doit être rafraîchi.

    Returns:
        flask.Response: JSON avec le statut et le nombre de mots-clés dans le cache.
    """
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, config FROM saved_filters WHERE id = ?", (filter_id,))
    row = cur.fetchone()
    if not row or row['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    config = json.loads(row['config']) if isinstance(row['config'], str) else row['config']
    # Pour les unions, enrichir la config avec les membres actuels
    cur.execute("SELECT filter_type FROM saved_filters WHERE id = ?", (filter_id,))
    ft = cur.fetchone()
    filter_type = ft['filter_type'] if ft else 'simple'
    if filter_type == 'union':
        config['filter_type'] = 'union'
        cur.execute("SELECT member_filter_id FROM filter_unions WHERE union_filter_id = ?", (filter_id,))
        config['union_member_ids'] = [r['member_filter_id'] for r in cur.fetchall()]
    cur.execute("DELETE FROM filter_cache WHERE filter_id = ?", (filter_id,))
    _rebuild_filter_cache(cur, filter_id, config, user_id)
    conn.commit(); conn.close()
    return jsonify({'status': 'ok', 'count': _count_filter_cache(filter_id)})


def _rebuild_filter_cache(cur, filter_id, config, user_id=None):
    """Reconstruit le cache d'un filtre en insérant les keyword_ids correspondants.

    Args:
        cur: Curseur de base de données actif.
        filter_id: Identifiant du filtre à reconstruire.
        config: Dictionnaire de configuration du filtre (section, recherche, sémantique, etc.).
        user_id: Identifiant de l'utilisateur propriétaire (pour le filtrage de confidentialité).
    """
    # Si c'est un filtre composé (union), merger les caches des membres
    filter_type = config.get('filter_type', 'simple')
    if filter_type == 'union':
        member_ids = config.get('union_member_ids', [])
        if member_ids:
            # Récupérer les keyword_ids de chaque membre et les unir (déduplication automatique par PRIMARY KEY)
            ph = ','.join('?' for _ in member_ids)
            cur.execute(f"""
                INSERT OR IGNORE INTO filter_cache (filter_id, keyword_id)
                SELECT ?, keyword_id FROM filter_cache
                WHERE filter_id IN ({ph})
            """, [filter_id] + member_ids)
        return

    # Filtre simple : construction de la requête
    privacy_where, privacy_params = _privacy_filter(user_id) if user_id else ("k.privacy_status = 'public'", [])
    conditions = [privacy_where]
    params = privacy_params[:]
    section = config.get('section', '').strip()
    subsection = config.get('subsection', '').strip()
    search_text = config.get('search_text', '').strip()
    search_neg = config.get('search_neg', '').strip()
    semantic_text = config.get('semantic_text', '').strip()
    min_confidence = float(config.get('min_confidence', 0))
    nsfw = str(config.get('nsfw_filter', ''))
    hidden_ids = config.get('hidden_kw_ids', [])

    if section:
        conditions.append("k.section_id = ?")
        params.append(section)
    if subsection:
        conditions.append("k.subsection_id = ?")
        params.append(subsection)
    if nsfw == '0':
        conditions.append("k.nsfw = 0")
    elif nsfw == '1':
        conditions.append("k.nsfw = 1")
    if search_text and not semantic_text:
        like = f"%{search_text.lower()}%"
        conditions.append("(LOWER(k.keyword) LIKE ? OR LOWER(k.description) LIKE ? OR LOWER(k.section_title) LIKE ? OR LOWER(k.subsection_title) LIKE ?)")
        params.extend([like, like, like, like])
    if search_neg:
        like_neg = f"%{search_neg.lower()}%"
        conditions.append("(LOWER(k.keyword) NOT LIKE ? AND LOWER(k.description) NOT LIKE ? AND LOWER(k.section_title) NOT LIKE ? AND LOWER(k.subsection_title) NOT LIKE ?)")
        params.extend([like_neg, like_neg, like_neg, like_neg])
    if hidden_ids and isinstance(hidden_ids, list) and len(hidden_ids) > 0:
        ph = ','.join('?' for _ in hidden_ids)
        conditions.append(f"k.id NOT IN ({ph})")
        params.extend(hidden_ids)

    if semantic_text:
        try:
            from embeddings import generate_embedding, cosine_similarity
            qe = generate_embedding(semantic_text)
            # Pré-filtrer section/nsfw/subsection dans la requête SQL (hidden_ids appliqué APRES la limite)
            sem_privacy_where, sem_privacy_params = _privacy_filter(user_id) if user_id else ("k.privacy_status = 'public'", [])
            sem_conds = [sem_privacy_where]
            sem_params = sem_privacy_params[:]
            if section:
                sem_conds.append("k.section_id = ?")
                sem_params.append(section)
            if subsection:
                sem_conds.append("k.subsection_id = ?")
                sem_params.append(subsection)
            if nsfw == '0':
                sem_conds.append("k.nsfw = 0")
            elif nsfw == '1':
                sem_conds.append("k.nsfw = 1")
            sem_where = " AND ".join(sem_conds)
            cur.execute(f"SELECT k.id, ke.embedding, k.keyword, k.description, k.section_title, k.subsection_title FROM keywords k JOIN keyword_embeddings ke ON ke.keyword_id = k.id WHERE {sem_where}", sem_params)
            # Calculer scores, filtrer, trier, limiter
            scored = []
            q_lower = search_text.lower() if search_text else ''
            neg_lower = search_neg.lower() if search_neg else ''
            for r in cur.fetchall():
                emb = json.loads(r['embedding'])
                sim = cosine_similarity(qe, emb)
                if sim < min_confidence:
                    continue
                # Appliquer texte (+) et exclusion (-) sur 4 champs (identique à loadKeywords)
                if q_lower or neg_lower:
                    fields = [
                        (r['keyword'] or '').lower(),
                        (r['description'] or '').lower(),
                        (r['section_title'] or '').lower(),
                        (r['subsection_title'] or '').lower()
                    ]
                    if q_lower and not any(q_lower in f for f in fields):
                        continue
                    if neg_lower and any(neg_lower in f for f in fields):
                        continue
                scored.append((r['id'], sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            # Prendre le top 500 (même ensemble que l'API), puis filtrer les masqués (comme renderTable)
            top = scored[:500]
            hidden_set = set(hidden_ids) if hidden_ids and isinstance(hidden_ids, list) else set()
            for kid, _ in top:
                if kid not in hidden_set:
                    cur.execute("INSERT OR IGNORE INTO filter_cache (filter_id, keyword_id) VALUES (?, ?)", (filter_id, kid))
        except Exception as e:
            print(f"[_rebuild_filter_cache] Erreur branche semantique filtre {filter_id}: {e}")
        return

    where = " AND ".join(conditions)
    cur.execute(f"INSERT OR IGNORE INTO filter_cache (filter_id, keyword_id) SELECT ?, k.id FROM keywords k WHERE {where}", [filter_id] + params)


def _count_filter_cache(filter_id):
    """Compte le nombre de mots-clés dans le cache d'un filtre.

    Args:
        filter_id: Identifiant du filtre.

    Returns:
        int: Nombre de mots-clés en cache pour ce filtre.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM filter_cache WHERE filter_id = ?", (filter_id,))
    c = cur.fetchone()[0]
    conn.close()
    return c



@app.route('/api/filters/<int:filter_id>/preview', methods=['GET'])
def preview_filter(filter_id):
    """Retourne un aperçu des mots-clés d'un filtre (20 premiers + total).

    Args:
        filter_id: Identifiant du filtre à prévisualiser.

    Returns:
        flask.Response: JSON contenant le nom, le total, un échantillon de mots-clés et la config.
    """
    guard = _login_required()
    if guard: return guard
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT k.keyword FROM filter_cache fc JOIN keywords k ON k.id = fc.keyword_id WHERE fc.filter_id = ? LIMIT 20", (filter_id,))
    keywords = [r['keyword'] for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM filter_cache WHERE filter_id = ?", (filter_id,))
    total = cur.fetchone()[0]
    cur.execute("SELECT name, config, filter_type FROM saved_filters WHERE id = ?", (filter_id,))
    info = cur.fetchone()
    result = {
        'name': info['name'] if info else '',
        'total': total,
        'keywords': keywords,
        'filter_type': info['filter_type'] if info else 'simple',
        'config': json.loads(info['config']) if info and isinstance(info['config'], str) else (info['config'] if info else {})
    }
    if info and info['filter_type'] == 'union':
        cur.execute("""
            SELECT fu.member_filter_id, sf.name
            FROM filter_unions fu
            JOIN saved_filters sf ON sf.id = fu.member_filter_id
            WHERE fu.union_filter_id = ?
        """, (filter_id,))
        result['union_members'] = [{'id': r['member_filter_id'], 'name': r['name']} for r in cur.fetchall()]
    conn.close()
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
# Phase 1 : Presets IA + Styles + Enhance
# ═══════════════════════════════════════════════════════════════════

