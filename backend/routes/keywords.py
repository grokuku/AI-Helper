"""Routes keywords for AI-Helper backend.
CRUD complet pour les mots-clés avec système de modération :

- privacy_status: 'public' | 'public_pending' | 'private'
- Un user voit : ses keywords (tous statuts) + les public des autres
- Un kw_editor/admin voit aussi les public_pending
- Les endpoints existants de liste (GET /api/keywords, sections, subsections, stats)
  sont filtrés pour n'exposer que les public + les siens
"""

import logging
from context import *


# ── CRUD mots-clés ───────────────────────────────────────────────

@app.route('/api/keywords', methods=['GET', 'POST'])
def list_or_create_keywords():
    """Liste les mots-clés visibles (GET) ou crée un nouveau mot-clé (POST).

    Returns:
        flask.Response: JSON listant les mots-clés filtrés (GET) ou l'ID du mot-clé créé (POST).
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()

    if request.method == 'POST':
        return _create_keyword(user_id)

    # ── GET ──
    conn = get_db()
    cur = conn.cursor()

    q = request.args.get('q', '').strip().lower()
    q_neg = request.args.get('q_neg', '').strip().lower()
    section = request.args.get('section', '').strip()
    subsection = request.args.get('subsection', '').strip()
    nsfw_raw = request.args.get('nsfw', '').strip()
    scope = request.args.get('scope', '').strip()  # backward compat ('mine' | 'public' | 'private' | 'pending')
    author = request.args.get('author', '').strip()  # user_id de l'auteur
    # Nouveaux filtres checkbox (combinables)
    privacy_filter = request.args.get('privacy', '').strip()  # comma-separated: 'public,private,pending'
    mine_only = request.args.get('mine', '').strip()  # '1' = mes keywords uniquement

    conditions = ["1=1"]
    params = []

    # Filtre privacy de base (visibilité selon le rôle)
    base_privacy_where, base_privacy_params = _privacy_filter(user_id)
    conditions.append(base_privacy_where)
    params.extend(base_privacy_params)

    # Filtre scope (backward compat) OU nouveaux filtres checkbox
    if scope:
        # Ancien mode : scope unique
        if scope == 'mine':
            conditions.append("k.user_id = ?")
            params.append(user_id)
        elif scope == 'public':
            conditions.append("k.privacy_status = 'public'")
        elif scope == 'private':
            conditions.append("k.privacy_status = 'private'")
            conditions.append("k.user_id = ?")
            params.append(user_id)
        elif scope == 'pending':
            conditions.append("k.privacy_status = 'public_pending'")
            if not is_kw_editor(user_id):
                conditions.append("k.user_id = ?")
                params.append(user_id)
    else:
        # Nouveau mode : checkboxes combinables
        if mine_only == '1':
            conditions.append("k.user_id = ?")
            params.append(user_id)
        if privacy_filter:
            statuses = [s.strip() for s in privacy_filter.split(',') if s.strip()]
            # Sécuriser les valeurs
            valid_statuses = [s for s in statuses if s in ('public', 'private', 'public_pending')]
            if valid_statuses:
                # Pour 'private' et 'pending', restreindre aux propres keywords si non éditeur
                if not is_kw_editor(user_id):
                    valid_statuses = [s for s in valid_statuses if s == 'public']
                    if not valid_statuses:
                        # Non-éditeur ne peut voir private/pending que si ce sont les siens
                        conditions.append("(k.privacy_status = 'public' OR k.user_id = ?)")
                        params.append(user_id)
                    else:
                        placeholders = ','.join('?' for _ in valid_statuses)
                        conditions.append(f"k.privacy_status IN ({placeholders})")
                        params.extend(valid_statuses)
                else:
                    placeholders = ','.join('?' for _ in valid_statuses)
                    conditions.append(f"k.privacy_status IN ({placeholders})")
                    params.extend(valid_statuses)

    # Filtre par auteur
    if author:
        conditions.append("k.user_id = ?")
        params.append(author)

    if q:
        like = f"%{q}%"
        conditions.append("(LOWER(k.keyword) LIKE ? OR LOWER(k.description) LIKE ? OR LOWER(k.section_title) LIKE ? OR LOWER(k.subsection_title) LIKE ?)")
        params.extend([like, like, like, like])

    if q_neg:
        like_neg = f"%{q_neg}%"
        conditions.append("(LOWER(k.keyword) NOT LIKE ? AND LOWER(k.description) NOT LIKE ? AND LOWER(k.section_title) NOT LIKE ? AND LOWER(k.subsection_title) NOT LIKE ?)")
        params.extend([like_neg, like_neg, like_neg, like_neg])

    if section:
        conditions.append("k.section_id = ?")
        params.append(section)
    if subsection:
        conditions.append("k.subsection_id = ?")
        params.append(subsection)

    if nsfw_raw in ('0', '1'):
        conditions.append("k.nsfw = ?")
        params.append(int(nsfw_raw))

    sql = f"""
        SELECT k.id, k.keyword, k.description, k.section_id, k.section_title,
               k.subsection_id, k.subsection_title, k.nsfw, k.privacy_status, k.user_id
        FROM keywords k
        WHERE {' AND '.join(conditions)}
        ORDER BY k.section_id, k.subsection_id, k.keyword
    """
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/keywords/authors', methods=['GET'])
def list_keyword_authors():
    """Liste les auteurs ayant créé au moins un keyword."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT DISTINCT k.user_id, u.username, u.display_name
        FROM keywords k
        LEFT JOIN users u ON u.id = k.user_id
        WHERE k.user_id IS NOT NULL AND {privacy_where}
        ORDER BY u.display_name, u.username
    """, privacy_params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


def _create_keyword(user_id):
    """POST /api/keywords — créer un nouveau mot-clé."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON requis'}), 400

    keyword = (data.get('keyword') or '').strip()
    description = (data.get('description') or '').strip()
    section_id = (data.get('section_id') or '').strip()
    section_title = (data.get('section_title') or '').strip()
    subsection_id = (data.get('subsection_id') or '').strip()
    subsection_title = (data.get('subsection_title') or '').strip()
    nsfw = int(data.get('nsfw', 0)) if data.get('nsfw') else 0
    privacy = data.get('privacy_status', 'private')

    if not keyword or not description:
        return jsonify({'error': 'keyword et description sont requis'}), 400

    if privacy not in ('private', 'public_pending', 'public'):
        privacy = 'private'

    # Un utilisateur normal ne peut pas créer directement en 'public'
    # (doit passer par public_pending → modération)
    if privacy == 'public' and not is_kw_editor(user_id):
        privacy = 'public_pending'

    conn = get_db()
    cur = conn.cursor()

    # Vérifier doublon (insensible à la casse) pour le même user OU global
    cur.execute("SELECT id FROM keywords WHERE LOWER(keyword) = LOWER(?)", (keyword,))
    if cur.fetchone():
        conn.close()
        return jsonify({'error': 'Ce mot-clé existe déjà (insensible à la casse)'}), 409

    cur.execute("""
        INSERT INTO keywords
            (keyword, description, section_id, section_title,
             subsection_id, subsection_title, nsfw, user_id, privacy_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (keyword, description, section_id, section_title,
          subsection_id, subsection_title, nsfw, user_id, privacy))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Régénérer l'embedding en arrière-plan
    _regenerate_keyword_embedding(new_id)

    return jsonify({'id': new_id, 'privacy_status': privacy}), 201


@app.route('/api/keywords/<int:keyword_id>', methods=['PUT', 'DELETE'])
def edit_or_delete_keyword(keyword_id):
    """Modifie (PUT) ou supprime (DELETE) un mot-clé existant.

    Args:
        keyword_id: Identifiant du mot-clé à modifier ou supprimer.

    Returns:
        flask.Response: JSON confirmant l'opération ou une erreur 403/404.
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM keywords WHERE id = ?", (keyword_id,))
    kw = cur.fetchone()
    if not kw:
        conn.close()
        return jsonify({'error': 'Mot-clé introuvable'}), 404

    kw = dict(kw)

    # Vérifier les droits
    is_owner = (kw['user_id'] == user_id)
    is_editor = is_kw_editor(user_id)

    if request.method == 'DELETE':
        if not is_owner and not is_admin(user_id):
            conn.close()
            return jsonify({'error': 'Vous ne pouvez supprimer que vos propres mots-clés'}), 403

        cur.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        cur.execute("DELETE FROM keyword_embeddings WHERE keyword_id = ?", (keyword_id,))

        # Invalider les caches de filtres qui contenaient ce keyword
        cur.execute("DELETE FROM filter_cache WHERE keyword_id = ?", (keyword_id,))

        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})

    # ── PUT ──
    data = request.get_json()
    if not data:
        conn.close()
        return jsonify({'error': 'JSON requis'}), 400

    # Qui peut modifier ?
    if is_owner:
        # Le propriétaire peut modifier son keyword quel que soit son statut
        pass
    elif is_editor:
        # Un éditeur peut modifier n'importe quel keyword (pour review)
        pass
    else:
        conn.close()
        return jsonify({'error': 'Vous ne pouvez modifier que vos propres mots-clés'}), 403

    keyword = (data.get('keyword') or '').strip()
    description = (data.get('description') or '').strip()
    section_id = (data.get('section_id') or kw['section_id'] or '').strip()
    section_title = (data.get('section_title') or kw['section_title'] or '').strip()
    subsection_id = (data.get('subsection_id') or kw['subsection_id'] or '').strip()
    subsection_title = (data.get('subsection_title') or kw['subsection_title'] or '').strip()
    nsfw = int(data.get('nsfw', kw['nsfw'])) if 'nsfw' in data else kw['nsfw']
    privacy = data.get('privacy_status', kw['privacy_status'])

    if not keyword or not description:
        conn.close()
        return jsonify({'error': 'keyword et description sont requis'}), 400

    if privacy not in ('private', 'public_pending', 'public'):
        privacy = kw['privacy_status']

    # Si un utilisateur non-éditeur modifie un keyword public,
    # le repasser en public_pending pour re-modération
    if is_owner and not is_editor and kw['privacy_status'] == 'public':
        privacy = 'public_pending'

    # Vérifier doublon (sauf soi-même)
    cur.execute("SELECT id FROM keywords WHERE LOWER(keyword) = LOWER(?) AND id != ?", (keyword, keyword_id))
    if cur.fetchone():
        conn.close()
        return jsonify({'error': 'Un autre mot-clé avec ce nom existe déjà'}), 409

    cur.execute("""
        UPDATE keywords SET
            keyword = ?, description = ?, section_id = ?, section_title = ?,
            subsection_id = ?, subsection_title = ?, nsfw = ?, privacy_status = ?
        WHERE id = ?
    """, (keyword, description, section_id, section_title,
          subsection_id, subsection_title, nsfw, privacy, keyword_id))
    conn.commit()
    conn.close()

    # Régénérer l'embedding
    _regenerate_keyword_embedding(keyword_id)

    return jsonify({'status': 'ok', 'id': keyword_id})


