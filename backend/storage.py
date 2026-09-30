"""
Storage abstraction layer for AI-Helper backend.

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
  SFTP_BASE_PATH  — base directory on the SFTP server (default /aih)
  SFTP_TIMEOUT    — socket/connect timeout in seconds (default 30)
  SFTP_DOWNLOAD_CONNECTIONS — canaux SFTP parallèles pour download (défaut 2)
"""

import contextlib
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

# ── Timeouts SFTP (anti-blocage) ─────────────────────────────────────
# Sans timeout, une connexion ou une opération SFTP peut pendre
# INDÉFINIMENT (hôte injoignable, réseau qui stalle). Comme le serveur
# Flask est threadé, la requête cliente reste alors bloquée ET — le canal
# SFTP étant partagé — TOUTES les autres opérations de stockage (vignettes,
# uploads ComfyUI, downloads) pendent derrière elle : galerie « figée ».
# Surchargeable via la variable d'environnement ``SFTP_TIMEOUT`` (secondes).
SFTP_TIMEOUT = float(os.environ.get("SFTP_TIMEOUT", "30"))

# Nombre de canaux SFTP de TÉLÉCHARGEMENT utilisables en parallèle (sur la MÊME
# connexion SSH: paramiko multiplexe les canaux). Un canal SFTP n'est pas
# thread-safe, donc chaque canal du pool est utilisé par un seul thread à la
# fois ; au-delà du plafond, on attend ou on retombe sur le canal principal
# sérialisé. 1 = un seul téléchargement à la fois (comportement historique),
# 2+ = les gros downloads (worker d'album) n'immobilisent plus toute la pile
# de stockage. Surchargeable via ``SFTP_DOWNLOAD_CONNECTIONS``.
try:
    SFTP_DOWNLOAD_CONNECTIONS = max(1, min(8, int(os.environ.get("SFTP_DOWNLOAD_CONNECTIONS", "2"))))
except (TypeError, ValueError):
    SFTP_DOWNLOAD_CONNECTIONS = 2

# ── Interface ────────────────────────────────────────────────────────

