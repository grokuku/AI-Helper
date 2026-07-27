"""Database initialization: schema creation, migrations, and default seeds."""

import sqlite3
import json

from extensions import DB_PATH
from db import get_db


# ── Table creation ─────────────────────────────────────────────────────

def _create_tables(conn):
    """Crée toutes les tables de base si elles n'existent pas.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """

    conn.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            description TEXT NOT NULL,
            section_id TEXT,
            section_title TEXT,
            subsection_id TEXT,
            subsection_title TEXT,
            nsfw INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_embeddings (
            keyword_id INTEGER PRIMARY KEY,
            embedding TEXT NOT NULL,
            FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            display_name TEXT,
            avatar TEXT,
            role TEXT DEFAULT 'user',
            settings TEXT DEFAULT '{}',
            guild_nickname TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_whitelist (
            discord_uid TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saved_filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            nsfw INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 0,
            config TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_cache (
            filter_id INTEGER NOT NULL,
            keyword_id INTEGER NOT NULL,
            PRIMARY KEY (filter_id, keyword_id),
            FOREIGN KEY (filter_id) REFERENCES saved_filters(id) ON DELETE CASCADE,
            FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE
        )
    """)

    # === Nouvelles tables (Phase 1 — Prompt Generator/Enhancer) ===
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            name TEXT NOT NULL,
            engine TEXT DEFAULT 'openai',
            base_url TEXT NOT NULL DEFAULT '',
            api_key_encrypted TEXT DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            is_global INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            style_text TEXT NOT NULL DEFAULT '',
            negative_prompt TEXT DEFAULT '',
            is_public INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            prompt_text TEXT NOT NULL,
            author_id TEXT NOT NULL,
            rating INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (template_id) REFERENCES prompt_templates(id),
            FOREIGN KEY (author_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_example_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            vote INTEGER NOT NULL CHECK(vote IN (-1, 1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (prompt_example_id) REFERENCES prompt_examples(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            preset_id INTEGER,
            template_id INTEGER,
            input_text TEXT NOT NULL DEFAULT '',
            output_text TEXT NOT NULL DEFAULT '',
            negative_prompt TEXT DEFAULT '',
            style_id INTEGER,
            model_used TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (preset_id) REFERENCES ai_presets(id),
            FOREIGN KEY (template_id) REFERENCES prompt_templates(id),
            FOREIGN KEY (style_id) REFERENCES styles(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            name TEXT NOT NULL,
            output_format TEXT NOT NULL DEFAULT 'text',
            system_prompt TEXT NOT NULL DEFAULT '',
            examples TEXT NOT NULL DEFAULT '[]',
            is_default INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)


# ── Migrations ────────────────────────────────────────────────────────

def _migrate_keywords(conn):
    """Migrations pour la table keywords.

    Ajoute les colonnes ``user_id``, ``privacy_status``, ``reviewed_by``,
    ``reviewed_at``, ``review_notes`` et ``created_at`` si absentes.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_kw = [r[1] for r in conn.execute("PRAGMA table_info(keywords)").fetchall()]

    if "user_id" not in cols_kw:
        conn.execute("ALTER TABLE keywords ADD COLUMN user_id TEXT REFERENCES users(id)")

    # Migration : colonnes privacy_status et review
    for col, default in [
        ("privacy_status", "'public'"),
        ("reviewed_by", "NULL"),
        ("reviewed_at", "NULL"),
        ("review_notes", "NULL"),
    ]:
        if col not in cols_kw:
            conn.execute(f"ALTER TABLE keywords ADD COLUMN {col} TEXT DEFAULT {default}")

    # created_at : SQLite n'accepte pas CURRENT_TIMESTAMP comme DEFAULT dans ALTER TABLE
    if "created_at" not in cols_kw:
        conn.execute("ALTER TABLE keywords ADD COLUMN created_at TEXT")
        conn.execute("UPDATE keywords SET created_at = datetime('now') WHERE created_at IS NULL")


def _migrate_users(conn):
    """Migrations pour la table users.

    Ajoute les colonnes ``role``, ``settings``, ``guild_nickname`` et
    ``api_token`` si absentes.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_users = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    for col, default in [("role", "'user'"), ("settings", "'{}'"), ("guild_nickname", "NULL"), ("api_token", "NULL")]:
        if col not in cols_users:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}")


def _migrate_presets(conn):
    """Migrations pour la table ai_presets.

    Ajoute la colonne ``is_client_side`` si absente.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_presets = [r[1] for r in conn.execute("PRAGMA table_info(ai_presets)").fetchall()]
    if "is_client_side" not in cols_presets:
        conn.execute("ALTER TABLE ai_presets ADD COLUMN is_client_side INTEGER DEFAULT 0")


def _migrate_styles(conn):
    """Migrations pour la table styles.

    Ajoute la colonne ``negative_prompt`` si absente.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_styles = [r[1] for r in conn.execute("PRAGMA table_info(styles)").fetchall()]
    if "negative_prompt" not in cols_styles:
        conn.execute("ALTER TABLE styles ADD COLUMN negative_prompt TEXT DEFAULT ''")


def _migrate_prompt_templates(conn):
    """Migrations pour la table prompt_templates.

    - Ajoute les colonnes ``name`` / ``is_public`` si absentes.
    - Supprime l'ancienne colonne ``prompt_type`` via table rebuild
      (passage à l'ID unique).

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_tmpl = [r[1] for r in conn.execute("PRAGMA table_info(prompt_templates)").fetchall()]
    if "name" not in cols_tmpl:
        conn.execute("ALTER TABLE prompt_templates ADD COLUMN name TEXT DEFAULT ''")
    if "is_public" not in cols_tmpl:
        conn.execute("ALTER TABLE prompt_templates ADD COLUMN is_public INTEGER DEFAULT 0")

    if "prompt_type" in cols_tmpl:
        conn.execute("UPDATE prompt_templates SET name = prompt_type WHERE name IS NULL OR name = ''")
        conn.execute("""
            CREATE TABLE _prompt_templates_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                name TEXT NOT NULL,
                output_format TEXT NOT NULL DEFAULT 'text',
                system_prompt TEXT NOT NULL DEFAULT '',
                examples TEXT NOT NULL DEFAULT '[]',
                is_default INTEGER DEFAULT 0,
                is_public INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            INSERT INTO _prompt_templates_new
                (id, user_id, name, output_format, system_prompt, examples, is_default, is_public, created_at, updated_at)
            SELECT id, user_id, COALESCE(NULLIF(name, ''), prompt_type),
                   output_format, system_prompt, examples, is_default, is_public, created_at, updated_at
            FROM prompt_templates
        """)
        conn.execute("DROP TABLE prompt_templates")
        conn.execute("ALTER TABLE _prompt_templates_new RENAME TO prompt_templates")


def _migrate_generated_prompts(conn):
    """Migrations pour la table generated_prompts.

    Supprime l'ancienne colonne ``prompt_type`` via table rebuild
    (remplacement par ``template_id``).

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_gp = [r[1] for r in conn.execute("PRAGMA table_info(generated_prompts)").fetchall()]
    if "prompt_type" in cols_gp:
        conn.execute("""
            CREATE TABLE _generated_prompts_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                preset_id INTEGER,
                template_id INTEGER,
                input_text TEXT NOT NULL DEFAULT '',
                output_text TEXT NOT NULL DEFAULT '',
                negative_prompt TEXT DEFAULT '',
                style_id INTEGER,
                model_used TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (preset_id) REFERENCES ai_presets(id),
                FOREIGN KEY (template_id) REFERENCES prompt_templates(id),
                FOREIGN KEY (style_id) REFERENCES styles(id)
            )
        """)
        conn.execute("""
            INSERT INTO _generated_prompts_new
                (id, user_id, preset_id, template_id, input_text, output_text, negative_prompt, style_id, model_used, created_at)
            SELECT id, user_id, preset_id, NULL, input_text, output_text, negative_prompt, style_id, model_used, created_at
            FROM generated_prompts
        """)
        conn.execute("DROP TABLE generated_prompts")
        conn.execute("ALTER TABLE _generated_prompts_new RENAME TO generated_prompts")


def _migrate_prompt_examples(conn):
    """Migrations pour la table prompt_examples.

    Supprime l'ancienne colonne ``type`` via table rebuild
    (remplacement par ``template_id``).

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_pe = [r[1] for r in conn.execute("PRAGMA table_info(prompt_examples)").fetchall()]
    if "type" in cols_pe:
        conn.execute("""
            CREATE TABLE _prompt_examples_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER,
                prompt_text TEXT NOT NULL,
                author_id TEXT NOT NULL,
                rating INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (template_id) REFERENCES prompt_templates(id),
                FOREIGN KEY (author_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            INSERT INTO _prompt_examples_new
                (id, template_id, prompt_text, author_id, rating, created_at)
            SELECT id, NULL, prompt_text, author_id, rating, created_at
            FROM prompt_examples
        """)
        conn.execute("DROP TABLE prompt_examples")
        conn.execute("ALTER TABLE _prompt_examples_new RENAME TO prompt_examples")


def _migrate_whitelist(conn):
    """Migration : crée la table discord_whitelist et auto-popule avec les users existants.

    Garantit que TOUS les utilisateurs actuels gardent leur accès lors du
    passage du système guild-based au système whitelist.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_whitelist (
            discord_uid TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Auto-populate : insérer tous les IDs existants de users dans la whitelist
    conn.execute("""
        INSERT OR IGNORE INTO discord_whitelist (discord_uid)
        SELECT id FROM users
        WHERE id NOT IN (SELECT discord_uid FROM discord_whitelist)
    """)


def _migrate_saved_filters(conn):
    """Migrations pour la table saved_filters.

    Ajoute la colonne ``filter_type`` (pour les filtres composés/union)
    si absente.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_filters = [r[1] for r in conn.execute("PRAGMA table_info(saved_filters)").fetchall()]
    if "filter_type" not in cols_filters:
        conn.execute("ALTER TABLE saved_filters ADD COLUMN filter_type TEXT DEFAULT 'simple'")


def _run_migrations(conn):
    """Exécute toutes les migrations dans le bon ordre.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    _migrate_keywords(conn)
    _migrate_users(conn)
    _migrate_presets(conn)
    _migrate_styles(conn)
    _migrate_prompt_templates(conn)
    _migrate_generated_prompts(conn)
    _migrate_prompt_examples(conn)
    _migrate_saved_filters(conn)
    _migrate_whitelist(conn)


# ── Post-migration table & index creation ─────────────────────────────

def _create_post_migration_tables(conn):
    """Crée les tables qui dépendent des migrations.

    Inclut : ``filter_unions``, ``enhance_sessions``, ``blobby_memories``,
    ``shared_workflows`` et ``file_uploads``.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_unions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            union_filter_id INTEGER NOT NULL,
            member_filter_id INTEGER NOT NULL,
            FOREIGN KEY (union_filter_id) REFERENCES saved_filters(id) ON DELETE CASCADE,
            FOREIGN KEY (member_filter_id) REFERENCES saved_filters(id) ON DELETE CASCADE,
            UNIQUE(union_filter_id, member_filter_id)
        )
    """)

    # Sessions de /api/enhance en mode client-side (LLM local).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enhance_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            state TEXT NOT NULL DEFAULT 'prepared',
            payload_json TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ── Table Blobby memories (mémoire vectorielle) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blobby_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'episode',
            content TEXT NOT NULL,
            embedding TEXT,
            importance INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ── Table Workflows partagés ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            workflow_data BLOB NOT NULL,
            decompressed_size INTEGER DEFAULT 0,
            required_nodes TEXT DEFAULT '[]',
            required_models TEXT DEFAULT '[]',
            required_loras TEXT DEFAULT '[]',
            is_public INTEGER DEFAULT 1,
            version INTEGER DEFAULT 1,
            downloads INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            tags TEXT DEFAULT '',
            comfyui_version TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ── Table File uploads (chunked upload pour gros fichiers) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_uploads (
            upload_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL,
            type TEXT NOT NULL,
            workflow_id INTEGER,
            chunk_size INTEGER NOT NULL,
            total_chunks INTEGER NOT NULL,
            received_chunks INTEGER DEFAULT 0,
            temp_path TEXT NOT NULL,
            final_path TEXT DEFAULT '',
            status TEXT DEFAULT 'uploading',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (workflow_id) REFERENCES shared_workflows(id) ON DELETE SET NULL
        )
    """)


