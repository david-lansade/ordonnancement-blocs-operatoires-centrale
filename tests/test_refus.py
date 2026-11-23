"""Tests du refus d'une nouvelle date par le patient (src/sma.py, patient_accepte ; réglages
refus_avance et refus_report de src/aleas.py).

Lancer : .venv\\Scripts\\python -m pytest tests
"""
import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_aleas_sma import HIST, SANS_ALEA, planning  # noqa: E402
from test_modele import instance  # noqa: E402

from src.aleas import PARAMS_ALEAS, parametres, tirer_aleas  # noqa: E402
from src.sma import ModeleHopital, _uniforme, simuler  # noqa: E402

# lits fermés annoncés à l'avance : les agents doivent déplacer des patients
FERMETURE = {**SANS_ALEA, "lits_fermes": 8, "fermeture_debut": 10, "fermeture_duree": 10, "preavis_fermeture": 5,
             "delai_prevenance": 1, "marge_lits": 0, "taux_annulation": 0.1}


def test_parametres_par_defaut():
    p = parametres({})
    assert 0 < p["refus_report"] < p["refus_avance"] < 1
    assert parametres({"refus_avance": "0.5"})["refus_avance"] == 0.5
    assert PARAMS_ALEAS["refus_avance"] == p["refus_avance"]


def test_tirage_reproductible_et_uniforme():
    assert _uniforme(3, 10, 20) == _uniforme(3, 10, 20)
    assert _uniforme(3, 10, 20) != _uniforme(4, 10, 20) != _uniforme(3, 11, 20)
    u = np.array([_uniforme(g, i, j) for g in range(3) for i in range(200) for j in range(40)])
    assert 0 <= u.min() and u.max() < 1
    assert u.mean() == pytest.approx(0.5, abs=0.01)
    assert np.mean(u < 0.3) == pytest.approx(0.3, abs=0.01)


def test_memes_refus_pour_toutes_les_strategies():
    """Même tirage d'aléas : le patient donne la même réponse pour le même jour, quelle que soit la stratégie."""
    d, sol = planning(2)
    aleas = tirer_aleas(d, HIST, {}, 5)
    m1, m2 = (ModeleHopital(d, sol, aleas, s, {}, rotation=0) for s in ("statique", "sma"))
    rng = random.Random(0)
    for _ in range(300):
        i = rng.randrange(len(sol))
        v = rng.choice(d["admissibles"][i])
        assert m1.patient_accepte(i, v, 0) == m2.patient_accepte(i, v, 0)
    assert m1.refus_connus == m2.refus_connus and m1.refus_connus


def test_meme_jour_et_premiere_date_toujours_acceptes():
    d, sol = planning(2)
    p = {"refus_avance": 1.0, "refus_report": 1.0}
    m = ModeleHopital(d, sol, tirer_aleas(d, HIST, p, 0), "sma", p, rotation=0)
    for i in range(len(sol)):
        meme_jour = [v for v in d["admissibles"][i] if m.jour[v] == m.jour[sol[i]]]
        assert all(m.patient_accepte(i, v, 0) for v in meme_jour)
        autres = [v for v in d["admissibles"][i] if m.jour[v] != m.jour[sol[i]]]
        assert not any(m.patient_accepte(i, v, 0) for v in autres)
    assert m.refus_avances > 0 and m.refus_reports > 0


def test_une_date_refusee_n_est_plus_proposee():
    d, sol = planning(2)
    p = {"refus_avance": 1.0, "refus_report": 1.0}
    m = ModeleHopital(d, sol, tirer_aleas(d, HIST, p, 0), "sma", p, rotation=0)
    for i in range(len(sol)):
        props = m.bloc.proposer(i, 0, m.jour[sol[i]])
        autres = [v for v in props if m.jour[v] != m.jour[sol[i]]]
        if autres:
            assert not m.patient_accepte(i, autres[0], 0)
            j = m.jour[autres[0]]
            assert (i, j) in m.refus_connus
            assert all(m.jour[v] != j for v in m.bloc.proposer(i, 0, m.jour[sol[i]]))
            return
    pytest.fail("aucune date à proposer")


def test_le_patient_qui_refuse_garde_sa_date():
    """Déplacement demandé pour libérer des lits : si le patient refuse toutes les dates, il garde la sienne."""
    d = instance(6)
    sol = [random.Random(6).choice(a) for a in d["admissibles"]]
    p = {**SANS_ALEA, "marge_lits": 0, "delai_prevenance": 1, "refus_avance": 1.0, "refus_report": 1.0}
    m = ModeleHopital(d, sol, tirer_aleas(d, HIST, p, 0), "sma", p, rotation=0)
    gardes = 0
    for i in range(len(sol)):
        if any(m.hebergement(i).accepte(i, v, 0)[0] and m.jour[v] != m.jour[sol[i]]
               for v in m.bloc.proposer(i, m.D, m.jour[sol[i]])):
            deplace = m.negocier(i, 0, m.lits, "essai")
            assert m.jour[m.ev.sol[i]] == m.jour[sol[i]]   # au plus une autre salle le même jour
            if not deplace:
                assert m.ev.sol[i] == sol[i] and "garde sa date" in m.journal[-1]["texte"]
                gardes += 1
    assert gardes >= 3


@pytest.mark.parametrize("strategie", ["statique", "sma", "centralise"])
def test_tous_refusent_aucune_date_ne_change(strategie):
    """Si les patients refusent toute nouvelle date, aucun ne change de date (un patient reporté reste sans solution)."""
    d, sol = planning(2)
    p = {**FERMETURE, "refus_avance": 1.0, "refus_report": 1.0, "tolerance_depassement": 0}
    I = simuler(d, sol, tirer_aleas(d, HIST, p, 0), strategie, p, rotation=0)["indicateurs"]
    assert I["patients_dates_changees"] == 0
    assert I["patients_reportes"] > 0 and I["refus_patients"] > 0


def test_sans_refus_aucun_refus_compte():
    d, sol = planning(2)
    p = {**FERMETURE, "refus_avance": 0.0, "refus_report": 0.0}
    for s in ("statique", "sma", "centralise"):
        I = simuler(d, sol, tirer_aleas(d, HIST, p, 0), s, p, rotation=0)["indicateurs"]
        assert I["refus_patients"] == I["patients_refus"] == 0


def test_refus_comptes_et_moins_de_dates_changees():
    """Avec des refus, les refus sont comptés (plus tôt / plus tard) et les agents changent moins de dates
    EN MOYENNE (sur un seul tirage, la suite de la simulation peut s'inverser : comparaison sur 8 tirages)."""
    d, sol = planning(2)
    sans = {**FERMETURE, "refus_avance": 0.0, "refus_report": 0.0}
    avec = {**FERMETURE, "refus_avance": 0.5, "refus_report": 0.5}
    b = simuler(d, sol, tirer_aleas(d, HIST, avec, 0), "sma", avec, rotation=0)["indicateurs"]
    assert b["refus_patients"] == b["refus_avances"] + b["refus_reports"] > 0
    assert 0 < b["patients_refus"] <= b["refus_patients"]
    dates = {nom: sum(simuler(d, sol, tirer_aleas(d, HIST, p, g), "sma", p, rotation=0)["indicateurs"]["patients_dates_changees"]
                      for g in range(8)) for nom, p in (("sans", sans), ("avec", avec))}
    assert dates["avec"] < dates["sans"]
