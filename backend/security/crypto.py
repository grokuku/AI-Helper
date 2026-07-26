"""Encryption utilities for API keys stored in the database."""

import os
import sqlite3

from cryptography.fernet import Fernet

from extensions import DB_PATH


def _get_encryption_key():
    """Récupère ou génère la clé de chiffrement Fernet.

    Priorité : variable d'environnement ``ENCRYPTION_KEY`` (plus sûre),
    puis la BDD (``app_settings``), avec génération auto si absente.

    Returns:
        Fernet: Une instance ``Fernet`` prête à chiffrer/déchiffrer.

    Raises:
        ValueError: Si la clé stockée est invalide pour Fernet.
    """
    env_key = os.environ.get("ENCRYPTION_KEY")
    if env_key:
        return Fernet(env_key.encode())
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute("SELECT value FROM app_settings WHERE key = 'encryption_key'")
    row = cur.fetchone()
    conn.close()
    key = row[0] if row else None
    if not key:
        key = Fernet.generate_key().decode()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ('encryption_key', key),
        )
        conn.commit()
        conn.close()
    return Fernet(key.encode())


def encrypt_api_key(plain):
    """Chiffre une clé API en clair avec Fernet.

    Args:
        plain (str): La clé API en clair à chiffrer.

    Returns:
        str: La clé chiffrée (token Fernet), ou une chaîne vide si ``plain`` est vide.
    """
    if not plain:
        return ''
    return _get_encryption_key().encrypt(plain.encode()).decode()


def decrypt_api_key(encrypted):
    """Déchiffre une clé API chiffrée avec Fernet.

    Args:
        encrypted (str): La clé chiffrée (token Fernet).

    Returns:
        str: La clé en clair, ou une chaîne vide si ``encrypted`` est vide.

    Raises:
        cryptography.fernet.InvalidToken: Si le token est invalide ou corrompu.
    """
    if not encrypted:
        return ''
    return _get_encryption_key().decrypt(encrypted.encode()).decode()