def _migrate_shared_workflows(conn):
    """Migration : colonne ``thumbnail`` pour ``shared_workflows``.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_wf = [r[1] for r in conn.execute("PRAGMA table_info(shared_workflows)").fetchall()]
    if "thumbnail" not in cols_wf:
        conn.execute("ALTER TABLE shared_workflows ADD COLUMN thumbnail TEXT DEFAULT ''")


def _migrate_file_uploads(conn):
    """Migrations pour ``file_uploads`` : fingerprint + Model Browser.

    Ajoute les colonnes ``fingerprint_head``, ``fingerprint_tail``,
    ``downloads`` et ``display_name`` si absentes.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    cols_fu = [r[1] for r in conn.execute("PRAGMA table_info(file_uploads)").fetchall()]
    if "fingerprint_head" not in cols_fu:
        conn.execute("ALTER TABLE file_uploads ADD COLUMN fingerprint_head TEXT DEFAULT ''")
    if "fingerprint_tail" not in cols_fu:
        conn.execute("ALTER TABLE file_uploads ADD COLUMN fingerprint_tail TEXT DEFAULT ''")

    cols_fu = [r[1] for r in conn.execute("PRAGMA table_info(file_uploads)").fetchall()]
    if "downloads" not in cols_fu:
        conn.execute("ALTER TABLE file_uploads ADD COLUMN downloads INTEGER DEFAULT 0")
    if "display_name" not in cols_fu:
        conn.execute("ALTER TABLE file_uploads ADD COLUMN display_name TEXT DEFAULT ''")


