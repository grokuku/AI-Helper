"""Tests de ``POST /api/files/check-batch`` — existence AVANT upload.

Contexte (bug réel côté pack ComfyUI-AI-Helper) : l'onglet 📤 Partager sautait
SILENCIEUSEMENT les fichiers déjà présents (déduplication par fingerprint) tout
en les affichant comme des succès, avec des débits absurdes (« 140 391 MB/s »).
Le correctif expose un check PAR LOT avant l'envoi :

    POST /api/files/check-batch
      {\"items\": [{\"filename\", \"size\", \"head\", \"tail\"}, ...]}
    → {\"items\": [{\"filename\", \"status\": \"identical\"|\"different\"|\"absent\",
                    \"remote\": {upload_id, filename, size, file_path, created_at}|null}]}

Contrôles NÉGATIFS (mutation) intégrés — chacun DOIT rougir si le code régresse :
  - ``test_same_size_different_tail_is_different`` : si l'implémentation ne
    comparait que la TAILLE, elle répondrait ``identical`` → assertion rouge ;
  - ``test_complete_but_missing_on_storage_is_absent`` : si ``storage.exists``
    était oublié, un fichier fantôme (ligne DB sans fichier) serait annoncé
    présent → assertion rouge ;
  - ``test_most_recent_identical_wins`` : si l'ordre ``created_at DESC`` sautait,
    un écrasement récent pourrait renvoyer l'ANCIEN upload_id (l'écrasement
    serait alors invisible pour les téléchargements suivants) → rouge ;
  - ``test_remote_never_exposes_user_id`` : garde anti-énumération d'identités.

La base est partagée (fixture ``app`` session) : nettoyage ``file_uploads``
APRÈS chaque test (FK vers ``users``) + suppression des fichiers créés.
"""

import os
import tempfile

import pytest

# ── Helpers ──────────────────────────────────────────────────────────

_USER_ID = "fb-check-user"
_created_storage_paths = []


def _ensure_user(user_id=_USER_ID):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, display_name, role) VALUES (?, ?, ?, ?)",
        (user_id, user_id, f"Nom {user_id}", "user"),
    )
    conn.commit()
    conn.close()


def _headers(make_token, user_id=_USER_ID):
    _ensure_user(user_id)
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _seed_upload(upload_id, filename, size, head, tail, content=None, created_at=None):
    """Insère une ligne ``file_uploads`` complète (+ fichier réel si content).

    ``content`` : octets écrits réellement sur le stockage (None = ligne
    fantôme volontaire, fichier absent).
    """
    from routes.helpers import get_db
    from storage import get_storage

    storage = get_storage()
    remote_path = f"workflows/models/{upload_id}/{filename}"
    if content is not None:
        fd, tmp = tempfile.mkstemp(prefix="fb_seed_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            assert storage.upload(tmp, remote_path), "seed: upload stockage"
            _created_storage_paths.append(remote_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    conn = get_db()
    conn.execute(
        "INSERT INTO file_uploads (upload_id, user_id, filename, size, type, "
        "chunk_size, total_chunks, received_chunks, temp_path, final_path, status, "
        "fingerprint_head, fingerprint_tail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (upload_id, _USER_ID, filename, size, "checkpoint",
         25 * 1024 * 1024, 1, 1, "", remote_path, "complete",
         head, tail, created_at),
    )
    conn.commit()
    conn.close()
    return remote_path


@pytest.fixture(autouse=True)
def _cleanup_file_uploads():
    """Nettoie lignes + fichiers créés après CHAQUE test du module."""
    yield
    from routes.helpers import get_db
    from storage import get_storage

    conn = get_db()
    try:
        conn.execute("DELETE FROM file_uploads")
        conn.commit()
    finally:
        conn.close()

    storage = get_storage()
    while _created_storage_paths:
        storage.delete(_created_storage_paths.pop())


def _post_batch(client, headers, items):
    return client.post("/api/files/check-batch", json={"items": items}, headers=headers)


# ── 1. Contrat nominal : identique / différent / absent ─────────────


def test_identical_existing_file_reports_identical(client, make_token):
    headers = _headers(make_token)
    remote = _seed_upload("fb-1", "model.safetensors", 1000, "h1", "t1", content=b"x" * 1000)

    resp = _post_batch(client, headers, [
        {"filename": "model.safetensors", "size": 1000, "head": "h1", "tail": "t1"},
    ])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    item = resp.get_json()["items"][0]
    assert item["status"] == "identical"
    assert item["remote"]["upload_id"] == "fb-1"
    assert item["remote"]["size"] == 1000
    assert item["remote"]["file_path"] == remote
    # Contrôle NÉGATIF : aucun identifiant interne exposé (anti-énumération).
    assert "user_id" not in item["remote"], f"user_id ne doit PAS être exposé : {item}"


def test_same_size_different_tail_is_different(client, make_token):
    """MÊME taille, empreinte différente → ``different`` (jamais ``identical``).

    Mutation : remplacer la comparaison taille+head+tail par une comparaison de
    taille seule ferait répondre ``identical`` → ce test rougit.
    """
    headers = _headers(make_token)
    _seed_upload("fb-2", "same-size.safetensors", 1000, "h1", "t1", content=b"x" * 1000)

    resp = _post_batch(client, headers, [
        {"filename": "same-size.safetensors", "size": 1000, "head": "h1", "tail": "AUTRE"},
    ])
    item = resp.get_json()["items"][0]
    assert item["status"] == "different", "même taille mais empreinte différente ≠ identique"
    assert item["remote"]["upload_id"] == "fb-2", "la version serveur actuelle est référencée"
    assert item["remote"]["size"] == 1000


def test_different_size_is_different(client, make_token):
    headers = _headers(make_token)
    _seed_upload("fb-3", "gros.safetensors", 2000, "h1", "t1", content=b"y" * 2000)

    resp = _post_batch(client, headers, [
        {"filename": "gros.safetensors", "size": 1000, "head": "h1", "tail": "t1"},
    ])
    item = resp.get_json()["items"][0]
    assert item["status"] == "different", "taille différente → déjà présent (version différente)"
    assert item["remote"]["size"] == 2000, "la taille SERVEUR est renvoyée (affichage avant écrasement)"


def test_absent_when_never_uploaded(client, make_token):
    headers = _headers(make_token)
    resp = _post_batch(client, headers, [
        {"filename": "inconnu.safetensors", "size": 42, "head": "h", "tail": "t"},
    ])
    item = resp.get_json()["items"][0]
    assert item["status"] == "absent"
    assert item["remote"] is None


# ── 2. Fantômes + ordre « plus récent d'abord » ─────────────────────


def test_complete_but_missing_on_storage_is_absent(client, make_token):
    """Ligne DB ``complete`` SANS fichier → ``absent`` + ligne marquée ``error``.

    Mutation : oublier ``storage.exists`` annoncerait ``identical`` pour un
    fichier qui n'existe plus → le pack sauterait l'upload à tort.
    """
    headers = _headers(make_token)
    _seed_upload("fb-ghost", "fantome.safetensors", 1000, "h1", "t1", content=None)

    resp = _post_batch(client, headers, [
        {"filename": "fantome.safetensors", "size": 1000, "head": "h1", "tail": "t1"},
    ])
    assert resp.get_json()["items"][0]["status"] == "absent"

    from routes.helpers import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status FROM file_uploads WHERE upload_id = 'fb-ghost'"
        ).fetchone()
        assert row["status"] == "error", "le fantôme doit être invalidé pour ne plus matcher"
    finally:
        conn.close()


