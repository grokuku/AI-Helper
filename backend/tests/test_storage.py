"""Tests for the SFTPStorage backend (paramiko mocked — no real network)."""

import io
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from storage import LocalStorage, SFTPStorage, get_storage, reload_storage


@pytest.fixture
def mock_paramiko():
    """Patch `paramiko` inside the storage module so SFTPStorage._connect
    never touches the network."""
    fake_sftp = MagicMock(name="sftp")
    fake_ssh = MagicMock(name="ssh")

    # sftp.open(...) returns a context manager writing into an in-memory dict.
    files = {}

    class _File:
        def __init__(self, path, mode):
            self.path = path
            self.mode = mode
            self.buf = bytearray(files.get(path, b""))
            self._closed = False

        def write(self, data):
            self.buf.extend(data)

        def set_pipelined(self, v):
            pass

        def close(self):
            files[self.path] = bytes(self.buf)
            self._closed = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            files[self.path] = bytes(self.buf)
            return False

    def _open(path, mode):
        return _File(path, mode)

    fake_sftp.open.side_effect = _open

    # By default, stat(".") succeeds (connection alive) but stat on
    # arbitrary paths raises FileNotFoundError (file doesn't exist).
    def _stat(path):
        if path == ".":
            return MagicMock()  # connection is alive
        raise FileNotFoundError(path)

    fake_sftp.stat.side_effect = _stat
    fake_ssh.open_sftp.return_value = fake_sftp
    fake_ssh.set_missing_host_key_policy = MagicMock()

    fake_module = MagicMock(name="paramiko")
    fake_module.SSHClient.return_value = fake_ssh
    fake_module.AutoAddPolicy = MagicMock()

    with patch.dict("sys.modules", {"paramiko": fake_module}):
        yield {
            "module": fake_module,
            "ssh": fake_ssh,
            "sftp": fake_sftp,
            "files": files,
        }


@pytest.fixture
def storage(mock_paramiko):
    """A fresh SFTPStorage instance using the mocked paramiko."""
    s = SFTPStorage(host="sftp.example", port=22, user="tester",
                    password="pw", base_path="/aih")
    return s


# ── Connection tests ──────────────────────────────────────────────────


class TestSFTPHostKeyTOFU:
    """La connexion SFTP mémorise la 1re host key (TOFU) au lieu d'AutoAddPolicy."""

    def test_connect_uses_tofu_policy_not_autoadd(self, storage, mock_paramiko):
        """AutoAddPolicy ne doit plus être instancié ; une politique est posée."""
        storage._connect()
        mock_paramiko["module"].AutoAddPolicy.assert_not_called()
        args, _ = mock_paramiko["ssh"].set_missing_host_key_policy.call_args
        assert args[0] is not None

    def test_connect_loads_existing_known_hosts(self, mock_paramiko, tmp_path, monkeypatch):
        """Un fichier known_hosts existant est chargé avant la connexion."""
        kh = tmp_path / "known_hosts"
        kh.write_text("sftp.example ssh-ed25519 AAAA...\n")
        monkeypatch.setenv("SFTP_KNOWN_HOSTS", str(kh))
        s = SFTPStorage(host="sftp.example", port=22, user="tester",
                        password="pw", base_path="/aih")
        s._connect()
        mock_paramiko["ssh"].load_host_keys.assert_called_once_with(str(kh))

    def test_known_hosts_default_next_to_db(self, storage):
        """Par défaut, le fichier known_hosts vit à côté de la BDD."""
        p = storage._known_hosts_path()
        assert p.name == ".sftp_known_hosts"


