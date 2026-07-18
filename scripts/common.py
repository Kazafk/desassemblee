"""Constantes partagées du pipeline Désassemblée."""
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Sessions législatives couvertes (clés en str, cohérent avec le champ
# `session` de l'API CLAIR)
SESSIONS = ["14", "15", "16", "17"]

# Plages d'années approximatives de chaque législature
SESSION_YEARS = {
    "14": (2012, 2017),
    "15": (2017, 2022),
    "16": (2022, 2024),
    "17": (2024, 2030),
}

# Session active : toujours re-fetchée, jamais servie depuis le cache
CURRENT_SESSION = "17"
