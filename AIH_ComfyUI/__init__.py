# ⛔ FICHIER MORT — N'EST JAMAIS EXECUTE PAR COMFYUI ⛔
#
# Le vrai point d'entree est AIH_Tools/__init__.py (le dossier parent).
# ComfyUI charge UNIQUEMENT le __init__.py a la racine du dossier dans
# custom_nodes/. Ce fichier-ci est dans un sous-dossier AIH_ComfyUI/.
#
# Le root __init__.py charge chaque module individuellement via
# _load_module() et construit NODE_CLASS_MAPPINGS a partir des classes.
# NE PAS ajouter de routes, d'imports critiques ou de logique ici —
# tout doit etre dans le root __init__.py.
#
# Ce fichier sert uniquement de documentation/reference.

from .nodes.elements_node import AIHElementsNode
from .nodes.enhance_node import AIHEnhanceNode
from .nodes.ideogram4_node import AIHIdeogram4Node
from .nodes.diagnostic_node import AIHDiagnosticNode

NODE_CLASS_MAPPINGS = {
    "AIHElementsNode": AIHElementsNode,
    "AIHEnhanceNode": AIHEnhanceNode,
    "AIHIdeogram4Node": AIHIdeogram4Node,
    "AIHDiagnosticNode": AIHDiagnosticNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AIHElementsNode": "AIH Elements Picker",
    "AIHEnhanceNode": "AIH Prompt Enhancer",
    "AIHIdeogram4Node": "AIH Ideogram 4 Builder",
    "AIHDiagnosticNode": "AIH Diagnostic",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]