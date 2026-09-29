# Albums publics — déploiement et exploitation

> Chantier « albums publics » (phases 1-5). Ce document couvre : l'architecture
> des deux processes, l'exposition par sous-domaine Caddy, les variables
> d'environnement, la procédure de mise en ligne, les vérifications et les
> points de sécurité. **Le `Caddyfile` vit hors du dépôt** : le snippet ci-dessous
> est à recopier, pas un fichier à modifier.

---

## 1. Architecture

```
                    Internet
                       │  https://albums.<domaine>/a/<clé>
                       ▼
              ┌──────────────────┐
              │ Caddy (TLS auto) │   PAS d'Authentik / forward_auth sur ce vhost
              └────────┬─────────┘
                       │ reverse_proxy 127.0.0.1:8081
                       │ (Caddy sur une AUTRE machine → § 2 : bind réseau + firewall)
                       ▼
              ┌──────────────────────┐        ┌─────────────────────────────┐
              │ SERVICE PUBLIC        │ lit    │  <AIH_ALBUM_WEB_DIR>/<clé>/  │
              │ backend/public_app.py │───────▶│    manifest.json            │
              │ GET/HEAD seulement    │        │    thumb/0001.jpg           │
              └──────────────────────┘        │    full/0001.jpg (ou .png)  │
                                              └─────────────▲───────────────┘
                                                            │ écrit (worker async)
   ┌────────────────────────────────────────────────────────┴───────────────┐
   │ SERVEUR PRIVÉ (derrière Authentik) : backend/app.py — ./run.sh         │
   │ API /api/albums/* (création, ajout, révocation, suppression, purge)    │
   └─────────────────────────────────────────────────────────────────────────┘
```

- **Privé** (`./run.sh`, port `FLASK_PORT`, défaut 5000) : crée les albums
  (`POST /api/albums`), un worker d'arrière-plan pré-génère les fichiers web,
  puis l'album passe `ready`. Le front affiche `public_url`
  (`AIH_ALBUM_PUBLIC_BASE_URL` + `/a/<clé>`).
- **Public** (`./run_public.sh`, bind **127.0.0.1:8081** par défaut, réglable
  par `AIH_ALBUM_BIND_HOST`) : process **séparé, lecture seule, isolé** — aucun
  import de la base, du storage, de l'auth ni de
  `flask_cors`. Il ne lit que les fichiers du webroot et les assets de
  `backend/public_web/`. Il n'écrit **jamais** sur disque.

Les deux processes doivent partager **le même `AIH_ALBUM_WEB_DIR`** (le privé
écrit, le public lit). Le webroot par défaut est `<BASE_DIR>/.cache/albums`.

> ⚠️ Ne pas exposer `backend/app.py` sur ce sous-domaine : seule la surface
> d'album est publique par conception. Le privé reste derrière Authentik.

---

## 2. Snippet Caddy (à recopier dans le `Caddyfile`)

```caddyfile
albums.<domaine> {
	# Surface PUBLIQUE volontaire : NE PAS importer le snippet Authentik ici,
	# pas de forward_auth, pas de basic_auth. L'URL d'album (clé opaque) est
	# une capability : quiconque a le lien voit l'album.
	reverse_proxy 127.0.0.1:8081

	# Durcissement proxy minimal. Le service émet DÉJÀ les en-têtes de sécurité
	# (X-Robots-Tag, Referrer-Policy, nosniff, X-Frame-Options, CSP stricte) :
	# on ne fait ici que retirer la bannière « Server ».
	header -Server

	# Journal d'accès dédié (facultatif). Les clés d'album y sont masquées par
	# le service côté application ; côté Caddy, préférer un log local non exposé.
	log {
		output file /var/log/caddy/albums-access.log
		format json
	}
}
```

Notes :

- **TLS automatique** : Caddy obtient le certificat Let's Encrypt pour
  `albums.<domaine>` dès que le DNS pointe vers le serveur.
- **CORS** : ne JAMAIS ajouter d'en-tête `Access-Control-Allow-*` ici ; le
  service vérifie activement l'absence de CORS (test dédié).
