"""Tests des aléas (src/aleas.py), du système multi-agents (src/sma.py) et de la
réoptimisation centralisée (aleas.reparer).

Lancer : .venv\\Scripts\\python -m pytest tests
"""
import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_modele import instance  # noqa: E402

from src.aleas import parametres, reparer, tirer_aleas  # noqa: E402
from src.modele import Evaluateur, evaluer  # noqa: E402
from src.sma import ModeleHopital, robustesse_plan, simuler  # noqa: E402

# historique d'urgences fictif : 400 jours, une urgence hospitalisée tous les 3 jours, une ambulatoire tous les 2 jours
HIST = {"debut": "2019-01-07", "nb_jours": 400, "hosp_entree": list(range(0, 400, 3)),
        "hosp_nuits": [1 + k % 4 for k in range(0, 400, 3)], "ambu_jour": list(range(0, 400, 2)), "covid": [140, 200]}
SANS_ALEA = {"intensite_urgences": 0.0, "taux_annulation": 0.0, "lits_fermes": 0, "retard_bloc": False,
             "part_prolongations": 0.0, "refus_avance": 0.0, "refus_report": 0.0}


def planning(graine=1):
    d = instance(graine, options=False)
    rng = random.Random(graine)
    return d, [rng.choice(a) for a in d["admissibles"]]


def test_aleas_reproductibles_et_independants_du_planning():
    d, _ = planning()
    a1, a2 = tirer_aleas(d, HIST, {}, 3), tirer_aleas(d, HIST, {}, 3)
    assert a1 == a2
    assert len(a1["annule"]) == len(d["duree"]) and len(a1["urg_ambu"]) == d["nb_jours"]
    assert all(0 <= dbt < fin <= d["nb_jours"] for _, dbt, fin in a1["urgences"])
    assert tirer_aleas(d, HIST, {}, 4) != a1


def test_sans_alea_les_trois_strategies_suivent_le_planning():
    """Sans aléa ni rotation, personne n'est reporté ni déplacé, et les lits simulés sont ceux du planning."""
    d, sol = planning()
    T = d["nb_jours"]
    d = {**d, "cap_lits": [1000] * T, "cap_ambu": [0 if t % 7 >= 5 else 1000 for t in range(T)]}   # aucune surcapacité
    aleas = tirer_aleas(d, HIST, SANS_ALEA, 0)
    ev = Evaluateur({**d, "lits_reserves": None}, sol)
    F = evaluer(sol, {**d, "penaliser_capacites": True})
    lits, places = np.asarray(ev.lits[:T]), np.asarray(ev.places[:T])
    semaine = np.arange(T) % 7 < 5
    for s in ("statique", "sma", "centralise"):
        r = simuler(d, sol, aleas, s, {**SANS_ALEA, "tolerance_depassement": 10000, "cout_deplacement": 1e9}, rotation=0)
        I = r["indicateurs"]
        assert I["patients_reportes"] == 0 and I["patients_dates_changees"] == 0, s
        assert I["patients_operes"] == len(sol)
        assert r["series"]["lits"] == pytest.approx(ev.lits[:d["nb_jours"]])
        # sans imprévu, F après la simulation est le F du planning, et les écarts-types sont ceux du planning
        assert I["F_apres"] == pytest.approx(F, abs=0.06)
        assert I["ecart_type_lits_semaine"] == pytest.approx(np.std(lits[semaine]), abs=0.006)
        assert I["ecart_type_lits_week_end"] == pytest.approx(np.std(lits[~semaine]), abs=0.006)
        assert I["ecart_type_places_semaine"] == pytest.approx(np.std(places[semaine]), abs=0.006)


def test_les_agents_reduisent_les_lits_manquants():
    """Lits fermés annoncés à l'avance : les agents déplacent des patients et font au moins aussi bien."""
    d, sol = planning(2)
    p = {**SANS_ALEA, "lits_fermes": 8, "fermeture_debut": 10, "fermeture_duree": 10, "preavis_fermeture": 5,
         "delai_prevenance": 1, "marge_lits": 0, "tolerance_depassement": 10000}
    aleas = tirer_aleas(d, HIST, p, 0)
    st = simuler(d, sol, aleas, "statique", p, rotation=0)["indicateurs"]
    sma = simuler(d, sol, aleas, "sma", p, rotation=0)["indicateurs"]
    assert st["lits_nuits_manquants"] > 0
    assert sma["lits_nuits_manquants"] <= st["lits_nuits_manquants"]
    assert sma["patients_operes"] == st["patients_operes"]


