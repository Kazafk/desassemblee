"""
Charge le CSV CHES (Chapel Hill Expert Survey) et extrait les scores
de positionnement gauche-droite (lrgen) pour les partis français.

Prérequis : télécharger CHES_Europe_Trend_1999-2023.csv depuis
https://chesdata.eu/ et le placer dans data/raw/ches/
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW_CHES = ROOT / "data" / "raw" / "ches"
OUT = ROOT / "data" / "processed" / "ches_france.json"

# Mapping noms CHES → slugs canoniques du projet
CHES_TO_CANONICAL = {
    "PCF":       "gdr",
    "PS":        "ps",
    "EELV":      "eelv",
    "PRG":       "prg",
    "MoDem":     "modem",
    "LREM":      "renaissance",
    "LaREM":     "renaissance",
    "REN":       "renaissance",
    "UDI":       "udi",
    "LR":        "lr",
    "UMP":       "lr",
    "FN":        "rn",
    "RN":        "rn",
    "LFI":       "lfi",
    "HOR":       "horizons",
    "FI":        "lfi",
}

# Variable gauche-droite générale dans CHES
LR_VARIABLE = "lrgen"


def load_ches() -> pd.DataFrame:
    csv_files = list(RAW_CHES.glob("*.csv"))
    if not csv_files:
        print(
            "ERREUR : aucun fichier CSV trouvé dans data/raw/ches/\n"
            "Télécharge CHES_Europe_Trend_1999-2023.csv depuis chesdata.eu",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.read_csv(csv_files[0], low_memory=False)
    print(f"CHES chargé : {len(df)} lignes, colonnes : {list(df.columns[:10])}")
    return df


def extract_france(df: pd.DataFrame) -> list[dict]:
    # Détecter la colonne pays (country ou cname)
    country_col = next(
        (c for c in df.columns if c.lower() in ("country", "cname", "countryname")),
        None,
    )
    if country_col is None:
        # Fallback : chercher 'france' dans toutes les colonnes string
        for col in df.select_dtypes(include="object").columns:
            if df[col].str.lower().str.contains("france", na=False).any():
                country_col = col
                break

    if country_col is None:
        print("ERREUR : impossible de trouver la colonne pays dans le CSV CHES.", file=sys.stderr)
        sys.exit(1)

    france = df[df[country_col].str.lower().str.contains("france", na=False)].copy()
    france = france[france["year"] >= 2012]

    if LR_VARIABLE not in france.columns:
        print(f"ERREUR : colonne '{LR_VARIABLE}' absente. Colonnes disponibles : {list(france.columns)}", file=sys.stderr)
        sys.exit(1)

    france = france.dropna(subset=[LR_VARIABLE])

    # Détecter la colonne nom du parti
    party_col = next(
        (c for c in df.columns if c.lower() in ("party_name", "partyname", "party", "abbrev")),
        None,
    )
    if party_col is None:
        print("ERREUR : impossible de trouver la colonne nom du parti.", file=sys.stderr)
        sys.exit(1)

    results = []
    for _, row in france.iterrows():
        party_name = str(row[party_col]).strip()
        canonical = CHES_TO_CANONICAL.get(party_name)
        if canonical is None:
            # Tentative de correspondance partielle
            for ches_name, slug in CHES_TO_CANONICAL.items():
                if ches_name.lower() in party_name.lower() or party_name.lower() in ches_name.lower():
                    canonical = slug
                    break

        raw_score = float(row[LR_VARIABLE])
        # Normalise CHES 0-10 → [-10, +10]
        calibrated = (raw_score - 5.0) * 2.0

        results.append({
            "ches_name": party_name,
            "canonical": canonical,
            "year": int(row["year"]),
            "lrgen_raw": round(raw_score, 3),
            "score": round(calibrated, 3),
        })

    results.sort(key=lambda x: (x["year"], x["ches_name"]))
    return results


def main() -> None:
    df = load_ches()
    data = extract_france(df)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n{len(data)} entrées exportées → {OUT}")

    # Aperçu
    mapped = [d for d in data if d["canonical"]]
    years = sorted(set(d["year"] for d in mapped))
    print(f"Partis mappés : {sorted(set(d['canonical'] for d in mapped))}")
    print(f"Années : {years}")

    if mapped:
        print("\nAperçu (2019 ou première année disponible) :")
        sample_year = 2019 if 2019 in years else years[0]
        for d in sorted(mapped, key=lambda x: x["score"]):
            if d["year"] == sample_year:
                print(f"  {d['canonical']:20s} {d['score']:+.1f}  (CHES raw: {d['lrgen_raw']})")


if __name__ == "__main__":
    main()
