"""Délai d'INACTIVITÉ du streaming download : échec borné, jamais infini.

Contexte (signalement utilisateur « Préparation côté serveur… 0 % / 0 B pendant
3 minutes et plus ») : si une lecture de stockage ne rend RIEN (canal SFTP mort
malgré son timeout socket, transport figé, stockage local bloqué), le flux ne
doit pas attendre indéfiniment — le client resterait à 0 octet des dizaines de
minutes. ``routes/files.py`` arme un watchdog par réponse : au-delà de
``STREAM_IDLE_TIMEOUT`` sans morceau, il FERME le flux et le transfert échoue
avec :class:`StreamStalledError` (loggé explicitement côté backend).

Contrôles négatifs par mutation (chacun DOIT faire rougir ce fichier) :
  M1  retirer le watchdog (lecture figée sans fermeture)
      → test_bloque_abandonne_apres_delai rouge (aucune exception, attente du
        cap dur) ;
  M2  ne pas vérifier ``watchdog.stalled`` avant de sortir sur ``b''``
      → test_bloque_abandonne_apres_delai rouge (fin de fichier silencieuse au
        lieu d'une erreur) ;
  M3  masquer TOUTE exception de lecture en StreamStalledError
      → test_erreur_reelle_non_masquee rouge (une vraie panne stockage serait
        rebaptisée « blocage »).
"""

import contextlib
import threading
import time

import pytest
import routes.files as files_mod
from routes.helpers import get_db

_USER_ID = "dl-stall-user"


def _ensure_user(user_id=_USER_ID):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, display_name, role) VALUES (?, ?, ?, ?)",
        (user_id, user_id, user_id, "user"),
    )
    conn.commit()
    conn.close()


def _headers(make_token, user_id=_USER_ID):
    _ensure_user(user_id)
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _seed_upload(upload_id, filename, size, final_path="models/gele.bin"):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO file_uploads "
        "(upload_id, user_id, filename, size, type, chunk_size, total_chunks, "
        " received_chunks, temp_path, final_path, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (upload_id, _USER_ID, filename, size, "model", 26214400, 1,
         1, "", final_path, "complete"),
    )
    conn.commit()
    conn.close()


class _FrozenReturningStream:
    """Lecture FIGÉE : ne rend rien tant que le watchdog n'a pas fermé.

    ``hard_cap`` borne l'attente : si le watchdog est retiré (MUTATION), la
    lecture rend ``b''`` au bout du cap — le test rougit (aucune erreur) au lieu
    de figer toute la suite.
    """

    def __init__(self, hard_cap=3.0):
        self.closed = threading.Event()
        self.hard_cap = hard_cap
        self.read_calls = 0

    def read(self, n):
        self.read_calls += 1
        self.closed.wait(self.hard_cap)
        return b""

    def close(self):
        self.closed.set()


class _FrozenRaisingStream:
    """Lecture FIGÉE dont la fermeture fait ÉCHOUER la lecture (comme un canal)."""

    def __init__(self, hard_cap=3.0):
        self.closed = threading.Event()
        self.hard_cap = hard_cap

    def read(self, n):
        self.closed.wait(self.hard_cap)
        if self.closed.is_set():
            raise OSError("canal fermé pendant la lecture")
        return b""

    def close(self):
        self.closed.set()


class _PacedStream:
    """Flux SAIN mais lent : un petit morceau à intervalle court, sans trou."""

    def __init__(self, payload, pause=0.01, slice_size=64 * 1024):
        self.payload = payload
        self.pause = pause
        self.slice_size = slice_size
        self.pos = 0
        self.closed = False

    def read(self, n):
        time.sleep(self.pause)
        if self.pos >= len(self.payload):
            return b""
        end = min(self.pos + min(n, self.slice_size), len(self.payload))
        buf = self.payload[self.pos:end]
        self.pos = end
        return buf

    def close(self):
        self.closed = True


class _FailingStream:
    """Panne RÉELLE immédiate (pas un blocage) : la lecture lève tout de suite."""

    def read(self, n):
        raise OSError("EIO : disque en panne")

    def close(self):
        pass


class _FakeStorage:
    def __init__(self, stream):
        self.stream = stream
        self.open_stream_called = False
        self.download_called = False

    def open_stream(self, remote_path, size=None):
        self.open_stream_called = True
        return self.stream

    def download(self, remote_path, local_path):  # pragma: no cover — interdit ici
        self.download_called = True
        return False

    def get_backend_name(self):
        return "stub-muet://test"

    def exists(self, remote_path):
        return True


def _dl_temp_files():
    import os
    try:
        return [f for f in os.listdir(files_mod.TEMP_DIR) if f.startswith("dl_")]
    except OSError:
        return []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM file_uploads WHERE upload_id LIKE 'dl-stall-%'")
        conn.commit()
    finally:
        conn.close()
    import os
    for name in _dl_temp_files():
        with contextlib.suppress(OSError):
            os.remove(os.path.join(files_mod.TEMP_DIR, name))


def _consume(client, upload_id, headers):
    resp = client.get(f"/api/files/{upload_id}/download", headers=headers, buffered=False)
    assert resp.status_code == 200
    return resp, b"".join(resp.response)


# ── 1. Stockage qui ne rend RIEN → abandon BORNÉ + erreur explicite ─────


