"""Méthodes hybrides : combinaisons des trois métaheuristiques du groupe (recuit simulé,
recherche tabou, algorithme génétique), sans modifier leurs fichiers ni evaluer.

Deux familles :

1. Hybrides en relais : les méthodes s'enchaînent ; chacune repart de la meilleure
   solution trouvée par la précédente et reçoit une part du budget d'évaluations.
   On explore d'abord (génétique, recuit chaud), on intensifie ensuite (tabou, recuit
   froid). Les fonctions appelées sont celles des fichiers recuit.py, tabou.py et
   genetique.py, avec les réglages de l'onglet Technique.
   Parts par défaut (essais sur la liste de 600 patients initiaux, budget 200 000,
   8 graines) : recuit 80 % puis tabou 20 % ; génétique 10 % puis recuit ou tabou 90 %.

2. Hybrides intégrés : une méthode travaille À L'INTÉRIEUR d'une autre.
   - Algorithme mémétique : algorithme génétique (mêmes opérateurs que genetique.py :
     tournoi, croisement par chirurgien, mutation, élitisme) dont chaque enfant est
     amélioré par une courte recherche tabou (recherche_tabou de tabou.py) avant
     d'entrer dans la population.
   - Recuit à liste tabou : recuit simulé (même acceptation exp(-Δ/T) et même
     refroidissement que recuit.py) qui s'interdit, comme la recherche tabou, de remettre
     tout de suite un patient dans la vacation qu'il vient de quitter.

Même interface que les trois méthodes : f(d, budget, graine, solution_initiale, ...)
-> dict (algo, solution, F, temps, evaluations, courbe, graine, parametres). Le budget
total d'évaluations est le même que pour une méthode seule : la comparaison reste
équitable. Le point de départ (aléatoire ou glouton) est choisi par la comparaison,
comme pour les autres méthodes : il ne fait pas partie de l'hybride.
"""
import math
import random

import numpy as np

from ..modele import Evaluateur, EvaluateurVect
from .commun import Suivi, Voisinage, appliquer, essayer, resultat, solution_aleatoire
from .genetique import algorithme_genetique
from .recuit import recuit_simule
from .tabou import recherche_tabou

METHODES_DE_BASE = {"recuit": recuit_simule, "tabou": recherche_tabou, "genetique": algorithme_genetique}
NOMS_COURTS = {"recuit": "Recuit", "tabou": "Tabou", "genetique": "Génétique"}


# ---------------------------------------------------------------------------
# 1. Hybrides en relais
# ---------------------------------------------------------------------------
def relais(d, etapes, budget=200000, graine=0, solution_initiale=None, reglages=None):
    """etapes : [(clé de la méthode, part du budget)], par exemple [("recuit", 0.8), ("tabou", 0.2)].
    La dernière étape reçoit le reste du budget (les parts sont normalisées).
    reglages : {clé de la méthode: paramètres}, ceux de l'onglet Technique."""
    reglages = reglages or {}
    suivi = Suivi(budget)
    etapes = [(cle, min(max(float(p), 0.0), 1.0)) for cle, p in etapes]   # parts entre 0 et 1
    total = sum(p for _, p in etapes) or 1.0
    sol = list(solution_initiale) if solution_initiale is not None else solution_aleatoire(d, graine)
    meilleur, meilleure_sol = float("inf"), sol
    courbe, utilise, detail = [], 0, []
    for k, (cle, part) in enumerate(etapes):
        b = budget - utilise if k == len(etapes) - 1 else int(round(budget * part / total))
        if b <= 0:
            continue
        r = METHODES_DE_BASE[cle](d, budget=b, graine=graine, solution_initiale=meilleure_sol,
                                  **reglages.get(cle, {}))
        for x, F in r["courbe"]:          # courbe mise bout à bout, en évaluations cumulées
            courbe.append([utilise + x, min(meilleur, F)])
        if r["F"] < meilleur:             # chaque étape part de la meilleure solution : F ne remonte pas
            meilleur, meilleure_sol = r["F"], r["solution"]
        utilise += r["evaluations"]
        detail.append({"methode": cle, "part": round(b / budget, 3), "evaluations": r["evaluations"],
                       "F": r["F"], "temps": r["temps"]})
    suivi.courbe = courbe
    nom = " → ".join(NOMS_COURTS[c] for c, _ in etapes)
    return resultat(nom, meilleure_sol, meilleur, suivi, utilise, graine,
                    parametres={"etapes": [f"{NOMS_COURTS[x['methode']]} {round(100 * x['part'])} %" for x in detail]},
                    etapes=detail)


def recuit_puis_tabou(d, budget=200000, graine=0, solution_initiale=None, part=0.8, reglages=None):
    """Recuit (part du budget) puis recherche tabou (le reste)."""
    return relais(d, [("recuit", part), ("tabou", 1 - part)], budget, graine, solution_initiale, reglages)


