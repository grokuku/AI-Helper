"""
AIH Music Node — Node ComfyUI "AIH Music" (MiniMax Music3 caption rewriter).

3 sorties : caption (STRING), lyrics (STRING), duration_seconds (INT).
2 entrées wires-only : musique, lyrics (+ llm_config en forceInput).

Deux modes :
  - MODE CLOUD (défaut) : si llm_config non connecté -> POST /api/music3/generate
    et le backend orchestre le pipeline en 5 étapes.
  - MODE LOCAL : si llm_config connecté -> la node exécute elle-même les 5 étapes
    via _llm_helper.call_llm, en miroir exact du pipeline backend. Les system
    prompts des 5 étapes sont importés depuis music_prompts.py (⚠️ mirror).

DOM widget : web/js/aih_music_widget.js.
"""

import json
import logging
import re

from . import _credentials
from . import _llm_helper
from . import music_prompts


# ── Helpers parsing (mirroir des helpers backend) ──────────────────────

def _strip_code_fences(text):
    """Retire les code fences markdown autour d'un bloc JSON. Retourne le texte nu."""
    if not text:
        return text
    s = text.strip()
    m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', s)
    if m:
        s = m.group(1).strip()
    return s


def _parse_json_strip(text):
    """Parse un JSON en retirant les code fences. dict|None."""
    if not text:
        return None
    try:
        data = json.loads(_strip_code_fences(text))
        return data if isinstance(data, dict) else None
    except Exception:
        logging.warning("[AIH Music] JSON parse failed")
        return None


