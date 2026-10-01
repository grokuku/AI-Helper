"""Marqueur de version/santé du backend : ``GET /api/health``.

Contexte : le correctif de streaming du download est un changement PYTHON du
backend AI-Helper (service SÉPARÉ de ComfyUI). Un backend non redémarré qui
précharge encore 13,5 Go ne se distingue pas d'un blocage sans marqueur. La
route expose donc le build ET la capacité ``download_streaming`` pour trancher
en 5 secondes (et pour que le pack refuse un transfert muet AVANT de lancer).

Contrôles négatifs par mutation (chacun DOIT faire rougir ce fichier) :
  M1  retirer la clé ``features.download_streaming`` (ou la passer à False)
      → test_health_declare_le_streaming rouge ;
  M2  ne plus enregistrer la route (``from routes.health import *`` retiré
      de app.py) → 404 → rouge ;
  M3  exposer un secret (ex. mot de passe SFTP) dans le payload → rouge.
"""

import routes.files as files_mod
from storage import SFTP_TIMEOUT


def test_health_repond_sans_authentification(client):
    """Diagnostic accessible SANS jeton (curl en 5 s), réponse JSON stable.

    NB : ce test N'IMPORTE PAS ``routes.health`` directement — sinon l'import
    enregistrerait la route et la mutation « route non enregistrée dans
    app.py » deviendrait indétectable (c'est ce que le contrôle M2 vérifie).
    """
    resp = client.get('/api/health')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['service'] == 'ai-helper-backend'
    assert isinstance(data['build'], str) and data['build'].startswith('backend-'), (
        f"marqueur de build backend attendu, obtenu {data.get('build')!r}")
    assert isinstance(data.get('git', ''), (str, type(None)))


def test_health_declare_le_streaming(client):
    """La capacité de streaming + ses réglages réels sont publiés.

    MUTATION M1 : sans ``download_streaming: true``, le pack ne peut plus
    détecter un backend obsolète → rouge.
    """
    data = client.get('/api/health').get_json()
    assert data['features']['download_streaming'] is True, (
        "le marqueur doit déclarer la capacité de streaming du download "
        "(c'est LUI que le pack vérifie avant un transfert)")
    assert data['features']['download_stream_chunk_size'] == files_mod.STREAM_CHUNK_SIZE
    assert data['features']['download_stream_idle_timeout_s'] == files_mod.STREAM_IDLE_TIMEOUT
    # Le délai d'inactivité du flux doit laisser le timeout socket SFTP agir
    # d'abord (sinon le watchdog couperait des canaux simplement lents).
    assert files_mod.STREAM_IDLE_TIMEOUT > SFTP_TIMEOUT, (
        f"STREAM_IDLE_TIMEOUT={files_mod.STREAM_IDLE_TIMEOUT} doit être > "
        f"SFTP_TIMEOUT={SFTP_TIMEOUT}")


def test_health_ne_fuite_aucun_secret(client):
    """Aucune valeur sensible dans le payload (MUTATION M3 : y ajouter un secret)."""
    raw = client.get('/api/health').get_data(as_text=True).lower()
    for needle in ('password', 'secret', 'token', 'api_key', 'private'):
        assert needle not in raw, f"le payload de santé contient « {needle} » : fuite"
