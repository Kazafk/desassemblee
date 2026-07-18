# Désassemblée

Visualisation de l'évolution du positionnement **gauche-droite** des partis politiques français depuis 2012, calculée de façon **scientifiquement indépendante** à partir des votes à l'Assemblée Nationale.

**[→ Voir le site](https://kazafk.github.io/desassemblee/)**

---

## Principe

La position d'un parti sur l'axe gauche-droite est souvent auto-déclarée (groupe politique, appartenance de coalition). Ce projet adopte une méthodologie inverse : les scores sont **dérivés du comportement de vote réel** des groupes parlementaires, puis calibrés sur des données académiques indépendantes.

```
Votes AN (API CLAIR)  ──►  PCA  ──►  Calibration CHES  ──►  Score [-10, +10]
```

### Couche 1 — Votes parlementaires (open data Assemblée nationale)

Pour chaque législature (14e–17e, de 2012 à aujourd'hui) :

1. Téléchargement des scrutins officiels depuis **[data.assemblee-nationale.fr](https://data.assemblee-nationale.fr/)** (archives JSON complètes, rafraîchies quotidiennement pour la législature en cours)
2. Construction d'une matrice `(groupes × scrutins)` à partir du décompte des voix par groupe : **+1** pour, **−1** contre, **0** abstention (seuil de majorité ±0,3)
3. **ACP à 2 composantes** sur cette matrice → le plan factoriel capture les deux clivages dominants du vote (typiquement gauche/droite et majorité/opposition)

Cette méthode produit un positionnement continu par groupe et par année civile, sans aucune hypothèse a priori sur l'idéologie des partis.

### Couche 2 — Calibration CHES (étalon académique)

Les composantes PCA brutes n'ont ni échelle ni orientation interprétables. On les projette sur **[-10, +10]** grâce au **[Chapel Hill Expert Survey (CHES)](https://chesdata.eu/)** :

- Enquête bisannuelle menée par des chercheurs en science politique
- Variable utilisée : `lrgen` (positionnement général gauche-droite, 0–10), normalisée en `(lrgen − 5) × 2`
- Vagues utilisées : **2014** (14e législature), **2019** (15e), **2024** (16e-17e) — chaque législature est calibrée sur la vague la plus proche de son milieu
- **Calibration supervisée** : régression bivariée `score = a·c1 + b·c2 + c` sur les partis communs — CHES sélectionne la direction du plan factoriel qui correspond à l'axe gauche-droite, même quand le premier axe PCA capture le clivage gouvernement/opposition (hémicycle sans majorité)

Qualité mesurée par r² : **0,93-0,98** sur les sessions 15-17, ≥ 0,74 sur la 14e (une année faible : 2016, période des frondeurs).

### Incertitude — bootstrap

Chaque score annuel est accompagné d'un **intervalle de confiance à 95 %** obtenu par bootstrap (200 rééchantillonnages des scrutins avec remise, PCA + calibration recalculées à chaque réplicat). Les bandes translucides du graphique matérialisent cette incertitude.

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
│   ├── app.js                     # Visualisation D3.js v7 (courbes + bandes IC 95 %)
│   └── data/
│       └── scores.json            # Données finales (générées par le pipeline)
│
├── scripts/
│   ├── common.py                  # Constantes partagées (sessions, chemins)
│   ├── fetch_ches.py              # Extraction scores CHES France
│   ├── fetch_an_archives.py       # Source principale : open data AN (législatures 14-17)
│   ├── fetch_clair.py             # Alternative API CLAIR (legacy, non utilisé par la CI)
│   ├── compute_scores.py          # PCA 2D + calibration bivariée + bootstrap IC
│   ├── build_data.py              # Assemblage scores.json final
│   ├── test_pipeline.py           # Tests unitaires (python -m unittest)
│   ├── groups_mapping.json        # Mapping sigles AN → slugs canoniques (par législature)
│   └── requirements.txt
│
├── data/
│   ├── raw/
│   │   ├── ches/                  # CSV CHES (téléchargement manuel, non versionné)
│   │   ├── an_archives/           # Zips open data AN (non versionnés, sauf organes_gp.json)
│   │   └── clair/                 # Matrices de votes (14-16 committées, statiques)
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

# 2. Télécharger les votes depuis l'open data AN (~1 min, ~50 Mo au premier run)
#    Les législatures archivées (14-16) ne sont jamais reconstruites ;
#    la législature courante est re-téléchargée à chaque exécution.
python scripts/fetch_an_archives.py

# 3. Calculer les scores PCA + calibration CHES + bootstrap (~2 min)
python scripts/compute_scores.py

# 4. Assembler le fichier final
python scripts/build_data.py

# Tests unitaires
python -m unittest discover -s scripts -p "test_*.py"
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

1. Lance les tests unitaires (barrière qualité)
2. Re-télécharge l'archive scrutins de la législature courante (~25 Mo, rafraîchie quotidiennement par l'AN)
3. Recalcule les scores et commit automatiquement `docs/data/scores.json` si changement

Le fichier CHES étant publié tous les 2–3 ans, il est mis à jour manuellement (déposer le nouveau CSV dans `data/raw/ches/` et relancer `fetch_ches.py`).

---

## Sources

| Source | Usage | Licence |
|---|---|---|
| [Open data Assemblée nationale](https://data.assemblee-nationale.fr/) | Scrutins et votes par groupe (législatures 14–17) | Licence ouverte Etalab |
| [Chapel Hill Expert Survey](https://chesdata.eu/) | Calibration gauche-droite (lrgen, vagues 2014/2019/2024) | Libre pour usage académique |
| [API CLAIR](https://api.clair.vote/) | Alternative legacy (17e législature uniquement) | Ouverte, sans authentification |

---

## Limites méthodologiques

- **Sénat exclu** : seule l'Assemblée nationale est couverte
- **Groupes parlementaires ≠ partis** : un groupe peut regrouper plusieurs partis ; le mapping `groups_mapping.json` est maintenu manuellement à chaque législature
- **Absences non comptées** : le score d'un groupe sur un scrutin est la majorité de ses exprimés (pour/contre/abstention)
- **CHES bisannuel** : chaque législature est calibrée sur une seule vague (la plus proche de son milieu) ; les variations intra-législature reposent sur les votes seuls
- **Partis sans groupe à l'AN** absents du graphique (ex. : Reconquête, UDR exclu faute d'ancre CHES)
- **Calibration bivariée** : avec 5-9 partis d'ancrage et 3 paramètres, le r² est mécaniquement optimiste — les intervalles bootstrap donnent une mesure d'incertitude plus honnête
