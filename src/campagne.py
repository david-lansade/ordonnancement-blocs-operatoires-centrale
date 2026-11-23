"""Campagne de scénarios (partie 6 de la soutenance) : charge normale ou surcharge,
petite ou grande échelle, perturbations fortes. Chaque scénario a ses aléas : ordinaires
(sans fermeture de lits), sauf « Perturbations fortes » ; la réaction (délai de prévenance,
marge de lits, tolérance du bloc) et la graine viennent des réglages de l'utilisateur.

Pour chaque scénario : on invente une liste de patients (même générateur que l'onglet
« Patients initiaux », graine fixée), on construit l'instance (même d que la comparaison),
on lance les méthodes choisies, puis on teste chaque planning obtenu face aux aléas
(src/aleas.py), sans coordination et avec les agents (src/sma.py). La liste des
patients initiaux de l'application n'est pas modifiée.
"""
import numpy as np

from . import scenario as S
from .aleas import ALEAS_CRISE, ALEAS_ORDINAIRES, parametres
from .comparaison import NOMS, lancer, respect_capacites
from .flux import tirer_flux_tirages
from .metaheuristiques import solution_aleatoire
from .modele import details
from .parallele import executer
from .sma import robustesse_plan

SCENARIOS = {
    "normale": {"nom": "Charge normale", "x": 600, "semaines": 8, "aleas": {},
                "description": "600 patients à placer sur 8 semaines"},
    "surcharge": {"nom": "Surcharge (+30 %)", "x": 780, "semaines": 8, "aleas": {},
                  "description": "30 % de patients en plus sur les mêmes 8 semaines"},
    "perturbations": {"nom": "Perturbations fortes", "x": 600, "semaines": 8,
                      "aleas": ALEAS_CRISE,
                      "description": "charge normale, mais 2 fois plus d'urgences, 10 % d'annulations, "
                                     "12 lits fermés 3 semaines, 25 % de séjours prolongés"},
    "petite": {"nom": "Petite échelle", "x": 150, "semaines": 2, "aleas": {"fermeture_debut": 7, "fermeture_duree": 7},
               "description": "150 patients sur 2 semaines"},
    "grande": {"nom": "Grande échelle", "x": 1200, "semaines": 16, "aleas": {},
               "description": "1 200 patients sur 16 semaines"},
}


def _tache(tache):
    """Un scénario × une méthode : optimisation puis robustesse (processus fils)."""
    cle, a, d, budget, graine, params_algos, hist, params_aleas, nb_tirages, rotation, flux = tache
    r = lancer(a, d, budget, graine, solution_aleatoire(d, graine), params_algos)
    det = details(r["solution"], d)
    I = det["indicateurs"]
    rob = robustesse_plan(d, r["solution"], hist, params_aleas, nb_tirages, rotation, flux=flux)
    return {"scenario": cle, "algo": a, "nom": NOMS[a], "F": r["F"], "temps": r["temps"],
            "remplissage": I["taux_remplissage_moyen"], "lits_max": I["lits_max"],
            "ecart_type_lits": I["ecart_type_lits"], "vacations_hors_marge": I["vacations_depassees"],
            # régularité du planning prévu (patients programmés seulement), mêmes définitions que l'onglet Planning
            "ecart_type_lits_semaine": I["ecart_type_lits_semaine"], "ecart_type_lits_week_end": I["ecart_type_lits_week_end"],
            "ecart_type_places_semaine": I["ecart_type_places_semaine"],
            **respect_capacites(det["lits"], det["places"], d), "robustesse": rob}


def lancer_campagne(ctx, cles, algos, budget=100000, nb_tirages=10, params_aleas=None, params_algos=None,
                    graine=0, rappel=None, processus=None, flux_par_jour=0.0):
    """Lance les scénarios `cles` avec les méthodes `algos` ; renvoie un tableau par scénario.
    flux_par_jour > 0 : de nouveaux patients arrivent pendant chaque simulation (src/flux.py),
    tirés une fois par scénario et par tirage, les mêmes pour toutes les méthodes."""
    params = dict(ctx.params)
    grille = ctx.grille(params["grille"])
    codes = {v["chir"] for v in grille}
    hist = ctx.historique_urgences()
    instances, taches = {}, []
    for k, cle in enumerate(cles):
        sc = SCENARIOS[cle]
        if rappel:
            rappel(0.0, f"Patients et instance du scénario « {sc['nom']} » ({k + 1}/{len(cles)})")
        patients = ctx.generateur.generer(sc["x"], sc["semaines"], params["date_ouverture"], graine, autorises=codes)
        p_sc = {**params, "instance_semaines": sc["semaines"]}
        d, P, vac = S.creer_instance_liste(patients, ctx.pred_complet, grille, p_sc, ctx.reserve, ctx.rotation)
        aleas = parametres({**(params_aleas or {}), **ALEAS_ORDINAIRES, **sc["aleas"]})
        flux = None
        if flux_par_jour and flux_par_jour > 0:
            if rappel:
                rappel(0.0, f"Nouveaux patients du scénario « {sc['nom']} » ({k + 1}/{len(cles)})")
            flux = tirer_flux_tirages(ctx.generateur, ctx.pred_complet, p_sc, d, flux_par_jour, aleas["graine"], nb_tirages)
        instances[cle] = {
            "cle": cle, "nom": sc["nom"], "description": sc["description"], "N": len(d["duree"]),
            "V": len(d["capacite"]), "T": d["nb_jours"],
            "charge_salles": round(float(np.sum(d["duree"]) / np.sum(d["capacite"])), 3),
            "aleas": {c: aleas[c] for c in ("intensite_urgences", "taux_annulation", "lits_fermes",
                                            "fermeture_duree", "part_prolongations")},
            # nouveaux patients ajoutés en moyenne par simulation (date souhaitée dans l'horizon)
            "flux": {"par_jour": float(flux_par_jour),
                     "ajoutes": round(float(np.mean([f["resume"]["ajoutes"] for f in flux])), 1)} if flux else None,
        }
        taches += [(cle, a, d, budget, graine, params_algos or {}, hist, aleas, nb_tirages, ctx.rotation, flux)
                   for a in algos]
    res = executer(_tache, taches, processus, rappel,
                   libelle=lambda t: f"{SCENARIOS[t[0]]['nom']}, {NOMS[t[1]]}")
    for cle in cles:
        lignes = sorted([r for r in res if r["scenario"] == cle], key=lambda r: r["F"])
        instances[cle]["methodes"] = lignes
        instances[cle]["meilleure_F"] = lignes[0]["algo"] if lignes else None
        instances[cle]["plus_robuste"] = min(
            lignes, key=lambda r: (r["robustesse"]["statique"]["lits_nuits_manquants"], r["F"]))["algo"] if lignes else None
    return {"scenarios": [instances[c] for c in cles], "budget": budget, "nb_tirages": nb_tirages, "graine": graine}