# ── Indexes ────────────────────────────────────────────────────────────

def _create_indexes(conn):
    """Crée tous les index de la base de données.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enhance_sessions_user ON enhance_sessions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enhance_sessions_expires ON enhance_sessions(expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blobby_memories_user ON blobby_memories(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blobby_memories_type ON blobby_memories(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_user ON shared_workflows(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_public ON shared_workflows(is_public)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_uploads_user ON file_uploads(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_uploads_status ON file_uploads(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_uploads_fp ON file_uploads(size, fingerprint_head, fingerprint_tail)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_whitelist_uid ON discord_whitelist(discord_uid)")


# ── Default templates ────────────────────────────────────────────────

def _insert_default_templates(conn):
    """Insert default system templates for each prompt type × format combination.

    Crée un template par défaut pour chaque combinaison de type de prompt
    (SD1.5, SDXL, Flux, Anima, Qwen, Liste, Ideogram4) et de format de sortie
    (text, markdown, json). Ideogram4 est toujours en JSON.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
        La connexion n'est **pas** fermée ici — l'appelant gère le cycle de vie.
    """
    # ---- Per-type docs ----
    DOC_SD15 = """For Stable Diffusion 1.5, use Danbooru-style tags separated by commas.
Recommended structure (in order of importance):
1. Quality: masterpiece, best quality, ultra-detailed, high resolution
2. Subject: 1girl, 1boy, person, character_name
3. Appearance: long hair, blue eyes, casual clothes
4. Action/pose: standing, sitting, looking at viewer, walking
5. Environment: city street, forest, bedroom, outdoor
6. Lighting: cinematic lighting, soft light, moody lighting
7. Style: photorealistic, anime, sketch, oil painting
8. Technical: sharp focus, depth of field, bokeh

