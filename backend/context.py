"""Shared imports and globals for AI-Helper backend route modules."""

import os
import sqlite3
import io
import json
import random
import time
import secrets
from datetime import datetime, timedelta
from threading import Thread

from flask import request, jsonify, send_file, send_from_directory, session, redirect, render_template_string, g, Response
from cryptography.fernet import Fernet

from extensions import app, oauth, DB_PATH, MD_PATH, BASE_DIR
from parser import parse_markdown
from exporter import export_to_markdown
from embeddings import generate_embedding, cosine_similarity, is_available, set_config
from auth import make_discord_session, check_whitelist_access, get_user_guilds, get_guild_member, get_user_info, avatar_url, get_logged_user, create_jwt, create_refresh_token, verify_jwt, jwt_required

from routes.helpers import (
    _login_required, _admin_required, _get_current_user_id, _authenticate_via_token,
    _sync_session_user, get_db, _init_db, _row_get,
    encrypt_api_key, decrypt_api_key, is_admin, is_kw_editor, _kw_editor_required,
    _privacy_filter, _regenerate_keyword_embedding, _generate_all_embeddings,
    _get_ollama_config, _check_rate_limit, _require_json,
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
    '_authenticate_via_token', '_sync_session_user',
    'get_db', '_init_db', '_row_get',
    'encrypt_api_key', 'decrypt_api_key', 'is_admin', 'is_kw_editor', '_kw_editor_required', '_privacy_filter', '_regenerate_keyword_embedding', '_generate_all_embeddings',
    '_get_ollama_config', '_check_rate_limit', '_require_json',
]
