"""Tests de cohérence des trois façons d'évaluer F.

Lancer : .venv\\Scripts\\python -m pytest tests
"""
import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import modele  # noqa: E402
from src.modele import Evaluateur, EvaluateurVect, evaluer  # noqa: E402

W1 = W1b = 1
W2 = 0.001
W3 = 5


def evaluer_reference(solution, d):
    """Copie exacte de la version de référence (CLAUDE.md, section 6)."""
    charge = [0] * len(d["capacite"])
    lits = [0] * d["nb_jours"]
    places = [0] * d["nb_jours"]
    for i, v in enumerate(solution):
        charge[v] += d["duree"][i]
        if d["ambu"][i] == 1:
            places[d["jour"][v]] += 1
        else:
            debut, nuits = d["jour"][v], d["nuits"][i]
            if (debut + nuits) % 7 == 6:
                nuits += 1
            for t in range(debut, min(debut + nuits, d["nb_jours"])):
                lits[t] += 1
    cible_lits = [sum(lits) * e / sum(d["pers"]) for e in d["pers"]]
    poids = [e if C > 0 else 0 for e, C in zip(d["pers"], d["cap_ambu"])]
    cible_places = [sum(places) * p / sum(poids) for p in poids]
    p_lits = sum((n - c) ** 2 for n, c in zip(lits, cible_lits))
    p_places = sum((a - c) ** 2 for a, c in zip(places, cible_places))
    p_salles = sum((H - c) ** 2 for H, c in zip(d["capacite"], charge))
    p_retard = sum(max(0, d["jour"][v] - d["limite"][i]) for i, v in enumerate(solution))
    return W1 * p_lits + W1b * p_places + W2 * p_salles + W3 * p_retard


def instance(graine, N=120, semaines=4, options=True):
    rng = random.Random(graine)
    T = 7 * semaines
    jours = [t for t in range(T) if t % 7 < 5]
    jour_v = [rng.choice(jours) for _ in range(40)]
    d = {
        "nb_jours": T,
        "pers": [0.6 if t % 7 >= 5 else 1 for t in range(T)],
        "cap_lits": [12 if t % 7 >= 5 else 15 for t in range(T)],
        "cap_ambu": [0 if t % 7 >= 5 else 6 for t in range(T)],
        "jour": jour_v,
        "capacite": [rng.choice([240, 300, 570]) for _ in jour_v],
        "duree": [rng.uniform(20, 200) for _ in range(N)],
        "sigma": [rng.uniform(10, 40) for _ in range(N)],
        "ambu": [int(rng.random() < 0.5) for _ in range(N)],
        "limite": [rng.randrange(T) for _ in range(N)],
    }
    d["nuits"] = [0 if a else rng.randint(1, 6) for a in d["ambu"]]
    d["admissibles"] = [rng.sample(range(40), 6) for _ in range(N)]
    if options:
        d["poids_retard"] = [rng.choice([0.5, 1, 3]) for _ in range(N)]
        d["souhait"] = [rng.randrange(T) for _ in range(N)]
        d["tolerance"] = [rng.choice([0, 0, 2, 5]) for _ in range(N)]
        d["lits_reserves"] = [rng.uniform(0, 2) for _ in range(T)]
        d["places_reservees"] = [rng.uniform(0, 1) if t % 7 < 5 else 0 for t in range(T)]
        d["poids"] = {"W1": 1.3, "W2": 0.002, "W4": 20, "W5": 0.1, "W6": 0.7}
        d["penaliser_capacites"] = True
        d["z_beta"] = 1.28
    return d


@pytest.mark.parametrize("graine", range(5))
def test_identique_a_la_reference_sans_cles_ajoutees(graine):
    d = instance(graine, options=False)
    rng = random.Random(graine)
    sol = [rng.choice(a) for a in d["admissibles"]]
    assert evaluer(sol, d) == pytest.approx(evaluer_reference(sol, d), rel=1e-12)


@pytest.mark.parametrize("graine", range(5))
def test_vectorise_egal_evaluer(graine):
    d = instance(graine)
    rng = random.Random(graine)
    sol = [rng.choice(a) for a in d["admissibles"]]
    assert EvaluateurVect(d)(np.array(sol)) == pytest.approx(evaluer(sol, d), rel=1e-9)


@pytest.mark.parametrize("graine", range(5))
def test_incremental_egal_evaluer(graine):
    d = instance(graine)
    rng = random.Random(graine)
    sol = [rng.choice(a) for a in d["admissibles"]]
    ev = Evaluateur(d, sol)
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)
    for _ in range(500):
        i = rng.randrange(len(sol))
        v = rng.choice(d["admissibles"][i])
        F = ev.essayer(i, v)
        sol2 = list(sol)
        sol2[i] = v
        assert F == pytest.approx(evaluer(sol2, d), rel=1e-9, abs=1e-6)
        if rng.random() < 0.5:
            ev.deplacer(i, v)
            sol = sol2
        k = rng.randrange(len(sol))
        F = ev.essayer_echange(i, k)
        sol3 = list(sol)
        sol3[i], sol3[k] = sol[k], sol[i]
        assert F == pytest.approx(evaluer(sol3, d), rel=1e-9, abs=1e-6)
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)


