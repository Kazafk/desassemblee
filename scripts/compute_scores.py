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

from common import ROOT, SESSIONS, SESSION_YEARS

CACHE_DIR = ROOT / "data" / "raw" / "clair"
CHES_FILE = ROOT / "data" / "processed" / "ches_france.json"
MAPPING_FILE = Path(__file__).parent / "groups_mapping.json"
OUT = ROOT / "data" / "processed" / "scores_computed.json"

# Nombre de rééchantillonnages bootstrap pour les intervalles de confiance
N_BOOTSTRAP = 200

# Tolérance de dépassement d'échelle : un score projeté dans [-11, +11] est
# clippé à [-10, +10] (bruit de calibration) ; au-delà, le point est EXCLU —
# l'extrapolation est sortie du domaine où la régression est valide
# (ex. UDI 2012 sans ancre CHES, projeté très au-delà de +10).
CLIP_TOLERANCE = 1.0


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


def pca_scores(mat: np.ndarray, groupes: list[str], verbose: bool = True) -> dict[str, list[float]]:
    """
    Applique PCA(n=2) et retourne les 2 composantes par groupe.

    Pourquoi 2 composantes : dans un hémicycle sans majorité, le premier
    axe PCA capture souvent le clivage gouvernement/opposition (LFI et RN
    du même côté) et non gauche/droite. La calibration CHES (régression
    bivariée) choisit ensuite la meilleure direction dans ce plan.
    """
    if mat.size == 0 or mat.shape[0] < 2:
        return {}

    imputer = SimpleImputer(strategy="median")
    mat_imputed = imputer.fit_transform(mat)

    n_comp = max(1, min(2, mat.shape[0] - 1, mat_imputed.shape[1]))

    # Ignorer les avertissements sklearn sur les matrices constantes
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pca = PCA(n_components=n_comp)
        scores_raw = pca.fit_transform(mat_imputed)

    if verbose:
        ratios = pca.explained_variance_ratio_
        detail = " + ".join(f"{r:.1%}" for r in ratios)
        print(f"    PCA variance expliquée : {detail} = {ratios.sum():.1%}")
        if ratios.sum() < 0.3:
            print(f"    ATTENTION : plan factoriel faible ({ratios.sum():.1%})")

    return {g: [float(v) for v in s] for g, s in zip(groupes, scores_raw)}


def calibrate(pca_dict: dict, ches_scores: dict[str, float], verbose: bool = True) -> tuple[dict[str, float], float]:
    """
    Calibration supervisée : régression multivariée CHES ≈ a·c1 + b·c2 + c
    sur les partis communs. La régression sélectionne la direction du plan
    factoriel qui correspond le mieux à l'axe gauche-droite académique —
    y compris quand l'axe 1 de la PCA capture un autre clivage
    (gouvernement/opposition). Le signe est absorbé par les coefficients.

    Retourne (scores calibrés sur [-10,+10], r²).
    Repli : régression 1D si 2 partis communs, normalisation min-max sinon.
    """
    # Normaliser : accepter des scalaires (tests, rétrocompatibilité) ou des vecteurs
    vec = {g: np.atleast_1d(np.asarray(v, dtype=float)) for g, v in pca_dict.items()}
    common_slugs = [s for s in vec if s in ches_scores]

    def fallback_minmax() -> tuple[dict[str, float], float]:
        if not vec:
            return {}, 0.0
        firsts = {g: float(v[0]) for g, v in vec.items()}
        vmin, vmax = min(firsts.values()), max(firsts.values())
        if vmax == vmin:
            return {g: 0.0 for g in firsts}, 0.0
        return {
            g: float(np.clip((v - vmin) / (vmax - vmin) * 20 - 10, -10, 10))
            for g, v in firsts.items()
        }, 0.0

    if len(common_slugs) >= 3:
        X = np.array([vec[s] for s in common_slugs])           # (k, n_comp)
        y = np.array([ches_scores[s] for s in common_slugs])
        A = np.hstack([X, np.ones((len(X), 1))])               # + intercept
        try:
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        except np.linalg.LinAlgError:
            return fallback_minmax()
        pred = A @ coef
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if verbose:
            print(f"    Calibration CHES (bivariée) : r²={r2:.3f} sur {len(common_slugs)} partis communs")
            if r2 < 0.5:
                print(f"    ATTENTION : r²={r2:.3f} < 0.5, calibration peu fiable")
        raw = {g: float(np.dot(np.append(v, 1.0), coef)) for g, v in vec.items()}
        return _clip_or_drop(raw, verbose), r2

    elif len(common_slugs) == 2:
        x = np.array([vec[s][0] for s in common_slugs])
        y = np.array([ches_scores[s] for s in common_slugs])
        if np.ptp(x) == 0:
            return fallback_minmax()
        slope, intercept, r, _, _ = linregress(x, y)
        r2 = r ** 2
        if verbose:
            print(f"    Calibration CHES (1D, 2 partis) : r²={r2:.3f}")
        raw = {g: float(slope * v[0] + intercept) for g, v in vec.items()}
        return _clip_or_drop(raw, verbose), r2

    else:
        if verbose:
            print(f"    Calibration CHES impossible ({len(common_slugs)} partis communs), normalisation [-10,+10]")
        return fallback_minmax()


