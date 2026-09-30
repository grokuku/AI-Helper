"""Tests de ``GET /api/files/<upload_id>/download`` — streaming direct.

Contexte (signalement utilisateur « téléchargement hyper lent, aucune
progression ») : le handler préchargeait le fichier ENTIER du stockage vers un
fichier temporaire (``storage.download``) AVANT d'appeler ``send_file`` :
  - DEUX transferts séquentiels (stockage→temp puis temp→client) ;
  - AUCUN octet pour le client pendant la phase de préparation (des dizaines de
    minutes pour un modèle de 13,5 Go) ;
  - double occupation disque sur le serveur ;
  - le temp n'était JAMAIS nettoyé : ``call_on_close`` ne s'exécute pas sur une
    réponse ``direct_passthrough`` (send_file) — constaté par harnais.

Contrat verrouillé ici :
  1. chemin NOMINAL : ``storage.open_stream`` (pas de ``download``, pas de temp),
     le PREMIER morceau part AVANT la fin de la lecture du stockage ;
  2. lecture par morceaux BORNÉS (``STREAM_CHUNK_SIZE``) : jamais le fichier
     entier en une lecture (sinon premier octet repoussé à la fin) ;
  3. déconnexion client → le flux est fermé (canal SFTP rendu, pas de fuite) ;
  4. repli (open_stream indisponible) : temp complet utilisé puis SUPPRIMÉ ;
  5. Content-Length + Content-Disposition d'attachement conservés ;
  6. échec du repli → 500 JSON, pas de réponse partielle.

Contrôles négatifs par mutation (chacun DOIT faire rougir ce fichier) :
  M1  forcer le chemin temp (open_stream → None)      → test_1 rouge (download appelé)
  M2  lire tout en UN read (supprimer la boucle)      → test_2 rouge (lecture non bornée)
  M3  supprimer le finally stream.close()             → test_3 rouge (flux non fermé)
  M4  revenir à call_on_close seul pour le temp       → test_4 rouge (temp restant)
  M5  utiliser len(payload) reçu au lieu de la taille DB → test_5 rouge
"""

import os

import pytest

import routes.files as files_mod
from routes.helpers import get_db

_USER_ID = "dl-stream-user"
PAYLOAD = bytes(range(256)) * 4096  # 1 Mo exactement
STREAM_CHUNK = files_mod.STREAM_CHUNK_SIZE


# ── Helpers ──────────────────────────────────────────────────────────


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


def _seed_upload(upload_id, filename, size, final_path="models/x.bin"):
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


class _ProbeStream:
    """Flux de lecture factice : trace les ``read(n)`` et la fermeture."""

    def __init__(self, payload, slice_size):
        self.payload = payload
        self.slice_size = slice_size
        self.pos = 0
        self.reads = []
        self.closed = False
        self.eof = False

    def read(self, n):
        if self.pos >= len(self.payload):
            self.eof = True
            return b""
        end = min(self.pos + min(n, self.slice_size), len(self.payload))
        buf = self.payload[self.pos:end]
        self.pos = end
        self.reads.append(len(buf))
        return buf

    def close(self):
        self.closed = True


class _FakeStorage:
    """Stockage factice : open_stream (nominal) OU download (repli)."""

    def __init__(self, stream=None, tmp_content=b"", download_ok=True,
                 tmp_name=None):
        self.stream = stream
        self.tmp_content = tmp_content
        self.download_ok = download_ok
        self.tmp_name = tmp_name
        self.download_called = False
        self.download_calls = 0
        self.open_stream_called = False

    def open_stream(self, remote_path):
        self.open_stream_called = True
        return self.stream

    def download(self, remote_path, local_path):
        self.download_called = True
        self.download_calls += 1
        if self.download_ok:
            with open(local_path, "wb") as f:
                f.write(self.tmp_content)
        return self.download_ok

    def exists(self, remote_path):
        return True


def _dl_temp_files():
    try:
        return [f for f in os.listdir(files_mod.TEMP_DIR) if f.startswith("dl_")]
    except OSError:
        return []


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM file_uploads WHERE upload_id LIKE 'dl-stream-%'")
        conn.commit()
    finally:
        conn.close()
    for name in _dl_temp_files():
        try:
            os.remove(os.path.join(files_mod.TEMP_DIR, name))
        except OSError:
            pass


