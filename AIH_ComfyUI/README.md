# AIH — ComfyUI Extension

Extension ComfyUI pour le générateur de prompts AIH.

## Installation

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/grokuku/AIH_Tools.git AIH_Tools
pip install -r AIH_Tools/AIH_ComfyUI/requirements.txt
```

> ⚠️ Le nom du dossier doit être **`AIH_Keywords`** (pas de points ni tirets) pour que Python puisse l'importer correctement.

Redémarrer ComfyUI.

## Composants

### Menu `[AIH ▾]`

Bouton dans la barre de menu ComfyUI :
- **🌐 Open Webpage** — ouvre le site AIH dans un nouvel onglet
- **⚙️ Paramètres** — modale pour configurer l'URL du serveur et la clé API

### Node `AIH Elements Picker`

Interface interactive pour composer des éléments :
- Ajout de filtres sauvegardés
- Recherche sémantique
- Add random avec compteur
- Génération et prévisualisation

### Node `AIH Prompt Enhancer`

Optimise un prompt via LLM avec paramètres de génération :
- Connexion depuis Elements Picker
- Type de prompt (SDXL, Flux, etc.)
- Format de sortie (texte, markdown, json)
- Preset IA et Style
- Instructions spéciales

## Configuration

1. Aller sur le site AIH → Settings
2. Copier la clé API
3. Dans ComfyUI → AIH → Paramètres
4. Coller la clé API

## Dépendances

- `requests`
