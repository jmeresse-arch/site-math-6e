#!/usr/bin/env python3
"""Génère un faux PDF EduConnect (données fictives) pour tester generate_fiches.py."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

FAKE_STUDENTS = [
    ("MARTIN", "Lucas", "p7abc12345"),
    ("DURAND", "Emma", "p7def67890"),
    ("PETIT", "Nathan", "p7ghi13579"),
]


def main():
    out_path = Path(__file__).parent / "educonnect_exemple.pdf"
    styles = getSampleStyleSheet()
    story = []
    for nom, prenom, identifiant in FAKE_STUDENTS:
        story.append(Paragraph("Notification de création de compte EduConnect", styles["Title"]))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(f"Nom : {nom}", styles["Normal"]))
        story.append(Paragraph(f"Prénom : {prenom}", styles["Normal"]))
        story.append(Paragraph(f"Identifiant : {identifiant}", styles["Normal"]))
        story.append(PageBreak())
    if story:
        story.pop()  # pas de saut de page final
    doc = SimpleDocTemplate(str(out_path), pagesize=A4)
    doc.build(story)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
