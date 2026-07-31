"""
AIH Prompt Enhancer Node — Optimise un prompt via LLM.
DOM widget + connexion aux éléments du Elements Picker.

L'api_key et l'URL du serveur sont lues depuis le fichier de credentials
ComfyUI (ComfyUI/user/default/aih_credentials.json) via le helper _credentials.

Le mode client-side (LLM local) est désactivé pour l'instant — le backend
fait tout l'appel LLM. Ce mode pourra être réintroduit plus tard via un
nouvel endpoint /api/enhance/preset-info qui résoudra les métadonnées
du preset (is_client_side, base_url) côté serveur.
"""

import json
import logging

from . import _credentials
from . import _llm_helper


class AIHEnhanceNode:
    CATEGORY = "AIH"
    FUNCTION = "enhance"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "base_prompt": ("STRING", {"multiline": True, "default": ""}),
                "template_id": ("INT", {"default": 0, "min": 0}),
                "preset_id": ("INT", {"default": 0, "min": 0}),
                "style_id": ("INT", {"default": 0, "min": -1}),
                "style_shortlist": ("STRING", {"default": "[]"}),  # frontend-only (filtre dropdown), pas envoyé à l'API
                "special_instructions": ("STRING", {"default": ""}),
            },
            "optional": {
                # JSON sérialisé des éléments (connecté à la sortie elements_json du Elements Picker)
                "elements": ("STRING", {"forceInput": True, "multiline": True, "default": "[]"}),
                "llm_config": ("AIH_LLM_CONFIG", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "AIH_LLM_CONFIG")
    RETURN_NAMES = ("prompt", "negative_prompt", "llm_config")

    def enhance(self, seed=0, base_prompt="", template_id=0,
                preset_id=0, style_id=0, style_shortlist="[]",
                special_instructions="", elements="[]", llm_config=None):
        # api_key et api_url lus depuis le fichier de credentials
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()

        # Defensive : ComfyUI peut envoyer une string vide pour un INT
        try:
            template_id = int(template_id) if template_id != "" else 0
        except (ValueError, TypeError):
            template_id = 0
        try:
            preset_id = int(preset_id) if preset_id != "" else 0
        except (ValueError, TypeError):
            preset_id = 0
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
                logging.warning(f"[AIH Enhance] Random style fetch failed: {e}")
                style_id = 0

        # Parse elements JSON (soit un tableau direct, soit l'objet _elements_json complet)
        elems = []
        elems_raw = ""
        try:
            elems_parsed = json.loads(elements) if elements else []
            if isinstance(elems_parsed, dict):
                elems = elems_parsed.get("elements", [])
            elif isinstance(elems_parsed, list):
                elems = elems_parsed
        except (json.JSONDecodeError, TypeError):
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
        parts = [p for p in [elems_text, elems_raw, base_prompt] if p]
        combined_text = "\n\n".join(parts)

        # Construire le payload pour /api/enhance
        payload = {
            "text": combined_text,
            "seed": seed if seed > 0 else None,
            "template_id": template_id if template_id > 0 else None,
            "preset_id": preset_id if preset_id > 0 else None,
            "style_id": style_id if style_id > 0 else None,
            "special_instructions": special_instructions,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # --- Mode LLM local (llm_config) ---
        if llm_config:
            system_prompt = (
                "You are an expert prompt engineer for image generation. "
                "Enhance and expand the user's prompt with vivid details, "
                "lighting, composition, and style. Return only the enhanced prompt."
            )
            user_prompt = combined_text or base_prompt
            enhanced = _llm_helper.call_llm(llm_config, system_prompt, user_prompt, seed=seed)
            if enhanced:
                return {
                    "ui": {"prompt": [enhanced], "negative_prompt": [""]},
                    "result": (enhanced, "", llm_config)
                }
            # Fallback sur le backend si le LLM local échoue

        # Mode cloud (defaut) : appel streaming vers /api/enhance
        try:
            import requests
            r = requests.post(f"{api_url}/enhance",
                              json=payload, headers=headers, stream=True, timeout=(10, 180))
            r.raise_for_status()
            prompt = ""
            neg_prompt = ""
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode('utf-8'))
                except Exception:
                    continue
                status = chunk.get("status", "")
                if status == "done":
                    prompt = chunk.get("output", "")
                    neg_prompt = chunk.get("negative_prompt", "")
                    break
                elif status == "error":
                    return {
                        "ui": {"prompt": [f"Erreur: {chunk.get('error', '')[:200]}"], "negative_prompt": [""]},
                        "result": (f"Erreur: {chunk.get('error', '')[:200]}", "", llm_config)
                    }
            return {
                "ui": {"prompt": [prompt], "negative_prompt": [neg_prompt]},
                "result": (prompt, neg_prompt, llm_config)
            }
        except ImportError:
            msg = "Erreur: module 'requests' manquant. pip install requests"
            return {
                "ui": {"prompt": [msg], "negative_prompt": [""]},
                "result": (msg, "", llm_config)
            }
        except Exception as e:
            msg = str(e)
            if "401" in msg:
                msg = "Erreur : clé API invalide ou manquante."
            elif "429" in msg:
                msg = "Erreur : rate limit atteint. Attendez un instant."
            else:
                msg = f"Erreur API : {msg}"
            return {
                "ui": {"prompt": [msg], "negative_prompt": [""]},
                "result": (msg, "", llm_config)
            }