def test_le_bloc_retient_la_date_acceptee_qui_degrade_le_moins_F():
    """Parmi les dates que l'agent d'hébergement accepte, le Bloc retient celle qui fait le moins
    augmenter F à l'instant t (urgences attendues comprises) ; l'évaluateur partagé reste à jour."""
    d = instance(6)   # urgences attendues, poids et pénalités de capacité
    rng = random.Random(6)
    sol = [rng.choice(a) for a in d["admissibles"]]
    # vacations libérées utilisables comme les autres (en « dernier recours », elles passent après les autres)
    p = {**SANS_ALEA, "marge_lits": 0, "delai_prevenance": 1, "vacations_liberees": "utilisables"}
    m = ModeleHopital(d, sol, tirer_aleas(d, HIST, p, 0), "sma", p, rotation=0)
    t, essais = 0, 0
    for i in range(len(sol)):
        acceptees = [v for v in m.bloc.proposer(i, t + m.D, m.jour[sol[i]]) if m.hebergement(i).accepte(i, v, t)[0]]
        if len(acceptees) < 2:
            continue
        ev = Evaluateur(m.d_courant(t), m.ev.sol)
        meilleure = min(ev.essayer(i, v) for v in acceptees)
        assert m.negocier(i, t, m.lits, "essai")
        assert m.ev.sol[i] in acceptees
        assert ev.essayer(i, m.ev.sol[i]) == pytest.approx(meilleure)
        essais += 1
    assert essais >= 5


def test_patient_convoque_jamais_deplace():
    """Un patient opéré avant t + délai de prévenance ne change jamais de date (sauf s'il est reporté par le bloc)."""
    d, sol = planning(3)
    p = {**SANS_ALEA, "lits_fermes": 10, "fermeture_debut": 3, "fermeture_duree": 20, "preavis_fermeture": 0,
         "delai_prevenance": 30, "tolerance_depassement": 10000}
    aleas = tirer_aleas(d, HIST, p, 0)
    I = simuler(d, sol, aleas, "sma", p, rotation=0)["indicateurs"]
    assert I["patients_dates_changees"] == 0   # tout l'horizon est dans le délai de prévenance


def test_reparer_respecte_les_contraintes_et_ameliore():
    d, sol = planning(4)
    d = {**d, "penaliser_capacites": True}
    mobiles = {i: 10 for i in range(0, len(sol), 2)}
    nouv, val = reparer(d, sol, mobiles, budget=2000, cout_deplacement=5.0, graine=1)
    assert all(v in a for v, a in zip(nouv, d["admissibles"]))
    assert all(nouv[i] == sol[i] for i in range(len(sol)) if i not in mobiles)
    assert all(d["jour"][nouv[i]] >= 10 for i in mobiles if nouv[i] != sol[i])
    nb = sum(a != b for a, b in zip(nouv, sol))
    assert val == pytest.approx(evaluer(nouv, d) + 5.0 * nb, rel=1e-6)
    assert val <= evaluer(sol, d) + 1e-6


def test_robustesse_agrege_les_tirages():
    d, sol = planning(5)
    R = robustesse_plan(d, sol, HIST, {"lits_fermes": 4}, nb_tirages=3, rotation=0)
    assert set(R) == {"statique", "sma"}
    assert R["sma"]["nb_tirages"] == 3 and 0 <= R["statique"]["part_sans_surcapacite"] <= 1


def flux_synthetique(d, n=30, graine=0):
    """Nouveaux patients fictifs (même forme que src/flux.tirer_flux) : arrivée t, date souhaitée t + 4 à 9 jours."""
    rng = random.Random(graine)
    T, P = d["nb_jours"], []
    while len(P) < n:
        t = rng.randrange(T - 10)
        s = t + rng.randint(4, 9)
        adm = [v for v, j in enumerate(d["jour"]) if s - 3 <= j <= s + 5 and j >= t + 2]
        if not adm:
            continue
        ambu = int(rng.random() < 0.5)
        P.append({"id": f"N{len(P) + 1:04d}", "arrivee": t, "souhait": s, "tolerance": 0, "limite": t + 60,
                  "admissibles": adm, "duree": rng.uniform(20, 120), "sigma": rng.uniform(5, 20), "ambu": ambu,
                  "nuits": 0 if ambu else rng.randint(1, 4), "poids_retard": 1.0, "chir": 0})
    return {"patients": P, "resume": {}}


def test_flux_vide_ne_change_rien():
    from src.flux import injecter
    d = instance(7)
    sol = [random.Random(7).choice(a) for a in d["admissibles"]]
    p = {"lits_fermes": 4}
    aleas = tirer_aleas(d, HIST, p, 0)
    d2, sol2, ids2, aleas2 = injecter(d, sol, [str(i) for i in range(len(sol))], aleas, {"patients": []}, p)
    for s in ("statique", "sma", "centralise"):
        a = simuler(d, sol, aleas, s, p, rotation=0)["indicateurs"]
        b = simuler(d2, sol2, aleas2, s, p, rotation=0)["indicateurs"]
        assert {k: v for k, v in a.items() if k != "temps_s"} == {k: v for k, v in b.items() if k != "temps_s"}, s


