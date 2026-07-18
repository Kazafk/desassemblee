"""
Tests unitaires du pipeline Désassemblée.

Lancement :  python -m unittest discover -s scripts -p "test_*.py" -v
        ou : python scripts/test_pipeline.py
"""
import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from common import SESSIONS, SESSION_YEARS
from compute_scores import (
    bootstrap_ci,
    calibrate,
    canonicalize,
    get_ches_scores_for_session,
    slugs_for_session,
)
from fetch_clair import SCRUTIN_FIELDS, trim_scrutin

MAPPING_FILE = Path(__file__).parent / "groups_mapping.json"


class TestCalibrate(unittest.TestCase):
    def test_relation_lineaire_parfaite(self):
        pca = {"a": -1.0, "b": 0.0, "c": 1.0}
        ches = {"a": -8.0, "b": 0.0, "c": 8.0}
        calibrated, r2 = calibrate(pca, ches, verbose=False)
        self.assertAlmostEqual(r2, 1.0, places=6)
        self.assertAlmostEqual(calibrated["a"], -8.0, places=6)
        self.assertAlmostEqual(calibrated["c"], 8.0, places=6)

    def test_signe_pca_inverse_corrige_par_la_pente(self):
        # Le signe d'un axe PCA est arbitraire : si l'axe est inversé,
        # la pente négative de la régression doit réorienter les scores.
        pca = {"gauche": 1.0, "centre": 0.0, "droite": -1.0}
        ches = {"gauche": -7.0, "centre": 0.0, "droite": 7.0}
        calibrated, r2 = calibrate(pca, ches, verbose=False)
        self.assertAlmostEqual(r2, 1.0, places=6)
        self.assertLess(calibrated["gauche"], calibrated["droite"])

    def test_extrapolation_sur_parti_hors_ches(self):
        pca = {"a": -1.0, "b": 1.0, "inconnu": 0.5}
        ches = {"a": -5.0, "b": 5.0}
        calibrated, _ = calibrate(pca, ches, verbose=False)
        self.assertAlmostEqual(calibrated["inconnu"], 2.5, places=6)

    def test_clip_aux_bornes(self):
        pca = {"a": -1.0, "b": 1.0, "extreme": 100.0}
        ches = {"a": -9.0, "b": 9.0}
        calibrated, _ = calibrate(pca, ches, verbose=False)
        self.assertEqual(calibrated["extreme"], 10.0)

    def test_fallback_normalisation_sans_ches(self):
        pca = {"a": 2.0, "b": 4.0, "c": 6.0}
        calibrated, r2 = calibrate(pca, {}, verbose=False)
        self.assertEqual(r2, 0.0)
        self.assertEqual(calibrated["a"], -10.0)
        self.assertEqual(calibrated["c"], 10.0)
        self.assertAlmostEqual(calibrated["b"], 0.0, places=6)

    def test_dict_vide(self):
        calibrated, r2 = calibrate({}, {}, verbose=False)
        self.assertEqual(calibrated, {})
        self.assertEqual(r2, 0.0)


class TestCanonicalize(unittest.TestCase):
    def test_moyenne_des_doublons(self):
        # Session 17 : NFP et LFI-NFP pointent tous deux vers lfi
        # (canonicalize vectorise : scalaires -> vecteurs à 1 composante)
        raw = {"nfp": -4.0, "lfi-nfp": -6.0, "rn": 8.0}
        mapping = {"nfp": "lfi", "lfi-nfp": "lfi", "rn": "rn"}
        result = canonicalize(raw, mapping)
        self.assertAlmostEqual(result["lfi"][0], -5.0)
        self.assertEqual(result["rn"][0], 8.0)

    def test_moyenne_vectorielle(self):
        raw = {"a": [1.0, 3.0], "b": [3.0, 5.0]}
        mapping = {"a": "x", "b": "x"}
        result = canonicalize(raw, mapping)
        self.assertEqual(result["x"], [2.0, 4.0])

    def test_ignore_slugs_non_mappes(self):
        raw = {"ni": 1.0, "ps": -3.0}
        mapping = {"ni": None, "ps": "ps"}
        result = canonicalize(raw, mapping)
        self.assertNotIn(None, result)
        self.assertEqual(set(result), {"ps"})


class TestSlugsForSession(unittest.TestCase):
    def setUp(self):
        self.mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))

    def test_slugs_en_minuscules(self):
        slugs = slugs_for_session("17", self.mapping)
        for k in slugs:
            self.assertEqual(k, k.lower(), f"slug non minuscule : {k}")

    def test_commentaires_exclus(self):
        for session in SESSIONS:
            slugs = slugs_for_session(session, self.mapping)
            self.assertNotIn("_comment", slugs)

    def test_toutes_sessions_presentes(self):
        for session in SESSIONS:
            self.assertTrue(
                slugs_for_session(session, self.mapping),
                f"session_{session} absente ou vide dans groups_mapping.json",
            )

    def test_lt_mappe_vers_liot(self):
        # Libertés et Territoires est le prédécesseur de LIOT, pas d'Horizons
        slugs = slugs_for_session("15", self.mapping)
        self.assertEqual(slugs.get("lt"), "liot")