# ── Modération ────────────────────────────────────────────────────

@app.route('/api/keywords/pending', methods=['GET'])
def list_pending_keywords():
    """Liste les mots-clés en attente de validation (kw_editor/admin seulement)."""
    guard = _kw_editor_required()
    if guard:
        return guard

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT k.id, k.keyword, k.description, k.section_id, k.section_title,
               k.subsection_id, k.subsection_title, k.nsfw, k.user_id,
               u.username as creator_name, k.created_at
        FROM keywords k
        LEFT JOIN users u ON u.id = k.user_id
        WHERE k.privacy_status = 'public_pending'
        ORDER BY k.created_at ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/keywords/<int:keyword_id>/review', methods=['POST'])
def review_keyword(keyword_id):
    """Valider ou rejeter un mot-clé en attente (kw_editor/admin seulement)."""
    guard = _kw_editor_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON requis'}), 400

    action = data.get('action', '')  # 'approve' | 'reject'
    notes = (data.get('notes') or '').strip()

    if action not in ('approve', 'reject'):
        return jsonify({'error': "action must be 'approve' or 'reject'"}), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id, privacy_status, user_id FROM keywords WHERE id = ?", (keyword_id,))
    kw = cur.fetchone()
    if not kw:
        conn.close()
        return jsonify({'error': 'Mot-clé introuvable'}), 404

    kw = dict(kw)
    if kw['privacy_status'] != 'public_pending':
        conn.close()
        return jsonify({'error': 'Ce mot-clé n\'est pas en attente de modération'}), 400

    # Si des champs édités sont fournis, on met à jour le keyword avant de valider/rejeter
    edit_fields = data.get('edits')
    if edit_fields and isinstance(edit_fields, dict):
        updates = []
        params = []
        for field in ('keyword', 'description', 'section_id', 'section_title',
                      'subsection_id', 'subsection_title', 'nsfw'):
            if field in edit_fields:
                updates.append(f"{field} = ?")
                params.append(edit_fields[field])
        if updates:
            params.append(keyword_id)
            cur.execute(f"UPDATE keywords SET {', '.join(updates)} WHERE id = ?", params)

    new_status = 'public' if action == 'approve' else 'private'

    cur.execute("""
        UPDATE keywords SET
            privacy_status = ?,
            reviewed_by = ?,
            reviewed_at = CURRENT_TIMESTAMP,
            review_notes = ?
        WHERE id = ?
    """, (new_status, user_id, notes, keyword_id))

    # Si on passe de public à private, invalider les caches qui pointent vers ce keyword
    if action == 'reject':
        cur.execute("DELETE FROM filter_cache WHERE keyword_id = ?", (keyword_id,))

    conn.commit()
    conn.close()

    # Si approuvé, régénérer l'embedding (au cas où l'éditeur a modifié)
    if action == 'approve':
        _regenerate_keyword_embedding(keyword_id)

    return jsonify({'status': 'ok', 'new_privacy_status': new_status})