def _get_streaming(client, upload_id, headers):
    """GET non bufferisé : permet d'observer l'état APRÈS CHAQUE morceau."""
    return client.get(
        f"/api/files/{upload_id}/download", headers=headers, buffered=False
    )


# ── 1. Streaming nominal : premier octet avant la fin de la lecture ──


def test_stream_premier_octet_avant_fin_de_lecture(client, make_token, monkeypatch):
    """Le 1er morceau client part AVANT que le stockage soit intégralement lu.

    Mutation M1 (storage.download préalable, chemin historique) : le client ne
    reçoit RIEN tant que la copie complète n'est pas finie → l'assertion
    ``not eof_at_first_chunk`` ET ``download_called is False`` rougissent.
    """
    headers = _headers(make_token)
    _seed_upload("dl-stream-1", "modele.safetensors", len(PAYLOAD))
    probe = _ProbeStream(PAYLOAD, slice_size=256 * 1024)
    fake = _FakeStorage(stream=probe)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = _get_streaming(client, "dl-stream-1", headers)
    assert resp.status_code == 200

    chunks = []
    state_at_first_chunk = None
    for chunk in resp.response:
        if not chunks:
            state_at_first_chunk = {
                "eof": probe.eof,
                "bytes_read_from_storage": probe.pos,
                "dl_temp_files": _dl_temp_files(),
            }
        chunks.append(chunk)

    assert b"".join(chunks) == PAYLOAD, "octets servis == fichier source"
    assert state_at_first_chunk is not None, "aucun morceau reçu"
    assert state_at_first_chunk["eof"] is False, (
        "le stockage était DÉJÀ entièrement lu au premier morceau client : "
        "ce n'est pas un streaming (préchargement complet)")
    assert state_at_first_chunk["bytes_read_from_storage"] < len(PAYLOAD), (
        "tout le fichier avait été lu du stockage avant le premier octet client")
    assert state_at_first_chunk["dl_temp_files"] == [], (
        "un fichier temp dl_* existait pendant le streaming (double disque)")
    assert fake.download_called is False, (
        "storage.download (copie complète) ne doit PAS être appelé sur le "
        "chemin streaming : c'est le double transfert d'origine")
    assert fake.open_stream_called is True


# ── 2. Lecture par morceaux bornés ───────────────────────────────────


def test_stream_lectures_bornees_au_chunk_size(client, make_token, monkeypatch):
    """Aucune lecture > STREAM_CHUNK_SIZE et plusieurs lectures pour > 1 chunk.

    Mutation M2 (lire tout le fichier en un seul ``read()``) : la lecture
    dépasserait ``STREAM_CHUNK_SIZE`` → rouge. Sans cette borne, le premier
    octet client attendrait la lecture SFTP complète (des minutes à 13,5 Go).
    """
    headers = _headers(make_token)
    payload = PAYLOAD * 3  # 3 Mo → 3 morceaux de 1 Mo
    _seed_upload("dl-stream-2", "gros.safetensors", len(payload))
    probe = _ProbeStream(payload, slice_size=4 * 1024 * 1024)  # le flux sait tout rendre
    fake = _FakeStorage(stream=probe)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = _get_streaming(client, "dl-stream-2", headers)
    body = b"".join(resp.response)

    assert body == payload
    assert probe.reads, "aucune lecture du stockage"
    assert max(probe.reads) <= STREAM_CHUNK, (
        f"lecture de {max(probe.reads)} octets > STREAM_CHUNK_SIZE "
        f"({STREAM_CHUNK}) : le premier octet serait repoussé à la fin")
    assert len(probe.reads) >= 3, (
        f"{len(probe.reads)} lecture(s) pour {len(payload)} octets : le fichier "
        "doit être lu par morceaux (1 Mo)")


# ── 3. Déconnexion client → flux fermé (pas de canal fuité) ──────────


