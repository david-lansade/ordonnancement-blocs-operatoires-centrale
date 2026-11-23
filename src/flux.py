"""Flux de nouveaux patients pendant la simulation multi-agents (onglet Simulation : option de
la situation, étape 1, appliquée aux étapes 2, 3 et 4).

Débit : patients programmés de toute la base (2019-2022) divisés par le nombre de jours ouvrés
(lundi au vendredi) de ces 4 ans : debit_historique() ; les urgences sont exclues, elles sont
déjà un aléa (src/aleas.py). Le réglage par défaut de la situation reprend cette moyenne.

Pendant la simulation, chaque jour ouvré t, n_t ~ Poisson(débit) demandes arrivent. Chaque
patient est inventé comme les patients initiaux (src/generation.py : chirurgien selon
l'activité 2022, type d'intervention, diagnostic, âge, anesthésie), puis µ, σ, A et L sont
prédits par le même modèle.

Délais (la base ne contient pas la date de la demande : HYPOTHÈSE, la même qu'à l'ouverture) :
    date souhaitée s_i   une semaine au hasard parmi les instance_semaines semaines qui suivent
                         la demande (à partir du lundi suivant), le jour selon les habitudes
                         du chirurgien (P(jour | chirurgien), comme Generateur.generer) ;
    limite d_i           jour de la demande + délai médical maximal ;
    fenêtre              [max(s_i − avance_max, demande + délai de préparation),
                          min(T − 1, s_i + τ_i + retard_max)] ; admissibles[i] suit la règle
                         de construire_d (vacations du chirurgien dans la fenêtre, assez longues).
Seuls les patients dont la date souhaitée tombe dans l'horizon simulé sont ajoutés ; les
autres relèvent du planning suivant (comptés, pas simulés).

Les tirages ont leurs propres graines : les autres aléas (urgences, annulations...) sont
exactement les mêmes avec ou sans flux. Les nouveaux patients subissent les mêmes aléas que
les autres (durée réelle N(µ, σ), annulation, séjour prolongé), tirés avec les mêmes réglages.
"""
import numpy as np
import pandas as pd

from .aleas import parametres
from .generation import completer_predictions

# clés de d, une valeur par patient, complétées pour les nouveaux patients
CHAMPS = ("duree", "sigma", "ambu", "nuits", "limite", "admissibles", "poids_retard", "chir", "souhait", "tolerance")


# Rythme proposé par défaut : une fraction ronde du rythme réel (choix de l'utilisateur, 2026-10-12).
# Les patients initiaux (600 sur 8 semaines) sont déjà toutes les demandes de l'horizon, environ 117 %
# de l'activité normale : au rythme réel, le flux saturerait le planning. Le flux réduit représente
# des demandes supplémentaires ; une simulation « réaliste » garderait le rythme réel avec une liste
# initiale d'environ 290 patients (texte de l'onglet Simulation).
FRACTION_DEFAUT = 0.25


def debit_historique(df, debut="2019-01-01", fin="2022-12-31"):
    """Patients programmés (urgences exclues) / jours ouvrés (lundi au vendredi, fériés compris), de debut à fin ;
    par_jour_defaut = FRACTION_DEFAUT × ce rythme (valeur proposée dans l'onglet Simulation)."""
    x = df[(df["urgence"] == 0) & (df["date_inter"] >= debut) & (df["date_inter"] <= fin)]
    jours = len(pd.bdate_range(debut, fin))
    return {"par_jour": round(len(x) / jours, 1), "debut": debut[:4], "fin": fin[:4],
            "patients": int(len(x)), "jours_ouvres": jours,
            "fraction": FRACTION_DEFAUT, "par_jour_defaut": round(FRACTION_DEFAUT * len(x) / jours, 1)}


