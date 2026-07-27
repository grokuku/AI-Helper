"""AIH Diagnostic Node — Debug onExecuted et DOM widget."""
import json


class AIHDiagnosticNode:
    CATEGORY = "AIH"
    FUNCTION = "run"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            },
            "optional": {
                "_diag_json": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("diagnostic",)

    def run(self, seed, _diag_json="{}"):
        try:
            cfg = json.loads(_diag_json) if _diag_json else {}
        except json.JSONDecodeError:
            cfg = {}
        mode = cfg.get("mode", "hello")
        texts = {
            "hello": f"Hello from AIH Diagnostic! Seed: {seed}",
            "short": "Short text test.",
            "medium": "Medium length test with more characters.",
            "long": "x" * 1000,
            "special": "Special chars: éàü€ & <script>alert('xss')</script> 🎉",
        }
        text_val = texts.get(mode, f"Unknown mode: {mode}")
        return {
            "ui": {"diagnostic": [text_val]},
            "result": (text_val,)
        }