class TestSFTPConnection:
    def test_sftp_connect_success(self, storage, mock_paramiko):
        """test_sftp_connect_success → mock SSHClient.connect OK"""
        sftp = storage._connect()
        assert sftp is mock_paramiko["sftp"]
        mock_paramiko["ssh"].connect.assert_called_once()
        # Verify connect was called with expected params
        call_kwargs = mock_paramiko["ssh"].connect.call_args
        assert call_kwargs.kwargs["username"] == "tester"
        assert call_kwargs.kwargs["password"] == "pw"
        assert call_kwargs.kwargs["port"] == 22

    def test_sftp_connect_failure(self, storage, mock_paramiko):
        """test_sftp_connect_failure → mock SSHClient.connect lève Exception"""
        mock_paramiko["ssh"].connect.side_effect = Exception("Connection refused")
        with pytest.raises(Exception, match="Connection refused"):
            storage._connect()

    def test_sftp_connect_with_key_path(self, mock_paramiko):
        """Connect using key_path instead of password."""
        s = SFTPStorage(host="sftp.example", port=2222, user="keyuser",
                        key_path="/path/to/key", base_path="/data")
        s._connect()
        call_kwargs = mock_paramiko["ssh"].connect.call_args
        assert call_kwargs.kwargs["key_filename"] == "/path/to/key"
        assert call_kwargs.kwargs["port"] == 2222
        assert "password" not in call_kwargs.kwargs

    def test_sftp_reconnect_after_close(self, storage, mock_paramiko):
        """test_sftp_reconnect_after_close → close then _connect again"""
        storage._connect()
        assert mock_paramiko["ssh"].open_sftp.call_count == 1
        storage.close()
        assert storage._sftp is None
        assert storage._ssh is None
        # Reconnect after close
        storage._connect()
        assert mock_paramiko["ssh"].open_sftp.call_count == 2

    def test_sftp_reconnect_after_dead_connection(self, storage, mock_paramiko):
        """If the connection is dead (stat('.') fails), _connect reconnects."""
        storage._connect()
        assert mock_paramiko["ssh"].open_sftp.call_count == 1
        # Simulate dead connection: stat(".") now raises
        mock_paramiko["sftp"].stat.side_effect = lambda p: (_ for _ in ()).throw(OSError("dead"))
        storage._connect()
        # Should have reconnected
        assert mock_paramiko["ssh"].open_sftp.call_count == 2


# ── Write / Read / Exists / Delete ────────────────────────────────────

class TestSFTPWriteReadExistsDelete:
    def test_sftp_write(self, storage, mock_paramiko, tmp_path):
        """test_sftp_write → mock sftp.put, vérifie que write est appelé"""
        local = tmp_path / "src.txt"
        local.write_text("payload content")
        ok = storage.upload(str(local), "remote.txt")
        assert ok is True
        mock_paramiko["sftp"].put.assert_called_once()
        args = mock_paramiko["sftp"].put.call_args[0]
        assert args[0] == str(local)
        assert args[1] == "/aih/remote.txt"

    def test_sftp_write_empty_file(self, storage, mock_paramiko):
        """test_sftp_write_empty_file → create_empty creates a 0-byte file"""
        ok = storage.create_empty("empty.txt")
        assert ok is True
        # sftp.open called with 'wb' mode
        open_call = mock_paramiko["sftp"].open.call_args
        assert open_call[0][1] == "wb"
        assert open_call[0][0] == "/aih/empty.txt"

    def test_sftp_read(self, storage, mock_paramiko, tmp_path):
        """test_sftp_read → mock sftp.get, vérifie le téléchargement"""
        dest = tmp_path / "out.txt"
        ok = storage.download("remote.txt", str(dest))
        assert ok is True
        mock_paramiko["sftp"].get.assert_called_once()
        args = mock_paramiko["sftp"].get.call_args[0]
        assert args[0] == "/aih/remote.txt"
        assert args[1] == str(dest)

    def test_sftp_read_nonexistent_file(self, storage, mock_paramiko, tmp_path):
        """test_sftp_read_nonexistent_file → lève une erreur, retourne False"""
        mock_paramiko["sftp"].get.side_effect = OSError("No such file")
        ok = storage.download("missing.txt", str(tmp_path / "out.txt"))
        assert ok is False

    def test_sftp_exists_true(self, storage, mock_paramiko):
        """test_sftp_exists_true → mock sftp.stat OK"""
        mock_paramiko["sftp"].stat.side_effect = lambda p: MagicMock()
        assert storage.exists("file.txt") is True
        mock_paramiko["sftp"].stat.assert_called_with("/aih/file.txt")

    def test_sftp_exists_false(self, storage, mock_paramiko):
        """test_sftp_exists_false → mock sftp.stat lève IOError"""
        mock_paramiko["sftp"].stat.side_effect = OSError("No such file")
        assert storage.exists("file.txt") is False

    def test_sftp_delete(self, storage, mock_paramiko):
        """test_sftp_delete → mock sftp.remove, vérifie l'appel"""
        ok = storage.delete("file.txt")
        assert ok is True
        mock_paramiko["sftp"].remove.assert_called_once_with("/aih/file.txt")

    def test_sftp_delete_failure_returns_false(self, storage, mock_paramiko):
        """test_sftp_delete failure → returns False"""
        mock_paramiko["sftp"].remove.side_effect = OSError("missing")
        assert storage.delete("file.txt") is False

    def test_sftp_list_files(self, storage, mock_paramiko):
        """test_sftp_list_files → mock sftp.listdir"""
        mock_paramiko["sftp"].listdir.return_value = ["a.txt", "b.txt", "c.log"]
        result = storage.list_dir("somewhere")
        assert result == ["a.txt", "b.txt", "c.log"]
        mock_paramiko["sftp"].listdir.assert_called_once_with("/aih/somewhere")

    def test_sftp_list_files_failure_returns_empty(self, storage, mock_paramiko):
        mock_paramiko["sftp"].listdir.side_effect = OSError("no dir")
        assert storage.list_dir("somewhere") == []


