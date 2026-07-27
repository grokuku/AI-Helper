"""
AIH Elements Picker Node — Custom widget (JavaScript).
L'UI interactive est rendue par web/js/aih_elements_widget.js.

Au "Run" (workflow), Python appelle directement l'API /api/generate
avec le seed courant + les éléments sérialisés → résultat déterministe.
Au "Test generation", JS appelle l'API pour un aperçu instantané.
"""

import json
import random


def _hash32(s):
    """FNV-1a 32-bit hash, identique a la fonction hash32() du widget JS."""
    h = 0x811c9dc5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xffffffff
    return h


def _pick_alternative(raw_text, seed, element_index):
    """Si raw_text contient des alternatives separees par '::', en choisit une.

    Deterministe par seed pour garantir la reproductibilite du workflow.
    Si seed == 0, choix aleatoire non-deterministe.
    """
    if not raw_text:
        return ""
    alts = [part.strip() for part in raw_text.split("::") if part.strip()]
    if len(alts) < 2:
        return raw_text
    if seed <= 0:
        return random.choice(alts)
    h = _hash32(f"{seed}|{element_index}|{raw_text}")
    return alts[h % len(alts)]


class AIHElementsNode:
    CATEGORY = "AIH"
    FUNCTION = "generate"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            },
            "optional": {
                # JSON sérialisé par le JS : elements + random_count
                # Masqué dans l'UI ComfyUI
                "_elements_json": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("elements",)

    def generate(self, seed, _elements_json="{}"):
        from . import _credentials
        try:
            elems_cfg = json.loads(_elements_json) if _elements_json else {}
        except json.JSONDecodeError:
            msg = "Erreur : config JSON invalide"
            return {
                "ui": {"elements": [msg]},
                "result": (msg,)
            }

        # api_key et api_url lus depuis le fichier de credentials
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()
        elements = elems_cfg.get("elements", [])
        random_count = int(elems_cfg.get("random_count", 0))

        # Filtrer les entrees marquees visible=False (masquees depuis l'UI)
        elements = [el for el in elements if el.get("visible") is not False]

        # Resoudre les alternatives "::" dans les textes raw/texte (deterministe)
        for i, el in enumerate(elements):
            if el.get("type") in ("raw", "text"):
                el["text"] = _pick_alternative(el.get("text", ""), seed, i)

        # Vérifier qu'il y a du contenu à générer
        if not elements and random_count <= 0:
            return {
                "ui": {"elements": ["⚠️ Aucun filtre sélectionné. Ajoutez des filtres dans la liste."]},
                "result": ("⚠️ Aucun filtre sélectionné. Ajoutez des filtres dans la liste.",)
            }

        # Construire le payload pour /api/generate
        payload = {"elements": elements}
        if seed > 0:
            payload["seed"] = seed
        if random_count > 0:
            payload["random_count"] = random_count
            payload["random_sfw"] = bool(elems_cfg.get("random_sfw", True))
            payload["random_nsfw"] = bool(elems_cfg.get("random_nsfw", False))

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            import requests
            r = requests.post(f"{api_url}/generate", json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            prompt = data.get("prompt", "")
            return {
                "ui": {"elements": [prompt]},
                "result": (prompt,)
            }
        except ImportError:
            msg = "Erreur : module 'requests' manquant. pip install requests"
            return {
                "ui": {"elements": [msg]},
                "result": (msg,)
            }
        except Exception as e:
            msg = str(e)
            if "401" in msg:
                msg = "Erreur : clé API invalide ou manquante. Configurez-la dans le menu AIH."
            elif "429" in msg:
                msg = "Erreur : rate limit atteint. Attendez un instant."
            else:
                msg = f"Erreur API : {msg}"
            return {
                "ui": {"elements": [msg]},
                "result": (msg,)
            }