class StorageBackend:
    """Interface commune pour tous les backends de stockage."""

    def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload un fichier local vers le stockage distant."""
        raise NotImplementedError

    def download(self, remote_path: str, local_path: str) -> bool:
        """Download un fichier depuis le stockage vers un chemin local."""
        raise NotImplementedError

    def open_stream(self, remote_path: str):
        """Ouvre un flux de LECTURE séquentiel sur ``remote_path`` (sans
        matérialiser le fichier complet).

        Retourne un objet avec ``read(n) -> bytes`` (b'' en fin de fichier) et
        ``close()``, ou ``None`` si ce backend ne sait pas streamer. L'appelant
        DOIT fermer le flux (``finally`` / ``contextlib``) : pour SFTP, la
        fermeture rend le canal dédié au pool.

        Par défaut : pas de streaming → l'appelant retombe sur ``download``
        (fichier temporaire complet, comportement historique).
        """
        return None

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

    def open_stream(self, remote_path: str):
        """Flux de lecture direct sur le fichier local (pas de copie préalable)."""
        try:
            return open(str(self._full_path(remote_path)), 'rb')
        except OSError as e:
            logging.warning(f"[LocalStorage] open_stream failed: {e}")
            return None

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
                 base_path: str = "/aih"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.base_path = base_path.rstrip("/")
        self._ssh = None
        self._sftp = None
        self._open_handles = {}  # chemin_distant → Handle SFTP ouvert en append
        # paramiko SFTPClient n'est PAS thread-safe : le canal partagé est
        # sérialisé par ce verrou (réentrant, car _connect le reprend).
        self._lock = threading.RLock()
        # Pool de canaux SFTP dédiés aux TÉLÉCHARGEMENTS (le canal principal
        # `_sftp` reste réservé aux autres opérations, sérialisées par `_lock`).
        self._download_free = []       # canaux disponibles (réutilisables)
        self._download_created = 0     # canaux créés (en service OU libres)
        self._download_cond = threading.Condition()

    def _known_hosts_path(self) -> Path:
        """Fichier known_hosts persistant (TOFU).

        Par défaut à côté de la BDD (même volume en Docker → persistant).
        Surchargeable via ``SFTP_KNOWN_HOSTS``.
        """
        override = os.environ.get("SFTP_KNOWN_HOSTS")
        if override:
            return Path(override)
        try:
            from extensions import DB_PATH
            return Path(DB_PATH).parent / ".sftp_known_hosts"
        except Exception:
            return Path(__file__).resolve().parent / ".sftp_known_hosts"

    def _connect(self):
        """Ouvre la connexion SFTP si pas déjà active (délimitée dans le temps).

        Tient ``self._lock`` (réentrant) : un seul client SFTP est créé,
        et son canal est sérialisé pour tous les appelants (paramiko
        SFTPClient n'est pas thread-safe).
        """
        with self._lock:
            if self._sftp:
                try:
                    # Vérifier que la connexion est encore vivante
                    self._sftp.stat(".")
                    return self._sftp
                except Exception:
                    # Connexion morte, on la reconnecte
                    with contextlib.suppress(Exception):
                        self._sftp.close()
                    self._sftp = None
                    self._ssh = None

            import paramiko

            class _TOFUMissingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
                """TOFU (Trust On First Use) : mémorise la 1re clé d'hôte
                rencontrée, refuse ensuite toute clé différente.

                Remplace AutoAddPolicy : un attaquant MITM ne peut plus injecter
                une clé d'hôte sur une connexion ultérieure (BadHostKeyException,
                dont le message indique la marche à suivre : supprimer la ligne
                du fichier known_hosts si le serveur a été légitimement réinstallé).
                """

                def __init__(self, known_hosts_path):
                    self._path = known_hosts_path

                def missing_host_key(self, client, hostname, key):
                    client.get_host_keys().add(hostname, key.get_name(), key)
                    try:
                        client.get_host_keys().save(self._path)
                        logging.info("[SFTP] Nouvelle host key mémorisée (TOFU) pour %s → %s",
                                     hostname, self._path)
                    except OSError as e:
                        # Fail-closed : si on ne peut pas persister la clé, on refuse
                        # plutôt que d'accepter une clé non vérifiée.
                        raise paramiko.SSHException(
                            f"Impossible de persister la host key TOFU ({self._path}) : {e}"
                        ) from e

            self._ssh = paramiko.SSHClient()
            kh_path = self._known_hosts_path()
            if kh_path.exists():
                self._ssh.load_host_keys(str(kh_path))
            self._ssh.set_missing_host_key_policy(_TOFUMissingHostKeyPolicy(str(kh_path)))

            connect_kwargs = {
                "port": self.port,
                "username": self.user,
                "timeout": SFTP_TIMEOUT,
                "banner_timeout": SFTP_TIMEOUT,
                "auth_timeout": SFTP_TIMEOUT,
            }
            if self.key_path:
                self._ssh.connect(self.host, key_filename=self.key_path, **connect_kwargs)
            else:
                self._ssh.connect(self.host, password=self.password, **connect_kwargs)
            # Keepalive : détecte les connexions mortes au lieu de pendre.
            with contextlib.suppress(Exception):
                self._ssh.get_transport().set_keepalive(max(5, int(SFTP_TIMEOUT)))
            self._sftp = self._ssh.open_sftp()
            # Timeout socket sur le canal : borne get/put/stat qui stallent.
            with contextlib.suppress(Exception):
                chan = self._sftp.get_channel()
                if chan is not None:
                    chan.settimeout(SFTP_TIMEOUT)
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
            except OSError:
                dirs_to_create.append(current)
                current = "/".join(current.split("/")[:-1])
        for d in reversed(dirs_to_create):
            with contextlib.suppress(Exception):  # race condition possible, on ignore
                sftp.mkdir(d)

    def create_empty(self, remote_path: str) -> bool:
        """Cree un fichier vide sur le SFTP."""
        with self._lock:
            try:
                sftp = self._connect()
                full = self._full_path(remote_path)
                self._mkdir_p(sftp, "/".join(full.split("/")[:-1]))
                with sftp.open(full, 'wb'):
                    pass  # fichier vide
                return True
            except Exception as e:
                logging.exception(f"[SFTP] create_empty failed: {e}")
                return False

    def append_chunk(self, remote_path: str, data: bytes) -> bool:
        """Append un chunk directement sur le fichier SFTP (pas de temp local)."""
        with self._lock:
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
        Garde le handle ouvert pour eviter de chercher la fin du fichier
        a chaque chunk."""
        with self._lock:
            try:
                sftp = self._connect()
                # Buffer interne paramiko plus gros (2MB au lieu de 32KB)
                sftp.sftp_chunk_size = 2 * 1024 * 1024
                full = self._full_path(remote_path)

                # Reutiliser le handle ouvert si deja cree (evite seek a chaque chunk)
                if full not in self._open_handles:
                    self._mkdir_p(sftp, "/".join(full.split("/")[:-1]))
                    f = sftp.open(full, 'ab')
                    f.set_pipelined(True)
                    self._open_handles[full] = f
                else:
                    f = self._open_handles[full]

                write_buf = 1024 * 1024
                while True:
                    buf = stream.read(write_buf)
                    if not buf:
                        break
                    f.write(buf)
                return True
            except Exception as e:
                self._close_handle_remote(remote_path)
                logging.exception(f"[SFTP] append_chunk_stream failed: {e}")
                return False

    def _close_handle_remote(self, remote_path: str):
        """Ferme le handle ouvert pour ce chemin distant (sous verrou)."""
        with self._lock:
            full = self._full_path(remote_path)
            if full in self._open_handles:
                with contextlib.suppress(Exception):
                    self._open_handles[full].close()
                del self._open_handles[full]

    def close_handle(self, remote_path: str):
        """Ferme le handle SFTP ouvert pour ce chemin."""
        self._close_handle_remote(remote_path)

    def upload(self, local_path: str, remote_path: str) -> bool:
        with self._lock:
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

    def _new_sftp_channel(self):
        """Ouvre un canal SFTP supplémentaire sur la connexion SSH (pool).

        Créé sous ``_lock`` : ne court jamais avec une reconnexion du canal
        principal. Le canal reçoit le même timeout socket que celui-ci.
        """
        with self._lock:
            self._connect()  # garantit une connexion SSH vivante (reconnecte au besoin)
            sftp = self._ssh.open_sftp()
        with contextlib.suppress(Exception):
            chan = sftp.get_channel()
            if chan is not None:
                chan.settimeout(SFTP_TIMEOUT)
        return sftp

    def _borrow_download_channel(self):
        """Réserve un canal de téléchargement (``None`` si aucun disponible).

        Jusqu'à ``SFTP_DOWNLOAD_CONNECTIONS`` canaux simultanés. Si le plafond
        est atteint, attend qu'un canal se libère (borne ``SFTP_TIMEOUT``) ;
        au-delà, ``None`` → repli sur le canal principal sérialisé (un
        téléchargement ne doit JAMAIS échouer à cause du pool).
        """
        with self._download_cond:
            if self._download_free:
                return self._download_free.pop()
            must_create = self._download_created < SFTP_DOWNLOAD_CONNECTIONS
            if must_create:
                self._download_created += 1
            else:
                deadline = time.monotonic() + SFTP_TIMEOUT
                while not self._download_free:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._download_cond.wait(remaining)
                return self._download_free.pop()
        try:
            return self._new_sftp_channel()
        except Exception as e:
            with self._download_cond:
                self._download_created = max(0, self._download_created - 1)
                self._download_cond.notify()
            logging.warning(f"[SFTP] canal de téléchargement indisponible, repli sérialisé : {e}")
            raise

    def _release_download_channel(self, channel, broken=False):
        """Rend un canal au pool (ou le ferme s'il est cassé)."""
        with self._download_cond:
            if broken:
                with contextlib.suppress(Exception):
                    channel.close()
                self._download_created = max(0, self._download_created - 1)
            else:
                self._download_free.append(channel)
            self._download_cond.notify()

    def _download_serialized(self, remote_path, local_path):
        """Téléchargement via le canal principal (sérialisé par ``_lock``)."""
        with self._lock:
            try:
                sftp = self._connect()
                sftp.get(self._full_path(remote_path), local_path)
                return True
            except Exception as e:
                logging.exception(f"[SFTP] download failed: {e}")
                return False

    def open_stream(self, remote_path: str):
        """Flux de lecture direct sur le SFTP via un canal DÉDIÉ du pool.

        Aucun fichier temporaire complet : l'appelant consomme les octets à la
        demande (le débit client est le débit SFTP, premier octet immédiat).
        Retourne ``None`` si aucun canal dédié n'est disponible (plafond atteint)
        ou si l'ouverture échoue : l'appelant retombe alors sur ``download``
        (chemin temp complet, toujours sûr). Jamais d'exception propagée pour
        ces cas de repli.
        """
        full = self._full_path(remote_path)
        for attempt in (0, 1):
            try:
                channel = self._borrow_download_channel()
            except Exception:
                channel = None
            if channel is None:
                return None
            try:
                handle = channel.open(full, 'rb')
            except Exception as e:
                # Canal peut-être mort (transport reconnecté…) : jeté puis un
                # seul nouvel essai, comme ``download``. Ensuite repli temp.
                self._release_download_channel(channel, broken=True)
                if attempt == 0:
                    logging.warning(f"[SFTP] open_stream échoué, nouvel essai : {e}")
                    continue
                logging.warning(f"[SFTP] open_stream impossible pour {remote_path} : {e}")
                return None
            return _SFTPReadStream(self, channel, handle)
        return None

    def download(self, remote_path: str, local_path: str) -> bool:
        """Télécharge un fichier distant vers ``local_path``.

        Utilise un canal DÉDIÉ du pool quand un est disponible : plusieurs
        téléchargements avancent en parallèle et le canal principal
        (uploads/stat/delete) n'est plus immobilisé par un gros download.
        Repli sérialisé (comportement historique) si le pool est indisponible.
        """
        try:
            channel = self._borrow_download_channel()
        except Exception:
            channel = None
        if channel is None:
            return self._download_serialized(remote_path, local_path)
        released = False
        try:
            channel.get(self._full_path(remote_path), local_path)
            return True
        except Exception as e:
            # Canal peut-être mort (transport reconnecté…) : on le jette puis on
            # retente UNE fois sur le canal principal (qui reconnecte au besoin).
            logging.warning(f"[SFTP] download via canal dédié échoué, nouvel essai sérialisé : {e}")
            released = True
            self._release_download_channel(channel, broken=True)
            return self._download_serialized(remote_path, local_path)
        finally:
            if not released:
                self._release_download_channel(channel)

    def delete(self, remote_path: str) -> bool:
        with self._lock:
            try:
                sftp = self._connect()
                full = self._full_path(remote_path)
                sftp.remove(full)
                return True
            except Exception:
                return False

    def exists(self, remote_path: str) -> bool:
        with self._lock:
            try:
                sftp = self._connect()
                full = self._full_path(remote_path)
                sftp.stat(full)
                return True
            except Exception:
                return False

    def list_dir(self, remote_dir: str) -> list:
        with self._lock:
            try:
                sftp = self._connect()
                full = self._full_path(remote_dir)
                return sftp.listdir(full)
            except Exception:
                return []

    def get_backend_name(self) -> str:
        return f"sftp://{self.host}:{self.port}{self.base_path}"

    def close(self):
        """Ferme proprement la connexion et les canaux du pool de téléchargement."""
        with self._lock:
            with self._download_cond:
                for channel in self._download_free:
                    with contextlib.suppress(Exception):
                        channel.close()
                self._download_free.clear()
                self._download_created = 0
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


