"""Comparaison des trois métaheuristiques et de leurs hybrides sur la même instance :
même d, mêmes graines, même solution de départ, même budget d'évaluations (section 9).

Les runs (une méthode, une graine) sont indépendants : ils tournent en parallèle dans des
processus séparés. Chaque run garde sa graine, donc les résultats sont les mêmes qu'en
séquentiel (seul le temps total change)."""
import numpy as np
import pandas as pd

from .metaheuristiques import ALGORITHMES, solution_aleatoire, solution_gloutonne
from .metaheuristiques.hybrides import HYBRIDES
from .modele import details, evaluer
from .parallele import executer, nb_processus

BUDGET_DEFAUT = 200000   # évaluations de F par méthode et par graine

NOMS = {"recuit": "Recuit simulé", "tabou": "Recherche tabou", "genetique": "Algorithme génétique",
        "relais_recuit_tabou": "Recuit → Tabou", "relais_genetique_recuit": "Génétique → Recuit",
        "relais_genetique_tabou": "Génétique → Tabou", "memetique": "Algorithme mémétique",
        "recuit_liste_tabou": "Recuit à liste tabou",
        "glouton": "Glouton (insertion)", "aleatoire": "Aléatoire"}
# seule : une des trois méthodes du groupe ; relais / integre : hybrides (metaheuristiques/hybrides.py) ;
# reference : sans métaheuristique
FAMILLES = {"recuit": "seule", "tabou": "seule", "genetique": "seule",
            "relais_recuit_tabou": "relais", "relais_genetique_recuit": "relais", "relais_genetique_tabou": "relais",
            "memetique": "integre", "recuit_liste_tabou": "integre", "glouton": "reference", "aleatoire": "reference"}


def lancer(a, d, budget, graine, init, params_algos):
    """Une méthode seule reçoit ses réglages ; un hybride reçoit ses réglages propres (parts du
    budget, population...) et ceux des méthodes qu'il combine."""
    if a in ALGORITHMES:
        return ALGORITHMES[a](d, budget=budget, graine=graine, solution_initiale=init, **params_algos.get(a, {}))
    reglages = {k: params_algos.get(k, {}) for k in ALGORITHMES}
    return HYBRIDES[a](d, budget=budget, graine=graine, solution_initiale=init, reglages=reglages,
                       **params_algos.get(a, {}))


def _executer(tache):
    """Un run (méthode a, graine s) ; exécuté dans un processus fils."""
    a, s, d, budget, init, params_algos = tache
    return a, s, lancer(a, d, budget, s, init, params_algos)


def _executer_tout(taches, processus, rappel):
    """Exécute les runs, en parallèle si possible ; renvoie {(a, s): résultat}."""
    res = executer(_executer, taches, processus, rappel, libelle=lambda t: f"{NOMS[t[0]]}, graine {t[1]}")
    return {(a, s): r for a, s, r in res}


def respect_capacites(lits, places, d):
    """Contraintes d'hébergement de la section 6 : n_t <= CL(t) et a_t <= CA(t)."""
    lits, places = np.asarray(lits, float), np.asarray(places, float)
    cl, ca = np.asarray(d["cap_lits"], float), np.asarray(d["cap_ambu"], float)
    nuits = int(np.sum(lits > cl + 1e-9))
    jours = int(np.sum(places > ca + 1e-9))
    return {"nuits_surcapacite": nuits, "jours_ambu_surcapacite": jours, "capacites_respectees": nuits + jours == 0}