def _clip_or_drop(raw: dict[str, float], verbose: bool = True) -> dict[str, float]:
    """
    Applique la règle de bornage : clip dans la marge de tolérance,
    exclusion au-delà (extrapolation hors du domaine de calibration).
    """
    result: dict[str, float] = {}
    dropped: list[str] = []
    for g, v in raw.items():
        if abs(v) > 10 + CLIP_TOLERANCE:
            dropped.append(g)
        else:
            result[g] = float(np.clip(v, -10, 10))
    if dropped and verbose:
        print(f"    Exclus (score hors [-{10 + CLIP_TOLERANCE:.0f}, +{10 + CLIP_TOLERANCE:.0f}], "
              f"extrapolation non fiable) : {sorted(dropped)}")
    return result


def canonicalize(raw: dict, api_to_canonical: dict[str, str | None]) -> dict:
    """
    Convertit {api_slug: composantes} en {canonical_slug: composantes}.
    Moyenne (composante par composante) quand plusieurs slugs API pointent
    vers le même canonique (ex. session 17 : NFP et LFI-NFP → lfi).
    Accepte scalaires ou vecteurs.
    """
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    for api_slug, v in raw.items():
        canon = api_to_canonical.get(api_slug)
        if not canon:
            continue
        arr = np.atleast_1d(np.asarray(v, dtype=float))
        sums[canon] = sums.get(canon, np.zeros_like(arr)) + arr
        counts[canon] = counts.get(canon, 0) + 1
    return {g: list(s / counts[g]) for g, s in sums.items()}


def bootstrap_ci(
    mat: np.ndarray,
    groupes: list[str],
    api_to_canonical: dict[str, str | None],
    ches_anchor: dict[str, float],
    n_boot: int = N_BOOTSTRAP,
    seed: int = 42,
) -> dict[str, tuple[float, float]]:
    """
    Intervalle de confiance à 95 % par groupe, obtenu en rééchantillonnant
    les scrutins (colonnes) avec remise puis en recalculant PCA + calibration.
    L'orientation de chaque réplicat est assurée par la pente de la régression
    de calibration (le signe PCA arbitraire est absorbé par la pente).
    Retourne {canonical_slug: (borne_basse, borne_haute)}.
    """
    if mat.size == 0 or mat.shape[1] < 10:
        return {}

    rng = np.random.default_rng(seed)
    n_scrutins = mat.shape[1]
    samples: dict[str, list[float]] = defaultdict(list)

    for _ in range(n_boot):
        idx = rng.integers(0, n_scrutins, n_scrutins)
        try:
            raw = pca_scores(mat[:, idx], groupes, verbose=False)
            canonical = canonicalize(raw, api_to_canonical)
            calibrated, _ = calibrate(canonical, ches_anchor, verbose=False)
        except (ValueError, np.linalg.LinAlgError):
            continue  # réplicat dégénéré (colonnes identiques...), on l'ignore
        for g, v in calibrated.items():
            samples[g].append(v)

    if not samples:
        return {}

    return {
        g: (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))
        for g, vals in samples.items()
    }


