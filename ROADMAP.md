# Roadmap — AI-Helper Helper

> Fichier vivant pour noter les idées, fonctionnalités et réflexions.

---

---

## 🚀 Session (29/06/2026) — Renommage FRIA→AIH, Node Keywords & Elements Picker LLM

### ✅ Refactor global : FRIA → AIH

Renommage complet et systématique du prefix `FRIA` → `AIH` dans toute la codebase :

| Élément | Avant | Après |
|---------|-------|------|
| Dossier ComfyUI | `FRIA_ComfyUI/` | `AIH_ComfyUI/` |
| Fichiers JS | `fria_*.js` | `aih_*.js` |
| Routes HTTP | `/fria/*` | `/aih/*` |
| localStorage | `FRIA_config` | `AIH_config` |
| Classes Python | `FRIA*` prefixes | `AIH*` prefixes |
| Fonctions JS | `_fetchFriaApi` | `_fetchAihApi` |
| Placeholders SFTP | `fria` | `aih` (frontend/index.html) |

- **Nettoyage** : suppression des fichiers morts (`FRIA_ComfyUI/`, `fria_shared.js`)
- **Ajout** : `AIH.getServerUrl()` dans `aih_shared.js`

### ✅ Menu ComfyUI — changement de titre

- Titre du menu : `"AIH ▾"` → `"AI Helper ▾"`

### ✅ Nouvelle node : AIH Keywords

Node ComfyUI complète pour la génération et le filtrage de keywords, avec widget JS riche.

| Composant | Détail |
|-----------|--------|
| **Node Python** | `AIHKeywordsNode` — `AIH_ComfyUI/nodes/keywords_node.py` |
| **Widget JS** | `web/js/aih_keywords_widget.js` |

**Inputs :**
- Section dropdown (liste des sections de la BDD)
- Subsection dropdown (filtrée par section)
- NSFW dropdown : Tout / SFW / NSFW
- Confidence slider (défaut 50%)
- Include / Exclude / Semantic text fields