class _SFTPReadStream:
    """Flux de lecture séquentiel sur un fichier SFTP (canal dédié au pool).

    Implémente le contrat ``read(n)``/``close()`` attendu par
    :meth:`SFTPStorage.open_stream`. La fermeture est IDEMPOTENTE et rend le
    canal au pool (ou le jette s'il est cassé) : jamais de canal fuité, même si
    le client HTTP se déconnecte en plein transfert (GeneratorExit → close).
    """

    def __init__(self, storage, channel, handle):
        self._storage = storage
        self._channel = channel
        self._handle = handle
        self._released = False

    def read(self, n: int) -> bytes:
        try:
            return self._handle.read(n)
        except Exception:
            self._release(broken=True)
            raise

    def close(self):
        self._release(broken=False)

    def _release(self, broken: bool):
        if self._released:
            return
        self._released = True
        with contextlib.suppress(Exception):
            self._handle.close()
        with contextlib.suppress(Exception):
            self._storage._release_download_channel(self._channel, broken=broken)


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
    sftp_base_path = "/aih"

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
                    from security.crypto import decrypt_with_lazy_migration
                    sftp_password = decrypt_with_lazy_migration(row[0], 'sftp_password')
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
            sftp_base_path = os.environ.get("SFTP_BASE_PATH", "/aih")

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

    t = threading.Thread(target=_run, daemon=True, name="aih-backup")
    t.start()
    logging.info(f"[backup] Scheduler started (every {interval_hours}h)")