def test_most_recent_identical_wins(client, make_token):
    """Après ÉCRASEMENT (2e upload des mêmes octets), le plus RÉCENT est renvoyé.

    Mutation : sans ``ORDER BY created_at DESC``, la déduplication suivante
    pourrait resélectionner l'ANCIEN upload_id → l'écrasement serait invisible
    pour les téléchargements ultérieurs.
    """
    headers = _headers(make_token)
    _seed_upload("fb-old", "ecrase.safetensors", 1000, "h1", "t1",
                 content=b"old", created_at="2026-01-01 10:00:00")
    _seed_upload("fb-new", "ecrase.safetensors", 1000, "h1", "t1",
                 content=b"new", created_at="2026-02-02 10:00:00")

    resp = _post_batch(client, headers, [
        {"filename": "ecrase.safetensors", "size": 1000, "head": "h1", "tail": "t1"},
    ])
    assert resp.get_json()["items"][0]["remote"]["upload_id"] == "fb-new"

    # Même garantie sur la route historique /files/check (flux de dédup du pack).
    legacy = client.post("/api/files/check", json={"size": 1000, "head": "h1", "tail": "t1"},
                         headers=headers)
    assert legacy.get_json()["upload_id"] == "fb-new", \
        "après écrasement, /files/check doit renvoyer le fichier le plus récent"


def test_without_fingerprint_falls_back_to_name_match(client, make_token):
    """Sans size/head/tail : seule la correspondance par NOM est évaluable."""
    headers = _headers(make_token)
    _seed_upload("fb-name", "sans-fp.safetensors", 1000, "h1", "t1", content=b"x" * 1000)

    resp = _post_batch(client, headers, [{"filename": "sans-fp.safetensors"}])
    item = resp.get_json()["items"][0]
    assert item["status"] == "different", "nom trouvé sans empreinte → version différente"


# ── 3. Sécurité / payloads invalides ─────────────────────────────────


def test_requires_authentication(client):
    resp = client.post("/api/files/check-batch", json={"items": [{"filename": "x"}]})
    assert resp.status_code == 401, "le check par lot exige un token comme /files/check"


@pytest.mark.parametrize("payload", [
    {},                                   # items absent
    {"items": []},                        # liste vide
    {"items": "pas une liste"},           # mauvais type
    {"items": [{"filename": "x"}] * 201},  # plafond dépassé
    {"items": ["pas un objet"]},           # item non-objet
])
def test_invalid_payload_400(client, make_token, payload):
    headers = _headers(make_token)
    resp = client.post("/api/files/check-batch", json=payload, headers=headers)
    assert resp.status_code == 400, f"payload {payload!r} doit être refusé"


def test_batch_returns_one_result_per_item_in_order(client, make_token):
    headers = _headers(make_token)
    _seed_upload("fb-multi", "present.safetensors", 10, "h", "t", content=b"x" * 10)
    resp = _post_batch(client, headers, [
        {"filename": "present.safetensors", "size": 10, "head": "h", "tail": "t"},
        {"filename": "absent.safetensors", "size": 5, "head": "h", "tail": "t"},
    ])
    items = resp.get_json()["items"]
    assert [i["filename"] for i in items] == ["present.safetensors", "absent.safetensors"]
    assert [i["status"] for i in items] == ["identical", "absent"]
