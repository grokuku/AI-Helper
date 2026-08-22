"""Routes sync for AI-Helper backend.

Synchronisation client <-> serveur :

- GET /api/sync/manifest : version + timestamps de chaque collection syncable.
- GET /api/sync/export  : export paginé (scope PUBLIC + MINE).
- POST /api/sync/apply  : application des écritures locales (outbox) avec
  résolution de conflits (base_version vs version serveur, dernier-écrit-gagne,
  tombstones pour delete, whitelist stricte des colonnes par introspection).

Contrats :
- ``schema_version`` = 3.
- Chaque ligne exportée est enrichie de ``client_id`` (uuid4 hex, persisté
  via ``UPDATE`` quand la colonne existe, sinon stable par requête),
  ``version`` (1 si absent), ``updated_at`` (ISO8601 UTC µs) et ``deleted`` (0).
- Les champs sensibles (ai_presets, api_key_encrypted, keyword_embeddings)
  ne sont JAMAIS exportés.
"""

import uuid
import base64
import hashlib
from datetime import datetime, timezone

from context import *


# ── Constantes ─────────────────────────────────────────────────────────

SCHEMA_VERSION = 3
DEFAULT_LIMIT = 500
MAX_LIMIT = 500

# Collections exportables, dans l'ordre du manifeste.
SYNC_COLLECTIONS = [
    "keywords",
    "saved_filters",
    "elements_presets",
    "styles",
    "prompt_templates",
]

# Colonnes toujours exclues de l'export (secrets / embeddings / presets LLM).
SENSITIVE_COLUMNS = {
    "ai_presets",
    "api_key",
    "api_key_encrypted",
    "keyword_embeddings",
    "embedding",
}

# Mapping nom de collection (entity_type) -> table SQL.
# Identité pour les 5 collections syncables, mais gardé explicite pour
# permettre d'éventuels alias futurs.
SYNC_ENTITY_TABLES = {
    "keywords": "keywords",
    "saved_filters": "saved_filters",
    "styles": "styles",
    "prompt_templates": "prompt_templates",
    "elements_presets": "elements_presets",
}

# Colonnes toujours protégées : jamais écrites depuis le payload client.
# ``updated_at`` reste éditable via la whitelist mais est toujours forcé à
# ``_iso_now()`` par l'endpoint apply (jamais pris tel quel du payload).
PROTECTED_SYNC_COLUMNS = {
    "id",
    "client_id",
    "user_id",
    "version",
    "sync_state",
    "deleted",
    "created_at",
}


# ── Helpers temps / dates ──────────────────────────────────────────────

