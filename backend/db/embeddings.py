"""Embedding generation and Ollama configuration helpers."""

import os
import json

from db import get_db
from embeddings import generate_embedding


def _regenerate_keyword_embedding(keyword_id: int):
    """Régénère l'embedding vectoriel pour un mot-clé spécifique.

    Lit le mot-clé, génère un embedding via le provider configuré
    (Ollama/Gemini), puis le stocke en BDD.

    Args:
        keyword_id (int): L'ID du mot-clé dans la table ``keywords``.
    """
    conn = None
    try:
        # Phase 1: read the keyword text, then CLOSE the connection
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT keyword, description FROM keywords WHERE id = ?", (keyword_id,))
        row = cur.fetchone()
        conn.close()
        conn = None
        if not row:
            return
        text = f"{row['keyword']}: {row['description']}"
        # Phase 2: slow HTTP call to Ollama/Gemini — NO DB connection held
        vec = generate_embedding(text)
        if not vec:
            return
        # Phase 3: open a NEW connection just for the write
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO keyword_embeddings (keyword_id, embedding) VALUES (?, ?)",
            (keyword_id, json.dumps(vec)),
        )
        conn.commit()
    except Exception as e:
        print(f"[_regenerate_keyword_embedding] Erreur: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _get_ollama_config() -> dict:
    """Lit la configuration Ollama depuis la BDD ou les variables d'env.

    Returns:
        dict: Un dictionnaire avec les clés ``"url"`` et ``"model"``.
    """
    config = {
        "url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        "model": os.environ.get("OLLAMA_MODEL", "nomic-embed-text"),
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        for key in ("ollama_url", "ollama_model"):
            cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            if row:
                config_key = key.replace("ollama_", "")
                config[config_key] = row["value"]
        conn.close()
    except Exception as e:
        print(f"[_get_ollama_config] Erreur: {e}")
    return config


def _generate_all_embeddings(conn):
    """Génère et stocke les embeddings pour tous les mots-clés.

    Supprime les embeddings existants puis régénère tout.

    Warning:
        Cette fonction est séquentielle et bloquante. Pour 500+ mots-clés,
        elle peut prendre 50+ secondes. Envisager un traitement asynchrone
        ou le batch de l'API Ollama pour les grands jeux de données.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cur = conn.cursor()
    cur.execute("SELECT id, keyword, description FROM keywords")
    rows = cur.fetchall()
    if not rows:
        return

    cur.execute("DELETE FROM keyword_embeddings")
    data = []
    for row in rows:
        text = f"{row['keyword']}: {row['description']}"
        vec = generate_embedding(text)
        data.append((row['id'], json.dumps(vec)))

    cur.executemany(
        "INSERT INTO keyword_embeddings (keyword_id, embedding) VALUES (?, ?)",
        data,
    )
    conn.commit()