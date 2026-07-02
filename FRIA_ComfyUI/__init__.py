# ⛔ FICHIER MORT — N'EST JAMAIS EXECUTE PAR COMFYUI ⛔
#
# Le vrai point d'entree est FRIA_Tools/__init__.py (le dossier parent).
# ComfyUI charge UNIQUEMENT le __init__.py a la racine du dossier dans
# custom_nodes/. Ce fichier-ci est dans un sous-dossier FRIA_ComfyUI/.
#
# Le root __init__.py charge chaque module individuellement via
# _load_module() et construit NODE_CLASS_MAPPINGS a partir des classes.
# NE PAS ajouter de routes, d'imports critiques ou de logique ici —
# tout doit etre dans le root __init__.py.
#
# Ce fichier sert uniquement de documentation/reference.

from .nodes.elements_node import FRIAElementsNode
from .nodes.enhance_node import FRIAEnhanceNode
from .nodes.ideogram4_node import FRIAIdeogram4Node
from .nodes.ideogram_prep_node import FRIAIdeogramPrepNode
from .nodes.ideogram_parse_node import FRIAIdeogramParseNode
from .nodes.prep_node import FRIAPromptPrepNode

from .nodes.diagnostic_node import FRIADiagnosticNode

NODE_CLASS_MAPPINGS = {
    "FRIAElementsNode": FRIAElementsNode,
    "FRIAEnhanceNode": FRIAEnhanceNode,
    "FRIAIdeogram4Node": FRIAIdeogram4Node,
    "FRIAIdeogramPrepNode": FRIAIdeogramPrepNode,
    "FRIAIdeogramParseNode": FRIAIdeogramParseNode,
    "FRIAPromptPrepNode": FRIAPromptPrepNode,
    "FRIADiagnosticNode": FRIADiagnosticNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FRIAElementsNode": "FR.IA Elements Picker",
    "FRIAEnhanceNode": "FR.IA Prompt Enhancer",
    "FRIAIdeogram4Node": "FR.IA Ideogram 4 Builder",
    "FRIAIdeogramPrepNode": "FR.IA Ideogram Prep",
    "FRIAIdeogramParseNode": "FR.IA Ideogram Parse",
    "FRIAPromptPrepNode": "FR.IA Prompt Prep",
    "FRIADiagnosticNode": "FR.IA Diagnostic",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]