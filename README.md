# Crossword Studio

Application Python pour créer des **grilles de mots croisés en LaTeX** à partir d'un fichier JSON ou d'une saisie manuelle, avec une interface graphique simple et soignée.

## Fonctionnalités

- import d'un fichier `JSON` via l'explorateur de fichiers
- saisie manuelle des **mots** et **définitions / indices**
- modification, suppression et filtrage des entrées
- choix d'un **dossier parent** d'export
- création d'un **nouveau sous-dossier projet** contenant :
  - `mots.json`
  - `crossword.tex`
  - `README.txt`
  - `crossword.pdf` si `pdflatex` est installé et si l'option est cochée
- gros bouton **GÉNÉRER LE PROJET**
- cases bloquées rendues en **hachures diagonales** dans le LaTeX
- mode **ligne de commande** conservé pour l'automatisation

---

## Prérequis

- Python **3.10+** recommandé
- `tkinter` disponible dans Python pour l'interface graphique
- `pdflatex` optionnel, uniquement pour générer automatiquement le PDF

## Fichiers attendus

Le script principal est :

```bash
crossword_to_latex.py
```

---

## Lancer l'application graphique

```bash
python crossword_to_latex.py
```

Au démarrage, l'application ouvre une fenêtre **Crossword Studio**.

---

## Utilisation de l'interface

### 1. Importer un JSON ou saisir les mots à la main

Dans la zone **1. Source des données** :

- **Importer un JSON…** : charge un fichier `.json`
- **Exporter le JSON courant…** : sauvegarde la liste actuelle en JSON
- **Vider la liste** : supprime toutes les entrées chargées

Dans la zone **2. Saisie / édition des mots** :

- champ **Mot**
- champ **Définition / indice**
- bouton **Ajouter le mot** pour enregistrer une nouvelle entrée

### 2. Modifier une entrée existante

Pour modifier un mot déjà présent :

1. clique sur une ligne du tableau
2. le mot se charge automatiquement dans le formulaire
3. modifie le contenu
4. clique sur **Enregistrer la modification**

Autres boutons utiles :

- **Charger la sélection** : recharge explicitement la ligne choisie dans le formulaire
- **Supprimer** : supprime l'entrée sélectionnée
- **Nouveau** : vide le formulaire et repasse en mode création

### 3. Régler les paramètres du projet

Dans la zone **3. Paramètres du projet** :

- **Titre du document** : titre affiché dans le document LaTeX
- **Qualité de recherche** : nombre d'essais pour trouver une meilleure grille
- **Graine aléatoire** : entier optionnel pour reproduire le même résultat
- **Dossier parent** : emplacement de sortie
- **Nom du sous-dossier** : nom du dossier projet créé dans le dossier parent
- **Compiler aussi le PDF** : coche cette case si `pdflatex` est installé

### 4. Générer le projet

Clique sur le gros bouton :

```text
GÉNÉRER LE PROJET
```

Le script crée alors un **nouveau dossier projet** avec tous les fichiers nécessaires.

### 5. Ouvrir le dossier généré

Le bouton **Ouvrir le dossier généré** permet d'ouvrir directement le dernier projet exporté.

---

## Formats JSON acceptés

### Format 1 : liste simple de mots

```json
["CHAT", "ARBRE", "PYTHON"]
```

### Format 2 : liste d'objets

```json
[
  {"mot": "chat", "definition": "Animal domestique"},
  {"mot": "python", "indice": "Langage ou serpent"}
]
```

### Format 3 : objet contenant une clé `mots`

```json
{
  "mots": [
    {"mot": "chat", "definition": "Animal domestique"},
    {"mot": "python", "definition": "Langage ou serpent"}
  ]
}
```

Clés reconnues pour les mots :

- `mot`, `word`, `texte`, `text`, `answer`, `solution`

Clés reconnues pour les définitions :

- `indice`, `definition`, `définition`, `clue`, `hint`, `question`

---

## Mode ligne de commande

Le script peut aussi être utilisé sans interface graphique.

### Exemple minimal

```bash
python crossword_to_latex.py mots.json crossword.tex
```

### Exemple avec options

```bash
python crossword_to_latex.py mots.json crossword.tex --attempts 300 --seed 42 --title "Mes mots croisés"
```

---

## Structure du dossier généré

Exemple :

```text
Mon_Projet_20260405_143000/
├── mots.json
├── crossword.tex
├── README.txt
└── crossword.pdf   (si compilation activée et disponible)
```

---

## Raccourcis utiles

- **Entrée** : ajoute le mot si aucune ligne n'est en cours d'édition
- **Entrée** : enregistre la modification si une ligne est sélectionnée
- **Ctrl + Entrée** : lance la génération du projet
- **Double-clic sur une ligne** : charge l'entrée dans l'éditeur

---

## Comportement du moteur

Le moteur essaie de :

- croiser un maximum de mots
- limiter la taille globale de la grille
- produire un document LaTeX propre
- afficher une grille et une solution

Les cases bloquées sont représentées par des **hachures diagonales**, ce qui évite les aplats noirs coûteux.

---

## Dépannage

### Le bouton de génération n'apparaît pas

Utilise la dernière version du script. Le bouton **GÉNÉRER LE PROJET** se trouve dans le panneau de droite.

### La modification d'un mot ne fonctionne pas

Vérifie que :

1. une ligne est bien sélectionnée
2. le formulaire est en **mode édition**
3. tu cliques sur **Enregistrer la modification** et non sur **Ajouter le mot**

### Le PDF n'est pas généré

Cela arrive si `pdflatex` n'est pas installé ou n'est pas dans le `PATH`.

Dans ce cas, le fichier `.tex` est quand même créé et peut être compilé manuellement :

```bash
pdflatex crossword.tex
```

### Tkinter n'est pas disponible

Sous certaines distributions Linux, il faut installer le paquet système correspondant.

Exemple Debian / Ubuntu :

```bash
sudo apt install python3-tk
```

---

## Conseils pour de meilleures grilles

- privilégier des mots de **longueurs variées**
- éviter les listes avec trop de mots très courts
- utiliser des mots qui partagent plusieurs lettres communes
- augmenter la **qualité de recherche** pour améliorer le placement

---

## Licence / personnalisation

Ce projet peut être adapté librement à ton usage. Tu peux personnaliser :

- le style LaTeX
- la taille des cases
- l'apparence des hachures
- les heuristiques de placement
- l'interface graphique

---

## Résumé rapide

1. lance `python crossword_to_latex.py`
2. importe un JSON ou saisis les mots
3. clique sur **Ajouter le mot** pour chaque entrée
4. choisis le dossier parent
5. clique sur **GÉNÉRER LE PROJET**
6. récupère le `.tex`, le `.json` et éventuellement le `.pdf`