# ── Bulk import ───────────────────────────────────────────────────

@app.route('/api/keywords/bulk', methods=['POST'])
def bulk_import_keywords():
    """Import en masse de mots-clés (format texte simple ou CSV)."""
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON requis'}), 400

    raw = (data.get('text') or '').strip()
    privacy = data.get('privacy_status', 'private')
    if privacy not in ('private', 'public_pending', 'public'):
        privacy = 'private'
    if privacy == 'public' and not is_kw_editor(user_id):
        privacy = 'public_pending'

    if not raw:
        return jsonify({'error': 'texte requis'}), 400

    # Format attendu : une ligne par mot-clé
    # Format simple : "keyword | description"
    # Format complet : "keyword | description | section | subsection | nsfw(0/1)"
    lines = [l.strip() for l in raw.split('\n') if l.strip()]

    conn = get_db()
    cur = conn.cursor()

    # Charger les keywords existants
    cur.execute("SELECT LOWER(keyword) FROM keywords")
    existing_set = {r[0] for r in cur.fetchall()}

    imported = 0
    skipped = 0
    errors = []
    new_ids = []  # Collect new keyword IDs for batch embedding

    for i, line in enumerate(lines):
        try:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 2:
                errors.append(f"Ligne {i+1}: format invalide (besoin keyword | description)")
                skipped += 1
                continue

            keyword = parts[0]
            description = parts[1]
            section_id = parts[2] if len(parts) > 2 else ''
            section_title = parts[2] if len(parts) > 2 else ''
            subsection_id = parts[3] if len(parts) > 3 else ''
            subsection_title = parts[3] if len(parts) > 3 else ''
            nsfw = int(parts[4]) if len(parts) > 4 and parts[4] in ('0', '1') else 0

            if not keyword or not description:
                errors.append(f"Ligne {i+1}: keyword et description requis")
                skipped += 1
                continue

            kw_lower = keyword.lower()
            if kw_lower in existing_set:
                errors.append(f"Ligne {i+1}: doublon (ignoré)")
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO keywords
                    (keyword, description, section_id, section_title,
                     subsection_id, subsection_title, nsfw, user_id, privacy_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (keyword, description, section_id, section_title,
                  subsection_id, subsection_title, nsfw, user_id, privacy))
            new_id = cur.lastrowid
            existing_set.add(kw_lower)
            imported += 1
            new_ids.append(new_id)
        except Exception as e:
            errors.append(f"Ligne {i+1}: {str(e)}")
            skipped += 1

    conn.commit()
    conn.close()

    # Régénérer les embeddings APRÈS avoir fermé la connexion principale
    for nid in new_ids:
        _regenerate_keyword_embedding(nid)

    return jsonify({
        'imported': imported,
        'skipped': skipped,
        'errors': errors[:20],  # Limiter le nombre d'erreurs
        'message': f"{imported} importés, {skipped} ignorés"
    })