Use parentheses for weighting: (important:1.2) for +20% emphasis.
Use brackets to reduce: [less important:0.8].
Avoid full sentences, prefer short tokens."""

    DOC_SDXL = """For SDXL, use a mix of natural language and concise tags.
Structure:
1. Subject: natural description
2. Appearance: attire, expression (3-4 details max)
3. Environment: setting (1-2 elements)
4. Atmosphere: lighting, mood (1-2 elements)
5. Quality: 1-2 qualifiers max (masterpiece, photorealistic)

IMPORTANT:
- Limit to 20-25 tags or 2-3 sentences max
- Never repeat the same concept
- No artist names, no "trending on...", no "artstation"
- Prioritize quality over quantity"""

    DOC_FLUX = """For Flux (Black Forest Labs), use natural language descriptions.
Structure: Subject + Action + Style + Context
1. Subject: the main focus (person, object, character)
2. Action: what the subject is doing or their pose
3. Style: artistic approach, medium, aesthetic
4. Context: setting, lighting, time of day, mood

IMPORTANT: Flux does NOT use Danbooru tags or weights (parentheses).
Write natural, descriptive sentences.
No negative prompt - Flux does not support it.

Example: "A young woman with red hair standing in a sunlit garden, soft focus, cinematic lighting, professional photography"
"""

    DOC_ANIMA = """For Anime/Manga models, use Danbooru tags with specific suffixes.
Recommended structure:
1. Character: 1girl, 1boy, 2girls, etc.
2. Composition: solo, multiple views, group
3. Pose: standing, sitting, from behind, crouching
4. Expression: smile, blush, angry, serious
5. Appearance: long hair, twintails, red eyes, school uniform
6. Background: detailed background, simple background, gradient
7. Style: anime, flat color, lineart, cel shading, pixel art
8. Quality: masterpiece, best quality, highres

