"""Routes music3 — "AIH Music" node backend (MiniMax Music3 caption rewriter).

This module implements the backend side of the "AIH Music" ComfyUI node. It
provides a 5-step LLM pipeline that mirrors the MiniMax "music-caption-rewriter"
skill (progressive disclosure: genre-router.md -> family -> index -> <=3 templates
-> structured "Music 3.0 Structured Caption" in 3 sections).

Two modes are supported:
  - LOCAL: the node fetches the reference files via GET /api/music3/reference/*
    and performs its own LLM calls.
  - CLOUD: the backend orchestrates everything via POST /api/music3/generate
    using the LLM provider configured in an ai_presets row.

Reference cache is synced daily from the MiniMax-Music3 GitHub repo (see
sync_music3_cache / backend/music3_refresh.py).
"""

import logging
import os
import re
import json
import tarfile
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from context import *
from routes.enhance import _call_llm_internal, _resolve_preset


# ── Référentiel & cache ───────────────────────────────────────────────
MUSIC3_REPO_URL = "https://github.com/MiniMax-AI/MiniMax-Music3"
MUSIC3_TARBALL = f"{MUSIC3_REPO_URL}/archive/refs/heads/main.tar.gz"
MUSIC3_REPO_SUBPATH = "skills/music-caption-rewriter/references"

# Cache local : backend/data/minimax-music3/references (backend/data gitignoré)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
MUSIC3_DATA_DIR = _BACKEND_DIR / 'data' / 'minimax-music3'
MUSIC3_REFS_DIR = MUSIC3_DATA_DIR / 'references'

# ── System prompts des 5 étapes ────────────────────────────────────────
# ⚠️ mirror — ces constantes sont dupliquées côté node ComfyUI (AIH Music)
# pour rester réutilisables en MODE LOCAL. Garder les deux en synchro.
STEP1_SYSTEM_PROMPT = (
    "parse a music description and optional lyrics into a structured brief JSON "
    "with keys: macro_genre, mood, tempo, vocal, instruments, sections, exclusions"
)

STEP2_SYSTEM_PROMPT = (
    "select 1-2 style family names from the router. "
    "Return ONLY the family names, one per line."
)

STEP3_SYSTEM_PROMPT = (
    "given the family index, select up to 3 template files with distinct roles "
    "(e.g. one for vocals, one for arrangement, one for production). "
    "Return ONLY relative file paths, one per line."
)

STEP4_SYSTEM_PROMPT = (
    "Write a 'Music 3.0 Structured Caption' for the given music brief, using the "
    "provided reference templates as style guidance. The caption MUST have exactly "
    "3 sections:\n"
    "1. Global Metadata (genre, subgenre, tempo, emotional progression, production profile)\n"
    "2. Vocal Details (lead, timbre, register, delivery, harmony/backing)\n"
    "3. Arrangement (section-by-section timeline, instrument lifecycles, transitions)\n"
    "Return only the structured caption."
)

STEP5_LYRICS_SYSTEM_PROMPT = (
    "You are a lyricist. Write original structured song lyrics matching the given "
    "music brief and caption. Use explicit section markers like [Verse], [Chorus], "
    "[Bridge], [Outro].\n"
    "Return only the lyrics."
)

STEP5_DURATION_SYSTEM_PROMPT = (
    "You are a music producer estimating song length. Given the music brief and the "
    "structured caption (tempo, number of sections, arrangement complexity), estimate "
    "the total duration in seconds. A standard pop song is 150-240s, an instrumental or "
    "ambient piece may be 120-300s, an EDM build 200-300s, a short jingle 30-60s.\n"
    "Return ONLY an integer number of seconds (e.g. 180). No other text."
)