def _iso_now():
    """Timestamp courant ISO8601 UTC avec microsecondes."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(v):
    """Parse une date ISO8601 (ou 'YYYY-MM-DD HH:MM:SS') → datetime UTC.

    Returns:
        datetime|None: datetime timezone-aware UTC, ou None si illisible.
    """
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_iso(v):
    """Convertit une date SQLite ('YYYY-MM-DD HH:MM:SS') ou NULL en ISO8601 UTC µs.

    Returns:
        str: Timestamp ISO8601 UTC avec microsecondes (now si ``v`` est vide).
    """
    if not v:
        return _iso_now()
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat()
    dt = _parse_iso(v)
    if dt is None:
        # Non parsable : renvoyer tel quel (probablement déjà ISO).
        return str(v)
    return dt.isoformat()


def _db_ts(v):
    """Convertit une date ISO8601 en représentation SQLite UTC pour comparaison.

    Returns:
        str|None: 'YYYY-MM-DD HH:MM:SS' (UTC), ou None si illisible.
    """
    dt = _parse_iso(v)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Helpers DB ─────────────────────────────────────────────────────────

def _table_columns(conn, table):
    """Retourne l'ensemble des colonnes réelles d'une table."""
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _whitelist_cols(conn, table):
    """Colonnes éditables d'une table (whitelist stricte).

    Introspection ``PRAGMA table_info`` ; exclut les colonnes protégées
    (``id``, ``client_id``, ``user_id``, ``version``, ``sync_state``,
    ``deleted``, ``created_at``). ``updated_at`` reste dans la whitelist
    mais est toujours forcé à ``_iso_now()`` par l'endpoint apply — la valeur
    envoyée par le client est ignorée.

    Args:
        conn (sqlite3.Connection): Connexion ouverte.
        table (str): Nom de la table.

    Returns:
        list[str]: Colonnes éditables, dans l'ordre de ``PRAGMA``.
    """
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []
    return [c for c in cols if c not in PROTECTED_SYNC_COLUMNS]


def _get_user_info():
    """Infos utilisateur pour le payload de sync.

    Returns:
        dict: ``{id, display_name, api_key_fingerprint, role}``.
    """
    user_id = _get_current_user_id()
    info = {
        "id": user_id,
        "display_name": None,
        "api_key_fingerprint": None,
        "role": "user",
    }
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT display_name, role FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            info["display_name"] = row["display_name"]
            if row["role"]:
                info["role"] = row["role"]
    finally:
        conn.close()

    auth = (request.headers.get("Authorization") or "").strip()
    if auth:
        info["api_key_fingerprint"] = hashlib.sha256(auth.encode("utf-8")).hexdigest()[:12]
    return info


def _serialize(collection, rows, conn):
    """Convertit chaque ``sqlite3.Row`` en dict de synchronisation.

    Enrichit chaque ligne avec :
    - ``client_id`` : uuid4 hex généré + persisté via UPDATE si la colonne
      ``client_id`` existe, sinon généré de façon stable par requête ;
    - ``version``   : valeur de la colonne, ou 1 si absente ;
    - ``updated_at``: ISO8601 UTC µs (colonne updated_at puis created_at, sinon now) ;
    - ``deleted``   : 0.

    Exclut les colonnes sensibles (ai_presets / api_key / embeddings).

    Args:
        collection (str): Nom de la table.
        rows (list[sqlite3.Row]): Lignes brutes à sérialiser.
        conn (sqlite3.Connection): Connexion ouverte (pour la persistance).

    Returns:
        list[dict]: Lignes sérialisées.
    """
    if not rows:
        return []

    cols = _table_columns(conn, collection)
    has_client_col = "client_id" in cols
    cache = getattr(g, "_sync_client_cache", None)
    if cache is None:
        cache = {}
        g._sync_client_cache = cache

    out = []
    for row in rows:
        d = dict(row)
        row_id = d.get("id")
        # Clé de cache : (collection, row_id) pour éviter les collisions entre tables.
        cache_key = (collection, row_id)

        # client_id : réutiliser la colonne si présente, sinon générer.
        #  - Colonne ``client_id`` existante : uuid4 hex généré + persisté via UPDATE.
        #  - Colonne absente (schéma actuel) : ID hex dérivé de (collection, row_id)
        #    pour rester stable entre les pages / requêtes d'un même objet.
        cid = d.get("client_id")
        if not cid:
            cid = cache.get(cache_key)
            if not cid:
                if has_client_col:
                    cid = uuid.uuid4().hex
                else:
                    cid = hashlib.md5(
                        f"{collection}:{row_id}".encode("utf-8")
                    ).hexdigest()
                cache[cache_key] = cid
                if has_client_col:
                    conn.execute(
                        f"UPDATE {collection} SET client_id = ? WHERE id = ?",
                        (cid, row_id),
                    )

        item = {
            "id": row_id,
            "client_id": cid,
            "version": d.get("version") or 1,
            "updated_at": _to_iso(d.get("updated_at") or d.get("created_at")),
            "deleted": 0,
        }
        for k, v in d.items():
            if k in SENSITIVE_COLUMNS or k in ("id", "client_id", "version", "updated_at", "deleted"):
                continue
            item[k] = v
        out.append(item)
    return out


# ── Endpoint 1 : manifeste ─────────────────────────────────────────────

@app.route('/api/sync/manifest', methods=['GET'])
def sync_manifest():
    """Manifeste de synchronisation : version + timestamps par collection.

    Returns:
        flask.Response: JSON ``{"schema_version", "server_time", "user", "collections"}``.
    """
    guard = _login_required()
    if guard:
        return guard

    conn = get_db()
    try:
        collections = {}
        for name in SYNC_COLLECTIONS:
            cols = _table_columns(conn, name)
            ts_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else None)
            if ts_col:
                cur = conn.execute(f"SELECT COUNT(*) AS c, MAX({ts_col}) AS ts FROM {name}")
            else:
                cur = conn.execute(f"SELECT COUNT(*) AS c, NULL AS ts FROM {name}")
            row = cur.fetchone()
            count = row["c"] if row else 0
            ts = row["ts"] if row else None
            collections[name] = {
                "version": count,
                "updated_at": _to_iso(ts),
            }
    finally:
        conn.close()

    return jsonify({
        "schema_version": SCHEMA_VERSION,
        "server_time": _iso_now(),
        "user": _get_user_info(),
        "collections": collections,
    })


# ── Endpoint 2 : export ────────────────────────────────────────────────

def _decode_cursor(cursor):
    """Décode un curseur base64 ``"<updated_at>|<id>"``.

    Returns:
        tuple: ``(updated_at_str, id)`` ou ``(None, None)`` si invalide.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts, id_str = raw.rsplit("|", 1)
        return ts, int(id_str)
    except Exception:
        return None, None


