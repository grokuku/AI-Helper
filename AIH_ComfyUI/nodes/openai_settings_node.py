"""
AIH OpenAI Settings — Node de configuration pour API compatible OpenAI.
Couvre Ollama, OpenAI, DeepSeek, Mistral, Groq, etc.
"""
import json

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
        config = {
            "type": "openai",
            "base_url": base_url.strip().rstrip("/"),
            "api_key": api_key.strip() if api_key else "",
            "model": model.strip() if model else "",
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        return (json.dumps(config),)