STEP5_INSTRUMENTAL_LYRICS_SYSTEM_PROMPT = (
    "You are structuring the lyrics field for a fully INSTRUMENTAL MiniMax Music 3.0 "
    "track (no vocals at all). Use ONLY the section tags [Intro], [Instrumental], and "
    "[Outro]. Do NOT use [Verse], [Pre-Chorus], [Chorus], [Bridge], or any vocal-only "
    "section tag. Do NOT write any sung text.\n"
    "Output only the bracketed structure, one tag per line. Put an [Instrumental] tag "
    "for each musical section implied by the brief/caption (typically 4-8 sections). "
    "Begin with [Intro] and end with [Outro]."
)


# ── Helpers cache / fichiers ───────────────────────────────────────────

def _music3_refs_root():
    """Crée le dossier de références si besoin et retourne son Path."""
    MUSIC3_REFS_DIR.mkdir(parents=True, exist_ok=True)
    return MUSIC3_REFS_DIR


def _resolve_reference(relpath):
    """Retourne un Path sécurisé (anti path traversal) ou None si fichier absent.

    Base = MUSIC3_REFS_DIR. Vérifie que la cible résolue reste bien DANS la base
    (containment via os.path.commonpath). Retourne None si traversal ou absent.
    """
    try:
        base = MUSIC3_REFS_DIR.resolve()
    except Exception:
        return None
    try:
        target = (base / relpath).resolve()
    except Exception:
        return None
    try:
        if os.path.commonpath([str(base), str(target)]) != str(base):
            return None
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def _read_reference(relpath):
    """Lit un fichier texte de référence (utf-8). None si absent / illisible."""
    p = _resolve_reference(relpath)
    if p is None:
        return None
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        logging.warning(f"[music3] read reference failed: {relpath}")
        return None


def _list_md_files():
    """Liste tous les chemins .md relatifs du cache (triés)."""
    root = _music3_refs_root()
    files = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith('.md'):
                full = Path(dirpath) / name
                try:
                    files.append(str(full.relative_to(root)))
                except ValueError:
                    continue
    return sorted(files)


def _manifest_path():
    return MUSIC3_DATA_DIR / 'manifest.json'


def _metadata_path():
    return MUSIC3_DATA_DIR / 'metadata.json'


def _read_last_updated():
    """Lit le dernier timestamp de mise à jour du cache depuis metadata.json."""
    try:
        p = _metadata_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding='utf-8'))
            return data.get('last_updated')
    except Exception:
        logging.warning("[music3] metadata read failed")
    return None


# ── LLM ────────────────────────────────────────────────────────────────

def _resolve_music_llm_config(user_id, preset_id=None):
    """Résout la config LLM {base_url, api_key, model} depuis un preset.

    Réutilise la logique de enhance._resolve_preset (fallback perso/global) puis
    decrypt_api_key. Retourne None si aucun preset résolu.
    """
    conn = get_db()
    try:
        preset = _resolve_preset(conn, preset_id, user_id)
        if isinstance(preset, tuple):
            logging.warning(f"[music3] preset resolution failed: {preset}")
            return None
        api_key = decrypt_api_key(preset['api_key_encrypted'])
        base_url = preset['base_url'].rstrip('/')
        model = preset['model']
        return {'base_url': base_url, 'api_key': api_key, 'model': model}
    except Exception:
        logging.exception("[music3] preset resolution exception")
        return None
    finally:
        conn.close()


def _call_music_llm(user_id, system_prompt, user_prompt, seed=0, temperature=0.4, preset_id=None):
    """Appelle le LLM via enhance._call_llm_internal et retourne le texte de sortie.

    Construit un llm_request OpenAI standard ({model, messages, temperature}),
    ajoute 'seed' si seed>0, puis délègue à _call_llm_internal (retry Ollama).
    """
    llm_config = _resolve_music_llm_config(user_id, preset_id)
    if not llm_config:
        raise RuntimeError("Aucun preset IA résolu pour l'appel LLM music3")
    llm_request = {
        'model': llm_config['model'],
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
    }
    if seed and seed > 0:
        llm_request['seed'] = int(seed)
    try:
        result = _call_llm_internal(llm_request, llm_config)
        content = result['choices'][0]['message']['content'].strip()
        logging.warning(f"[music3] LLM ok model={llm_config['model']} len={len(content)}")
        return content
    except Exception as e:
        logging.warning(f"[music3] LLM exception: {e!r}")
        raise


