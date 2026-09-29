"""Tests du SERVICE PUBLIC des albums — phase 2 (``backend/public_app.py``).

Le service public est un process SÉPARÉ, en LECTURE SEULE, isolé du backend
privé : il ne sert que des fichiers sous ``AIH_ALBUM_WEB_DIR`` (manifest,
vignettes, grandes versions) et les assets de ``backend/public_web``.

Contrôles NÉGATIFS (un test doit ROUGIR si la protection disparaît) :
  - ``test_uniform_404_body_and_status`` : toute divergence de corps/statut
    entre clé inconnue, clé révoquée, item hors manifest, nom invalide et
    route inconnue ferait échouer l'égalité ;
  - ``test_no_cors_header_on_any_response`` : un ``Access-Control-Allow-Origin``
    résiduel ferait échouer l'assertion ;
  - ``test_unlisted_file_is_not_served`` : sans la vérification d'appartenance
    au manifest, un fichier présent sur disque serait servi ;
  - ``test_traversal_is_refused`` : sans confinement, ``..%2f`` sortirait du
    dossier de l'album ;
  - ``test_service_is_read_only`` : toute écriture disque ferait diverger
    l'empreinte avant/après ;
  - ``test_public_app_has_no_private_imports`` : tout import du privé
    (``db``, ``storage``, ``auth``, ``flask_cors``…) ferait échouer l'AST ;
  - ``test_page_js_uses_textcontent_never_innerhtml`` : un ``innerHTML`` dans
    ``album.js`` rouvrirait la porte à une XSS ;
  - ``test_guard_off_is_strictly_inert`` : le moindre 429/log en mode ``off``
    signalerait un garde-fou non désactivable (défaut du chantier) ;
  - ``test_guard_log_counts_without_blocking`` : un 429 en mode ``log``
    ferait échouer la promesse « observer sans bloquer » ;
  - ``test_guard_on_blocks_over_threshold_by_ip`` / ``…_404_burst`` : sans
    blocage par IP, l'abus/balayage ne serait pas coupé en mode ``on`` ;
  - ``test_guard_logs_never_leak_full_key`` : une ligne de log contenant la
    clé complète exposerait une capability dans ``public_server.log``.
"""

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import public_app
import pytest

# ── Constantes ────────────────────────────────────────────────────────

KEY_A = "A" * 43
KEY_UNKNOWN = "B" * 43
KEY_REVOKED = "C" * 43

REQUIRED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Credentials",
    "Access-Control-Allow-Methods",
    "Access-Control-Allow-Headers",
    "Access-Control-Expose-Headers",
)
UNIFORM_404_BODY = b"Not Found\n"
UNIFORM_405_BODY = b"Method Not Allowed\n"

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_WEB_DIR = os.path.join(BACKEND_DIR, "public_web")
ALBUM_JS_PATH = os.path.join(PUBLIC_WEB_DIR, "album.js")
ALBUM_CSS_PATH = os.path.join(PUBLIC_WEB_DIR, "album.css")
INDEX_HTML_PATH = os.path.join(PUBLIC_WEB_DIR, "index.html")
PUBLIC_APP_PATH = os.path.join(BACKEND_DIR, "public_app.py")
JSDOM_HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "album_public_page.mjs")


# ── Helpers ───────────────────────────────────────────────────────────