def _scope_sql(name, user_id, cols):
    """Clause WHERE du scope PUBLIC + MINE pour une collection.

    keywords          : via ``_privacy_filter(user_id)`` (public + les siens).
    autres collections: ``(is_public = 1 OR user_id = ?)`` si colonne présente,
                        sinon ``user_id = ?`` (ex: elements_presets sans is_public).

    Returns:
        tuple: ``(clause, params)``.
    """
    if name == "keywords":
        # _privacy_filter gère déjà (privacy_status = 'public' OR user_id = ?)
        # pour un user normal, et inclut aussi les public_pending pour les éditeurs.
        return _privacy_filter(user_id)
    if "is_public" in cols:
        return "(t.is_public = 1 OR t.user_id = ?)", [user_id]
    return "t.user_id = ?", [user_id]


def _export_collection(conn, name, user_id, since_db, cursor_vals, limit):
    """Exporte une collection (scope PUBLIC+MINE, filtre since, pagination curseur).

    Args:
        conn (sqlite3.Connection): Connexion ouverte.
        name (str): Nom de la collection.
        user_id (str): ID de l'utilisateur courant.
        since_db (str|None): Timestamp SQLite UTC pour le filtre incrémental.
        cursor_vals (tuple): ``(updated_at_str, id)`` du curseur, ou ``(None, None)``.
        limit (int): Nombre max de lignes (<= 500).

    Returns:
        tuple: ``(rows, ts_col, has_more)``.
    """
    cols = _table_columns(conn, name)
    ts_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else None)
    if ts_col is None:
        return [], None, False

    if name == "keywords":
        from_sql = "keywords k"
        alias = "k"
        scope_clause, scope_params = _privacy_filter(user_id)
    else:
        from_sql = f"{name} t"
        alias = "t"
        scope_clause, scope_params = _scope_sql(name, user_id, cols)

    conditions = [f"({scope_clause})"]
    params = list(scope_params)

    if since_db:
        if "deleted" in cols:
            conditions.append(f"({alias}.{ts_col} > ? OR {alias}.deleted = 1)")
        else:
            conditions.append(f"{alias}.{ts_col} > ?")
        params.append(since_db)

    c_ts, c_id = cursor_vals
    if c_ts is not None:
        conditions.append(
            f"({alias}.{ts_col} > ? OR ({alias}.{ts_col} = ? AND {alias}.id > ?))"
        )
        params.extend([c_ts, c_ts, c_id])

    sql = (
        f"SELECT * FROM {from_sql} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY {alias}.{ts_col}, {alias}.id "
        f"LIMIT ?"
    )
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()
    has_more = len(rows) > limit
    return rows[:limit], ts_col, has_more


@app.route('/api/sync/export', methods=['GET'])
def sync_export():
    """Export paginé des collections (scope PUBLIC + MINE).

    Query params:
        collections (str): liste CSV de noms de collections (défaut: toutes).
        since (str): ISO8601 — lignes dont updated_at > since (ou deleted=1).
        cursor (str): curseur base64 ``"<updated_at>|<id>"`` pour la pagination.
        limit (int): nombre max de lignes par collection (<= 500).

    Returns:
        flask.Response: JSON ``{"schema_version", "user", "collections",
                                "has_more", "next_cursor"}``.
    """
    guard = _login_required()
    if guard:
        return guard

    user_id = _get_current_user_id()

    collections_param = (request.args.get('collections') or '').strip()
    if collections_param:
        names = [c.strip() for c in collections_param.split(',') if c.strip()]
        names = [c for c in names if c in SYNC_COLLECTIONS]
    else:
        names = list(SYNC_COLLECTIONS)

    since_raw = (request.args.get('since') or '').strip()
    since_db = _db_ts(since_raw) if since_raw else None
    if since_raw and since_db is None:
        return jsonify({'error': 'paramètre since invalide (ISO8601 attendu)'}), 400

    cursor_raw = (request.args.get('cursor') or '').strip()
    cursor_vals = _decode_cursor(cursor_raw) if cursor_raw else (None, None)
    if cursor_raw and cursor_vals[0] is None:
        return jsonify({'error': 'paramètre cursor invalide'}), 400

    try:
        limit = int(request.args.get('limit', DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    conn = get_db()
    result = {}
    has_more = False
    next_cursor = None
    try:
        for name in names:
            rows, ts_col, more = _export_collection(
                conn, name, user_id, since_db, cursor_vals, limit
            )
            result[name] = _serialize(name, rows, conn)
            if more:
                has_more = True
                if rows:
                    last = rows[-1]
                    raw_ts = last[ts_col] or ""
                    next_cursor = base64.urlsafe_b64encode(
                        f"{raw_ts}|{last['id']}".encode("utf-8")
                    ).decode("ascii")
        # Persister les éventuels client_id générés (no-op si colonne absente).
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "schema_version": SCHEMA_VERSION,
        "user": _get_user_info(),
        "collections": result,
        "has_more": has_more,
        "next_cursor": next_cursor,
    })