Use suffixes like (lineart), (flat color:1.2), (sketch).
Brackets [ ] also work to reduce emphasis."""

    DOC_QWEN = """For Qwen-Image (Alibaba, 20B MMDiT), use structured natural language descriptions.
Qwen-Image excels at:
- Multilingual text rendering (English, Chinese, Korean, Japanese...)
- Varied styles: photorealistic, painting, anime, minimalist design
- Complex scenes with characters, architecture, nature
- Precise image editing

Recommended format:
1. Subject: natural description (who, what, appearance)
2. Action/pose: what the subject is doing
3. Environment: setting, decor, background
4. Atmosphere: lighting, mood, time of day
5. Style: artistic approach, medium, quality

IMPORTANT:
- Use natural, descriptive sentences (like Flux)
- No Danbooru tags or weight parentheses
- Specify text rendering if needed (e.g. "a sign that reads '...'")
- Structure from general to specific
- Limit to 2-3 sentences or 30 elements max"""

    DOC_LISTE = """Format: structured list organized by categories.
Organize the prompt into relevant categories describing the image:

subject:
- [description of main subject, age, origin]

clothing:
- [description of clothing and accessories]

style:
- [artistic style, technique]

environment:
- [setting, decor, ambiance]

expression:
- [expression, pose, mood]

lighting:
- [lighting type, luminous atmosphere]

colors:
- [dominant palette, hues]

Every category should be relevant to the image being described.
Adapt categories based on content (e.g. architecture, nature, portrait...).
Tags should be concise but descriptive."""

    DOC_IDEOGRAM4 = """Ideogram 4 uses STRUCTURED JSON CAPTIONS. Output ONLY pure JSON, no code fences, no commentary.

## JSON Schema
{"high_level_description":"1-2 sentence summary","style_description":{"aesthetics":"2-4 keywords","lighting":"specific","photo" OR "art_style":"details","medium":"photograph/illustration/etc","color_palette":["#RRGGBB"]},"compositional_deconstruction":{"background":"scene backdrop","elements":[{"type":"obj","bbox":[y_min,x_min,y_max,x_max],"desc":"..."}]}}

Key order: high_level_description, style_description, compositional_deconstruction.
Use EITHER "photo" OR "art_style", never both.
In style_description: photo path = aesthetics, lighting, photo, medium, color_palette. Non-photo = aesthetics, lighting, medium, art_style, color_palette.
Required: aesthetics, lighting, medium. color_palette is optional.

doc_bboxes_rule (IMPORTANT): bbox format: [y_min, x_min, y_max, x_max] in PIXEL COORDINATES matching the IMAGE DIMENSIONS. Origin top-left. A bbox SURROUNDS the subject tightly. For a standing person: y_span > x_span (tall narrow). For a lying/diving person: x_span > y_span (wide short). NEVER make a standing person's bbox wider than it is tall.

USER-PROVIDED elements (from "ELEMENTS TO PLACE IN THE SCENE") MUST each appear in the output with a bbox, MUST be present in the final JSON. Main user-provided subject = largest bbox, centered.

Bboxes CAN overlap when it makes sense for the scene: a foreground element naturally has a bbox that partially covers a background element (e.g., a person standing in front of a wall, a tree in front of a mountain, a ball partially hidden behind a chair). This is NORMAL and ENCOURAGED when it reflects the actual depth of the scene.

You MAY add 1-5 ADDITIONAL bboxes for background/decorative/environmental elements that improve the rendering precision and the mise en page. Examples: architecture (wall, door, window), furniture (table, chair, shelf), nature (tree, mountain, cloud), atmosphere (light beam, fog, glow), small props (lamp, cup, plant, book). These extra elements:
- Can be of any type ("obj" for objects, "text" for rendered text)
- Can have bboxes that overlap with user-provided elements (foreground/background relationship)
- Can be small bboxes for fine details or large bboxes for environment zones
- Should describe what makes the scene more realistic and grounded

Adding context bboxes is ENCOURAGED when it helps Ideogram 4 understand the full scene (where the subject sits, what surrounds them, the light direction, etc.).

color_palette: array of UPPERCASE #RRGGBB strings. Up to 16 in style, up to 5 per element.
Element type: "obj" for subjects/objects, "text" for literal text rendered in image.
background is REQUIRED in compositional_deconstruction.