- **Firewall** : rien à ouvrir tant que le service écoute sur `127.0.0.1`
  (défaut) — seuls 80/443 restent ouverts, déjà gérés par Caddy. Si Caddy est
  sur une AUTRE machine, lire le sous-chapitre suivant : bind réseau **et**
  restriction firewall à l'IP du proxy.
- Un vhost par sous-domaine : ne pas mélanger avec le vhost privé/Authentik.

### Reverse proxy sur une autre machine (Caddy distant ou Docker)

Par défaut le service écoute sur `127.0.0.1` : **injoignable depuis une autre
machine**. Pour qu'un Caddy situé ailleurs puisse l'atteindre :

1. Dans le `.env` racine, donner l'interface d'écoute — `0.0.0.0` (toutes) ou
   l'IP de l'interface du réseau concerné :

   ```dotenv
   AIH_ALBUM_BIND_HOST=0.0.0.0
   ```

   Valeurs acceptées : IP nue IPv4/IPv6 (`0.0.0.0`, `::`, `192.168.1.10`,
   `::1`) ou `localhost`. Toute autre valeur (vide, `hôte:port`, URL, crochets
   `[::1]`…) est **ignorée** avec un avertissement dans `public_server.log` et
   le service retombe sur `127.0.0.1` — jamais de crash au démarrage.

2. Relancer `./run_public.sh` : le script exporte le `.env` et affiche l'hôte
   effectif (avertissement explicite si l'écoute n'est plus loopback).
   Vérifier ensuite côté machine :

   ```bash
   ss -ltnp | grep ':8081'   # attendu 0.0.0.0:8081 ou <IP>:8081 (adapter le port)
   ```

3. **Restreindre l'accès par firewall à la seule IP du reverse proxy** : sans
   cela le service est exposé à tout le réseau et l'isolation loopback est
   perdue (la clé d'album reste la seule capability). Exemple `ufw` :

   ```bash
   sudo ufw allow from <IP_DU_PROXY> to any port 8081 proto tcp
   sudo ufw status numbered     # 8081/tcp ALLOW <IP_DU_PROXY> uniquement
   ```

   Équivalent `nftables` (chaîne input en politique `drop`) :

   ```bash
   sudo nft add rule inet filter input ip saddr <IP_DU_PROXY> tcp dport 8081 accept
   ```

   Contrôle négatif : depuis toute autre machine que le proxy,
   `nc -vz <IP_SERVICE> 8081` doit **échouer** (refus/timeout).

4. Côté Caddy (machine distante), viser l'IP du service **sur le réseau**, pas
   `127.0.0.1` : `reverse_proxy <IP_SERVICE>:8081`.

#### Cas Docker / réseau partagé

- Dans un conteneur, `127.0.0.1` désigne **le conteneur lui-même** : Caddy ne
  peut pas l'atteindre, même sur la même machine. Binder sur `0.0.0.0`
  (`AIH_ALBUM_BIND_HOST=0.0.0.0` dans le `.env`) et faire communiquer Caddy et
  le service par un **réseau Docker partagé**.
- Caddy conteneurisé sur le même réseau : viser le **nom de service ou du
  conteneur** (`reverse_proxy aih-public:8081`) sans publier le port sur
  l'hôte — cas le plus étanche (aucun port réseau exposé), à préférer.
- Caddy sur une autre machine que le conteneur : publier le port en le liant à
  l'IP jointe (`-p <IP_INTERFACE>:8081:8081`, **pas** `-p 8081:8081` seul),
  puis appliquer la règle firewall du point 3.
- `AIH_ALBUM_WEB_DIR` doit rester **partagé** entre privé et public (volume
  commun) quel que soit le mode réseau.

> ⚠️ Rappel : ce vhost est **SANS Authentik** (pas de `forward_auth`, pas de
> `basic_auth`) — la clé d'album opaque est la seule capability. Toute
> exposition réseau non filtrée affaiblit donc directement l'isolation du
> service public. En cas d'exposition, activer au minimum `AIH_ALBUM_GUARD=log`
> (`on` si nécessaire) et surveiller `public_server.log`.

---

## 3. Variables d'environnement

| Variable | Défaut | Rôle | Recommandé |
|---|---|---|---|
| `AIH_ALBUM_PORT` | `8081` | Port d'écoute du service public | `8081` si libre |
| `AIH_ALBUM_BIND_HOST` | `127.0.0.1` | Hôte d'écoute du service public. Loopback par défaut (**non exposé au réseau**) ; `0.0.0.0` ou IP d'interface pour un reverse proxy distant — **firewall obligatoire** (§ 2) | laisser `127.0.0.1`, sauf Caddy distant : `0.0.0.0` + firewall |
| `AIH_ALBUM_WEB_DIR` | `<BASE_DIR>/.cache/albums` | Webroot des albums — **identique pour le privé et le public** | laisser le défaut (ou disque persistant) |
| `AIH_ALBUM_PUBLIC_BASE_URL` | *(vide)* | Base des `public_url` renvoyées par l'API privée ; vide → `public_url: null` (le front affiche la clé) | `https://albums.<domaine>` |
| `AIH_ALBUM_GUARD` | `off` | Garde-fou soft du service public : `off` \| `log` \| `on` (voir § 6) | `log` pour observer, `on` pour couper l'abus |
| `AIH_ALBUM_MAX_SYNC` | — | **Prévue mais NON lue par le code actuel (aucun effet)** | ne pas s'en servir |
| `AIH_ALBUM_MAX_ITEMS` | — | **Prévue mais NON lue par le code actuel (aucun effet)** | ne pas s'en servir |

Bornes réelles à connaître :

- une création/ajout d'album accepte au plus **500 ids** (`MAX_ALBUM_IDS`,
  `backend/routes/albums.py`) ;
