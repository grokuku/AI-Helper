# AI-Helper — Règles du projet

## Conventions générales

### Ne pas compiler dans le dossier source
- Les builds, `__pycache__/`, `*.pyc`, `node_modules/` ne doivent jamais atterrir dans les sources.
- Le `.gitignore` actuel couvre déjà `__pycache__/`, `*.pyc`, `keywords.db`, `.env`.

### Pas de fichiers de données dans les sources
- La base SQLite (`keywords.db`) ne doit pas être stockée dans le dépôt.
- Les fichiers d'import temporaires (`__tmp_import.md`) sont déjà ignorés.
- Les fichiers de configuration locale (`.env`, tokens) ne doivent pas être commités.

### Objectif : dossier source propre
- Le dépôt doit pouvoir être copié-collé directement sur un serveur de test sans trier/supprimer des fichiers.
- Tout fichier généré automatiquement doit être dans `.gitignore` ou produit ailleurs.

## Architecture du projet

```
AI-Helper/
├── backend/          # Serveur Flask (app.py, auth.py, parser.py, etc.)
├── frontend/         # Interface web (index.html)
├── AIH_ComfyUI/     # Extension ComfyUI (nodes, web/)
├── web/js/           # Widgets JS pour ComfyUI
├── AGENTS.md         # Règles du projet
└── ROADMAP.md        # Roadmap et état d'avancement
```

## Filtres

- Les **filtres simples** stockent leur config dans `saved_filters.config` (JSON avec section, subsection, search_text, etc.)
- Les **filtres composés (union)** sont marqués `filter_type='union'` et référencent leurs membres via la table `filter_unions`.
- Le cache de chaque filtre est dans `filter_cache` — regénéré via `_rebuild_filter_cache()`.
- Un **filtre union** merge les caches de ses membres (déduplication automatique par PRIMARY KEY).

## Conventions de code

### Backend (Python/Flask)
- Fonctions helpers préfixées par `_` (ex: `_rebuild_filter_cache`, `_row_get`).
- Les endpoints API sont regroupés par domaine (`/api/filters`, `/api/presets`, etc.).
- Les migrations BDD se font dans `_init_db()` via `ALTER TABLE ... ADD COLUMN` ou `CREATE TABLE IF NOT EXISTS`.
- Utiliser `_login_required()` pour protéger les endpoints.
- Utiliser `get_db()` avec `sqlite3.Row` — ne pas oublier de `conn.close()`.

### Frontend (HTML/JS natif)
- Utiliser `const $ = id => document.getElementById(id)` pour les refs DOM.
- Les fonctions async utilisent `try/catch` systématique.
- L'API base URL est dans la constante `API` (configurée dynamiquement).
- `safeJson(res)` pour gérer les réponses non-JSON.
- `showModal(titre, message, type)` pour les notifications.
- `showConfirm(titre, message, callback)` pour les confirmations.
- `showPrompt(titre, label, placeholder, callback)` pour les saisies.

## Workflow Git

### ⛔ RÈGLE ABSOLUE : PAS DE GIT
- **L'assistant ne fait JAMAIS de `git commit`.**
- **L'assistant ne fait JAMAIS de `git push`.**
- **L'assistant ne fait JAMAIS de `git add`.**
- **L'assistant ne fait JAMAIS de `git reset`.**
- **Toute opération Git (commit, push, add, reset, rebase, cherry-pick, etc.) est exclusivement réservée à l'utilisateur.**
- Si l'utilisateur demande explicitement un commit ou un push, lui rappeler cette règle et refuser poliment.
- Les modifications de code sont faites dans les fichiers, point final. L'utilisateur décide quand et comment les versionner.

### Conventions de commit (pour l'utilisateur)
- Commits atomiques : un changement logique par commit.
- Suivre le format existant des messages de commit.

## Ideogram 4 — Architecture LLM

