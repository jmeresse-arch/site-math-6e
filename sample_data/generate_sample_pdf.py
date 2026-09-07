#!/usr/bin/env python3
"""Génère un faux PDF ÉduConnect (données fictives) reproduisant la mise en page
réelle du courrier "Mise à disposition de votre compte ÉduConnect Élève",
pour tester generate_fiches.py sans utiliser de données d'élèves réelles."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

# (prénom, NOM, classe, identifiant, mot de passe provisoire) — tout est fictif
FAKE_STUDENTS = [
    ("Lucas", "MARTIN", "6A", "l.martin", "42V7ZJ7EXPC6"),
    ("Emma", "DURAND", "6A", "e.durand3", "9KQ2WX5MTRB8"),
    ("Nathan", "PETIT", "6B", "n.petit", "7HN4YC1PDVA3"),
]


def main():
    out_path = Path(__file__).parent / "educonnect_exemple.pdf"
    styles = getSampleStyleSheet()
    entete = ParagraphStyle("entete", parent=styles["Normal"], alignment=TA_RIGHT)
    centre = ParagraphStyle("centre", parent=styles["Normal"], alignment=TA_CENTER)

    story = []
    for prenom, nom, classe, identifiant, mdp in FAKE_STUDENTS:
        story.append(Paragraph("Le 21/08/2026", entete))
        story.append(Paragraph(f"{prenom} {nom}", entete))
        story.append(Paragraph(classe, entete))
        story.append(Spacer(1, 10 * mm))
        story.append(
            Paragraph("Mise à disposition de votre compte ÉduConnect Élève", styles["Heading2"])
        )
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(f"{prenom} {nom},", styles["Normal"]))
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                "L'établissement CLG EXEMPLE vous informe que vous disposez d'un compte "
                "ÉduConnect. Ce compte unique est personnel.",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 8 * mm))
        story.append(
            Paragraph(
                "Indiquez sur la page de connexion ÉduConnect, les identifiant et mot de passe "
                "ci-dessous pour activer votre compte :",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"Identifiant : {identifiant}", centre))
        story.append(Paragraph(f"Mot de passe provisoire : {mdp}", centre))
        story.append(Spacer(1, 4 * mm))
        story.append(
            Paragraph("Le mot de passe est à modifier lors de la première connexion.", styles["Normal"])
        )
        story.append(PageBreak())
    if story:
        story.pop()

    SimpleDocTemplate(str(out_path), pagesize=A4).build(story)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
