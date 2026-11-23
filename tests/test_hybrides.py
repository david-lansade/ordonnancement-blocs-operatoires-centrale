"""Tests des méthodes hybrides (src/metaheuristiques/hybrides.py) et de l'exécution
parallèle de la comparaison.

Lancer : .venv\\Scripts\\python -m pytest tests
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_modele import instance  # noqa: E402

from src.comparaison import _executer_tout, lancer  # noqa: E402
from src.metaheuristiques import recuit_simule, solution_aleatoire  # noqa: E402
from src.metaheuristiques.hybrides import HYBRIDES, relais  # noqa: E402
from src.modele import evaluer  # noqa: E402

BUDGET = 3000


@pytest.mark.parametrize("cle", list(HYBRIDES))
def test_hybride_valide(cle):
    """Budget respecté, solution admissible, F annoncé = evaluer, courbe décroissante, résultat reproductible."""
    d = instance(3)
    init = solution_aleatoire(d, 3)
    r = HYBRIDES[cle](d, budget=BUDGET, graine=3, solution_initiale=init)
    assert r["evaluations"] <= BUDGET
    assert all(v in a for v, a in zip(r["solution"], d["admissibles"]))
    assert r["F"] == pytest.approx(evaluer(r["solution"], d), rel=1e-6)
    assert r["F"] <= evaluer(init, d) + 1e-6
    x = [p[0] for p in r["courbe"]]
    y = [p[1] for p in r["courbe"]]
    assert x == sorted(x) and all(b <= a + 1e-6 for a, b in zip(y, y[1:]))
    assert HYBRIDES[cle](d, budget=BUDGET, graine=3, solution_initiale=init)["F"] == r["F"]


def test_relais_ne_degrade_pas_la_premiere_etape():
    """La 2e étape repart de la meilleure solution de la 1re : F final <= F de la 1re étape seule."""
    d = instance(5)
    init = solution_aleatoire(d, 5)
    r = relais(d, [("recuit", 0.5), ("tabou", 0.5)], budget=BUDGET, graine=5, solution_initiale=init)
    seul = recuit_simule(d, budget=BUDGET // 2, graine=5, solution_initiale=init)
    assert r["etapes"][0]["F"] == pytest.approx(seul["F"])
    assert r["F"] <= seul["F"] + 1e-6
    assert sum(e["evaluations"] for e in r["etapes"]) == r["evaluations"] == BUDGET


def test_reglages_transmis_aux_hybrides():
    """lancer() donne aux hybrides leurs réglages propres et ceux des méthodes combinées."""
    d = instance(2)
    init = solution_aleatoire(d, 2)
    params = {"recuit": {"acceptation_initiale": 0.3}, "relais_recuit_tabou": {"part": 0.6}}
    r = lancer("relais_recuit_tabou", d, BUDGET, 2, init, params)
    assert [e["part"] for e in r["etapes"]] == [0.6, 0.4]
    r = lancer("memetique", d, BUDGET, 2, init, {"memetique": {"taille_population": 4, "budget_local": 200}})
    assert r["parametres"]["taille_population"] == 4 and r["parametres"]["budget_local"] == 200


def test_parallele_identique_au_sequentiel():
    """Chaque run garde sa graine : mêmes résultats avec ou sans processus parallèles."""
    d = instance(4, N=60)
    taches = [(a, s, d, 1500, solution_aleatoire(d, s), {}) for s in (0, 1)
              for a in ("recuit", "relais_recuit_tabou", "recuit_liste_tabou")]
    seq = _executer_tout(taches, 1, None)
    par = _executer_tout(taches, 2, None)
    assert {k: r["F"] for k, r in seq.items()} == {k: r["F"] for k, r in par.items()}
