"""
Assemble le fichier final scores.json pour la visualisation web.
Combine les scores calculés avec les métadonnées de partis (couleurs, noms).
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMPUTED = ROOT / "data" / "processed" / "scores_computed.json"
OUT_DIR = ROOT / "docs" / "data"
OUT = OUT_DIR / "scores.json"

# Métadonnées statiques par slug canonique
PARTY_META = {
    "lfi": {
        "name": "La France insoumise",
        "name_short": "LFI",
        "color": "#BE0000",
        "family": "gauche_radicale",
        "order": 1,
    },
    "gdr": {
        "name": "Gauche Démocrate et Républicaine (PCF)",
        "name_short": "GDR/PCF",
        "color": "#8B0000",
        "family": "gauche_radicale",
        "order": 2,
    },
    "ps": {
        "name": "Parti Socialiste",
        "name_short": "PS",
        "color": "#E91E8C",
        "family": "gauche",
        "order": 3,
    },
    "eelv": {
        "name": "Les Écologistes",
        "name_short": "EELV",
        "color": "#00A550",
        "family": "gauche",
        "order": 4,
    },
    "prg": {
        "name": "Parti Radical de Gauche",
        "name_short": "PRG",
        "color": "#FF6B9D",
        "family": "centre_gauche",
        "order": 5,
    },
    "liot": {
        "name": "Libertés, Indépendants, Outre-mer et Territoires",
        "name_short": "LIOT",
        "color": "#78909C",
        "family": "centre",
        "order": 6,
    },
    "modem": {
        "name": "Mouvement Démocrate",
        "name_short": "MoDem",
        "color": "#FF8C00",
        "family": "centre",
        "order": 7,
    },
    "renaissance": {
        "name": "Renaissance (ex-LREM)",
        "name_short": "Renaissance",
        "color": "#FF6D00",
        "family": "centre",
        "order": 8,
    },
    "udi": {
        "name": "Union des Démocrates et Indépendants",
        "name_short": "UDI",
        "color": "#42A5F5",
        "family": "centre_droit",
        "order": 9,
    },
    "horizons": {
        "name": "Horizons",
        "name_short": "Horizons",
        "color": "#0288D1",
        "family": "centre_droit",
        "order": 10,
    },
    "lr": {
        "name": "Les Républicains",
        "name_short": "LR/UMP",
        "color": "#1565C0",
        "family": "droite",
        "order": 11,
    },
    "rn": {
        "name": "Rassemblement National",
        "name_short": "RN",
        "color": "#1A237E",
        "family": "droite_radicale",
        "order": 12,
    },
}

ELECTIONS = [
    {"year": 2012, "label": "Législatives juin 2012", "note": "Victoire PS"},
    {"year": 2017, "label": "Législatives juin 2017", "note": "Victoire LREM"},
    {"year": 2022, "label": "Législatives juin 2022", "note": "NUPES / Ensemble"},
    {"year": 2024, "label": "Législatives juillet 2024", "note": "Victoire NFP (1er tour)"},
]


def main() -> None:
    if not COMPUTED.exists():
        print(f"ERREUR : {COMPUTED} introuvable — lance d'abord compute_scores.py")
        return

    computed = json.loads(COMPUTED.read_text(encoding="utf-8"))

    parties = []
    for slug, meta in sorted(PARTY_META.items(), key=lambda x: x[1]["order"]):
        if slug not in computed:
            print(f"  Avertissement : aucun score pour '{slug}' — parti exclu")
            continue

        scores = computed[slug]
        if not scores:
            continue

        parties.append({
            "slug": slug,
            "name": meta["name"],
            "name_short": meta["name_short"],
            "color": meta["color"],
            "family": meta["family"],
            "scores": scores,
        })

    output = {
        "meta": {
            "updated": str(date.today()),
            "sources": [
                "API CLAIR — votes AN (sessions 14-17)",
                "Chapel Hill Expert Survey 2023 (chesdata.eu)",
            ],
            "method": "PCA sur votes parlementaires AN, calibrée sur CHES (variable lrgen)",
            "scale": "[-10, +10] — gauche (négatif) à droite (positif)",
            "note": "Points ◆ = ancre CHES (enquête académique). Points ● = PCA sur votes.",
        },
        "parties": parties,
        "elections": ELECTIONS,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"scores.json exporte -> {OUT}")
    print(f"{len(parties)} partis, années couvertes :")
    for p in parties:
        years = [s["year"] for s in p["scores"]]
        print(f"  {p['name_short']:20s} {min(years)}–{max(years)} ({len(years)} pts)")


if __name__ == "__main__":
    main()
