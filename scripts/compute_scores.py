"""
Calcule les scores gauche-droite par groupe et par année :
1. PCA sur la matrice de votes (groupes × scrutins) par session
2. Calibration linéaire sur les scores CHES (Chapel Hill Expert Survey)
3. Calcul annuel (sous-matrice par année civile)

Sortie : data/processed/scores_computed.json
"""
import json
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import linregress
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "raw" / "clair"
CHES_FILE = ROOT / "data" / "processed" / "ches_france.json"
MAPPING_FILE = Path(__file__).parent / "groups_mapping.json"
OUT = ROOT / "data" / "processed" / "scores_computed.json"

SESSIONS = ["14", "15", "16", "17"]
# Années approximatives de chaque session
SESSION_YEARS = {"14": (2012, 2017), "15": (2017, 2022), "16": (2022, 2024), "17": (2024, 2030)}


def load_vote_matrix(session: str) -> tuple[list[str], list[str], np.ndarray]:
    """
    Retourne (groupe_slugs, scrutin_keys, matrix_np)
    matrix_np shape : (n_groupes, n_scrutins), valeurs +1/-1/0
    """
    path = CACHE_DIR / f"session_{session}_vote_matrix.json"
    if not path.exists():
        return [], [], np.array([])

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not raw:
        return [], [], np.array([])

    groupes = sorted(raw.keys())
    scrutins = sorted(set(k for v in raw.values() for k in v.keys()))
    if not scrutins:
        return [], [], np.array([])

    mat = np.zeros((len(groupes), len(scrutins)), dtype=np.float32)
    for i, g in enumerate(groupes):
        for j, s in enumerate(scrutins):
            mat[i, j] = raw[g].get(s, np.nan)

    return groupes, scrutins, mat


def pca_scores(mat: np.ndarray, groupes: list[str]) -> dict[str, float]:
    """
    Applique PCA(n=1) et retourne le score brut par groupe.
    Impute les NaN par la médiane de chaque colonne avant PCA.
    """
    if mat.size == 0 or mat.shape[0] < 2:
        return {}

    imputer = SimpleImputer(strategy="median")
    mat_imputed = imputer.fit_transform(mat)

    # Ignorer les avertissements sklearn sur les matrices constantes
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pca = PCA(n_components=1)
        scores_raw = pca.fit_transform(mat_imputed)[:, 0]

    explained = pca.explained_variance_ratio_[0]
    print(f"    PCA variance expliquée : {explained:.1%}")
    if explained < 0.15:
        print(f"    ATTENTION : faible variance expliquée ({explained:.1%}) — axe G/D peu structuré")

    return {g: float(s) for g, s in zip(groupes, scores_raw)}


def calibrate(pca_dict: dict[str, float], ches_scores: dict[str, float]) -> tuple[dict[str, float], float]:
    """
    Régression linéaire PCA → CHES sur les partis communs.
    Retourne (scores calibrés, r²).
    Si moins de 2 partis en commun, retourne les scores PCA normalisés sur [-10,+10].
    """
    common = [(pca_dict[s], ches_scores[s]) for s in pca_dict if s in ches_scores]

    if len(common) >= 2:
        x = np.array([c[0] for c in common])
        y = np.array([c[1] for c in common])
        slope, intercept, r, _, _ = linregress(x, y)
        r2 = r ** 2
        print(f"    Calibration CHES : r²={r2:.3f} sur {len(common)} partis communs")
        if r2 < 0.5:
            print(f"    ATTENTION : r²={r2:.3f} < 0.5, calibration peu fiable")
        calibrated = {g: float(np.clip(slope * v + intercept, -10, 10)) for g, v in pca_dict.items()}
        return calibrated, r2
    else:
        print(f"    Calibration CHES impossible ({len(common)} partis communs < 2), normalisation [-10,+10]")
        if not pca_dict:
            return {}, 0.0
        vals = list(pca_dict.values())
        vmin, vmax = min(vals), max(vals)
        if vmax == vmin:
            return {g: 0.0 for g in pca_dict}, 0.0
        calibrated = {g: float(np.clip((v - vmin) / (vmax - vmin) * 20 - 10, -10, 10)) for g, v in pca_dict.items()}
        return calibrated, 0.0


def get_ches_scores_for_session(ches_data: list[dict], session: str, mapping: dict) -> dict[str, float]:
    """
    Pour une session donnée, trouve les scores CHES les plus proches.
    Stratégie : utiliser toutes les vagues CHES disponibles ≤ year_end + 2.
    Pour les sessions récentes sans vague CHES contemporaine, utilise la dernière disponible.
    Retourne {slug_canonique: score_calibré}.
    """
    year_start, year_end = SESSION_YEARS[session]

    # Toutes les vagues disponibles avant la fin de session (avec marge +2 ans)
    available = [d for d in ches_data if d["year"] <= year_end + 2 and d["canonical"]]

    if not available:
        return {}

    # Utiliser uniquement la vague la plus récente disponible pour cette session
    max_year = max(d["year"] for d in available)
    closest_wave = [d for d in available if d["year"] == max_year]

    by_canonical: dict[str, list[float]] = defaultdict(list)
    for d in closest_wave:
        by_canonical[d["canonical"]].append(d["score"])

    return {slug: float(np.mean(scores)) for slug, scores in by_canonical.items()}


