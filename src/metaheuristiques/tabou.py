"""Recherche tabou (cours : on se déplace vers le meilleur voisin non tabou, même
s'il est moins bon ; une liste tabou interdit de revenir en arrière ; critère
d'aspiration : un mouvement tabou est permis s'il bat la meilleure solution)."""
import random

from ..modele import Evaluateur
from .commun import Suivi, Voisinage, appliquer, essayer, resultat, solution_aleatoire


def recherche_tabou(d, budget=30000, graine=0, solution_initiale=None, taille_voisinage=40,
                    duree_tabou=(10, 30), p_echange=0.3):
    """Paramètres :
      taille_voisinage : nombre de voisins tirés et évalués à chaque itération
                         (le voisinage complet, Σ |V_i|, est trop grand) ;
      duree_tabou : après avoir sorti le patient i de la vacation v, le retour
                    (i, v) est interdit pendant un nombre d'itérations tiré dans cet
                    intervalle (durée aléatoire pour éviter les cycles)."""
    rng = random.Random(graine)
    sol = list(solution_initiale) if solution_initiale is not None else solution_aleatoire(d, graine)
    ev = Evaluateur(d, sol)
    vois = Voisinage(d, rng, p_echange)
    suivi = Suivi(budget)
    F = ev.valeur()
    meilleur, meilleure_sol = F, list(ev.sol)
    suivi.noter(0, meilleur, force=True)
    tabou = {}   # (patient, vacation) -> dernière itération interdite
    it = 0
    while ev.nb_evaluations < budget:
        it += 1
        choix, F_choix = None, float("inf")
        for _ in range(min(taille_voisinage, budget - ev.nb_evaluations)):
            mvt = vois.tirer(ev.sol)
            Fm = essayer(ev, mvt)
            if mvt[0] == "deplacer":
                interdit = tabou.get((mvt[1], mvt[2]), 0) >= it
            else:
                i, k = mvt[1], mvt[2]
                interdit = tabou.get((i, ev.sol[k]), 0) >= it or tabou.get((k, ev.sol[i]), 0) >= it
            if interdit and Fm >= meilleur - 1e-9:   # aspiration
                continue
            if Fm < F_choix:
                choix, F_choix = mvt, Fm
        if choix is None:
            continue
        # le patient ne pourra pas revenir tout de suite dans sa vacation d'origine
        if choix[0] == "deplacer":
            tabou[(choix[1], ev.sol[choix[1]])] = it + rng.randint(*duree_tabou)
        else:
            tabou[(choix[1], ev.sol[choix[1]])] = it + rng.randint(*duree_tabou)
            tabou[(choix[2], ev.sol[choix[2]])] = it + rng.randint(*duree_tabou)
        appliquer(ev, choix)
        F = F_choix
        if F < meilleur - 1e-9:
            meilleur, meilleure_sol = F, list(ev.sol)
        if it % 500 == 0:
            ev.resynchroniser()
            F = ev.valeur()
            tabou = {k: v for k, v in tabou.items() if v >= it}
        suivi.noter(ev.nb_evaluations, meilleur)
    return resultat("Recherche tabou", meilleure_sol, meilleur, suivi, ev.nb_evaluations, graine,
                    parametres={"taille_voisinage": taille_voisinage, "duree_tabou": list(duree_tabou),
                                "p_echange": p_echange},
                    iterations=it)