## Mapping user input to JSON
- General description -> high_level_description + style_description + background
- Each numbered element -> one entry in elements (type "obj", bbox from scene)
- IMAGE DIMENSIONS -> determines element layout: landscape = spread horizontally, portrait = stack vertically
- STYLE block -> preserved verbatim in style_description

## Tips
- medium: photograph, illustration, 3d_render, painting, anime...
- aesthetics: 2-4 keywords
- lighting: be specific (golden hour rim light, low-key deep shadows)
- photo: include camera details
- art_style: describe the look
- context bboxes: add 1-5 small/large bboxes for environment (wall, ground, sky, furniture, light source) to help Ideogram 4 anchor the scene realistically

## Example (barista scene, 3 elements, 1024x1024 image, bboxes in pixel coords)
Input: A medium-shot photograph of a barista pouring latte art in a cozy cafe. Elements: 1. A young barista with curly hair. 2. A porcelain cup with latte art. 3. An espresso machine. IMAGE DIMENSIONS: 1024x1024.
Output:
{"high_level_description":"A medium-shot photograph of a barista carefully pouring latte art in a warm, cozy cafe.","style_description":{"aesthetics":"warm, intimate, artisanal","lighting":"soft natural window light, gentle shadows","photo":"shallow depth of field, eye-level, 50mm lens","medium":"photograph","color_palette":["#F5E6D3","#6F4E37","#FFFFFF","#2C1810"]},"compositional_deconstruction":{"background":"A blurred cafe interior with warm wooden counters and hanging plants in soft focus.","elements":[{"type":"obj","bbox":[150,200,750,600],"desc":"A young barista with curly auburn hair, focused expression, wearing a cream apron."},{"type":"obj","bbox":[500,450,700,680],"desc":"A white porcelain cup with intricate rosetta latte art on a wooden saucer."},{"type":"obj","bbox":[100,650,450,1000],"desc":"A vintage brass espresso machine with steam rising, polished wood accents."}]}}
"""
    DOCS = {
        "sd15": DOC_SD15,
        "sdxl": DOC_SDXL,
        "flux": DOC_FLUX,
        "anima": DOC_ANIMA,
        "qwen": DOC_QWEN,
        "liste": DOC_LISTE,
        "ideogram4": DOC_IDEOGRAM4,
    }

    # ---- Output format rules ----
    FORMAT_RULES = {
        "text": "Output ONLY the final prompt, without quotes or code blocks. No explanations, no comments, no introductory sentences. Maximum 30 tags or 3 sentences. The prompt must be directly usable.",
        "markdown": "Output in plain Markdown. The prompt is the main content. Maximum 30 elements. No code blocks (```).",
        "json": "Output in pure JSON, WITHOUT a code block (no ```json). Format: {\"prompt\": \"...\", \"negative_prompt\": \"...\"}. Maximum 30 tags in the prompt.",
    }

    # ---- Common rules ----
    CONSIGNES = """