# ── Export ────────────────────────────────────────────────────────

@app.route('/api/keywords/export', methods=['GET'])
def export_keywords():
    """Export des mots-clés au format Markdown (similaire au fichier source)."""
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    scope = request.args.get('scope', 'public').strip()  # 'mine' | 'public' | 'all'

    conn = get_db()
    cur = conn.cursor()

    if scope == 'mine':
        cur.execute("""
            SELECT * FROM keywords
            WHERE user_id = ?
            ORDER BY section_id, subsection_id, id
        """, (user_id,))
    elif scope == 'all':
        cur.execute("""
            SELECT * FROM keywords
            ORDER BY section_id, subsection_id, id
        """)
    else:
        # public (par défaut)
        cur.execute("""
            SELECT * FROM keywords
            WHERE privacy_status = 'public'
            ORDER BY section_id, subsection_id, id
        """)

    rows = cur.fetchall()
    conn.close()

    # Générer le Markdown
    lines = ["# Keywords Export", ""]
    current_section = None
    current_subsection = None

    for r in rows:
        sec = r['section_id'] or ''
        sec_title = r['section_title'] or ''
        sub = r['subsection_id'] or ''
        sub_title = r['subsection_title'] or ''

        if sec != current_section:
            lines.append(f"## {sec}. {sec_title}")
            lines.append("")
            current_section = sec
            current_subsection = None

        if sub != current_subsection:
            lines.append(f"### {sub} — {sub_title}")
            lines.append("")
            current_subsection = sub

        nsfw_tag = " [NSFW]" if r['nsfw'] else ""
        lines.append(f"- **{r['keyword']}**{nsfw_tag}: {r['description']}")

    text = "\n".join(lines)
    return Response(text, mimetype='text/markdown',
                    headers={'Content-Disposition': 'attachment; filename=keywords-export.md'})


