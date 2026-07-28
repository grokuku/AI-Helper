"""
AIH Prompt Prep Node — Prépare les prompts (system + user) pour n'importe quel LLM.

Au lieu d'appeler un LLM en interne, ce node fait un seul appel léger au
backend AIH (`/api/enhance/prompts`) qui retourne les chaînes prêtes :

  - llm_prompt     : le prompt utilisateur fusionné (à brancher sur l'entrée
                     `prompt` du node LLM)
  - system_prompt  : le prompt système complet (à brancher sur
                     `system_prompt` du node LLM)
  - neg_prompt     : le negative prompt du style (à brancher sur KSampler
                     CLIP Text Encode négatif)

L'api_key et l'URL du serveur sont lues depuis le fichier de credentials
ComfyUI (ComfyUI/user/default/aih_credentials.json), pas depuis un widget
STRING (évite les fuites dans les workflows exportés et les bugs d'index
de widgets).

Entrées :
  - seed (INT)
  - base_prompt (STRING multiligne)
  - template_id (INT)
  - style_id (INT, widget natif ou piloté par le DOM widget)
  - special_instructions (STRING)
  - elements (STRING, optionnel) — JSON du Elements Picker

Sorties :
  - llm_prompt (STRING)
  - system_prompt (STRING)
  - neg_prompt (STRING)
"""

import json
import logging

from . import _credentials


class AIHPromptPrepNode:
    CATEGORY = "AIH"
    FUNCTION = "prepare"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "base_prompt": ("STRING", {"multiline": True, "default": ""}),
                "template_id": ("INT", {"default": 0, "min": 0}),
                "style_id": ("INT", {"default": 0, "min": -1}),
                "style_shortlist": ("STRING", {"default": "[]"}),  # frontend-only (filtre dropdown), pas envoyé à l'API
                "special_instructions": ("STRING", {"default": ""}),
            },
            "optional": {
                # JSON sérialisé des éléments (connecté à la sortie elements_json du Elements Picker)
                "elements": ("STRING", {"forceInput": True, "multiline": True, "default": "[]"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("llm_prompt", "system_prompt", "neg_prompt")

    def prepare(self, seed=0, base_prompt="", template_id=0,
                style_id=0, style_shortlist="[]",
                special_instructions="", elements="[]"):
        # api_key et api_url lus depuis le fichier de credentials
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()

        # Defensive : ComfyUI peut envoyer une string vide pour un INT
        try:
            template_id = int(template_id) if template_id != "" else 0
        except (ValueError, TypeError):
            template_id = 0
        try:
            style_id = int(style_id) if style_id != "" else 0
        except (ValueError, TypeError):
            style_id = 0

        # Si style_id == -1 (mode random), piocher un style au hasard
        if style_id == -1:
            try:
                import requests as _req
                resp = _req.get(
                    f"{api_url}/styles",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10
                )
                if resp.ok:
                    styles = resp.json()
                    if styles:
                        import random as _rand
                        chosen = _rand.choice(styles)
                        style_id = chosen.get("id", 0) if isinstance(chosen, dict) else 0
            except Exception as e:
                logging.warning(f"[AIH Prep] Random style fetch failed: {e}")
                style_id = 0

        # Parser elements : soit un tableau direct, soit l'objet _elements_json complet
        elems = []
        elems_raw = ""
        try:
            elems_parsed = json.loads(elements) if elements else []
            if isinstance(elems_parsed, dict):
                elems = elems_parsed.get("elements", [])
            elif isinstance(elems_parsed, list):
                elems = elems_parsed
        except (json.JSONDecodeError, TypeError):
            # Pas du JSON → texte brut
            elems_raw = elements or ""

        def _fmt_elems(elist):
            lines = []
            for e in elist:
                if e.get("type") == "filter":
                    name = e.get("name") or f"ID {e.get('id', '?')}"
                    lines.append(f"[Filtre: {name}]")
                elif e.get("type") == "text":
                    lines.append(f"[Recherche: {e.get('text', '')}]")
                elif e.get("type") == "random":
                    lines.append("[Éléments aléatoires]")
            return "\n".join(lines)

        elems_text = _fmt_elems(elems)
        # Concaténer : éléments formatés + texte brut + prompt de base
        parts = [p for p in [elems_text, elems_raw, base_prompt] if p]
        combined_text = "\n\n".join(parts)

        # Construire le payload pour /api/enhance/prompts
        payload = {
            "text": combined_text,
            "seed": seed if seed > 0 else None,
            "template_id": template_id if template_id > 0 else None,
            "style_id": style_id if style_id > 0 else None,
            "special_instructions": special_instructions,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Un seul appel : /api/enhance/prompts (pas d'appel LLM)
        try:
            import requests
            r = requests.post(f"{api_url}/enhance/prompts",
                              json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()

            llm_prompt = data.get("llm_prompt", "")
            system_prompt = data.get("system_prompt", "")
            neg_prompt = data.get("neg_prompt", "")

            logging.warning(
                f"[AIH Prep] template_id={template_id} style_id={style_id} "
                f"model='{data.get('model', '?')}' "
                f"sys_len={len(system_prompt)} user_len={len(llm_prompt)} neg_len={len(neg_prompt)}"
            )

            return {
                "ui": {
                    "llm_prompt": [llm_prompt],
                    "system_prompt": [system_prompt],
                    "neg_prompt": [neg_prompt],
                },
                "result": (llm_prompt, system_prompt, neg_prompt),
            }
        except ImportError:
            msg = "Erreur: module 'requests' manquant. pip install requests"
            return {
                "ui": {"llm_prompt": [msg], "system_prompt": [""], "neg_prompt": [""]},
                "result": (msg, "", ""),
            }
        except Exception as e:
            err = str(e)
            if "401" in err:
                msg = "Erreur : clé API invalide ou manquante. Configurez-la dans le menu AIH."
            elif "429" in err:
                msg = "Erreur : rate limit atteint. Attendez un instant."
            elif "404" in err:
                msg = f"Erreur : endpoint /enhance/prompts introuvable sur {api_url}. Backend AIH pas à jour ?"
            else:
                msg = f"Erreur API : {err}"
            logging.warning(f"[AIH Prep] {msg}")
            return {
                "ui": {"llm_prompt": [msg], "system_prompt": [""], "neg_prompt": [""]},
                "result": (msg, "", ""),
            }
