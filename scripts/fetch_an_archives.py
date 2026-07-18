"""
Construit les matrices de votes des législatures 14 à 17 à partir de
l'open data officiel de l'Assemblée nationale (data.assemblee-nationale.fr).

Source unique du pipeline : les archives de l'AN couvrent toutes les
législatures et celle de la législature courante est rafraîchie
quotidiennement par l'AN. Téléchargement direct (~1-26 Mo par législature),
sans rate limit — là où l'API CLAIR nécessitait ~10h de requêtes paginées.

Sorties : data/raw/clair/session_{14..17}_vote_matrix.json
          (lues par compute_scores.py)
Les législatures archivées (14-16) ne sont jamais reconstruites si leur
matrice existe ; la législature courante (17) est reconstruite à chaque run.

L'agrégation par groupe utilise decompteVoix (pour/contre/abstention),
avec un seuil de majorité à ±0.3.
"""
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, CURRENT_SESSION

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW_DIR = ROOT / "data" / "raw" / "an_archives"
OUT_DIR = ROOT / "data" / "raw" / "clair"   # même dossier que fetch_clair
RAW_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://data.assemblee-nationale.fr/static/openData/repository"

SCRUTINS_URLS = {
    "14": f"{BASE}/14/loi/scrutins/Scrutins_XIV.json.zip",
    "15": f"{BASE}/15/loi/scrutins/Scrutins_XV.json.zip",
    "16": f"{BASE}/16/loi/scrutins/Scrutins.json.zip",
    "17": f"{BASE}/17/loi/scrutins/Scrutins.json.zip",  # rafraîchi quotidiennement par l'AN
}

# Référentiel de tous les organes depuis la XIe législature (uid PO... → sigle)
AMO30_URL = (
    f"{BASE}/17/amo/tous_acteurs_mandats_organes_xi_legislature/"
    "AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json.zip"
)

ORGANES_CACHE = RAW_DIR / "organes_gp.json"


def download_zip(url: str, dest: Path, force: bool = False) -> zipfile.ZipFile:
    """Télécharge un zip (avec cache disque) et le retourne ouvert."""
    if dest.exists() and not force:
        print(f"  Cache : {dest.name}")
        return zipfile.ZipFile(dest)
    print(f"  Téléchargement {url.rsplit('/', 1)[-1]}...")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  {len(r.content) / 1024 / 1024:.1f} Mo enregistrés")
    return zipfile.ZipFile(dest)


