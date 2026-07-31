"""
AIH LMStudio Settings — Node de configuration pour LM Studio local.
"""
import json

class AIHLMStudioSettingsNode:
    CATEGORY = "AIH"
    FUNCTION = "generate_config"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_identifier": ("STRING", {
                    "default": "",
                    "tooltip": "LM Studio model key. Leave empty for default/loaded model."
                }),
                "auto_unload": ("BOOLEAN", {"default": True}),
                "unload_delay": ("INT", {"default": 0, "min": 0, "max": 3600, "step": 1}),
                "max_tokens": ("INT", {"default": 1000, "min": 1, "max": 4096, "step": 1}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("AIH_LLM_CONFIG",)
    RETURN_NAMES = ("llm_config",)

    def generate_config(self, model_identifier, auto_unload, unload_delay, max_tokens, temperature):
        config = {
            "type": "lmstudio",
            "model": model_identifier.strip() if model_identifier else "",
            "auto_unload": bool(auto_unload),
            "unload_delay": int(unload_delay),
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        return (json.dumps(config),)