def genetique_puis_recuit(d, budget=200000, graine=0, solution_initiale=None, part=0.1, reglages=None):
    """Algorithme génétique (part du budget) puis recuit simulé (le reste)."""
    return relais(d, [("genetique", part), ("recuit", 1 - part)], budget, graine, solution_initiale, reglages)


def genetique_puis_tabou(d, budget=200000, graine=0, solution_initiale=None, part=0.1, reglages=None):
    """Algorithme génétique (part du budget) puis recherche tabou (le reste)."""
    return relais(d, [("genetique", part), ("tabou", 1 - part)], budget, graine, solution_initiale, reglages)


# ---------------------------------------------------------------------------
# 2. Hybrides intégrés
# ---------------------------------------------------------------------------
def memetique(d, budget=200000, graine=0, solution_initiale=None, taille_population=6, budget_local=1000,
              reglages=None):
    """Algorithme mémétique : génétique + recherche tabou courte sur chaque individu.
    Paramètres propres :
      taille_population : plus petite que pour l'AG seul, car chaque individu coûte
                          budget_local évaluations de plus (6 et 1 000 : meilleur réglage
                          essayé, de 4 à 40 individus et de 200 à 3 000 évaluations) ;
      budget_local : évaluations de la recherche tabou appliquée à chaque enfant ;
                     l'enfant amélioré remplace l'enfant (on garde les gènes améliorés).
    Les opérateurs (croisement, tournoi, mutation, élites) suivent les réglages de
    l'algorithme génétique, la recherche locale ceux de la recherche tabou."""
    reglages = reglages or {}
    reg_tabou = reglages.get("tabou", {})
    reg_ag = reglages.get("genetique", {})
    p_croisement = reg_ag.get("p_croisement", 0.9)
    taux_mutation = reg_ag.get("taux_mutation")
    taille_tournoi = reg_ag.get("taille_tournoi", 3)
    nb_elites = reg_ag.get("nb_elites", 2)
    croisement = reg_ag.get("croisement", "chirurgien")
    rng = random.Random(graine)
    nrng = np.random.default_rng(graine)
    ev = EvaluateurVect(d)            # évaluation complète d'un enfant avant la recherche locale
    N = len(d["duree"])
    adm = [np.asarray(a) for a in d["admissibles"]]
    taux = taux_mutation if taux_mutation is not None else 2.0 / N
    groupes = {}
    for i, c in enumerate(d.get("chir") or [0] * N):
        groupes.setdefault(c, []).append(i)
    groupes = [np.asarray(g) for g in groupes.values()]
    suivi = Suivi(budget)
    nb_eval = 0
    meilleur, meilleure_sol = float("inf"), None
    nb_locales = 0

    def ameliorer(x):
        """Évalue l'individu x puis l'améliore par une courte recherche tabou ; renvoie (x', F')."""
        nonlocal nb_eval, meilleur, meilleure_sol, nb_locales
        F = ev(x)
        nb_eval += 1
        b = min(budget_local, budget - nb_eval)
        if b > 0:
            r = recherche_tabou(d, budget=b, graine=rng.randrange(1 << 30), solution_initiale=x, **reg_tabou)
            nb_eval += r["evaluations"]
            nb_locales += 1
            if r["F"] < F:
                x, F = np.asarray(r["solution"]), r["F"]
        if F < meilleur - 1e-9:
            meilleur, meilleure_sol = F, x.copy()
        suivi.noter(nb_eval, meilleur)
        return x, F

    def aleatoire():
        return np.array([a[nrng.integers(len(a))] for a in adm])

    depart = [np.asarray(solution_initiale)] if solution_initiale is not None else []
    depart.append(np.asarray(solution_aleatoire(d, graine)))
    pop, fit = [], []
    while len(pop) < taille_population and nb_eval < budget:
        x, F = ameliorer(depart.pop(0) if depart else aleatoire())
        pop.append(x)
        fit.append(F)
    suivi.noter(nb_eval, meilleur, force=True)

    def tournoi():
        cand = rng.sample(range(len(pop)), min(taille_tournoi, len(pop)))
        return pop[min(cand, key=lambda k: fit[k])]

    generations = 0
    while nb_eval < budget:
        generations += 1
        ordre = np.argsort(fit)
        nouvelle = [pop[k].copy() for k in ordre[:nb_elites]]
        nouv_fit = [fit[k] for k in ordre[:nb_elites]]
        while len(nouvelle) < taille_population and nb_eval < budget:
            p1, p2 = tournoi(), tournoi()
            if rng.random() < p_croisement:
                if croisement == "chirurgien":
                    enfant = p1.copy()
                    for g in groupes:
                        if rng.random() < 0.5:
                            enfant[g] = p2[g]
                else:
                    enfant = np.where(nrng.random(N) < 0.5, p1, p2)
            else:
                enfant = p1.copy()
            mutes = np.flatnonzero(nrng.random(N) < taux)
            if len(mutes) == 0:
                mutes = [rng.randrange(N)]
            for i in mutes:
                enfant[i] = adm[i][nrng.integers(len(adm[i]))]
            x, F = ameliorer(enfant)
            nouvelle.append(x)
            nouv_fit.append(F)
        pop, fit = nouvelle, nouv_fit
    return resultat("Algorithme mémétique", meilleure_sol, meilleur, suivi, nb_eval, graine,
                    parametres={"taille_population": taille_population, "budget_local": budget_local,
                                "p_croisement": p_croisement, "taux_mutation": round(taux, 5),
                                "taille_tournoi": taille_tournoi, "nb_elites": nb_elites, "croisement": croisement},
                    generations=generations, recherches_locales=nb_locales)


