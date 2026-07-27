"""
Blobby Companion — A friendly blob character for the ComfyUI canvas.
100% Canvas 2D, zero external assets.
"""

# Blobby is a pure frontend extension (no Python nodes).
# The JS file registers itself as a ComfyUI extension and hooks into the canvas.
# Activation is controlled via the AIH menu toggle in aih_menu.js.

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
