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

from common import SESSION_YEARS, CURRENT_SESSION

BASE_URL = "https://api.clair.vote/api/v1"
CHAMBRE = "assemblee"
REQUEST_DELAY = 6.5  # secondes (rate limit : 10 req/min)

# Seuls champs conservés dans le cache scrutins (le JSON brut de l'API
# pèse ~430 Mo avec les textes complets ; trimmé il tombe à quelques Mo)
SCRUTIN_FIELDS = ("numero", "date", "session", "importance", "titre")


def trim_scrutin(s: dict) -> dict:
    return {k: s[k] for k in SCRUTIN_FIELDS if k in s}

# Nombre max de scrutins à utiliser par session pour la matrice de votes
MAX_SCRUTINS_PER_SESSION = 300


def get(path: str, params: dict | None = None, retries: int = 3) -> dict:
    url = f"{BASE_URL}{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                try:
                    wait = int(float(r.headers.get("x-ratelimit-reset", "60")))
                except ValueError:
                    wait = 60
                # Certaines API renvoient un timestamp UNIX, pas une durée
                if wait > 1_000_000:
                    wait = int(wait - time.time())
                wait = min(max(wait, 5), 180)  # borné entre 5s et 3min
                print(f"  Rate limit, attente {wait}s...", flush=True)
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
            # Migration : trimmer les caches anciens qui contiennent
            # encore tous les champs bruts de l'API (~430 Mo)
            if any(k not in SCRUTIN_FIELDS for k in existing[0]):
                existing = [trim_scrutin(s) for s in existing]
                cache_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
                print(f"  Cache migre (champs reduits a {SCRUTIN_FIELDS})")
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

    all_scrutins = existing + [trim_scrutin(s) for s in new_scrutins]
    # Trier par numero croissant
    all_scrutins.sort(key=lambda s: s["numero"])
    # Dédupliquer
    seen = set()
    deduped = []
    for s in all_scrutins:
        if s["numero"] not in seen:
            seen.add(s["numero"])
            deduped.append(s)

    # Pas d'indent : fichier volumineux, l'indentation double sa taille
    cache_path.write_text(json.dumps(deduped, ensure_ascii=False), encoding="utf-8")
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
    """
    Retourne les votes individuels pour un scrutin.
    L'API impose limit <= 100 : pagination via meta.hasNext
    (jusqu'à 6 pages pour un scrutin où les 577 députés votent).
    """
    votes: list[dict] = []
    page = 1
    while True:
        data = get(f"/scrutins/{numero}/votes", {"limit": 100, "page": page})
        votes.extend(data.get("data", []))
        if not data.get("meta", {}).get("hasNext"):
            break
        page += 1
    return votes


def build_vote_matrix(session: str, scrutins: list[dict]) -> dict:
    """
    Construit la matrice de votes agrégés par groupe pour une session.
    Retourne : { groupe_slug: { "scrutin_N|date": +1|-1|0 } }
    """
    matrix_path = CACHE_DIR / f"session_{session}_vote_matrix.json"

    # Pour les sessions archivées, réutiliser le cache
    if matrix_path.exists() and session != CURRENT_SESSION:
        print(f"  Matrice session {session} : chargée depuis le cache")
        return json.loads(matrix_path.read_text(encoding="utf-8"))

    ENCODE = {"pour": 1, "contre": -1, "abstention": 0}
    matrix: dict[str, dict[str, int]] = defaultdict(dict)

    # Reprendre un checkpoint partiel si présent (crash / interruption)
    done_keys: set[str] = set()
    if matrix_path.exists():
        try:
            partial = json.loads(matrix_path.read_text(encoding="utf-8"))
            for slug, votes_dict in partial.items():
                matrix[slug].update(votes_dict)
            done_keys = {k for v in partial.values() for k in v}
            print(f"  Session {session} : checkpoint charge ({len(done_keys)} scrutins deja traites)", flush=True)
        except (json.JSONDecodeError, AttributeError):
            print(f"  Session {session} : checkpoint corrompu, repart de zero", flush=True)

    total = len(scrutins)
    processed = 0

    for i, scrutin in enumerate(scrutins):
        numero = scrutin["numero"]
        date = str(scrutin.get("date", ""))[:10]

        if f"{numero}|{date}" in done_keys:
            continue

        if i % 20 == 0:
            print(f"  Session {session} : scrutin {i+1}/{total} (n.{numero}, {date})...", flush=True)

        try:
            votes = fetch_votes_pour_scrutin(numero)
        except Exception as e:
            print(f"    Erreur n.{numero}: {e}", flush=True)
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

        processed += 1
        # Checkpoint périodique : un crash ne coûte plus que 25 scrutins
        if processed % 25 == 0:
            matrix_path.write_text(json.dumps(dict(matrix), ensure_ascii=False), encoding="utf-8")

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
