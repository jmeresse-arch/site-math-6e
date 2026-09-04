#!/usr/bin/env python3
"""
Génère des fiches PDF récapitulatives des identifiants élèves (Scribe + EduConnect).

Entrées :
  - un fichier CSV export du serveur Scribe : colonnes CLASSE, NOM, PRENOM, LOGIN,
    "MOT DE PASSE", NUMERO ELEVE (optionnel), INE (optionnel)
  - un ou plusieurs PDF export du serveur EduConnect (texte sélectionnable), dont on
    extrait l'identifiant EduConnect de chaque élève

Règle métier :
  - identifiant Scribe / mot de passe Scribe : repris tels quels du CSV
  - identifiant EduConnect : extrait des PDF EduConnect
  - mot de passe EduConnect : (mot de passe Scribe) + "-974" + (valeur de la colonne CLASSE)

Sortie :
  - un PDF par classe, avec une fiche par élève (identifiants Scribe + EduConnect)

Voir README.md pour le mode d'emploi complet.
"""

import argparse
import csv
import io
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Spacer,
    PageBreak,
    Paragraph,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER


# --------------------------------------------------------------------------
# Normalisation / matching de noms
# --------------------------------------------------------------------------

def normalize_name(s: str) -> str:
    """Majuscules, sans accents, sans ponctuation, espaces compressés."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --------------------------------------------------------------------------
# Modèle élève
# --------------------------------------------------------------------------

@dataclass
class Eleve:
    classe: str
    nom: str
    prenom: str
    login: str
    mdp_scribe: str
    numero_eleve: str = ""
    ine: str = ""
    identifiant_educonnect: Optional[str] = None
    source_pdf: Optional[str] = None

    @property
    def mdp_educonnect(self) -> str:
        return f"{self.mdp_scribe}-974{self.classe.strip()}"

    @property
    def cle_normalisee(self) -> str:
        return normalize_name(f"{self.nom} {self.prenom}")


# --------------------------------------------------------------------------
# Lecture du CSV Scribe
# --------------------------------------------------------------------------

REQUIRED_COLUMNS = ["CLASSE", "NOM", "PRENOM", "LOGIN", "MOT DE PASSE"]
OPTIONAL_COLUMNS = ["NUMERO ELEVE", "INE"]


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        return dialect.delimiter
    except csv.Error:
        # Repli raisonnable pour un export français (Excel FR utilise ';')
        if "\t" in sample.splitlines()[0]:
            return "\t"
        if ";" in sample.splitlines()[0]:
            return ";"
        return ","


def read_csv_scribe(path: Path) -> list[Eleve]:
    raw_bytes = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Impossible de décoder {path} (essayé utf-8-sig/cp1252/latin-1)")

    delimiter = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    if reader.fieldnames is None:
        raise ValueError(f"Le fichier {path} semble vide")

    # normalisation des en-têtes : strip + upper, pour tolérer espaces parasites
    header_map = {}
    for raw_header in reader.fieldnames:
        clean = raw_header.strip().upper()
        header_map[clean] = raw_header

    missing = [c for c in REQUIRED_COLUMNS if c not in header_map]
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans {path} : {missing}\n"
            f"Colonnes trouvées : {list(header_map.keys())}"
        )

    eleves = []
    for row in reader:
        def get(col):
            raw_header = header_map.get(col)
            if raw_header is None:
                return ""
            return (row.get(raw_header) or "").strip()

        if not get("NOM") and not get("PRENOM"):
            continue  # ligne vide

        eleves.append(
            Eleve(
                classe=get("CLASSE"),
                nom=get("NOM"),
                prenom=get("PRENOM"),
                login=get("LOGIN"),
                mdp_scribe=get("MOT DE PASSE"),
                numero_eleve=get("NUMERO ELEVE"),
                ine=get("INE"),
            )
        )
    return eleves


# --------------------------------------------------------------------------
# Extraction des identifiants EduConnect depuis les PDF
# --------------------------------------------------------------------------

# Motifs génériques pour repérer un identifiant EduConnect dans le texte extrait
# d'un PDF. À ajuster si le format réel diffère (voir --dump-text).
IDENTIFIANT_PATTERNS = [
    re.compile(r"identifiant[^:\n]{0,20}:\s*([A-Za-z0-9._-]{4,25})", re.IGNORECASE),
    re.compile(r"\bID(?:ENTIFIANT)?\s*EDUCONNECT\s*:?\s*([A-Za-z0-9._-]{4,25})", re.IGNORECASE),
]

NOM_PATTERN = re.compile(r"^\s*nom\s*:?\s*(.+?)\s*$", re.IGNORECASE)
PRENOM_PATTERN = re.compile(r"^\s*pr[ée]nom\s*:?\s*(.+?)\s*$", re.IGNORECASE)


def extract_text_from_pdf(path: Path) -> list[str]:
    """Retourne le texte de chaque page du PDF."""
    if pdfplumber is None:
        raise RuntimeError(
            "Le module pdfplumber est requis pour lire les PDF EduConnect. "
            "Installez les dépendances avec: pip install -r requirements.txt"
        )
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def guess_name_from_filename(path: Path) -> str:
    """Tente de deviner un nom/prénom à partir du nom de fichier
    (ex: Notification-compte-DUPONT-Jean.pdf)."""
    stem = path.stem
    stem = re.sub(r"(?i)(notification|compte|educonnect|eleve|eleve|-)", " ", stem)
    return normalize_name(stem)


def find_identifiant(text: str) -> Optional[str]:
    for line in text.splitlines():
        for pattern in IDENTIFIANT_PATTERNS:
            m = pattern.search(line)
            if m:
                return m.group(1)
    return None


def find_nom_prenom(text: str) -> Optional[str]:
    nom = None
    prenom = None
    for line in text.splitlines():
        if nom is None:
            m = NOM_PATTERN.match(line)
            if m:
                nom = m.group(1)
                continue
        if prenom is None:
            m = PRENOM_PATTERN.match(line)
            if m:
                prenom = m.group(1)
    if nom and prenom:
        return normalize_name(f"{nom} {prenom}")
    return None


@dataclass
class BlocEduConnect:
    identifiant: Optional[str]
    cle_normalisee: Optional[str]
    source: str
    texte_brut: str


def parse_educonnect_pdfs(paths: list[Path]) -> list[BlocEduConnect]:
    """Chaque page de chaque PDF est traitée comme un bloc "un élève"."""
    blocs = []
    for path in paths:
        pages = extract_text_from_pdf(path)
        for i, page_text in enumerate(pages):
            identifiant = find_identifiant(page_text)
            cle = find_nom_prenom(page_text)
            if cle is None:
                # repli : nom de fichier (utile si un PDF par élève, une seule page)
                cle = guess_name_from_filename(path) if len(pages) == 1 else None
            source = f"{path.name} (page {i + 1})"
            blocs.append(BlocEduConnect(identifiant, cle, source, page_text))
    return blocs


def match_educonnect(eleves: list[Eleve], blocs: list[BlocEduConnect]) -> list[str]:
    """Associe à chaque élève son identifiant EduConnect. Retourne la liste des
    messages d'avertissement (élèves non trouvés, ambiguïtés, etc.)."""
    warnings = []
    # index des blocs valides par clé normalisée
    index = defaultdict(list)
    for b in blocs:
        if b.cle_normalisee and b.identifiant:
            index[b.cle_normalisee].append(b)

    for e in eleves:
        candidats = index.get(e.cle_normalisee)
        if not candidats:
            # tentative souple : contient / est contenu dans
            souples = [
                b
                for key, bl in index.items()
                for b in bl
                if e.cle_normalisee in key or key in e.cle_normalisee
            ]
            candidats = souples or None

        if not candidats:
            warnings.append(
                f"Aucun identifiant EduConnect trouvé pour {e.prenom} {e.nom} ({e.classe})"
            )
            continue
        if len(candidats) > 1:
            idents = {c.identifiant for c in candidats}
            if len(idents) > 1:
                warnings.append(
                    f"Plusieurs identifiants EduConnect différents trouvés pour "
                    f"{e.prenom} {e.nom} ({e.classe}) : {idents} -> le premier a été retenu "
                    f"({candidats[0].source})"
                )
        e.identifiant_educonnect = candidats[0].identifiant
        e.source_pdf = candidats[0].source

    return warnings


