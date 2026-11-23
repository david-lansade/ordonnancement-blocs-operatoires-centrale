"""Tests du score de robustesse (src/robustesse.py) et de son calcul dans la simulation (src/sma.py).

Lancer : .venv\\Scripts\\python -m pytest tests
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_aleas_sma import HIST, SANS_ALEA, planning  # noqa: E402

from src.aleas import parametres, tirer_aleas  # noqa: E402
from src.modele import details  # noqa: E402
from src.robustesse import CRITERES, OBJECTIF_REMPLISSAGE, niveau, reglages_valides, score_robustesse  # noqa: E402
from src.sma import ModeleHopital, simuler  # noqa: E402

INDICATEURS = sorted({k for c in CRITERES for k in c["indicateurs"]} | {"liberees_depart"})


def sans_incident():
    return {k: 0 for k in INDICATEURS}


def test_poids_et_score_parfait():
    assert sum(c["poids"] for c in CRITERES) == 100
    res = score_robustesse(sans_incident(), 600)
    assert res["score_robustesse"] == 100
    assert all(res["perte_" + c["cle"]] == 0 for c in CRITERES)


def test_perte_lineaire_puis_plafonnee():
    """Pour 100 patients, la moitié du seuil coûte la moitié du poids ; au-delà du seuil, tout le poids, pas plus."""
    lits = next(c for c in CRITERES if c["cle"] == "lits")
    I = {**sans_incident(), "lits_nuits_manquants": lits["seuil"] / 2}
    assert score_robustesse(I, 100)["perte_lits"] == pytest.approx(lits["poids"] / 2)
    assert score_robustesse(I, 200)["perte_lits"] == pytest.approx(lits["poids"] / 4)   # ramené à 100 patients
    I["lits_nuits_manquants"] = 50 * lits["seuil"]
    res = score_robustesse(I, 100)
    assert res["perte_lits"] == lits["poids"] and res["score_robustesse"] == 100 - lits["poids"]


def test_vacations_liberees_en_part_des_vacations_libres():
    lib = next(c for c in CRITERES if c["cle"] == "liberees")
    I = {**sans_incident(), "liberees_depart": 20, "liberees_rouvertes": 2}   # 10 %
    assert score_robustesse(I, 600)["perte_liberees"] == pytest.approx(lib["poids"] * 10 / lib["seuil"])
    I["liberees_depart"] = 0   # aucune vacation libérée au départ : rien à perdre
    assert score_robustesse(I, 600)["perte_liberees"] == 0


def test_quatre_familles():
    familles = {}
    for c in CRITERES:
        familles[c["famille"]] = familles.get(c["famille"], 0) + c["poids"]
    attendu = {"Sécurité des patients": 43.7, "Parcours des patients": 24.3, "Organisation du bloc": 14.7,
               "Qualité du planning exécuté": 17.3}
    assert familles.keys() == attendu.keys()
    assert all(familles[f] == pytest.approx(v) for f, v in attendu.items())


def test_qualite_executee_en_base_directe():
    """Qualité du planning exécuté : valeur absolue (lits d'écart, points de remplissage), pas ramenée à 100 patients."""
    for cle, ind in (("lissage_lits", "ecart_cible_lits_execute"), ("lissage_places", "ecart_cible_places_execute"),
                     ("remplissage", "manque_remplissage")):
        c = next(k for k in CRITERES if k["cle"] == cle)
        I = {**sans_incident(), ind: c["seuil"] / 2}
        assert score_robustesse(I, 600)["perte_" + cle] == pytest.approx(c["poids"] / 2)
        assert score_robustesse(I, 100)["perte_" + cle] == pytest.approx(c["poids"] / 2)


def test_qualite_prevue_comme_le_modele():
    """Lissage et remplissage du planning prévu : mêmes valeurs que modele.details (écart à la cible des
    termes W1 / W1′, remplissage des vacations utilisées), patients programmés seulement."""
    d, sol = planning()
    m = ModeleHopital(d, sol, tirer_aleas(d, HIST, {}, 0), "statique", {}, rotation=0)
    ref = details(sol, {**d, "lits_reserves": None, "places_reservees": None, "rotation": 0})["indicateurs"]
    assert m.qualite_prevue["lits"] == pytest.approx(ref["ecart_type_lits"], abs=0.006)
    assert m.qualite_prevue["places"] == pytest.approx(ref["ecart_type_places"], abs=0.006)
    assert m.qualite_prevue["remplissage"] == pytest.approx(ref["remplissage_vacations_utilisees"], abs=0.0006)


def test_qualite_sans_imprevu():
    """Sans aucun imprévu, le planning exécuté est le planning prévu : ni lissage ni remplissage dégradés, et le
    score juge sa qualité au niveau du planning prévu (valeur absolue)."""
    d, sol = planning()
    T = d["nb_jours"]
    d = {**d, "cap_lits": [1000] * T, "cap_ambu": [0 if t % 7 >= 5 else 1000 for t in range(T)]}   # aucune surcapacité
    calme = {**SANS_ALEA, "tolerance_depassement": 10000, "cout_deplacement": 1e9}
    for strategie in ("statique", "sma"):
        I = simuler(d, sol, tirer_aleas(d, HIST, calme, 0), strategie, calme, rotation=0)["indicateurs"]
        assert I["patients_reportes"] == 0 and I["patients_dates_changees"] == 0
        assert I["ecart_cible_lits_execute"] == I["ecart_cible_lits_prevu"] > 0
        assert I["ecart_cible_places_execute"] == I["ecart_cible_places_prevu"]
        assert I["hausse_ecart_lits"] == I["hausse_ecart_places"] == I["baisse_remplissage"] == 0
        assert I["taux_occupation_bloc_utilisees"] == pytest.approx(I["remplissage_prevu"], abs=0.0006)
        assert I["manque_remplissage"] == pytest.approx(max(0.0, 100 * (OBJECTIF_REMPLISSAGE - I["remplissage_prevu"])), abs=0.1)
        for cle, ind in (("lissage_lits", "ecart_cible_lits_prevu"), ("lissage_places", "ecart_cible_places_prevu"),
                         ("remplissage", "manque_remplissage")):
            c = next(k for k in CRITERES if k["cle"] == cle)
            assert I["perte_" + cle] == pytest.approx(c["poids"] * min(1.0, I[ind] / c["seuil"]), abs=0.01)


def test_reglages_modifies():
    """Seuils et poids modifiés dans l'onglet Simulation : remplacent les valeurs par défaut du critère."""
    I = {**sans_incident(), "lits_nuits_manquants": 4}   # 4 lits manquants pour 100 patients
    defaut = score_robustesse(I, 100)["perte_lits"]
    modifie = score_robustesse(I, 100, {"lits": {"seuil": 8, "poids": 40}})
    assert modifie["perte_lits"] == pytest.approx(40 * 4 / 8) != defaut
    assert modifie["score_robustesse"] == pytest.approx(100 - 20)
    # valeurs invalides ou clés inconnues : ignorées, le critère garde ses valeurs par défaut
    for mauvais in ({"lits": {"seuil": 0, "poids": 40}}, {"lits": {"seuil": "a"}}, {"inconnu": {"seuil": 1, "poids": 1}}):
        assert reglages_valides(mauvais) is None
        assert score_robustesse(I, 100, mauvais)["perte_lits"] == defaut


def test_reglages_transmis_a_la_simulation():
    """parametres garde les réglages valides (même appliqué deux fois) et la simulation les utilise."""
    reglages = {"lits": {"seuil": 5, "poids": 0}, "dates_changees": {"seuil": 1, "poids": 50}}
    p = parametres({"reglages_score": reglages})
    assert p["reglages_score"] == reglages and parametres(p)["reglages_score"] == reglages
    assert "reglages_score" not in parametres({})
    d, sol = planning()
    I = simuler(d, sol, tirer_aleas(d, HIST, p, 0), "sma", p, rotation=0)["indicateurs"]
    assert I["perte_lits"] == 0   # poids nul
    attendu = 50 * min(1.0, 100 * I["patients_dates_changees"] / I["patients_planning"] / 1)
    assert I["perte_dates_changees"] == pytest.approx(attendu, abs=0.01)


def test_niveaux():
    assert [niveau(s) for s in (100, 80, 79.9, 65, 50, 49.9, 0)] == \
        ["robuste", "robuste", "acceptable", "acceptable", "fragile", "critique", "critique"]


@pytest.mark.parametrize("strategie", ["statique", "sma"])
def test_score_calcule_dans_la_simulation(strategie):
    d, sol = planning()
    I = simuler(d, sol, tirer_aleas(d, HIST, {}, 0), strategie, {}, rotation=0)["indicateurs"]
    assert I["patients_planning"] == sum(v >= 0 for v in sol)
    pertes = [I["perte_" + c["cle"]] for c in CRITERES]
    assert all(0 <= p <= c["poids"] for p, c in zip(pertes, CRITERES))
    assert I["score_robustesse"] == pytest.approx(100 - sum(pertes), abs=0.06)
    assert 0 <= I["places_manquantes"]