def tirer_flux(generateur, predicteur, params, d, debit, graine=0):
    """Nouvelles demandes sur l'horizon de d (jour 0 = lundi), indépendantes du planning.
    Renvoie {"patients": [...], "resume": {...}} ; chaque patient a les champs de CHAMPS,
    son jour d'arrivée (arrivee), sa fenêtre et ses admissibles (indices des vacations de d)."""
    rng = np.random.default_rng([int(graine), 1])
    T = int(d["nb_jours"])
    semaines = int(params["instance_semaines"])
    vac_chir = np.asarray(d["vac_chir"])
    jour_v = np.asarray(d["jour"], dtype=np.int64)
    cap_v = np.asarray(d["capacite"], dtype=float)
    z = float(d.get("z_beta", 1.0))
    p_chir = generateur.chirurgiens(set(d["vac_chir"]))
    patients, arrivees, apres_horizon, sans_vacation = [], 0, 0, 0
    for t in range(T):
        if t % 7 >= 5:
            continue
        n = int(rng.poisson(max(0.0, float(debit))))
        arrivees += n
        for chir in rng.choice(p_chir.index.to_numpy(), size=n, p=p_chir.values):
            pj = generateur.p_jour.get(chir)
            j = int(rng.choice(5, p=(pj / pj.sum()).values)) if pj is not None and pj.sum() > 0 else int(rng.integers(5))
            s = t - t % 7 + 7 * (1 + int(rng.integers(semaines))) + j
            if s >= T:
                apres_horizon += 1
                continue
            pat = generateur.patient(chir, rng)
            pat.update(arrivee=t, souhait=s)
            patients.append(pat)
    completer_predictions(patients, predicteur, params["methode_prediction"], params)
    tol = int(params["tolerance"])
    garde = []
    for pat in patients:
        t, s = pat["arrivee"], pat["souhait"]
        pat["duree"] = round(pat["duree"] + float(params["rotation_dans_mu"]), 1)
        fmin = max(0, s - int(params["avance_max"]), t + int(params["delai_preparation"]))
        fmax = min(T - 1, s + tol + int(params["retard_max"]))
        siennes = np.flatnonzero(vac_chir == pat["chir"])
        fen = siennes[(jour_v[siennes] >= fmin) & (jour_v[siennes] <= fmax)]
        longues = fen[cap_v[fen] >= pat["duree"] + z * pat["sigma"]]
        adm = longues if len(longues) else fen
        if not len(adm):
            adm = siennes[jour_v[siennes] >= fmin]
        if not len(adm):
            sans_vacation += 1
            continue
        pat.update(id=f"N{len(garde) + 1:04d}", source="flux", tolerance=tol, limite=t + int(params["delai_medical"]),
                   fenetre_min=int(fmin), fenetre_max=int(fmax), admissibles=[int(v) for v in adm])
        garde.append(pat)
    return {"patients": garde, "resume": {
        "par_jour": float(debit), "arrivees": arrivees, "ajoutes": len(garde), "apres_horizon": apres_horizon,
        "sans_vacation": sans_vacation, "ambulatoires": int(sum(p["ambu"] for p in garde)),
        "minutes": round(float(sum(p["duree"] for p in garde))),
    }}


def tirer_flux_tirages(generateur, predicteur, params, d, debit, graine, nb_tirages, rappel=None):
    """Un flux par tirage d'aléas (graines graine, graine + 1, ...), comme tirer_aleas dans robustesse_plan."""
    res = []
    for r in range(int(nb_tirages)):
        if rappel:
            rappel(0.0, f"Nouveaux patients du tirage {r + 1}/{nb_tirages}")
        res.append(tirer_flux(generateur, predicteur, params, d, debit, graine + r))
    return res


def injecter(d, solution, ids, aleas, flux, params_aleas=None, graine=None):
    """d, solution, identifiants et aléas complétés par les nouveaux patients. Leur vacation
    vaut -1 : ils n'existent pour les agents qu'à partir de leur jour d'arrivée (aleas["arrivee"]).
    graine : celle du tirage d'aléas (défaut : la graine des réglages)."""
    P = flux["patients"]
    n, N = len(P), len(d["duree"])
    d2 = dict(d)
    for k in CHAMPS:
        if d.get(k) is not None:
            d2[k] = list(d[k]) + [q[k] for q in P]
    p = parametres(params_aleas)
    rng = np.random.default_rng([int(p["graine"] if graine is None else graine), 2])
    mu = np.asarray([q["duree"] for q in P], dtype=float)
    sig = np.asarray([q["sigma"] for q in P], dtype=float)
    duree_reelle = np.maximum(10.0, rng.normal(mu, sig)) if p["retard_bloc"] else mu.copy()
    annule = rng.random(n) < p["taux_annulation"]
    preavis = rng.integers(1, max(1, p["preavis_annulation_max"]) + 1, size=n)
    prolonge = (rng.random(n) < p["part_prolongations"]) & (np.asarray([q["ambu"] for q in P]) == 0)
    a2 = {**aleas,
          "duree_reelle": list(aleas["duree_reelle"]) + duree_reelle.tolist(),
          "annule": list(aleas["annule"]) + annule.tolist(),
          "preavis": list(aleas["preavis"]) + preavis.tolist(),
          "prolonge": list(aleas["prolonge"]) + prolonge.tolist(),
          "arrivee": [None] * N + [int(q["arrivee"]) for q in P]}
    return d2, list(solution) + [-1] * n, list(ids) + [q["id"] for q in P], a2
