"""
AIH Preview Node — Envoie une image (tensor ComfyUI) au backend AIH
pour prévisualisation côté serveur. Node terminale (OUTPUT_NODE=True)
qui ne modifie pas l'image : elle agit comme un pass-through tout en
produisant un effet de bord (POST vers /api/preview).

L'api_key et l'URL du serveur sont lues depuis le fichier de credentials
ComfyUI (ComfyUI/user/default/aih/credentials.json) via le helper
_credentials — même pattern que enhance_node.py.
"""

import io
import logging

from . import _credentials


class AIHPreviewNode:
    """Node terminale qui envoie l'image reçue au backend AIH /api/preview."""

    CATEGORY = "AIH"
    FUNCTION = "preview"
    OUTPUT_NODE = True  # node terminale (effet de bord)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),  # tensor natif ComfyUI [B,H,W,C], values 0-1
            },
            "optional": {
                "llm_config": ("STRING", {"forceInput": True}),  # passthrough pour chaînage
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "llm_config")

    def preview(self, image, llm_config=None):
        # --- 1. Convertir le tensor IMAGE [B,H,W,C] en image PIL ---
        try:
            import torch
            import numpy as np
            from PIL import Image as PILImage

            # Prendre la première image du batch
            if hasattr(image, "cpu"):
                tensor = image[0].cpu()
            else:
                tensor = image[0]

            # Convertir en uint8 [H,W,C]
            arr = (tensor * 255.0).clamp(0, 255) if hasattr(tensor, "clamp") else np.clip(tensor * 255.0, 0, 255)
            arr = arr.to(torch.uint8) if hasattr(arr, "to") else arr.astype(np.uint8)
            arr_np = arr.numpy()

            pil_img = PILImage.fromarray(arr_np, mode="RGB")
        except Exception as e:
            logging.warning(f"[AIH Preview] Failed to convert tensor to PIL: {e}")
            # Ne pas crasher — retourner l'image telle quelle
            return {"result": (image, llm_config)}

        # --- 2. Convertir en PNG bytes ---
        try:
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception as e:
            logging.warning(f"[AIH Preview] Failed to encode PNG: {e}")
            return {"result": (image, llm_config)}

        # --- 3. Récupérer les credentials ---
        api_url = _credentials.get_api_url()
        api_key = _credentials.get_api_key()

        if not api_url:
            logging.warning("[AIH Preview] No api_url configured — skipping preview upload.")
            return {"result": (image, llm_config)}

        # --- 4. POST multipart/form-data vers /api/preview ---
        try:
            import requests

            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            files = {"file": ("preview.png", png_bytes, "image/png")}

            resp = requests.post(
                f"{api_url}/preview",
                headers=headers,
                files=files,
                timeout=30,
            )

            if resp.ok:
                logging.info(f"[AIH Preview] Upload OK ({resp.status_code}) — {len(png_bytes)} bytes sent.")
            else:
                logging.warning(
                    f"[AIH Preview] Upload failed: HTTP {resp.status_code} — {resp.text[:300]}"
                )
        except ImportError:
            logging.warning("[AIH Preview] module 'requests' manquant — skipping preview upload.")
        except Exception as e:
            logging.warning(f"[AIH Preview] Network error during upload: {e}")

        # --- 5. Retourner l'image et le llm_config (pass-through) ---
        return {"result": (image, llm_config)}