def slugs_for_session(session: str, mapping: dict) -> dict[str, str | None]:
    """Retourne {api_slug: canonical_slug} pour une session (slugs en minuscules)."""
    session_map = mapping.get(f"session_{session}", {})
    return {k.lower(): v.get("canonical") for k, v in session_map.items() if "_comment" not in k}


def compute_session_scores(
    session: str,
    groupes: list[str],
    scrutins: list[str],
    mat: np.ndarray,
    ches_data: list[dict],
    mapping: dict,
) -> dict[str, list[dict]]:
    """
    Retourne {canonical_slug: [{year, score, source}]}
    """
    if mat.size == 0:
        print(f"  Session {session} : matrice vide, skip")
        return {}

    api_to_canonical = slugs_for_session(session, mapping)
    ches_anchor = get_ches_scores_for_session(ches_data, session, mapping)

    print(f"\n  === Session {session} (matrice {mat.shape[0]}×{mat.shape[1]}) ===")

    # Score global de session (sur tous les scrutins)
    raw_global = pca_scores(mat, groupes)

    # Canoniser les slugs
    canonical_global: dict[str, float] = {}
    for api_slug, raw in raw_global.items():
        canon = api_to_canonical.get(api_slug)
        if canon:
            if canon not in canonical_global:
                canonical_global[canon] = raw
            else:
                canonical_global[canon] = (canonical_global[canon] + raw) / 2

    calibrated_global, r2 = calibrate(canonical_global, ches_anchor)

    # Assurer que LFI < RN (corriger le signe si inversé)
    if "lfi" in calibrated_global and "rn" in calibrated_global:
        if calibrated_global["lfi"] > calibrated_global["rn"]:
            print(f"    Inversion du signe PCA (LFI={calibrated_global['lfi']:.1f} > RN={calibrated_global['rn']:.1f})")
            calibrated_global = {g: -v for g, v in calibrated_global.items()}

    # Scores annuels : sous-matrice par année civile
    year_start, year_end = SESSION_YEARS[session]
    results: dict[str, list[dict]] = defaultdict(list)

    for year in range(year_start, min(year_end, 2027)):
        year_str = str(year)
        # Format clé : "numero|YYYY-MM-DD" — extraire l'année de la partie date
        year_indices = [j for j, s in enumerate(scrutins) if s.split("|")[1][:4] == year_str if "|" in s]

        if len(year_indices) < 10:
            # Trop peu de scrutins pour une PCA fiable — utiliser le score global
            for canon, score in calibrated_global.items():
                results[canon].append({
                    "year": year,
                    "score": round(score, 2),
                    "source": "pca_session_global",
                    "r2": round(r2, 3),
                })
            continue

        mat_year = mat[:, year_indices]
        raw_year = pca_scores(mat_year, groupes)

        canonical_year: dict[str, float] = {}
        for api_slug, raw in raw_year.items():
            canon = api_to_canonical.get(api_slug)
            if canon:
                canonical_year[canon] = raw

        calibrated_year, r2_year = calibrate(canonical_year, ches_anchor)

        if "lfi" in calibrated_year and "rn" in calibrated_year:
            if calibrated_year["lfi"] > calibrated_year["rn"]:
                calibrated_year = {g: -v for g, v in calibrated_year.items()}

        for canon, score in calibrated_year.items():
            results[canon].append({
                "year": year,
                "score": round(score, 2),
                "source": "pca_calibrated",
                "r2": round(r2_year, 3),
                "n_scrutins": len(year_indices),
            })

    # Ajouter les ancres CHES (score direct)
    ches_years = [d["year"] for d in ches_data if year_start <= d["year"] <= year_end and d["canonical"]]
    for d in ches_data:
        if year_start <= d["year"] <= year_end and d["canonical"]:
            results[d["canonical"]].append({
                "year": d["year"],
                "score": round(d["score"], 2),
                "source": "ches_anchor",
                "lrgen_raw": d.get("lrgen_raw"),
            })

    return dict(results)


def main() -> None:
    if not CHES_FILE.exists():
        print(f"ERREUR : {CHES_FILE} introuvable — lance d'abord fetch_ches.py")
        return

    ches_data = json.loads(CHES_FILE.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))

    all_scores: dict[str, list[dict]] = defaultdict(list)

    for session in SESSIONS:
        groupes, scrutins, mat = load_vote_matrix(session)
        if mat.size == 0:
            print(f"Session {session} : pas de matrice de votes disponible")
            continue

        session_scores = compute_session_scores(session, groupes, scrutins, mat, ches_data, mapping)
        for canon, entries in session_scores.items():
            all_scores[canon].extend(entries)

    # Dédupliquer et trier par année
    for canon in all_scores:
        seen_years: dict[int, dict] = {}
        for entry in all_scores[canon]:
            y = entry["year"]
            # Préférer ches_anchor > pca_calibrated > pca_session_global
            priority = {"ches_anchor": 0, "pca_calibrated": 1, "pca_session_global": 2}
            if y not in seen_years or priority.get(entry["source"], 9) < priority.get(seen_years[y]["source"], 9):
                seen_years[y] = entry
        all_scores[canon] = sorted(seen_years.values(), key=lambda x: x["year"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(all_scores), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nScores exportes -> {OUT}")
    for canon, entries in sorted(all_scores.items()):
        years = [e["year"] for e in entries]
        scores = [e["score"] for e in entries]
        print(f"  {canon:20s} années={min(years)}-{max(years)}, score moyen={np.mean(scores):+.1f}")


if __name__ == "__main__":
    main()
