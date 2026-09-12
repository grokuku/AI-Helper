"""Politique anti-SSRF partagée pour les appels LLM sortants.

Ce module centralise la validation des URLs de fournisseurs LLM utilisée par
les routes presets (sondes ``list-models`` / ``detect-context``) ET par la
détection de fenêtre de contexte (``_get_model_context``, routes/enhance.py).
Aucune duplication de la politique : une seule implémentation, un seul opt-in.

Règles (inchangées, extraites telles quelles de ``routes/presets.py``) :
- https:// uniquement, sauf hôte privé opt-in ou ``AIH_ALLOW_HTTP_LLM=1`` ;
- pas de credentials embarquées (user:pass@) ;
- port standard (443/80), sauf hôte privé opt-in ;
- hôte : pas d'IP privée/loopback/link-local, ni de DNS rebinding (toutes les
  IP résolues sont vérifiées) ;
- opt-in par variables d'environnement ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` (LLM
  locaux type Ollama/LM Studio) et ``AIH_ALLOW_HTTP_LLM``.
"""

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

# ── Protection anti-SSRF (appels LLM sortants) ───────────────────────

_PRIVATE_NETWORKS = (
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),   # CGNAT
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),  # link-local
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('::/128'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('::ffff:0:0/96'),   # IPv4-mapped IPv6
    ipaddress.ip_network('fc00::/7'),        # ULA
    ipaddress.ip_network('fe80::/10'),       # link-local IPv6
)


_MAX_REDIRECTS = 3


# Message actionnable (sans détail réseau) expliquant comment autoriser un LLM
# local/privé. Partagé par les erreurs HTTP (presets) et les logs (enhance).
LLM_URL_OPTIN_HINT = (
    "Pour un LLM local/privé (Ollama, LM Studio, vLLM…), autorisez son hôte via "
    "AIH_ALLOW_PRIVATE_LLM_HOSTS (ex. AIH_ALLOW_PRIVATE_LLM_HOSTS=localhost,192.168.1.20) "
    "et, pour du http://, ajoutez AIH_ALLOW_HTTP_LLM=1."
)


def _is_private_ip(ip_str):
    """True si l'adresse est privée/loopback/link-local (ou non parsable)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # non parsable → refuser par défaut
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 → vérifier la forme IPv4
    return any(ip in net for net in _PRIVATE_NETWORKS)


def _opt_in_private_hosts():
    """Hôtes privés explicitement autorisés (LLM locaux type ollama).

    Variable d'environnement ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` : liste de
    hostnames/IP séparés par des virgules. Toujours vide par défaut.
    """
    raw = os.environ.get('AIH_ALLOW_PRIVATE_LLM_HOSTS', '')
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def _host_allowed(host):
    """True si l'hôte peut être ciblé par un appel LLM sortant.

    Vérifie : IP littérale non privée, ou hostname dont TOUTES les IP
    résolues sont publiques (anti DNS-rebinding). Un hôte listé dans
    ``AIH_ALLOW_PRIVATE_LLM_HOSTS`` est toujours accepté (opt-in admin).
    """
    host = host.lower()
    if host in _opt_in_private_hosts():
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return not _is_private_ip(host)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {info[4][0] for info in infos}
    return bool(ips) and all(not _is_private_ip(ip) for ip in ips)


def _validate_llm_base_url(base_url):
    """Valide un base_url d'API LLM avant appel sortant (anti-SSRF).

    Retourne un message d'erreur (str) ou ``None`` si l'URL est acceptée.
    Règles :
    - https:// uniquement (http:// si hôte privé opt-in ou AIH_ALLOW_HTTP_LLM=1)
    - pas de credentials embarquées (user:pass@)
    - port standard (443/80), sauf hôte privé opt-in
    - hôte : pas d'IP privée/loopback/link-local, ni de DNS rebinding
      (toutes les IP résolues sont vérifiées)
    """
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return "URL invalide"
    if parsed.scheme not in ('http', 'https'):
        return "URL non supportée (https requis)"
    if parsed.username or parsed.password:
        return "Credentials embarquées interdites"
    host = (parsed.hostname or '').lower()
    if not host:
        return "Hôte manquant"
    opt_in = host in _opt_in_private_hosts()
    if parsed.scheme == 'http' and not opt_in and os.environ.get('AIH_ALLOW_HTTP_LLM', '') != '1':
        return "URL non supportée (https requis)"
    if not opt_in:
        default_port = 443 if parsed.scheme == 'https' else 80
        if parsed.port is not None and parsed.port != default_port:
            return "Port non autorisé"
        if not _host_allowed(host):
            return "Hôte refusé (adresse privée ou introuvable)"
    return None


def _safe_llm_get(url, headers, timeout=(5, 10)):
    """GET HTTP avec validation anti-SSRF, redirection par redirection.

    Lève ``ValueError`` (message générique, sans détail réseau) si un hôte
    est refusé ou si le nombre maximal de redirections est dépassé.
    """
    import requests
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        err = _validate_llm_base_url(current)
        if err:
            raise ValueError(f"Hôte refusé : {err}")
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get('Location')
            if not loc:
                raise ValueError("Redirection sans destination")
            current = urljoin(current, loc)
            continue
        return resp
    raise ValueError("Trop de redirections")


def _safe_llm_post(url, json_payload, headers, timeout=(5, 10)):
    """POST HTTP avec validation anti-SSRF (pas de suivi de redirection).

    Mêmes règles que ``_safe_llm_get`` pour l'hôte (IP privée interdites sauf
    opt-in ``AIH_ALLOW_PRIVATE_LLM_HOSTS``, anti DNS-rebinding, https requis).
    Une redirection sur un POST est traitée comme un échec (conservateur).
    """
    import requests
    err = _validate_llm_base_url(url)
    if err:
        raise ValueError(f"Hôte refusé : {err}")
    return requests.post(url, json=json_payload, headers=headers,
                         timeout=timeout, allow_redirects=False)