- l'album est un **snapshot figé** : un média uploadé plus tard n'y entre
  jamais automatiquement ;
- la purge définitive d'un média le retire de tous ses albums (fichiers +
  manifest réécrits, `backend/album_web.py:remove_media_from_albums`).

`run_public.sh` exporte le `.env` racine (le service public n'importe pas
dotenv) ; sans `.env`, les défauts ci-dessus s'appliquent. Une valeur d'hôte
invalide retombe sur `127.0.0.1` (avertissement dans `public_server.log`).

---

## 4. Procédure de mise en ligne (numérotée)

1. **DNS** : créer l'enregistrement `A` (et `AAAA` si IPv6) `albums.<domaine>`
   → IP du serveur. Vérifier la propagation :
   `dig +short albums.<domaine>`.
2. **Caddy** : recopier le vhost du § 2 dans le `Caddyfile`, puis
   `caddy validate --config /etc/caddy/Caddyfile` et `systemctl reload caddy`.
3. **`.env`** (racine du projet) : ajouter au minimum
   `AIH_ALBUM_PUBLIC_BASE_URL=https://albums.<domaine>` (optionnel :
   `AIH_ALBUM_GUARD=log`, `AIH_ALBUM_WEB_DIR=...`).
4. **Redémarrer le privé** : `./run.sh` — le privé relit le `.env` et affiche
   désormais les URLs publiques complètes.
5. **Lancer le public** : `./run_public.sh` — vérifie le venv, tue l'ancien
   process public uniquement, écrit les logs dans `public_server.log`.