# ── Endpoint 3 : apply (outbox locale) ─────────────────────────────────

def _find_sync_row(conn, table, user_id, client_id):
    """Retrouve la row serveur par ``(user_id, client_id)``.

    Returns:
        sqlite3.Row|None: La row si trouvée, sinon ``None``.
    """
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
    except Exception:
        return None


def _find_constraint_conflict(conn, table, user_id, payload, row_id=None):
    """Retrouve la row en conflit sur une contrainte d'unicité.

    - ``keywords``        : ``UNIQUE(LOWER(keyword))`` (globale, insensible à la casse).
    - ``elements_presets``: ``UNIQUE(user_id, name)``.

    Args:
        conn (sqlite3.Connection): Connexion ouverte.
        table (str): Table concernée.
        user_id (str): Utilisateur courant.
        payload (dict): Payload de l'opération (contient la clé candidate).
        row_id (int|None): ID à exclure (``None`` pour create).

    Returns:
        sqlite3.Row|None: La row existante en conflit, sinon ``None``.
    """
    if table == "keywords":
        kw = payload.get("keyword")
        if kw:
            sql = "SELECT * FROM keywords WHERE LOWER(keyword) = LOWER(?)"
            params = [kw]
            if row_id is not None:
                sql += " AND id != ?"
                params.append(row_id)
            return conn.execute(sql, params).fetchone()
    elif table == "elements_presets":
        name = payload.get("name")
        if name:
            sql = "SELECT * FROM elements_presets WHERE user_id = ? AND name = ?"
            params = [user_id, name]
            if row_id is not None:
                sql += " AND id != ?"
                params.append(row_id)
            return conn.execute(sql, params).fetchone()
    return None


def _server_row_dict(table, row, conn):
    """Sérialise une row serveur pour le client (format export, ``deleted`` réel).

    Args:
        table (str): Table concernée.
        row (sqlite3.Row): Row brute.
        conn (sqlite3.Connection): Connexion ouverte.

    Returns:
        dict|None: Dict sérialisé (format export), ou ``None`` si ``row`` vide.
    """
    if row is None:
        return None
    d = dict(row)
    item = {
        "id": d.get("id"),
        "client_id": d.get("client_id"),
        "version": d.get("version") or 1,
        "updated_at": _to_iso(d.get("updated_at") or d.get("created_at")),
        "deleted": d.get("deleted") or 0,
    }
    for k, v in d.items():
        if k in SENSITIVE_COLUMNS or k in ("id", "client_id", "version", "updated_at", "deleted"):
            continue
        item[k] = v
    return item


def _conflict_result(op_id, message, conn, table, row):
    """Résultat ``conflict`` avec la row serveur que le client doit adopter."""
    sr = _server_row_dict(table, row, conn)
    return {
        "op_id": op_id,
        "status": "conflict",
        "message": message,
        "server_version": sr["version"] if sr else None,
        "server_updated_at": sr["updated_at"] if sr else None,
        "server_row": sr,
    }


