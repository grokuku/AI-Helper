"""
AIH Elements Picker Node — Custom widget (JavaScript).
L'UI interactive est rendue par web/js/aih_elements_widget.js.

Au "Run" (workflow), Python appelle directement l'API /api/generate
avec le seed courant + les éléments sérialisés → résultat déterministe.
Au "Test generation", JS appelle l'API pour un aperçu instantané.

Mode "intelligent" LLM :
  - Syntaxe ||concept:N dans une liste → liste générée par LLM
  - 🧠 ON sur une liste manuelle → filtrage LLM par contexte
  - Appel POST /api/keywords/llm-process (preset_id, instruction, input_text)
  - Traitement séquentiel avec accumulation de contexte

Format des listes manuelles :
  - {bleu::rouge::vert} → un bloc de choix, random dedans
  - femme de {25::30::35::40} ans → template avec bloc inline
  - {blond::brun} cheveux {long::court} → multiples blocs indépendants
  - Texte hors {} = littéral, retourné tel quel
"""

import json
import logging
import random
import re


def _hash32(s):
    """FNV-1a 32-bit hash, identique a la fonction hash32() du widget JS."""
    h = 0x811c9dc5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xffffffff
    return h


# Regex pour détecter la syntaxe ||concept ou ||concept:N (ancre en début de texte)
_LLM_CONCEPT_RE = re.compile(r'\|\|([^:]+)(?::(\d+))?')


# Regex pour trouver les blocs {choix1::choix2::...}
_BRACE_BLOCK_RE = re.compile(r'\{([^}]+)\}')


def _extract_brace_blocks(text):
    """Extrait tous les blocs {a::b::c} du texte.

    Retourne une liste de tuples (match_obj, [choix1, choix2, ...]).
    Si aucun bloc {} n'est trouvé, retourne une liste vide.
    """
    blocks = []
    for m in _BRACE_BLOCK_RE.finditer(text):
        content = m.group(1)
        choices = [c.strip() for c in content.split("::") if c.strip()]
        blocks.append((m, choices))
    return blocks


def _resolve_braces(text, seed, element_index):
    """Résout tous les blocs {a::b::c} dans le texte.

    Pour chaque bloc, choisit une option (déterministe par seed si seed > 0,
    sinon aléatoire) et remplace le bloc par le choix dans le texte.
    Le texte hors {} est retourné littéral.
    Si aucun bloc {} trouvé, retourne le texte tel quel.
    """
    if not text:
        return ""

    blocks = _extract_brace_blocks(text)
    if not blocks:
        return text

    result = text
    for m, choices in blocks:
        if not choices:
            continue
        if len(choices) == 1:
            chosen = choices[0]
        elif seed <= 0:
            chosen = random.choice(choices)
        else:
            h = _hash32(f"{seed}|{element_index}|{m.group(0)}")
            chosen = choices[h % len(choices)]
        result = result.replace(m.group(0), chosen, 1)

    return result


def _resolve_braces_with_filtered(text, seed, element_index, filtered_choices):
    """Résout les blocs {} en utilisant uniquement les choix filtrés par le LLM.

    Pour chaque bloc, intersecte ses choix avec filtered_choices.
    Si l'intersection est non vide, pick dedans. Sinon, fallback random
    dans les choix originaux du bloc.
    """
    if not text:
        return ""

    blocks = _extract_brace_blocks(text)
    if not blocks:
        return text

    filtered_set = set(c.strip() for c in filtered_choices)
    result = text
    for m, choices in blocks:
        if not choices:
            continue
        # Intersection des choix du bloc avec les choix filtrés
        valid = [c for c in choices if c in filtered_set]
        if not valid:
            # Fallback: utiliser tous les choix originaux du bloc
            valid = choices
        if len(valid) == 1:
            chosen = valid[0]
        elif seed <= 0:
            chosen = random.choice(valid)
        else:
            h = _hash32(f"{seed}|{element_index}|{m.group(0)}")
            chosen = valid[h % len(valid)]
        result = result.replace(m.group(0), chosen, 1)

    return result


def _pick_from_list(items, seed, element_index, raw_text):
    """Choisit un élément dans une liste. Déterministe si seed > 0, sinon random."""
    if not items:
        return ""
    if seed <= 0:
        return random.choice(items)
    h = _hash32(f"{seed}|{element_index}|{raw_text}")
    return items[h % len(items)]