def comparer(d, P, vac, algos=("recuit", "tabou", "genetique"), budget=BUDGET_DEFAUT, graines=(0,),
             depart="aleatoire", params_algos=None, rotation=12.0, rappel=None, processus=None):
    """Lance chaque méthode (seule ou hybride) pour chaque graine. rappel(fraction, message) suit l'avancement.
    processus : nombre de runs simultanés (None : selon le nombre de cœurs, au plus PROCESSUS_MAX ; 1 : séquentiel)."""
    params_algos = params_algos or {}
    inits = {s: solution_aleatoire(d, s) if depart == "aleatoire" else solution_gloutonne(d, s)[0] for s in graines}
    taches = [(a, s, d, budget, inits[s], params_algos) for s in graines for a in algos]
    processus = nb_processus(len(taches), processus)
    if rappel:
        rappel(0.0, f"{len(taches)} runs lancés ({processus} à la fois)")
    resultats = _executer_tout(taches, processus, rappel)
    runs, meilleurs = [], {}
    for s in graines:            # ordre fixe (graine, méthode) : même synthèse qu'en séquentiel
        for a in algos:
            r = resultats[(a, s)]
            runs.append({"algo": a, "graine": s, "F": r["F"], "temps": r["temps"], "evaluations": r["evaluations"]})
            if a not in meilleurs or r["F"] < meilleurs[a]["F"]:
                meilleurs[a] = r
    if rappel:
        rappel(1.0, "Synthèse")
    # références : départ aléatoire et insertion gloutonne (sans métaheuristique)
    refs = {"aleatoire": solution_aleatoire(d, graines[0]), "glouton": solution_gloutonne(d, graines[0])[0]}
    df_runs = pd.DataFrame(runs)
    synthese, occupation, termes, courbes = [], {}, {}, {}
    for a in list(algos) + list(refs):
        sol = meilleurs[a]["solution"] if a in meilleurs else refs[a]
        det = details(sol, d)
        ligne = {"algo": a, "nom": NOMS[a], "famille": FAMILLES[a], **det["indicateurs"],
                 **respect_capacites(det["lits"], det["places"], d)}
        if a in meilleurs:
            x = df_runs[df_runs["algo"] == a]
            ligne.update({"F_moyen": float(x["F"].mean()), "F_ecart_type": float(x["F"].std(ddof=0)),
                          "F_min": float(x["F"].min()), "F_max": float(x["F"].max()),
                          "temps_moyen": float(x["temps"].mean()), "evaluations": int(x["evaluations"].mean()),
                          "nb_graines": int(len(x))})
            courbes[a] = meilleurs[a]["courbe"]
            ligne["parametres"] = meilleurs[a].get("parametres", {})
        else:
            F = evaluer(sol, d)
            ligne.update({"F_moyen": F, "F_ecart_type": 0.0, "F_min": F, "F_max": F, "temps_moyen": None,
                          "evaluations": None, "nb_graines": 1})
        synthese.append(ligne)
        occupation[a] = {"lits": det["lits"], "places": det["places"], "charge": det["charge"],
                         "cible_lits": det["cible_lits"], "cible_places": det["cible_places"]}
        termes[a] = det["termes"]
    det0 = details(refs["aleatoire"], d)
    meilleur_algo = min(meilleurs, key=lambda a: meilleurs[a]["F"])
    dates = pd.date_range(vac["date"].min() - pd.Timedelta(days=int(vac["jour"].min())), periods=d["nb_jours"])
    return {
        "instance": {
            "N": len(d["duree"]), "V": len(d["capacite"]), "T": d["nb_jours"], "exclus": d.get("exclus", 0),
            "vacations_retirees": d.get("vacations_retirees", 0),
            "minutes_demandees": round(float(np.sum(d["duree"]))), "minutes_offertes": int(np.sum(d["capacite"])),
            "part_ambu": round(float(np.mean(d["ambu"])), 3),
            "admissibles_moyen": round(float(np.mean([len(a) for a in d["admissibles"]])), 1),
            "budget": budget, "graines": list(graines), "depart": depart,
        },
        "synthese": synthese, "runs": runs, "courbes": courbes, "termes": termes,
        "occupation": occupation,
        "cible_lits_forme": det0["cible_lits"], "cap_lits": d["cap_lits"], "cap_ambu": d["cap_ambu"],
        "pers": d["pers"], "dates": [x.strftime("%Y-%m-%d") for x in dates],
        "meilleur": {"algo": meilleur_algo, "F": meilleurs[meilleur_algo]["F"],
                     "solution": meilleurs[meilleur_algo]["solution"]},
        "solutions": {a: meilleurs[a]["solution"] for a in meilleurs},
    }