Mandatory rules:
- The assigned STYLE must be preserved exactly as-is
- In case of conflict, prioritize: base prompt > elements > random
- Remove duplicates
- Organize by importance (30 tags max)
- Add 2-3 qualifiers max (masterpiece, best quality)
- **DO NOT REPEAT** the same tags/concepts
- **OUTPUT ONLY the prompt**, nothing else: no explanations, no comments, no introductory sentences
- The prompt must be ready to use in an image generator"""

    # ---- Examples (short and targeted) ----
    EXAMPLES = {
        "sdxl": json.dumps([
            "A serene portrait of a young woman with auburn hair and freckles, golden hour sunlight, soft bokeh, cream linen dress, ethereal atmosphere, photorealistic",
            "Ancient ruined castle on a misty mountain peak, dramatic cloudy sky, rays of light, overgrown vines, cinematic composition, epic fantasy",
            "Steaming cup of coffee on rustic wooden table, morning light, shallow depth of field, steam particles, warm tones, product photography",
        ]),
        "sd15": json.dumps([
            "masterpiece, best quality, ultra-detailed, 1girl, solo, long blonde hair, blue eyes, white sundress, standing on beach, sunset, ocean waves, cinematic lighting, soft focus, photorealistic, sharp focus",
            "masterpiece, best quality, 1boy, short brown hair, glasses, casual clothes, sitting at desk, coffee shop, warm lighting, detailed background, bokeh, (photorealistic:1.2)",
            "masterpiece, high resolution, detailed, fantasy landscape, ruined castle, mountain peak, mist, dramatic sky, cinematic composition, (epic:1.3), intricate detail, sharp focus",
        ]),
        "flux": json.dumps([
            "A young woman with flowing red hair standing in a sunlit forest clearing, soft golden rays filtering through leaves, wearing a flowing white dress, ethereal atmosphere, professional photography, shallow depth of field",
            "A majestic medieval castle perched on a cliff edge at sunset, dramatic clouds with orange and purple hues, birds circling in the distance, cinematic wide shot, highly detailed, photorealistic",
            "A cozy library interior with floor-to-ceiling bookshelves, warm lamplight, an old leather armchair, dust particles dancing in the light, vintage atmosphere, ultra-detailed, architectural photography",
        ]),
        "anima": json.dumps([
            "masterpiece, best quality, 1girl, solo, long silver hair, purple eyes, serious expression, school uniform, standing, detailed background, sunset rooftop, cinematic lighting, anime style, highres",
            "masterpiece, 1boy, short black hair, katana, dynamic pose, action scene, glowing effects, detailed background, night city, (lineart:1.1), vibrant colors, anime shading",
            "best quality, 2girls, sitting, cafe, outdoor, daytime, smiling, casual clothes, detailed background, soft lighting, (cel shading:1.2), vibrant, highres",
        ]),
        "qwen": json.dumps([
            "A professional portrait of a young woman with freckles and auburn hair, wearing a cream linen shirt, standing in a sunlit garden with soft golden hour lighting, shallow depth of field, photorealistic style, 8k detail",
            "A magical forest scene with a wooden cottage nestled among giant glowing mushrooms, fireflies floating in the air, twilight atmosphere, a small sign reading 'Welcome Home' in elegant script, detailed foliage, fantasy illustration style",
            "A cinematic wide shot of a cyberpunk city street at night, neon signs in English and Japanese, rain-slicked pavement reflecting colorful lights, a lone figure with an umbrella walking, moody blue and pink lighting, ultra-realistic",
        ]),
        "liste": json.dumps([
            "subject:\n- woman, 28 years old, caucasian\n- long auburn hair\n- freckles\n\nclothing:\n- white flowing dress\n\nstyle:\n- photorealistic\n- masterpiece\n- 8k\n\nenvironment:\n- sunlit forest\n- rays of light\n\nlighting:\n- golden hour\n- soft bokeh\n\nexpression:\n- gentle gaze\n- slight smile",
            "subject:\n- man, 35 years old\n- short brown hair\n- glasses\n\nclothing:\n- gray suit\n- black tie\n\nstyle:\n- photorealistic\n- sharp focus\n\nenvironment:\n- modern office\n- floor-to-ceiling window\n\nlighting:\n- natural light\n- backlit\n\nexpression:\n- confident\n- looking at camera",
            "subject:\n- medieval castle\n- ancient ruins\n\nstyle:\n- epic fantasy\n- cinematic\n- highly detailed\n\nenvironment:\n- mountain peak\n- morning mist\n- dramatic clouds\n\ncolors:\n- warm tones\n- orange and purple\n\nmood:\n- mysterious\n- majestic",
        ]),
        "ideogram4": json.dumps([
            '{"high_level_description": "A medium-shot photograph of a barista carefully pouring latte art in a warm, cozy cafe.", "style_description": {"aesthetics": "warm, intimate, artisanal", "lighting": "soft natural window light, gentle shadows", "photo": "shallow depth of field, eye-level, 50mm lens", "medium": "photograph", "color_palette": ["#F5E6D3", "#6F4E37", "#FFFFFF", "#2C1810", "#D4A574"]}, "compositional_deconstruction": {"background": "A blurred cafe interior with warm wooden counters, hanging plants, and the suggestion of other patrons in soft focus.", "elements": [{"type": "obj", "bbox": [200, 250, 800, 700], "desc": "A young barista with curly auburn hair and a focused expression, wearing a cream apron over a dark shirt."}, {"type": "obj", "bbox": [550, 400, 750, 650], "desc": "A white porcelain cup with intricate rosetta latte art on a wooden saucer."}, {"type": "obj", "bbox": [100, 600, 500, 1000], "desc": "A vintage brass espresso machine with steam rising from its portafilter."}]}}',
            '{"high_level_description": "A lone sailboat on calm water at sunset.", "style_description": {"aesthetics": "serene, warm, golden hour", "lighting": "golden hour backlighting, warm atmospheric haze", "photo": "wide angle, f/8, long exposure", "medium": "photograph", "color_palette": ["#FF6B35", "#F7C59F", "#004E89", "#1A659E", "#2B2D42"]}, "compositional_deconstruction": {"background": "A calm ocean stretching to a low horizon, sky washed in orange and pink with thin wisps of cloud.", "elements": [{"type": "obj", "desc": "A single sailboat with a white triangular sail, silhouetted against the setting sun."}]}}',
            '{"high_level_description": "A clean, modern business card layout for a tech company.", "style_description": {"aesthetics": "minimal, professional, geometric", "lighting": "even, diffuse studio lighting", "medium": "graphic_design", "art_style": "flat vector design, generous whitespace, sans-serif typography", "color_palette": ["#FFFFFF", "#F0F0F0", "#333333", "#0066FF", "#00CC88"]}, "compositional_deconstruction": {"background": "A solid off-white card surface with subtle paper texture.", "elements": [{"type": "text", "text": "ACME TECH", "desc": "Bold dark grey sans-serif company name across the upper third of the card."}, {"type": "text", "text": "hello@acme.tech", "desc": "Small blue sans-serif contact email near the bottom of the card."}]}}',
        ]),
    }

    # ---- Insert default templates ----
    # Chaque template est identifie par son ID unique. Le `name` est
    # l'affichage humain ; `output_format` est le format de sortie attendu.
    for pt, doc in DOCS.items():
        examples = EXAMPLES.get(pt, "[]")
        # Ideogram 4 est forcement JSON ; les autres types ont 3 formats.
        formats = ["json"] if pt == "ideogram4" else ["text", "markdown", "json"]
        for fmt in formats:
            if pt == "ideogram4":
                fmt_rule = "Follow the EXACT JSON schema described above. Output PURE JSON, NO code block, NO commentary, NO markdown."
            else:
                fmt_rule = FORMAT_RULES.get(fmt, FORMAT_RULES["text"])
            name = pt.upper()
            if fmt != "text":
                name = f"{pt.upper()} {fmt.capitalize()}"
            system_prompt = f"""You are an expert assistant for image prompt generation.
