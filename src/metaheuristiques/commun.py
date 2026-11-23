"""Éléments communs aux trois métaheuristiques : solutions initiales, voisinage,
suivi de la convergence.

Toutes les méthodes travaillent sur le même vecteur solution (solution[i] = vacation
du patient i), ne tirent que dans admissibles[i], et s'arrêtent après le même
budget d'évaluations de F (une évaluation = la valeur de F d'une solution candidate,
complète ou incrémentale).
"""
import random
import time

from ..modele import Evaluateur


class Suivi:
    """Courbe de convergence : meilleure valeur de F en fonction du nombre d'évaluations."""

    def __init__(self, budget, nb_points=200):
        self.pas = max(1, budget // nb_points)
        self.prochain = 0
        self.courbe = []
        self.debut = time.perf_counter()

    def noter(self, nb_eval, meilleur, force=False):
        if force or nb_eval >= self.prochain:
            self.courbe.append([int(nb_eval), float(meilleur)])
            self.prochain = nb_eval + self.pas

    def temps(self):
        return time.perf_counter() - self.debut


def resultat(nom, solution, F, suivi, nb_eval, graine, **extra):
    suivi.noter(nb_eval, F, force=True)
    return {"algo": nom, "solution": [int(v) for v in solution], "F": float(F),
            "temps": round(suivi.temps(), 3), "evaluations": int(nb_eval),
            "courbe": suivi.courbe, "graine": graine, **extra}


def solution_aleatoire(d, graine=0):
    rng = random.Random(graine)
    return [rng.choice(a) for a in d["admissibles"]]


def solution_gloutonne(d, graine=0):
    """Insertion gloutonne : les patients arrivent par date limite croissante (les
    plus longs d'abord à date égale) et chacun va dans la vacation admissible qui
    augmente le moins F. C'est aussi la règle d'affectation en ligne (un patient à
    la fois), sans métaheuristique."""
    rng = random.Random(graine)
    N = len(d["duree"])
    ev = Evaluateur(d, [-1] * N)
    ordre = sorted(range(N), key=lambda i: (d["limite"][i], -d["duree"][i], rng.random()))
    for i in ordre:
        meilleur, v_best = float("inf"), None
        for v in d["admissibles"][i]:
            F = ev.essayer(i, v)
            if F < meilleur - 1e-9 or (abs(F - meilleur) <= 1e-9 and rng.random() < 0.5):
                meilleur, v_best = F, v
        ev.deplacer(i, v_best)
    return list(ev.sol), ev.nb_evaluations


class Voisinage:
    """Deux mouvements :
      - déplacement : un patient change de vacation (dans sa liste admissible) ;
      - échange : deux patients du même chirurgien échangent leurs vacations
        (utile quand les vacations sont pleines)."""

    def __init__(self, d, rng, p_echange=0.3):
        self.adm = d["admissibles"]
        self.adm_set = [set(a) for a in self.adm]
        self.rng = rng
        self.p_echange = p_echange
        self.N = len(self.adm)
        groupes = {}
        for i, c in enumerate(d.get("chir") or [0] * self.N):
            groupes.setdefault(c, []).append(i)
        self.meme_chir = [groupes[c] for c in (d.get("chir") or [0] * self.N)]
        self.mobiles = [i for i in range(self.N) if len(self.adm[i]) > 1]

    def tirer(self, sol):
        """Renvoie ("deplacer", i, v) ou ("echanger", i, k)."""
        rng = self.rng
        i = rng.choice(self.mobiles) if self.mobiles else rng.randrange(self.N)
        if rng.random() < self.p_echange:
            groupe = self.meme_chir[i]
            for _ in range(5):
                k = rng.choice(groupe)
                if (k != i and sol[k] != sol[i] and sol[k] in self.adm_set[i]
                        and sol[i] in self.adm_set[k]):
                    return ("echanger", i, k)
        a = self.adm[i]
        if len(a) == 1:
            return ("deplacer", i, a[0])
        v = rng.choice(a)
        while v == sol[i]:
            v = rng.choice(a)
        return ("deplacer", i, v)


def essayer(ev, mvt):
    return ev.essayer(mvt[1], mvt[2]) if mvt[0] == "deplacer" else ev.essayer_echange(mvt[1], mvt[2])


def appliquer(ev, mvt):
    if mvt[0] == "deplacer":
        ev.deplacer(mvt[1], mvt[2])
    else:
        ev.echanger(mvt[1], mvt[2])