def _apply_create(conn, table, user_id, client_id, payload, whitelist, op_id):
    """Crée une row (idempotent : si elle existe déjà → traité comme update).

    Les colonnes protégées du payload (client_id/user_id/version/sync_state/
    deleted/created_at) sont ignorées ; ``updated_at`` est forcé à ``_iso_now()``.
    Une violation d'unicité (keyword / elements_presets) → ``conflict`` + row serveur.
    """
    row = _find_sync_row(conn, table, user_id, client_id)
    if row is not None:
        # Idempotence : l'objet existe déjà côté serveur → update direct.
        return _apply_update(conn, table, row, user_id, client_id, payload,
                             whitelist, op_id, base_version=None,
                             client_updated_at=None, force=True)

    # Contrôle d'unicité applicatif (même règle que keywords.py) :
    # keywords global sur LOWER(keyword), elements_presets sur (user_id, name).
    conflict_row = _find_constraint_conflict(conn, table, user_id, payload)
    if conflict_row is not None:
        return _conflict_result(
            op_id, "contrainte d'unicité violée", conn, table, conflict_row
        )

    now = _iso_now()
    cols = [c for c in whitelist if c in payload and c != "updated_at"]

    insert_cols = ["client_id", "user_id", "version", "sync_state", "deleted"]
    insert_vals = [client_id, user_id, 1, "synced", 0]
    if "created_at" in _table_columns(conn, table):
        insert_cols.append("created_at")
        insert_vals.append(now)
    insert_cols.append("updated_at")
    insert_vals.append(now)
    insert_cols.extend(cols)
    insert_vals.extend(payload[c] for c in cols)

    sql = (
        f"INSERT INTO {table} ({', '.join(insert_cols)})"
        f" VALUES ({', '.join('?' for _ in insert_vals)})"
    )
    try:
        conn.execute(sql, insert_vals)
    except sqlite3.IntegrityError:
        conflict_row = _find_constraint_conflict(conn, table, user_id, payload)
        if conflict_row is not None:
            return _conflict_result(
                op_id, "contrainte d'unicité violée", conn, table, conflict_row
            )
        raise

    return {
        "op_id": op_id,
        "status": "applied",
        "server_version": 1,
        "server_updated_at": now,
    }


def _apply_update(conn, table, row, user_id, client_id, payload, whitelist,
                  op_id, base_version, client_updated_at, force=False):
    """Met à jour une row avec résolution de conflit.

    Règle : si ``base_version >= version serveur`` → applique (version+1).
    Sinon, dernier-écrit-gagne : le plus récent de ``client_updated_at`` vs
    ``updated_at`` serveur gagne (client → applique, serveur → conflict).
    """
    if row is None:
        # Row absente côté serveur : upsert (create avec le client_id demandé).
        return _apply_create(conn, table, user_id, client_id, payload, whitelist, op_id)

    server_version = int(row["version"] or 1)
    now = _iso_now()

    try:
        base = int(base_version) if base_version is not None else None
    except (TypeError, ValueError):
        base = None

    # Applicable sans conflit ?
    if not force:
        apply_now = base is not None and base >= server_version
        if not apply_now:
            # Conflit : dernier-écrit-gagne sur updated_at.
            client_dt = _parse_iso(client_updated_at)
            server_dt = _parse_iso(row["updated_at"] or row["created_at"])
            if client_dt is not None and (server_dt is None or client_dt > server_dt):
                apply_now = True
        if not apply_now:
            return _conflict_result(
                op_id, "conflit : la version serveur est plus récente",
                conn, table, row,
            )

    cols = [c for c in whitelist if c in payload and c != "updated_at"]
    # Contrôle d'unicité applicatif avant l'UPDATE (renommage → collision).
    conflict_row = _find_constraint_conflict(conn, table, user_id, payload, row_id=row["id"])
    if conflict_row is not None:
        return _conflict_result(
            op_id, "contrainte d'unicité violée", conn, table, conflict_row
        )

    sets = [f"{c} = ?" for c in cols]
    sets.append("version = version + 1")
    sets.append("updated_at = ?")
    params = [payload[c] for c in cols] + [now, row["id"]]
    sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?"
    try:
        conn.execute(sql, params)
    except sqlite3.IntegrityError:
        conflict_row = _find_constraint_conflict(
            conn, table, user_id, payload, row_id=row["id"]
        )
        if conflict_row is not None:
            return _conflict_result(
                op_id, "contrainte d'unicité violée", conn, table, conflict_row
            )
        raise

    return {
        "op_id": op_id,
        "status": "applied",
        "server_version": server_version + 1,
        "server_updated_at": now,
    }