def load_groupes_referentiel() -> dict[str, str]:
    """
    Retourne {organe_uid: libelleAbrev} pour tous les groupes politiques (GP)
    de l'Assemblée nationale depuis la XIe législature.
    """
    if ORGANES_CACHE.exists():
        return json.loads(ORGANES_CACHE.read_text(encoding="utf-8"))

    print("--- Référentiel organes (AMO30) ---")
    zf = download_zip(AMO30_URL, RAW_DIR / "amo30.zip")

    mapping: dict[str, str] = {}
    organe_files = [n for n in zf.namelist() if "/organe/" in n or n.startswith("organe")]
    if not organe_files:
        # Structure alternative : un seul gros fichier JSON
        organe_files = [n for n in zf.namelist() if n.endswith(".json")]

    for name in organe_files:
        try:
            data = json.loads(zf.read(name).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        # Fichier par organe : {"organe": {...}}
        organes = []
        if "organe" in data:
            organes = [data["organe"]]
        elif "export" in data:
            # Gros fichier unique : export.organes.organe = [...]
            org_list = data.get("export", {}).get("organes", {}).get("organe", [])
            organes = org_list if isinstance(org_list, list) else [org_list]

        for o in organes:
            if o.get("codeType") == "GP":  # GP = groupe politique
                uid = o.get("uid")
                abbrev = o.get("libelleAbrev", "")
                if uid and abbrev:
                    mapping[uid] = abbrev

    ORGANES_CACHE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {len(mapping)} groupes politiques référencés")
    return mapping


def ensure_list(x) -> list:
    """La conversion XML→JSON de l'AN produit un dict quand il n'y a qu'un élément."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def load_scrutins(leg: str, force: bool = False) -> list[dict]:
    """Charge tous les scrutins d'une législature depuis l'archive officielle."""
    zf = download_zip(SCRUTINS_URLS[leg], RAW_DIR / f"scrutins_{leg}.zip", force=force)

    scrutins: list[dict] = []
    for name in zf.namelist():
        if not name.endswith(".json"):
            continue
        data = json.loads(zf.read(name).decode("utf-8"))
        if "scrutins" in data:
            # Fichier unique contenant toute la législature
            scrutins.extend(ensure_list(data["scrutins"].get("scrutin")))
        elif "scrutin" in data:
            # Un fichier par scrutin
            scrutins.append(data["scrutin"])

    print(f"  {len(scrutins)} scrutins chargés")
    return scrutins


def build_matrix(leg: str, scrutins: list[dict], organes: dict[str, str]) -> tuple[dict, dict]:
    """
    Construit ({groupe_abbrev_lower: {"numero|date": +1|-1|0}},
               {"numero|date": codeTypeVote}).
    Seuil de majorité ±0.3. Le second dict permet de filtrer les scrutins
    par type (SPO ordinaire, SPS solennel, MOC censure...) en aval.
    """
    matrix: dict[str, dict[str, int]] = defaultdict(dict)
    types: dict[str, str] = {}
    unknown_refs: dict[str, int] = defaultdict(int)

    for s in scrutins:
        numero = s.get("numero")
        date = str(s.get("dateScrutin", ""))[:10]
        if not numero or not date:
            continue
        key = f"{numero}|{date}"
        types[key] = (s.get("typeVote") or {}).get("codeTypeVote", "SPO")

        try:
            groupes = ensure_list(s["ventilationVotes"]["organe"]["groupes"]["groupe"])
        except (KeyError, TypeError):
            continue

        for g in groupes:
            ref = g.get("organeRef")
            abbrev = organes.get(ref)
            if not abbrev:
                unknown_refs[ref] += 1
                continue

            decompte = (g.get("vote") or {}).get("decompteVoix") or {}
            try:
                pour = int(decompte.get("pour") or 0)
                contre = int(decompte.get("contre") or 0)
                abst = int(decompte.get("abstention") or decompte.get("abstentions") or 0)
            except (ValueError, TypeError):
                continue

            exprimes = pour + contre + abst
            if exprimes == 0:
                continue
            avg = (pour - contre) / exprimes
            matrix[abbrev.lower()][key] = 1 if avg > 0.3 else (-1 if avg < -0.3 else 0)

    if unknown_refs:
        print(f"  organeRef inconnus (ignorés) : {dict(unknown_refs)}")

    return dict(matrix), types


def main() -> None:
    organes = load_groupes_referentiel()

    for leg in ("14", "15", "16", "17"):
        current = leg == CURRENT_SESSION
        out_path = OUT_DIR / f"session_{leg}_vote_matrix.json"
        types_path = OUT_DIR / f"session_{leg}_scrutin_types.json"
        if out_path.exists() and types_path.exists() and not current:
            print(f"\nLégislature {leg} : matrice déjà présente, skip ({out_path.name})")
            continue

        print(f"\n--- Législature {leg}{' (courante, re-téléchargée)' if current else ''} ---")
        scrutins = load_scrutins(leg, force=current)
        matrix, types = build_matrix(leg, scrutins, organes)

        out_path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")
        types_path.write_text(json.dumps(types, ensure_ascii=False), encoding="utf-8")
        n_scrutins = len(set(k for v in matrix.values() for k in v))
        print(f"  Matrice : {len(matrix)} groupes × {n_scrutins} scrutins -> {out_path.name}")
        print(f"  Groupes : {sorted(matrix.keys())}")


if __name__ == "__main__":
    main()
