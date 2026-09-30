"""Finalisation d'upload fichier : reprise après échec + nettoyage des orphelins.

Contexte (bug réel côté pack ComfyUI-AI-Helper) : la finalisation
(``POST /api/files/complete``) recopie le fichier TEMPORAIRE COMPLET vers le
stockage. Cette étape est longue pour un gros modèle (13,5 Go) et peut échouer
ou être coupée côté client. Ce fichier vérifie le CONTRAT serveur qui rend
l'échec propre et REJOUABLE :

  1. échec de la recopie vers le stockage → 500, ligne ``status='error'`` et
     fichier temporaire SUPPRIMÉ (pas de 13 Go orphelin) ;
  2. l'upload est REJOUABLE : un nouvel init/chunk/complete après l'échec
     aboutit, sans laisser d'ancienne ligne ni de temp résiduel ;
  3. les uploads ABANDONNÉS (``status='uploading'`` ancien : client fermé) sont
     purgés au prochain ``/files/init`` — temp supprimé + ligne ``'error'`` ;
  4. contrôle NÉGATIF : un upload RÉCENT en cours n'est JAMAIS purgé (un gros
     transfert légitime qui dure des heures ne doit pas être cassé) ;
  5. confinement : la purge ne supprime QUE sous ``TEMP_DIR`` (jamais un chemin
     arbitraire stocké en base).

Usage : TMPDIR=/projects/.aih_tmp /projects/AI-Helper/.venv/bin/python -m pytest backend/tests
"""

import io
import os

import pytest
import storage as storage_module
from storage import LocalStorage

_USER_ID = "fu-recovery-user"


# ── Helpers ──────────────────────────────────────────────────────────

def _ensure_user(user_id=_USER_ID):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, role) VALUES (?, ?, ?)",
        (user_id, f"user_{user_id[:8]}", "user"),
    )
    conn.commit()
    conn.close()


def _headers(make_token, user_id=_USER_ID):
    _ensure_user(user_id)
    return {"Authorization": f"Bearer {make_token(user_id)}"}


@pytest.fixture()
def file_storage(tmp_path, monkeypatch):
    """LocalStorage isolé + TEMP_DIR isolé (aucune écriture dans le repo ni /tmp)."""
    from routes import files as files_module

    st = LocalStorage(str(tmp_path / "uploads"))
    monkeypatch.setattr(storage_module, "_storage_instance", st, raising=False)
    temp_dir = tmp_path / "aih_uploads"
    temp_dir.mkdir()
    monkeypatch.setattr(files_module, "TEMP_DIR", str(temp_dir))
    yield st, temp_dir


def _init(client, headers, *, filename="model.safetensors", size=8, ftype="checkpoint"):
    return client.post("/api/files/init", json={
        "filename": filename, "size": size, "type": ftype,
    }, headers=headers)