### Format Bbox : pixels → conversion 0-1000
- Le LLM travaille en **coordonnées pixels réelles** (ex: 1792x1008)
- L'API Ideogram 4 exige du **0-1000 normalisé** ([y_min, x_min, y_max, x_max])
- `convert_bboxes_to_normalized()` convertit après la sortie LLM, avant le return API
- **Détection automatique** : si max(bbox) ≤ 1000 → déjà normalisé, on ne touche pas
- **⚠️ PROBLÈME CONNU** : gemma3:12b ne respecte pas toujours les pixels. Si le LLM mélange (certaines bbox en 0-1000, d'autres en pixels), la conversion est partielle/incohérente. Exemple réel : élément 4 bbox [1100,600,1700,950] (pixels, converti) mais éléments 1-3 max≤1000 (non convertis car détectés comme déjà normalisés). Résultat : mix incohérent dans la sortie finale.

### Passes LLM
1. **Passe 1 (Génération)** : system_prompt + merged_text → JSON caption avec bboxes
2. **Passe 2 (Validation spatiale)** : corrige les bboxes, temperature 0.1
3. **Conversion** : pixels → 0-1000 pour Ideogram 4
4. Seulement `ideogram4` a une passe de validation (validation_passes=1)

### Debug output
- L'API `/api/enhance` retourne `debug_md` : markdown avec toutes les passes
- Node ComfyUI a une 5ème sortie `debug` (STRING)
- JS a un bouton "🔍 Voir debug LLM" ouvrant une fenêtre avec le markdown

### Règles Bbox
- Coords 0-1000 = 0-100% de chaque dimension (format Ideogram natif)
- Personne debout : y_span > x_span (haut/étroit)
- Personne allongée/plongeante : x_span > y_span (large/bas)
- **La forme du bbox dépend du SUJET, pas du ratio de l'image**
- Le LAYOUT (position) dépend du ratio : paysage = étaler horizontalement, portrait = empiler
- **Mais en coords pixels** : une sphère = bbox carré (x_span ≈ y_span), le LLM n'a pas à compenser l'aspect ratio

### Node ComfyUI (AIHIdeogram4Node)
- RETURN_TYPES : (STRING, INT, INT, IMAGE, STRING) = (prompt, width, height, preview, debug)
- Widgets natifs : seed, width, height, description, element_1..4, _api_config (1 seul hidden)
- **PAS de forceInput** sur width/height, **PAS de multiline** sur element_1..4
- DOM widget : preset/style selects, generate button, result textarea, debug button
- `_api_config` = JSON {api_url, api_key, preset_id, style_id}
- `loadedGraphNode` hook pour restore après ComfyUI workflow load

### Templates version
- v8 → v9 : bbox format changé de 0-1000 vers pixels, ajout routine conversion
- Migration auto si DB version < templates_version

### Decisions clés
- Simplicité > complexité : peu de widgets natifs = sérialisation stable
- Texte custom ComfyUI = raw (verbatim, pas de recherche sémantique)
- Update mechanism : git fetch + reset --hard + os.execv restart
- Frontend : index.html uniquement.

## Ollama Cloud - instabilité observée
- Ollama Cloud renvoie parfois des outputs vides OU manifestement tronqués (ex: 25 chars `masterpiece, best quality`)
- Bug principalement visible sur le **premier call après période d'inactivité** (cold start)
- **Important** : les `logging.warning(...)` introduisent un délai qui peut masquer des race conditions
- Si bug réapparaît après retrait des logs : ajouter un `time.sleep(0.1)` au même endroit
- Fix actuel : retry si `len(output) < 50`, jusqu'à 3 tentatives
- Seuil 50 chars rejette les outputs manifestement cassés (vides OU `masterpiece, best quality`)
- **Aucun filtre is_global dans le flow enhance** - c'est Ollama qui est instable, pas notre code

## Debug logging infrastructure
- Pattern `logging.warning(f"[enhance] ...")` pour tracer les requêtes/réponses
- 3 logs en place : REQUEST (params reçus), preset utilisé, LLM response (output_len + preview)
- Visible dans `server.log` (configuré par `run.sh`)
- **ATTENTION** : les logs peuvent masquer des bugs par leur effet de timing
