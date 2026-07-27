"""
AIH Ideogram Prep Node — Prépare les prompts (system + user) pour Ideogram 4.

Au lieu d'appeler un LLM en interne, ce node fait un seul appel léger au
backend AIH (`/api/ideogram/prep`) qui retourne les chaînes prêtes pour
la passe 1 :

  - llm_prompt     : le prompt utilisateur (GENERAL DESCRIPTION, ELEMENTS TO
                     PLACE, IMAGE DIMENSIONS, STYLE, ADDITIONAL INSTRUCTIONS)
  - system_prompt  : le prompt système (règles JSON Ideogram 4 strictes)
  - context        : JSON bundle {original_input, width, height, style_text,
                     model} pour la passe 2 (construit par /api/ideogram/parse)

L'api_key et l'URL du serveur sont lues depuis le fichier de credentials
ComfyUI (ComfyUI/user/default/aih_credentials.json) via le helper _credentials.

Entrées :
  - seed (INT)
  - description (STRING multiligne)
  - element_1..4 (STRING) — widgets natifs
  - special_instructions (STRING)
  - style_id (INT, widget natif piloté par le DOM widget)

Sorties :
  - llm_prompt (STRING)
  - system_prompt (STRING)
  - context (STRING, JSON sérialisé)
"""

import json
import logging

from . import _credentials


class AIHIdeogramPrepNode:
    CATEGORY = "AIH"
    FUNCTION = "prepare"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "width": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
                "height": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
                "description": ("STRING", {"multiline": True, "default": ""}),
                "element_1": ("STRING", {"default": ""}),
                "element_2": ("STRING", {"default": ""}),
                "element_3": ("STRING", {"default": ""}),
                "element_4": ("STRING", {"default": ""}),
                "special_instructions": ("STRING", {"default": ""}),
                "style_id": ("INT", {"default": 0, "min": 0}),
                "template_id": ("INT", {"default": 0, "min": 0}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("llm_prompt", "system_prompt", "context")

    def prepare(self, seed=0, width=1024, height=1024, description="",
                element_1="", element_2="", element_3="", element_4="",
                special_instructions="", style_id=0, template_id=0):
        import logging as _log
        # Defensive : ComfyUI peut envoyer une string vide pour un INT
        try:
            template_id = int(template_id) if template_id != "" else 0
        except (ValueError, TypeError):
            template_id = 0
        try:
            style_id = int(style_id) if style_id != "" else 0
        except (ValueError, TypeError):
            style_id = 0
        _log.warning(f"[AIH Ideogram Prep PYTHON] template_id={template_id} style_id={style_id}")
        # api_key et api_url lus depuis le fichier de credentials
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()

        # Construire ep_elements (format Ideogram 4 : liste de {type, text})
        ep_elements = []
        for el in [element_1, element_2, element_3, element_4]:
            if el and el.strip():
                ep_elements.append({"type": "text", "text": el.strip()})

        # Construire le payload pour /api/ideogram/prep
        payload = {
            "text": description.strip(),
            "seed": seed if seed > 0 else None,
            "template_id": template_id if template_id > 0 else None,
            "width": width,
            "height": height,
            "ep_elements": ep_elements,
            "style_id": style_id if style_id > 0 else None,
            "special_instructions": special_instructions,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            import requests
            r = requests.post(f"{api_url}/ideogram/prep",
                              json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()

            llm_prompt = data.get("llm_prompt", "")
            system_prompt = data.get("system_prompt", "")
            context = data.get("context", "{}")

            logging.warning(
                f"[AIH Ideogram Prep] style_id={style_id} model='{data.get('model', '?')}' "
                f"sys_len={len(system_prompt)} user_len={len(llm_prompt)}"
            )

            return {
                "ui": {
                    "llm_prompt": [llm_prompt],
                    "system_prompt": [system_prompt],
                },
                "result": (llm_prompt, system_prompt, context),
            }
        except ImportError:
            msg = "Erreur: module 'requests' manquant. pip install requests"
            return {
                "ui": {"llm_prompt": [msg], "system_prompt": [""], "context": ["{}"]},
                "result": (msg, "", "{}"),
            }
        except Exception as e:
            err = str(e)
            if "401" in err:
                msg = "Erreur : clé API invalide ou manquante. Configurez-la dans le menu AIH."
            elif "404" in err:
                msg = f"Erreur : endpoint /ideogram/prep introuvable sur {api_url}. Backend AIH pas à jour ?"
            else:
                msg = f"Erreur API : {err}"
            logging.warning(f"[AIH Ideogram Prep] {msg}")
            return {
                "ui": {"llm_prompt": [msg], "system_prompt": [""], "context": ["{}"]},
                "result": (msg, "", "{}"),
            }
