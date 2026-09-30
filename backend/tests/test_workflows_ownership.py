"""Tests de PROPRIÉTÉ des workflows partagés — ``is_mine`` calculé côté serveur.

Contexte (bug prouvé côté front) : ``checkExisting`` de
``js/aih_workflow_share.js`` comparait ``items[i].user_id === me.id`` alors que
``GET /api/workflows`` (liste) n'expose PAS ``user_id`` → la détection « mettre à
jour l'existant » ne se déclenchait jamais → publications en DOUBLON.

Correctif : le serveur calcule un booléen ``is_mine`` (liste ET détail) ; aucun
identifiant interne (``user_id``) n'est exposé — pas d'énumération possible.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection régresse) :
  - ``test_list_exposes_is_mine_without_user_id`` : la présence d'un ``user_id``
    (ou ``owner_id``) dans un item ferait échouer l'assertion ;
  - ``test_detail_exposes_is_mine_without_user_id`` : idem sur le détail ;
  - ``test_put_delete_other_user_forbidden`` : sans garde, on obtiendrait 200.
"""

import json

import pytest

# ── Helpers ────────────────────────────────────────────────────────────


def _ensure_user(user_id, role="user"):
    from routes.helpers import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (id, username, display_name, role) VALUES (?, ?, ?, ?)",
        (user_id, f"user_{user_id}", f"Nom {user_id}", role),
    )
    conn.commit()
    conn.close()


def _headers(make_token, user_id, role="user"):
    _ensure_user(user_id, role)
    return {"Authorization": f"Bearer {make_token(user_id, role=role)}"}


@pytest.fixture(autouse=True)
def _workflow_isolation():
    """Nettoie les workflows/uploads après CHAQUE test du module.

    La base est partagée (fixture ``app`` session) : les lignes
    ``shared_workflows``/``file_uploads`` référencent ``users`` (FK) et
    feraient échouer les tests ultérieurs qui vident ``users``.
    """
    yield
    from routes.helpers import get_db

    conn = get_db()
    try:
        conn.execute("DELETE FROM shared_workflows")
        conn.execute("DELETE FROM file_uploads")
        conn.commit()
    finally:
        conn.close()


def _publish(client, headers, name="Mon workflow"):
    resp = client.post(
        "/api/workflows",
        json={"name": name, "workflow_json": json.dumps({"nodes": [], "links": []})},
        headers=headers,
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ── 1. Liste : is_mine + AUCUN user_id ────────────────────────────────


def test_list_exposes_is_mine_without_user_id(client, make_token):
    alice = _headers(make_token, "wf-alice")
    bob = _headers(make_token, "wf-bob")
    wf_id = _publish(client, alice, "Partagé")

    data = client.get("/api/workflows", headers=alice).get_json()
    items = data["items"]
    assert len(items) >= 1, "le workflow publié doit apparaître"
    mine = next(i for i in items if i["id"] == wf_id)
    assert mine["is_mine"] is True, "le propriétaire doit voir is_mine=true"

    # Contrôle NÉGATIF : aucun identifiant de propriétaire dans la liste.
    for item in items:
        assert "user_id" not in item, f"user_id ne doit PAS être exposé : {item}"
        assert "owner_id" not in item, f"owner_id ne doit PAS être exposé : {item}"

    data_bob = client.get("/api/workflows", headers=bob).get_json()
    mine_bob = next(i for i in data_bob["items"] if i["id"] == wf_id)
    assert mine_bob["is_mine"] is False, "un autre utilisateur doit voir is_mine=false"
    assert "user_id" not in mine_bob


# ── 2. Détail : is_mine + AUCUN user_id ───────────────────────────────


def test_detail_exposes_is_mine_without_user_id(client, make_token):
    alice = _headers(make_token, "wf-alice")
    bob = _headers(make_token, "wf-bob")
    wf_id = _publish(client, alice, "Détail")

    d_alice = client.get(f"/api/workflows/{wf_id}", headers=alice).get_json()
    assert d_alice["is_mine"] is True
    assert "user_id" not in d_alice, "le détail ne doit PAS exposer user_id"

    d_bob = client.get(f"/api/workflows/{wf_id}", headers=bob).get_json()
    assert d_bob["is_mine"] is False
    assert "user_id" not in d_bob, "le détail ne doit PAS exposer user_id"


# ── 3. Défense en profondeur : PUT/DELETE d'autrui restent 403 ────────


def test_put_delete_other_user_forbidden(client, make_token):
    alice = _headers(make_token, "wf-alice")
    bob = _headers(make_token, "wf-bob")
    wf_id = _publish(client, alice, "Protégé")

    # PUT d'autrui → 403 (même si le front ne propose plus de le faire).
    r_put = client.put(f"/api/workflows/{wf_id}", json={"name": "pirate"}, headers=bob)
    assert r_put.status_code == 403, r_put.get_data(as_text=True)

    # DELETE d'autrui → 403.
    r_del = client.delete(f"/api/workflows/{wf_id}", headers=bob)
    assert r_del.status_code == 403, r_del.get_data(as_text=True)

    # Le propriétaire, lui, peut modifier puis supprimer.
    assert client.put(
        f"/api/workflows/{wf_id}", json={"name": "à moi"}, headers=alice
    ).status_code == 200
    assert client.delete(f"/api/workflows/{wf_id}", headers=alice).status_code == 200
