#!/usr/bin/env python3
"""
Génère des fiches PDF récapitulatives des identifiants élèves (Scribe + ÉduConnect).

Entrées :
  - un fichier CSV export du serveur Scribe : colonnes CLASSE, NOM, PRENOM, LOGIN,
    "MOT DE PASSE", NUMERO ELEVE (optionnel), INE (optionnel)
  - un ou plusieurs PDF "Mise à disposition de votre compte ÉduConnect Élève"
    (une page par élève), dont on extrait l'identifiant ÉduConnect

Deux modes de lecture des PDF, choisis automatiquement page par page :
  1. couche texte du PDF (exact, rapide) — cas normal
  2. OCR (si la page est une image scannée) — nécessite Tesseract installé

Règle métier :
  - identifiant / mot de passe Scribe : repris tels quels du CSV
  - identifiant ÉduConnect : extrait des PDF ÉduConnect
  - mot de passe ÉduConnect : (mot de passe Scribe) + "-974" + (valeur colonne CLASSE)

Sortie :
  - un PDF de fiches par classe (ou un seul fichier avec --one-file)
  - un rapport CSV listant, élève par élève, ce qui a été extrait et son statut

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
from difflib import SequenceMatcher
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
# Outils texte
# --------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def normalize_name(s: str) -> str:
    """Majuscules, sans accents, sans ponctuation, espaces compressés."""
    if not s:
        return ""
    s = strip_accents(s).upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in normalize_name(s).split() if len(t) > 1]


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
    source_pdf: str = ""
    statut: str = "non traité"

    @property
    def mdp_educonnect(self) -> str:
        # la classe est mise en majuscules dans le suffixe : 6a -> -9746A
        return f"{self.mdp_scribe}-974{self.classe.strip().upper()}"

    @property
    def identifiant_attendu(self) -> str:
        """Motif habituel des identifiants ÉduConnect élève : initiale prénom + . + nom."""
        pren = re.sub(r"[^a-z]", "", strip_accents(self.prenom).lower())
        nom = re.sub(r"[^a-z]", "", strip_accents(self.nom).lower())
        if not pren or not nom:
            return ""
        return f"{pren[0]}.{nom}"


# --------------------------------------------------------------------------
# Lecture du CSV Scribe
# --------------------------------------------------------------------------

REQUIRED_COLUMNS = ["CLASSE", "NOM", "PRENOM", "LOGIN", "MOT DE PASSE"]


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        for cand in ("\t", ";", ","):
            if cand in first:
                return cand
        return ";"


def read_csv_scribe(path: Path) -> list[Eleve]:
    raw_bytes = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Impossible de décoder {path} (utf-8/cp1252/latin-1)")

    reader = csv.DictReader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    if reader.fieldnames is None:
        raise ValueError(f"Le fichier {path} semble vide")

    header_map = {h.strip().upper(): h for h in reader.fieldnames}
    missing = [c for c in REQUIRED_COLUMNS if c not in header_map]
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans {path} : {missing}\n"
            f"Colonnes trouvées : {list(header_map.keys())}"
        )

    eleves = []
    for row in reader:
        def get(col):
            h = header_map.get(col)
            return (row.get(h) or "").strip() if h else ""

        if not get("NOM") and not get("PRENOM"):
            continue
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
# Lecture des PDF ÉduConnect (couche texte, sinon OCR)
# --------------------------------------------------------------------------

# "Identifiant : e.durand3" en début de ligne. Le jeu de caractères est large car
# l'OCR produit parfois des symboles parasites (ex: 'l' lu '|').
CARACTERES_IDENT = r"A-Za-z0-9._@|!\[\]/\\-"
IDENT_LINE_RE = re.compile(rf"^\s*identifiant\s*:\s*([{CARACTERES_IDENT}]{{3,40}})", re.IGNORECASE)
# repli : n'importe où dans le texte, mais le ':' doit suivre "identifiant"
IDENT_ANY_RE = re.compile(rf"identifiant\s*:\s*([{CARACTERES_IDENT}]{{3,40}})", re.IGNORECASE)

MIN_TEXT_CHARS = 80  # en dessous, la page est considérée comme une image à OCRiser

# Confusions classiques de l'OCR sur le suffixe numérique d'un identifiant
LETTRE_VERS_CHIFFRE = {
    "g": "9", "q": "9", "o": "0", "O": "0", "D": "0", "Q": "0",
    "l": "1", "i": "1", "I": "1", "s": "5", "S": "5", "z": "2", "Z": "2",
    "b": "6", "G": "6", "B": "8", "t": "7", "T": "7", "A": "4",
    "|": "1", "!": "1", "[": "1", "]": "1",
}

# Symboles parasites de l'OCR, à ramener vers des lettres dans la partie "nom"
SYMBOLE_VERS_LETTRE = {"|": "l", "!": "l", "[": "l", "]": "l", "/": "l", "\\": "l", "1": "l", "0": "o"}


@dataclass
class PageInfo:
    source: str
    texte: str
    identifiant: Optional[str]
    ocr: bool = False


def _find_identifiant_in_text(texte: str) -> Optional[str]:
    for ligne in texte.splitlines():
        m = IDENT_LINE_RE.match(ligne)
        if m:
            return m.group(1)
    m = IDENT_ANY_RE.search(texte)
    return m.group(1) if m else None


def _ocr_page(pdf_path: Path, page_index: int, scale: int = 4) -> tuple[str, Optional[str]]:
    """OCR d'une page image. Retourne (texte complet, identifiant).

    Le texte général est lu en français ; la ligne de l'identifiant est relue en
    anglais avec une liste de caractères restreinte, car le dictionnaire français
    fait confondre les chiffres avec des lettres (ex: '9' lu 'g')."""
    import pypdfium2 as pdfium
    import pytesseract
    from pytesseract import Output

    image = pdfium.PdfDocument(str(pdf_path))[page_index].render(scale=scale).to_pil()
    data = pytesseract.image_to_data(image, lang="fra", output_type=Output.DICT)

    lignes = {}
    for j, mot in enumerate(data["text"]):
        mot = mot.strip()
        if not mot:
            continue
        cle = (data["block_num"][j], data["par_num"][j], data["line_num"][j])
        info = lignes.setdefault(cle, {"mots": [], "box": [10**9, 10**9, 0, 0]})
        info["mots"].append(mot)
        box = info["box"]
        box[0] = min(box[0], data["left"][j])
        box[1] = min(box[1], data["top"][j])
        box[2] = max(box[2], data["left"][j] + data["width"][j])
        box[3] = max(box[3], data["top"][j] + data["height"][j])

    texte_lignes = []
    identifiant = None
    for info in lignes.values():
        ligne = " ".join(info["mots"])
        texte_lignes.append(ligne)
        if identifiant is None and IDENT_LINE_RE.match(ligne):
            x0, y0, x1, y1 = info["box"]
            crop = image.crop(
                (max(0, x0 - 15), max(0, y0 - 10), min(image.width, x1 + 15), min(image.height, y1 + 10))
            )
            crop = crop.resize((crop.width * 3, crop.height * 3))
            relu = pytesseract.image_to_string(crop, lang="eng", config="--psm 7").strip()
            m = IDENT_ANY_RE.search(relu)
            if m:
                identifiant = m.group(1)

    texte = "\n".join(texte_lignes)
    if identifiant is None:
        identifiant = _find_identifiant_in_text(texte)
    return texte, identifiant


def extract_pages(paths: list[Path], autoriser_ocr: bool = True, verbose: bool = True) -> list[PageInfo]:
    if pdfplumber is None:
        raise RuntimeError(
            "Le module pdfplumber est requis. Installez : pip install -r requirements.txt"
        )

    pages: list[PageInfo] = []
    ocr_indisponible_signale = False

    for path in paths:
        with pdfplumber.open(path) as pdf:
            nb_pages = len(pdf.pages)
            textes = [(p.extract_text() or "") for p in pdf.pages]

        for i, texte in enumerate(textes):
            source = f"{path.name} p.{i + 1}"
            if len(texte.strip()) >= MIN_TEXT_CHARS:
                pages.append(PageInfo(source, texte, _find_identifiant_in_text(texte), ocr=False))
                continue

            # page sans couche texte -> OCR
            if not autoriser_ocr:
                pages.append(PageInfo(source, texte, None, ocr=False))
                continue
            try:
                if verbose:
                    print(f"  OCR {source} ({i + 1}/{nb_pages})...", flush=True)
                texte_ocr, identifiant = _ocr_page(path, i)
                pages.append(PageInfo(source, texte_ocr, identifiant, ocr=True))
            except ImportError as exc:
                if not ocr_indisponible_signale:
                    print(
                        f"ATTENTION: page image détectée mais OCR indisponible ({exc}).\n"
                        "  Installez Tesseract puis : pip install pytesseract pypdfium2\n"
                        "  (voir README.md, section OCR)"
                    )
                    ocr_indisponible_signale = True
                pages.append(PageInfo(source, texte, None, ocr=False))
            except Exception as exc:  # tesseract absent du PATH, page illisible...
                if not ocr_indisponible_signale:
                    print(f"ATTENTION: OCR impossible ({exc}). Voir README.md, section OCR.")
                    ocr_indisponible_signale = True
                pages.append(PageInfo(source, texte, None, ocr=False))

    return pages


# --------------------------------------------------------------------------
# Association élève <-> page PDF
# --------------------------------------------------------------------------

SEUIL_CORRESPONDANCE = 0.85


def _score_nom(eleve_tokens: list[str], page_tokens: set[str]) -> float:
    """Moyenne, sur chaque mot du nom de l'élève, de la meilleure ressemblance
    trouvée parmi les mots de la page (tolère les coquilles d'OCR)."""
    if not eleve_tokens:
        return 0.0
    total = 0.0
    for t in eleve_tokens:
        if t in page_tokens:
            total += 1.0
            continue
        meilleur = 0.0
        for pt in page_tokens:
            if abs(len(pt) - len(t)) > 3:
                continue
            r = SequenceMatcher(None, t, pt).ratio()
            if r > meilleur:
                meilleur = r
        total += meilleur
    return total / len(eleve_tokens)


def corriger_identifiant(brut: str, eleve: Eleve) -> tuple[str, str]:
    """Contrôle l'identifiant lu contre le motif attendu (initiale.nom + chiffres).

    Le préfixe "initiale.nom" est connu de façon sûre grâce au CSV : s'il est
    reconnaissable malgré les coquilles de l'OCR, on le rétablit et on ne conserve
    de la lecture que le suffixe numérique.
    Retourne (identifiant, statut)."""
    if not brut:
        return "", "identifiant introuvable"

    brut = brut.strip().strip(".,;:")
    attendu = eleve.identifiant_attendu
    if not attendu:
        return brut.lower(), "extrait"

    bas = brut.lower()

    # cas idéal : le préfixe attendu est lu tel quel
    if bas.startswith(attendu):
        suffixe = bas[len(attendu):]
        if not suffixe or suffixe.isdigit():
            return bas, "extrait (conforme au nom)"
        corrige = "".join(LETTRE_VERS_CHIFFRE.get(c, c) for c in suffixe)
        if corrige.isdigit():
            return attendu + corrige, "corrigé par OCR (à vérifier)"
        return bas, "extrait (suffixe inhabituel, à vérifier)"

    # sinon : on cherche où se termine la partie "nom" et on la rétablit
    for coupe in range(max(1, len(attendu) - 2), min(len(bas), len(attendu) + 2) + 1):
        tete = "".join(SYMBOLE_VERS_LETTRE.get(c, c) for c in bas[:coupe])
        if SequenceMatcher(None, tete, attendu).ratio() < 0.85:
            continue
        suffixe = "".join(LETTRE_VERS_CHIFFRE.get(c, c) for c in bas[coupe:])
        if not suffixe:
            return attendu, "corrigé par OCR (conforme au nom)"
        if suffixe.isdigit():
            return attendu + suffixe, "corrigé par OCR (à vérifier)"

    if SequenceMatcher(None, bas, attendu).ratio() >= 0.8:
        return bas, "extrait (écart avec le nom, à vérifier)"
    return bas, "extrait (ne correspond pas au nom, à VÉRIFIER)"


def associer(eleves: list[Eleve], pages: list[PageInfo]) -> list[str]:
    """Associe chaque élève à la page PDF qui le concerne. Retourne les avertissements."""
    avertissements = []
    pages_tokens = [set(tokens(p.texte)) for p in pages]
    pages_utilisees: dict[int, str] = {}

    for eleve in eleves:
        etoks = tokens(f"{eleve.nom} {eleve.prenom}")
        meilleur_score, meilleur_i, ex_aequo = 0.0, None, 0

        for i, ptoks in enumerate(pages_tokens):
            score = _score_nom(etoks, ptoks)
            # petit bonus si la classe de l'élève figure aussi sur la page
            if score > 0 and normalize_name(eleve.classe) in ptoks:
                score += 0.05
            if score > meilleur_score + 1e-9:
                meilleur_score, meilleur_i, ex_aequo = score, i, 1
            elif abs(score - meilleur_score) < 1e-9 and score > 0:
                ex_aequo += 1

        if meilleur_i is None or meilleur_score < SEUIL_CORRESPONDANCE:
            eleve.statut = "aucune page PDF trouvée"
            avertissements.append(
                f"Aucun identifiant ÉduConnect trouvé pour {eleve.prenom} {eleve.nom} ({eleve.classe})"
            )
            continue

        if ex_aequo > 1:
            avertissements.append(
                f"Plusieurs pages correspondent à {eleve.prenom} {eleve.nom} ({eleve.classe}) "
                f"— la première a été retenue, à vérifier"
            )

        page = pages[meilleur_i]
        eleve.source_pdf = page.source
        if meilleur_i in pages_utilisees:
            avertissements.append(
                f"La page {page.source} est attribuée à deux élèves "
                f"({pages_utilisees[meilleur_i]} et {eleve.prenom} {eleve.nom}) — à vérifier"
            )
        pages_utilisees[meilleur_i] = f"{eleve.prenom} {eleve.nom}"

        identifiant, statut = corriger_identifiant(page.identifiant or "", eleve)
        eleve.identifiant_educonnect = identifiant or None
        eleve.statut = statut
        if "VÉRIFIER" in statut or "introuvable" in statut:
            avertissements.append(
                f"{eleve.prenom} {eleve.nom} ({eleve.classe}) : {statut} "
                f"[{identifiant or '—'}, {page.source}]"
            )

    # filet de sécurité : un même identifiant ne peut pas appartenir à deux élèves
    par_identifiant = defaultdict(list)
    for eleve in eleves:
        if eleve.identifiant_educonnect:
            par_identifiant[eleve.identifiant_educonnect].append(f"{eleve.prenom} {eleve.nom}")
    for identifiant, noms in par_identifiant.items():
        if len(noms) > 1:
            avertissements.append(
                f"L'identifiant '{identifiant}' est attribué à plusieurs élèves "
                f"({', '.join(noms)}) — à VÉRIFIER"
            )

    return avertissements


# --------------------------------------------------------------------------
# Génération des fiches PDF
# --------------------------------------------------------------------------

def build_student_card(e: Eleve, styles) -> Table:
    titre = Paragraph(f"<b>{e.nom} {e.prenom}</b> — Classe {e.classe}", styles["CardTitle"])
    data = [
        ["Compte SCRIBE (ordinateurs du collège)", ""],
        ["Identifiant", e.login or "—"],
        ["Mot de passe", e.mdp_scribe or "—"],
        ["Compte ÉDUCONNECT", ""],
        ["Identifiant", e.identifiant_educonnect or "(à compléter)"],
        ["Mot de passe", e.mdp_educonnect],
    ]
    table = Table(data, colWidths=[58 * mm, 62 * mm])
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
                ("FONTNAME", (1, 1), (1, 2), "Courier-Bold"),
                ("FONTNAME", (1, 4), (1, 5), "Courier-Bold"),
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
    outer = Table([[titre], [table]], colWidths=[120 * mm])
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
    styles.add(ParagraphStyle(name="CardTitle", fontSize=10, alignment=TA_CENTER, spaceAfter=2))

    par_classe = defaultdict(list)
    for e in eleves:
        par_classe[e.classe].append(e)
    output_dir.mkdir(parents=True, exist_ok=True)

    def render(story, path):
        SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title="Identifiants élèves",
        ).build(story)
        print(f"-> {path}")

    def fiches(classe):
        items = [Paragraph(f"<b>Classe {classe}</b>", styles["Heading2"]), Spacer(1, 5 * mm)]
        for e in sorted(par_classe[classe], key=lambda x: (x.nom, x.prenom)):
            items.append(build_student_card(e, styles))
            items.append(Spacer(1, 5 * mm))
        return items

    if one_file:
        story = []
        for classe in sorted(par_classe):
            if story:
                story.append(PageBreak())
            story += fiches(classe)
        render(story, output_dir / "fiches_identifiants.pdf")
    else:
        for classe in sorted(par_classe):
            nom_fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", classe.strip()) or "classe"
            render(fiches(classe), output_dir / f"fiches_{nom_fichier}.pdf")


