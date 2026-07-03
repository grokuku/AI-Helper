"""
Storage abstraction layer for FR.IA backend.

Provides a unified interface for file storage that can be backed by:
  - LocalStorage : filesystem (default, no config needed)
  - SFTPStorage   : remote SFTP server (paramiko)

The backend reads SFTP_* env vars (or app_settings DB) at startup.
Clients never know where files are actually stored — they only talk
HTTP to the Flask backend.

Env vars:
  SFTP_HOST       — hostname or IP
  SFTP_PORT       — port (default 22)
  SFTP_USER       — username
  SFTP_PASSWORD   — password (or use SFTP_KEY_PATH)
  SFTP_KEY_PATH   — path to SSH private key
  SFTP_BASE_PATH  — base directory on the SFTP server (default /fria)
"""

import os
import logging
from pathlib import Path
from datetime import datetime


# ── Interface ────────────────────────────────────────────────────────

class StorageBackend:
    """Interface commune pour tous les backends de stockage."""

    def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload un fichier local vers le stockage distant."""
        raise NotImplementedError

    def download(self, remote_path: str, local_path: str) -> bool:
        """Download un fichier depuis le stockage vers un chemin local."""
        raise NotImplementedError

    def delete(self, remote_path: str) -> bool:
        """Supprime un fichier du stockage."""
        raise NotImplementedError

    def append_chunk(self, remote_path: str, data: bytes) -> bool:
        """Append un chunk de donnees a un fichier existant sur le stockage.
        Utile pour les uploads chunkees sans fichier temporaire local.
        Par defaut non implemente (utilise un fichier temporaire local)."""
        raise NotImplementedError

    def append_chunk_stream(self, remote_path: str, stream, buf_size: int = 65536) -> bool:
        """Append un stream vers un fichier distant, par petits buffers.
        Evite de charger tout le chunk en memoire (utile pour serveurs low-RAM).
        Par defaut: lit le stream par buf_size et appelle append_chunk."""
        try:
            while True:
                buf = stream.read(buf_size)
                if not buf:
                    break
                if not self.append_chunk(remote_path, buf):
                    return False
            return True
        except Exception:
            return False

    def create_empty(self, remote_path: str) -> bool:
        """Cree un fichier vide sur le stockage (pour init d'upload direct)."""
        raise NotImplementedError

    def exists(self, remote_path: str) -> bool:
        """Vérifie si un fichier existe."""
        raise NotImplementedError

    def list_dir(self, remote_dir: str) -> list:
        """Liste les fichiers dans un dossier."""
        raise NotImplementedError

    def get_backend_name(self) -> str:
        """Retourne le nom du backend (pour debug/admin)."""
        return "unknown"


# ── Local Storage ────────────────────────────────────────────────────

class LocalStorage(StorageBackend):
    """Stockage local sur le filesystem."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _full_path(self, remote_path: str) -> Path:
        return self.base_dir / remote_path

    def create_empty(self, remote_path: str) -> bool:
        """Cree un fichier vide en local."""
        try:
            dest = self._full_path(remote_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.touch()
            return True
        except Exception as e:
            logging.warning(f"[LocalStorage] create_empty failed: {e}")
            return False

    def append_chunk(self, remote_path: str, data: bytes) -> bool:
        """Append un chunk directement au fichier final (pas de temp file)."""
        try:
            dest = self._full_path(remote_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(str(dest), 'ab') as f:
                f.write(data)
            return True
        except Exception as e:
            logging.warning(f"[LocalStorage] append_chunk failed: {e}")
            return False

    def append_chunk_stream(self, remote_path: str, stream, buf_size: int = 65536) -> bool:
        """Stream direct vers le fichier local par petits buffers."""
        try:
            dest = self._full_path(remote_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(str(dest), 'ab') as f:
                while True:
                    buf = stream.read(buf_size)
                    if not buf:
                        break
                    f.write(buf)
            return True
        except Exception as e:
            logging.warning(f"[LocalStorage] append_chunk_stream failed: {e}")
            return False

    def upload(self, local_path: str, remote_path: str) -> bool:
        import shutil
        try:
            dest = self._full_path(remote_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(local_path, str(dest))
            return True
        except Exception as e:
            logging.exception(f"[LocalStorage] upload failed: {e}")
            return False

    def download(self, remote_path: str, local_path: str) -> bool:
        import shutil
        try:
            src = self._full_path(remote_path)
            if not src.exists():
                return False
            shutil.copy(str(src), local_path)
            return True
        except Exception as e:
            logging.exception(f"[LocalStorage] download failed: {e}")
            return False

    def delete(self, remote_path: str) -> bool:
        try:
            p = self._full_path(remote_path)
            if p.exists():
                p.unlink()
                return True
            return False
        except Exception:
            return False

    def exists(self, remote_path: str) -> bool:
        return self._full_path(remote_path).exists()

    def list_dir(self, remote_dir: str) -> list:
        d = self._full_path(remote_dir)
        if not d.exists():
            return []
        return [f.name for f in d.iterdir() if f.is_file()]

    def get_backend_name(self) -> str:
        return "local"


# ── SFTP Storage ─────────────────────────────────────────────────────

class SFTPStorage(StorageBackend):
    """Stockage SFTP via paramiko (connexion lazy, réutilisée)."""

    def __init__(self, host: str, port: int = 22, user: str = "",
                 password: str = None, key_path: str = None,
                 base_path: str = "/fria"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.base_path = base_path.rstrip("/")
        self._ssh = None
        self._sftp = None

    def _connect(self):
        """Ouvre la connexion SFTP si pas déjà active."""
        if self._sftp:
            try:
                # Vérifier que la connexion est encore vivante
                self._sftp.stat(".")
                return self._sftp
            except Exception:
                # Connexion morte, on la reconnecte
                try:
                    self._sftp.close()
                except Exception:
                    pass
                self._sftp = None
                self._ssh = None

        import paramiko
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if self.key_path:
            self._ssh.connect(self.host, port=self.port,
                              username=self.user, key_filename=self.key_path)
        else:
            self._ssh.connect(self.host, port=self.port,
                              username=self.user, password=self.password)
        self._sftp = self._ssh.open_sftp()
        logging.info(f"[SFTP] Connected to {self.host}:{self.port} as {self.user}")
        return self._sftp

    def _full_path(self, remote_path: str) -> str:
        """Retourne le chemin absolu sur le serveur SFTP."""
        if remote_path.startswith("/"):
            return remote_path
        return f"{self.base_path}/{remote_path}"

    def _mkdir_p(self, sftp, remote_dir: str):
        """Crée les dossiers parents récursivement (like mkdir -p)."""
        dirs_to_create = []
        current = remote_dir
        while current and current != "/" and current != self.base_path:
            try:
                sftp.stat(current)
                break  # existe déjà
            except IOError:
                dirs_to_create.append(current)
                current = "/".join(current.split("/")[:-1])
        for d in reversed(dirs_to_create):
            try:
                sftp.mkdir(d)
            except Exception:
                pass  # race condition possible, on ignore

    def create_empty(self, remote_path: str) -> bool:
        """Cree un fichier vide sur le SFTP."""
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            self._mkdir_p(sftp, "/".join(full.split("/")[:-1]))
            with sftp.open(full, 'wb') as f:
                pass  # fichier vide
            return True
        except Exception as e:
            logging.exception(f"[SFTP] create_empty failed: {e}")
            return False

    def append_chunk(self, remote_path: str, data: bytes) -> bool:
        """Append un chunk directement sur le fichier SFTP (pas de temp local)."""
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            with sftp.open(full, 'ab') as f:
                f.write(data)
            return True
        except Exception as e:
            logging.exception(f"[SFTP] append_chunk failed: {e}")
            return False

    def append_chunk_stream(self, remote_path: str, stream, buf_size: int = 65536) -> bool:
        """Stream vers SFTP par buffers de 1MB avec pipelining.
        Paramiko ecrit par chunks de ~32KB par defaut (un ACK par chunk),
        ce qui est tres lent. On passe a 1MB + pipelining."""
        try:
            sftp = self._connect()
            # Buffer interne paramiko plus gros (2MB au lieu de 32KB)
            sftp.sftp_chunk_size = 2 * 1024 * 1024
            full = self._full_path(remote_path)
            with sftp.open(full, 'ab') as f:
                f.set_pipelined(True)
                write_buf = 1024 * 1024
                while True:
                    buf = stream.read(write_buf)
                    if not buf:
                        break
                    f.write(buf)
            return True
        except Exception as e:
            logging.exception(f"[SFTP] append_chunk_stream failed: {e}")
            return False

    def upload(self, local_path: str, remote_path: str) -> bool:
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            self._mkdir_p(sftp, "/".join(full.split("/")[:-1]))
            sftp.put(local_path, full)
            logging.info(f"[SFTP] Uploaded {local_path} → {full}")
            return True
        except Exception as e:
            logging.exception(f"[SFTP] upload failed: {e}")
            return False

    def download(self, remote_path: str, local_path: str) -> bool:
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            sftp.get(full, local_path)
            return True
        except Exception as e:
            logging.exception(f"[SFTP] download failed: {e}")
            return False

    def delete(self, remote_path: str) -> bool:
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            sftp.remove(full)
            return True
        except Exception:
            return False

    def exists(self, remote_path: str) -> bool:
        try:
            sftp = self._connect()
            full = self._full_path(remote_path)
            sftp.stat(full)
            return True
        except Exception:
            return False

    def list_dir(self, remote_dir: str) -> list:
        try:
            sftp = self._connect()
            full = self._full_path(remote_dir)
            return sftp.listdir(full)
        except Exception:
            return []

    def get_backend_name(self) -> str:
        return f"sftp://{self.host}:{self.port}{self.base_path}"

    def close(self):
        """Ferme proprement la connexion."""
        try:
            if self._sftp:
                self._sftp.close()
            if self._ssh:
                self._ssh.close()
        except Exception:
            pass
        finally:
            self._sftp = None
            self._ssh = None


# ── Factory ──────────────────────────────────────────────────────────

_storage_instance = None


def get_storage() -> StorageBackend:
    """
    Retourne l'instance du backend de stockage (singleton).
    Lit la configuration depuis la BDD (app_settings), puis fallback sur env vars.
    Fallback sur LocalStorage si SFTP non configuré.
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    # Priorité 1 : config BDD (app_settings)
    sftp_host = None
    sftp_port = 22
    sftp_user = ""
    sftp_password = None
    sftp_base_path = "/fria"

    try:
        import sqlite3
        from extensions import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        for key in ('sftp_host', 'sftp_port', 'sftp_user', 'sftp_password', 'sftp_base_path'):
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            if row and row[0]:
                if key == 'sftp_port':
                    sftp_port = int(row[0])
                elif key == 'sftp_password':
                    sftp_password = row[0]
                else:
                    val = row[0]
                    if key == 'sftp_host': sftp_host = val
                    elif key == 'sftp_user': sftp_user = val
                    elif key == 'sftp_base_path': sftp_base_path = val
        conn.close()
    except Exception:
        pass  # BDD pas encore initialisée → fallback env vars

    # Priorité 2 : variables d'environnement (si BDD vide)
    if not sftp_host:
        sftp_host = os.environ.get("SFTP_HOST")
        if sftp_host:
            sftp_port = int(os.environ.get("SFTP_PORT", "22"))
            sftp_user = os.environ.get("SFTP_USER", "")
            sftp_password = os.environ.get("SFTP_PASSWORD")
            sftp_base_path = os.environ.get("SFTP_BASE_PATH", "/fria")

    if sftp_host:
        _storage_instance = SFTPStorage(
            host=sftp_host, port=sftp_port, user=sftp_user,
            password=sftp_password, base_path=sftp_base_path,
        )
        logging.info(f"[Storage] Using SFTP backend: {sftp_host}")
    else:
        # Fallback : stockage local
        from extensions import BASE_DIR
        local_dir = str(BASE_DIR / "uploads")
        _storage_instance = LocalStorage(local_dir)
        logging.info(f"[Storage] Using local backend: {local_dir}")

    return _storage_instance


def reload_storage():
    """Force la relecture de la config (utile après changement admin)."""
    global _storage_instance
    if _storage_instance and hasattr(_storage_instance, 'close'):
        _storage_instance.close()
    _storage_instance = None
    return get_storage()


# ── Backup ───────────────────────────────────────────────────────────

def backup_database(db_path: str, max_backups: int = 7) -> bool:
    """
    Export la BDD SQLite vers le stockage distant.
    Garde les max_backups derniers backups (rotation).

    Utilise VACUUM INTO pour un snapshot cohérent sans verrouiller la BDD.
    """
    import sqlite3
    import tempfile

    storage = get_storage()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    local_tmp = tempfile.mktemp(suffix=f"_{timestamp}.db")

    try:
        # Snapshot cohérent de la BDD
        conn = sqlite3.connect(db_path)
        conn.execute(f"VACUUM INTO '{local_tmp}'")
        conn.close()

        if not os.path.exists(local_tmp):
            logging.error("[backup] VACUUM INTO failed, no file created")
            return False

        remote_path = f"backups/keywords_{timestamp}.db"
        success = storage.upload(local_tmp, remote_path)

        if success:
            logging.info(f"[backup] Uploaded to {remote_path}")
            # Rotation : supprimer les vieux backups
            _rotate_backups(storage, max_backups)
        else:
            logging.error("[backup] Upload failed")

        return success
    except Exception as e:
        logging.exception(f"[backup] failed: {e}")
        return False
    finally:
        if os.path.exists(local_tmp):
            os.remove(local_tmp)


def _rotate_backups(storage: StorageBackend, max_backups: int):
    """Supprime les backups les plus anciens au-delà de max_backups."""
    try:
        files = storage.list_dir("backups")
        # Filtrer les fichiers de backup (format: keywords_YYYY-MM-DD_HHMMSS.db)
        backups = sorted([f for f in files if f.startswith("keywords_") and f.endswith(".db")])
        while len(backups) > max_backups:
            old = backups.pop(0)
            storage.delete(f"backups/{old}")
            logging.info(f"[backup] Rotated old backup: {old}")
    except Exception as e:
        logging.warning(f"[backup] rotation failed: {e}")


def start_backup_scheduler(db_path: str, interval_hours: int = 24):
    """
    Démarre un thread daemon qui backup la BDD toutes les interval_hours heures.
    À appeler au démarrage de l'app Flask.
    """
    import threading

    def _run():
        import time
        while True:
            time.sleep(interval_hours * 3600)
            try:
                backup_database(db_path)
            except Exception:
                logging.exception("[backup] scheduled backup failed")

    t = threading.Thread(target=_run, daemon=True, name="fria-backup")
    t.start()
    logging.info(f"[backup] Scheduler started (every {interval_hours}h)")