def test_deconnexion_client_ferme_le_flux(client, make_token, monkeypatch):
    """Client qui coupe en plein transfert → ``stream.close()`` appelé.

    Mutation M3 (supprimer le ``finally`` qui ferme le flux) : le canal SFTP
    resterait réservé indéfiniment (fuite du pool, downloads suivants bloqués)
    → rouge.
    """
    headers = _headers(make_token)
    _seed_upload("dl-stream-3", "coupe.safetensors", len(PAYLOAD))
    probe = _ProbeStream(PAYLOAD, slice_size=128 * 1024)
    fake = _FakeStorage(stream=probe)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = _get_streaming(client, "dl-stream-3", headers)
    it = iter(resp.response)
    first = next(it)
    assert first, "premier morceau non vide"
    assert probe.closed is False, "le flux ne doit pas être fermé en plein transfert"

    resp.close()  # déconnexion client explicite
    assert probe.closed is True, (
        "déconnexion client : le flux du storage doit être FERMÉ "
        "(sinon canal SFTP fuité)")


# ── 4. Repli : temp complet puis SUPPRIMÉ ────────────────────────────


def test_repli_temp_complet_utilise_puis_supprime(client, make_token, monkeypatch):
    """``open_stream`` indisponible → temp complet, puis nettoyage GARANTI.

    Le repli reste fonctionnel (octets corrects) mais SANS fuite disque.

    Mutation M4 (revenir à ``response.call_on_close`` seul) : le temp complet
    resterait sur le disque après la réponse (vérifié au harnais) → rouge.
    """
    headers = _headers(make_token)
    payload = PAYLOAD * 2
    _seed_upload("dl-stream-4", "repli.safetensors", len(payload))
    fake = _FakeStorage(stream=None, tmp_content=payload)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = _get_streaming(client, "dl-stream-4", headers)
    assert resp.status_code == 200
    chunks = list(resp.response)

    assert b"".join(chunks) == payload, "le repli sert bien le fichier complet"
    assert fake.download_called is True, "le repli utilise storage.download"
    assert _dl_temp_files() == [], (
        "le temp complet créé par le repli doit être SUPPRIMÉ après la réponse "
        f"(fichiers restants : {_dl_temp_files()})")


# ── 5. En-têtes : Content-Length (taille DB) + attachment ────────────


def test_content_length_et_attachment(client, make_token, monkeypatch):
    """Content-Length = taille DB (pas un re-comptage hasardeux) + attachment.

    Mutation M5 : ignorer la taille DB rendrait l'en-tête absent → rouge.
    """
    headers = _headers(make_token)
    _seed_upload("dl-stream-5", "en-tetes.safetensors", len(PAYLOAD))
    fake = _FakeStorage(stream=_ProbeStream(PAYLOAD, slice_size=256 * 1024))
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = _get_streaming(client, "dl-stream-5", headers)
    assert resp.status_code == 200
    assert resp.headers.get("Content-Length") == str(len(PAYLOAD)), (
        "Content-Length doit venir de la taille du fichier (progression/ETA client)")
    disposition = resp.headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment"), disposition
    assert "en-tetes.safetensors" in disposition, disposition
    assert resp.headers.get("Content-Type", "").startswith("application/octet-stream")
    b"".join(resp.response)


# ── 6. Échec du repli → 500 JSON (jamais de réponse partielle) ───────


def test_repli_echec_download_500(client, make_token, monkeypatch):
    headers = _headers(make_token)
    _seed_upload("dl-stream-6", "indispo.safetensors", 1024)
    fake = _FakeStorage(stream=None, download_ok=False)
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp = client.get("/api/files/dl-stream-6/download", headers=headers)

    assert resp.status_code == 500
    assert "Échec" in resp.get_json()["error"] or "échec" in resp.get_json()["error"].lower()
    assert _dl_temp_files() == []


# ── 7. Métadonnées invalides : contrats conservés ───────────────────


def test_upload_inconnu_404_et_statut_non_complete_400(client, make_token, monkeypatch):
    headers = _headers(make_token)
    fake = _FakeStorage(stream=_ProbeStream(b"x", slice_size=1))
    monkeypatch.setattr(files_mod, "get_storage", lambda: fake)

    resp404 = client.get("/api/files/dl-stream-inconnu/download", headers=headers)
    assert resp404.status_code == 404

    _seed_upload("dl-stream-7", "partiel.safetensors", 10)
    conn = get_db()
    conn.execute("UPDATE file_uploads SET status = 'uploading' WHERE upload_id = ?", ("dl-stream-7",))
    conn.commit()
    conn.close()
    resp400 = client.get("/api/files/dl-stream-7/download", headers=headers)
    assert resp400.status_code == 400
    assert fake.open_stream_called is False, "aucune lecture du stockage sans fichier complet"
