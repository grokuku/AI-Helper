"""Shared imports and globals for AI-Helper backend route modules."""

import io
import json
import os
import random
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from parser import parse_markdown
from threading import Thread

from auth import (
    avatar_url,
    check_whitelist_access,
    create_jwt,
    create_refresh_token,
    get_guild_member,
    get_logged_user,
    get_user_guilds,
    get_user_info,
    jwt_required,
    make_discord_session,
    verify_jwt,
)
from cryptography.fernet import Fernet
from embeddings import cosine_similarity, generate_embedding, is_available, set_config
from exporter import export_to_markdown
from extensions import BASE_DIR, DB_PATH, MD_PATH, app, oauth
from flask import (
    Response,
    g,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    session,
)
from routes.helpers import (
    _admin_required,
    _authenticate_via_token,
    _bootstrap_role,
    _check_rate_limit,
    _generate_all_embeddings,
    _get_current_user_id,
    _get_ollama_config,
    _init_db,
    _kw_editor_required,
    _login_required,
    _privacy_filter,
    _regenerate_keyword_embedding,
    _require_json,
    _row_get,
    _sync_session_user,
    decrypt_api_key,
    encrypt_api_key,
    get_db,
    is_admin,
    is_kw_editor,
)

# Force `from context import *` to import all these names (including underscore ones)
__all__ = [
    'app', 'oauth', 'DB_PATH', 'MD_PATH', 'BASE_DIR',
    'os', 'sqlite3', 'io', 'json', 'random', 'time', 'secrets',
    'datetime', 'timedelta', 'Thread', 'Fernet',
    'request', 'jsonify', 'send_file', 'send_from_directory', 'session',
    'redirect', 'render_template_string', 'g', 'Response',
    'parse_markdown', 'export_to_markdown',
    'generate_embedding', 'cosine_similarity', 'is_available', 'set_config',
    'make_discord_session', 'check_whitelist_access', 'get_user_guilds', 'get_guild_member',
    'get_user_info', 'avatar_url', 'get_logged_user',
    'create_jwt', 'create_refresh_token', 'verify_jwt', 'jwt_required',
    '_login_required', '_admin_required', '_get_current_user_id',
    '_authenticate_via_token', '_sync_session_user', '_bootstrap_role',
    'get_db', '_init_db', '_row_get',
    'encrypt_api_key', 'decrypt_api_key', 'is_admin', 'is_kw_editor', '_kw_editor_required', '_privacy_filter', '_regenerate_keyword_embedding', '_generate_all_embeddings',
    '_get_ollama_config', '_check_rate_limit', '_require_json',
]