# ── Vérification de doublons ─────────────────────────────────────

@app.route('/api/keywords/check-duplicates', methods=['POST'])
def check_keyword_duplicates():
    """Vérifie si un mot-clé existe déjà (recherche exacte et sémantique)."""
    guard = _login_required()
    if guard:
        return guard

    data = request.get_json()
    if not data or not data.get('keyword'):
        return jsonify({'error': 'keyword requis dans le body'}), 400

    keyword = data['keyword'].strip()
    threshold = float(data.get('threshold', 0.85))
    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)

    # Phase 1: read all needed data from DB, then CLOSE the connection
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT k.id, k.keyword, k.description, k.privacy_status, k.user_id
        FROM keywords k
        WHERE LOWER(k.keyword) = LOWER(?) AND {privacy_where}
    """, [keyword] + privacy_params)
    exact_matches = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT k.id, k.keyword, k.description, k.privacy_status, k.user_id, ke.embedding
        FROM keywords k
        JOIN keyword_embeddings ke ON ke.keyword_id = k.id
        WHERE {privacy_where}
    """, privacy_params)
    all_rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Phase 2: slow operations — NO DB connection held
    semantic_matches = []
    try:
        from embeddings import generate_embedding, cosine_similarity
        vec = generate_embedding(keyword)
        if vec:
            for r in all_rows:
                try:
                    ref_vec = json.loads(r['embedding'])
                    sim = cosine_similarity(vec, ref_vec)
                    if sim >= threshold:
                        semantic_matches.append({
                            'id': r['id'],
                            'keyword': r['keyword'],
                            'description': r['description'],
                            'privacy_status': r['privacy_status'],
                            'user_id': r['user_id'],
                            'similarity': round(sim, 4)
                        })
                except Exception:
                    logging.exception("keywords: embedding parse failed in duplicate check")
                    continue
            semantic_matches.sort(key=lambda x: x['similarity'], reverse=True)
            semantic_matches = semantic_matches[:10]  # Top 10
    except Exception as e:
        print(f"[check_keyword_duplicates] Erreur sémantique: {e}")

    return jsonify({
        'exact_matches': exact_matches,
        'semantic_matches': semantic_matches,
        'message': f"{len(exact_matches)} exacte(s), {len(semantic_matches)} similaire(s)"
    })


