"""
Analyse ponctuelle : pourquoi le PS se rapproche-t-il de la gauche
dans les scores 2025-2026 ?

1. Trajectoire du score PS avec IC
2. Taux d'accord de vote SOC vs chaque groupe, par année (session 17)
3. Décomposition par type de scrutin (censure, budget, autres)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 1. Trajectoire PS ──────────────────────────────────────────────
scores = json.loads((ROOT / "data" / "processed" / "scores_computed.json").read_text(encoding="utf-8"))
print("=== Trajectoire PS ===")
for e in scores["ps"]:
    ci = e.get("ci")
    ci_str = f"  IC=[{ci[0]:+.1f}, {ci[1]:+.1f}]" if ci else ""
    print(f"  {e['year']}: {e['score']:+.2f}  ({e['source']}, r2={e.get('r2', '-')}){ci_str}")

# ── 2. Taux d'accord SOC vs autres groupes, par année ─────────────
matrix = json.loads((ROOT / "data" / "raw" / "clair" / "session_17_vote_matrix.json").read_text(encoding="utf-8"))

soc = matrix.get("soc", {})
groupes = [g for g in matrix if g not in ("soc", "ni")]

print("\n=== Taux d'accord de vote SOC vs autres groupes (session 17) ===")
print(f"{'groupe':12s}", end="")
years = ["2024", "2025", "2026"]
for y in years:
    print(f"{y:>12s}", end="")
print(f"{'n_commun':>10s}")

for g in sorted(groupes):
    row = matrix[g]
    print(f"{g:12s}", end="")
    total_common = 0
    for y in years:
        commun = [k for k in soc if k in row and k.split("|")[1][:4] == y]
        total_common += len(commun)
        if len(commun) < 20:
            print(f"{'—':>12s}", end="")
            continue
        accord = sum(1 for k in commun if soc[k] == row[k]) / len(commun)
        print(f"{accord:>11.0%} ", end="")
    print(f"{total_common:>10d}")

# ── 3. Positions du SOC lui-même par année ─────────────────────────
print("\n=== Répartition des positions SOC par année ===")
for y in years:
    keys = [k for k in soc if k.split("|")[1][:4] == y]
    if not keys:
        continue
    pour = sum(1 for k in keys if soc[k] == 1)
    contre = sum(1 for k in keys if soc[k] == -1)
    abst = sum(1 for k in keys if soc[k] == 0)
    n = len(keys)
    print(f"  {y}: {n} scrutins — pour {pour/n:.0%}, contre {contre/n:.0%}, abstention {abst/n:.0%}")

# ── 4. Où le SOC diverge-t-il de LFI mais converge avec EPR ? ─────
print("\n=== Croisement SOC / LFI-NFP / EPR (2025 vs 2026) ===")
lfi = matrix.get("lfi-nfp", {})
epr = matrix.get("epr", {})
for y in ("2025", "2026"):
    keys = [k for k in soc if k in lfi and k in epr and k.split("|")[1][:4] == y]
    n = len(keys)
    if not n:
        continue
    avec_lfi_contre_epr = sum(1 for k in keys if soc[k] == lfi[k] != epr[k])
    avec_epr_contre_lfi = sum(1 for k in keys if soc[k] == epr[k] != lfi[k])
    avec_les_deux = sum(1 for k in keys if soc[k] == epr[k] == lfi[k])
    seul = n - avec_lfi_contre_epr - avec_epr_contre_lfi - avec_les_deux
    print(f"  {y} ({n} scrutins) : avec LFI contre EPR {avec_lfi_contre_epr/n:.0%} | "
          f"avec EPR contre LFI {avec_epr_contre_lfi/n:.0%} | "
          f"avec les deux {avec_les_deux/n:.0%} | seul {seul/n:.0%}")