def _call_llm_process(api_url, api_key, preset_id, instruction, input_text=""):
    """Appelle POST /api/keywords/llm-process.

    Retourne le texte output (str) ou None si erreur/timeout/réponse vide.
    """
    try:
        import requests
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "preset_id": preset_id,
            "instruction": instruction,
            "input_text": input_text,
        }
        r = requests.post(
            f"{api_url}/keywords/llm-process",
            json=body,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        output = data.get("output", "")
        return output if output else None
    except Exception as e:
        logging.warning(f"[AIH Elements] LLM call failed: {e}")
        return None


def _parse_llm_list(output_text):
    """Parse une sortie LLM en liste d'éléments (séparés par virgules ou newlines)."""
    if not output_text:
        return []
    # Remplacer les newlines par virgules puis splitter
    cleaned = output_text.replace("\n", ",").replace("\r", ",")
    items = [x.strip() for x in cleaned.split(",") if x.strip()]
    return items


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

        # --- Mode intelligent LLM ---
        preset_id = int(elems_cfg.get("preset_id", 0) or 0)
        llm_default_count = int(elems_cfg.get("llm_default_count", 10) or 10)
        brain_toggles = elems_cfg.get("brain_toggles", [])

        # Filtrer les entrees marquees visible=False (masquees depuis l'UI)
        elements = [el for el in elements if el.get("visible") is not False]

        # Resoudre les blocs {a::b::c} et les listes LLM "||" dans les textes raw/texte.
        # Traitement séquentiel : le contexte accumule les mots-clés choisis dans les
        # listes précédentes pour les appels LLM avec 🧠 ON.
        context = []  # liste des mots-clés choisis (pour le contexte LLM)
        indices_to_skip = set()  # indices d'éléments LLM à skip (liste vide)

        for i, el in enumerate(elements):
            if el.get("type") not in ("raw", "text"):
                continue

            raw_text = el.get("text", "")
            if not raw_text:
                continue

            brain_on = (
                bool(brain_toggles[i])
                if isinstance(brain_toggles, list) and i < len(brain_toggles)
                else False
            )

            # --- Détection syntaxe ||concept:N (liste générée par LLM) ---
            llm_match = _LLM_CONCEPT_RE.match(raw_text.strip())

            if llm_match:
                concept = llm_match.group(1).strip()
                count_str = llm_match.group(2)
                count = int(count_str) if count_str else llm_default_count

                # preset_id == 0 → pas de LLM disponible, skip cette liste
                if preset_id == 0:
                    indices_to_skip.add(i)
                    continue

                # Construire instruction et input_text selon 🧠 ON/OFF
                if brain_on:
                    instruction = (
                        f"Génère {count} {concept} cohérents avec le contexte. "
                        f"Retourne uniquement une liste séparée par des virgules."
                    )
                    input_text = (
                        f"Contexte: [{', '.join(context)}]" if context else ""
                    )
                else:
                    instruction = (
                        f"Génère {count} {concept}. "
                        f"Retourne uniquement une liste séparée par des virgules."
                    )
                    input_text = ""

                output = _call_llm_process(
                    api_url, api_key, preset_id, instruction, input_text
                )
                if output:
                    items = _parse_llm_list(output)
                    if items:
                        chosen = _pick_from_list(items, seed, i, raw_text)
                        el["text"] = chosen
                        context.append(chosen)
                    else:
                        indices_to_skip.add(i)
                else:
                    # Fallback LLM ||: skip (liste vide)
                    indices_to_skip.add(i)
                continue

            # --- Liste manuelle avec blocs {a::b::c} ou texte littéral ---
            blocks = _extract_brace_blocks(raw_text)
            has_braces = len(blocks) >= 1

            if has_braces and brain_on and preset_id != 0:
                # 🧠 ON + liste manuelle avec {} → filtrage LLM par contexte
                # Concaténer tous les choix de tous les blocs
                all_choices = []
                for _, choices in blocks:
                    all_choices.extend(choices)

                instruction = (
                    "Filtre cette liste pour garder uniquement les éléments "
                    "cohérents avec le contexte. Retourne uniquement une liste "
                    "séparée par des virgules."
                )
                input_text = (
                    f"Contexte: [{', '.join(context)}]\n"
                    f"Liste: [{', '.join(all_choices)}]"
                )

                output = _call_llm_process(
                    api_url, api_key, preset_id, instruction, input_text
                )
                if output:
                    filtered = _parse_llm_list(output)
                    if filtered:
                        el["text"] = _resolve_braces_with_filtered(
                            raw_text, seed, i, filtered
                        )
                        context.append(el["text"])
                        continue
                    else:
                        logging.warning(
                            "[AIH Elements] LLM filter returned empty list, "
                            "falling back to random"
                        )
                else:
                    logging.warning(
                        "[AIH Elements] LLM filter call failed, "
                        "falling back to random"
                    )
                # Fallback: random dans les blocs d'origine
                el["text"] = _resolve_braces(raw_text, seed, i)
                context.append(el["text"])
            else:
                # Comportement standard (déterministe par seed)
                # Sans {} → texte littéral retourné tel quel
                el["text"] = _resolve_braces(raw_text, seed, i)
                if el["text"]:
                    context.append(el["text"])

        # Retirer les éléments LLM qui n'ont pas pu être générés (listes vides)
        if indices_to_skip:
            elements = [
                el for i, el in enumerate(elements) if i not in indices_to_skip
            ]

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