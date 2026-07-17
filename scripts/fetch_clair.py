"""
Télécharge les données de votes de l'Assemblée Nationale depuis l'API CLAIR
et les met en cache localement en JSON.

API CLAIR : https://api.clair.vote/api/v1
Rate limit : 10 req/min (pause de 6s entre requêtes)
Sans authentification.

Notes sur l'API (découvertes empiriques) :
- Réponse toujours en {"data": [...], "meta": {...}}
- Les filtres session= et dateMin= ne fonctionnent PAS côté serveur
- Tri par défaut : plus récent en premier (numero décroissant)
- Le champ `session` dans chaque scrutin permet de filtrer côté client

Stratégie :
- Fetch complet de TOUS les scrutins (metadata), mis en cache
- Sélection des scrutins "importance >= 2" par session pour la matrice de votes
- Re-fetch incrémental : seulement les nouveaux scrutins (numero > max connu)
"""
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

# Force UTF-8 sur stdout/stderr (Windows cp1252 sinon)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "raw" / "clair"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://api.clair.vote/api/v1"
CHAMBRE = "assemblee"
REQUEST_DELAY = 6.5  # secondes (rate limit : 10 req/min)

# Sessions législatives (numéro session → plage d'années approximative)
SESSION_YEARS = {
    "14": (2012, 2017),
    "15": (2017, 2022),
    "16": (2022, 2024),
    "17": (2024, 2030),
}

# Nombre max de scrutins à utiliser par session pour la matrice de votes
MAX_SCRUTINS_PER_SESSION = 300