Your task: transform the provided content into an optimized {pt.upper()} prompt.

## Documentation: How to build a {pt.upper()} prompt
{doc}

## Output format
{fmt_rule}

## Examples
Here are examples of well-structured {pt.upper()} prompts:
"""
            examples_list = json.loads(examples)
            for ex in examples_list:
                system_prompt += f"\n- {ex}"
            system_prompt += CONSIGNES

            conn.execute("""
                INSERT INTO prompt_templates
                    (user_id, name, output_format, system_prompt, examples, is_default)
                VALUES (NULL, ?, ?, ?, ?, 1)
            """, (name, fmt, system_prompt.strip(), examples))

    # ── Template de validation Ideogram 4 (template standard) ────────
    # (optionnel, n'est pas créé automatiquement)
    # Le template peut être créé manuellement dans l'onglet Templates.
    # Son system_prompt sera utilisé pour la passe 2 si validation_template_id est fourni.

    conn.commit()
    # Do NOT close — caller (_init_db) owns the connection lifecycle


# ── Seeds ──────────────────────────────────────────────────────────────

def _seed_defaults(conn):
    """Insère les templates par défaut si aucun n'existe.

    Args:
        conn (sqlite3.Connection): La connexion SQLite active.
    """
    existing = conn.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    if existing == 0:
        _insert_default_templates(conn)


# ── Main entry point ───────────────────────────────────────────────────

def _init_db():
    """Crée la base et les tables si elles n'existent pas.

    Orchestration :
        1. Création des tables de base
        2. Migrations (ALTER TABLE, rebuilds)
        3. Création des tables post-migration (dépendent des migrations)
        4. Migrations des nouvelles tables
        5. Création des index
        6. Seeds (templates par défaut)

    Raises:
        sqlite3.OperationalError: Si une opération de migration échoue.
    """
    new = not DB_PATH.exists()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")

    # 1. Tables de base
    _create_tables(conn)

    # 2. Migrations sur les tables existantes
    _run_migrations(conn)

    # 3. Tables post-migration (dépendent des migrations ci-dessus)
    _create_post_migration_tables(conn)

    # 4. Migrations sur les nouvelles tables
    _migrate_shared_workflows(conn)
    _migrate_file_uploads(conn)

    # 5. Index
    _create_indexes(conn)

    # 6. Seeds
    _seed_defaults(conn)

    conn.commit()
    conn.close()