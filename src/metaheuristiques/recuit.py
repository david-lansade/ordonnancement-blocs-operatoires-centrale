"""Recuit simulé (cours : acceptation d'une dégradation Δ avec la probabilité exp(-Δ/T),
T décroissante)."""
import math
import random

from ..modele import Evaluateur
from .commun import Suivi, Voisinage, appliquer, essayer, resultat, solution_aleatoire


def recuit_simule(d, budget=30000, graine=0, solution_initiale=None, T0=None,
                  ratio_T_finale=1e-4, p_echange=0.3, acceptation_initiale=0.5):
    """Paramètres :
      T0 : température initiale ; si None, elle est calibrée pour qu'une dégradation
           moyenne soit acceptée avec la probabilité acceptation_initiale (100
           mouvements d'essai, comptés dans le budget) ;
      refroidissement géométrique T <- α T, α choisi pour atteindre
      T0 * ratio_T_finale à la fin du budget."""
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
    acceptes = 0
    while ev.nb_evaluations < budget:
        mvt = vois.tirer(ev.sol)
        F_new = essayer(ev, mvt)
        delta = F_new - F
        if delta <= 0 or rng.random() < math.exp(-delta / T):
            appliquer(ev, mvt)
            F = F_new
            acceptes += 1
            if F < meilleur - 1e-9:
                meilleur, meilleure_sol = F, list(ev.sol)
            if acceptes % 5000 == 0:   # évite la dérive des sommes flottantes
                ev.resynchroniser()
                F = ev.valeur()
        T *= alpha
        suivi.noter(ev.nb_evaluations, meilleur)
    return resultat("Recuit simulé", meilleure_sol, meilleur, suivi, ev.nb_evaluations, graine,
                    parametres={"T0": round(T0, 3), "alpha": alpha, "p_echange": p_echange},
                    taux_acceptation=round(acceptes / max(1, ev.nb_evaluations), 3))