def write_report(eleves: list[Eleve], path: Path):
    """Rapport de contrôle (sans mots de passe) pour vérifier l'extraction."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["CLASSE", "NOM", "PRENOM", "LOGIN SCRIBE", "IDENTIFIANT EDUCONNECT", "STATUT", "SOURCE PDF"])
        for e in sorted(eleves, key=lambda x: (x.classe, x.nom, x.prenom)):
            w.writerow(
                [e.classe, e.nom, e.prenom, e.login, e.identifiant_educonnect or "", e.statut, e.source_pdf]
            )
    print(f"-> {path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Génère des fiches PDF d'identifiants Scribe + ÉduConnect."
    )
    parser.add_argument("--csv", type=Path, help="CSV export Scribe")
    parser.add_argument("--educonnect-pdf", nargs="*", type=Path, default=[], help="Fichiers PDF ÉduConnect")
    parser.add_argument("--educonnect-dir", type=Path, help="Dossier contenant les PDF ÉduConnect")
    parser.add_argument("--output-dir", type=Path, default=Path("out"), help="Dossier de sortie (défaut: out)")
    parser.add_argument("--one-file", action="store_true", help="Un seul PDF au lieu d'un par classe")
    parser.add_argument("--no-ocr", action="store_true", help="Désactiver l'OCR des pages images")
    parser.add_argument(
        "--dump-text",
        action="store_true",
        help="Afficher le texte lu dans les PDF (diagnostic) sans générer de fiches",
    )
    args = parser.parse_args()

    if args.csv and not args.csv.exists():
        print(f"ERREUR : le fichier CSV '{args.csv}' est introuvable.")
        candidats = sorted(
            p.name for p in Path.cwd().iterdir()
            if p.is_file() and p.suffix.lower() in (".csv", ".txt", ".xlsx", ".xls")
        )
        if candidats:
            print("Fichiers de ce type présents dans le dossier courant :")
            for c in candidats:
                print(f"  - {c}")
            print(
                "Reprenez le nom exact ci-dessus (les fichiers Excel .xlsx doivent d'abord "
                "être enregistrés au format CSV)."
            )
        else:
            print(f"Aucun fichier CSV dans le dossier courant ({Path.cwd()}).")
        return 1

    if args.educonnect_dir and not args.educonnect_dir.is_dir():
        print(f"ERREUR : le dossier '{args.educonnect_dir}' est introuvable.")
        return 1

    pdf_paths = list(args.educonnect_pdf)
    if args.educonnect_dir:
        pdf_paths += sorted(args.educonnect_dir.glob("*.pdf"))

    # sécurité : ne jamais relire les fiches produites par le programme lui-même
    fiches_generees = [p for p in pdf_paths if p.name.lower().startswith("fiches_")]
    if fiches_generees:
        print(
            "ATTENTION: ces fichiers ressemblent à des fiches déjà produites par ce "
            "programme et sont ignorés (ce ne sont pas des courriers ÉduConnect) :"
        )
        for p in fiches_generees:
            print(f"  - {p.name}")
        print(
            "  Le dossier des PDF ÉduConnect ne doit contenir que les courriers d'origine "
            "(6A.pdf, 6B.pdf, ...), et le dossier de sortie doit être différent."
        )
        pdf_paths = [p for p in pdf_paths if p not in fiches_generees]

    manquants = [p for p in args.educonnect_pdf if not p.exists()]
    if manquants:
        for p in manquants:
            print(f"ERREUR : le fichier PDF '{p}' est introuvable.")
        return 1

    if args.educonnect_dir and not pdf_paths:
        print(f"ERREUR : aucun fichier .pdf dans le dossier '{args.educonnect_dir}'.")
        return 1

    if args.dump_text:
        if not pdf_paths:
            parser.error("--dump-text nécessite --educonnect-pdf ou --educonnect-dir")
        for page in extract_pages(pdf_paths, autoriser_ocr=not args.no_ocr):
            mode = "OCR" if page.ocr else "texte"
            print(f"\n===== {page.source} [{mode}] =====")
            print(page.texte)
            print(f"--> identifiant détecté : {page.identifiant or '(aucun)'}")
        return 0

    if not args.csv:
        parser.error("--csv est requis (sauf en mode --dump-text)")

    eleves = read_csv_scribe(args.csv)
    print(f"{len(eleves)} élève(s) lu(s) depuis {args.csv}")

    if pdf_paths:
        print(f"Lecture de {len(pdf_paths)} PDF ÉduConnect...")
        pages = extract_pages(pdf_paths, autoriser_ocr=not args.no_ocr)
        nb_ocr = sum(1 for p in pages if p.ocr)
        print(f"{len(pages)} page(s) lue(s)" + (f" dont {nb_ocr} par OCR" if nb_ocr else ""))
        for a in associer(eleves, pages):
            print(f"ATTENTION: {a}")
    else:
        print("ATTENTION: aucun PDF ÉduConnect fourni, les identifiants resteront à compléter.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    generate_pdf(eleves, args.output_dir, one_file=args.one_file)
    write_report(eleves, args.output_dir / "rapport_extraction.csv")

    ok = sum(1 for e in eleves if e.identifiant_educonnect)
    print(f"\n{ok}/{len(eleves)} identifiant(s) ÉduConnect renseigné(s).")
    print("Vérifiez rapport_extraction.csv, notamment les lignes marquées 'à vérifier'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
