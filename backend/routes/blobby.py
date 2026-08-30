"""Routes Blobby — système de mémoire vectorielle."""

from context import *


@app.route('/api/blobby/memory', methods=['POST'])
def blobby_memory_save():
    """Sauvegarde un souvenir avec embedding."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    data = request.get_json() or {}

    content = (data.get('content') or '').strip()
    mem_type = (data.get('type') or 'episode').strip()
    importance = int(data.get('importance', 3))

    if not content:
        return jsonify({'error': 'content requis'}), 400
    if mem_type not in ('episode', 'fact', 'personality', 'relationship'):
        mem_type = 'episode'
    importance = max(1, min(5, importance))

    # Calculer l'embedding
    embedding = None
    try:
        vec = generate_embedding(content)
        embedding = json.dumps(vec)
    except Exception as e:
        print(f"[blobby/memory] Embedding error: {e}")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO blobby_memories (user_id, type, content, embedding, importance)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, mem_type, content, embedding, importance))
    mem_id = cur.lastrowid

    # Purge : garder max 50 épisodes par user
    if mem_type == 'episode':
        count = cur.execute("SELECT COUNT(*) FROM blobby_memories WHERE user_id = ? AND type = 'episode'", (user_id,)).fetchone()[0]
        if count > 50:
            # Supprimer les moins importants (importance * access_count / age)
            cur.execute("""
                DELETE FROM blobby_memories WHERE id IN (
                    SELECT id FROM blobby_memories
                    WHERE user_id = ? AND type = 'episode'
                    ORDER BY importance * (access_count + 1) ASC, created_at ASC
                    LIMIT ?
                )
            """, (user_id, count - 50))

    conn.commit()
    conn.close()
    return jsonify({'id': mem_id, 'ok': True})


@app.route('/api/blobby/memory/search', methods=['GET'])
def blobby_memory_search():
    """Recherche sémantique dans les souvenirs."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()

    q = request.args.get('q', '').strip()
    limit = int(request.args.get('limit', 5))
    mem_type = request.args.get('type', '').strip()  # filtre optionnel par type

    if not q:
        # Sans query : retourner les souvenirs les plus importants
        conn = get_db()
        cur = conn.cursor()
        sql = "SELECT id, type, content, importance, created_at, last_accessed, access_count FROM blobby_memories WHERE user_id = ?"
        params = [user_id]
        if mem_type:
            sql += " AND type = ?"
            params.append(mem_type)
        sql += " ORDER BY importance DESC, last_accessed DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'results': rows, 'mode': 'importance'})

    # Recherche sémantique
    try:
        query_vec = generate_embedding(q)
    except Exception as e:
        return jsonify({'error': f'Embedding error: {e}'}), 500

    conn = get_db()
    cur = conn.cursor()
    sql = "SELECT id, type, content, embedding, importance, created_at, last_accessed, access_count FROM blobby_memories WHERE user_id = ?"
    params = [user_id]
    if mem_type:
        sql += " AND type = ?"
        params.append(mem_type)
    cur.execute(sql, params)
    rows = cur.fetchall()

    if not rows:
        conn.close()
        return jsonify({'results': [], 'mode': 'semantic'})

    scored = []
    for r in rows:
        try:
            mem_vec = json.loads(r['embedding']) if r['embedding'] else None
            sim = cosine_similarity(query_vec, mem_vec) if mem_vec else 0
        except Exception:
            sim = 0
        # Score = similarité * (importance / 5)
        score = sim * (r['importance'] / 5.0)
        scored.append({
            'id': r['id'],
            'type': r['type'],
            'content': r['content'],
            'importance': r['importance'],
            'created_at': r['created_at'],
            'score': round(score, 4),
            'similarity': round(sim, 4),
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    results = scored[:limit]

    # Mettre à jour last_accessed et access_count pour les résultats
    for r in results:
        cur.execute(
            "UPDATE blobby_memories SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1 WHERE id = ?",
            (r['id'],)
        )
    conn.commit()
    conn.close()

    return jsonify({'results': results, 'mode': 'semantic'})


@app.route('/api/blobby/memory/list', methods=['GET'])
def blobby_memory_list():
    """Liste tous les souvenirs (debug)."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    mem_type = request.args.get('type', '').strip()

    conn = get_db()
    cur = conn.cursor()
    sql = "SELECT id, type, content, importance, created_at, last_accessed, access_count FROM blobby_memories WHERE user_id = ?"
    params = [user_id]
    if mem_type:
        sql += " AND type = ?"
        params.append(mem_type)
    sql += " ORDER BY created_at DESC"
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({'memories': rows, 'count': len(rows)})


@app.route('/api/blobby/memory/<int:mem_id>', methods=['DELETE'])
def blobby_memory_delete(mem_id):
    """Supprime un souvenir."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM blobby_memories WHERE id = ? AND user_id = ?", (mem_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/blobby/memory/forget', methods=['POST'])
def blobby_memory_forget():
    """Tout oublier (reset complet)."""
    guard = _login_required()
    if guard: return guard
    user_id = _get_current_user_id()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM blobby_memories WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'deleted': cur.rowcount})
