import sqlite3
from pathlib import Path
from collections import OrderedDict


def export_to_markdown(db_path: str) -> str:
    """Exporte tous les mots-clés de la base vers une chaîne Markdown formatée.

    Args:
        db_path: Chemin vers le fichier de base de données SQLite.

    Returns:
        str: Le contenu Markdown généré, organisé par sections et sous-sections.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM keywords
        ORDER BY section_id, subsection_id, id
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "# 📚 Collection vide\n\n> Aucun mot-clé dans la base de données.\n"

    # Grouper : section -> subsection -> [rows]
    sections = OrderedDict()
    for row in rows:
        sec_id = row['section_id']
        sec_title = row['section_title']
        sub_id = row['subsection_id']
        sub_title = row['subsection_title']

        if sec_id not in sections:
            sections[sec_id] = {'title': sec_title, 'subsections': OrderedDict()}

        if sub_id not in sections[sec_id]['subsections']:
            sections[sec_id]['subsections'][sub_id] = {'title': sub_title, 'rows': []}

        sections[sec_id]['subsections'][sub_id]['rows'].append(row)

    md_lines = [
        "# 📚 Collection Complète de Mots-Clés pour Génération d'Images (IA / Danbooru / Illustrious)",
        "",
        "> **Source :** Base de données interne",
        "> **Catégories :" + " → ".join(f"{sec_id} {s['title']}" for sec_id, s in sections.items())[:100] + "...",
        "",
    ]

    for sec_id, sec_data in sections.items():
        md_lines.append(f"## {sec_id}. {sec_data['title']}")
        md_lines.append("")

        for sub_id, sub_data in sec_data['subsections'].items():
            md_lines.append(f"### {sub_id} — {sub_data['title']}")
            md_lines.append("")
            md_lines.append("| Mot-clé | Description |")
            md_lines.append("|---|---|")
            for row in sub_data['rows']:
                k = row['keyword'].replace('|', '\\|')
                d = row['description'].replace('|', '\\|')
                md_lines.append(f"| `{k}` | {d} |")
            md_lines.append("")

    # Footer
    md_lines.append("> **Note :** Généré automatiquement depuis la base de données.")
    md_lines.append("")
    return "\n".join(md_lines)


def export_to_file(db_path: str, output_path: str):
    """Exporte la base de mots-clés vers un fichier Markdown sur disque.

    Args:
        db_path: Chemin vers le fichier de base de données SQLite.
        output_path: Chemin du fichier Markdown de sortie à créer.
    """
    content = export_to_markdown(db_path)
    Path(output_path).write_text(content, encoding='utf-8')


if __name__ == '__main__':
    print(export_to_markdown('../keywords.db')[:1000])
