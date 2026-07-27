"""
AIH — Update Manager (côté ComfyUI).

Gere la mise a jour du repo Git AIH_Tools installe dans ComfyUI/custom_nodes/.
Independant du backend (qui est sur une autre machine) — on update juste
les fichiers locaux du repo pour avoir les dernieres nodes.

Methodes :
    - update_repo()     : git fetch + git reset --hard FETCH_HEAD
    - restart_server()  : os.execv dans un thread separe (inspire CUI-Holaf-Utils)
"""

import os
import sys
import time
import threading
import subprocess


def _find_repo_root(start_path):
    """Remonte l'arborescence depuis start_path jusqu'a trouver un dossier .git."""
    current = os.path.abspath(start_path)
    # On commence par le dossier parent (le repo contient AIH_ComfyUI/, donc
    # le .git est dans le parent, pas dans AIH_ComfyUI/)
    current = os.path.dirname(current)
    # securite : ne pas remonter au-dela de la racine du systeme
    while current and current != os.path.dirname(current):
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        current = os.path.dirname(current)
    return None


def update_repo():
    """
    Met a jour le repo via git fetch + git reset --hard FETCH_HEAD.
    Renvoie un dict avec le statut et le log complet.
    """
    # Trouver le repo : on est dans AIH_ComfyUI/update_manager.py,
    # le repo racine est 2 niveaux au-dessus
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = _find_repo_root(here)
    if not repo_root:
        return {
            "status": "error",
            "message": "Impossible de trouver le repo Git (.git introuvable en remontant l'arborescence).",
            "log": "",
            "updated": False,
        }

    log_lines = []
    log_lines.append(f"Repo: {repo_root}")
    log_lines.append("")

    # Etape 1 : verifier l'etat actuel (on veut savoir si on est en retard)
    try:
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=repo_root, timeout=10, encoding="utf-8", errors="replace",
        )
        before_hash = before.stdout.strip()
        log_lines.append(f"Avant : HEAD = {before_hash[:12]}")
    except Exception as e:
        return {
            "status": "error",
            "message": f"Impossible de lire HEAD : {e}",
            "log": "\n".join(log_lines),
            "updated": False,
        }

    # Etape 2 : git fetch origin
    log_lines.append("→ git fetch origin")
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True, text=True, cwd=repo_root, timeout=120, encoding="utf-8", errors="replace",
        )
        log_lines.append(fetch.stdout.strip())
        if fetch.stderr.strip():
            log_lines.append(fetch.stderr.strip())
        if fetch.returncode != 0:
            return {
                "status": "error",
                "message": "git fetch a echoue.",
                "log": "\n".join(log_lines),
                "updated": False,
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": "git fetch a timeout (120s).",
            "log": "\n".join(log_lines),
            "updated": False,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"git fetch erreur : {e}",
            "log": "\n".join(log_lines),
            "updated": False,
        }

    # Etape 3 : comparer FETCH_HEAD avec HEAD
    try:
        fetch_head = subprocess.run(
            ["git", "rev-parse", "FETCH_HEAD"],
            capture_output=True, text=True, cwd=repo_root, timeout=10, encoding="utf-8", errors="replace",
        )
        fetch_hash = fetch_head.stdout.strip()
        log_lines.append(f"Apres fetch : FETCH_HEAD = {fetch_hash[:12]}")

        if fetch_hash == before_hash:
            log_lines.append("")
            log_lines.append("✓ Deja a jour — aucune modification disponible.")
            return {
                "status": "ok",
                "message": "Deja a jour.",
                "log": "\n".join(log_lines),
                "updated": False,
                "before": before_hash,
                "after": before_hash,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Impossible de lire FETCH_HEAD : {e}",
            "log": "\n".join(log_lines),
            "updated": False,
        }

    # Etape 4 : git reset --hard FETCH_HEAD
    log_lines.append("")
    log_lines.append("→ git reset --hard FETCH_HEAD")
    try:
        reset = subprocess.run(
            ["git", "reset", "--hard", "FETCH_HEAD"],
            capture_output=True, text=True, cwd=repo_root, timeout=60, encoding="utf-8", errors="replace",
        )
        log_lines.append(reset.stdout.strip())
        if reset.stderr.strip():
            log_lines.append(reset.stderr.strip())
        if reset.returncode != 0:
            return {
                "status": "error",
                "message": "git reset a echoue.",
                "log": "\n".join(log_lines),
                "updated": False,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"git reset erreur : {e}",
            "log": "\n".join(log_lines),
            "updated": False,
        }

    # Etape 5 : resume des changements
    log_lines.append("")
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat", f"{before_hash}..{fetch_hash}"],
            capture_output=True, text=True, cwd=repo_root, timeout=10, encoding="utf-8", errors="replace",
        )
        if diff.stdout.strip():
            log_lines.append("Fichiers modifies :")
            log_lines.append(diff.stdout.strip())
        else:
            log_lines.append("(Aucun fichier different — strange)")
    except Exception:
        pass

    log_lines.append("")
    log_lines.append(f"✓ Mise a jour terminee : {before_hash[:12]} → {fetch_hash[:12]}")

    return {
        "status": "ok",
        "message": "Mise a jour reussie.",
        "log": "\n".join(log_lines),
        "updated": True,
        "before": before_hash,
        "after": fetch_hash,
    }


def _do_restart_blocking():
    """Redemarre ComfyUI via os.execv. Execute dans un thread pour laisser
    la reponse HTTP partir avant le restart."""
    time.sleep(1)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        # Log to stderr (visible in ComfyUI console) and try os._exit as fallback
        print(f"[AIH Update] CRITICAL: Restart via os.execv failed: {e}", file=sys.stderr)
        print(f"[AIH Update] Falling back to os._exit(1)", file=sys.stderr)
        os._exit(1)


def restart_server():
    """Planifie un restart de ComfyUI dans 1s. Renvoie immediatement."""
    threading.Thread(target=_do_restart_blocking, daemon=True).start()
    return {
        "status": "ok",
        "message": "Restart planifie (1s).",
    }
