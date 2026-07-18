"""
Charge le CSV CHES (Chapel Hill Expert Survey) et extrait les scores
de positionnement gauche-droite (lrgen) pour les partis français.

Prérequis : placer le fichier CSV CHES dans data/raw/ches/
Testé avec : 1999-2024_CHES_dataset_meansV2.csv
Source : https://chesdata.eu/
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_CHES = ROOT / "data" / "raw" / "ches"
OUT = ROOT / "data" / "processed" / "ches_france.json"

# Code pays France dans le dataset CHES
FRANCE_COUNTRY_CODE = 6

# Mapping abrév. CHES → slugs canoniques du projet
CHES_TO_CANONICAL = {
    # Gauche radicale / communiste
    "PCF":      "gdr",
    "PG":       "lfi",    # Parti de Gauche (précurseur LFI)
    "FI":       "lfi",    # La France Insoumise
    # Gauche
    "PS":       "ps",
    "PRG":      "prg",    # Parti Radical de Gauche
    # Écologie
    "EELV":     "eelv",
    "VERTS":    "eelv",
    "LE/EELV":  "eelv",   # Les Écologistes / EELV (2024)
    # Centre / macronisme
    "MODEM":    "modem",
    "MoDem":    "modem",
    "LREM":     "renaissance",
    "RE":       "renaissance",  # Renaissance (ex-LREM, 2024)
    "Horizons": "horizons",
    # Centre-droit (on exclut NC et AC — trop petits, bruitent la calibration)
    # Droite
    "UMP":      "lr",
    "LR":       "lr",
    # PRV exclu : Parti Radical Valoisien est trop centriste vs LR mainstream
    # Extrême droite
    "FN":       "rn",
    "RN":       "rn",
    # Exclure : MPF, DLF, REC (Reconquête), Ensemble (liste européenne)
}

# Axes CHES extraits (échelle 0-10, normalisés vers [-10,+10]) :
#   lrgen  — gauche-droite générale
#   lrecon — gauche-droite économique (redistribution, marché)
#   galtan — GAL/TAN sociétal (libertaire/vert vs autoritaire/national)
LR_VARIABLE = "lrgen"
AXIS_VARIABLES = ["lrgen", "lrecon", "galtan"]


def load_ches() -> pd.DataFrame:
    csv_files = list(RAW_CHES.glob("*.csv"))
    if not csv_files:
        print(
            "ERREUR : aucun fichier CSV trouve dans data/raw/ches/\n"
            "Telecharge le dataset depuis chesdata.eu",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.read_csv(csv_files[0], low_memory=False)
    print(f"CHES charge : {len(df)} lignes, {len(df.columns)} colonnes")
    print(f"Fichier : {csv_files[0].name}")
    return df


def extract_france(df: pd.DataFrame) -> list[dict]:
    # Filtrer France (country=6) et années >= 2012
    france = df[df["country"] == FRANCE_COUNTRY_CODE].copy()
    france = france[france["year"] >= 2012]

    if LR_VARIABLE not in france.columns:
        print(
            f"ERREUR : colonne '{LR_VARIABLE}' absente.\n"
            f"Colonnes disponibles : {list(df.columns)}",
            file=sys.stderr,
        )
        sys.exit(1)

    france = france.dropna(subset=[LR_VARIABLE])

    missing_axes = [a for a in AXIS_VARIABLES if a not in france.columns]
    if missing_axes:
        print(f"ATTENTION : axes absents du CSV : {missing_axes}", file=sys.stderr)

    # Colonne nom du parti
    party_col = "party"

    results = []
    for _, row in france.iterrows():
        party_name = str(row[party_col]).strip()
        canonical = CHES_TO_CANONICAL.get(party_name)

        raw_score = float(row[LR_VARIABLE])
        entry = {
            "ches_name": party_name,
            "canonical": canonical,
            "year": int(row["year"]),
            "lrgen_raw": round(raw_score, 3),
            # Normalise CHES 0-10 vers [-10, +10] : score = (x - 5) * 2
            "score": round((raw_score - 5.0) * 2.0, 3),
        }
        # Axes thématiques (mêmes normalisations), absents si NaN
        for axis in ("lrecon", "galtan"):
            if axis in france.columns and not pd.isna(row.get(axis)):
                raw = float(row[axis])
                entry[f"{axis}_raw"] = round(raw, 3)
                entry[f"score_{axis}"] = round((raw - 5.0) * 2.0, 3)

        results.append(entry)

    results.sort(key=lambda x: (x["year"], x["ches_name"]))
    return results


def main() -> None:
    df = load_ches()
    data = extract_france(df)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n{len(data)} entrees exportees -> {OUT}")

    mapped = [d for d in data if d["canonical"]]
    years = sorted(set(d["year"] for d in mapped))
    print(f"Partis mappes : {sorted(set(d['canonical'] for d in mapped))}")
    print(f"Vagues disponibles : {years}")

    if mapped:
        for year in years:
            print(f"\n  === {year} ===")
            wave = sorted([d for d in mapped if d["year"] == year], key=lambda x: x["score"])
            for d in wave:
                print(f"  {d['canonical']:20s} {d['score']:+.1f}  (CHES raw: {d['lrgen_raw']}  [{d['ches_name']}])")


if __name__ == "__main__":
    main()
