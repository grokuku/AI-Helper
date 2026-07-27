"""
AIH Keywords — Node ComfyUI pour construire des filtres de mots-clés.

IN (caché, géré par le widget JS) :
  _keywords_config : STRING (JSON) contenant la config du filtre + les keywords

OUT :
  keywords_list  : STRING — liste des mots-clés séparés par des virgules

Le widget JS (aih_keywords_widget.js) s'occupe de :
- Charger les sections/sous-sections depuis l'API
- Faire les appels API pour récupérer les keywords filtrés
- Mettre à jour le widget caché _keywords_config
"""

import json


class AIHKeywordsNode:
    CATEGORY = "AIH"
    FUNCTION = "process"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                # JSON mis à jour par le widget JS via les appels API
                # Masqué dans l'UI ComfyUI
                "_keywords_config": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("keywords_list",)

    def process(self, _keywords_config="{}"):
        """
        _keywords_config est un JSON string mis à jour par le widget JS.
        Format attendu :
        {
          "keywords_text": "mot1, mot2, mot3",
          "keywords": [{"id": 1, "keyword": "mot1", ...}, ...],
          "total": 3,
          "config": {...}
        }
        """
        try:
            data = json.loads(_keywords_config) if isinstance(_keywords_config, str) else _keywords_config
        except (json.JSONDecodeError, TypeError):
            data = {}

        keywords_list = data.get("keywords_text", "")

        return (keywords_list,)