def _parse_json_strip(text):
    """Parse un JSON en retirant les code fences markdown si présents. None sinon."""
    if not text:
        return None
    s = text.strip()
    m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        logging.warning("[music3] JSON parse failed")
        return None


def _parse_duration(text):
    """Parse une durée en secondes depuis un texte. 0 si invalide.

    Accepte soit un marqueur 'DURATION: N' / 'duration: N', soit un simple entier
    isolé (le premier nombre 30..900 rencontré, borné pour éviter de lire un id
    ou un token). Retourne 0 si rien de valide.
    """
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
    # Premier entier isolé (borne réaliste 30..900 s) — le LLM a pu répondre '180' seul.
    m3 = re.search(r'(?<!\d)(\d{2,3})(?!\d)', text)
    if m3:
        try:
            val = int(m3.group(1))
        except ValueError:
            return 0
        if 30 <= val <= 900:
            return val
    return 0


def _is_instrumental(brief, musique=""):
    """Détecte si le morceau est instrumental (pas de voix/chant).

    Sources : description texte, brief (champ vocal / instrumental).
    """
    if musique and re.search(r'\binstrumental\b|no\s+vocal|no\s+sing|instrumental-only|sans\s+voix', musique, re.IGNORECASE):
        return True
    if isinstance(brief, dict):
        if brief.get("instrumental") is True:
            return True
        v = str(brief.get("vocal") or "").lower()
        if v in ("instrumental", "none", "no vocals", "none (instrumental)", "n/a", "absent", "aucune"):
            return True
    return False


# ── Pipeline en 5 étapes ───────────────────────────────────────────────

def _step1_music_brief(user_id, musique, lyrics, seed, preset_id):
    """Etape 1 : construit un brief structuré JSON depuis la description + lyrics."""
    user = musique
    if lyrics and lyrics.strip():
        user = f"{musique}\n\nLyrics:\n{lyrics}"
    content = _call_music_llm(user_id, STEP1_SYSTEM_PROMPT, user, seed=seed, preset_id=preset_id)
    data = _parse_json_strip(content)
    if isinstance(data, dict):
        return data
    return {}


def _step2_route(user_id, brief, genre_router_text, seed, preset_id):
    """Etape 2 : sélectionne 1-2 familles de styles depuis genre-router.md."""
    system = f"{genre_router_text}\n\n{STEP2_SYSTEM_PROMPT}"
    user = json.dumps(brief, ensure_ascii=False)
    content = _call_music_llm(user_id, system, user, seed=seed, preset_id=preset_id)
    familles = [ln.strip() for ln in (content or '').splitlines() if ln.strip()]
    return familles


def _step3_select(user_id, brief, family_index_text, seed, preset_id):
    """Etape 3 : sélectionne <=3 chemins de templates depuis l'index de famille."""
    user = f"Brief:\n{json.dumps(brief, ensure_ascii=False)}\n\nFamily index:\n{family_index_text}"
    content = _call_music_llm(user_id, STEP3_SYSTEM_PROMPT, user, seed=seed, preset_id=preset_id)
    paths = [ln.strip() for ln in (content or '').splitlines() if ln.strip()]
    return paths[:3]


def _step4_caption(user_id, brief, templates_text, seed, preset_id):
    """Etape 4 : écrit la 'Music 3.0 Structured Caption' (3 sections), temp basse."""
    system = f"{STEP4_SYSTEM_PROMPT}\n\nReference templates:\n{templates_text}"
    user = json.dumps(brief, ensure_ascii=False)
    return _call_music_llm(user_id, system, user, seed=seed, temperature=0.2, preset_id=preset_id) or ""