@pytest.mark.parametrize("strategie", ["statique", "sma", "centralise"])
def test_nouveaux_patients_places_apres_leur_arrivee(strategie):
    """Un nouveau patient n'existe pour les agents qu'à partir de son arrivée, et reçoit une date
    au plus tôt arrivée + délai de prévenance ; les autres aléas ne changent pas."""
    from src.flux import injecter
    d = instance(8)
    sol = [random.Random(8).choice(a) for a in d["admissibles"]]
    N = len(sol)
    flux = flux_synthetique(d, 30, 8)
    p = {**SANS_ALEA, "delai_prevenance": 2, "tolerance_depassement": 10000}
    aleas = tirer_aleas(d, HIST, p, 0)
    d2, sol2, ids2, aleas2 = injecter(d, sol, [str(i) for i in range(N)], aleas, flux, p)
    assert aleas2["duree_reelle"][:N] == aleas["duree_reelle"] and aleas2["urgences"] == aleas["urgences"]
    m = ModeleHopital(d2, sol2, aleas2, strategie, p, rotation=0)
    arrivee = aleas2["arrivee"]
    while m.t < m.T:
        assert all(m.ev.sol[i] == -1 for i in range(N, len(sol2)) if arrivee[i] >= m.t)
        m.step()
    I = m.resultats(0.0)["indicateurs"]
    assert I["nouveaux_patients"] == 30
    places = [i for i in range(N, len(sol2)) if m.ev.sol[i] >= 0]
    assert len(places) + I["nouveaux_sans_creneau"] == 30
    assert all(m.jour[m.ev.sol[i]] >= arrivee[i] + m.D for i in places)
    assert I["nouveaux_operes"] == len([i for i in places if i in m.operes])


def test_robustesse_avec_un_flux_par_tirage():
    """Étapes 3 et 4 : chaque tirage d'aléas reçoit son propre flux de nouveaux patients."""
    d, sol = planning(9)
    flux = [flux_synthetique(d, 10, 1), flux_synthetique(d, 20, 2)]
    R = robustesse_plan(d, sol, HIST, {"lits_fermes": 4}, nb_tirages=2, rotation=0, flux=flux)
    assert R["sma"]["nouveaux_patients"] == 15 and R["statique"]["nouveaux_patients"] == 15
    sans = robustesse_plan(d, sol, HIST, {"lits_fermes": 4}, nb_tirages=2, rotation=0)
    assert sans["sma"]["nouveaux_patients"] == 0


def test_parametres_convertis():
    p = parametres({"retard_bloc": "false", "lits_fermes": "12", "inconnu": 1})
    assert p["retard_bloc"] is False and p["lits_fermes"] == 12 and "inconnu" not in p


# ----- vacations libérées (réglage vacations_liberees, 2026-10-13) -----

def planning_avec_liberees(graine=8, nb=6):
    """Planning où nb vacations ont été vidées (leurs patients déplacés vers une autre vacation admissible)."""
    d = instance(graine)
    rng = random.Random(graine)
    sol = [rng.choice(a) for a in d["admissibles"]]
    vides = []
    for v in rng.sample(range(len(d["capacite"])), 20):
        if len(vides) == nb:
            break
        patients = [i for i, w in enumerate(sol) if w == v]
        autres = {i: [w for w in d["admissibles"][i] if w != v and w not in vides] for i in patients}
        if all(autres.values()):
            for i in patients:
                sol[i] = autres[i][0]
            vides.append(v)
    return d, sol, set(vides)


@pytest.mark.parametrize("strategie", ["statique", "sma", "centralise"])
def test_vacations_liberees_fermees_jamais_rouvertes(strategie):
    from src.flux import injecter
    d, sol, vides = planning_avec_liberees()
    # sans refus des patients : ils changent le parcours de la simulation, et la comparaison des modes n'est plus garantie
    p = {"lits_fermes": 4, "taux_annulation": 0.1, "refus_avance": 0.0, "refus_report": 0.0}
    aleas = tirer_aleas(d, HIST, p, 0)
    d2, sol2, _, aleas2 = injecter(d, sol, [str(i) for i in range(len(sol))], aleas, flux_synthetique(d, 40, 3), p)
    res = {}
    for mode in ("utilisables", "dernier_recours", "fermees"):
        m = ModeleHopital(d2, sol2, aleas2, strategie, {**p, "vacations_liberees": mode}, rotation=0)
        assert m.liberees >= vides
        res[mode] = m.lancer()["indicateurs"]
        assert res[mode]["liberees_depart"] == len(m.liberees_depart)
        assert res[mode]["patients_liberees"] == sum(1 for v in m.ev.sol if v in m.liberees_depart)
        # une vacation rouverte n'est plus « libérée » pour la suite de la simulation
        assert all(m.ev.nb[v] == 0 for v in m.liberees)
    assert res["fermees"]["liberees_rouvertes"] == 0 and res["fermees"]["patients_liberees"] == 0
    assert res["dernier_recours"]["patients_liberees"] <= res["utilisables"]["patients_liberees"]


def test_vacations_liberees_reglage_inconnu():
    d, sol = planning()
    with pytest.raises(ValueError):
        ModeleHopital(d, sol, tirer_aleas(d, HIST, {}, 0), "sma", {"vacations_liberees": "autre"})