6. **Vérifier** :
   - `curl -sSI https://albums.<domaine>/a/inconnu` → `HTTP/2 404`,
     `content-type: text/plain`, en-têtes noindex/nosniff…, **aucune** page ni
     redirection de login (`location:` absent) ;
   - `curl -sSI https://albums.<domaine>/a/<clé>/manifest.json` → `200`,
     `cache-control: no-cache` ;
   - le privé répond toujours (ouvrir l'interface) ;
   - `tail -f public_server.log` ne montre aucune erreur au démarrage.
7. **Partager** : dans le front privé, copier l'URL (`https://albums…/a/<clé>`)
   ou le QR affiché par la modale de résultat.
8. **Révoquer** (en cas de fuite) : bouton « Révoquer » côté privé, puis
   `curl -sS -o /dev/null -w '%{http_code}\n' https://albums.<domaine>/a/<clé>/manifest.json`
   → `404`.

---

## 5. Vérifications rapides (aide-mémoire)

```bash
# 404 uniforme, sans login (le point de contrôle de la checklist) :
curl -sSI https://albums.<domaine>/a/inconnu | head -12

# Manifest d'un album (remplacer <clé>) :
curl -sS https://albums.<domaine>/a/<clé>/manifest.json | python3 -m json.tool

# Hôte/port d'écoute effectifs (loopback par défaut ; 0.0.0.0 si proxy distant) :
ss -ltnp | grep ':8081'

# Service local (avant Caddy) :
curl -sSI http://127.0.0.1:8081/a/inconnu

# État des processes (le privé et le public sont indépendants) :
pgrep -af "backend/(public_)?app.py"

# Logs :
tail -n 50 public_server.log     # service public
grep '\[public\] écoute' public_server.log | tail -1   # hôte:port effectifs au démarrage
tail -n 50 server.log            # serveur privé
```

Attendu sur `/a/inconnu` : `404`, corps `Not Found`, en-têtes `X-Robots-Tag:
noindex, nofollow, noarchive`, `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP stricte,
**aucun** en-tête `Access-Control-Allow-*` et **aucune** redirection.

---

## 6. Garde-fou de débit (soft, désactivé par défaut)

Choix du chantier : **pas de rate limiting par défaut** (pour ne pas gêner
l'usage légitime). Un garde-fou optionnel est disponible via
`AIH_ALBUM_GUARD` :

| Mode | Comportement |
|---|---|
| `off` (défaut) | Aucun comptage, aucun log, aucun 429 — comportement inchangé. |
| `log` | Compte par IP et loggue les dépassements **sans jamais bloquer** : rafales de requêtes et 404 répétés (signal d'énumération). |
| `on` | Comme `log`, mais renvoie **429 uniforme** (`Retry-After: 60`) à l'IP en dépassement ; les autres IP ne sont pas affectées. |

Seuils (constantes dans `backend/public_app.py`, fenêtre glissante de 60 s) :

- **240 requêtes / min / IP** toutes routes confondues (`GUARD_MAX_CALLS`) —
  une page d'album de 100 photos ≈ 103 requêtes au premier chargement, puis
  cache navigateur ;
- **30 réponses 404 / min / IP** (`GUARD_MAX_404`) : en mode `on`, une IP qui
  balaye est coupée même sur un chemin valide tant que la fenêtre reste pleine.

Les logs du garde-fou contiennent l'IP, le motif et le chemin **avec la clé
masquée** (`/a/<key>`) : jamais de capability en clair. Le journal d'accès
Werkzeug est filtré de la même façon.

Pour l'activer : `AIH_ALBUM_GUARD=log` (observation) → relancer
`./run_public.sh`, surveiller `public_server.log`, puis `AIH_ALBUM_GUARD=on`
si nécessaire. Une valeur inconnue retombe sur `off` (fail-safe).

---

## 7. Sécurité — ce qui est garanti

- **Clé = capability non devinable** : `secrets.token_urlsafe(32)` → 43
  caractères (256 bits), unicité vérifiée en base (`_new_album_key`).
- **Pas d'énumération** : 404 STRICTEMENT uniforme (même corps/statut) pour
  clé inconnue, clé mal formée, item hors manifest, fichier non listé, route
  inconnue ; une clé bien formée reçoit toujours la coquille, l'état
  « introuvable » est géré côté page (test dédié).
- **Appartenance vérifiée à chaque requête** : regex stricte sur clé/nom,
  dossier existant, item ∈ manifest, confinement `realpath` sous le dossier de
  l'album ; un fichier présent mais non listé n'est pas servi.
- **Lecture seule** : seules `GET`/`HEAD` ; tout le reste → `405` uniforme.
- **Zéro fuite privée** : manifest sans `media_id`/`user_id`/chemin/nom
  d'origine ; noms de fichiers opaques (`0001.jpg`) ; images pleine taille
  **ré-encodées** (JPEG q90, ou PNG si transparence réelle) → EXIF/métadonnées
  purgés.
- **Pas de CORS** : aucun en-tête `Access-Control-Allow-*` (le service les
  supprime activement).
- **En-têtes** : `X-Robots-Tag` (noindex), `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP stricte
  (`default-src 'none'`, pas d'inline script).
- **Révocation immédiate** : le dossier est renommé **avant** le passage en
  `revoked` ; manifest et médias répondent 404 dans la seconde.
- **Isolation du process** : `public_app.py` n'importe ni `db`, ni `storage`,
  ni `auth`, ni `security`, ni `routes`, ni `flask_cors` — vérifié par analyse
  AST **et** par import dans un process frais (tests dédiés).
- **Traversal refusé** : `..%2f`, `%2e%2e`, noms hors manifest → 404 uniforme.
- **Logs sans clé complète** : garde-fou et journal Werkzeug masquent
  `/a/<clé>` en `/a/<key>`.

### Risques résiduels ASSUMÉS

- **Fuite du lien** : l'URL est une capability — qui l'a peut voir l'album
  (pas de mot de passe, pas de login). En cas de fuite : révoquer.
- **Bande passante / hotlinking** : pas de rate limit par défaut, pas de
  protection anti-hotlink ; les fichiers pleine taille (immutables, cache 1 an)
  peuvent être repris tels quels. Activer `AIH_ALBUM_GUARD=on` pour borner,
  et surveiller `public_server.log`.
- **Caches clients** : les médias sont servis `immutable, max-age=31536000` ;
  une révocation coupe l'accès réseau mais **ne purge pas** ce qu'un visiteur a
  déjà téléchargé/en cache.
- **Fichiers révoqués conservés** : la révocation renomme le dossier
  (`<clé>.revoked`) sans le supprimer — libérer l'espace manuellement si
  besoin.
- **Serveur de développement Flask** : le service public utilise
  `app.run(threaded=True)` (pas de Gunicorn/Waitress dans les dépendances) —
  suffisant derrière Caddy pour un usage personnel, mais pas de workers ni de
  limite de concurrence robuste. À durcir si l'audience grossit.
- **Logs Caddy** : si le log d'accès Caddy est activé, il contient les URLs
  complètes (clés incluses) ; le placer sur un disque non exposé.
- **Exposition réseau si bind non-loopback** : passer `AIH_ALBUM_BIND_HOST` à
  `0.0.0.0` ou à une IP rend le service joignable au-delà de la machine ; sans
  règle firewall limitée à l'IP du reverse proxy, toute la surface publique
  (page, manifest, médias) l'est aussi. Le défaut loopback est conservé
  précisément pour éviter ce scénario.

---

## 8. Révocation, suppression, purge

| Action (front privé) | Effet base | Effet disque | Effet public |
|---|---|---|---|
| **Révoquer** | `status='revoked'` | dossier renommé `<clé>.revoked` | 404 immédiat |
| **Supprimer** | lignes supprimées (CASCADE items) | dossier(s) `.revoked*` supprimés | 404 immédiat |
| **Purger un média** (corbeille → purge définitive) | liens `album_media` retirés | fichiers de l'item supprimés + manifest réécrit | l'item disparaît de la page |

---

## 9. Fichiers concernés (repères)

- `run_public.sh` — lanceur du service public (à ne pas confondre avec `run.sh`,
  lanceur privé ; le pkill du public ne cible que `backend/public_app.py`).
- `backend/public_app.py` — service public (routes, en-têtes, garde-fou).
- `backend/public_web/` — coquille HTML + assets + briques holaf vendues.
- `backend/album_web.py` — génération des fichiers/manifest (côté privé).
- `backend/routes/albums.py` — API privée `/api/albums/*`.
- `backend/tests/test_albums.py`, `backend/tests/test_albums_public.py` —
  suites privée et publique (contrôles négatifs inclus).