def _step5_duration_and_lyrics(user_id, brief, caption, user_lyrics, musique, seed, preset_id):
    """Etape 5 : estime la durée (secondes) et fournit les paroles.

    - duration : parse un entier en secondes depuis la réponse (0 par défaut).
    - lyrics   : si instrumental -> structure [Instrumental] sans sections vocales ;
      sinon si user_lyrics non vide -> conservées ; sinon générées [Verse][Chorus].
    """
    duration = 0
    lyrics = ""
    # Contexte commun.
    user = (
        f"Brief:\n{json.dumps(brief, ensure_ascii=False)}\n\n"
        f"Caption:\n{caption}"
    )
    instrumental = _is_instrumental(brief, user_lyrics if not musique else musique)
    # Toujours estimer la durée via un appel LLM dédié (brief + caption).
    try:
        dur_content = _call_music_llm(
            user_id, STEP5_DURATION_SYSTEM_PROMPT, user, seed=seed, preset_id=preset_id
        ) or ""
        duration = _parse_duration(dur_content)
    except Exception:
        logging.warning("[music3] step5 duration estimation failed")
        duration = 0
    # Paroles : si instrumental, structure sans sections vocales (le skill : do not add vocals).
    if instrumental:
        try:
            content = _call_music_llm(
                user_id, STEP5_INSTRUMENTAL_LYRICS_SYSTEM_PROMPT, user, seed=seed, preset_id=preset_id
            ) or ""
            lyrics = content.strip()
        except Exception:
            logging.warning("[music3] step5 instrumental lyrics failed")
            lyrics = "[Instrumental]"
    elif user_lyrics and user_lyrics.strip():
        # Paroles fournies : on les conserve telles quelles.
        lyrics = user_lyrics
    else:
        try:
            content = _call_music_llm(
                user_id, STEP5_LYRICS_SYSTEM_PROMPT, user, seed=seed, preset_id=preset_id
            ) or ""
            content = re.sub(r'^DURATION\s*[:=]\s*\d+.*$', '', content, flags=re.MULTILINE | re.IGNORECASE).strip()
            lyrics = content
        except Exception:
            logging.warning("[music3] step5 lyrics generation failed")
            lyrics = ""
    return duration, lyrics


def _build_fallback_caption(brief):
    """Fallback sans routage : liste les fichiers du cache comme templates."""
    files = _list_md_files()
    if not files:
        return "No reference templates available in cache."
    lines = ["# Available reference templates", ""]
    for f in files[:10]:
        content = _read_reference(f)
        if content:
            lines.append(f"## {f}\n{content[:2000]}")
    return "\n\n".join(lines)


def _run_pipeline(user_id, musique, lyrics, seed=0, preset_id=None):
    """Orchestration séquentielle des 5 étapes. Retourne {caption, lyrics, duration_seconds}."""
    brief = {}
    caption = ""
    final_lyrics = ""
    duration_seconds = 0

    # Etape 1 : brief
    try:
        brief = _step1_music_brief(user_id, musique, lyrics, seed, preset_id)
    except Exception:
        logging.warning("[music3] step1 (brief) failed")
        brief = {}

    # Etapes 2-4 : routage + sélection + caption (avec fallback propre)
    try:
        router = _read_reference("genre-router.md")
        if router:
            familles = _step2_route(user_id, brief, router, seed, preset_id)
            if familles:
                index_parts = []
                for fam in familles:
                    idx = _read_reference(f"families/{fam}/index.md")
                    if idx:
                        index_parts.append(f"--- {fam} ---\n{idx}")
                combined_index = "\n\n".join(index_parts)
                if combined_index:
                    selected = _step3_select(user_id, brief, combined_index, seed, preset_id)
                    template_parts = []
                    for rel in selected:
                        if not rel:
                            continue
                        t = _read_reference(rel)
                        if t:
                            template_parts.append(f"--- {rel} ---\n{t}")
                    templates_text = "\n\n".join(template_parts)
                    caption = _step4_caption(user_id, brief, templates_text, seed, preset_id)
    except Exception:
        logging.exception("[music3] routing pipeline failed, falling back to direct caption")

    # Fallback : pas de routage possible -> caption direct sans routage
    if not caption:
        try:
            fallback = _build_fallback_caption(brief)
            caption = _step4_caption(user_id, brief, fallback, seed, preset_id)
        except Exception:
            logging.exception("[music3] fallback caption failed")
            caption = ""

    # Etape 5 : durée + paroles
    try:
        duration_seconds, final_lyrics = _step5_duration_and_lyrics(
            user_id, brief, caption, lyrics, musique, seed, preset_id
        )
    except Exception:
        logging.exception("[music3] step5 (duration/lyrics) failed")
        duration_seconds = 0
        final_lyrics = lyrics if lyrics and lyrics.strip() else ""

    return {
        'caption': caption,
        'lyrics': final_lyrics,
        'duration_seconds': duration_seconds,
    }


