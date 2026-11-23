"""Algorithme génétique (cours : population, sélection, croisement, mutation,
remplacement avec élitisme)."""
import random

import numpy as np

from ..modele import EvaluateurVect
from .commun import Suivi, resultat, solution_aleatoire


def algorithme_genetique(d, budget=30000, graine=0, solution_initiale=None, taille_population=50,
                         p_croisement=0.9, taux_mutation=None, taille_tournoi=3, nb_elites=2,
                         croisement="chirurgien"):
    """Paramètres :
      sélection par tournoi (taille_tournoi individus, le meilleur gagne) ;
      croisement « chirurgien » : pour chaque chirurgien, l'enfant reprend en bloc
        l'affectation de tous ses patients chez l'un des deux parents (on garde la
        cohérence du remplissage de ses vacations) ; « uniforme » : patient par patient ;
      mutation : chaque gène change avec la probabilité taux_mutation (défaut 2/N),
        au moins une mutation par enfant ;
      élitisme : les nb_elites meilleurs passent tels quels à la génération suivante.
    Chaque enfant évalué compte pour une évaluation."""
    rng = random.Random(graine)
    nrng = np.random.default_rng(graine)
    ev = EvaluateurVect(d)
    N = len(d["duree"])
    adm = [np.asarray(a) for a in d["admissibles"]]
    taux = taux_mutation if taux_mutation is not None else 2.0 / N
    groupes = {}
    for i, c in enumerate(d.get("chir") or [0] * N):
        groupes.setdefault(c, []).append(i)
    groupes = [np.asarray(g) for g in groupes.values()]
    suivi = Suivi(budget)

    def aleatoire():
        return np.array([a[nrng.integers(len(a))] for a in adm])

    pop = []
    if solution_initiale is not None:
        pop.append(np.asarray(solution_initiale))
    pop.append(np.asarray(solution_aleatoire(d, graine)))
    while len(pop) < taille_population:
        pop.append(aleatoire())
    fit = [ev(ind) for ind in pop]
    b = int(np.argmin(fit))
    meilleur, meilleure_sol = fit[b], pop[b].copy()
    suivi.noter(ev.nb_evaluations, meilleur, force=True)

    def tournoi():
        cand = rng.sample(range(len(pop)), taille_tournoi)
        return pop[min(cand, key=lambda k: fit[k])]

    generations = 0
    while ev.nb_evaluations < budget:
        generations += 1
        ordre = np.argsort(fit)
        nouvelle = [pop[k].copy() for k in ordre[:nb_elites]]
        nouv_fit = [fit[k] for k in ordre[:nb_elites]]
        while len(nouvelle) < taille_population and ev.nb_evaluations < budget:
            p1, p2 = tournoi(), tournoi()
            if rng.random() < p_croisement:
                if croisement == "chirurgien":
                    enfant = p1.copy()
                    for g in groupes:
                        if rng.random() < 0.5:
                            enfant[g] = p2[g]
                else:
                    masque = nrng.random(N) < 0.5
                    enfant = np.where(masque, p1, p2)
            else:
                enfant = p1.copy()
            mutes = np.flatnonzero(nrng.random(N) < taux)
            if len(mutes) == 0:
                mutes = [rng.randrange(N)]
            for i in mutes:
                enfant[i] = adm[i][nrng.integers(len(adm[i]))]
            f = ev(enfant)
            nouvelle.append(enfant)
            nouv_fit.append(f)
            if f < meilleur - 1e-9:
                meilleur, meilleure_sol = f, enfant.copy()
            suivi.noter(ev.nb_evaluations, meilleur)
        pop, fit = nouvelle, nouv_fit
    return resultat("Algorithme génétique", meilleure_sol, meilleur, suivi, ev.nb_evaluations, graine,
                    parametres={"taille_population": taille_population, "p_croisement": p_croisement,
                                "taux_mutation": round(taux, 5), "taille_tournoi": taille_tournoi,
                                "nb_elites": nb_elites, "croisement": croisement},
                    generations=generations)
