import re
from pathlib import Path


def parse_markdown(filepath: str) -> list[dict]:
    """Parse le fichier markdown Keywords-Complete.md et retourne une liste de dictionnaires."""
    keywords = []
    lines = Path(filepath).read_text(encoding='utf-8').splitlines()

    current_section_id = ""
    current_section_title = ""
    current_subsection_id = ""
    current_subsection_title = ""
    nsfw = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Section principale : ## I. LA TÊTE ET LE VISAGE
        m = re.match(r'^##\s+([IVXLCDM]+)[\.\s]\s+(.*)$', line)
        if m:
            current_section_id = m.group(1)
            current_section_title = m.group(2).strip()
            nsfw = current_section_id == "VII"
            continue

        # Sous-section : ### I.A — Les Cheveux ...
        m = re.match(r'^###\s+([IVXLCDM]+\.[A-Z]+)\s*[—\-]\s+(.*)$', line)
        if m:
            current_subsection_id = m.group(1)
            current_subsection_title = m.group(2).strip()
            continue

        # Ligne de tableau avec mot-clé en backticks
        m = re.match(r'^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|$', line)
        if m:
            keyword = m.group(1).strip()
            description = m.group(2).strip()

            # Ignore les en-têtes
            if keyword.lower() in ('mot-clé', 'keyword'):
                continue

            keywords.append({
                'keyword': keyword,
                'description': description,
                'section_id': current_section_id or '',
                'section_title': current_section_title,
                'subsection_id': current_subsection_id or '',
                'subsection_title': current_subsection_title,
                'nsfw': nsfw
            })
            continue

        # Si le mot-clé est en gras (**) au lieu de backticks – fallback sécurisé
        m = re.match(r'^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.+?)\s*\|$', line)
        if m:
            keyword = m.group(1).strip()
            description = m.group(2).strip()
            if keyword.lower() in ('mot-clé', 'keyword'):
                continue
            keywords.append({
                'keyword': keyword,
                'description': description,
                'section_id': current_section_id or '',
                'section_title': current_section_title,
                'subsection_id': current_subsection_id or '',
                'subsection_title': current_subsection_title,
                'nsfw': nsfw
            })
            continue

    return keywords


if __name__ == '__main__':
    # Test rapide
    import json
    results = parse_markdown('../Keywords-Complete.md')
    print(f"Entrées trouvées : {len(results)}")
    print(json.dumps(results[:3], ensure_ascii=False, indent=2))