# ── Sync du cache depuis GitHub ────────────────────────────────────────

def _find_extracted_root(tmpdir):
    """Retrouve le dossier MUSIC3_REPO_SUBPATH dans l'archive extraite.

    L'archive contient un dossier racine (ex: MiniMax-Music3-main) ; on cherche
    dedans skills/music-caption-rewriter/references.
    """
    for name in os.listdir(tmpdir):
        full = os.path.join(tmpdir, name)
        if os.path.isdir(full):
            cand = os.path.join(full, MUSIC3_REPO_SUBPATH)
            if os.path.isdir(cand):
                return cand
    return None


def sync_music3_cache(force=False):
    """Télécharge et installe le cache de références MiniMax Music3.

    - Si not force et app_settings['music3_cache_last_updated'] < 24h -> skip.
    - Sinon télécharge MUSIC3_TARBALL, extrait, copie le subpath vers
      MUSIC3_REFS_DIR, régénère manifest.json + metadata.json et met à jour
      app_settings['music3_cache_last_updated'].
    """
    import requests

    # --- Skip si déjà à jour et pas forcé ---
    if not force:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'music3_cache_last_updated'"
            ).fetchone()
            last = row[0] if row else None
        finally:
            conn.close()
        if last:
            try:
                dt = datetime.fromisoformat(last)
                if (datetime.now(timezone.utc) - dt) < timedelta(hours=24):
                    return {'skipped': True, 'last_updated': last}
            except Exception:
                pass

    _music3_refs_root()
    tmpdir = tempfile.mkdtemp(prefix='music3_')
    try:
        resp = requests.get(MUSIC3_TARBALL, timeout=(10, 120), stream=True)
        resp.raise_for_status()
        tarpath = os.path.join(tmpdir, 'repo.tar.gz')
        with open(tarpath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        with tarfile.open(tarpath, 'r:gz') as tar:
            tar.extractall(tmpdir)

        src = _find_extracted_root(tmpdir)
        if not src:
            raise RuntimeError("references subpath not found in tarball")

        # Remplacement du cache
        shutil.rmtree(MUSIC3_REFS_DIR, ignore_errors=True)
        shutil.copytree(src, MUSIC3_REFS_DIR)

        files = _list_md_files()
        last_updated = datetime.now(timezone.utc).isoformat()

        (MUSIC3_DATA_DIR / 'manifest.json').write_text(
            json.dumps({'files': files, 'last_updated': last_updated}, ensure_ascii=False),
            encoding='utf-8',
        )
        (MUSIC3_DATA_DIR / 'metadata.json').write_text(
            json.dumps({'last_updated': last_updated, 'files': len(files)}, ensure_ascii=False),
            encoding='utf-8',
        )

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('music3_cache_last_updated', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (last_updated,),
            )
            conn.commit()
        finally:
            conn.close()

        return {'status': 'ok', 'files': len(files), 'last_updated': last_updated}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Scheduler in-process (remplace le cron externe) ─────────────────────

def start_music3_refresh_scheduler():
    """Démarre un thread daemon qui rafraîchit le cache music3 à intervalle régulier.

    Lit la config app_settings :
      - music3_refresh_enabled        : '1' = activé (défaut), sinon '0' -> le scheduler ne démarre pas.
      - music3_refresh_interval_hours : intervalle entre les contrôles (défaut 24h).

    Le cache a déjà sa propre date-check interne (sync_music3_cache(force=False)
    skip si app_settings['music3_cache_last_updated'] < 24h), donc le thread peut
    appeler sync_music3_cache() sans force : la vraie fréquence de rafraîchissement
    reste ~24h même si le contrôle est fréquent.

    Réplique le pattern de storage.start_backup_scheduler : thread daemon avec
    while True + time.sleep + try/except logging.
    """
    import threading
    import time

    interval_hours = 24
    enabled = '1'
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'music3_refresh_interval_hours'"
            ).fetchone()
            if row and row[0]:
                try:
                    interval_hours = int(row[0])
                except (TypeError, ValueError):
                    interval_hours = 24
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'music3_refresh_enabled'"
            ).fetchone()
            if row and row[0]:
                enabled = row[0]
        finally:
            conn.close()
    except Exception:
        logging.exception("[music3] failed to read scheduler config, using defaults")

    if enabled != '1':
        logging.info("[music3] Scheduler disabled (music3_refresh_enabled=0)")
        return

    if interval_hours <= 0:
        interval_hours = 24

    def _run():
        # Refresh immédiat au lancement (respecte la date-check interne <24h)
        try:
            sync_music3_cache()
        except Exception:
            logging.exception("[music3] initial cache refresh failed")
        while True:
            time.sleep(interval_hours * 3600)
            try:
                sync_music3_cache()
            except Exception:
                logging.exception("[music3] scheduled cache refresh failed")

    t = threading.Thread(target=_run, daemon=True, name="aih-music3-refresh")
    t.start()
    logging.info(f"[music3] Scheduler started (every {interval_hours}h)")