**Boutons :**
- Load (dropdown des filtres sauvegardés depuis l'API)
- Save (POST `/api/filters`)
- Reset

**Sorties :**
- `random_keyword` (STRING, déterministe par seed)
- `keywords_list` (STRING)
- Format de sortie : dropdown Text / JSON / Markdown

**Fonctionnalités :**
- Auto-update : debounce 500ms sur tous les changements → GET `/api/keywords`
- Persistance workflow : widget caché `_keywords_config` (serializable), restauration complète au chargement
- Seed caché : `control_after_generate` masqué pour forcer la réexécution à chaque run
- Recherche sémantique : implémentée côté backend (GET `/api/keywords` avec paramètre `semantic` + `min_confidence`, embedding cosine similarity)
- Layout : NSFW à gauche, slider au milieu (flex:1), format à droite

**Fix backend :** mismatch de clé `semantic` vs `semantic_text` dans `filters.py` (rétro-compatible)

### ✅ Elements Picker — Mode LLM Intelligent

Ajout d'un mode LLM contextuel dans l'Elements Picker :

| Fonctionnalité | Détail |
|---------------|--------|
| Toggle 🧠 par liste | Activation du filtrage LLM contextuel (OFF par défaut) |
| Dropdown Preset IA | Sélection du modèle LLM (GET `/api/presets`) |
| Champ nombre par défaut | Pour la syntaxe `||concept` (défaut 10) |
| Syntaxe `||concept:N` | Génération de listes par LLM (ex: `||couleur:10` → 10 couleurs) |
| Mode filtrage (🧠 ON, liste manuelle) | LLM filtre la liste selon le contexte accumulé → random dans la sous-liste |
| Mode génération (||concept, 🧠 ON) | LLM génère une liste contextuelle → random dedans |
| Mode génération (||concept, 🧠 OFF) | LLM génère une liste simple → random dedans |
| Contexte séquentiel | Traitement haut→bas, accumulation des choix précédents |
| Fallback erreur LLM | Random sur liste d'origine (manuel) ou skip (||) |
| Appel backend | POST `/api/keywords/llm-process` avec `{preset_id, instruction, input_text}` |
| Sérialisation | `preset_id`, `llm_default_count`, `brain_toggles[]` dans `_elements_json` |

### ✅ Elements Picker — Nouveau format de listes

Migration du format de parsing des listes dans l'Elements Picker :

| Aspect | Ancien format | Nouveau format |
|-------|--------------|---------------|
| Syntaxe | `bleu::rouge::vert` | `{bleu::rouge::vert}` |
| Split | par `::` | blocs entre accolades `{}` |
| Templates | N/A | `femme de {25::30::35::40} ans` → "femme de 30 ans" |
| Blocs multiples | N/A | `{blond::brun} cheveux {long::court}` → "brun cheveux court" |
| Texte littéral | N/A | Sans `{}` = texte brut retourné tel quel |
| Badge visuel | N/A | 🔀N affiche le nombre de choix par bloc en temps réel |
| Compatibilité LLM | `||concept:N` inchangé | `||` et `{}` ne se mélangent pas |

### ✅ Fix ordre de chargement JS

- Renommage `aih_shared.js` → `03_aih_shared.js` et `aih_widget_base.js` → `04_aih_widget_base.js`
- Correction du crash `AIH.waitForApp is not a function` (ordre alphabétique ComfyUI)
- Fix global pour tous les widgets (elements, enhance, ideogram, prep, terminal)

### 📋 Analyse des icônes

Inventaire complet des icônes utilisées dans la codebase :

- **78 emojis/icônes uniques** sur 20+ fichiers
- **12 incohérences identifiées** (✓/✗ vs ✅/❌, 🔄 surutilisé, 📦 pour 3 concepts, etc.)
- Remplacement par SVG icons0.dev prévu pour plus tard (non fait cette session)

### 📝 Fichiers créés

| Fichier | Description |
|---------|-------------|
| `AIH_ComfyUI/nodes/keywords_node.py` | Node Python AIH Keywords |
| `web/js/aih_keywords_widget.js` | Widget JS AIH Keywords |

### 📝 Fichiers modifiés

| Fichier | Changement |
|---------|-----------|
| `__init__.py` (root) | Enregistrement `AIHKeywordsNode` |
| `web/js/aih_shared.js` (→ `03_aih_shared.js`) | Ajout `getServerUrl()`, renommage |
| `web/js/aih_elements_widget.js` | Mode LLM, format `{}`, badge choix |
| `AIH_ComfyUI/nodes/elements_node.py` | Parsing `{}`, LLM, `||concept` |
| `frontend/index.html` | Placeholders SFTP `fria`→`aih` |
| `web/js/02_aih_model_browser.js` | `_fetchFriaApi` → `_fetchAihApi` |
| `web/js/aih_menu.js` | Titre menu "AI Helper ▾" |
| `backend/routes/keywords.py` | Recherche sémantique |
| `backend/routes/filters.py` | Fix clé `semantic`/`semantic_text` (rétro-compatible) |

---

## 🚀 Session (28/06/2026) — Code Review Blobby Companion

### ⬜ Code review Blobby Companion — 4/4 findings

Tous les findings Blobby ont été corrigés lors de la session précédente.

---

## 🚀 Session (28/06/2026) — Audit exhaustif du code

### 🔍 Analyse complète — nouveaux bugs découverts

| # | Fichier | Bug | Sévérité | Statut |
|---|---------|-----|----------|--------|
| **N1** | `app.py` | **`import logging` manquant** — `_load_ollama_config_at_startup()` utilise `logging.warning()` sans avoir importé `logging`. Si une exception survient (BDD absente), crée un `NameError` qui tue le thread de démarrage silencieusement. | **CRITIQUE** | ⬜ |
| **N2** | `app-core.js` / `app-admin.js` | **`escapeHtml()` définie 2 fois** dans `app-keywords.js` lignes ~760-761. Pas de bug runtime (la 2nde définition écrase la 1ère) mais code mort. | LOW | ⬜ |
| **N3** | `enhance_node.py` | **`seed: null` envoyé au backend** — quand `seed=0`, le payload JSON contient `"seed": null` (car `seed if seed > 0 else None`). Certains parseurs LLM peuvent mal interpréter `null`. | MEDIUM | ⬜ |
| **N4** | `enhance.py` | **`_call_llm_internal` ne vérifie pas la qualité du dernier retry** — après la boucle `for retry in range(3)`, si l'output final fait < 50 chars (toutes les tentatives échouées silencieusement), le résultat tronqué est renvoyé sans alerte. | MEDIUM | ⬜ |
| **N5** | `enhance.py` | **`_do_validation_pass` pas de retry** — contrairement à `_call_llm_internal`, la fonction de validation spatiale n'a pas de retry sur output vide/tronqué. Instabilité Ollama Cloud non gérée pour la validation. | MEDIUM | ⬜ |
| **N6** | `ideogram4_node.py` | **`_build_caption_local_loop()` jamais appelée** — méthode définie mais non utilisée dans `build_caption()`. Code mort dans un node ComfyUI. | LOW | ⬜ |
| **N7** | `import_export.py` | **Variable `tmp` scope dangereux dans `try/finally`** — `tmp` n'est défini que dans la branche `if 'file' in request.files`. Protégé par court-circuit `delete_after and tmp.exists()` mais fragile au refactoring. | LOW | ⬜ |
| **N8** | `enhance.py` | **`repeat_penalty` envoyé à toutes les APIs** — paramètre spécifique Ollama envoyé à toute API OpenAI-compatible. OpenAI/DeepSeek l'ignorent silencieusement mais certains providers (vLLM, TGI) peuvent rejeter le paramètre inconnu. | MEDIUM | ⬜ |
| **N9** | `keywords_llm_process` | **Timeout 5s trop court pour listing `/models`** — Ollama Cloud avec cold start peut prendre >5s. Le timeout devrait être augmenté ou rendu configurable. | LOW | ⬜ |
| **N10** | `app-admin.js` | **`loadOllamaConfig()` ne gère pas les erreurs HTTP** — si le serveur répond 500, `res.json()` succeed avec un message d'erreur mais le code n'affiche pas l'erreur, juste "Erreur" générique. | LOW | ⬜ |
| **N11** | `_prepare_enhance` | **Seed complètement ignoré dans le flow `/api/enhance`** — le paramètre `seed` est reçu dans `data` mais n'est ni passé à l'appel LLM ni utilisé pour le `random.choice()` des EP/random. Pas de déterminisme possible. | HIGH | ⬜ (déjà tracké L4, confirmé par audit) |
| **N12** | `enhance.py` | **`frequency_penalty` ET `repeat_penalty` envoyés simultanément** — bug connu M6. Confirmé présent dans `_prepare_enhance()` ligne ~690 : les deux paramètres sont dans `llm_request`. Ollama les accepte mais c'est redondant/contradictoire. | HIGH | ⬜ (M6) |
| **N13** | `exporter.py` | **Troncature `[:100]` dans le footer** — `" → ".join(...)[:100] + "..."` peut couper un titre de section en plein milieu (bug L11, confirmé). | LOW | ⬜ (L11) |
| **N14** | `app-keywords.js` | **`kwLoadColWidths()` applique les largeurs à TOUS les `<th>`** — y compris la colonne score qui est cachée en mode texte, provoquant un décalage visuel (déjà tracké). | MEDIUM | ⬜ |

### 📊 Résumé de l'audit exhaustif (28/06/2026)

| Sévérité | Nouveaux bugs | Déjà trackés (confirmés) | Total |
|-----------|--------------|--------------------------|-------|
| 🔴 Critique | 1 | 0 | **1** |
| 🟠 Majeur | 0 | 0 | **0** |
| 🟡 Medium | 4 | 2 (M6, L4) | **6** |
| 🟢 Low | 6 | 1 (L11) | **7** |
| **Total** | **11** | **3** | **14** |

### ⚠️ Points d'attention (non-bugs, mais à connaître)

| # | Description |
|---|-------------|
| A1 | **`_load_ollama_config_at_startup()` attrape `Exception`** avec un bare `except` — le `NameError` de N1 serait attrapé ici, donc pas de plantage complet, mais la config Ollama ne serait pas chargée et le log serait trompeur. À nettoyer (corriger N1 + logger correct). |
| A2 | **`_finish_enhance_pass1` ouvre une 2ème connexion BDD** (`conn2 = get_db()`) malgré le fix M5 qui réduisait de 6 à 1 connexion dans `_prepare_enhance`. Le total cloud = 2 connexions par requête enhance. Non bloquant mais sous-optimal. |
| A3 | **`callEnhanceLocalLLM()` ne passe pas l'API key du preset au LLM local** — commentaire dans le code : "la cle API est deja dans llm_config.api_key, mais pas d'auth explicite ici". Si un serveur LLM local nécessite une auth (ex: LM Studio configuré), le mode client-side échouera. |
| A4 | **`_generate_all_embeddings()` synchrone et bloquante** — pour 500+ keywords, peut prendre 50+ secondes. Mentionné dans le code comme "à envisager en asynchrone". |
| A5 | **`bulk_import_keywords()` appelle `_regenerate_keyword_embedding()` par keyword** — 1 appel HTTP Ollama par keyword importé. Très lent pour des imports de 100+ keywords. |
| A6 | **`_build_debug_markdown()` construit le debug même pour les templates non-Ideogram** — le debug markdown est maintenant généré pour tous les templates (plus de filtre `ideogram`). Pas de bug mais peut produire un markdown vide/inutile pour les templates texte simples. |
| A7 | **`export_to_markdown()` mentionne "Danbooru / Illustrious"** dans le titre générique. Héritage du format historique. Harmless. |

### ⬜ Rappel des bugs restants non-corrigés (trackés avant cette session)

| # | Description | Sévérité | Priorité |
|---|-------------|----------|----------|
| M6 | `repeat_penalty` + `frequency_penalty` simultanés | HIGH | ⬜ |
| L4/L11 | Seed ignoré dans `/api/enhance` | HIGH | ⬜ |
| L1 | Embedding par keyword dans bulk import (lent) | MEDIUM | ⬜ |
| L2 | `scan_keyword_duplicates` O(n²) | LOW | ⬜ |
| L5 | `filtersBar` déclaré inutilisé | LOW | ⬜ |
| L6 | `_loadApiKeySettings()` jamais appelé (code mort) | LOW | ⬜ |
| L7 | `document.execCommand('copy')` déprécié | LOW | ⬜ |
| L8 | Variables `delete_after`/`tmp` scope fragile | LOW | ⬜ |
| L9 | Doublon global check inclut les keywords privés | LOW | ⬜ |
| L10 | README ComfyUI nom contradictoire | LOW | ⬜ |
| H2 | Encryption key en BDD (migrer env var) | MEDIUM | ⬜ |
| H4 | CORS wide open | MEDIUM | ⬜ |
| H5 | API key dans localStorage | MEDIUM | ⬜ |
| H6 | `conn.close()` sans `finally` dans plusieurs routes | MEDIUM | ⬜ |

---

## 🚀 Session (28/06/2026) — Code Review Blobby Companion

### ✅ Code review Blobby Companion — 3/4 findings corrigés

| Finding | Severity | Fix | Statut |
|---------|----------|-----|--------|
| Empty-string collision stuck detection | **HIGH** | `lastReplyHash = ''` → `'__INITIAL__'` ; `.substring(0,100)` → `.trim()` | ✅ |
| Duplicate fallback dead code | **MEDIUM** | Ligne morte `if (!finalReply)` supprimée | ✅ |
| Commentaire threshold erroné | **LOW** | `3ème fois` → `2ème fois` | ✅ |
| Unicode escaping inconsistency | **LOW** | Résolu par suppression de la ligne morte (MEDIUM) | ✅ |

---

## 🚀 Session (22/06/2026) — Correction Blobby + Code Review Backend

### ✅ Code review backend — 15/20 findings corrigés

| Finding | Severity | Fix | Statut |
|---------|----------|-----|--------|
| F1 — SECRET_KEY régénéré à chaque restart | HIGH | Persisté dans `.secret_key` + fallback env var | ✅ |
| F2 — Double `conn.close()` dans `_insert_default_templates` | HIGH | Supprimé, le caller gère le cycle | ✅ |
| F3 — 25+ `except Exception: pass` silencieux | HIGH | Remplacés par `logging.exception()` partout | ✅ |
| F4 — SQL f-string visuellement dangereux | MEDIUM | Commentaire `# SAFE:` ajouté | ✅ |
| F5 — Clé Fernet dans la même BDD que les données | MEDIUM | `ENCRYPTION_KEY` env var prioritaire | ✅ |
| F7 — TOCTOU race premier admin | MEDIUM | `BEGIN IMMEDIATE` transaction | ✅ |
| F8 — Pas de rate limiting | MEDIUM | Token bucket in-memory sur 3 endpoints | ✅ |
| F9 — Retry Ollama sans backoff | MEDIUM | Exponential backoff (1s/2s/4s) + `try/catch` | ✅ |
| F10 — `_load_ollama_config` fail silencieux | MEDIUM | `logging.warning()` ajouté | ✅ |
| F11 — Timeout connect/read 180s | LOW | `timeout=(10, 180)` séparé | ✅ |
| F16 — Bbox detection per-element (mix coords) | LOW | Heuristique globale : si UNE bbox > 1000 → toutes en pixels | ✅ |
| F18 — `os.execv` fail → thread meurt silencieusement | LOW | `os._exit(1)` fallback + log stderr | ✅ |
| F19 — `debug=True` en production | LOW | `FLASK_DEBUG=0` env var | ✅ |
| F20 — Pas de validation Content-Type | LOW | Helper `_require_json()` → 415 | ✅ |
| F12 — `keywords.db` tracké par git | LOW | Faux positif — déjà ignoré | ⬜ |
| F6 — `is_admin` fail open | LOW | Faux positif — déjà fail-secure | ⬜ |

### ✅ Blobby — Rollback de la migration ratée des settings

- **Problème** : 8 commits qui tentaient de déplacer les settings Blobby du chat (⚙️) vers la modale AI-Helper de ComfyUI (`renderBlobbyTab` dans `aih_menu.js`). Cause : boucle infinie (`openSettings` → click Blobby tab → `renderBlobbyTab` → `openSettings`...), `Blobby is not defined` (const pas sur `window`), settings jamais appliqués en temps réel.
- **Correction** : revert `web/js/blobby_companion.js` et `web/js/aih_menu.js` à leur état `71dd2b0` (settings dans le chat, mémoire vectorielle intacte). Tous les fixes backend conservés par cherry-pick.

### ✅ Blobby — Corrections diverses

| Fix | Détail |
|-----|--------|
| **DOM widgets figés** | `canvas.setDirty(false, true)` → `setDirty(true, true)` (foreground + background) |
| **Shell /bin/bash** | `_sp.run(cmd, shell=True)` utilise maintenant `/bin/bash` si disponible (support boucles for, `[ tests ]`) |
| **Regex [SHELL] tronquée** | `[^\]]+` → `.+` (greedy, va au dernier `]` de la ligne, pas au premier) |
| **Slider transparence supprimé** | Slider inutilisable dans le header du chat (conflit avec le drag) |
| **Markdown rendering** | Parser Markdown inline (tables, gras, italique, listes, code, headings, liens) |
| **Lookbehind incompatible** | `(?<!\*)` → `(^|[^*])` pour compatibilité navigateur |
| **Callback écrase le markdown** | `lastMsg.innerHTML = updatedReply` → `_blobbyMarkdownToHtml(updatedReply)` |
| **Boucle agentic** | Multi-tour LLM (max 5) : le LLM peut enchaîner commandes → résultats → analyse |
| **Info environnement** | Prompt enrichi avec OS, shell, chemins ComfyUI |
| **Placeholders visibles** | `⏳` nettoyés avant affichage final |
| **Continuïté du chat** | `_getRecentChatHistory()` inclut l'historique dans les tours agentic |

### ⬜ Blobby — Restant

| Tâche | Priorité |
|-------|----------|
| Modifier le caractère / personnalité de Blobby | Moyenne (quand l'agentic loop sera stable) |
| CORS sync serveur (500 + headers manquants) | Faible (localStorage fonctionne en fallback) |

### ✅ Audit bugs — Mis à jour

| Bug | Statut |
|-----|--------|
| M6 — `repeat_penalty` + `frequency_penalty` simultanés | ⬜ Restant |
| L1 — Embedding par keyword (lent) | ⬜ Restant |
| Seed ComfyUI ignoré dans `/api/enhance` | ⬜ Restant |
| `loadColWidths()` redimensionne colonnes cachées | ⬜ Restant |
| Code mort : `_loadApiKeySettings()` jamais appelé | ⬜ Restant |
| Troncature footer export `[:100]` | ⬜ Restant |
| README ComfyUI : nom contradictoire | ⬜ Restant |
| H2 — Encryption key en BDD (migrer env var) | ⬜ Restant |
| H4 — CORS wide open | ⬜ Restant |
| H5 — API key dans localStorage | ⬜ Restant |

---

## 🔍 Audit complet du code — Rapport de bugs (20/06/2026)

> Analyse exhaustive de tous les fichiers du projet (backend, frontend, ComfyUI).
> Bugs corrigés marqués ✅, bugs restants marqués ⬜.

### 🔴 Bugs critiques (runtime errors)

| # | Fichier | Bug | Statut |
|---|---------|-----|-------|
| **C1** | `enhance.py` | `_prepare_validation_pass()` référence `critique_prompt` (inexistant). | ✅ Corrigé → `user_content` |
| **C2** | `keywords.py` | `list_subsections()` : `FROM keywords` sans alias `k`. | ✅ Corrigé → `FROM keywords k` |
| **C3** | `keywords.py` | `check_keyword_duplicates()` : même problème d'alias. | ✅ Corrigé → `FROM keywords k` |

### 🟠 Bugs majeurs (sécurité / logique)

| # | Fichier | Bug | Statut |
|---|---------|-----|-------|
| **M1** | `enhance.py` | EP semantic search sans `_privacy_filter` → fuite privacy. | ✅ Corrigé |
| **M2** | `generate.py` | Semantic search sans `_privacy_filter` → fuite privacy. | ✅ Corrigé |
| **M3** | `enhance.py` | `keywords_llm_process()` : header d'auth manquant pour `/models`. | ✅ Corrigé |
| **M4** | `export.py` | Code orphelin (`result['output'] = corrected`) unreachable. | ✅ Corrigé (supprimé) |
| **M5** | `enhance.py` | `_prepare_enhance()` : 6 connexions DB sans `try/finally`. | ✅ Corrigé → 1 connexion + `try/finally` |
| **M6** | `enhance.py` | `repeat_penalty` + `frequency_penalty` envoyés simultanément. | ⬜ Restant (confirmé 28/06 — `_prepare_enhance` ligne ~690) |
| **N11** | `app.py` / `enhance.py` | **NOUVEAU** : `import logging` manquant dans `app.py` — `_load_ollama_config_at_startup()` `logging.warning()` cause `NameError`. | ⬜ Critique |

### 🟡 Bugs mineurs (code quality / performance)

| # | Fichier | Bug | Statut |
|---|---------|-----|-------|
| **L1** | `keywords.py` | `bulk_import_keywords()` : embedding par keyword (lent). | ⬜ Restant |
| **L2** | `keywords.py` | `scan_keyword_duplicates()` : O(n²). | ⬜ Restant |
| **L3** | `enhance.py` | `except ValueError: raise` trop large dans sessions. | ⬜ Restant |
| **L4** | `enhance.py` | Seed ignoré dans `/api/enhance`. | ⬜ Restant (confirmé 28/06 : seed jamais utilisé dans `_prepare_enhance()`) |
| **L5** | `app-core.js` | `filtersBar` déclaré mais inutilisé (ligne 17). | ⬜ Restant |
| **L6** | `app-filters.js` | `_loadApiKeySettings()` jamais appelé (code mort, distinct de `loadApiKeySettings()`). | ⬜ Restant |
| **L7** | `app-admin.js` | `document.execCommand('copy')` déprécié (3 occurrences : `copyEnhanceOutput`, `genCopy`, fallback `copyApiKey`). | ⬜ Restant |
| **L8** | `import_export.py` | Variables `delete_after`/`tmp` scope fragile dans `try/finally` (protégé par court-circuit `and`). | ⬜ Restant |
| **L9** | `keywords.py` | Check de doublon global incluant les keywords privés. | ⬜ Restant |
| **L10** | `AIH_ComfyUI/README.md` | Contradiction `AIH_Tools` vs `AIH_Keywords`. | ⬜ Restant |
| **L11** | `exporter.py` | Troncature `[:100]` dans le footer (coupe un titre en plein milieu). | ⬜ Restant |
| **N2** | `app-keywords.js` | **NOUVEAU** : `escapeHtml()` définie 2 fois (lignes ~760-761). | ⬜ Low |
| **N3** | `enhance_node.py` | **NOUVEAU** : `"seed": null` envoyé quand seed=0 (`seed if seed > 0 else None`). | ⬜ Medium |
| **N4** | `enhance.py` | **NOUVEAU** : `_call_llm_internal` ne vérifie pas la qualité du dernier retry (output < 50 chars passé silencieusement). | ⬜ Medium |
| **N5** | `enhance.py` | **NOUVEAU** : `_do_validation_pass` pas de retry sur output vide/tronqué. | ⬜ Medium |
| **N6** | `ideogram4_node.py` | **NOUVEAU** : `_build_caption_local_loop()` définie mais jamais appelée. | ⬜ Low |
| **N8** | `enhance.py` | **NOUVEAU** : `repeat_penalty` (Ollama-only) envoyé à toutes les APIs. | ⬜ Medium |
| **L11** | `exporter.py` | Troncature `[:100]` dans le footer. | ⬜ Restant |

### 📊 Résumé de l'audit

| Sévérité | Total init. | Corrigés | Restants (init) | Nouveaux 28/06 | Total restant |
|-----------|------------|----------|----------------|-----------------|---------------|
| 🔴 Critique | 3 | 3 ✅ | 0 | **1** (N1) | **1** |
| 🟠 Majeur | 6 | 5 ✅ | 1 (M6) | **0** | **1** |
| 🟡 Mineur | 11 | 0 | 11 | **5** (N2,N3,N4,N5,N8) | **16** |
| **Total** | **20** | **8** | **12** | **6** | **18** ( + 2 points d'attention ) |

---

## 🚀 Session (20/06/2026) — Bug fixes + Refonte Keywords Manager

### ✅ Bugs critiques corrigés
- **C1** : `critique_prompt` → `user_content` dans `_prepare_validation_pass()` (enhance.py)
- **C2** : `list_subsections()` — ajout alias `k` sur `FROM keywords` (keywords.py)
- **C3** : `check_keyword_duplicates()` — ajout alias `k` sur `FROM keywords` (keywords.py)

### ✅ Bugs majeurs corrigés
- **M1** : Ajout `_privacy_filter` dans la recherche sémantique des EP elements (enhance.py)
- **M2** : Ajout `_privacy_filter` dans la recherche sémantique du generate (generate.py)
- **M3** : Headers d'auth construits explicitement pour `/models` dans `keywords_llm_process()` (enhance.py)
- **M4** : Suppression du code orphelin en fin de `export.py`
- **M5** : `_prepare_enhance()` — 6 connexions DB → 1 seule avec `try/finally` (enhance.py)

### ✅ Refonte du Keywords Manager

#### Interface — panneau unique
- **Suppression du split gauche/droite** : un seul panneau pleine largeur
- **Tableau HTML** avec 8 colonnes : checkbox, keyword, description, section, sous-section, NSFW, statut, actions
- **Édition directe inline** : chaque cellule contient un input/select/checkbox toujours éditable (plus de dépliage)
- **Sticky header** : les titres de colonnes restent visibles pendant le scroll
- **Colonnes redimensionnables** : poignées de drag sur les `<th>`, largeurs sauvegardées dans `settings.kwManagerColumns`
- **Ctrl+Enter** pour sauvegarder une ligne depuis n'importe quel champ
- **Dirty tracking** : bordure ambre sur les lignes modifiées non sauvegardées

#### Multi-sélection + suppression multiple
- **Click / Ctrl+Click / Shift+Click / Ctrl+Shift+Click** — sélection classique
- **Click sur la ligne** (hors inputs/boutons) déclenche aussi la sélection
- **Boutons Tout / Aucun / Inverser** dans la toolbar
- **Suppression multiple** avec confirmation

#### Filtres enrichis
- **NSFW** : 2 checkboxes `[SFW] [NSFW]` (3 possibilités : tout, SFW, NSFW)
- **Privacy** : 4 checkboxes `[🌐 Public] [🔒 Privé] [🟡 En attente] [👤 Mes keywords]` combinables
- **Auteur** : dropdown listant les créateurs de keywords (`/api/keywords/authors`)
- **Section** : dropdown existant conservé
- **Recherche texte** : champ existant conservé
- Backend : nouveaux params `privacy` (comma-separated) + `mine` (1/0), backward compat avec `scope`

#### Section/Subsection — combobox avec auto-ID
- **Datalist** : suggestions des sections/sous-sections existantes + possibilité d'en créer de nouvelles en tapant
- **Auto-génération d'ID** : chiffres romains pour sections (I, II, III…), lettres pour sous-sections (I.A, I.B…)
- **Filtre contextuel** : le datalist des sous-sections se filtre selon la section sélectionnée
- Plus de champs ID manuels — tout est automatique

#### Modale de création
- Bouton **+ Add** ouvre une modale draggable avec : keyword, description, section/subsection (combobox), NSFW, privacy
- Bouton supprimer en mode édition
- Vérification de doublons intégrée

#### Modération pending intégrée
- **Filtre « ⏳ En attente »** : les keywords pending s'affichent dans la liste principale (plus de panneau séparé)
- **Boutons ✅ / ❌** sur chaque ligne pending (visible uniquement pour kw_editor/admin)
- **Édition + validation en une action** : le kw_editor peut corriger les champs puis cliquer ✅ → les modifications sont sauvegardées en même temps que la validation
- Backend : `review_keyword` accepte un champ `edits` (dict) pour mettre à jour le keyword avant de changer son `privacy_status`

#### Re-modération automatique
- **Règle** : si un utilisateur non-éditeur modifie un de ses keywords `public`, il est automatiquement remis en `public_pending`
- Les kw_editor/admin qui modifient gardent le statut `public` (ils sont modérateurs)

#### Backend — nouveaux endpoints & params
- `GET /api/keywords/authors` : liste les auteurs ayant créé au moins un keyword
- `GET /api/keywords` : nouveaux params `privacy` (comma-separated), `mine` (1/0), `author` (user_id), `scope=pending`
- `POST /api/keywords/<id>/review` : accepte `edits` (dict) pour édition lors de la validation
- `PUT /api/keywords/<id>` : re-modération auto (public → pending si non-éditeur)
- Migration BDD : colonne `created_at` ajoutée à la table `keywords`

### ⬜ Blobby — CORS sync serveur (à planifier)
- **Problème** : Blobby (dans ComfyUI) fait des requêtes vers `aih.holaf.fr/api/settings` pour synchroniser ses données. Le serveur répond **500** (erreur serveur) et Flask-CORS n'ajoute pas les headers CORS sur les réponses d'erreur → le navigateur bloque la réponse → erreurs CORS dans la console.
- **Impact** : utilisateurs en local via IP directe (ex: `http://192.168.x.x:8188`) ont les mêmes erreurs CORS. Les données locales (localStorage) fonctionnent quand même.
- **Ce qui marche** : animation canvas (local), commandes shell/git (local ComfyUI), chat LLM (si token valide — CORS open sur `/api/*` en 200, mais pas sur les 500).
- **Ce qui casse** : sync settings Blobby (`GET /api/settings` → 500 → CORS manquant). Chat LLM aussi si le token est expiré/invalide.
- **Note** : les erreurs `authentik.holaf.fr` dans la console ne viennent PAS de AI-Helper — c'est un reverse proxy d'auth configuré séparément sur le ComfyUI de l'utilisateur, pas lié à notre code.
- **Solutions possibles** :
  - [ ] Ajouter les headers CORS manuellement sur les réponses d'erreur (error handler Flask)
  - [ ] Ajouter un flag pour désactiver la sync serveur de Blobby
  - [ ] Détecter l'échec de sync et basculer en mode local-only silencieux
  - [ ] Le chat LLM pourrait passer par le serveur ComfyUI local (proxy) au lieu d'appeler directement le backend

### ⬜ Blobby — Système de mémoire vectorielle (à planifier)

> Objectif : Blobby retient les conversations passées et évolue au fil du temps.
> Recherche sémantique des souvenirs pertinents avant chaque envoi au LLM.
> L'utilisateur ressent une continuité et une évolution du personnage.

#### Architecture

```
User: "tu te souviens de mon projet de workflow flux?"
    │
    ├─► 1. Embedding du message utilisateur
    ├─► 2. Recherche sémantique dans les souvenirs
    │      (cosine_similarity vs embeddings stockés)
    ├─► 3. Injection dans le prompt :
    │      [SOUVENIRS PERTINENTS]
    │      • Il y a 3 jours : l'utilisateur travaille sur un workflow flux.8
    │      • Il y a 1 semaine : l'utilisateur a demandé de l'aide pour des upscalers
    │      [PERSONNALITÉ]
    │      • Blobby est devenu plus confiant, aime aider avec les workflows
    │      [FAITS SUR L'UTILISATEUR]
    │      • Utilise souvent KSampler
    │      • Préfère les workflows simples
    │
    └─► 4. Envoi au LLM + affichage réponse
         └─► 5. Sauvegarde du nouveau souvenir (arrière-plan, fire-and-forget)
              → POST /api/blobby/memory (texte + embedding + métadonnées)
```

#### 5 niveaux de mémoire

| Niveau | Description | Stockage | Limite |
|--------|-------------|---------|--------|
| **1. Épisodes** | Événements marquants avec date + importance (1-5) | BDD vectorielle | 50 max, purge par importance |
| **2. Faits** | Infos durables sur l'utilisateur (confirmés 3x) | BDD (JSON) | 30 max |
| **3. Personnalité** | Traits de caractère évolutifs + histoire | BDD (JSON) | Évolution max 5% par mise à jour |
| **4. Relation** | Familiarité, confiance, humeur | BDD (JSON) | Évolution max 10% par interaction |
| **5. Historique** | Derniers messages du chat | localStorage | 15 messages max |

#### Backend — nouveau

- **Table BDD** : `blobby_memories`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `user_id TEXT NOT NULL` (FK users)
  - `type TEXT` ('episode' | 'fact' | 'personality' | 'relationship')
  - `content TEXT NOT NULL` (texte du souvenir)
  - `embedding TEXT` (vecteur JSON, calculé via `generate_embedding()`)
  - `importance INTEGER DEFAULT 3` (1-5, pour épisodes)
  - `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
  - `last_accessed TIMESTAMP` (mise à jour à chaque récupération)
  - `access_count INTEGER DEFAULT 0` (plus c'est accédé, plus c'est important)

- **Endpoints** :
  - `POST /api/blobby/memory` — sauvegarde un souvenir (calcule l'embedding via `generate_embedding()`)
  - `GET /api/blobby/memory/search?q=...&limit=5` — recherche sémantique (cosine_similarity) + tri par pertinence/importance
  - `GET /api/blobby/memory/list` — liste tous les souvenirs (pour debug/affichage)
  - `DELETE /api/blobby/memory/<id>` — supprime un souvenir
  - `POST /api/blobby/memory/forget` — tout oublier (reset complet)
  - `POST /api/blobby/memory/update` — met à jour personnalité/relation (appelé en arrière-plan après chaque conversation)

- **Recherche** :
  - Calcul de l'embedding de la requête via `generate_embedding()`
  - Chargement de tous les embeddings en mémoire (quelques centaines max)
  - `cosine_similarity()` sur chaque souvenir
  - Tri par score * (importance / 5) → pertinence + importance
  - Top N (configurable selon `max_context` du modèle)

- **Fallback local** (si backend inaccessible) :
  - Stockage dans `localStorage.blobbyMemories` (JSON, sans embeddings)
  - Recherche par mots-clés (correspondance simple, pas de sémantique)
  - Basculement automatique : essaie le backend, si échec → local

#### Frontend (JS) — nouveau

- **Avant chaque message** :
  1. Appel `GET /api/blobby/memory/search?q=` + message utilisateur
  2. Construction du prompt : [SOUVENIRS] + [PERSONNALITÉ] + [FAITS] + [HISTORIQUE] + [MESSAGE]
  3. Envoi au LLM

- **Après chaque réponse** (fire-and-forget, pas d'attente) :
  1. Appel `POST /api/blobby/memory` avec le résumé de l'échange
  2. Tous les 5 messages : appel `POST /api/blobby/memory/update` pour mettre à jour personnalité + relation
  3. Si la réponse contient `⚠️ NOUVEL APPRENTISSAGE` : sauvegarde immédiate comme épisode

- **Gestion de la taille de contexte** :
  - Détectée via `max_context` (déjà implémenté, retourné par `/api/keywords/llm-process`)
  - Modèle 4k → 3 souvenirs + 10 messages d'historique
  - Modèle 32k+ → 10 souvenirs + 20 messages d'historique
  - Si total dépasse le contexte : réduire d'abord l'historique, puis les souvenirs

- **Évolution progressive de la personnalité** :
  - Mise à jour tous les 5 messages (pas après chaque message)
  - Le LLM reçoit l'ancienne personnalité + la conversation récente
  - Garde 80% de l'ancienne + max 20% de nouveau
  - Un seul message ne peut pas changer radicalement la personnalité
  - Si quelqu'un dit "parle comme un pirate" 1 fois → Blobby le fait pour rire, mais sa personnalité ne change pas. Si 100 fois → Blobby intègre lentement un trait "humour pirate"

- **Boutons chat** :
  - 🗑 Clear (existant) — efface l'historique du chat
  - 🧹 Oublier — réinitialise TOUS les souvenirs (épisodes, faits, personnalité, relation) avec confirmation
  - 📦 Compact (existant) — toggle mode compact

- **Persistance** :
  - Souvenirs → BDD backend (survie entre machines si même compte)
  - Historique récent → localStorage (local, rapide)
  - Skills → localStorage (existant, déjà implémenté)
  - Apparence/Caractère → localStorage + sync serveur (existant)

#### Dépendances

- **Embeddings** : utilise l'infrastructure existante (`embeddings.py` → Ollama ou Gemini)
- **Cosine similarity** : `embeddings.py` déjà implémenté
- **BDD** : SQLite existant, nouvelle table
- **LLM** : utilise le même preset que pour le chat (`/api/keywords/llm-process`)
- **Auth** : utilise le token Bearer existant

#### Questions ouvertes / points d'attention

- [ ] **Latence** : l'embedding + recherche ajoute ~200-500ms avant chaque message. Acceptable ?
- [ ] **Coût LLM** : la mise à jour mémoire (tous les 5 messages) fait un appel LLM supplémentaire. Négligeable ?
- [ ] **Privacy** : les souvenirs sont stockés sur le serveur. Faut-il un toggle "mémoire locale uniquement" ?
- [ ] **Partage de souvenirs** : si l'utilisateur utilise Blobby sur 2 machines (ComfyUI local + distant), les souvenirs sont partagés via le backend. C'est le comportement attendu ?
- [ ] **Purge automatique** : algorithme de purge quand on dépasse 50 épisodes : supprimer d'abord les moins importants ET les plus anciens non accédés récemment. Score de purge = `importance * access_count / age`.
- [ ] **Métadonnées des souvenirs** : doit-on stocker le type de souvenir (positif/négatif/neutre) pour que Blobby adapte son ton ?
- [ ] **Détection de "moments marquants"** : le LLM peut marquer certains échanges comme importants dans sa réponse (ex: `⚠️ MEMORABLE`), ce qui augmente l'importance de l'épisode.

---

## 🚀 Session en cours (19/06/2026)

### ✅ Blobby Companion — Intégration complète
- **Intégration** : personnage interactif 100% Canvas 2D qui vit sur le canvas ComfyUI.
- **Activation** : via menu AI-Helper > "Activer Blobby" avec badge ON/OFF + option "💬 Chat" visible seulement quand Blobby est actif.
- **Clic droit** plus utilisé — tout se fait depuis le menu AI-Helper.

### ✅ Chat Blobby
- **Modale de discussion** flottante, déplaçable, redimensionnable (`resize: both`), avec persistance position/taille.
- **Slider de transparence** dans l'en-tête (10-100%), persisté.
- **Historique** sauvegardé dans `localStorage.AIH_config.blobbyData.chatHistory` (50 derniers messages).
- **Barre de contexte** en bas : estime les tokens (`~XXX tokens | YYYY max`) avec code couleur selon le ratio.
- **Caractère Groot-like** : Blobby ne répond que "Blobby" avec variations expressives.
- **Humeur influencée** : le prompt LLM inclut l'humeur actuelle (happy/surprised/sleepy).
- **Provider LLM** configurable dans les paramètres (dropdown).
- **Sync serveur** vers backend AI-Helper avec debounce 2s + indicateur visuel ⬤ (vert/jaune/rouge).

### ✅ Paramètres Blobby (3 onglets)
- **Général** : choix du provider LLM + slider FPS animation (0-165).
- **Caractère** : textarea éditable pour la personnalité, reset au défaut.
- **Apparence** :
  - Particules (10-120)
  - Transparences : Corps (%), Cerveau (%)
  - Yeux : Hauteur, Écartement, Taille
  - Bouche : Hauteur, Taille
  - Cerveau : Taille
  - Couleurs par humeur : 4 color pickers (😊 Heureux, 😮 Surpris, 😴 Endormi, 😐 Neutre)
- Modale déplaçable et redimensionnable.

### ✅ Physique et rendu
- **Collision avec les nœuds** : les titres des nœuds sont inclus dans la bounding box (`getNodeBounds()` utilise `LiteGraph.NODE_TITLE_HEIGHT`).
- **Framerate-indépendant** : toutes les animations (position, amortissement, bouche, organes) utilisent `deltaTime * 60`.
- **Performance** : `setInterval(33ms)` + `setDirty(false, true)` — pas de conflit avec le rAF de ComfyUI.
- **Apparence** : toutes les propriétés visuelles sont configurables via localStorage.

### ✅ Filtres — Bug privacy_status
- **Problème** : `_rebuild_filter_cache()` utilisait `privacy_status = 'public'` en dur, ignorant les mots-clés privés (`'private'`) et en attente (`'public_pending'`).
- **Fix** : passage de `user_id` à `_rebuild_filter_cache()`, utilisation de `_privacy_filter(user_id)` pour les trois appels (POST, PUT, refresh).

### ✅ Bulk Import — Nouveautés
- **Onglets** : modale avec 2 onglets (Import / Génération IA).
- **Import** : checkbox "Convertir via LLM" + dropdown provider + niveau NSFW (SFW/Sexy/Érotique/Porno).
- **Génération IA** : textarea d'instructions + dropdown provider + niveau NSFW → résultat parsé en tableau éditable dans l'onglet Import.
- **LLM instruction stricte** : format `keyword | description | section | subsection | nsfw(0/1)` imposé, pas de texte hors-format.
- **Persistance choix** : provider et niveau NSFW sauvegardés dans `AIH_config`.

### ✅ Scan des doublons
- **Timeout 60s supprimé** : plus de limite arbitraire.
- **Bouton Annuler** : barre de progression avec bouton "✕ Annuler", annulation propre via `AbortController`.
- **Résultats scrollables** : modale dédiée large (700px) avec `overflow-y: auto`.

### ✅ API — Améliorations
- **`/api/keywords/llm-process`** : nouvel endpoint simple pour les appels LLM (bulk import / chat).
- **Retourne `max_context`** : détecté via API `/models` du provider, fallback par nom de modèle, défaut 4096.
- **`/api/settings`** : les données Blobby sont mergées avec les settings existants pour ne pas les écraser.

### ✅ Normalisation des données : `prompt_type` → `template_id` (migration radicale)
- **Problème** : `prompt_type` était utilisé à la fois comme catégorie (slug texte) et comme identifiant fonctionnel, ce qui créait de la confusion et des bugs de persistance (renommer un template cassait les workflows).
- **Solution** : suppression pure et simple du champ `prompt_type` partout. Chaque template est désormais identifié par son `id` entier unique.
- **BDD** :
  - `prompt_templates.prompt_type` → supprimé
  - `prompt_templates.name` → reste l'affichage humain
  - `generated_prompts.prompt_type` → `generated_prompts.template_id` (FK INTEGER)
  - `prompt_examples.type` → `prompt_examples.template_id` (FK INTEGER)
  - Contrainte `UNIQUE(user_id, prompt_type, output_format)` → supprimée (l'`id` est l'unique identifiant)
  - Migrations avec `DROP/CREATE/INSERT` pour les tables existantes
  - Le `output_format` est conservé dans la table (utilisé pour validation future)
- **Backend** :
  - `enhance.py` `_prepare_enhance()` reçoit `template_id` (INT), résout le template par `id` via `WHERE id = ? AND (is_public OR is_default OR user_id)`, lit `output_format` du template
  - Alias `prompt_type` conservé temporairement (cast en INT) pour rétrocompatibilité, à retirer après migration
  - Branche "Ideogram 4" dédiée supprimée du code, le format JSON est maintenant piloté par le `output_format` du template
  - Templates par défaut : 1 par type (`SDXL`, `Flux`, etc. pour `output_format='text'`), `Ideogram 4` pour `output_format='json'`
  - Fonctions `_default_format_for_type()` / `_DEFAULT_FORMAT_BY_TYPE` supprimées
  - Fonction `_migrate_templates_to_english()` supprimée (migration manuelle faite)
- **Frontend site web** :
  - Dropdown `enhance-type` utilise `t.id` comme valeur
  - `doEnhance()` envoie `template_id` au lieu de `prompt_type`
  - Stats membre (`/api/members/<id>`) utilisent le `name` du template via jointure
- **Nodes Python ComfyUI** :
  - `enhance_node.py`, `prep_node.py`, `ideogram4_node.py`, `ideogram_prep_node.py` : `prompt_type` STRING → `template_id` INT
  - Conversion défensive contre `''` (ComfyUI peut envoyer une string vide pour un INT)
- **Widgets JS ComfyUI** :
  - 4 widgets réécrits (`aih_enhance_widget.js`, `aih_prep_widget.js`, `aih_ideogram4_widget.js`, `aih_ideogram_prep_widget.js`)
  - Pattern commun : `Promise.all` pour attendre les listes, flag `_aihRestored` pour éviter la sync prématurée, callback natif appelé après `w.value = val` pour propager au graph
  - `ResizeObserver` retiré (causait un bug de grid collapse au release de la souris), remplacé par `gridTemplateColumns = "1fr 1fr"` forcé
  - Dropdown Template peuplé avec `item.id` (INT)
  - Payload envoie `template_id` (pas `prompt_type`)
- **Vérifications** : syntaxe Python + JS : OK

### ✅ Bug "Dropdown Template non synchronisé avec le widget natif" — RÉSOLU
- **Symptôme initial** : après sélection d'un template autre que SDXL, le backend recevait `prompt_type='sdxl'` (premier template de la liste). F5 = retour à SDXL.
- **Cause racine** : le widget natif STRING `prompt_type` n'était pas synchronisé avec le dropdown DOM, ou la valeur était écrasée.
- **Fix appliqué** :
  - Remplacement `prompt_type` STRING → `template_id` INT dans les nodes Python
  - Dropdown DOM peuplé avec `item.id`
  - `syncNativeWidgets()` appelle `widget.callback(val)` après `w.value = val` pour propager au graph ComfyUI
  - `restoreFromNativeWidgets()` lit le widget natif comme un INT (gère le cas 0)
  - Flag `_aihRestored` empêche la sync prématurée pendant le chargement
  - `Promise.all` pour attendre toutes les listes avant la première sync
  - Le widget natif est correctement sérialisé dans le workflow JSON
- **Bénéfice** : plus de bug de retour à SDXL, le template choisi est conservé après F5.

### ✅ Bug "Refactoring massif — découpage du code" — RÉSOLU
- **`frontend/index.html`** : 3917 → 757 lignes. CSS extrait → `css/app.css` (469 lignes). JS extrait → `js/app.js` (2691 lignes).
- **`frontend/js/app.js`** : 2691 lignes découpées en 4 modules : `app-core.js` (317), `app-keywords.js` (415), `app-filters.js` (1134), `app-admin.js` (826).
- **`backend/app.py`** : 3871 → 61 lignes. Routes découpées en 14 modules dans `backend/routes/`.
- **`backend/extensions.py`** : création app Flask + constantes partagées (22 lignes).
- **`backend/context.py`** : imports partagés pour les modules routes (43 lignes).
- **`frontend/beta.html`** supprimé — plus de synchro à maintenir.
- Bugs d'imports résolus : `_login_required` via `from context import *` + `__all__`, `helpers.py` imports manquants, `from routes.helpers import *` n'importe pas les underscore.

### ✅ Interface — onglets + relooking — RÉSOLU
- **Header renommé** : "FR-I.A Helper" avec 4 onglets : Prompt Helper, Style, Template, Keywords Manager.
- **Boutons filtres** (Charger/Save As/Gérer) : toolbar colorée (indigo/emerald/violet) avec badge, déplacée dans un encadré à droite sous la recherche sémantique.
- **Paramètres** allégée : Provider LLM + Compte (styles et templates déplacés vers onglets dédiés).

### ✅ Onglet Styles — interface deux colonnes — RÉSOLU
- **Colonne gauche** : liste des styles (nom, auteur, 🌐/🔒), clic = édition, boutons 📋 Cloner / 🗑 Supprimer.
- **Colonne droite** : édition plein écran, textareas ×3 hauteur + resize vertical + persistance hauteurs.
- **Bouton Export** ajouté (📥 format texte).
- **Bouton "+ Add Style"** ajouté en haut de la liste.
- Admin peut éditer/supprimer les styles sans propriétaire. Non-admin peut cloner les styles publics.
- **Modale de gestion des styles du prompt generator supprimée** : l'éditeur fullscreen dans l'onglet Style remplace la modale. Le bouton "Styles" du prompt generator et la modale associée ont été retirés.

### ✅ Onglet Templates — refonte complète — RÉSOLU
- **Colonne gauche** : liste normalisée comme les styles (nom, auteur, 🌐/🔒, 📋 Cloner, 🗑 Supprimer).
- **Colonne droite** : nom full width, Instructions + Exemples en deux sous-colonnes.
- **Le nom du template est son affichage humain** — `prompt_type` n'est plus utilisé comme identifiant (voir section de normalisation).
- **Dropdown `enhance-type` dynamique** : peuplé depuis les templates disponibles (utilise `id` comme valeur).
- **Format (text/md/json) + Public** descendus au-dessus du bouton Sauvegarder.
- **Bouton "+ Add Template"** ajouté en haut de la liste.
- **Admin** peut éditer/supprimer tous les templates. Propriété par utilisateur.
- **DB templates** : `name`, `is_public` ajoutés en migrations.
- **`prompt_type` supprimé** de la table (migration appliquée).

### ✅ Validation format LLM — ré-évaluée
- La roadmap originale prévoyait un auto-fix selon le format de sortie. Maintenant que le format est piloté par le `output_format` du template directement (pas de branche hardcodée par type), cette tâche est moins urgente.
- Le `output_format` est dans le payload de la réponse debug et peut être utilisé par les nodes ComfyUI (Ideogram Parse) pour validation.
- À reconsidérer : un endpoint de validation côté backend.

### ✅ Templates — dropdown dynamique dans les nodes ComfyUI — RÉSOLU
- **Dropdown Template** ajouté dans les 4 nodes ComfyUI (Enhancer, Prep, Ideogram Builder, Ideogram Prep).
- **Chargement lazy** : templates fetchés depuis `/api/prompts/templates` au `mousedown` (avec cache TTL 15s).
- **Widget natif** : `template_id` (INT) piloté par le dropdown DOM (caché).
- **Backend** :
  - Résolution template par `id` uniquement.
  - `template_id` (INT) requis, pas de fallback.
  - Erreur 400 explicite si template non trouvé ou inaccessible.
- **Resize** : `node.onResize` met à jour la largeur du container ; `gridTemplateColumns = "1fr 1fr"` forcé pour éviter l'effondrement. Pas de `ResizeObserver` (causait un bug de grid collapse).

### ✅ Sécurité — code review audit — RÉSOLU/PARTIEL
- **M8** : `is_admin()` retournait `True` on error (fail open) → corrigé en `return False` (fail secure).
- **M9** : `AbortSignal.timeout()` fallback pour navigateurs anciens (utilise `AbortController` + `setTimeout` si non disponible).
- **H3** : `_init_db()` était appelé à chaque connexion via `get_db()`. Maintenant appelé une seule fois au démarrage dans `app.py` (après les imports des routes, pour éviter les imports circulaires).

### ⚠️ Sécurité — non corrigé (changements majeurs)
- **H2** : Encryption key stockée dans la BDD. Migrer vers une variable d'environnement (casserait les clés existantes — migration des données nécessaire).
- **H4** : CORS wide open pour `/api/*`. Restreindre aux origines connues.
- **H5** : API key dans `localStorage`. Refactor architectural majeur.
- **H6** : `conn.close()` sans `finally` dans plusieurs routes. Refactor lourd.
- **M1** : Bbox detection fragile (heuristique `max(bbox) <= 1000`).
- **M4** : `_rebuild_filter_cache` charge tous les embeddings.
- **M6** : `_prepare_enhance` ouvre 2 connexions au lieu d'1.

---

## 🚀 État actuel (mi-2026)

**Contexte** : Serveur `kw.holaf.fr` (backend Flask + Discord OAuth). Le projet utilise **2 instances Ollama distinctes** + DeepSeek — voir section [Architecture Ollama](#-architecture-ollama--split-llm--embeddings) plus bas.

### ✅ Résolu cette session (normalisation `template_id`)

- **Suppression de `prompt_type`** dans 3 tables BDD (prompt_templates, generated_prompts, prompt_examples) avec migration `DROP/CREATE/INSERT` pour les données existantes.
- **Renommage `template_id` (INT)** dans 4 nodes Python ComfyUI avec conversion défensive contre string vide.
- **Réécriture de 4 widgets JS ComfyUI** : flag `_aihRestored`, callback natif, `Promise.all`, suppression de `ResizeObserver`, dropdowns peuplés avec `item.id`.
- **Backend `_prepare_enhance`** : résolution par `id`, suppression de `type_formats`/`format_instruction` mort, suppression de `_default_format_for_type`.
- **Templates par défaut** : insertion directe avec `name` (ex: "SDXL", "SDXL Markdown", "Ideogram 4") et `output_format` correspondant.
- **Aliases temporaires** : `prompt_type` (STRING) est encore accepté comme alias dans `_prepare_enhance` (cast en INT) pour rétro-compatibilité — à retirer après migration des nodes des utilisateurs.

### ✅ Résolu cette session (bugs template ComfyUI)

- **Bouton "Styles" du prompt generator et modale associée supprimés** : l'éditeur fullscreen dans l'onglet Style remplace la modale.
- **Bouton "+ Add Style" / "+ Add Template"** ajoutés en haut des listes.
- **Bug resize dropdown Template** : après release de la souris, le grid s'effondrait en 1 colonne. Fix : suppression du `ResizeObserver` buggé, forçage de `gridTemplateColumns = "1fr 1fr"`.
- **Bug string vide au RUN** : le widget natif INT recevait `''` et plantait avec `invalid literal for int()`. Fix : conversion défensive dans les 4 nodes Python + garantie que les selects ne sont jamais vides dans les widgets JS.

### ✅ Résolu cette session (audit backend)

- **`_init_db()` appelé une fois au démarrage** : retiré de `get_db()`, déplacé dans `app.py` après les imports des routes (pour éviter les imports circulaires).
- **`is_admin()` fail secure** : `return True` → `return False` on exception.
- **`AbortSignal.timeout()` fallback** dans `aih_menu.js` pour navigateurs anciens.
- **Debug button** : `document.write` + interpolation remplacés par `createElement` + `textContent` (prévention XSS).

### ✅ Résolu cette session (refactoring massif du code)

- **Découpage frontend** : `index.html` 3917 → 757 lignes. JS en 4 modules (`app-core.js`, `app-keywords.js`, `app-filters.js`, `app-admin.js`).
- **Découpage backend** : `app.py` 3871 → 61 lignes. Routes en 14 modules dans `backend/routes/`.
- **Bugs d'imports** : `_login_required` via `from context import *` + `__all__`, `helpers.py` imports manquants.

### ✅ Résolu (sessions précédentes)

- **`sqlite3.Row` n'a pas de `.get()`** — helper `_row_get()` + `safeJson()` côté frontend.
- **Fuite de connexion BDD** dans `discord_callback()`.
- **`_admin_required()` défini 2 fois** — la 2ème définition écrasait la 1ère.
- **FK constraint bloquait DELETE styles/presets** — `generated_prompts` référençait la ligne → NULL avant delete.
- **`JSON.parse: unexpected character`** — `await res.json()` sur du HTML d'erreur. Helper `safeJson()`.
- **Unreachable code** dans `deleteUser()` / `adminClearDb()`.
- **Décalage cache des filtres** (LIMIT 20, hidden_ids ignorés, etc.).
- **Mots masqués (👁️) non restaurés** au chargement d'un filtre.
- **Recherche sémantique dans `/api/enhance` silencieusement cassée** — fix SELECT avec embedding.
- **Filtre union → simple laisse `filter_type='union'` en BDD**.
- **Discord OAuth** : `SECRET_KEY` fixé, `DISCORD_REDIRECT_URI` aligné avec Discord Dev Portal.
- **Migration serveur cloud** : `getApiUrl()` lit `localStorage.AIH_config.serverUrl`.
- **Terminal AI-Helper** : panel flottant singleton via menu AI-Helper, WebSocket `/aih/terminal`, PTY serveur, xterm.js.
- **Qwen → Qwen-Image** : documentation mise à jour.

### ✅ Résolu cette session (audit code review 6 fichiers)

- **H1, M2** : widgets Ideogram utilisaient `t.prompt_type` (champ inexistant) — corrigé pour utiliser `t.id` (INT).
- **M7** : `loadTemplates` appelé à chaque `mousedown` sans cache — corrigé avec `refreshTemplatesIfStale` et `_cache.tmpl` TTL.
- **L3** : debug button `document.write` interpolation → `createElement` + `textContent`.

---

## 🧠 Elements Picker (panneau droit haut)

### Concept
Générer un prompt complet en combinant des **éléments**. Chaque élément est une source de mot-clé :
- **Filtre sauvegardé** → pioche un mot-clé au hasard dans le cache du filtre
- **Texte libre** → recherche sémantique → pioche un mot-clé au hasard

### Interface utilisateur
- [x] Liste d'éléments (lignes), chaque ligne = un élément
- [x] Au début : 1 ligne vide. Quand remplie → une nouvelle ligne vide apparaît
- [x] Chaque ligne : sélecteur de filtre OU champ de texte libre
- [x] Explorateur de filtres avec toggles : "Mes filtres" / "Filtres publics", "SFW" / "NSFW" / "Les deux"
- [x] Deux boutons : "Add saved filter" / "Add semantic filter"
- [x] Texte libre dans une modale (showPrompt) au lieu d'un prompt() natif
- [x] Persistance server-side des éléments sauvegardés dans `users.settings`
- [ ] Slider de confiance minimale pour la recherche sémantique (dans le générateur) — Le slider existe dans le panneau gauche ; prévu aussi dans le générateur
- [x] Bouton "Générer" → pioche aléatoirement dans chaque élément → combine → affiche le prompt
- [x] Zone d'affichage du prompt final + bouton "Copier"
- [x] Génération instantanée (pas d'animation)

### Filtres sauvegardés
- [x] Bouton "Charger" / "Save" / "Save As" dans la barre de filtres (panneau gauche)
- [x] Capture tous les paramètres : section, recherche texte, recherche sémantique, NSFW, slider confiance
- [x] Modale de création : nom, catégorie (texte libre), SFW/NSFW, public/privé
- [x] Bouton "Mng" → modale avec rename, delete, rebuild cache (↻)
- [x] Recharger un filtre dans la fenêtre gauche → charge ses paramètres dans les filtres
- [x] Cache correctement regénéré avec tous les filtres (bug "Anciens filtres piochent dans la liste globale" résolu)
- [ ] Bouton "Tout recharger" → regénère le cache pour tous les filtres
- [x] Pas de limite de filtres par utilisateur
- [x] Les filtres publics sont visibles par tous les membres connectés

### Cache
- [x] À la sauvegarde : exécute la requête → stocke les IDs des mots-clés correspondants dans `filter_cache`
- [x] Le générateur pioche dans le cache → pas d'appel Ollama à chaque génération
- [x] Invalidation du cache après un import de `.md`
- [x] Cache inclut : recherche (+), recherche (-), mots masqués (hidden_kw_ids), NSFW, section, confiance sémantique
- [x] Cache trié par score (branche sémantique) → limité à 500 comme l'API
- [x] hidden_ids appliqués APRÈS la limite
- [x] Texte (+) et exclusion (-) appliqués comme post-filtres

---

## ⚡ Prompt Generator/Enhancer (panneau droit bas)

### Concept
Transformer un prompt brut en un prompt optimisé via un LLM, en combinant plusieurs sources d'entrée.

### Sources d'entrée (fusionnées avant envoi au LLM)

| Priorité | Source | Déclencheur |
|---|---|---|
| **Haute** | Text box (utilisateur) | Saisie manuelle |
| **Moyenne** | Elements Picker (panneau haut) | Checkbox "Base from Elements Picker" |
| **Basse** | Random elements | Checkbox + sélecteur N |

### Configuration
- [x] **Dropdown Template** : peuplé dynamiquement depuis `/api/prompts/templates` (utilise `id` comme valeur, `name` comme affichage)
- [x] **Dropdown Style** : peuplé depuis `/api/styles`
- [x] **Dropdown Preset IA** : peuplé depuis `/api/presets`
- [x] **Checkbox "Use Elements Picker"** → exécute l'EP + affiche le résultat dans `gen-output`
- [x] **Checkbox "Add random"** + compteur N
- [x] **Système de priorités** : haute (textbox) > moyenne (EP) > basse (random)
- [x] **Few-shot examples** : lus depuis `examples` du template
- [x] **Instructions spéciales** : textarea dans le payload (UI à vérifier)
- [ ] **Checkbox Prompt négatif**

> **Note** : le dropdown "Format" (text/markdown/json) a été retiré du panneau enhance. Le format est désormais déterminé par l'`output_format` du template sélectionné. L'éditeur de templates garde le choix `output_format` pour surcharger par type.

### Actions
- [x] **Bouton Générer** : envoie au LLM → affiche le résultat
- [x] **Bouton Copier** : copie le prompt généré
- [x] **Reset** : bouton ↻ dans l'en-tête

### Prompt Examples (système auto-nourri) — RE-ÉVALUATION
- L'API `/api/prompts/examples` et `/vote` existent mais ne sont plus utilisées (les templates remplissent ce rôle).
- À nettoyer : code mort dans `enhance.py` (déjà fait dans cette session).
- Le système de votes peut être réintroduit plus tard si besoin (Phase 7).

---

## ⚙️ Presets IA (panneau de configuration utilisateur)

### Concept
Chaque utilisateur configure ses propres presets de modèles IA (API compatible OpenAI). Les presets sont indépendants par utilisateur.

### Interface
- [x] Panneau de configuration utilisateur
- [x] Dropdown "Moteur" : API compatible OpenAI
- [x] Champs : URL, API Key (chiffrée), Modèle, Nom
- [x] Actions : Sauvegarder / Dupliquer / Effacer
- [x] Possibilité de switcher entre plusieurs presets

### Sécurité
- [x] API key chiffrée côté serveur avant stockage (Fernet)
- [ ] Clé de chiffrement dans une variable d'environnement (H2 code review)
- [x] Migration : clé générée au premier lancement si absente

---

## 🎨 Styles (prédéfinis)

### Concept
Styles réutilisables ajoutés aux prompts avant envoi au LLM (ex: "Hyper realistic, 1970 vintage photography"). Fonctionnent comme les filtres sauvegardés : public/privé, avec auteur.

### Interface
- [x] Onglet "Style" fullscreen (2 colonnes : liste à gauche, édition à droite)
- [x] Dropdown "Style" dans l'enhancer pour sélectionner un style
- [x] Affichage du nom de l'auteur à côté du nom du style
- [x] Bouton "Add Style" en haut de la liste

---

## 🧩 Architecture Ollama (split LLM / Embeddings)

### Vue d'ensemble

```
AI-Helper-keywords backend (Flask, kw.holaf.fr)
    │
    ├── /api/enhance/prompts, /api/ideogram/prep, etc.
    │      │
    │      ├─► Ollama Cloud (abonnement) ──► LLM chat (gpt-oss, deepseek-v4, qwen3.5, ...)
    │      └─► DeepSeek API (abonnement) ──► backup LLM
    │
    └── /api/search/semantic, /api/embeddings/build
           │
           └─► Ollama CPU distant (dédié) ──► modèle embeddings (nomic-embed-text, ...)
```

### Variables d'environnement

| Var | Usage | Défaut |
|---|---|---|
| `OLLAMA_URL` | Endpoint Ollama pour les **embeddings** | `http://localhost:11434` |
| `OLLAMA_MODEL` | Modèle embeddings | `nomic-embed-text` |
| `OLLAMA_URL_LLM` | Endpoint Ollama Cloud pour les **LLM** | `https://ollama.com` |
| `OLLAMA_MODEL_LLM` | Modèle LLM par défaut | (configuré dans Presets) |
| `DEEPSEEK_API_KEY` | Clé DeepSeek (backup LLM) | vide |
| `DEEPSEEK_URL` | Endpoint DeepSeek | `https://api.deepseek.com/v1` |

> **Note** : Les URLs sont surchargeables dynamiquement via la table `app_settings` (colonnes `ollama_url`, `ollama_model`). Le split LLM/Embeddings n'est **pas encore implémenté** dans `app.py`. TODO : ajouter `OLLAMA_URL_LLM` distinct dans le panneau Presets.

---

## 🔧 Technique

### Tables BDD (état actuel, après normalisation `template_id`)

| Table | Colonnes clés |
|---|---|
| `ai_presets` | id, user_id, name, engine, base_url, api_key_encrypted, model, is_global, is_client_side |
| `styles` | id, user_id, name, style_text, negative_prompt, is_public |
| `prompt_templates` | **id**, user_id, **name**, **output_format**, system_prompt, examples, is_default, is_public |
| `prompt_examples` | id, **template_id** (FK), prompt_text, author_id, rating |
| `generated_prompts` | id, user_id, preset_id, **template_id** (FK), input_text, output_text, style_id, model_used |
| `filter_cache` | filter_id, keyword_id |
| `enhance_sessions` | id, user_id, state, payload_json, expires_at |

### Endpoints API

| Méthode | Route | Description |
|---|---|---|
| `GET/POST` | `/api/presets` | Lister / Créer un preset |
| `PUT/DELETE` | `/api/presets/<id>` | Modifier / Supprimer |
| `POST` | `/api/presets/<id>/duplicate` | Dupliquer un preset |
| `GET` | `/api/presets/<id>/models` | Lister les modèles du serveur |
| `GET/POST` | `/api/styles` | Lister / Créer un style |
| `PUT/DELETE` | `/api/styles/<id>` | Modifier / Supprimer |
| `GET/POST` | `/api/prompts/templates` | Lister / Créer un template |
| `PUT/DELETE` | `/api/prompts/templates/<id>` | Modifier / Supprimer |
| `GET` | `/api/prompts/templates/defaults` | Lister les templates par défaut |
| `GET` | `/api/enhance` | Générer/améliorer un prompt via LLM (streaming ndjson) |
| `POST` | `/api/enhance/prompts` | Variante découplée (3 strings prêtes pour un node LLM externe) |
| `POST` | `/api/ideogram/prep` | Préparer un appel LLM pour Ideogram 4 |
| `POST` | `/api/ideogram/parse` | Parser/valider la réponse LLM Ideogram 4 |

### Payload `/api/enhance`

```json
{
  "text": "...",
  "seed": 42,
  "template_id": 3,        // ID du template (INTEGER, requis)
  "preset_id": 1,           // ID du preset IA (optionnel, fallback = premier preset user/global)
  "style_id": 5,            // ID du style (optionnel)
  "style_text": "...",
  "special_instructions": "...",
  "ep_elements": [...],
  "random_count": 0,
  "width": 1024,            // pour Ideogram 4
  "height": 1024
}
```

### Initialisation BDD

`_init_db()` est appelé **une fois** au démarrage de l'app dans `app.py` (après les imports de routes, pour éviter les imports circulaires). Plus jamais dans `get_db()` — ça évitait de refaire les migrations et CREATE TABLE à chaque requête.

---

## 🎨 UI générale
- [ ] Drag & drop des mots-clés vers le générateur
- [ ] Double-clic sur un mot-clé → ajoute au générateur
- [x] Recherche sémantique (+) avec confiance (slider)
- [x] Recherche (+) dans tous les champs (keyword, description, section, subsection)
- [x] Recherche négative (-) pour exclure des mots-clés
- [x] Masquage local des mots-clés (👁️) avec compteur et "Réafficher"
- [x] Boutons reset par panneau (remise à zéro)
- [x] Panneaux distincts : coins arrondis, fonds differencies (3 couleurs), gaps transparents
- [ ] Code couleur par section (dans le tableau des mots-clés)
- [x] Spinner de chargement visible pendant les filtres
- [x] Barre de filtres sticky (reste visible en scroll)
- [ ] Compteur de tokens
- [x] Footer global avec stats
- [x] Modales uniformes : toutes draggables, pas d'alert() natif
- [x] Modales redimensionnables : poignées 8 directions (coins + bords) sur la modale settings

---

## 👥 Gestion des membres
- [x] Liste des membres avec avatar, nom, rôle
- [x] Clic sur un membre → détail avec :
  - [x] Grande photo (256px)
  - [x] Stats : nombre de filtres, nombre de prompts
  - [x] Favoris : type de prompt préféré, style préféré
  - [x] Historique : 15 derniers prompts (texte complet)
- [x] Endpoint API dédié : `GET /api/members/<user_id>`

### Phases complétées

#### Phase 1 — Fondations ✅
- [x] Toutes les tâches sont terminées

#### Phase 2 — Intégration Elements Picker ✅
- [x] Toutes les tâches sont terminées

#### Phase 3 — Templates personnalisables ✅ TERMINÉE
- [x] Table BDD `prompt_templates` avec `id`, `user_id`, `name`, `output_format`, `system_prompt`, `examples`, `is_default`, `is_public`
- [x] Templates par défaut (SDXL, SD1.5, Flux, Anima, Qwen-Image, Liste, Ideogram 4)
- [x] API CRUD `/api/prompts/templates`
- [x] Résolution dans `/api/enhance` par `template_id` (depuis la normalisation)
- [x] UI onglet "Templates" 2 colonnes (instructions + exemples)
- [x] Édition du prompt système (textarea)
- [x] Sauvegarde en template personnel
- [x] Reset vers le template par défaut
- [x] Export du template en JSON (📥 Exporter)
- [x] Paramètres enhance persistés : template_id, preset_id, style_id
- [x] Bouton "+ Add Template" en haut de la liste

#### Phase 4 — Polish & Bonus (en cours)
- [x] **Suppression de `prompt_type` (juin 2026)** — voir section de normalisation
- [x] **Ideogram 4 : backend support** (juin 2026) — Branche dédiée dans `_prepare_enhance` qui formate l'entrée en sections nommées (GENERAL DESCRIPTION + ELEMENTS TO PLACE + IMAGE DIMENSIONS + STYLE) au lieu du format avec priorités. Champs `width` et `height`. Bbox obligatoire dans le template.
- [x] **Ideogram 4 : nodes ComfyUI** (juin 2026) — `AIHIdeogram4Node` (builder avec bouton Generate + preview) et `AIHIdeogramPrepNode` (découplé, 3 strings).
- [x] **Ideogram 4 : node preview** — `AIHIdeogram4Node` rend un preview visuel des bboxes (PIL + canvas).
- [ ] Checkbox Prompt négatif
- [ ] Base de prompts négatifs
- [x] Instructions spéciales dans le prompt système (backend OK, UI à vérifier)
- [ ] Export du résultat
- [ ] Drag & drop des mots-clés vers le générateur (UI générale)

#### Phase 5 — Audit (juin 2026)
Code review complet, classé par priorité :

**🟠 Majeurs (en cours de traitement) :**
- [x] **Seed ComfyUI ignoré dans `/api/enhance`** — `random.choice()` et `ORDER BY RANDOM()` ignorent le seed envoyé. Cohérence à faire avec `/api/generate` qui utilise `rng = random.Random(seed)`. *À traiter*.
- [ ] **`loadColWidths()` redimensionne les colonnes cachées** — applique la largeur à TOUS les `<th>`, y compris `score-header` qui est `hidden` en mode texte. *À corriger*.
- [x] **`user_id` inutilisé dans 4 endpoints** — `list_keywords()`, `list_sections()`, `stats()`, `presets()`. Pas de bug mais fausse l'intent. *À nettoyer*.

**🟡 Mineurs (nettoyage) :**
- [ ] **Code mort : `_loadApiKeySettings()` jamais appelé** — Garder `loadApiKeySettings()` (appelé) et supprimer la version `_loadApiKeySettings()` (jamais appelée). *À faire*.
- [x] **Code mort dans `enhance_prompt`** — `format_instruction`, query `prompt_examples` supprimés. *Fait*.
- [ ] **Bug n°11 : variables `cur`/`cur2`/`conn`/`conn2` multiples dans `enhance_prompt`** — Renommer en `_db_ep`, `_db_rand`, `_db_template`. *À faire*.
- [ ] **`var filtersBar` déclaré mais plus utilisé** dans `app-core.js`. *À supprimer.* (Code toujours présent ligne 17)
- [ ] **Troncature inesthétique dans `exporter.py`** — Le `[:100]` coupe la liste concaténée des catégories au milieu d'un titre. *À corriger*.
- [ ] **README ComfyUI : nom de dossier contradictoire** — `git clone ... AIH_Tools` mais l'avertissement dit `AIH_Keywords`. *À corriger*.

**⚠️ Points d'attention :**
- [x] **Vérifier persistance des paramètres enhance** — L'API `/api/settings` existe, le frontend appelle PUT après chaque changement.
- [x] **UI `special_instructions`** — Backend OK, UI existe dans le panneau enhance.
- [x] **Système `prompt_examples` (votes)** — Code mort nettoyé.
- [x] **Page `/settings` ComfyUI** — C'est un onglet "Compte" dans la modale Paramètres.

**🔴 Hauts (à planifier) :**
- [ ] **H2** : Encryption key en BDD. Migrer vers env var.
- [ ] **H4** : CORS wide open. Restreindre.
- [ ] **H5** : API key dans `localStorage`. Refactor.
- [ ] **H6** : `conn.close()` sans `finally`.

#### Phase 6 — Frontend (à planifier)
- [ ] Drag & drop des mots-clés vers le générateur
- [ ] Double-clic sur un mot-clé → ajoute au générateur
- [ ] Code couleur par section
- [ ] Compteur de tokens
- [ ] Bouton "Tout recharger" → regénère le cache pour tous les filtres
- [ ] Slider de confiance minimale dans le générateur (panneau droit)

#### Phase 7 — Community & Management

##### Feature Requests
- [ ] Page `/features` listant les demandes de fonctionnalités des utilisateurs
- [ ] Système de vote (👍/👎)
- [ ] Statuts : "proposée", "en cours", "faite", "refusée"
- [ ] Filtrer par statut, tri par votes/popularité
- [ ] Suggestion automatique : dédoublonnage
- [ ] Notification quand une feature change de statut

##### User Prompts
- [ ] Page `/prompts` : bibliothèque de prompts partagés
- [ ] Formulaire d'ajout : titre, prompt, type, tags
- [ ] Édition par l'auteur
- [ ] Recherche de doublons (LLM semantic similarity)
- [ ] Système de rating
- [ ] Filtres par type/note/tag/auteur
- [ ] Modération : signaler, cacher, supprimer
- [ ] Export vers le générateur

---

## 🐛 Bugs identifiés

### Backend — enhance.py

- [x] **CRITIQUE : 500 sur GET /api/presets et GET /api/styles** — Résolu. Cause : `sqlite3.Row` n'a pas de méthode `.get()`. Fix : helper `_row_get()`.
- [x] **CRITIQUE : GET /api/filters plante pour les filtres union** — Résolu. Fix : `conn.close()` déplacé après la boucle for.
- [x] **CRITIQUE : Recherche sémantique dans `/api/enhance` silencieusement cassée** — Résolu. Fix : `SELECT k.keyword, ke.embedding` + `r['embedding']`.
- [x] **CRITIQUE : Filtre union → simple laisse `filter_type='union'` en BDD** — Résolu. Fix : ajouter un `else` pour repasser en `'simple'`.
- [x] **Bug : URLs LLM locales invalides pour les utilisateurs distants** — Résolu avec l'option "Client-side".
- [x] **Bug : Mauvaise URL pour le endpoint members** — Corrigé.
- [x] **Bug : PUT /api/filters/<id> plante (KeyError)** — Corrigé.
- [x] **Bug : Fuite de connexion BDD dans `discord_callback()`** — Corrigé.
- [x] **Bug : Message d'erreur obsolète** — "Token HF" → "Serveur Ollama inaccessible".
- [x] **Commentaire obsolète** — "embeddings HF" → "embeddings Ollama".
- [x] **`_admin_required()` défini 2 fois** — Suppression de la 2ème définition.
- [x] **FK constraint bloquait DELETE styles/presets** — `NULL` des références avant suppression.
- [x] **Variable inutilisée** — `stats()`, `sections()`, `list_keywords()`, `presets()` calculent `user_id` sans l'utiliser.
- [x] **Colonne `config` pas mise à jour au Save** — Résolu.
- [x] **Preview `total` plafonné à 20** — Résolu (COUNT(*) séparé du LIMIT).
- [x] **Cache sémantique ignorait section/nsfw/hidden_ids/search_neg** — Résolu.
- [ ] **Seed ComfyUI ignoré dans `/api/enhance`** — `random.choice()` et `ORDER BY RANDOM()` ignorent le seed.
- [x] **Code mort dans `enhance_prompt`** — `format_instruction` et query `prompt_examples` supprimés.
- [x] **`prompt_type` STRING reçu comme `''` au RUN** — Conversion défensive dans les 4 nodes Python + dropdowns JS jamais vides.
- [x] **Widget natif `prompt_type` non synchronisé avec dropdown DOM** — Remplacé par `template_id` (INT) avec callback + flag `_aihRestored` + `Promise.all`.
- [x] **Dropdown Template revient à SDXL après F5** — Résolu par `template_id` INT + callback + restoration.
- [x] **`_init_db()` appelé à chaque connexion** — Déplacé dans `app.py`, appelé une fois au démarrage.
- [x] **Import circulaire `_init_db` dans `extensions.py`** — Déplacé dans `app.py` après les imports de routes.
- [x] **`is_admin()` fail open** — `return False` on exception (fail secure).
- [x] **F2 _do_enhance thread** — `setConfig(url=, model=)` au lieu de (url=, model=).

### Backend — parser.py
- [x] **Parser ignorait les chiffres romains avec L, C, D** — Fix : `[IVXLCDM]+`.

### Backend — auth.py
- [x] **Imports inutilisés** — Supprimés.

### Backend — exporter.py
- [x] **Export sans `ORDER BY` cohérent** — Résolu (c'était juste une note de roadmap obsolète).
- [ ] **Troncature inesthétique du footer** — `[:100]` coupe la liste concaténée.

### Frontend — index.html
- [ ] **Potentiel : `filtersBar` déclaré mais plus utilisé** — Toujours présent dans `app-core.js:17`. *À supprimer.* (Roadmap disait [x] mais code toujours là)
- [ ] **Score header visible en mode texte** — `scoreHeader` est initialisé comme `hidden` mais affecté par `loadColWidths` qui applique des largeurs à tous les `<th>`.
- [x] **Unreachable code dans deleteUser/adminClearDb** — Code après `return;` déplacé dans le callback.
- [x] **`delStyle()` silencieux** — Ajout d'affichage d'erreur.
- [x] **Confidence slider ne refetchait pas l'API** — Maintenant invalide le cache.
- [x] **Recherche texte (+) et exclusion (-) ignorées avec sémantique** — Post-filtres.
- [x] **hiddenKWs non restaurés au chargement d'un filtre** — Restaurés.
- [x] **Label "X résultats (Y masqués)" ambigu** — Changé.
- [ ] **Code mort : `_loadApiKeySettings()` jamais appelé** — À supprimer.

### Frontend — ComfyUI widgets
- [x] **Bug dropdown Template** — `template_id` INT, callback, `Promise.all`, flag `_aihRestored`.
- [x] **Grid collapse au release de la souris** — `ResizeObserver` retiré, `gridTemplateColumns = "1fr 1fr"` forcé.
- [x] **Templates Ideogram 4 utilisaient `t.prompt_type`** — Remplacé par `t.id`.
- [x] **Cache TTL pour templates** — `refreshTemplatesIfStale` ajouté.
- [x] **Debug button XSS** — `textContent` au lieu de `document.write` interpolation.
- [ ] **Node Prompt Enhancer — fil branché sur `special_instructions` non pris en compte** — Quand on branche un fil (avec un texte) sur l'entrée `special_instructions` d'`AIHEnhanceNode`, la node exécute avec le texte écrit dans le widget, pas celui branché via le fil. Bug connu de la frontend Vue ComfyUI (issue #5692, modèle "Widget Input Socket" RFC #9) : les widgets et les input slots coexistent, et la valeur du widget prime sur celle du fil. Réf : https://github.com/Comfy-Org/ComfyUI_frontend/issues/5692 (fixé dans PR #5834). À vérifier si une future version de ComfyUI_frontend résout le problème, ou trouver un workaround côté node.
- [x] **Elements Picker — cause racine de la perte de persistance `_elements_json` (frontend Vue 1.47.11)** — RÉSOLU (fix `990b698`). La frontend ComfyUI Vue (v1.47.11) a `migrateWidgetsValues()` (`src/utils/litegraphUtil.ts` L189-211, appelée dans `src/services/litegraphService.ts` L588) qui migre les vieux workflows pré-v1.16 en retirant **positionnellement** les valeurs correspondant aux inputs `forceInput` (widgets factices sérialisés). Elle flatMap les inputs en `[forceInput?]` (double entrée si `control_after_generate`), compare la longueur avec `widgets_values.length`, et si c'est **ÉGAL**, retire les valeurs aux index forceInput (garde-fou). Pour `AIHElementsNode` : inputs = elements_input, seed (sans `control_after_generate` dans la spec), `_elements_json`, llm_config (`forceInput`) → flatMap = `[false, false, false, true]` = 4 entrées = 4 valeurs → la migration s'exécute → retire l'index 3 (le JSON de `_elements_json`) en croyant que c'est un dummy de llm_config → éléments perdus. L'Enhancer n'avait PAS ce bug : aucun `forceInput` dans sa définition → flatMap ne contient que des `false` → la migration est un no-op. **Fix** : déclarer `control_after_generate: "randomize"` explicitement dans la spec de `seed` (`elements_node.py`) → le flatMap passe à 5 entrées (seed compte pour 2) ≠ 4 → early return → `_elements_json` n'est plus retiré. **À retenir** : tout node avec des entrées `forceInput` + widgets sérialisés est susceptible de déclencher ce bug si le compte positionnel tombe juste — ajuster le nombre d'entrées du flatMap (ex: déclarer `control_after_generate`) pour casser le garde-fou de longueur.

---

## 🧩 AI-Helper — Extension ComfyUI

### Concept

Extension légère qui ajoute un bouton `[AI-Helper]` dans la barre de menu de ComfyUI.

### Menu `[AI-Helper ▾]`

| Option | Action |
|--------|--------|
| **Open Webpage** | Ouvre `https://kw.holaf.fr` dans un nouvel onglet |
| **Paramètres** | Ouvre la modale de configuration |

### Modale Paramètres

- **URL du serveur** : défaut `https://kw.holaf.fr`, configuré via menu AI-Helper → Compte
- **Clé API** : token Bearer généré depuis `/api/auth/token`

### Architecture

| Composant | Rôle | Statut |
|-----------|------|--------|
| **Menu `[AI-Helper ▾]`** | Point d'entrée global | ✅ |
| **Node Elements Picker** | Composer des éléments (filtres, sémantique, random) | ✅ |
| **Node Prompt Enhancer** | Optimise via LLM cloud | ✅ |
| **Node Prompt Prep** | Prépare 3 strings pour un LLM externe | ✅ |
| **Node Ideogram 4 Builder** | Génère un JSON Ideogram 4 | ✅ |
| **Node Ideogram Prep** | Prépare 3 strings pour Ideogram 4 | ✅ |
| **Node Ideogram Parse** | Parse la réponse LLM Ideogram 4 | ✅ |
| **Node Diagnostic** | Debug DOM widget | ✅ |
| **Panel Terminal** | Accès shell via WebSocket | ✅ |

### Nœuds ComfyUI — pattern commun (post-normalisation `template_id`)

Tous les nœuds sauf Diagnostic et Ideogram Parse utilisent le pattern suivant :
- **Widget natif `template_id`** (INT) caché, piloté par le DOM.
- **Widget natif `style_id`** (INT) caché, piloté par le DOM.
- **Widget natif `preset_id`** (INT) caché, piloté par le DOM (sauf Prep et Ideogram Prep qui n'ont pas de preset).
- **Widgets JS** : dropdowns peuplés depuis `/api/prompts/templates`, `/api/styles`, `/api/presets`.
- **Sync** : `widget.value = val` puis `widget.callback(val)` pour propager au graph.
- **Restore** : flag `_aihRestored` + `Promise.all` pour les chargements.
- **Resize** : `node.onResize` met à jour la largeur du container, `gridTemplateColumns = "1fr 1fr"` forcé.
- **Conversion défensive côté Python** : `int(val) if val != "" else 0` pour les INT.

### Fichiers de l'extension

```
AI-Helper-ComfyUI/
├── __init__.py
├── nodes/
│   ├── __init__.py
│   ├── _credentials.py          # lecture aih_credentials.json
│   ├── elements_node.py         # Elements Picker
│   ├── enhance_node.py          # Prompt Enhancer (cloud)
│   ├── prep_node.py             # Prompt Prep (decoupled)
│   ├── ideogram4_node.py        # Ideogram 4 Builder
│   ├── ideogram_prep_node.py    # Ideogram Prep (decoupled)
│   ├── ideogram_parse_node.py   # Ideogram Parse + preview bboxes
│   ├── diagnostic_node.py       # Diagnostic
│   ├── terminal.py              # PTY serveur pour /aih/terminal
│   └── update_manager.py        # Mise à jour du repo
├── web/
│   └── js/
│       ├── aih_menu.js
│       ├── aih_elements_widget.js
│       ├── aih_enhance_widget.js
│       ├── aih_prep_widget.js
│       ├── aih_ideogram4_widget.js
│       ├── aih_ideogram_prep_widget.js
│       ├── aih_terminal_widget.js
│       ├── xterm.js              # bundle UMD
│       └── xterm-addon-fit.js    # bundle UMD
├── pyproject.toml
├── README.md
└── requirements.txt
```

### Roadmap d'implémentation

| # | Étape | Statut |
|---|-------|--------|
| 1-4 | Site web : auth token, BDD, UI | ✅ |
| 5-6 | Menu + Widget Elements Picker | ✅ |
| 7 | Node Elements Picker | ✅ |
| 8 | Node Prompt Enhancer | ✅ |
| 8b | Nodes Prompt Prep + Ideogram 4 Builder + Ideogram Prep + Parse | ✅ |
| 9 | Tests + Déploiement | ✅ (déployé, utilisateurs testent) |
| 10 | Publication registry | ⬜ |

### Notes de packaging

- Le nom du dossier doit être **`AIH_Tools`** (le nom du repo GitHub) pour que Python importe `AIH_ComfyUI` correctement. Le README doit refléter cette consigne (corriger l'avertissement obsolète "AIH_Keywords").

### Site web — Onglet "Compte" dans la modale Paramètres

- Endpoint `/api/auth/token` (GET/POST) pour récupérer/régénérer la clé API.
- Colonne `users.api_token` ajoutée en migration.
- Middleware `_authenticate_via_token()` lit le header `Authorization: Bearer <token>`.

### Checklist migration serveur cloud (référence)

1. Copier `.env` complet (CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SECRET_KEY fixe, GUILD_ID optionnel)
2. Vérifier alignement exact `DISCORD_REDIRECT_URI` ↔ Redirects sur Discord Dev Portal
3. Si reverse-proxy HTTPS : ajouter `ProxyFix` à `extensions.py`
4. Pull du repo + restart extension ComfyUI → `web/js/*` rechargés
5. Côté user : menu **AI-Helper → Compte** → mettre la nouvelle `URL du serveur` + clé API + vider cache navigateur
6. Tester login Discord + génération d'un node Ideogram4

---

## 📝 Notes de session

- Les commits se font par l'utilisateur, pas l'assistant.
- Convention : un commit = un changement logique.
- Le serveur de prod est sur `/projects/AIH_Tools` (cloud), pas `AI-Helper-keywords/`.
- Le frontend web est servi par Flask depuis `frontend/`.
- Le frontend ComfyUI est servi par ComfyUI depuis `web/js/` (extension `AIH_ComfyUI`).