class TestMappingConsistency(unittest.TestCase):
    def test_slugs_canoniques_connus_de_build_data(self):
        from build_data import PARTY_META
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
        for session in SESSIONS:
            for api_slug, info in mapping[f"session_{session}"].items():
                if api_slug == "_comment" or info.get("canonical") is None:
                    continue
                self.assertIn(
                    info["canonical"], PARTY_META,
                    f"session {session} : '{info['canonical']}' absent de PARTY_META",
                )

    def test_ches_canonical_connus_de_build_data(self):
        from build_data import PARTY_META
        from fetch_ches import CHES_TO_CANONICAL
        for ches_name, canon in CHES_TO_CANONICAL.items():
            self.assertIn(canon, PARTY_META, f"CHES '{ches_name}' -> '{canon}' absent de PARTY_META")


class TestChesWaveSelection(unittest.TestCase):
    def setUp(self):
        self.ches_data = [
            {"year": y, "canonical": "ps", "score": -3.0}
            for y in (2014, 2019, 2024)
        ]

    def test_session_14_utilise_vague_2014(self):
        # Bug historique : la session 14 était calibrée sur CHES 2019
        scores = get_ches_scores_for_session(self.ches_data, "14", {})
        self.assertTrue(scores)  # la vague choisie doit contenir ps

    def test_vague_la_plus_proche_du_milieu(self):
        # Sessions et vague attendue (milieu de session)
        expected = {"14": 2014, "15": 2019, "16": 2024, "17": 2024}
        for session, wave in expected.items():
            data = [
                {"year": y, "canonical": f"p{y}", "score": 0.0}
                for y in (2014, 2019, 2024)
            ]
            scores = get_ches_scores_for_session(data, session, {})
            self.assertEqual(
                set(scores), {f"p{wave}"},
                f"session {session} : vague attendue {wave}, obtenu {set(scores)}",
            )


class TestTrimScrutin(unittest.TestCase):
    def test_ne_garde_que_les_champs_utiles(self):
        raw = {
            "numero": 42, "date": "2024-01-01", "session": "17",
            "importance": 3, "titre": "Test",
            "texte_integral": "x" * 10000, "amendements": [1, 2, 3],
        }
        trimmed = trim_scrutin(raw)
        self.assertEqual(set(trimmed), set(SCRUTIN_FIELDS))

    def test_champs_manquants_toleres(self):
        trimmed = trim_scrutin({"numero": 1})
        self.assertEqual(trimmed, {"numero": 1})


class TestBootstrapCI(unittest.TestCase):
    def test_ic_coherents_sur_matrice_synthetique(self):
        # 6 groupes, 40 scrutins : votes structurés par un axe latent + bruit
        rng = np.random.default_rng(0)
        positions = np.array([-1.0, -0.6, -0.2, 0.2, 0.6, 1.0])
        cutpoints = rng.uniform(-1, 1, 40)
        mat = np.sign(positions[:, None] - cutpoints[None, :]).astype(np.float32)
        # 5 % de bruit
        noise = rng.random(mat.shape) < 0.05
        mat[noise] *= -1

        groupes = ["g1", "g2", "g3", "g4", "g5", "g6"]
        mapping = {g: g for g in groupes}
        ches = {"g1": -8.0, "g3": -2.0, "g4": 2.0, "g6": 8.0}

        ci = bootstrap_ci(mat, groupes, mapping, ches, n_boot=50)

        self.assertEqual(set(ci), set(groupes))
        for g, (lo, hi) in ci.items():
            self.assertLessEqual(lo, hi, f"{g} : IC inversé [{lo}, {hi}]")
        # L'ordre idéologique doit être préservé (centres des IC croissants)
        centers = [(ci[g][0] + ci[g][1]) / 2 for g in groupes]
        self.assertEqual(centers, sorted(centers))

    def test_matrice_trop_petite_retourne_vide(self):
        self.assertEqual(bootstrap_ci(np.zeros((3, 5)), ["a", "b", "c"], {}, {}), {})


class TestSessionYears(unittest.TestCase):
    def test_sessions_contigues(self):
        for a, b in zip(SESSIONS, SESSIONS[1:]):
            self.assertEqual(
                SESSION_YEARS[a][1], SESSION_YEARS[b][0],
                f"trou entre sessions {a} et {b}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