def test_bloque_abandonne_apres_delai(client, make_token, monkeypatch):
    """0 octet pendant STREAM_IDLE_TIMEOUT → 500 JSON explicite, pas d'attente infinie.

    La 1re lecture est faite AVANT de répondre : un stockage muet produit une
    erreur JSON PROPRE ("aucun morceau reçu…") au lieu d'une page d'erreur
    générique ou d'une réponse tronquée.

    MUTATIONS M1/M2 : sans watchdog (ou en sortant silencieusement sur ``b''``),
    ``client.get`` rend un 200 vide sans erreur → le test est rouge après
    le cap dur (3 s).
    """
    headers = _headers(make_token)
    _seed_upload("dl-stall-1", "Krea2-Turbo-int8-ConvRot.safetensors", 13500000000)
    probe = _FrozenReturningStream()
    fake = _FakeStorage(stream=probe)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)
    monkeypatch.setattr(files_mod, "STREAM_IDLE_TIMEOUT", 0.25)

    t0 = time.monotonic()
    resp = client.get("/api/files/dl-stall-1/download", headers=headers)
    elapsed = time.monotonic() - t0

    assert resp.status_code == 500, (
        f"un stockage muet doit produire une erreur explicite, pas {resp.status_code}")
    err = (resp.get_json() or {}).get("error", "")
    assert "aucun morceau reçu" in err, err
    assert "muet" in err, err
    assert elapsed < 1.5, (
        f"l'abandon a pris {elapsed:.2f} s malgré STREAM_IDLE_TIMEOUT=0.25 s : "
        "le client resterait des minutes à 0 octet (watchdog inopérant)")
    assert probe.closed.is_set(), "le flux doit être FERMÉ pour débloquer la lecture"
    assert fake.download_called is False, (
        "aucun repli préchargement : le flux était disponible (juste muet)")
    assert _dl_temp_files() == [], "aucun temp dl_* ne doit être créé sur ce chemin"


def test_fermeture_qui_fait_echouer_la_lecture_reste_un_blocage_explicite(
        client, make_token, monkeypatch):
    """La lecture qui LÈVE à cause du watchdog → 500 JSON « muet », pas OSError brut.

    (Cas SFTP réel : fermer le canal pendant un ``read`` en cours le fait lever.)
    """
    headers = _headers(make_token)
    _seed_upload("dl-stall-2", "gele.safetensors", 1024 * 1024)
    probe = _FrozenRaisingStream()
    monkeypatch.setattr(files_mod, "get_storage", lambda: _FakeStorage(stream=probe))
    monkeypatch.setattr(files_mod, "STREAM_IDLE_TIMEOUT", 0.25)

    resp = client.get("/api/files/dl-stall-2/download", headers=headers)
    assert resp.status_code == 500
    assert "aucun morceau reçu" in (resp.get_json() or {}).get("error", "")
    assert probe.closed.is_set()


# ── 2. Contrôle négatif : une vraie erreur n'est PAS rebaptisée « blocage » ──


def test_erreur_reelle_non_masquee(client, make_token, monkeypatch):
    """Panne immédiate du stockage → message PROPRE d'échec de lecture, pas « muet ».

    MUTATION M3 : rebaptiser toute erreur de lecture en « aucun morceau reçu »
    ferait passer une panne réelle pour un délai d'inactivité → rouge.
    """
    headers = _headers(make_token)
    _seed_upload("dl-stall-3", "eio.safetensors", 1024)
    monkeypatch.setattr(files_mod, "get_storage", lambda: _FakeStorage(stream=_FailingStream()))

    resp = client.get("/api/files/dl-stall-3/download", headers=headers)

    assert resp.status_code == 500
    err = (resp.get_json() or {}).get("error", "")
    assert "Lecture du stockage impossible" in err, err
    assert "aucun morceau reçu" not in err, (
        f"une erreur de stockage réelle a été rebaptisée 'blocage' : {err}")


# ── 3. Pas de FAUX POSITIF : un flux lent mais alimenté aboutit ──────────


def test_flux_lent_mais_actif_n_est_pas_abandonne(client, make_token, monkeypatch):
    """Des morceaux toutes les 10 ms avec un seuil de 0,5 s → transfert complet."""
    headers = _headers(make_token)
    payload = bytes(range(256)) * 2048  # 512 Ko
    _seed_upload("dl-stall-4", "lent.safetensors", len(payload))
    probe = _PacedStream(payload)
    monkeypatch.setattr(files_mod, "get_storage", lambda: _FakeStorage(stream=probe))
    monkeypatch.setattr(files_mod, "STREAM_IDLE_TIMEOUT", 0.5)

    resp, body = _consume(client, "dl-stall-4", headers)

    assert body == payload, "le flux alimenté doit être servi en entier"
    assert probe.pos == len(payload)
    assert resp.headers.get("Content-Length") == str(len(payload))


# ── 4. Le watchdog ne laisse pas de thread orphelin ──────────────────────


def test_watchdog_thread_arrete_apres_succes(client, make_token, monkeypatch):
    """Après un transfert réussi, plus aucun thread ``aih-stream-watchdog`` vivant."""
    headers = _headers(make_token)
    payload = b"x" * (64 * 1024)
    _seed_upload("dl-stall-5", "court.safetensors", len(payload))
    monkeypatch.setattr(files_mod, "get_storage",
                        lambda: _FakeStorage(stream=_PacedStream(payload, pause=0)))
    monkeypatch.setattr(files_mod, "STREAM_IDLE_TIMEOUT", 0.5)

    _resp, body = _consume(client, "dl-stall-5", headers)
    assert body == payload

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        alive = [t for t in threading.enumerate() if t.name == "aih-stream-watchdog"]
        if not alive:
            break
        time.sleep(0.05)
    alive = [t for t in threading.enumerate() if t.name == "aih-stream-watchdog"]
    assert not alive, f"thread(s) watchdog orphelin(s) : {alive}"