def get_ches_scores_for_session(ches_data: list[dict], session: str, mapping: dict) -> dict[str, float]:
    """
    Pour une session donnée, trouve les scores CHES les plus proches.
    Stratégie : vague CHES la plus proche du MILIEU de la session
    (éviter les calibrations anachroniques, ex. session 14 sur CHES 2019).
    Retourne {slug_canonique: score_calibré}.
    """
    year_start, year_end = SESSION_YEARS[session]
    midpoint = (year_start + min(year_end, 2026)) / 2

    available = [d for d in ches_data if d["canonical"]]
    if not available:
        return {}

    # Vague dont l'année est la plus proche du milieu de session
    wave_years = sorted(set(d["year"] for d in available))
    best_year = min(wave_years, key=lambda y: abs(y - midpoint))
    closest_wave = [d for d in available if d["year"] == best_year]
    print(f"    Vague CHES retenue pour session {session} : {best_year} (milieu de session ~{midpoint:.0f})")

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
    canonical_global = canonicalize(raw_global, api_to_canonical)
    calibrated_global, r2 = calibrate(canonical_global, ches_anchor)

    # Garde-fou : la pente de la régression oriente déjà l'axe, ce cas
    # ne devrait jamais se produire sur une session calibrée. S'il se
    # produit, la calibration elle-même est suspecte — on alerte fort.
    if "lfi" in calibrated_global and "rn" in calibrated_global:
        if calibrated_global["lfi"] > calibrated_global["rn"]:
            print(f"    ALERTE : LFI ({calibrated_global['lfi']:.1f}) > RN ({calibrated_global['rn']:.1f}) "
                  f"apres calibration — l'axe PCA ne reflete probablement pas le clivage G/D")
            calibrated_global = {g: -v for g, v in calibrated_global.items()}

    # Intervalle de confiance bootstrap sur le score global de session
    ci_global = bootstrap_ci(mat, groupes, api_to_canonical, ches_anchor)

    # Scores annuels : sous-matrice par année civile
    year_start, year_end = SESSION_YEARS[session]
    results: dict[str, list[dict]] = defaultdict(list)

    for year in range(year_start, min(year_end, 2027)):
        year_str = str(year)
        # Format clé : "numero|YYYY-MM-DD" — extraire l'année de la partie date
        year_indices = [j for j, s in enumerate(scrutins) if "|" in s and s.split("|")[1][:4] == year_str]

        if len(year_indices) < 10:
            # Trop peu de scrutins pour une PCA fiable — utiliser le score global
            for canon, score in calibrated_global.items():
                entry = {
                    "year": year,
                    "score": round(score, 2),
                    "source": "pca_session_global",
                    "r2": round(r2, 3),
                }
                if canon in ci_global:
                    entry["ci"] = [round(ci_global[canon][0], 2), round(ci_global[canon][1], 2)]
                results[canon].append(entry)
            continue

        mat_year = mat[:, year_indices]
        raw_year = pca_scores(mat_year, groupes)
        canonical_year = canonicalize(raw_year, api_to_canonical)
        calibrated_year, r2_year = calibrate(canonical_year, ches_anchor)

        if "lfi" in calibrated_year and "rn" in calibrated_year:
            if calibrated_year["lfi"] > calibrated_year["rn"]:
                print(f"    ALERTE annee {year} : LFI > RN apres calibration")
                calibrated_year = {g: -v for g, v in calibrated_year.items()}

        ci_year = bootstrap_ci(mat_year, groupes, api_to_canonical, ches_anchor)

        for canon, score in calibrated_year.items():
            entry = {
                "year": year,
                "score": round(score, 2),
                "source": "pca_calibrated",
                "r2": round(r2_year, 3),
                "n_scrutins": len(year_indices),
            }
            if canon in ci_year:
                entry["ci"] = [round(ci_year[canon][0], 2), round(ci_year[canon][1], 2)]
            results[canon].append(entry)

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


def dedupe_entries(entries: list[dict]) -> list[dict]:
    """
    Conserve UNE entrée par couple (année, famille de source), où la famille
    est "ches" (ancre d'experts) ou "pca" (mesure par les votes).

    Les deux familles coexistent pour une même année : le front-end choisit
    ensuite le mode d'affichage (votes seuls, CHES seul, ou hybride).
    Au sein de la famille pca : pca_calibrated > pca_session_global.
    """
    FAMILY = {"ches_anchor": "ches", "pca_calibrated": "pca", "pca_session_global": "pca"}
    PRIORITY = {"ches_anchor": 0, "pca_calibrated": 0, "pca_session_global": 1}

    seen: dict[tuple[int, str], dict] = {}
    for entry in entries:
        key = (entry["year"], FAMILY.get(entry["source"], "pca"))
        if key not in seen or PRIORITY.get(entry["source"], 9) < PRIORITY.get(seen[key]["source"], 9):
            seen[key] = entry
    return sorted(seen.values(), key=lambda x: (x["year"], x["source"]))


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
        all_scores[canon] = dedupe_entries(all_scores[canon])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(all_scores), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nScores exportes -> {OUT}")
    for canon, entries in sorted(all_scores.items()):
        years = [e["year"] for e in entries]
        scores = [e["score"] for e in entries]
        print(f"  {canon:20s} années={min(years)}-{max(years)}, score moyen={np.mean(scores):+.1f}")


if __name__ == "__main__":
    main()
