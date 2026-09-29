"""Service PUBLIC des albums — process SÉPARÉ, LECTURE SEULE, isolé du privé.

Point d'entrée autonome (``python backend/public_app.py``) qui sert la page
publique d'un album. Ce service n'a STRICTEMENT AUCUN lien avec le backend
privé : il n'importe ni la base (``db``), ni le storage/SFTP (``storage``), ni
l'authentification/session (``auth``, ``security``), ni ``flask_cors``. Il ne
lit QUE des fichiers sous ``AIH_ALBUM_WEB_DIR`` (dossier web d'un album, produit
par la phase 1) et sous ``backend/public_web`` (assets de la coquille), et
n'écrit JAMAIS sur disque.

Contrat produit par la phase 1 (``backend/album_web.py``) ::

    <webroot>/<key>/
      manifest.json      # {title, description, count, items[{i,w,h,ext}], updated_at}
      thumb/0001.jpg     # vignettes 512 (toujours JPG)
      full/0001.jpg      # grande version (ou 0001.png si alpha réel)

Nommage OPAQUE ``0001`` ↔ numéro d'item ``i``. Aucune donnée privée dans le
manifest (ni media_id, ni user_id, ni chemin, ni nom d'origine).

Routes (GET/HEAD uniquement) :
  * ``GET /a/<key>``                 → coquille HTML (lue depuis public_web/)
  * ``GET /a/<key>/manifest.json``   → manifest, no-cache + ETag (+ 304)
  * ``GET /a/<key>/t/<name>``        → vignette (immutable + ETag + 304)
  * ``GET /a/<key>/f/<name>``        → grande version (immutable + ETag + 304)
  * ``GET /assets/<path>``           → assets de la coquille (confinés)
  * tout le reste                    → 404 STRICTEMENT UNIFORME
  * méthode autre que GET/HEAD       → 405 uniforme

Vérification d'appartenance À CHAQUE requête média : regex stricte sur ``key``
et ``name``, dossier existant, ``name`` ∈ items du manifest, confinement par
``realpath`` sous ``<webroot>/<key>/`` et fichier existant. Un fichier présent
sur disque mais NON listé dans le manifest n'est PAS servi.

En-têtes de sécurité sur TOUTES les réponses : ``X-Robots-Tag`` (noindex…),
``Referrer-Policy: no-referrer``, ``X-Content-Type-Options: nosniff``,
``X-Frame-Options: DENY`` et une CSP stricte. AUCUN en-tête CORS n'est émis.

Écoute : ``AIH_ALBUM_BIND_HOST`` (défaut ``127.0.0.1`` — loopback, AUCUNE
exposition réseau) et ``AIH_ALBUM_PORT`` (défaut ``8081``). Pour un reverse
proxy sur une AUTRE machine : binder sur une interface joignable (``0.0.0.0``
ou l'IP de l'interface) **et** restreindre l'accès par firewall à la seule IP
du proxy (``docs/albums.md``) — ce vhost reste SANS Authentik.

GARDE-FOU SOFT de débit (phase 5) : ``AIH_ALBUM_GUARD`` ∈ ``off`` (défaut) |
``log`` | ``on``. En ``log``, les dépassements (rafale par IP, 404 répétés —
signal d'énumération) sont comptés et loggués SANS bloquer ; en ``on``, la
réponse devient 429. Implémentation LOCALE à fenêtre glissante (même interface
que ``backend/security/ratelimit.py``) : importer ce module tirerait
``security.auth`` → ``db``/``auth`` et casserait l'isolation du process
(contrôle AST ``test_public_app_has_no_private_imports``).
Les logs du garde-fou (et le journal d'accès Werkzeug) ne contiennent JAMAIS
la clé d'album complète : la capability ``/a/<clé-opaque>`` y est masquée en
``/a/<key>``.
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time

from flask import Flask, Response, abort, request, send_file

# ── Racines et constantes ─────────────────────────────────────────────

# Assets de la coquille publique (aucun lien avec le front privé).
PUBLIC_WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_web")

# Racine du webroot des albums (même variable que la phase 1) et repli projet
# ``<BASE_DIR>/.cache/albums`` (BASE_DIR = parent du dossier ``backend``).
ALBUM_WEB_DIR_ENV = "AIH_ALBUM_WEB_DIR"
ALBUM_PORT_ENV = "AIH_ALBUM_PORT"
ALBUM_PORT_DEFAULT = 8081
# Hôte d'écoute : LOOPBACK par défaut → le service n'est PAS exposé au réseau.
# Reverse proxy sur une AUTRE machine : AIH_ALBUM_BIND_HOST=0.0.0.0 (ou l'IP de
# l'interface) PUIS restriction FIREWALL à l'IP du proxy (docs/albums.md).
ALBUM_BIND_HOST_ENV = "AIH_ALBUM_BIND_HOST"
ALBUM_BIND_HOST_DEFAULT = "127.0.0.1"

# Clé d'album opaque : alphabet URL-safe, sans ``/`` ni ``.`` (donc aucun
# path-traversal possible par la clé). Même contrat que ``album_web``.
_ALBUM_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
# Nom d'item opaque : ``0001`` … ``99999999`` + extension média autorisée.
_ITEM_NAME_RE = re.compile(r"^(\d{4,8})\.(jpg|png)$")

MANIFEST_NAME = "manifest.json"
THUMB_SUBDIR = "thumb"
FULL_SUBDIR = "full"

# MIME déduit de l'EXTENSION DU MANIFEST (jamais du client).
_MIME_BY_EXT = {"jpg": "image/jpeg", "png": "image/png"}

# CSP stricte : aucun script inline, aucune ressource externe. ``style-src``
# tolère l'inline car les briques holaf injectent leur <style> scopé.
_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

# Corps (octets) STRICTEMENT identiques pour tous les 404 et 405.
_NOT_FOUND_BODY = b"Not Found\n"
_METHOD_NOT_ALLOWED_BODY = b"Method Not Allowed\n"
_TOO_MANY_REQUESTS_BODY = b"Too Many Requests\n"

# Journal applicatif du service public (le garde-fou y écrit ses alertes).
logger = logging.getLogger("public_app")

# En-têtes CORS que l'on s'assure de ne JAMAIS émettre.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Credentials",
    "Access-Control-Allow-Methods",
    "Access-Control-Allow-Headers",
    "Access-Control-Expose-Headers",
)


# ── Configuration (env) ───────────────────────────────────────────────


def album_web_root():
    """Racine du webroot des albums (``AIH_ALBUM_WEB_DIR`` ou défaut projet)."""
    override = os.environ.get(ALBUM_WEB_DIR_ENV)
    if override:
        return override
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, ".cache", "albums")


def album_port():
    """Port d'écoute public (``AIH_ALBUM_PORT``, défaut 8081)."""
    raw = os.environ.get(ALBUM_PORT_ENV, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return ALBUM_PORT_DEFAULT


def _is_valid_bind_host(value):
    """Hôte d'écoute accepté : IP nue (v4/v6) ou ``localhost``.

    Volontairement STRICT : ni nom DNS arbitraire, ni ``hôte:port``, ni
    crochets IPv6. Une valeur douteuse ne doit JAMAIS faire échouer ``app.run``
    (``socket.gaierror``) : elle retombe sur ``ALBUM_BIND_HOST_DEFAULT``.
    """
    if value.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def album_bind_host():
    """Hôte d'écoute public (``AIH_ALBUM_BIND_HOST``, défaut ``127.0.0.1``).

    Valeur vide → défaut (silencieux) ; valeur invalide → défaut +
    avertissement dans ``public_server.log`` (aucune exception). Pour un
    reverse proxy sur une autre machine, fournir ``0.0.0.0`` ou l'IP de
    l'interface joignable **et** restreindre par firewall (``docs/albums.md``).
    """
    raw = os.environ.get(ALBUM_BIND_HOST_ENV, "")
    value = raw.strip() if isinstance(raw, str) else ""
    if _is_valid_bind_host(value):
        return value
    if value:
        logger.warning(
            "[public] %s=%r ignoré (attendu : IP nue ou localhost) — repli sur %s",
            ALBUM_BIND_HOST_ENV,
            value,
            ALBUM_BIND_HOST_DEFAULT,
        )
    return ALBUM_BIND_HOST_DEFAULT


def _is_within(path, root):
    """``path`` (absolu, résolu) est-il DANS ``root`` (absolu, résolu) ?"""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


# ── Garde-fou SOFT de débit (phase 5, désactivé par défaut) ───────────
#
# ``AIH_ALBUM_GUARD`` ∈ ``off`` (défaut) | ``log`` | ``on`` :
#   * ``off`` : AUCUN comptage, aucun log — comportement phase 2 STRICT ;
#   * ``log`` : compte et loggue les dépassements (rafale par IP, 404 répétés)
#     SANS jamais bloquer (aucun 429) ;
#   * ``on``  : les dépassements deviennent des 429 uniformes pour l'IP
#     concernée uniquement (les autres IP ne sont pas affectées).
#
# Deux compteurs à fenêtre glissante PAR IP :
#   * ``all`` : toutes méthodes confondues — ``GUARD_MAX_CALLS`` / fenêtre ;
#   * ``404`` : réponses 404 (balayage / énumération) — ``GUARD_MAX_404``.
# En mode ``on``, une IP saturée en 404 est bloquée (429) même sur un chemin
# valide tant que la fenêtre reste pleine (les 404 restent comptabilisés et
# loggués). Les seuils ne sont PAS des quotas applicatifs : l'objectif est de
# couper l'abus manifeste, pas l'usage normal (une page d'album de 100 photos
# ≈ 103 requêtes au premier chargement, puis cache navigateur).
#
# Implémentation LOCALE volontaire, même interface que
# ``backend/security/ratelimit.py`` (``_rate_limit(key, max_calls, window)``) :
# importer ce module privé tirerait ``security.auth`` → ``db``/``auth`` et
# casserait l'isolation de ce process (cf. test AST d'isolation).

ALBUM_GUARD_ENV = "AIH_ALBUM_GUARD"
GUARD_MODES = ("off", "log", "on")
GUARD_WINDOW_SECONDS = 60.0
GUARD_MAX_CALLS = 240  # requêtes GET/HEAD par IP et par fenêtre (toutes routes)
GUARD_MAX_404 = 30  # réponses 404 par IP et par fenêtre (balayage)
GUARD_MAX_KEYS = 4096  # borne mémoire des compteurs (purge au-delà)

# Chemin d'album : ``/a/<clé 20-64>`` — groupe 1 réutilisé pour la rédaction.
_ALBUM_KEY_PATH_RE = re.compile(r"(/a/)[A-Za-z0-9_-]{20,64}")

_guard_lock = threading.Lock()
_guard_windows = {}  # clé de compteur -> liste de timestamps
_guard_last_log = {}  # clé de log -> dernier log (anti-inondation)


def album_guard_mode():
    """Mode du garde-fou — valeur inconnue/absente : ``off`` (fail-safe)."""
    raw = (os.environ.get(ALBUM_GUARD_ENV) or "").strip().lower()
    return raw if raw in GUARD_MODES else "off"


def _guard_redact_path(path):
    """Chemin loggable SANS la clé d'album (capability jamais écrite en clair)."""
    return _ALBUM_KEY_PATH_RE.sub(r"\1<key>", path or "/")[:120]


def _guard_prune_locked(now):
    """Purge les compteurs expirés si la table dépasse ``GUARD_MAX_KEYS``.

    Fail-open : si la purge ne suffit pas (attaque massive multi-IP), on vide
    les compteurs — mieux vaut perdre la mesure que croître sans borne.
    """
    if len(_guard_windows) <= GUARD_MAX_KEYS:
        return
    deadline = now - GUARD_WINDOW_SECONDS
    for key in [k for k, v in _guard_windows.items() if not v or v[-1] < deadline]:
        del _guard_windows[key]
    if len(_guard_windows) > GUARD_MAX_KEYS:
        _guard_windows.clear()


def _rate_limit(key, max_calls, window_seconds):
    """Fenêtre glissante en mémoire (MÊME interface que ``security/ratelimit``).

    Retourne ``True`` si l'appel est autorisé, ``False`` si le seuil est déjà
    atteint. En cas de dépassement l'échantillon n'est PAS ajouté : le compteur
    retombe mécaniquement dès que la fenêtre glisse.
    """
    now = time.time()
    with _guard_lock:
        bucket = _guard_windows.setdefault(key, [])
        while bucket and bucket[0] < now - window_seconds:
            bucket.pop(0)
        if len(bucket) >= max_calls:
            _guard_prune_locked(now)
            return False
        bucket.append(now)
        _guard_prune_locked(now)
        return True


def _rate_peek(key, max_calls, window_seconds):
    """Vérifie un compteur SANS échantillonner (pour bloquer en mode ``on``)."""
    now = time.time()
    with _guard_lock:
        bucket = _guard_windows.get(key) or []
        while bucket and bucket[0] < now - window_seconds:
            bucket.pop(0)
        return len(bucket) < max_calls


def _guard_log_once(log_key, message):
    """Loggue au plus UNE fois par fenêtre par clé (anti-inondation des logs)."""
    now = time.time()
    with _guard_lock:
        if now - _guard_last_log.get(log_key, 0.0) < GUARD_WINDOW_SECONDS:
            return
        if len(_guard_last_log) > GUARD_MAX_KEYS:
            _guard_last_log.clear()
        _guard_last_log[log_key] = now
    logger.warning("%s", message)


def clear_guard_state():
    """Vide les compteurs du garde-fou (tests uniquement ; sans effet en prod)."""
    with _guard_lock:
        _guard_windows.clear()
        _guard_last_log.clear()


def _too_many_requests():
    """429 uniforme du garde-fou (mode ``on`` uniquement)."""
    response = Response(_TOO_MANY_REQUESTS_BODY, status=429, mimetype="text/plain")
    response.headers["Retry-After"] = str(int(GUARD_WINDOW_SECONDS))
    return response


def _guard_incoming():
    """before_request : compte la requête, et en mode ``on`` répond 429.

    Enregistré AVANT le rejet des méthodes non-lecture pour compter TOUTE
    requête ; en ``off`` il ne fait strictement rien (retour immédiat).
    """
    mode = album_guard_mode()
    if mode == "off":
        return None
    ip = request.remote_addr or "-"
    if not _rate_limit(f"all:{ip}", GUARD_MAX_CALLS, GUARD_WINDOW_SECONDS):
        message = (
            f"[guard] mode={mode} ip={ip} motif=rafale "
            f"compte>{GUARD_MAX_CALLS}/{int(GUARD_WINDOW_SECONDS)}s "
            f"chemin={_guard_redact_path(request.path)}"
        )
        if mode == "on":
            logger.warning("%s → 429", message)
            return _too_many_requests()
        _guard_log_once(f"all:{ip}", message)
        return None
    if mode == "on" and not _rate_peek(f"404:{ip}", GUARD_MAX_404, GUARD_WINDOW_SECONDS):
        logger.warning(
            "[guard] mode=on ip=%s motif=404-repetes → 429 chemin=%s",
            ip,
            _guard_redact_path(request.path),
        )
        return _too_many_requests()
    return None


def _guard_outgoing(response):
    """after_request : compte les 404 et loggue les rafales (``log``/``on``)."""
    mode = album_guard_mode()
    if mode != "off" and response.status_code == 404:
        ip = request.remote_addr or "-"
        if not _rate_limit(f"404:{ip}", GUARD_MAX_404, GUARD_WINDOW_SECONDS):
            _guard_log_once(
                f"404:{ip}",
                f"[guard] mode={mode} ip={ip} motif=404-repetes "
                f"compte>{GUARD_MAX_404}/{int(GUARD_WINDOW_SECONDS)}s "
                f"chemin={_guard_redact_path(request.path)}",
            )
    return response


def _redact_album_keys_filter(record):
    """Filtre logging : masque les clés d'album dans les messages formatés.

    Appliqué au logger ``werkzeug`` (journal d'accès du serveur de dev) pour
    qu'une capability URL ne soit JAMAIS écrite en clair dans
    ``public_server.log``. Le message peut contenir des séquences ANSI : la
    rédaction porte sur le chemin, pas sur le style.
    """
    message = record.getMessage()
    redacted = _ALBUM_KEY_PATH_RE.sub(r"\1<key>", message)
    if redacted != message:
        record.msg = redacted
        record.args = ()
    return True


# ── Cache RAM du manifest (invalidé par (mtime_ns, size)) ─────────────

_manifest_cache = {}
_manifest_lock = threading.Lock()


def clear_manifest_cache():
    """Vide le cache RAM (utilisé par les tests ; aucun effet en production)."""
    with _manifest_lock:
        _manifest_cache.clear()


def _manifest_index(manifest):
    """Index ``{numéro d'item: extension}`` à partir du manifest (tolérant)."""
    by_num = {}
    items = manifest.get("items")
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                num = int(it.get("i"))
            except (TypeError, ValueError):
                continue
            ext = it.get("ext") or ".jpg"
            if isinstance(ext, str):
                by_num[num] = ext.lstrip(".").lower()
    return by_num


def _read_manifest(album_path):
    """Lit et met en cache le manifest d'un album.

    Retourne ``(raw_bytes, manifest, by_num, etag)`` — ou ``None`` si le fichier
    est absent/illisible/invalide (→ 404 uniforme côté appelant). Le cache est
    invalidé par ``(mtime_ns, size)`` : toute réécriture atomique (phase 1) est
    immédiatement visible.
    """
    manifest_path = os.path.join(album_path, MANIFEST_NAME)
    try:
        st = os.stat(manifest_path)
    except OSError:
        return None
    stat_key = (st.st_mtime_ns, st.st_size)
    with _manifest_lock:
        cached = _manifest_cache.get(album_path)
        if cached is not None and cached[0] == stat_key:
            return cached[1], cached[2], cached[3], cached[4]
    try:
        with open(manifest_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        manifest = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(manifest, dict):
        return None
    by_num = _manifest_index(manifest)
    # ETag fort : dérivé du contenu (donc stable pour un même manifest).
    etag = f'"{hashlib.sha256(raw).hexdigest()[:32]}"'
    with _manifest_lock:
        _manifest_cache[album_path] = (stat_key, raw, manifest, by_num, etag)
    return raw, manifest, by_num, etag


def _etag_matches(header_value, etag):
    """``If-None-Match`` contient-il ``etag`` (ou ``*``) ?"""
    if not header_value:
        return False
    candidates = [c.strip() for c in header_value.split(",")]
    if "*" in candidates:
        return True
    weak = "W/" + etag
    return etag in candidates or weak in candidates


# ── Application Flask ─────────────────────────────────────────────────

app = Flask(__name__)

# Garde-fou SOFT enregistré en PREMIER (enregistrement explicite : il précède le
# rejet GET/HEAD) : en ``off`` il ne fait RIEN ; en ``log``/``on`` il compte
# toute requête. ``after_request`` : la sécurité des en-têtes s'exécute après
# lui (ordre inverse d'enregistrement), les 429 portent donc les en-têtes.
app.before_request(_guard_incoming)
app.after_request(_guard_outgoing)


@app.before_request
def _reject_non_read_methods():
    """Toute méthode autre que GET/HEAD → 405 uniforme (aucune écriture)."""
    if request.method not in ("GET", "HEAD"):
        abort(405)


@app.after_request
def _security_headers(response):
    """Pose les en-têtes de sécurité et garantit l'ABSENCE de CORS."""
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = _CSP
    for name in _CORS_HEADERS:
        while name in response.headers:
            del response.headers[name]
    return response


@app.errorhandler(404)
def _not_found(_error):
    """404 STRICTEMENT uniforme, quel que soit le motif."""
    return Response(_NOT_FOUND_BODY, status=404, mimetype="text/plain")


@app.errorhandler(405)
def _method_not_allowed(_error):
    """405 uniforme + ``Allow`` (GET/HEAD uniquement)."""
    response = Response(_METHOD_NOT_ALLOWED_BODY, status=405, mimetype="text/plain")
    response.headers["Allow"] = "GET, HEAD"
    return response


# ── Route : coquille HTML ─────────────────────────────────────────────


@app.get("/a/<key>")
def album_shell(key):
    """Sert la coquille HTML (le JS lit la clé dans l'URL).

    Une clé bien formée reçoit TOUJOURS la coquille (200) : l'existence de
    l'album est ensuite testée par le fetch du manifest, ce qui permet un état
    « album introuvable » CÔTÉ PAGE et évite de distinguer un album existant
    d'un album inconnu au niveau HTTP. Une clé mal formée → 404 uniforme.
    """
    if not _ALBUM_KEY_RE.match(key):
        abort(404)
    index_path = os.path.join(PUBLIC_WEB_ROOT, "index.html")
    try:
        response = send_file(index_path, mimetype="text/html", conditional=True)
    except OSError:
        abort(404)
    response.headers["Cache-Control"] = "no-cache"
    return response


# ── Route : manifest ──────────────────────────────────────────────────


@app.get("/a/<key>/manifest.json")
def album_manifest(key):
    """Sert le manifest (octets tels quels) : no-cache + ETag (+ 304)."""
    if not _ALBUM_KEY_RE.match(key):
        abort(404)
    info = _read_manifest(os.path.join(album_web_root(), key))
    if info is None:
        abort(404)
    raw, _manifest, _by_num, etag = info
    if _etag_matches(request.headers.get("If-None-Match"), etag):
        response = Response(status=304)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return response
    response = Response(raw, mimetype="application/json")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["ETag"] = etag
    return response


# ── Routes : médias (vignette / grande version) ───────────────────────


def _serve_media(key, name, subdir):
    """Sert un média d'album après vérification COMPLÈTE d'appartenance."""
    if not _ALBUM_KEY_RE.match(key):
        abort(404)
    match = _ITEM_NAME_RE.match(name)
    if not match:
        abort(404)
    item_no = int(match.group(1))
    ext = match.group(2)

    album_path = os.path.join(album_web_root(), key)
    info = _read_manifest(album_path)
    if info is None:
        abort(404)
    _raw, _manifest, by_num, _etag = info

    item_ext = by_num.get(item_no)
    if item_ext is None:
        abort(404)  # fichier éventuellement présent mais NON listé → 404
    if subdir == THUMB_SUBDIR:
        # Les vignettes sont TOUJOURS JPG (phase 1), quel que soit l'ext full.
        if ext != "jpg":
            abort(404)
    else:
        if item_ext != ext:
            abort(404)

    album_real = os.path.realpath(album_path)
    target = os.path.realpath(os.path.join(album_path, subdir, name))
    if not _is_within(target, album_real):
        abort(404)  # anti-traversal (défense en profondeur)
    if not os.path.isfile(target):
        abort(404)

    response = send_file(target, mimetype=_MIME_BY_EXT[ext], conditional=True, etag=True)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/a/<key>/t/<name>")
def album_thumb(key, name):
    """Vignette JPG d'un album (item ``name`` ∈ manifest)."""
    return _serve_media(key, name, THUMB_SUBDIR)


@app.get("/a/<key>/f/<name>")
def album_full(key, name):
    """Grande version d'un album (ext de l'item ∈ manifest)."""
    return _serve_media(key, name, FULL_SUBDIR)


# ── Route : assets statiques de la coquille ───────────────────────────


@app.get("/assets/<path:rel>")
def public_asset(rel):
    """Sert un asset de ``backend/public_web`` confiné par ``realpath``."""
    root = os.path.realpath(PUBLIC_WEB_ROOT)
    target = os.path.realpath(os.path.join(root, rel))
    if not _is_within(target, root):
        abort(404)
    if os.path.basename(target).startswith("."):
        abort(404)
    if not os.path.isfile(target):
        abort(404)
    response = send_file(target, conditional=True, etag=True)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ── Lancement (process séparé) ────────────────────────────────────────


def main():
    """Lance le service public (``AIH_ALBUM_BIND_HOST``:``AIH_ALBUM_PORT``)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    # Le journal d'accès Werkzeug contient le CHEMIN COMPLET (donc la clé
    # d'album) : on masque les capabilities AVANT toute écriture dans
    # public_server.log. Le garde-fou n'a aucun effet sur ce point.
    logging.getLogger("werkzeug").addFilter(_redact_album_keys_filter)
    host = album_bind_host()
    port = album_port()
    webroot = album_web_root()
    guard_mode = album_guard_mode()
    logging.info("[public] albums webroot = %s", webroot)
    # IPv6 : crochets dans l'URL affichée (http://[::1]:8081), hôte nu pour bind.
    logging.info("[public] écoute http://%s:%s", f"[{host}]" if ":" in host else host, port)
    if host not in ("127.0.0.1", "::1", "localhost"):
        logging.warning(
            "[public] écoute NON-LOOPBACK (%s) : restreindre le port %s par FIREWALL "
            "(seule l'IP du reverse proxy) — vhost SANS Authentik, cf. docs/albums.md",
            host,
            port,
        )
    logging.info(
        "[public] garde-fou AIH_ALBUM_GUARD=%s (seuils %d req/%ds et %d 404/%ds par IP)",
        guard_mode,
        GUARD_MAX_CALLS,
        int(GUARD_WINDOW_SECONDS),
        GUARD_MAX_404,
        int(GUARD_WINDOW_SECONDS),
    )
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