# ── Chunked streaming ─────────────────────────────────────────────────

class TestSFTPChunkStream:
    def test_sftp_write_chunk_stream(self, storage, mock_paramiko):
        """test_sftp_write_chunk_stream → mock append mode, pipelined handle"""
        stream = io.BytesIO(b"hello world chunk data")
        ok = storage.append_chunk_stream("video/raw.mp4", stream)
        assert ok is True
        # sftp.open was called in append mode
        open_call = mock_paramiko["sftp"].open.call_args
        assert open_call[0][0] == "/aih/video/raw.mp4"
        assert open_call[0][1] == "ab"
        # The handle should be kept open in _open_handles
        assert "/aih/video/raw.mp4" in storage._open_handles

    def test_sftp_write_chunk_stream_reuses_handle(self, storage, mock_paramiko):
        """Second call to append_chunk_stream reuses the open handle."""
        stream1 = io.BytesIO(b"part1")
        storage.append_chunk_stream("file.bin", stream1)
        first_open_count = mock_paramiko["sftp"].open.call_count

        stream2 = io.BytesIO(b"part2")
        storage.append_chunk_stream("file.bin", stream2)
        # open should NOT have been called again (handle reused)
        assert mock_paramiko["sftp"].open.call_count == first_open_count

    def test_sftp_write_chunk_stream_failure(self, storage, mock_paramiko):
        """test_sftp_write_chunk_stream failure → returns False, closes handle"""
        mock_paramiko["sftp"].open.side_effect = OSError("cannot open")
        stream = io.BytesIO(b"data")
        ok = storage.append_chunk_stream("file.bin", stream)
        assert ok is False

    def test_sftp_close_handle(self, storage, mock_paramiko):
        """close_handle closes the pipelined handle for a given path."""
        stream = io.BytesIO(b"data")
        storage.append_chunk_stream("file.bin", stream)
        assert "/aih/file.bin" in storage._open_handles
        storage.close_handle("file.bin")
        assert "/aih/file.bin" not in storage._open_handles


# ── Close ────────────────────────────────────────────────────────────

class TestSFTPClose:
    def test_sftp_close(self, storage, mock_paramiko):
        """test_sftp_close → mock sftp.close + ssh.close"""
        storage._connect()
        storage.close()
        mock_paramiko["sftp"].close.assert_called_once()
        mock_paramiko["ssh"].close.assert_called_once()
        assert storage._sftp is None
        assert storage._ssh is None

    def test_sftp_close_without_connection(self, storage, mock_paramiko):
        """close() is safe even if never connected."""
        storage.close()
        assert storage._sftp is None
        assert storage._ssh is None

    def test_sftp_close_idempotent(self, storage, mock_paramiko):
        """Double close() does not raise."""
        storage._connect()
        storage.close()
        storage.close()  # should not raise


# ── Timeouts réseau & sûreté de thread (anti-blocage) ─────────────────

