"""
AIH OpenAI Settings — Node de configuration pour API compatible OpenAI.
Couvre Ollama, OpenAI, DeepSeek, Mistral, Groq, etc.
"""
import json
import logging

class AIHOpenAISettingsNode:
    CATEGORY = "AIH"
    FUNCTION = "generate_config"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "base_url": ("STRING", {
                    "default": "http://localhost:11434/v1",
                    "tooltip": "OpenAI-compatible API base URL. Examples: http://localhost:11434/v1 (Ollama), https://api.openai.com/v1, https://api.deepseek.com"
                }),
                "api_key": ("STRING", {
                    "default": "",
                    "tooltip": "API key. Leave empty for local servers (Ollama)."
                }),
                "model": ("STRING", {
                    "default": "",
                    "tooltip": "Model name. Examples: llama3:8b, gpt-4o, deepseek-chat"
                }),
                "max_tokens": ("INT", {"default": 1000, "min": 1, "max": 4096, "step": 1}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("AIH_LLM_CONFIG",)
    RETURN_NAMES = ("llm_config",)

    def generate_config(self, base_url, api_key, model, max_tokens, temperature):
        base = base_url.strip().rstrip("/")

        # Lire la clé API depuis le fichier local si pas fournie dans le widget
        key = api_key.strip() if api_key else ""
        if not key:
            try:
                import os, json
                # Utiliser folder_paths pour trouver le dossier utilisateur
                try:
                    import folder_paths
                    user_dir = folder_paths.get_user_directory()
                except Exception:
                    user_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user")
                keys_path = os.path.join(user_dir, "default", "aih", "openai_keys.json")
                if os.path.isfile(keys_path):
                    with open(keys_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    key = data.get(base, "")
            except Exception as e:
                logging.warning(f"[AIH OpenAI Settings] Failed to read API key: {e}")

        config = {
            "type": "openai",
            "base_url": base,
            "api_key": key,
            "model": model.strip() if model else "",
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        return (json.dumps(config),)