def _write_album(root, key, *, title="Mon album", description="Ma description", items=((1, ".jpg"),), manifest=True):
    """Crée un dossier album FACTICE conforme au format de la phase 1.

    ``items`` = suite de ``(numéro, extension)`` ; les vignettes sont toujours
    en ``.jpg`` ; la grande version porte l'extension de l'item.
    """
    album_dir = os.path.join(root, key)
    os.makedirs(os.path.join(album_dir, "thumb"), exist_ok=True)
    os.makedirs(os.path.join(album_dir, "full"), exist_ok=True)
    if manifest:
        body = {
            "title": title,
            "description": description,
            "count": len(items),
            "items": [{"i": n, "w": 100, "h": 80, "ext": e} for n, e in items],
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        with open(os.path.join(album_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)
    for n, e in items:
        with open(os.path.join(album_dir, "thumb", f"{n:04d}.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + f"thumb-{n}".encode())
        with open(os.path.join(album_dir, "full", f"{n:04d}{e}"), "wb") as f:
            f.write((b"\x89PNG\r\n" if e == ".png" else b"\xff\xd8\xff\xe0") + f"full-{n}".encode())
    return album_dir


def _tree_signature(root):
    """Empreinte (chemin relatif, taille, mtime_ns) de tout l'arbre."""
    sig = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            st = os.stat(p)
            sig[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return sig


def _collect_private_fields(obj, path="$"):
    """Liste les clés privées interdites trouvées dans un manifest."""
    forbidden = {"media_id", "user_id", "final_path", "storage_path", "path", "filename", "key", "origin_name"}
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in forbidden:
                found.append(f"{path}.{k}")
            found.extend(_collect_private_fields(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            found.extend(_collect_private_fields(v, f"{path}[{idx}]"))
    return found


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def public_env(tmp_path, monkeypatch):
    """Webroot d'albums temporaire + purge du cache RAM du manifest."""
    root = tmp_path / "albums"
    root.mkdir()
    monkeypatch.setenv(public_app.ALBUM_WEB_DIR_ENV, str(root))
    public_app.clear_manifest_cache()
    yield str(root)
    public_app.clear_manifest_cache()


@pytest.fixture()
def pub_client(public_env):  # noqa: ARG001 — dépend de l'environnement posé
    """Client Flask du service public (AUCUNE base, AUCUNE auth)."""
    public_app.app.config["TESTING"] = True
    return public_app.app.test_client()


@pytest.fixture(autouse=True)
def guard_env(monkeypatch):
    """État du garde-fou isolé pour CHAQUE test : mode absent + compteurs vides.

    Les compteurs sont globaux (clé par IP) : sans purge, une rafale d'un test
    pourrait bloquer les requêtes du test suivant (l'IP des tests est
    ``127.0.0.1`` par défaut).
    """
    public_app.clear_guard_state()
    monkeypatch.delenv(public_app.ALBUM_GUARD_ENV, raising=False)
    yield monkeypatch
    public_app.clear_guard_state()


# ── 1. Coquille HTML ──────────────────────────────────────────────────


def test_shell_is_served_without_inline_script(pub_client, public_env):
    _write_album(public_env, KEY_A)
    r = pub_client.get("/a/" + KEY_A)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    assert r.headers["Cache-Control"] == "no-cache"
    html = r.get_data(as_text=True)
    # AUCUN script inline : chaque <script> doit porter un src.
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags, "la coquille doit charger au moins un script externe"
    assert all("src=" in tag for tag in tags), f"script inline interdit : {tags}"
    # Briques vendues + logique de page.
    for asset in (
        "/assets/album.js",
        "/assets/album.css",
        "/assets/vendor/holaf/holaf-viewport.js",
        "/assets/vendor/holaf/holaf-virtual-grid.js",
        "/assets/vendor/holaf/holaf-lightbox.js",
    ):
        assert asset in html, f"asset manquant dans la coquille : {asset}"


def test_shell_unknown_key_serves_same_shell(pub_client, public_env):
    """Anti-énumération : une clé bien formée reçoit TOUJOURS la coquille.

    L'existence de l'album est ensuite révélée (ou non) par le manifest, ce qui
    permet un état 404 CÔTÉ PAGE sans distinguer, au niveau HTTP, un album
    existant d'un album inconnu.
    """
    _write_album(public_env, KEY_A)
    r_known = pub_client.get("/a/" + KEY_A)
    r_unknown = pub_client.get("/a/" + KEY_UNKNOWN)
    assert r_known.status_code == 200
    assert r_unknown.status_code == 200
    assert r_known.get_data() == r_unknown.get_data(), "coquille identique attendue (anti-énumération)"


def test_shell_invalid_key_is_404(pub_client):
    for bad in ("/a/short", "/a/" + "x" * 80, "/a/a.b.c"):
        r = pub_client.get(bad)
        assert r.status_code == 404, bad
        assert r.get_data() == UNIFORM_404_BODY


# ── 2. Manifest ───────────────────────────────────────────────────────


def test_manifest_ok_no_cache_and_etag(pub_client, public_env):
    album_dir = _write_album(public_env, KEY_A)
    r = pub_client.get(f"/a/{KEY_A}/manifest.json")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.headers["Cache-Control"] == "no-cache"
    assert r.headers.get("ETag")
    # Le service renvoie les OCTETS DU FICHIER tels quels (aucune injection).
    with open(os.path.join(album_dir, "manifest.json"), "rb") as f:
        assert r.get_data() == f.read()
    # Le manifest de la phase 1 ne porte AUCUNE donnée privée.
    assert _collect_private_fields(r.get_json()) == []


def test_manifest_304_on_if_none_match(pub_client, public_env):
    _write_album(public_env, KEY_A)
    r1 = pub_client.get(f"/a/{KEY_A}/manifest.json")
    etag = r1.headers["ETag"]
    r2 = pub_client.get(f"/a/{KEY_A}/manifest.json", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.get_data() == b""
    assert r2.headers["ETag"] == etag
    assert r2.headers["Cache-Control"] == "no-cache"


def test_manifest_cache_invalidated_by_mtime_size(pub_client, public_env):
    _write_album(public_env, KEY_A, title="Version un")
    assert pub_client.get(f"/a/{KEY_A}/manifest.json").get_json()["title"] == "Version un"
    # Réécriture ATOMIQUE (comme la phase 1) avec un contenu plus long.
    _write_album(public_env, KEY_A, title="Version deux plus longue")
    assert pub_client.get(f"/a/{KEY_A}/manifest.json").get_json()["title"] == "Version deux plus longue"


# ── 3. Médias (vignettes / grandes versions) ──────────────────────────


def test_thumb_and_full_ok_cache_immutable_mime(pub_client, public_env):
    _write_album(public_env, KEY_A, items=((1, ".jpg"), (2, ".png")))

    thumb = pub_client.get(f"/a/{KEY_A}/t/0001.jpg")
    assert thumb.status_code == 200
    assert thumb.headers["Content-Type"] == "image/jpeg"
    assert thumb.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert thumb.headers.get("ETag")
    assert thumb.get_data().startswith(b"\xff\xd8")

    full_jpg = pub_client.get(f"/a/{KEY_A}/f/0001.jpg")
    assert full_jpg.status_code == 200
    assert full_jpg.headers["Content-Type"] == "image/jpeg"
    assert full_jpg.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    full_png = pub_client.get(f"/a/{KEY_A}/f/0002.png")
    assert full_png.status_code == 200
    assert full_png.headers["Content-Type"] == "image/png"
    assert full_png.get_data().startswith(b"\x89PNG")

    # La vignette du PNG reste un JPG (format de la phase 1).
    assert pub_client.get(f"/a/{KEY_A}/t/0002.jpg").status_code == 200
    # Mauvaise extension pour cet item → 404 (ext du manifest fait foi).
    assert pub_client.get(f"/a/{KEY_A}/f/0002.jpg").status_code == 404


def test_media_304_on_if_none_match(pub_client, public_env):
    _write_album(public_env, KEY_A)
    r1 = pub_client.get(f"/a/{KEY_A}/t/0001.jpg")
    etag = r1.headers["ETag"]
    r2 = pub_client.get(f"/a/{KEY_A}/t/0001.jpg", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.get_data() == b""
    assert r2.headers["ETag"] == etag
    assert r2.headers["Cache-Control"] == "public, max-age=31536000, immutable"


# ── 4. 404 STRICTEMENT uniforme ───────────────────────────────────────


def test_uniform_404_body_and_status(pub_client, public_env):
    """Tous les motifs de non-appartenance produisent le MÊME 404."""
    album_dir = _write_album(public_env, KEY_A, items=((1, ".jpg"),))
    # Fichier PRÉSENT mais NON listé dans le manifest.
    with open(os.path.join(album_dir, "full", "0002.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0unlisted")
    # Album révoqué = dossier renommé.
    revoked_dir = os.path.join(public_env, KEY_REVOKED)
    _write_album(public_env, KEY_REVOKED)
    os.makedirs(os.path.join(public_env, ".revoked"), exist_ok=True)
    os.rename(revoked_dir, os.path.join(public_env, ".revoked", KEY_REVOKED))

    cases = {
        "clé inconnue": f"/a/{KEY_UNKNOWN}/manifest.json",
        "clé révoquée": f"/a/{KEY_REVOKED}/manifest.json",
        "clé invalide": "/a/tropcourt/manifest.json",
        "item hors manifest": f"/a/{KEY_A}/f/0009.jpg",
        "fichier non listé": f"/a/{KEY_A}/f/0002.jpg",
        "nom invalide (lettres)": f"/a/{KEY_A}/f/abcd.jpg",
        "nom invalide (ext)": f"/a/{KEY_A}/f/0001.gif",
        "nom invalide (casse)": f"/a/{KEY_A}/f/0001.JPG",
        "nom invalide (chiffres)": f"/a/{KEY_A}/f/1.jpg",
        "route inconnue (sous-route)": f"/a/{KEY_A}/bogus",
        "route inconnue (racine)": "/totalement/inconnu",
        "route inconnue (asset)": "/assets/nexistepas.css",
        "dossier album absent": f"/a/{KEY_UNKNOWN}/t/0001.jpg",
    }
    responses = {label: pub_client.get(path) for label, path in cases.items()}
    for label, r in responses.items():
        assert r.status_code == 404, f"{label} : {r.status_code}"
        assert r.get_data() == UNIFORM_404_BODY, f"{label} : corps non uniforme"
        assert r.headers["Content-Type"].startswith("text/plain"), label
    # Corps ET statut STRICTEMENT identiques entre TOUS les cas.
    assert len({r.status_code for r in responses.values()}) == 1
    assert len({r.get_data() for r in responses.values()}) == 1


def test_unlisted_file_is_not_served(pub_client, public_env):
    album_dir = _write_album(public_env, KEY_A, items=((1, ".jpg"),))
    with open(os.path.join(album_dir, "full", "0002.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff\xe0unlisted")
    assert pub_client.get(f"/a/{KEY_A}/f/0002.jpg").status_code == 404
    assert pub_client.get(f"/a/{KEY_A}/t/0002.jpg").status_code == 404


def test_revocation_renames_folder_and_404s(pub_client, public_env):
    _write_album(public_env, KEY_REVOKED)
    assert pub_client.get(f"/a/{KEY_REVOKED}/manifest.json").status_code == 200
    os.makedirs(os.path.join(public_env, ".revoked"), exist_ok=True)
    os.rename(
        os.path.join(public_env, KEY_REVOKED),
        os.path.join(public_env, ".revoked", KEY_REVOKED),
    )
    assert pub_client.get(f"/a/{KEY_REVOKED}/manifest.json").status_code == 404
    assert pub_client.get(f"/a/{KEY_REVOKED}/t/0001.jpg").status_code == 404


def test_missing_or_corrupt_manifest_404(pub_client, public_env):
    _write_album(public_env, KEY_A, manifest=False, items=((1, ".jpg"),))
    assert pub_client.get(f"/a/{KEY_A}/manifest.json").status_code == 404
    assert pub_client.get(f"/a/{KEY_A}/t/0001.jpg").status_code == 404
    with open(os.path.join(public_env, KEY_A, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{ pas du json")
    public_app.clear_manifest_cache()
    assert pub_client.get(f"/a/{KEY_A}/manifest.json").status_code == 404


# ── 5. Traversal ──────────────────────────────────────────────────────


def test_traversal_is_refused(pub_client, public_env):
    _write_album(public_env, KEY_A)
    attempts = [
        f"/a/{KEY_A}/f/..%2f..%2fmanifest.json",
        f"/a/{KEY_A}/f/%2e%2e%2f%2e%2e%2fmanifest.json",
        f"/a/{KEY_A}/f/....//manifest.json",
        f"/a/{KEY_A}/t/..%2fmanifest.json",
        f"/a/{KEY_A}/f/0001.jpg%2f..",
        f"/a/{KEY_A}/f/%2Fetc%2Fpasswd",
    ]
    for path in attempts:
        r = pub_client.get(path)
        assert r.status_code == 404, path
        assert r.get_data() == UNIFORM_404_BODY, path
    # Assets : sortie de public_web refusée.
    for path in ("/assets/..%2f..%2fapp.py", "/assets/%2e%2e/%2e%2e/app.py", "/assets/../../backend/app.py"):
        r = pub_client.get(path)
        assert r.status_code == 404, path


# ── 6. Méthodes interdites ────────────────────────────────────────────


def test_non_read_methods_are_405(pub_client, public_env):
    _write_album(public_env, KEY_A)
    targets = ["/a/" + KEY_A, f"/a/{KEY_A}/manifest.json", "/totalement/inconnu", "/assets/album.css"]
    for method in ("post", "put", "delete", "patch", "options"):
        for path in targets:
            r = getattr(pub_client, method)(path)
            assert r.status_code == 405, f"{method.upper()} {path} → {r.status_code}"
            assert r.get_data() == UNIFORM_405_BODY, f"{method.upper()} {path}"
            assert r.headers.get("Allow") == "GET, HEAD"
    # GET et HEAD restent autorisés.
    assert pub_client.get("/a/" + KEY_A).status_code == 200
    head = pub_client.head("/a/" + KEY_A)
    assert head.status_code == 200 and head.get_data() == b""


# ── 7. En-têtes de sécurité & absence de CORS ─────────────────────────


SECURITY_CASES = [
    ("shell", lambda k: "/a/" + k),
    ("manifest", lambda k: f"/a/{k}/manifest.json"),
    ("thumb", lambda k: f"/a/{k}/t/0001.jpg"),
    ("full", lambda k: f"/a/{k}/f/0001.jpg"),
    ("404", lambda k: f"/a/{k}/inconnu"),
]


def test_security_headers_on_all_responses(pub_client, public_env):
    _write_album(public_env, KEY_A)
    responses = [pub_client.get(fn(KEY_A)) for _label, fn in SECURITY_CASES]
    responses.append(pub_client.post("/a/" + KEY_A))  # 405
    responses.append(pub_client.get("/assets/album.css"))  # asset
    for r in responses:
        assert r.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("Content-Security-Policy") == REQUIRED_CSP


def test_no_cors_header_on_any_response(pub_client, public_env):
    _write_album(public_env, KEY_A)
    urls = [
        "/a/" + KEY_A,
        f"/a/{KEY_A}/manifest.json",
        f"/a/{KEY_A}/t/0001.jpg",
        f"/a/{KEY_A}/f/0001.jpg",
        "/assets/album.css",
        "/assets/album.js",
        f"/a/{KEY_UNKNOWN}/manifest.json",  # 404
        "/totalement/inconnu",  # 404
    ]
    for url in urls:
        r = pub_client.get(url, headers={"Origin": "https://evil.example"})
        for header in CORS_HEADERS:
            assert header not in r.headers, f"{url} expose {header}"


# ── 8. Assets confinés ────────────────────────────────────────────────


def test_assets_are_served_and_confined(pub_client):
    css = pub_client.get("/assets/album.css")
    assert css.status_code == 200
    assert css.headers["Content-Type"].startswith("text/css")
    assert css.headers["Cache-Control"] == "public, max-age=3600"

    js = pub_client.get("/assets/album.js")
    assert js.status_code == 200
    assert js.headers["Content-Type"].startswith("text/javascript") or "javascript" in js.headers["Content-Type"]

    brick = pub_client.get("/assets/vendor/holaf/holaf-virtual-grid.js")
    assert brick.status_code == 200

    # Sortie du dossier public_web → 404.
    assert pub_client.get("/assets/../../backend/app.py").status_code == 404
    assert pub_client.get("/assets/nexistepas.css").status_code == 404


# ── 9. Lecture seule (aucune écriture) ────────────────────────────────


def test_service_is_read_only(pub_client, public_env):
    _write_album(public_env, KEY_A, items=((1, ".jpg"), (2, ".png")))
    before = _tree_signature(public_env)
    # Batterie de requêtes (dont méthodes d'écriture refusées).
    pub_client.get("/a/" + KEY_A)
    pub_client.get(f"/a/{KEY_A}/manifest.json")
    pub_client.get(f"/a/{KEY_A}/t/0001.jpg")
    pub_client.get(f"/a/{KEY_A}/f/0002.png")
    pub_client.get(f"/a/{KEY_UNKNOWN}/manifest.json")
    pub_client.post(f"/a/{KEY_A}/manifest.json", json={"title": "hack"})
    pub_client.delete(f"/a/{KEY_A}/t/0001.jpg")
    after = _tree_signature(public_env)
    assert before == after, "le service public ne doit JAMAIS écrire sur disque"


# ── 10. Configuration / lancement ─────────────────────────────────────


def test_bind_and_port_defaults(monkeypatch):
    monkeypatch.delenv(public_app.ALBUM_PORT_ENV, raising=False)
    assert public_app.ALBUM_BIND_HOST == "127.0.0.1"
    assert public_app.album_port() == 8081
    monkeypatch.setenv(public_app.ALBUM_PORT_ENV, "9099")
    assert public_app.album_port() == 9099
    monkeypatch.setenv(public_app.ALBUM_PORT_ENV, "nawak")
    assert public_app.album_port() == 8081


def test_webroot_default(monkeypatch, tmp_path):
    monkeypatch.delenv(public_app.ALBUM_WEB_DIR_ENV, raising=False)
    root = public_app.album_web_root()
    assert root.endswith(os.path.join(".cache", "albums"))
    monkeypatch.setenv(public_app.ALBUM_WEB_DIR_ENV, str(tmp_path))
    assert public_app.album_web_root() == str(tmp_path)


# ── 11. Isolation : ZÉRO import du privé ──────────────────────────────


def test_public_app_has_no_private_imports():
    with open(PUBLIC_APP_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=PUBLIC_APP_PATH)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"db", "storage", "auth", "security", "flask_cors", "extensions", "album_web", "routes"}
    offenders = {name for name in imported if name.split(".")[0] in forbidden}
    assert offenders == set(), f"imports privés interdits : {offenders}"
    # Le service ne doit pas non plus référencer de session/cookies Flask ni
    # de CORS (analyse par NOM (AST) : les commentaires/docstrings sont ignorés).
    used_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            used_names.add(node.attr)
    forbidden_names = {"session", "flask_cors", "CORS", "set_cookie", "login_required"}
    assert used_names & forbidden_names == set(), f"références privées interdites : {used_names & forbidden_names}"


def test_private_backend_is_untouched():
    """Le service public vit dans des fichiers NEUFS : le privé est inchangé.

    (Contrôle structurel : ``public_app`` n'importe jamais ``app`` ni ``routes``.)
    """
    app_source = Path(os.path.join(BACKEND_DIR, "app.py")).read_text(encoding="utf-8")
    assert "public_app" not in app_source


# ── 12. Page publique (album.js) ──────────────────────────────────────


def test_page_files_exist_and_are_script_src_only():
    for path in (INDEX_HTML_PATH, ALBUM_CSS_PATH, ALBUM_JS_PATH):
        assert os.path.isfile(path), f"fichier de page manquant : {path}"
    html = Path(INDEX_HTML_PATH).read_text(encoding="utf-8")
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags and all("src=" in t for t in tags)


def test_page_js_uses_textcontent_never_innerhtml():
    src = Path(ALBUM_JS_PATH).read_text(encoding="utf-8")
    # Patterns d'USAGE (avec point d'accès) : la mention en commentaire d'un
    # interdit n'est pas un usage.
    assert re.search(r"\.innerHTML\b", src) is None, "album.js ne doit JAMAIS utiliser innerHTML"
    assert re.search(r"\.outerHTML\b", src) is None
    assert re.search(r"document\.write\s*\(", src) is None
    assert re.search(r"\beval\s*\(", src) is None
    # Le titre et la description passent par textContent.
    assert "textContent" in src
    assert "createElement" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent → harnais jsdom non exécutable")
def test_page_js_jsdom_harness():
    """Exécute le harnais jsdom de album.js (titre/description/grille/états)."""
    env = os.environ.copy()
    env.setdefault("JSDOM_DIR", "/projects/holaf-lib/node_modules")
    proc = subprocess.run(
        [shutil.which("node"), JSDOM_HARNESS],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if proc.returncode == 2:
        pytest.skip("jsdom introuvable → harnais ignoré (SKIP, pas un succès)")
    assert proc.returncode == 0, f"harnais jsdom en échec :\n{proc.stdout}\n{proc.stderr}"
    assert "textContent" in proc.stdout


# ── 13. Garde-fou SOFT de débit (phase 5) ─────────────────────────────


def _get(client, path, ip="1.1.1.1"):
    """GET avec IP SIMULÉE (chaque IP a son propre compteur)."""
    return client.get(path, environ_overrides={"REMOTE_ADDR": ip})


def _guard_messages(caplog):
    return [r.getMessage() for r in caplog.records if "[guard]" in r.getMessage()]


def test_guard_defaults_thresholds_and_modes(guard_env):
    """Défaut off + seuils documentés (une régression du défaut = 429 inattendus)."""
    assert public_app.album_guard_mode() == "off"
    assert public_app.GUARD_MODES == ("off", "log", "on")
    assert public_app.GUARD_MAX_CALLS == 240
    assert public_app.GUARD_MAX_404 == 30
    assert public_app.GUARD_WINDOW_SECONDS == 60.0
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "LOG")
    assert public_app.album_guard_mode() == "log"
    # Valeur inconnue → fail-safe « off » (aucun blocage par accident).
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "nawak")
    assert public_app.album_guard_mode() == "off"


def test_guard_off_is_strictly_inert(guard_env, pub_client, public_env, monkeypatch, caplog):
    """Contrôle négatif : en ``off`` (et valeur inconnue), aucun 429 ni log."""
    _write_album(public_env, KEY_A)
    # Seuils ridicules : si le mode off n'était pas respecté, 429 immédiats.
    monkeypatch.setattr(public_app, "GUARD_MAX_CALLS", 1)
    monkeypatch.setattr(public_app, "GUARD_MAX_404", 1)
    with caplog.at_level(logging.WARNING, logger="public_app"):
        codes = [_get(pub_client, "/a/" + KEY_A).status_code for _ in range(6)]
        codes += [_get(pub_client, f"/a/{KEY_UNKNOWN}/manifest.json").status_code for _ in range(4)]
        guard_env.setenv(public_app.ALBUM_GUARD_ENV, "nawak")
        codes += [_get(pub_client, "/a/" + KEY_A).status_code for _ in range(2)]
    assert codes == [200] * 6 + [404] * 4 + [200] * 2
    assert _guard_messages(caplog) == []
    # Le mode off ne doit même pas alimenter les compteurs.
    assert public_app._guard_windows == {}


def test_guard_log_counts_without_blocking(guard_env, pub_client, public_env, monkeypatch, caplog):
    """Mode ``log`` : dépassements comptés et loggués, JAMAIS de 429."""
    _write_album(public_env, KEY_A)
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "log")
    monkeypatch.setattr(public_app, "GUARD_MAX_CALLS", 3)
    monkeypatch.setattr(public_app, "GUARD_MAX_404", 2)
    with caplog.at_level(logging.WARNING, logger="public_app"):
        assert all(_get(pub_client, "/a/" + KEY_A).status_code == 200 for _ in range(8))
        assert all(_get(pub_client, f"/a/{KEY_A}/f/0009.jpg").status_code == 404 for _ in range(4))
    messages = _guard_messages(caplog)
    assert any("motif=rafale" in m and "mode=log" in m for m in messages)
    assert any("motif=404-repetes" in m and "mode=log" in m for m in messages)
    # Anti-inondation : un seul log par motif et par fenêtre.
    assert len([m for m in messages if "motif=rafale" in m]) == 1
    assert len([m for m in messages if "motif=404-repetes" in m]) == 1


def test_guard_logs_never_leak_full_key(guard_env, pub_client, public_env, monkeypatch, caplog):
    """Contrôle négatif : la capability n'apparaît JAMAIS en clair dans un log."""
    _write_album(public_env, KEY_A)
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "log")
    monkeypatch.setattr(public_app, "GUARD_MAX_CALLS", 1)
    monkeypatch.setattr(public_app, "GUARD_MAX_404", 1)
    with caplog.at_level(logging.WARNING, logger="public_app"):
        _get(pub_client, f"/a/{KEY_A}/manifest.json")
        _get(pub_client, f"/a/{KEY_UNKNOWN}/manifest.json")
    messages = _guard_messages(caplog)
    assert messages, "au moins un log du garde-fou attendu"
    assert all(KEY_A not in m and KEY_UNKNOWN not in m for m in messages)
    assert any("/a/<key>" in m for m in messages)

    # Le filtre du journal d'accès Werkzeug masque lui aussi la clé…
    record = logging.LogRecord(
        "werkzeug",
        logging.INFO,
        __file__,
        1,
        f'127.0.0.1 - - [x] "GET /a/{KEY_A} HTTP/1.1" 200 -',
        (),
        None,
    )
    assert public_app._redact_album_keys_filter(record) is True
    assert KEY_A not in record.getMessage() and "/a/<key>" in record.getMessage()
    # …et laisse INTACT un message sans clé (contrôle négatif).
    keep = logging.LogRecord("werkzeug", logging.INFO, __file__, 1, "GET /assets/album.css", (), None)
    public_app._redact_album_keys_filter(keep)
    assert keep.getMessage() == "GET /assets/album.css"


def test_guard_on_blocks_over_threshold_by_ip(guard_env, pub_client, public_env, monkeypatch, caplog):
    """Mode ``on`` : 429 au-delà du seuil, par IP, avec récupération."""
    _write_album(public_env, KEY_A)
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "on")
    monkeypatch.setattr(public_app, "GUARD_MAX_CALLS", 3)
    monkeypatch.setattr(public_app, "GUARD_MAX_404", 100)
    monkeypatch.setattr(public_app, "GUARD_WINDOW_SECONDS", 0.2)
    with caplog.at_level(logging.WARNING, logger="public_app"):
        codes = [_get(pub_client, "/a/" + KEY_A, ip="1.1.1.1").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    # Contrôle négatif : une AUTRE IP n'est jamais affectée par le blocage.
    assert _get(pub_client, "/a/" + KEY_A, ip="9.9.9.9").status_code == 200
    blocked = _get(pub_client, "/a/" + KEY_A, ip="1.1.1.1")
    assert blocked.status_code == 429
    assert blocked.get_data() == b"Too Many Requests\n"
    assert blocked.headers["Retry-After"] == str(int(public_app.GUARD_WINDOW_SECONDS))
    assert blocked.headers["Content-Type"].startswith("text/plain")
    # Le 429 porte les en-têtes de sécurité et AUCUN en-tête CORS.
    assert blocked.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert blocked.headers["X-Frame-Options"] == "DENY"
    for header in CORS_HEADERS:
        assert header not in blocked.headers
    assert any("→ 429" in m for m in _guard_messages(caplog))
    # La fenêtre glisse : l'IP bloquée redevient servie après expiration.
    time.sleep(0.25)
    assert _get(pub_client, "/a/" + KEY_A, ip="1.1.1.1").status_code == 200


def test_guard_on_blocks_404_burst(guard_env, pub_client, public_env, monkeypatch):
    """Mode ``on`` : une IP qui balaye (404 répétés) est coupée, même sur un chemin valide."""
    _write_album(public_env, KEY_A)
    guard_env.setenv(public_app.ALBUM_GUARD_ENV, "on")
    monkeypatch.setattr(public_app, "GUARD_MAX_CALLS", 100)
    monkeypatch.setattr(public_app, "GUARD_MAX_404", 2)
    codes = [_get(pub_client, f"/a/{KEY_UNKNOWN}/manifest.json", ip="3.3.3.3").status_code for _ in range(2)]
    assert codes == [404, 404]
    assert _get(pub_client, "/a/" + KEY_A, ip="3.3.3.3").status_code == 429
    # Contrôle négatif : les autres IP continuent d'être servies normalement.
    assert _get(pub_client, "/a/" + KEY_A, ip="4.4.4.4").status_code == 200
    assert _get(pub_client, f"/a/{KEY_UNKNOWN}/manifest.json", ip="4.4.4.4").status_code == 404


# ── 14. Isolation renforcée + lanceur public ──────────────────────────


def test_public_app_import_pulls_no_private_module():
    """Process FRAIS : importer ``public_app`` ne charge AUCUN module privé.

    Contrôle plus fort que l'AST : même une dépendance transitive (ex. importer
    le limiteur privé, qui tire ``security.auth`` → ``db``) ferait échouer le test.
    """
    code = (
        "import sys; import public_app; "
        "bad=[m for m in ('db','storage','auth','security','flask_cors','extensions','album_web','routes') "
        "if m in sys.modules]; print('PRIVATE:' + ','.join(bad))"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, f"import public_app en échec :\n{proc.stderr}"
    assert proc.stdout.strip().splitlines()[-1] == "PRIVATE:", f"modules privés importés : {proc.stdout.strip()}"


def test_run_public_script_targets_only_public_process():
    """``run_public.sh`` : exécutable, log dédié, pkill limité au PUBLIC.

    Contrôle négatif : un ``pkill`` visant ``backend/app.py`` (l'entrée privée)
    tuerait le serveur privé au lancement du service public → le test échoue.
    """
    script = os.path.join(os.path.dirname(BACKEND_DIR), "run_public.sh")
    assert os.path.isfile(script), "run_public.sh manquant"
    assert os.access(script, os.X_OK), "run_public.sh doit être exécutable"
    src = Path(script).read_text(encoding="utf-8")
    pkill_lines = [line.strip() for line in src.splitlines() if line.strip().startswith("pkill")]
    assert pkill_lines, "au moins un pkill attendu"
    for line in pkill_lines:
        assert "public_app.py" in line, f"pkill doit cibler le process public : {line}"
        # ``app.py`` non précédé de ``public_`` = entrée privée → interdit.
        assert re.search(r"(?<!public_)app\.py", line) is None, f"pkill ne doit pas viser le privé : {line}"
    assert "public_server.log" in src
