# Générateur de fiches d'identifiants élèves (Scribe + EduConnect)

Génère des fiches PDF récapitulant, pour chaque élève :

- son identifiant et mot de passe **Scribe** (repris tels quels du CSV export du serveur Scribe)
- son identifiant **EduConnect** (extrait des PDF EduConnect) et son mot de passe EduConnect,
  calculé automatiquement comme :

  ```
  mot_de_passe_educonnect = mot_de_passe_scribe + "-974" + classe
  ```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Vérifier le format de vos PDF EduConnect (recommandé avant tout)

Le texte des PDF EduConnect n'est pas forcément mis en page exactement comme prévu par le
script. Avant de générer les fiches, inspectez ce qui est réellement extrait :

```bash
python3 generate_fiches.py --dump-text --educonnect-pdf chemin/vers/notification1.pdf
# ou pour un dossier entier :
python3 generate_fiches.py --dump-text --educonnect-dir chemin/vers/dossier_pdf/
```

Le script reconnaît par défaut des lignes du type :
```
Nom : DUPONT
Prénom : Jean
Identifiant : p7xxxxxxxxx
```

Si vos PDF EduConnect ont une mise en page différente (ex: un fichier par élève déjà nommé
`NOM-Prenom.pdf`, ou un intitulé différent pour l'identifiant), indiquez-le et les motifs de
reconnaissance (`IDENTIFIANT_PATTERNS`, `NOM_PATTERN`, `PRENOM_PATTERN` en haut de
`generate_fiches.py`) seront ajustés en conséquence.

Si les PDF sont nommés individuellement par élève (un fichier PDF = un élève, une seule
page), le script se rabat automatiquement sur le nom de fichier pour retrouver l'élève,
même si le texte interne ne contient pas explicitement "Nom :"/"Prénom :".

## 2. Générer les fiches

```bash
python3 generate_fiches.py \
    --csv chemin/vers/eleves.csv \
    --educonnect-dir chemin/vers/dossier_pdf/ \
    --output-dir out/
```

Par défaut, un fichier PDF est généré **par classe** (`out/fiches_6A.pdf`, `out/fiches_6B.pdf`,
...). Pour un seul fichier PDF regroupant toutes les classes, ajoutez `--one-file`.

Le script affiche un avertissement pour chaque élève pour lequel aucun identifiant
EduConnect n'a pu être trouvé (la fiche indique alors "(à renseigner)" à la main).

## Format attendu du CSV Scribe

Colonnes requises (l'ordre n'a pas d'importance, les espaces superflus dans les en-têtes
sont tolérés, le délimiteur `;`, `,` ou tabulation est détecté automatiquement) :

```
CLASSE;NOM;PRENOM;LOGIN;MOT DE PASSE;NUMERO ELEVE;INE
```

`NUMERO ELEVE` et `INE` sont lus mais non utilisés dans les fiches actuellement.

**Important** : le mot de passe EduConnect est construit en ajoutant `-974` suivi de la
valeur **exacte** de la colonne CLASSE (ex: classe `6A` -> suffixe `-9746A`). Si vos classes
ont un format différent (ex: `601`, `6°A`...), le suffixe suivra ce même format tel quel.

## Exemple / test

Un jeu de données fictif est fourni dans `sample_data/` (aucune donnée réelle d'élève) :

```bash
python3 sample_data/generate_sample_pdf.py   # génère sample_data/educonnect_exemple.pdf
python3 generate_fiches.py \
    --csv sample_data/eleves_exemple.csv \
    --educonnect-pdf sample_data/educonnect_exemple.pdf \
    --output-dir out/ --one-file
```

## ⚠️ Confidentialité des données

Les fichiers CSV et PDF contiennent des données personnelles d'élèves (noms, identifiants,
mots de passe). Le `.gitignore` de ce dépôt exclut déjà tout CSV/PDF réel ainsi que les
dossiers `data/` et `out/` — **ne committez jamais de vraies données d'élèves dans ce dépôt
git**. Traitez ces fichiers uniquement en local et supprimez les fiches PDF générées une
fois distribuées.
