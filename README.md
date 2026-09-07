# Générateur de fiches d'identifiants élèves (Scribe + ÉduConnect)

Produit des fiches PDF récapitulant, pour chaque élève :

- son identifiant et son mot de passe **Scribe** (repris tels quels du CSV export Scribe) ;
- son identifiant **ÉduConnect** (extrait des courriers PDF « Mise à disposition de votre
  compte ÉduConnect Élève ») ;
- son mot de passe **ÉduConnect**, calculé automatiquement :

  ```
  mot_de_passe_educonnect = mot_de_passe_scribe + "-974" + classe
  ```

  Exemple : mot de passe Scribe `Ab3kZ9q`, classe `6a` → `Ab3kZ9q-9746A` (classe en majuscules)

## Installation (Windows)

1. Installer Python depuis <https://www.python.org/downloads/> en cochant
   **« Add python.exe to PATH »** pendant l'installation.
2. Ouvrir l'invite de commandes, aller dans le dossier du programme et installer les
   bibliothèques :

   ```
   cd C:\Users\...\fiches
   python -m pip install -r requirements.txt
   ```

   (sous Windows la commande est `python`, pas `python3`)

## Deux cas selon vos PDF ÉduConnect

Le programme choisit tout seul, page par page :

| Cas | Ce que fait le programme | Fiabilité |
|---|---|---|
| Le PDF contient du **texte sélectionnable** | Lecture directe du texte | exacte |
| Le PDF est une **image scannée** | OCR (reconnaissance de caractères) | très bonne, avec contrôle |

**Conseil important** : utilisez le PDF **original** téléchargé depuis ÉduConnect. Si vous le
découpez ou le ré-exportez avec certains outils, le texte peut être transformé en image, ce
qui oblige à passer par l'OCR — moins fiable et beaucoup plus lent.

### Installer l'OCR (uniquement si vos PDF sont des images)

1. Installer Tesseract : <https://github.com/UB-Mannheim/tesseract/wiki>
   (pendant l'installation, cocher le pack de langue **French**).
2. Vérifier que `tesseract` est accessible ; sinon, ajouter son dossier
   (`C:\Program Files\Tesseract-OCR`) au PATH de Windows.

Si Tesseract n'est pas installé et qu'une page image est rencontrée, le programme le signale
et continue en laissant l'identifiant à compléter à la main.

## Utilisation

### 1. Vérifier ce que le programme lit dans vos PDF (recommandé la première fois)

```
python generate_fiches.py --dump-text --educonnect-dir pdf
```

Chaque page est affichée avec la mention `[texte]` ou `[OCR]`, suivie de l'identifiant
détecté.

### 2. Générer les fiches

```
python generate_fiches.py --csv eleves.csv --educonnect-dir pdf --output-dir out
```

Résultat dans le dossier `out` :

- `fiches_6A.pdf`, `fiches_6B.pdf`, ... — un fichier par classe (option `--one-file` pour
  tout regrouper dans un seul PDF) ;
- `rapport_extraction.csv` — **à consulter** : liste chaque élève, l'identifiant ÉduConnect
  retenu, sa provenance (fichier + page) et son statut.

### Options

| Option | Rôle |
|---|---|
| `--csv` | fichier CSV export Scribe |
| `--educonnect-dir` | dossier contenant les PDF ÉduConnect |
| `--educonnect-pdf` | un ou plusieurs fichiers PDF (au lieu d'un dossier) |
| `--output-dir` | dossier de sortie (par défaut `out`) |
| `--one-file` | un seul PDF au lieu d'un par classe |
| `--no-ocr` | désactiver l'OCR |
| `--dump-text` | diagnostic : afficher le texte lu, sans générer de fiches |

## Statuts du rapport d'extraction

| Statut | Signification |
|---|---|
| `extrait (conforme au nom)` | identifiant lu directement, et cohérent avec le nom de l'élève |
| `corrigé par OCR (conforme au nom)` | coquille d'OCR rectifiée d'après le nom, sans ambiguïté |
| `corrigé par OCR (à vérifier)` | un chiffre a été rétabli ; **vérifiez cette ligne** |
| `... à vérifier` / `à VÉRIFIER` | l'identifiant ne correspond pas au format attendu |
| `identifiant introuvable` | ligne « Identifiant : » absente ou illisible |
| `aucune page PDF trouvée` | aucun courrier ÉduConnect ne correspond à cet élève |

### Comment les erreurs d'OCR sont corrigées

Les identifiants ÉduConnect élève suivent le format `initiale du prénom` + `.` + `nom`
(+ un chiffre en cas d'homonymie), par exemple `e.durand3` pour Emma DURAND. Comme le CSV
donne le nom exact, le programme s'en sert pour contrôler et rectifier la lecture :

- la ligne de l'identifiant est relue en anglais, ce qui évite que le dictionnaire français
  transforme les chiffres en lettres (un `9` final était lu `g`) ;
- la partie « nom » est rétablie d'après le CSV lorsqu'elle est reconnaissable
  (`|.martin` → `l.martin`) ;
- tout ce qui reste douteux est signalé dans le rapport plutôt que corrigé en silence.

## Format attendu du CSV Scribe

Colonnes requises (l'ordre est libre, les espaces dans les en-têtes sont tolérés, et le
séparateur `;`, `,` ou tabulation est détecté automatiquement) :

```
CLASSE;NOM;PRENOM;LOGIN;MOT DE PASSE;NUMERO ELEVE;INE
```

`NUMERO ELEVE` et `INE` sont lus mais ne figurent pas sur les fiches.

Le suffixe du mot de passe ÉduConnect reprend la valeur de la colonne CLASSE, **mise en
majuscules** : classe `6A` → `-9746A`, classe `6a` → `-9746A`, classe `601` → `-974601`.

## Exemple de test (données fictives)

```
python sample_data/generate_sample_pdf.py
python generate_fiches.py --csv sample_data/eleves_exemple.csv --educonnect-pdf sample_data/educonnect_exemple.pdf --output-dir out --one-file
```

## ⚠️ Confidentialité

Les fichiers CSV et PDF contiennent des données personnelles d'élèves (noms, identifiants,
mots de passe). Le `.gitignore` exclut déjà tout CSV/PDF réel ainsi que les dossiers `data/`
et `out/` : **ne mettez jamais de vraies données d'élèves sur GitHub**. Traitez ces fichiers
en local et supprimez les fiches une fois distribuées.