def get(path: str, params: dict | None = None, retries: int = 3) -> dict:
    url = f"{BASE_URL}{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("x-ratelimit-reset", 60))
                print(f"  Rate limit, attente {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            print(f"  Erreur ({e}), retry {attempt + 1}/{retries}...")
            time.sleep(10)
    return {}


def fetch_groupes() -> list[dict]:
    cache_path = CACHE_DIR / "groupes_current.json"
    if cache_path.exists():
        print("  Groupes : chargés depuis le cache")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    print("  Fetch groupes (session courante)...")
    data = get("/groupes", {"chambre": CHAMBRE, "limit": 100})
    groupes = data.get("data", [])
    cache_path.write_text(json.dumps(groupes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {len(groupes)} groupes enregistrés")
    return groupes


def fetch_all_scrutins() -> list[dict]:
    """
    Charge tous les scrutins AN depuis l'API (ou le cache).
    Retourne la liste complète triée par numero croissant.
    """
    cache_path = CACHE_DIR / "scrutins_all.json"

    # Charger le cache existant
    existing = []
    max_numero = 0
    if cache_path.exists():
        existing = json.loads(cache_path.read_text(encoding="utf-8"))
        if existing:
            max_numero = max(s["numero"] for s in existing)
            print(f"  Cache : {len(existing)} scrutins (dernier n°{max_numero})")

    # Fetch page 1 pour connaître le total et le plus récent
    print("  Fetch scrutins (page 1)...")
    first = get("/scrutins", {"chambre": CHAMBRE, "limit": 100})
    meta = first.get("meta", {})
    total = meta.get("total", 0)
    items_p1 = first.get("data", [])

    if not items_p1:
        print("  Aucun scrutin retourné")
        return existing

    latest_numero = items_p1[0]["numero"] if items_p1 else 0

    if max_numero >= latest_numero:
        print(f"  Cache à jour (n°{max_numero} = dernier disponible)")
        return existing

    new_count = latest_numero - max_numero
    print(f"  {new_count} nouveaux scrutins a telecharger (n.{max_numero+1} -> n.{latest_numero})")

    # Collecter les nouveaux scrutins
    new_scrutins = [s for s in items_p1 if s["numero"] > max_numero]
    page = 2

    while True:
        oldest_on_page = new_scrutins[-1]["numero"] if new_scrutins else latest_numero
        if oldest_on_page <= max_numero:
            break

        meta = first.get("meta", {}) if page == 2 else {}
        data = get("/scrutins", {"chambre": CHAMBRE, "limit": 100, "page": page})
        items = data.get("data", [])
        page_meta = data.get("meta", {})

        if not items:
            break

        new_on_page = [s for s in items if s["numero"] > max_numero]
        new_scrutins.extend(new_on_page)

        if len(new_on_page) < len(items):
            break  # On a rattrapé le cache

        if not page_meta.get("hasNext", False):
            break

        page += 1
        if page % 10 == 0:
            print(f"  Page {page} (n.{items[-1]['numero']} -> {items[0]['numero']})...")

    all_scrutins = existing + new_scrutins
    # Trier par numero croissant
    all_scrutins.sort(key=lambda s: s["numero"])
    # Dédupliquer
    seen = set()
    deduped = []
    for s in all_scrutins:
        if s["numero"] not in seen:
            seen.add(s["numero"])
            deduped.append(s)

    cache_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {len(deduped)} scrutins total enregistrés")
    return deduped


def select_scrutins_per_session(all_scrutins: list[dict]) -> dict[str, list[dict]]:
    """
    Groupe les scrutins par session et sélectionne les plus importants.
    Retourne {session_str: [scrutins sélectionnés]}
    """
    by_session: dict[str, list[dict]] = defaultdict(list)
    for s in all_scrutins:
        session = str(s.get("session", "?"))
        if session in SESSION_YEARS:
            by_session[session].append(s)

    selected = {}
    for session, scrutins in by_session.items():
        # Trier par importance décroissante, puis par numero décroissant
        scrutins_sorted = sorted(scrutins, key=lambda s: (-s.get("importance", 0), -s["numero"]))
        # Prendre importance >= 2 ou les MAX_SCRUTINS_PER_SESSION premiers
        important = [s for s in scrutins_sorted if s.get("importance", 0) >= 2]
        if len(important) < 50:
            # Pas assez d'importants, prendre les premiers sans filtre
            important = scrutins_sorted[:MAX_SCRUTINS_PER_SESSION]
        else:
            important = important[:MAX_SCRUTINS_PER_SESSION]
        selected[session] = important
        print(f"  Session {session} : {len(scrutins)} scrutins total, {len(important)} sélectionnés")

    return selected


def fetch_votes_pour_scrutin(numero: int) -> list[dict]:
    """Retourne les votes individuels pour un scrutin (liste brute)."""
    # Certains scrutins ont jusqu'à 577 votants (nombre de députés)
    data = get(f"/scrutins/{numero}/votes", {"limit": 600})
    return data.get("data", [])


def build_vote_matrix(session: str, scrutins: list[dict]) -> dict:
    """
    Construit la matrice de votes agrégés par groupe pour une session.
    Retourne : { groupe_slug: { "scrutin_N|date": +1|-1|0 } }
    """
    matrix_path = CACHE_DIR / f"session_{session}_vote_matrix.json"

    # Pour les sessions archivées, réutiliser le cache
    if matrix_path.exists() and session != "17":
        print(f"  Matrice session {session} : chargée depuis le cache")
        return json.loads(matrix_path.read_text(encoding="utf-8"))

    ENCODE = {"pour": 1, "contre": -1, "abstention": 0}
    matrix: dict[str, dict[str, int]] = defaultdict(dict)
    total = len(scrutins)

    for i, scrutin in enumerate(scrutins):
        numero = scrutin["numero"]
        date = str(scrutin.get("date", ""))[:10]

        if i % 20 == 0:
            print(f"  Session {session} : scrutin {i+1}/{total} (n°{numero}, {date})...")

        try:
            votes = fetch_votes_pour_scrutin(numero)
        except Exception as e:
            print(f"    Erreur n°{numero}: {e}")
            continue

        # Agréger par groupe
        groupe_votes: dict[str, list[int]] = defaultdict(list)
        for vote in votes:
            parl = vote.get("parlementaire", {})
            grp = parl.get("groupe", {})
            slug = grp.get("slug") if isinstance(grp, dict) else None
            position = vote.get("position", "")
            if slug and position in ENCODE:
                groupe_votes[slug].append(ENCODE[position])

        # Vote majoritaire par groupe
        scrutin_key = f"{numero}|{date}"
        for slug, vals in groupe_votes.items():
            if vals:
                avg = sum(vals) / len(vals)
                matrix[slug][scrutin_key] = 1 if avg > 0.3 else (-1 if avg < -0.3 else 0)

    result = dict(matrix)
    matrix_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Matrice session {session} : {len(result)} groupes x {total} scrutins")
    return result


def main() -> None:
    print(f"=== fetch_clair.py --- {datetime.now():%Y-%m-%d %H:%M} ===\n")

    print("--- Groupes ---")
    fetch_groupes()

    print("\n--- Scrutins (tous) ---")
    all_scrutins = fetch_all_scrutins()

    print("\n--- Selection par session ---")
    selected = select_scrutins_per_session(all_scrutins)

    print("\n--- Matrices de votes ---")
    for session, scrutins in sorted(selected.items()):
        print(f"\nSession {session} ({len(scrutins)} scrutins)...")
        build_vote_matrix(session, scrutins)

    print("\nDone.")


if __name__ == "__main__":
    main()
