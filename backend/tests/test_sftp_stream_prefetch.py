"""Pipeline de lecture SFTP (prefetch) — correctif de DÉBIT du download.

Cause MESURÉE (harnais hors dépôt /projects/.aih_tmp/dl_speed : backend réel +
client réel + vrai serveur paramiko + proxy à latence) : sans prefetch,
``SFTPFile.read(n)`` découpe la demande en requêtes de 32768 octets traitées
UNE PAR UNE (un aller-retour réseau chacune, ``SFTPClient._request``) →
32,06 requêtes/Mo, **0,84 Mo/s à RTT nul** (42 ms/requête même en loopback :
Nagle + ACK différé côté serveur) et 0,36 Mo/s à 80 ms de RTT. Avec
``SFTPFile.prefetch()``, les requêtes partent en avance : le débit suit le lien.

Contrat verrouillé ici :
  1. toute lecture SFTP est PIPELINÉE par une fenêtre de prefetch BORNÉE
     (jamais « jusqu'à la fin » : 13 Go seraient bufferisés en RAM sinon) ;
  2. la fenêtre n'est ré-armée QUE lorsqu'elle est consommée (aucune requête
     en double → aucune donnée orpheline dans le buffer paramiko) ;
  3. repli sûr : prefetch indisponible → lectures classiques, jamais d'échec ;
  4. taille inconnue → bornée par un FSTAT (1 aller-retour) ;
  5. débit minimal sous latence simulée : les lectures ne paient PAS un
     aller-retour par paquet ;
  6. ``STREAM_CHUNK_SIZE`` ≥ 4 Mo (morceaux du streaming backend).

Contrôles négatifs par mutation (chacun DOIT faire rougir ce fichier) :
  M1  ``_arm_prefetch`` neutralisé (return immédiat)          → test_debit_sous_latence
  M2  prefetch de la taille ENTIÈRE du fichier (non borné)    → test_prefetch_arme_et_borne
  M3  ré-armement à chaque read (sans consommer la fenêtre)   → test_rearmement
  M4  propager l'exception de prefetch au lieu de replier     → test_repli_si_prefetch
  M5  fermer le handle sur abandon au lieu de jeter le canal  → test_open_stream_abandon
"""

import time
from unittest.mock import MagicMock

import pytest

import routes.files as files_mod
import storage as storage_module

LATENCY = 0.02          # 20 ms par aller-retour (latence simulée d'un SFTP)
PKT = 32768             # taille de requête paramiko (SFTPFile.MAX_REQUEST_SIZE)