def _parse_duration(text):
    """Parse une durée en secondes depuis un texte. 0 si invalide (mirror backend)."""
    if not text:
        return 0
    m = re.search(r'DURATION\s*[:=]\s*(\d+)', text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 0
    m2 = re.search(r'\bduration\s*[:=]\s*(\d+)', text, re.IGNORECASE)
    if m2:
        try:
            return int(m2.group(1))
        except ValueError:
            return 0
    return 0


def _split_lines(content):
    """Retourne la liste des lignes non vides d'un texte."""
    if not content:
        return []
    return [ln.strip() for ln in content.splitlines() if ln.strip()]


class AIHMusicNode:
    CATEGORY = "AIH"
    FUNCTION = "generate"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Widget natif seed — reproductibilité (même seed -> même sortie).
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                    "control_after_generate": "randomize"}),
            },
            "optional": {
                # WIRES uniquement — tout est optionnel.
                "musique": ("STRING", {"forceInput": True, "multiline": True, "default": ""}),
                "lyrics": ("STRING", {"forceInput": True, "multiline": True, "default": ""}),
                "llm_config": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("caption", "lyrics", "duration_seconds")

    def generate(self, seed=0, musique="", lyrics="", llm_config=None):
        # api_url / api_key lus depuis le fichier de credentials local
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()

        if llm_config:
            return self._generate_local(seed, musique, lyrics, llm_config, api_url, api_key)
        return self._generate_cloud(seed, musique, lyrics, api_url, api_key)

    # ── MODE CLOUD ──────────────────────────────────────────────────────
    def _generate_cloud(self, seed, musique, lyrics, api_url, api_key):
        """Délègue l'orchestration au backend (POST /api/music3/generate)."""
        payload = {"musique": musique or "", "lyrics": lyrics or ""}
        if seed and seed > 0:
            payload["seed"] = int(seed)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _err(msg):
            return {
                "ui": {"caption": [msg], "lyrics": [""], "duration": [0]},
                "result": (msg, "", 0),
            }

        try:
            import requests
            r = requests.post(f"{api_url}/music3/generate", json=payload,
                              headers=headers, timeout=(10, 180))
            r.raise_for_status()
            data = r.json()
            c = data.get("caption") or ""
            l = data.get("lyrics") or ""
            try:
                d = int(data.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                d = 0
            return {
                "ui": {"caption": [c], "lyrics": [l], "duration": [d]},
                "result": (c, l, d),
            }
        except ImportError:
            return _err("Erreur: module 'requests' manquant. pip install requests")
        except Exception as e:
            msg = str(e)
            if "401" in msg:
                msg = "Erreur : clé API invalide ou manquante."
            elif "429" in msg:
                msg = "Erreur : rate limit atteint. Attendez un instant."
            else:
                msg = f"Erreur API : {msg}"
            return _err(msg)

    # ── MODE LOCAL ──────────────────────────────────────────────────────
    def _generate_local(self, seed, musique, lyrics, llm_config, api_url, api_key):
        """Orchestrateur LOCAL : miroir du pipeline backend (5 étapes)."""
        brief = {}
        caption = ""
        final_lyrics = ""
        duration_seconds = 0

        # Etape 1 : brief
        try:
            brief = self._step1_brief(musique, lyrics, llm_config, seed)
        except Exception:
            logging.warning("[AIH Music] step1 (brief) failed")
            brief = {}

        # Etapes 2-4 : routage + sélection + caption
        try:
            router = self._fetch_ref(api_url, api_key, "genre-router.md")
            if router:
                familles = self._step2_route(brief, router, llm_config, seed)
                if familles:
                    index_parts = []
                    for fam in familles:
                        idx = self._fetch_ref(api_url, api_key, f"families/{fam}/index.md")
                        if idx:
                            index_parts.append(f"--- {fam} ---\n{idx}")
                    combined_index = "\n\n".join(index_parts)
                    if combined_index:
                        selected = self._step3_select(brief, combined_index, llm_config, seed)
                        template_parts = []
                        for rel in selected:
                            if not rel:
                                continue
                            t = self._fetch_ref(api_url, api_key, rel)
                            if t:
                                template_parts.append(f"--- {rel} ---\n{t}")
                        templates_text = "\n\n".join(template_parts)
                        caption = self._step4_caption(brief, templates_text, llm_config, seed)
        except Exception:
            logging.exception("[AIH LOCAL] routing pipeline failed, falling back to direct caption")

        # Fallback : pas de routage -> caption direct sans routage
        if not caption:
            try:
                fallback = self._build_fallback_caption(api_url, api_key)
                caption = self._step4_caption(brief, fallback, llm_config, seed)
            except Exception:
                logging.exception("[AIH LOCAL] fallback caption failed")
                caption = ""

        # Etape 5 : durée + paroles
        try:
            duration_seconds, final_lyrics = self._step5_duration_lyrics(
                brief, caption, lyrics, llm_config, seed
            )
        except Exception:
            logging.exception("[AIH LOCAL] step5 (duration/lyrics) failed")
            duration_seconds = 0
            final_lyrics = lyrics if lyrics and lyrics.strip() else ""

        return {
            "ui": {"caption": [caption], "lyrics": [final_lyrics], "duration": [duration_seconds]},
            "result": (caption, final_lyrics, duration_seconds),
        }

    def _fetch_ref(self, api_url, api_key, relpath):
        """GET {api_url}/music3/reference/{relpath} — retourne le texte ou ''."""
        try:
            import requests
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            r = requests.get(f"{api_url}/music3/reference/{relpath}",
                             headers=headers, timeout=15)
            if r.ok:
                return r.text
        except Exception as e:
            logging.warning(f"[AIH Music] reference fetch failed ({relpath}): {e}")
        return ""

    def _call_llm(self, llm_config, system_prompt, user_prompt, seed=0):
        """Wrapper autour de _llm_helper.call_llm avec seed contrôlé."""
        return _llm_helper.call_llm(
            llm_config, system_prompt, user_prompt,
            seed=seed if seed and seed > 0 else None,
        )

    # Étape 1 — brief structuré
    def _step1_brief(self, musique, lyrics, llm_config, seed=0):
        content = self._call_llm(
            llm_config,
            music_prompts.STEP1_SYSTEM_PROMPT,
            music_prompts.build_step1_user(musique, lyrics),
            seed=seed,
        )
        data = _parse_json_strip(content)
        return data if isinstance(data, dict) else {}

    # Étape 2 — routage vers 1-2 familles
    def _step2_route(self, brief, genre_router_text, llm_config, seed=0):
        content = self._call_llm(
            llm_config,
            music_prompts.build_step2_system(genre_router_text),
            music_prompts.build_step2_user(brief),
            seed=seed,
        )
        return _split_lines(content)

    # Étape 3 — sélection <=3 templates
    def _step3_select(self, brief, family_index_text, llm_config, seed=0):
        content = self._call_llm(
            llm_config,
            music_prompts.STEP3_SYSTEM_PROMPT,
            music_prompts.build_step3_user(brief, family_index_text),
            seed=seed,
        )
        return _split_lines(content)[:3]

    # Étape 4 — caption text (temp basse)
    def _step4_caption(self, brief, templates_text, llm_config, seed=0):
        # appel direct LLM avec temperature plus basse via config max conservé
        content = _llm_helper.call_llm(
            llm_config,
            music_prompts.build_step4_system(templates_text),
            music_prompts.build_step4_user(brief),
            seed=seed if seed and seed > 0 else None,
        )
        return content or ""

    # Étape 5 — durée + paroles
    def _step5_duration_lyrics(self, brief, caption, user_lyrics, llm_config, seed=0):
        duration = 0
        lyrics = ""
        if user_lyrics and user_lyrics.strip():
            # Paroles fournies : on les conserve telles quelles.
            lyrics = user_lyrics
            duration = _parse_duration(
                f"{json.dumps(brief, ensure_ascii=False)} {caption}"
            )
            return duration, lyrics
        # Générer des paroles structurées via LLM.
        content = self._call_llm(
            llm_config,
            music_prompts.STEP5_LYRICS_SYSTEM_PROMPT,
            music_prompts.build_step5_user(brief, caption),
            seed=seed,
        ) or ""
        duration = _parse_duration(content)
        # Retirer la ligne de durée des paroles.
        content = re.sub(
            r'^DURATION\s*[:=]\s*\d+.*$', '', content,
            flags=re.MULTILINE | re.IGNORECASE,
        ).strip()
        return duration, content

    # Fallback sans routage : liste les fichiers du cache comme templates.
    def _build_fallback_caption(self, api_url, api_key):
        files = self._fetch_manifest(api_url, api_key)
        if not files:
            return "No reference templates available in cache."
        lines = ["# Available reference templates", ""]
        for f in files[:10]:
            content = self._fetch_ref(api_url, api_key, f)
            if content:
                lines.append(f"## {f}\n{content[:2000]}")
        return "\n\n".join(lines)

    def _fetch_manifest(self, api_url, api_key):
        """GET /api/music3/manifest — retourne la liste des fichiers .md."""
        try:
            import requests
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            r = requests.get(f"{api_url}/music3/manifest", headers=headers, timeout=15)
            if r.ok:
                data = r.json()
                if isinstance(data, dict):
                    return data.get("files") or []
        except Exception as e:
            logging.warning(f"[AIH Music] manifest fetch failed: {e}")
        return []
