"""Database package: connection, init, embeddings."""

import sqlite3

from extensions import DB_PATH


def get_db():
    """Ouvre une connexion SQLite configurée (WAL, foreign keys, row_factory).

    Returns:
        sqlite3.Connection: Une connexion SQLite avec ``row_factory = Row``,
            WAL activé, foreign keys ON, et busy_timeout de 30 s.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _row_get(row, key, default=None):
    """Récupère une valeur d'un ``sqlite3.Row`` de façon sécurisée.

    Les objets ``sqlite3.Row`` ne supportent pas la méthode ``.get()`` ;
    cette fonction émule ce comportement.

    Args:
        row (sqlite3.Row): La ligne de résultat SQLite.
        key (str): La clé (nom de colonne) à récupérer.
        default: La valeur par défaut si la clé est absente ou ``None``.

    Returns:
        La valeur de la colonne ou ``default`` si introuvable.
    """
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError):
        return default