class _FakeHandle:
    """Handle paramiko minimal, modèle de LATENCE fidèle.

    - ``read(n)`` hors zone pipelinée : même coût que paramiko — une latence
      par requête de 32768 octets (le ``BufferedFile.read`` boucle en interne),
      comptabilisée dans ``sync_requests`` ;
    - ``prefetch(file_size)`` rend la zone disponible sans coût par paquet
      (les réponses se recouvrent) : ``read`` dans la zone est immédiat.
    """

    def __init__(self, data, latency=LATENCY, fail_prefetch=False, with_stat=True):
        self.data = data
        self.pos = 0
        self.latency = latency
        self.fail_prefetch = fail_prefetch
        self.with_stat = with_stat
        self.prefetch_calls = []
        self.stat_calls = 0
        self.sync_requests = 0
        self.pipelined_reads = 0
        self.ranges = []
        self.closed = False

    # ── API paramiko utilisée par _SFTPReadStream ────────────────────────
    def stat(self):
        self.stat_calls += 1
        if not self.with_stat:
            raise OSError("FSTAT indisponible")
        return type("A", (), {"st_size": len(self.data)})()

    def prefetch(self, file_size, max_concurrent_requests=None):
        self.prefetch_calls.append((file_size, max_concurrent_requests))
        if self.fail_prefetch:
            raise OSError("prefetch refusé par le canal")
        self.ranges.append((self.pos, min(file_size, len(self.data))))

    def _pipelined(self, start, end):
        return any(a <= start and end <= b for a, b in self.ranges)

    def read(self, n):
        if self.closed:
            raise ValueError("handle fermé")
        start, end = self.pos, min(self.pos + n, len(self.data))
        if end <= start:
            return b""
        if self.latency and not self._pipelined(start, end):
            requests = -(-(end - start) // PKT)  # ceil
            self.sync_requests += requests
            time.sleep(self.latency * requests)
        else:
            self.pipelined_reads += 1
        self.pos = end
        return self.data[start:end]

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _small_window(monkeypatch):
    """Fenêtre fixée pour des tests rapides et déterministes."""
    monkeypatch.setattr(storage_module, "SFTP_PREFETCH_WINDOW", 1024 * 1024)


def _read_all(stream, size=128 * 1024):
    out = bytearray()
    while True:
        chunk = stream.read(size)
        if not chunk:
            break
        out += chunk
    return bytes(out)


# ── 1. Prefetch armé à l'ouverture, borné à la fenêtre ─────────────────


def test_prefetch_arme_et_borne_a_la_fenetre(monkeypatch):
    data = bytes(8 * 1024 * 1024)  # 8 Mo > fenêtre 1 Mo
    handle = _FakeHandle(data)
    stream = storage_module._SFTPReadStream(None, None, handle, size=len(data))

    assert handle.prefetch_calls, (
        "aucun prefetch à l'ouverture : les lectures paieraient un aller-retour "
        "par paquet de 32 Ko (~32 RTT/Mo, plafond mesuré < 1 Mo/s)")
    first_size, concurrency = handle.prefetch_calls[0]
    assert first_size == storage_module.SFTP_PREFETCH_WINDOW, (
        f"fenêtre de prefetch non bornée : {first_size} octets demandés "
        "(le fichier entier mettrait 13 Go en RAM)")
    assert concurrency is None, (
        "prefetch SANS plafond de requêtes : le thread envoie la fenêtre puis "
        "se TERMINE toujours. Un plafond le laisse tourner à 100 Hz après un "
        "abandon (réponses en erreur jamais retirées de _prefetch_extents)")
    assert _read_all(stream, 1024 * 1024) == data


def test_taille_inconnue_bornee_par_stat():
    data = bytes(4 * 1024 * 1024)
    handle = _FakeHandle(data)
    stream = storage_module._SFTPReadStream(None, None, handle, size=None)

    assert handle.stat_calls == 1, "taille absente de la BDD → un FSTAT pour borner"
    assert handle.prefetch_calls[0][0] == storage_module.SFTP_PREFETCH_WINDOW, (
        "sans taille connue, la fenêtre doit rester bornée")
    assert _read_all(stream) == data


# ── 2. Ré-armement UNIQUEMENT après consommation complète ──────────────


def test_rearmement_apres_consommation_complete(monkeypatch):
    monkeypatch.setattr(storage_module, "SFTP_PREFETCH_WINDOW", 64 * 1024)
    data = bytes(256 * 1024)  # 4 fenêtres de 64 Ko
    handle = _FakeHandle(data)
    stream = storage_module._SFTPReadStream(None, None, handle, size=len(data))
    assert handle.prefetch_calls == [(64 * 1024, None)]

    got = stream.read(32 * 1024)
    assert len(handle.prefetch_calls) == 1, (
        "ré-armement en pleine fenêtre : un second prefetch recouvrirait le "
        "premier (requêtes en double → données orphelines en RAM)")
    got += stream.read(32 * 1024)  # fenêtre exactement consommée
    got += stream.read(16 * 1024)  # le read suivant arme la fenêtre suivante
    assert len(handle.prefetch_calls) == 2, (
        "fenêtre consommée non ré-armée : lectures suivantes non pipelinées")
    assert handle.prefetch_calls[1][0] == 128 * 1024, (
        "la fenêtre suivante doit couvrir [position, position + fenêtre]")
    got += _read_all(stream, 16 * 1024)
    assert got == data
    assert handle.sync_requests == 0, (
        "aucune lecture hors pipeline : les fenêtres suivantes doivent être "
        "ré-armées avant que leur contenu ne soit demandé")


# ── 3. Repli sûr si prefetch indisponible ──────────────────────────────


def test_repli_si_prefetch_indisponible():
    data = bytes(128 * 1024)
    handle = _FakeHandle(data, fail_prefetch=True)
    stream = storage_module._SFTPReadStream(None, None, handle, size=len(data))

    assert len(handle.prefetch_calls) == 1, "une seule tentative, pas de boucle"
    assert _read_all(stream, 32 * 1024) == data, (
        "prefetch indisponible : les lectures classiques doivent servir le "
        "fichier (jamais d'échec de download pour ça)")


def test_repli_stat_indisponible():
    data = bytes(64 * 1024)
    handle = _FakeHandle(data, with_stat=False)
    stream = storage_module._SFTPReadStream(None, None, handle, size=None)
    assert handle.prefetch_calls == [], "pas de prefetch sans taille"
    assert _read_all(stream, 16 * 1024) == data


# ── 4. Débit sous latence simulée : pas d'aller-retour par paquet ──────


def test_debit_sous_latence_simulee(monkeypatch):
    """4 Mo à 20 ms par requête de 32 Ko : 2,56 s sans pipeline, ~0 s avec."""
    monkeypatch.setattr(storage_module, "SFTP_PREFETCH_WINDOW", 64 * 1024 * 1024)
    data = bytes(4 * 1024 * 1024)
    handle = _FakeHandle(data, latency=LATENCY)
    stream = storage_module._SFTPReadStream(None, None, handle, size=len(data))

    t0 = time.monotonic()
    out = _read_all(stream, 1024 * 1024)
    elapsed = time.monotonic() - t0
    naive = (len(data) // PKT) * LATENCY  # 128 allers-retours × 20 ms

    assert out == data
    assert handle.sync_requests == 0, (
        f"{handle.sync_requests} requêtes SYNCHRONES : le fichier n'est pas "
        "lu via le pipeline (prefetch désarmé ?)")
    assert elapsed < naive / 4, (
        f"{elapsed:.3f} s pour 4 Mo (< {naive:.2f} s sans pipeline) : la "
        "latence est encore payée par paquet")


# ── 5. Intégration open_stream : canal rendu + prefetch armé ───────────


def test_open_stream_fin_de_fichier_rend_le_canal():
    storage = storage_module.SFTPStorage(
        host="sftp.example", port=22, user="u", password="p", base_path="/aih")
    handle = _FakeHandle(bytes(64 * 1024))
    channel = MagicMock(name="pool-channel")
    channel.open.return_value = handle
    releases = []
    storage._borrow_download_channel = lambda: channel
    storage._release_download_channel = (
        lambda ch, broken=False: releases.append((ch, broken)))

    stream = storage.open_stream("models/small.bin", size=64 * 1024)
    assert handle.prefetch_calls[0][0] == 64 * 1024
    assert _read_all(stream, 16 * 1024) == bytes(64 * 1024)  # EOF consommée
    stream.close()
    assert handle.closed is True, "fin de fichier → handle fermé"
    assert releases == [(channel, False)], (
        "download TERMINÉ → canal rendu au pool (réutilisable)")


def test_open_stream_abandon_jette_le_canal_sans_bloquer():
    """Annulation/déconnexion en plein prefetch : ni blocage ni thread orphelin."""
    storage = storage_module.SFTPStorage(
        host="sftp.example", port=22, user="u", password="p", base_path="/aih")
    size = 16 * 1024 * 1024
    handle = _FakeHandle(bytes(size))
    channel = MagicMock(name="pool-channel")
    channel.open.return_value = handle
    releases = []
    storage._borrow_download_channel = lambda: channel
    storage._release_download_channel = (
        lambda ch, broken=False: releases.append((ch, broken)))

    stream = storage.open_stream("models/big.bin", size=size)
    stream.read(4096)  # ABANDON : le flux n'est pas consommé jusqu'au bout
    stream.close()
    assert releases == [(channel, True)], (
        "abandon → canal JETÉ (jamais rendu au pool avec des requêtes prefetch "
        "en vol : le prochain download hériterait d'un canal pollué)")
    assert handle.closed is False, (
        "pas de handle.close() sur abandon : il attendrait que le serveur "
        "réponde à tout le reste de la fenêtre (des Mo, jusqu'au timeout)")


def test_open_stream_erreur_de_lecture_jette_le_canal():
    storage = storage_module.SFTPStorage(
        host="sftp.example", port=22, user="u", password="p", base_path="/aih")

    class _BrokenHandle(_FakeHandle):
        def read(self, n):
            raise OSError("EIO")

    handle = _BrokenHandle(bytes(64 * 1024))
    channel = MagicMock(name="pool-channel")
    channel.open.return_value = handle
    releases = []
    storage._borrow_download_channel = lambda: channel
    storage._release_download_channel = (
        lambda ch, broken=False: releases.append((ch, broken)))

    stream = storage.open_stream("models/broken.bin", size=64 * 1024)
    with pytest.raises(OSError):
        stream.read(4096)
    assert releases == [(channel, True)], (
        "erreur de lecture → canal jeté (jamais réutilisé cassé)")
    stream.close()  # idempotent, ne re-libère pas


# ── 6. Morceaux du streaming ≥ 4 Mo ────────────────────────────────────


def test_stream_chunk_size_au_moins_4_mo():
    assert files_mod.STREAM_CHUNK_SIZE >= 4 * 1024 * 1024, (
        f"STREAM_CHUNK_SIZE={files_mod.STREAM_CHUNK_SIZE} : trop petit, "
        "chaque morceau refait un tour Flask/werkzeug")