class TestSFTPTimeoutsAndThreadSafety:
    """Sans timeout, une opération SFTP peut bloquer INDÉFINIMENT (galerie
    « figée »). Et paramiko SFTPClient n'est pas thread-safe : le canal
    partagé doit être sérialisé."""

    def test_timeout_is_positive(self):
        import storage as storage_module

        assert storage_module.SFTP_TIMEOUT > 0

    def test_connect_passes_explicit_timeouts(self, storage, mock_paramiko):
        """connect() reçoit timeout/banner_timeout/auth_timeout explicites."""
        import storage as storage_module

        storage._connect()
        kwargs = mock_paramiko["ssh"].connect.call_args.kwargs
        assert kwargs["timeout"] == storage_module.SFTP_TIMEOUT
        assert kwargs["banner_timeout"] == storage_module.SFTP_TIMEOUT
        assert kwargs["auth_timeout"] == storage_module.SFTP_TIMEOUT

    def test_channel_gets_socket_timeout(self, storage, mock_paramiko):
        """Le canal SFTP reçoit un timeout socket (borne get/put/stat)."""
        import storage as storage_module

        storage._connect()
        chan = mock_paramiko["sftp"].get_channel.return_value
        chan.settimeout.assert_called_once_with(storage_module.SFTP_TIMEOUT)

    def test_operations_are_serialized_across_threads(self, storage, mock_paramiko):
        """[NEGATIVE] Deux threads ne doivent JAMAIS entrer en concurrence dans
        le client SFTP : sans le verrou, le compteur d'opérations simultanées
        dépasserait 1."""
        import threading
        import time

        storage._connect()
        active = {"n": 0, "max": 0}
        guard = threading.Lock()

        def slow_put(*_args, **_kwargs):
            with guard:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.05)
            with guard:
                active["n"] -= 1

        mock_paramiko["sftp"].put.side_effect = slow_put
        threads = [
            threading.Thread(target=storage.upload, args=("/tmp/x", f"r{i}"))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert active["max"] == 1, "le canal SFTP a été utilisé en concurrence"


# ── Path helpers ─────────────────────────────────────────────────────

class TestSFTPPathHelpers:
    def test_sftp_full_path_relative(self, storage):
        """test_sftp_full_path → vérifie la construction du chemin"""
        assert storage._full_path("a/b.txt") == "/aih/a/b.txt"

    def test_sftp_full_path_absolute(self, storage):
        """Absolute remote paths are returned as-is."""
        assert storage._full_path("/abs/b.txt") == "/abs/b.txt"

    def test_base_path_trailing_slash_stripped(self, mock_paramiko):
        s = SFTPStorage(host="h", base_path="/aih/")
        assert s.base_path == "/aih"

    def test_get_backend_name(self, storage):
        name = storage.get_backend_name()
        assert name == "sftp://sftp.example:22/aih"

    def test_mkdirp_creates_missing_dirs(self, storage, mock_paramiko):
        """_mkdir_p walks up and creates directories that don't exist."""
        # Everything raises IOError (doesn't exist), so all dirs get created
        mock_paramiko["sftp"].stat.side_effect = OSError("nope")
        mock_paramiko["sftp"].mkdir = MagicMock()
        storage._mkdir_p(mock_paramiko["sftp"], "/aih/sub/deep")
        # At least the deepest dirs should have been created
        assert mock_paramiko["sftp"].mkdir.called

    def test_mkdirp_stops_at_existing_dir(self, storage, mock_paramiko):
        """_mkdir_p stops walking up once it finds an existing directory."""
        call_log = []

        def _stat(path):
            call_log.append(path)
            if path == "/aih":
                return MagicMock()  # exists
            raise OSError("nope")

        mock_paramiko["sftp"].stat.side_effect = _stat
        mock_paramiko["sftp"].mkdir = MagicMock()
        storage._mkdir_p(mock_paramiko["sftp"], "/aih/a/b")
        # Should not try to mkdir /aih (it exists)
        mkdir_calls = [c.args[0] for c in mock_paramiko["sftp"].mkdir.call_args_list]
        assert "/aih" not in mkdir_calls


# ── Factory / singleton ──────────────────────────────────────────────

class TestStorageFactory:
    """The get_storage() singleton should fall back to LocalStorage when no
    SFTP config is present (the default test environment)."""

    def setup_method(self):
        reload_storage()  # reset singleton

    def teardown_method(self):
        reload_storage()  # clean up for other tests

    def test_factory_returns_local_without_sftp_config(self, monkeypatch):
        monkeypatch.delenv("SFTP_HOST", raising=False)
        reload_storage()
        storage = get_storage()
        assert storage.get_backend_name() == "local"

    def test_factory_returns_sftp_when_host_set(self, monkeypatch):
        monkeypatch.setenv("SFTP_HOST", "sftp.example")
        monkeypatch.setenv("SFTP_USER", "u")
        monkeypatch.setenv("SFTP_PASSWORD", "p")
        reload_storage()
        storage = get_storage()
        assert storage.get_backend_name().startswith("sftp://")


# ── Pool de canaux de téléchargement (parallélisme du worker d'album) ─


class TestSFTPDownloadChannelPool:
    """Les téléchargements utilisent des canaux DÉDIÉS (pool) : plusieurs
    downloads avancent en parallèle et le canal principal reste réservé aux
    autres opérations (uploads/stat/delete), sérialisées par `_lock`."""

    def _make_storage(self, mock_paramiko, monkeypatch, cap, channels):
        import storage as storage_module

        monkeypatch.setattr(storage_module, "SFTP_DOWNLOAD_CONNECTIONS", cap)
        mock_paramiko["ssh"].open_sftp.side_effect = list(channels)
        return storage_module.SFTPStorage(
            host="sftp.example", port=22, user="tester", password="pw", base_path="/aih"
        )

    def test_downloads_run_in_parallel_on_dedicated_channels(self, mock_paramiko, monkeypatch, tmp_path):
        """[NEGATIVE] Le pool retiré, deux downloads seraient sérialisés
        (max concurrent = 1) au lieu de 2."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        pool2 = MagicMock(name="pool2")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1, pool2])

        active = {"n": 0, "max": 0}
        guard = threading.Lock()

        def slow_get(*_args, **_kwargs):
            with guard:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.1)
            with guard:
                active["n"] -= 1

        pool1.get.side_effect = slow_get
        pool2.get.side_effect = slow_get

        results = []

        def run(i):
            results.append(s.download(f"r{i}.bin", str(tmp_path / f"d{i}.bin")))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert active["max"] == 2, "deux téléchargements doivent être simultanés"
        assert results == [True, True]
        assert pool1.get.call_count + pool2.get.call_count == 2
        # Le canal principal n'est servi par AUCUN download.
        main.get.assert_not_called()

    def test_download_falls_back_to_main_when_pool_creation_fails(self, mock_paramiko, monkeypatch, tmp_path):
        """Création du canal dédié impossible → repli sérialisé, jamais d'échec."""
        main = MagicMock(name="main")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, OSError("pas de canal en plus")])

        assert s.download("x.bin", str(tmp_path / "x.bin")) is True
        main.get.assert_called_once()

    def test_download_retries_serialized_when_channel_breaks(self, mock_paramiko, monkeypatch, tmp_path):
        """Canal dédié cassé (transport mort…) → 1 nouvel essai sur le canal
        principal (qui reconnectera au besoin), pas d'item perdu."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        pool1.get.side_effect = OSError("canal mort")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1])

        assert s.download("y.bin", str(tmp_path / "y.bin")) is True
        assert pool1.get.call_count == 1
        main.get.assert_called_once()
        pool1.close.assert_called()  # canal cassé fermé (pas remis au pool)

    def test_download_pool_cap_one_serializes(self, mock_paramiko, monkeypatch, tmp_path):
        """SFTP_DOWNLOAD_CONNECTIONS=1 → un seul téléchargement à la fois."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        s = self._make_storage(mock_paramiko, monkeypatch, 1, [main, pool1])

        active = {"n": 0, "max": 0}
        guard = threading.Lock()

        def slow_get(*_args, **_kwargs):
            with guard:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.05)
            with guard:
                active["n"] -= 1

        pool1.get.side_effect = slow_get

        def run(i):
            s.download(f"z{i}.bin", str(tmp_path / f"z{i}.bin"))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert active["max"] == 1, "plafond 1 → téléchargements sérialisés"
        assert pool1.get.call_count == 2

    def test_close_closes_pool_channels(self, mock_paramiko, monkeypatch, tmp_path):
        """close() ferme aussi les canaux du pool (aucune session qui fuit)."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1])

        assert s.download("w.bin", str(tmp_path / "w.bin")) is True
        assert s._download_free == [pool1]
        s.close()
        pool1.close.assert_called()
        assert s._download_free == []
        assert s._download_created == 0

    def test_download_does_not_block_main_channel_operations(self, mock_paramiko, monkeypatch, tmp_path):
        """[NEGATIVE] Sans le pool, le download tiendrait `_lock` pendant tout
        le transfert : l'upload devrait attendre sa fin (test ROUGE)."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1])

        started = threading.Event()
        release = threading.Event()

        def slow_get(*_args, **_kwargs):
            started.set()
            release.wait(5)

        pool1.get.side_effect = slow_get

        downloader = threading.Thread(target=s.download, args=("big.bin", str(tmp_path / "big.bin")))
        downloader.start()
        assert started.wait(5), "le download n'a pas démarré"

        t0 = time.perf_counter()
        uploader = threading.Thread(target=s.upload, args=(str(tmp_path / "u.bin"), "u.bin"))
        uploader.start()
        uploader.join(timeout=2)
        elapsed = time.perf_counter() - t0

        release.set()
        downloader.join(timeout=5)

        assert not uploader.is_alive(), "l'upload est resté bloqué par le téléchargement"
        assert elapsed < 1.0, f"upload bloqué {elapsed:.2f}s par le download"
        main.put.assert_called_once()


