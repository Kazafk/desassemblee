# Désassemblée

Visualisation de l'évolution du positionnement **gauche-droite** des partis politiques français depuis 2012, calculée de façon **scientifiquement indépendante** à partir des votes à l'Assemblée Nationale.

**[→ Voir le site](https://kazafk.github.io/desassemblee/)**

---

## Principe

La position d'un parti sur l'axe gauche-droite est souvent auto-déclarée (groupe politique, appartenance de coalition). Ce projet adopte une méthodologie inverse : les scores sont **dérivés du comportement de vote réel** des groupes parlementaires, puis calibrés sur des données académiques indépendantes.

```
Votes AN (API CLAIR)  ──►  PCA  ──►  Calibration CHES  ──►  Score [-10, +10]
```

### Couche 1 — Votes parlementaires (API CLAIR)

Pour chaque session législative (14e–17e, de 2012 à aujourd'hui) :

1. Récupération des scrutins à l'Assemblée Nationale via l'**[API CLAIR](https://api.clair.vote/)**
2. Construction d'une matrice `(groupes × scrutins)` : **+1** pour, **−1** contre, **0** abstention/absent
3. **ACP (PCA)** sur cette matrice → le premier composant capte ~80 % de la variance, ce qui correspond empiriquement à l'axe gauche-droite dominant dans les votes

Cette méthode produit un score continu par groupe et par année civile, sans aucune hypothèse a priori sur l'idéologie des partis.

### Couche 2 — Calibration CHES (étalon académique)

Les scores PCA bruts n'ont pas d'échelle interprétable. On les convertit en valeurs sur **[-10, +10]** grâce au **[Chapel Hill Expert Survey (CHES)](https://chesdata.eu/)** :

- Enquête bisannuelle menée par des chercheurs en science politique
- Variable utilisée : `lrgen` (positionnement général gauche-droite, 0–10)
- Vagues disponibles pour la France : **2014, 2019, 2024**
- Normalisation : `score = (lrgen − 5) × 2`
- Calibration : régression linéaire `score_calibré = a × score_PCA + b` sur les partis communs

La qualité de la calibration est mesurée par r² (valeur cible > 0,7).

---

## Résultats

| Parti | Score 2024 (CHES) | Famille |
|---|---|---|
| LFI | −8,4 | Gauche radicale |
| GDR/PCF | −6,5 | Gauche radicale |
| EELV | −5,4 | Gauche |
| PS | −3,1 | Gauche |
| MoDem | +0,7 | Centre |
| Renaissance | +2,5 | Centre |
| Horizons | +3,2 | Centre-droit |
| LR | +5,5 | Droite |
| RN | +7,6 | Droite radicale |

---

## Structure du projet

```
désassemblée/
│
├── docs/                          # Site GitHub Pages
│   ├── index.html                 # Page principale (style journal)
│   ├── style.css                  # Design variables (palette crème + serif)
│   ├── app.js                     # Visualisation D3.js v7
│   └── data/
│       └── scores.json            # Données finales (générées par le pipeline)
│
├── scripts/
│   ├── fetch_ches.py              # Extraction scores CHES France
│   ├── fetch_clair.py             # Fetch votes AN via API CLAIR (avec cache)
│   ├── compute_scores.py          # PCA + calibration → scores par parti/année
│   ├── build_data.py              # Assemblage scores.json final
│   ├── groups_mapping.json        # Mapping slugs API → slugs canoniques (par session)
│   └── requirements.txt
│
├── data/
│   ├── raw/
│   │   ├── ches/                  # CSV CHES (téléchargement manuel, non versionné)
│   │   └── clair/                 # Cache JSON API CLAIR (gitignored)
│   └── processed/
│       ├── ches_france.json       # Scores CHES France extraits
│       └── scores_computed.json   # Scores PCA calibrés avant mise en forme
│
└── .github/
    └── workflows/
        └── update-data.yml        # Mise à jour automatique hebdomadaire
```

---

## Lancer le pipeline localement

### Prérequis

- Python 3.10+
- Le fichier CHES (`1999-2024_CHES_dataset_meansV2.csv`) placé dans `data/raw/ches/`  
  → Téléchargeable gratuitement sur [chesdata.eu](https://chesdata.eu/)

```bash
pip install -r scripts/requirements.txt
```

### Ordre d'exécution

```bash
# 1. Extraire les scores CHES France (rapide, ~1s)
python scripts/fetch_ches.py

# 2. Télécharger les votes depuis l'API CLAIR
#    ⚠ Longue opération au premier lancement (~30-45 min pour 8 000+ scrutins)
#    Les sessions archivées (14-16) sont mises en cache — les runs suivants sont rapides.
python scripts/fetch_clair.py

# 3. Calculer les scores PCA + calibration CHES
python scripts/compute_scores.py

# 4. Assembler le fichier final
python scripts/build_data.py
```

Le fichier `docs/data/scores.json` est mis à jour et prêt pour la visualisation.

### Vérifications attendues

- `fetch_ches.py` : ordre idéologique correct (LFI < PS < MoDem < LR < RN)
- `compute_scores.py` : r² > 0,7 pour chaque session ; variance PCA expliquée > 50 %
- `build_data.py` : 12 partis dans le JSON, années 2012–présent

---

## Déploiement GitHub Pages

Le site est servi depuis le dossier `/docs` de la branche `main`.

```
Settings → Pages → Source: Deploy from branch → Branch: main, /docs
```

URL : `https://kazafk.github.io/desassemblee/`

---

## Mise à jour automatique

Un **GitHub Action** tourne chaque lundi à 6h UTC :

1. Récupère uniquement les nouveaux scrutins depuis le dernier run (fetch incrémental)
2. Recalcule les scores de la session en cours
3. Commit automatiquement `docs/data/scores.json` si des changements sont détectés

Le fichier CHES étant publié tous les 2–3 ans, il est mis à jour manuellement (déposer le nouveau CSV dans `data/raw/ches/` et relancer `fetch_ches.py`).

---

## Sources

| Source | Usage | Licence |
|---|---|---|
| [API CLAIR](https://api.clair.vote/) | Votes parlementaires AN (sessions 14–17) | Ouverte, sans authentification |
| [Chapel Hill Expert Survey](https://chesdata.eu/) | Calibration gauche-droite (lrgen, vagues 2014/2019/2024) | Libre pour usage académique |

---

## Limites méthodologiques

- **Sénat exclu** : l'API CLAIR ne couvre que l'Assemblée Nationale
- **Groupes parlementaires ≠ partis** : un groupe peut regrouper plusieurs partis ; le mapping `groups_mapping.json` est maintenu manuellement à chaque législature
- **Absences non distinguées des abstentions** : les deux sont encodés 0 dans la matrice
- **CHES bisannuel** : entre deux vagues, les scores de calibration sont interpolés depuis la dernière vague disponible
- **Partis sans présence à l'AN** absents du graphique (ex. : DLF, Reconquête)