def test_insertion_depuis_vide():
    d = instance(7)
    ev = Evaluateur(d, [-1] * len(d["duree"]))
    rng = random.Random(7)
    sol = []
    for i in range(len(d["duree"])):
        v = rng.choice(d["admissibles"][i])
        ev.deplacer(i, v)
        sol.append(v)
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)


def test_poids_par_defaut():
    assert (modele.W1, modele.W1b, modele.W2, modele.W3) == (1.0, 1.0, 0.001, 5.0)


def test_remplissage_avec_rotations():
    """Remplissage des salles = (Σ µ + rotation × (n − 1) par vacation) / Σ H ; sans clé rotation, inchangé ; F inchangé."""
    d = instance(7, options=False)
    rng = random.Random(7)
    sol = [rng.choice(a) for a in d["admissibles"]]
    sans = modele.details(sol, d)
    avec = modele.details(sol, {**d, "rotation": 12})
    n = np.bincount(sol, minlength=len(d["capacite"]))
    attendu = (sum(d["duree"]) + 12 * np.maximum(n - 1, 0).sum()) / sum(d["capacite"])
    assert avec["indicateurs"]["taux_remplissage_moyen"] == pytest.approx(attendu, abs=1e-3)
    assert avec["indicateurs"]["taux_remplissage_sans_rotation"] == sans["indicateurs"]["taux_remplissage_moyen"]
    assert avec["F"] == pytest.approx(sans["F"])


# ----- terme des salles « regroupement » (clé terme_salles, 2026-10-13) -----

def instance_regroupement(graine):
    d = instance(graine)
    d["terme_salles"] = "regroupement"
    d["epsilon_salles"] = 0.7
    return d


@pytest.mark.parametrize("graine", range(5))
def test_regroupement_vectorise_et_incremental_egaux_evaluer(graine):
    d = instance_regroupement(graine)
    rng = random.Random(graine)
    sol = [rng.choice(a) for a in d["admissibles"]]
    assert EvaluateurVect(d)(np.array(sol)) == pytest.approx(evaluer(sol, d), rel=1e-9)
    ev = Evaluateur(d, sol)
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)
    for _ in range(500):
        i = rng.randrange(len(sol))
        v = rng.choice(d["admissibles"][i])
        sol2 = list(sol)
        sol2[i] = v
        assert ev.essayer(i, v) == pytest.approx(evaluer(sol2, d), rel=1e-9, abs=1e-6)
        if rng.random() < 0.5:
            ev.deplacer(i, v)
            sol = sol2
        k = rng.randrange(len(sol))
        sol3 = list(sol)
        sol3[i], sol3[k] = sol[k], sol[i]
        assert ev.essayer_echange(i, k) == pytest.approx(evaluer(sol3, d), rel=1e-9, abs=1e-6)
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)


def test_regroupement_vider_puis_remplir_une_vacation():
    """Une vacation vidée ne coûte plus rien (incrémental = evaluer), puis redevient payante."""
    d = instance_regroupement(3)
    rng = random.Random(3)
    sol = [rng.choice(a) for a in d["admissibles"]]
    ev = Evaluateur(d, sol)
    v0 = max(set(sol), key=sol.count)
    for i in [i for i, v in enumerate(sol) if v == v0]:
        autre = next(v for v in d["admissibles"][i] if v != v0)
        ev.deplacer(i, autre)
        sol[i] = autre
    assert ev.nb[v0] == 0
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)
    assert modele.details(sol, d)["indicateurs"]["vacations_vides"] >= 1
    i = next(i for i, a in enumerate(d["admissibles"]) if v0 in a)
    ev.deplacer(i, v0)
    sol[i] = v0
    assert ev.valeur() == pytest.approx(evaluer(sol, d), rel=1e-9)


def test_regroupement_exemple_de_l_aide():
    """Tableau de l'onglet Technique : deux vacations de 5 h, quatre patients de 30 min dans chacune,
    déplacés un par un de la première vers la seconde (W2 = 0,05, ε = 0,5 ; ancien terme : W2 = 0,001)."""
    T = 7
    d = {"nb_jours": T, "pers": [1] * T, "cap_lits": [99] * T, "cap_ambu": [99] * T, "jour": [0, 1],
         "capacite": [300, 300], "duree": [30.0] * 8, "ambu": [1] * 8, "nuits": [0] * 8,
         "limite": [T] * 8, "admissibles": [[0, 1]] * 8}
    regroup = {**d, "terme_salles": "regroupement"}
    termes = lambda sol, dd: modele.details(sol, dd)["termes"]["salles"]   # noqa: E731
    attendu_ancien = [64.8, 66.6, 72.0, 81.0, 93.6]
    attendu = [21.6, 21.45, 21.0, 20.25, 4.2]
    for k in range(5):   # k patients déplacés de la vacation 0 vers la vacation 1
        sol = [0] * (4 - k) + [1] * (4 + k)
        assert termes(sol, d) == pytest.approx(attendu_ancien[k])
        assert termes(sol, regroup) == pytest.approx(attendu[k])
    assert modele.lire_poids(regroup)["W2"] == modele.W2_REGROUPEMENT == 0.05
    assert modele.EPSILON_SALLES == 0.5