@app.route('/api/keywords/scan-duplicates', methods=['GET'])
def scan_keyword_duplicates():
    """Scan complet de la base : trouve tous les doublons exacts et sémantiques."""
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)

    conn = get_db()
    cur = conn.cursor()

    # 1. Exact duplicates (même mot, casse insensible)
    cur.execute(f"""
        SELECT LOWER(keyword) as kw_lower, GROUP_CONCAT(id) as ids,
               GROUP_CONCAT(keyword) as keywords,
               COUNT(*) as cnt
        FROM keywords k
        WHERE {privacy_where}
        GROUP BY LOWER(keyword)
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
    """, privacy_params)
    exact_duplicates = []
    for r in cur.fetchall():
        ids = [int(x) for x in r['ids'].split(',')]
        kws = r['keywords'].split(',')
        exact_duplicates.append({
            'normalized': r['kw_lower'],
            'count': r['cnt'],
            'ids': ids,
            'keywords': kws,
        })

    # 2. Semantic duplicates : pour chaque keyword, trouver les similaires
    # On charge les embeddings et on compare par paires
    cur.execute(f"""
        SELECT k.id, k.keyword, k.description, k.privacy_status, ke.embedding
        FROM keywords k
        JOIN keyword_embeddings ke ON ke.keyword_id = k.id
        WHERE {privacy_where}
    """, privacy_params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Phase 2: O(n²) computation — NO DB connection held
    semantic_groups = []
    try:
        from embeddings import generate_embedding, cosine_similarity
        # Construire des groupes par similarite
        visited = set()
        threshold = 0.85
        for i, r1 in enumerate(rows):
            if r1['id'] in visited:
                continue
            try:
                v1 = json.loads(r1['embedding'])
            except Exception:
                logging.exception("keywords: embedding parse failed in semantic groups")
                continue
            group = [{
                'id': r1['id'],
                'keyword': r1['keyword'],
                'description': r1['description'],
                'privacy_status': r1['privacy_status'],
                'similarity': 1.0,
            }]
            visited.add(r1['id'])
            for j, r2 in enumerate(rows):
                if j <= i or r2['id'] in visited:
                    continue
                try:
                    v2 = json.loads(r2['embedding'])
                    sim = cosine_similarity(v1, v2)
                    if sim >= threshold:
                        group.append({
                            'id': r2['id'],
                            'keyword': r2['keyword'],
                            'description': r2['description'],
                            'privacy_status': r2['privacy_status'],
                            'similarity': round(sim, 4),
                        })
                        visited.add(r2['id'])
                except Exception:
                    logging.exception("keywords: embedding parse failed in group inner loop")
                    continue
            if len(group) > 1:
                group.sort(key=lambda x: x['similarity'], reverse=True)
                semantic_groups.append(group)
        semantic_groups.sort(key=lambda g: len(g), reverse=True)
    except Exception as e:
        print(f"[scan_keyword_duplicates] Erreur semantique: {e}")

    return jsonify({
        'exact_duplicates': exact_duplicates,
        'semantic_groups': semantic_groups,
        'exact_count': len(exact_duplicates),
        'semantic_count': len(semantic_groups),
        'total_duplicates': sum(g['count'] for g in exact_duplicates) + sum(len(g) for g in semantic_groups),
    })


# ── Sections / Subsections / Stats (avec filtre privacy) ──────────

@app.route('/api/subsections', methods=['GET'])
def list_subsections():
    """Liste les sous-sections visibles avec le nombre de mots-clés dans chacune.

    Returns:
        flask.Response: JSON listant les sous-sections et leurs décomptes.
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)
    section_id = request.args.get('section', '').strip()

    conn = get_db()
    cur = conn.cursor()

    where_parts = [privacy_where]
    params = privacy_params.copy()

    if section_id:
        where_parts.append("section_id = ?")
        params.append(section_id)

    cur.execute(f"""
        SELECT k.subsection_id, k.subsection_title, COUNT(*) as total
        FROM keywords k
        WHERE {' AND '.join(where_parts)}
        GROUP BY k.subsection_id
        ORDER BY k.subsection_id
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/sections', methods=['GET'])
def list_sections():
    """Liste les sections visibles avec le nombre de mots-clés et le décompte NSFW.

    Returns:
        flask.Response: JSON listant les sections et leurs statistiques.
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT k.section_id, k.section_title,
               COUNT(*) as total,
               SUM(k.nsfw) as nsfw_count
        FROM keywords k
        WHERE {privacy_where}
        GROUP BY k.section_id
        ORDER BY k.section_id
    """, privacy_params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/stats', methods=['GET'])
def stats():
    """Retourne des statistiques globales sur les mots-clés et prompts générés.

    Returns:
        flask.Response: JSON avec total, nsfw_total, section_count, subsection_count, generated_total.
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()
    privacy_where, privacy_params = _privacy_filter(user_id)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) as total,
               SUM(k.nsfw) as nsfw_total,
               COUNT(DISTINCT k.section_id) as section_count,
               COUNT(DISTINCT k.subsection_id) as subsection_count
        FROM keywords k
        WHERE {privacy_where}
    """, privacy_params)
    row = dict(cur.fetchone())
    row = {k: (v if v is not None else 0) for k, v in row.items()}
    cur.execute("SELECT COUNT(*) as total FROM generated_prompts")
    gen = cur.fetchone()
    row['generated_total'] = gen['total'] if gen else 0
    conn.close()
    return jsonify(row)