def recuit_liste_tabou(d, budget=200000, graine=0, solution_initiale=None, reglages=None):
    """Recuit simulé à liste tabou. Même recuit que recuit.py, avec ses réglages
    (calibration de T0 sur 100 mouvements d'essai, acceptation exp(-Δ/T), refroidissement
    géométrique), plus une liste tabou réglée comme la recherche tabou :
      quand un patient i quitte la vacation v, le retour (i, v) est interdit pendant un
      nombre de mouvements acceptés tiré dans duree_tabou ; un mouvement interdit est
      remplacé par un autre tirage sans être évalué (il ne consomme pas de budget) ;
      après 20 tirages interdits de suite, le dernier est évalué quand même.
    Effet attendu : à basse température, le recuit ne défait plus aussitôt ce qu'il vient
    de faire (moins de va-et-vient sur les plateaux de F)."""
    reglages = reglages or {}
    rs = reglages.get("recuit", {})
    T0 = rs.get("T0")
    ratio_T_finale = rs.get("ratio_T_finale", 1e-4)
    p_echange = rs.get("p_echange", 0.3)
    acceptation_initiale = rs.get("acceptation_initiale", 0.5)
    duree_tabou = tuple(reglages.get("tabou", {}).get("duree_tabou", (10, 30)))
    rng = random.Random(graine)
    sol = list(solution_initiale) if solution_initiale is not None else solution_aleatoire(d, graine)
    ev = Evaluateur(d, sol)
    vois = Voisinage(d, rng, p_echange)
    suivi = Suivi(budget)
    F = ev.valeur()
    meilleur, meilleure_sol = F, list(ev.sol)
    suivi.noter(0, meilleur, force=True)

    if T0 is None:
        degradations = [max(0.0, essayer(ev, vois.tirer(ev.sol)) - F) for _ in range(100)]
        degradations = [x for x in degradations if x > 0] or [1.0]
        T0 = (sum(degradations) / len(degradations)) / -math.log(acceptation_initiale)
    T = T0
    restant = max(1, budget - ev.nb_evaluations)
    alpha = ratio_T_finale ** (1.0 / restant)
    tabou = {}          # (patient, vacation) -> dernier mouvement accepté où le retour est interdit
    acceptes = ecartes = 0

    def interdit(mvt):
        if mvt[0] == "deplacer":
            return tabou.get((mvt[1], mvt[2]), -1) >= acceptes
        i, k = mvt[1], mvt[2]
        return tabou.get((i, ev.sol[k]), -1) >= acceptes or tabou.get((k, ev.sol[i]), -1) >= acceptes

    while ev.nb_evaluations < budget:
        mvt = vois.tirer(ev.sol)
        for _ in range(20):             # on retire un autre mouvement tant qu'il est tabou
            if not interdit(mvt):
                break
            ecartes += 1
            mvt = vois.tirer(ev.sol)
        F_new = essayer(ev, mvt)
        delta = F_new - F
        if delta <= 0 or rng.random() < math.exp(-delta / T):
            for p in ((mvt[1],) if mvt[0] == "deplacer" else (mvt[1], mvt[2])):
                tabou[(p, ev.sol[p])] = acceptes + rng.randint(*duree_tabou)
            appliquer(ev, mvt)
            F = F_new
            acceptes += 1
            if F < meilleur - 1e-9:
                meilleur, meilleure_sol = F, list(ev.sol)
            if acceptes % 5000 == 0:   # évite la dérive des sommes flottantes
                ev.resynchroniser()
                F = ev.valeur()
                tabou = {k: v for k, v in tabou.items() if v >= acceptes}
        T *= alpha
        suivi.noter(ev.nb_evaluations, meilleur)
    return resultat("Recuit à liste tabou", meilleure_sol, meilleur, suivi, ev.nb_evaluations, graine,
                    parametres={"T0": round(T0, 3), "alpha": alpha, "p_echange": p_echange,
                                "duree_tabou": list(duree_tabou)},
                    taux_acceptation=round(acceptes / max(1, ev.nb_evaluations), 3), mouvements_ecartes=ecartes)


# Registre utilisé par la comparaison (src/comparaison.py) : clé -> fonction
HYBRIDES = {
    "relais_recuit_tabou": recuit_puis_tabou,
    "relais_genetique_recuit": genetique_puis_recuit,
    "relais_genetique_tabou": genetique_puis_tabou,
    "memetique": memetique,
    "recuit_liste_tabou": recuit_liste_tabou,
}