# --------------------------------------------------------------------------
# Génération du PDF de sortie
# --------------------------------------------------------------------------

def build_student_card(e: Eleve, styles) -> Table:
    title = Paragraph(f"<b>{e.nom} {e.prenom}</b> — Classe {e.classe}", styles["CardTitle"])

    data = [
        ["Compte SCRIBE (ordinateurs)", ""],
        ["Identifiant", e.login or "—"],
        ["Mot de passe", e.mdp_scribe or "—"],
        ["Compte EDUCONNECT", ""],
        ["Identifiant", e.identifiant_educonnect or "(à renseigner)"],
        ["Mot de passe", e.mdp_educonnect],
    ]
    table = Table(data, colWidths=[55 * mm, 65 * mm])
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("SPAN", (0, 3), (1, 3)),
                ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#2c3e50")),
                ("BACKGROUND", (0, 3), (1, 3), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (1, 0), colors.white),
                ("TEXTCOLOR", (0, 3), (1, 3), colors.white),
                ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 3), (1, 3), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    outer = Table([[title], [table]], colWidths=[120 * mm])
    outer.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return outer


def generate_pdf(eleves: list[Eleve], output_dir: Path, one_file: bool):
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CardTitle",
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=2,
        )
    )

    by_classe = defaultdict(list)
    for e in eleves:
        by_classe[e.classe].append(e)

    output_dir.mkdir(parents=True, exist_ok=True)

    def render(story_items, path):
        doc = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
        )
        doc.build(story_items)

    if one_file:
        story = []
        for classe in sorted(by_classe):
            if story:
                story.append(PageBreak())
            story.append(Paragraph(f"<b>Classe {classe}</b>", styles["Heading2"]))
            story.append(Spacer(1, 6 * mm))
            for e in sorted(by_classe[classe], key=lambda x: (x.nom, x.prenom)):
                story.append(build_student_card(e, styles))
                story.append(Spacer(1, 6 * mm))
        render(story, output_dir / "fiches_identifiants.pdf")
        print(f"-> {output_dir / 'fiches_identifiants.pdf'}")
    else:
        for classe in sorted(by_classe):
            story = [Paragraph(f"<b>Classe {classe}</b>", styles["Heading2"]), Spacer(1, 6 * mm)]
            for e in sorted(by_classe[classe], key=lambda x: (x.nom, x.prenom)):
                story.append(build_student_card(e, styles))
                story.append(Spacer(1, 6 * mm))
            safe_classe = re.sub(r"[^A-Za-z0-9_-]+", "_", classe.strip()) or "classe"
            path = output_dir / f"fiches_{safe_classe}.pdf"
            render(story, path)
            print(f"-> {path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Génère des fiches PDF d'identifiants Scribe + EduConnect à partir "
        "d'un CSV Scribe et de PDF EduConnect."
    )
    parser.add_argument("--csv", required=False, type=Path, help="CSV export Scribe")
    parser.add_argument(
        "--educonnect-pdf",
        nargs="*",
        type=Path,
        default=[],
        help="Un ou plusieurs fichiers PDF EduConnect",
    )
    parser.add_argument(
        "--educonnect-dir",
        type=Path,
        default=None,
        help="Dossier contenant les PDF EduConnect (tous les .pdf du dossier sont utilisés)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("out"), help="Dossier de sortie (défaut: out/)"
    )
    parser.add_argument(
        "--one-file",
        action="store_true",
        help="Générer un seul PDF pour toutes les classes (par défaut : un PDF par classe)",
    )
    parser.add_argument(
        "--dump-text",
        action="store_true",
        help="N'affiche que le texte extrait des PDF EduConnect (pour vérifier/ajuster "
        "les motifs d'extraction), sans générer de fiches",
    )
    args = parser.parse_args()

    pdf_paths = list(args.educonnect_pdf)
    if args.educonnect_dir:
        pdf_paths += sorted(args.educonnect_dir.glob("*.pdf"))

    if args.dump_text:
        if not pdf_paths:
            print("Aucun PDF fourni (--educonnect-pdf ou --educonnect-dir).")
            return 1
        for path in pdf_paths:
            print(f"\n===== {path} =====")
            for i, page_text in enumerate(extract_text_from_pdf(path)):
                print(f"--- page {i + 1} ---")
                print(page_text)
        return 0

    if not args.csv:
        parser.error("--csv est requis (sauf en mode --dump-text)")

    eleves = read_csv_scribe(args.csv)
    print(f"{len(eleves)} élève(s) lu(s) depuis {args.csv}")

    if pdf_paths:
        blocs = parse_educonnect_pdfs(pdf_paths)
        warnings = match_educonnect(eleves, blocs)
        for w in warnings:
            print(f"ATTENTION: {w}")
    else:
        print(
            "ATTENTION: aucun PDF EduConnect fourni. "
            "Les identifiants EduConnect seront marqués '(à renseigner)'."
        )

    generate_pdf(eleves, args.output_dir, one_file=args.one_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