def _apply_delete(conn, table, row, op_id):
    """Supprime par tombstone (``deleted=1``), jamais de DELETE physique."""
    if row is not None:
        now = _iso_now()
        conn.execute(
            f"UPDATE {table} SET deleted = 1, version = version + 1, updated_at = ?"
            f" WHERE id = ?",
            (now, row["id"]),
        )
        return {
            "op_id": op_id,
            "status": "applied",
            "server_version": int(row["version"] or 1) + 1,
            "server_updated_at": now,
        }
    # Row absente ou déjà supprimée : idempotent, rien à faire.
    return {"op_id": op_id, "status": "applied"}


def _apply_op(conn, user_id, op):
    """Applique une opération de l'outbox locale (create/update/delete)."""
    if not isinstance(op, dict):
        raise ValueError("op invalide : objet attendu")
    op_id = op.get("op_id")
    entity_type = (op.get("entity_type") or "").strip()
    entity_client_id = (op.get("entity_client_id") or "").strip()
    action = (op.get("op") or "").strip()
    payload = op.get("payload")
    base_version = op.get("base_version")
    client_updated_at = op.get("client_updated_at")

    table = SYNC_ENTITY_TABLES.get(entity_type)
    if table is None:
        raise ValueError(f"entity_type inconnu : {entity_type!r}")
    if not entity_client_id:
        raise ValueError("entity_client_id requis")
    if action not in ("create", "update", "delete"):
        raise ValueError(f"op inconnue : {action!r}")
    if not isinstance(payload, dict):
        payload = {}

    whitelist = _whitelist_cols(conn, table)
    row = _find_sync_row(conn, table, user_id, entity_client_id)

    if action == "create":
        return _apply_create(conn, table, user_id, entity_client_id,
                             payload, whitelist, op_id)
    if action == "update":
        return _apply_update(conn, table, row, user_id, entity_client_id,
                             payload, whitelist, op_id, base_version,
                             client_updated_at)
    return _apply_delete(conn, table, row, op_id)


@app.route('/api/sync/apply', methods=['POST'])
def sync_apply():
    """Applique les écritures locales de l'extension (outbox).

    Body JSON :
        ``{"client_id": str, "schema_version": int, "ops": [...]}`` avec
        chaque op = ``{op_id, entity_type, entity_client_id, op, payload,
                      base_version, client_updated_at}``.

    Résolution de conflits :
        - ``create``   : idempotent (existe déjà → update) ; violation de la
          contrainte d'unicité (UNIQUE(LOWER(keyword)), UNIQUE(user_id,name))
          → ``status='conflict'`` + ``server_row``.
        - ``update``   : ``base_version >= version serveur`` → applique
          (version+1). Sinon dernier-écrit-gagne sur ``updated_at`` : client
          gagne → applique, serveur gagne → ``conflict`` + ``server_row``.
        - ``delete``   : tombstone (``deleted=1``), jamais de DELETE physique.

    Returns:
        flask.Response: ``{"applied", "conflicts", "errors", "results"}``
        avec ``results = [{op_id, status, server_version, server_updated_at,
                            server_row?}]``.
    """
    guard = _login_required()
    if guard:
        return guard

    rl = _check_rate_limit("sync_apply")
    if rl:
        return rl

    mime = _require_json()
    if mime:
        return mime

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'corps JSON objet attendu'}), 422

    client_id = (data.get('client_id') or '').strip()
    schema_version = data.get('schema_version')
    ops = data.get('ops')

    if not client_id:
        return jsonify({'error': 'client_id requis'}), 422
    if schema_version != SCHEMA_VERSION:
        return jsonify({
            'error': f'schema_version attendu : {SCHEMA_VERSION} (reçu : {schema_version})',
            'schema_version': SCHEMA_VERSION,
        }), 409
    if not isinstance(ops, list) or not ops:
        return jsonify({'error': 'ops : liste non vide attendue'}), 422

    user_id = _get_current_user_id()
    conn = get_db()
    applied = 0
    conflicts = 0
    errors = 0
    results = []
    try:
        for op in ops:
            try:
                result = _apply_op(conn, user_id, op)
            except Exception as exc:  # noqa: BLE001 — une op ne bloque pas les autres
                errors += 1
                results.append({
                    "op_id": op.get("op_id") if isinstance(op, dict) else None,
                    "status": "error",
                    "message": str(exc)[:300],
                })
                continue
            results.append(result)
            status = result.get("status")
            if status == "applied":
                applied += 1
            elif status == "conflict":
                conflicts += 1
            else:
                errors += 1
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "applied": applied,
        "conflicts": conflicts,
        "errors": errors,
        "results": results,
    })