class _ReadHandle:
    """Handle SFTP de lecture simulé (payload fixe, trace la fermeture)."""

    def __init__(self, payload):
        self.payload = payload
        self.pos = 0
        self.closed = False

    def read(self, n):
        buf = self.payload[self.pos:self.pos + n]
        self.pos += len(buf)
        return buf

    def close(self):
        self.closed = True


class TestOpenStream:
    """``open_stream`` : flux direct storage → client (pas de temp complet).

    C'est le contrat qui permet au download HTTP de servir le premier octet
    immédiatement et de ne PAS doubler le transfert (stockage→temp puis
    temp→client). Le canal dédié au pool doit être rendu à la fermeture, jeté
    s'il est cassé, et jamais fuiter.
    """

    def _make_storage(self, mock_paramiko, monkeypatch, cap, channels):
        import storage as storage_module

        monkeypatch.setattr(storage_module, "SFTP_DOWNLOAD_CONNECTIONS", cap)
        mock_paramiko["ssh"].open_sftp.side_effect = list(channels)
        return storage_module.SFTPStorage(
            host="sftp.example", port=22, user="tester", password="pw", base_path="/aih"
        )

    def test_open_stream_reads_on_dedicated_channel_and_releases(self, mock_paramiko, monkeypatch):
        """Lecture par morceaux + canal RENDU au pool une seule fois."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        handle = _ReadHandle(b"abcdefgh")
        pool1.open.return_value = handle
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1])

        stream = s.open_stream("models/a.bin")
        assert stream is not None
        assert pool1.open.call_args[0] == ("/aih/models/a.bin", "rb")
        assert stream.read(4) == b"abcd"
        assert stream.read(4) == b"efgh"
        assert stream.read(4) == b""
        assert s._download_free == [], "canal encore en service pendant le flux"

        stream.close()
        assert handle.closed is True
        assert s._download_free == [pool1], "fermeture → canal rendu au pool"
        stream.close()  # idempotent
        assert s._download_free == [pool1], "aucun double release"
        main.open.assert_not_called(), "le canal principal reste libre (uploads/stat)"

    def test_open_stream_none_sans_canal_disponible(self, mock_paramiko, monkeypatch):
        """Plafond atteint/aucun canal → None (l'appelant retombe sur le temp)."""
        main = MagicMock(name="main")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main])
        monkeypatch.setattr(s, "_borrow_download_channel", lambda: None)
        assert s.open_stream("models/a.bin") is None

    def test_open_stream_echec_ouverture_retente_puis_none(self, mock_paramiko, monkeypatch):
        """Canal mort au moment d'ouvrir : jeté, un seul nouvel essai, puis repli."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        pool2 = MagicMock(name="pool2")
        pool1.open.side_effect = OSError("canal mort")
        pool2.open.side_effect = OSError("toujours mort")
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1, pool2])

        assert s.open_stream("models/a.bin") is None
        assert pool1.close.called, "canal en échec fermé (pas remis au pool)"
        assert pool2.close.called
        assert s._download_free == []
        assert s._download_created == 0, "compteur du pool décrémenté (pas de fuite)"

    def test_open_stream_erreur_lecture_jette_le_canal(self, mock_paramiko, monkeypatch):
        """Erreur en plein transfert → canal JETÉ, l'erreur remonte au client."""
        main = MagicMock(name="main")
        pool1 = MagicMock(name="pool1")
        handler = MagicMock(name="handle")
        handler.read.side_effect = OSError("coupure en plein flux")
        pool1.open.return_value = handler
        s = self._make_storage(mock_paramiko, monkeypatch, 2, [main, pool1])

        stream = s.open_stream("models/a.bin")
        with pytest.raises(OSError):
            stream.read(1024)
        assert pool1.close.called, "canal cassé fermé (jeté du pool)"
        assert s._download_free == []
        stream.close()  # après erreur : ne double-libère pas
        assert s._download_free == []

    def test_open_stream_local_lit_sans_copie(self, tmp_path):
        """Local : flux direct sur le fichier (aucune copie préalable) + absent → None."""
        base = tmp_path / "store"
        base.mkdir()
        (base / "a.bin").write_bytes(b"hello")
        s = LocalStorage(str(base))

        stream = s.open_stream("a.bin")
        assert stream is not None
        assert stream.read(2) == b"he"
        stream.close()
        assert s.open_stream("absent.bin") is None