def _chunk(client, headers, upload_id, content, chunk_size):
    for i in range(0, len(content), chunk_size):
        rc = client.post(
            "/api/files/chunk",
            data={
                "upload_id": upload_id,
                "chunk_index": str(i // chunk_size),
                "data": (io.BytesIO(content[i:i + chunk_size]), "model.safetensors"),
            },
            headers=headers,
        )
        assert rc.status_code == 200, rc.get_data(as_text=True)


def _row(upload_id):
    from routes.helpers import get_db

    conn = get_db()
    try:
        return conn.execute(
            "SELECT status, temp_path, final_path, received_chunks, total_chunks "
            "FROM file_uploads WHERE upload_id = ?", (upload_id,)
        ).fetchone()
    finally:
        conn.close()


def _cleanup_upload(upload_id):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute("DELETE FROM file_uploads WHERE upload_id = ?", (upload_id,))
    conn.commit()
    conn.close()


# ── 1. Échec de la recopie → 'error' + temp nettoyé ──────────────────

def test_complete_storage_failure_marks_error_and_cleans_temp(client, make_token, file_storage):
    st, temp_dir = file_storage
    headers = _headers(make_token)
    payload = b"0123456789"

    r = _init(client, headers, size=len(payload))
    assert r.status_code == 200, r.get_data(as_text=True)
    upload_id = r.get_json()["upload_id"]
    _chunk(client, headers, upload_id, payload, r.get_json()["chunk_size"])

    temp_path = _row(upload_id)["temp_path"]
    assert os.path.isfile(temp_path), "le temp doit exister avant la finalisation"

    # Mutation : sans le nettoyage, le temp de 13 Go resterait.
    st.upload = lambda *a, **k: False  # type: ignore[assignment]
    rc = client.post("/api/files/complete", json={"upload_id": upload_id}, headers=headers)
    assert rc.status_code == 500, rc.get_data(as_text=True)

    row = _row(upload_id)
    assert row["status"] == "error", "un échec de stockage doit marquer la ligne 'error'"
    assert not os.path.isfile(temp_path), "le fichier temporaire doit être supprimé (pas d'orphelin)"
    assert not os.path.exists(temp_path)
    _cleanup_upload(upload_id)


# ── 2. Rejouabilité après échec ──────────────────────────────────────

def test_complete_is_replayable_after_failure(client, make_token, file_storage):
    """Après un échec, un nouvel upload du MÊME fichier aboutit (aucune ligne
    fantôme, aucun temp accumulé)."""
    st, temp_dir = file_storage
    headers = _headers(make_token)
    payload = b"abcdefghij"

    # 1er essai : échec de stockage.
    r = _init(client, headers, size=len(payload))
    first_id = r.get_json()["upload_id"]
    _chunk(client, headers, first_id, payload, r.get_json()["chunk_size"])
    temp_first = _row(first_id)["temp_path"]
    st.upload = lambda *a, **k: False  # type: ignore[assignment]
    assert client.post("/api/files/complete", json={"upload_id": first_id},
                       headers=headers).status_code == 500

    # 2e essai : le stockage fonctionne → ça doit passer.
    del st.upload  # restaure la méthode de la classe
    r2 = _init(client, headers, size=len(payload), ftype="checkpoint")
    assert r2.status_code == 200
    second_id = r2.get_json()["upload_id"]
    assert second_id != first_id, "un nouvel upload doit générer un NOUVEL upload_id"
    _chunk(client, headers, second_id, payload, r2.get_json()["chunk_size"])
    rc = client.post("/api/files/complete", json={"upload_id": second_id}, headers=headers)
    assert rc.status_code == 200, rc.get_data(as_text=True)
    final_path = rc.get_json()["file_path"]
    assert st.exists(final_path), "le fichier doit être présent dans le stockage après reprise"

    assert _row(first_id)["status"] == "error"
    assert _row(second_id)["status"] == "complete"
    assert not os.path.isfile(temp_first), "le temp du 1er essai ne doit pas rester"
    # Aucun temp résiduel sous TEMP_DIR après la reprise.
    leftovers = [p for p in os.listdir(str(temp_dir)) if p.endswith(".tmp")]
    assert leftovers == [], f"temps orphelins : {leftovers}"
    _cleanup_upload(first_id)
    _cleanup_upload(second_id)


# ── 3. Purge des uploads abandonnés ──────────────────────────────────

def _seed_uploading_row(upload_id, temp_path, *, age_hours=48, size=1024):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT INTO file_uploads (upload_id, user_id, filename, size, type, "
        "chunk_size, total_chunks, received_chunks, temp_path, final_path, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'uploading', datetime('now', ?))",
        (upload_id, _USER_ID, "gros.safetensors", size, "checkpoint",
         25 * 1024 * 1024, 1, 0, temp_path, f"-{age_hours} hours"),
    )
    conn.commit()
    conn.close()


def test_stale_uploading_row_is_purged_on_next_init(client, make_token, file_storage):
    """Un upload abandonné (client fermé) ne doit pas laisser son temp pour
    toujours : la purge opportuniste le nettoie au prochain /files/init."""
    st, temp_dir = file_storage
    headers = _headers(make_token)
    orphan = temp_dir / "abandonne.tmp"
    orphan.write_bytes(b"z" * 2048)
    _seed_uploading_row("stale-1", str(orphan), age_hours=48)

    assert orphan.exists()
    r = _init(client, headers, filename="nouveau.safetensors")
    assert r.status_code == 200, r.get_data(as_text=True)
    new_id = r.get_json()["upload_id"]

    assert not orphan.exists(), "le temp de l'upload abandonné doit être supprimé"
    assert _row("stale-1")["status"] == "error", "la ligne abandonnée passe en 'error'"
    _cleanup_upload("stale-1")
    _cleanup_upload(new_id)


def test_fresh_uploading_row_is_not_purged(client, make_token, file_storage):
    """NEGATIVE : un upload RÉCENT en cours (gros transfert légitime) doit
    survivre — sinon la purge casserait l'upload en cours de l'utilisateur."""
    st, temp_dir = file_storage
    headers = _headers(make_token)
    in_progress = temp_dir / "en-cours.tmp"
    in_progress.write_bytes(b"y" * 2048)
    _seed_uploading_row("fresh-1", str(in_progress), age_hours=0)  # created_at ≈ maintenant

    r = _init(client, headers, filename="autre.safetensors")
    assert r.status_code == 200
    new_id = r.get_json()["upload_id"]

    assert in_progress.exists(), "un upload en cours ne doit JAMAIS être purgé"
    assert _row("fresh-1")["status"] == "uploading"
    _cleanup_upload("fresh-1")
    _cleanup_upload(new_id)


def test_purge_never_deletes_outside_temp_dir(client, make_token, file_storage, tmp_path):
    """Confinement : même pour une ligne abandonnée, on ne supprime JAMAIS un
    fichier hors de TEMP_DIR (chemin arbitraire en base)."""
    st, temp_dir = file_storage
    headers = _headers(make_token)
    precious = tmp_path / "a-ne-pas-supprimer.txt"
    precious.write_text("important", encoding="utf-8")
    _seed_uploading_row("stale-outside", str(precious), age_hours=72)

    r = _init(client, headers, filename="x.safetensors")
    assert r.status_code == 200
    new_id = r.get_json()["upload_id"]

    assert precious.exists(), "la purge doit rester confinée à TEMP_DIR"
    assert precious.read_text(encoding="utf-8") == "important"
    assert _row("stale-outside")["status"] == "error"
    _cleanup_upload("stale-outside")
    _cleanup_upload(new_id)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