# ── Endpoints ──────────────────────────────────────────────────────────

@app.route('/api/music3/reference/<path:path>', methods=['GET'])
def music3_reference(path):
    """Sert un fichier texte du cache de références (text/plain). 404 si absent."""
    guard = _login_required()
    if guard:
        return guard

    if path == 'manifest.json':
        p = _manifest_path()
        if p and p.is_file():
            return send_file(str(p), mimetype='application/json')
        return jsonify({'error': 'manifest non trouvé'}), 404

    p = _resolve_reference(path)
    if p is None:
        return jsonify({'error': 'référence introuvable'}), 404
    return send_file(str(p), mimetype='text/plain')


@app.route('/api/music3/manifest', methods=['GET'])
def music3_manifest():
    """Retourne {files, last_updated} — liste des .md du cache."""
    guard = _login_required()
    if guard:
        return guard
    files = _list_md_files()
    return jsonify({'files': files, 'last_updated': _read_last_updated()})


@app.route('/api/music3/generate', methods=['POST'])
def music3_generate():
    """Mode cloud : orchestre le pipeline complet. Body {musique, lyrics, seed, preset_id}."""
    guard = _login_required()
    if guard:
        return guard
    rl = _check_rate_limit("music3_generate", max_calls=20, window_seconds=60)
    if rl:
        return rl
    user_id = _get_current_user_id()
    data = request.get_json() or {}
    musique = (data.get('musique') or '').strip()
    if not musique:
        return jsonify({'error': 'musique requis'}), 400
    lyrics = data.get('lyrics') or ''
    try:
        seed = int(data.get('seed', 0) or 0)
    except (TypeError, ValueError):
        seed = 0
    preset_id = data.get('preset_id')

    result = _run_pipeline(user_id, musique, lyrics, seed, preset_id)
    return jsonify(result)


@app.route('/api/music3/refresh', methods=['POST'])
def music3_refresh():
    """Force la resynchronisation du cache de références (admin seulement)."""
    guard = _admin_required()
    if guard:
        return guard
    info = sync_music3_cache(force=True)
    return jsonify(info)
