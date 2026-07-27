"""Tests for the SFTPStorage backend (paramiko mocked — no real network)."""

import io
import os
import logging
from unittest.mock import patch, MagicMock

import pytest

from storage import SFTPStorage, get_storage, reload_storage


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
        mock_paramiko["sftp"].stat.side_effect = lambda p: (_ for _ in ()).throw(IOError("dead"))
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
        mock_paramiko["sftp"].get.side_effect = IOError("No such file")
        ok = storage.download("missing.txt", str(tmp_path / "out.txt"))
        assert ok is False

    def test_sftp_exists_true(self, storage, mock_paramiko):
        """test_sftp_exists_true → mock sftp.stat OK"""
        mock_paramiko["sftp"].stat.side_effect = lambda p: MagicMock()
        assert storage.exists("file.txt") is True
        mock_paramiko["sftp"].stat.assert_called_with("/aih/file.txt")

    def test_sftp_exists_false(self, storage, mock_paramiko):
        """test_sftp_exists_false → mock sftp.stat lève IOError"""
        mock_paramiko["sftp"].stat.side_effect = IOError("No such file")
        assert storage.exists("file.txt") is False

    def test_sftp_delete(self, storage, mock_paramiko):
        """test_sftp_delete → mock sftp.remove, vérifie l'appel"""
        ok = storage.delete("file.txt")
        assert ok is True
        mock_paramiko["sftp"].remove.assert_called_once_with("/aih/file.txt")

    def test_sftp_delete_failure_returns_false(self, storage, mock_paramiko):
        """test_sftp_delete failure → returns False"""
        mock_paramiko["sftp"].remove.side_effect = IOError("missing")
        assert storage.delete("file.txt") is False

    def test_sftp_list_files(self, storage, mock_paramiko):
        """test_sftp_list_files → mock sftp.listdir"""
        mock_paramiko["sftp"].listdir.return_value = ["a.txt", "b.txt", "c.log"]
        result = storage.list_dir("somewhere")
        assert result == ["a.txt", "b.txt", "c.log"]
        mock_paramiko["sftp"].listdir.assert_called_once_with("/aih/somewhere")

    def test_sftp_list_files_failure_returns_empty(self, storage, mock_paramiko):
        mock_paramiko["sftp"].listdir.side_effect = IOError("no dir")
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
        mock_paramiko["sftp"].open.side_effect = IOError("cannot open")
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
        mock_paramiko["sftp"].stat.side_effect = IOError("nope")
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
            raise IOError("nope")

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