"""Aléas du planning et réoptimisation (parties 5 à 7 de la soutenance).

Six aléas, tirés au hasard avec une graine fixée sur l'horizon du planning (jour 0 = lundi) ;
le sixième (refus des patients) est tiré dans src/sma.py au moment où une date est proposée.
Ils ne dépendent pas du planning testé : deux plannings (ou deux stratégies) tirés avec la
même graine subissent exactement les mêmes aléas.

1. Urgences : séjours en urgence RÉELS de la base (hors COVID) pendant T jours consécutifs,
   tirés au hasard dans l'historique (période commençant un lundi, hors printemps 2020).
   Le planning ne compte pas les urgences (profil MOYEN seulement prévu : urgences_prevues_lits) ; ici elles
   arrivent pour de vrai, jour après jour. intensite_urgences = 1,5 : chaque séjour compte
   une ou deux fois (1,5 en moyenne).
2. Annulations : chaque patient est annulé avec la probabilité taux_annulation, annoncée
   1 à preavis_annulation_max jours avant son opération.
3. Lits fermés : lits_fermes lits indisponibles pendant fermeture_duree nuits à partir du
   jour fermeture_debut, annoncé preavis_fermeture jours avant.
4. Retard bloc : durée réelle d'une intervention ~ N(µ_i, σ_i), au moins 10 min ; la
   rotation entre deux patients s'ajoute.
5. Séjours prolongés : un patient hospitalisé reste une nuit de plus avec la probabilité
   part_prolongations ; on l'apprend le jour de sa sortie prévue.
6. Refus des patients : un patient à qui l'on propose une nouvelle date la refuse avec la probabilité
   refus_avance (date plus tôt) ou refus_report (date plus tard). Tiré dans src/sma.py au moment où la
   date est proposée (tirage propre au patient et au jour, à partir de la graine du tirage : "graine").
   Hypothèses à dire d'expert : la base ne dit pas combien de patients refusent une nouvelle date.

reparer() est la réoptimisation centralisée : une recherche locale qui minimise
F + coût × (nombre de patients déplacés), en ne bougeant que les patients pas encore
convoqués. Elle utilise l'Evaluateur incrémental de modele.py, sans modifier evaluer.
"""
import random

import numpy as np
import pandas as pd

from .modele import Evaluateur
from .robustesse import reglages_valides

# Valeurs de démonstration (réglables dans l'onglet « Simulation et aléas »)
PARAMS_ALEAS = {
    "intensite_urgences": 1.0,     # × les urgences réelles de la période tirée
    "taux_annulation": 0.05,       # part des patients annulés
    "preavis_annulation_max": 7,   # jours : annulation annoncée 1 à 7 jours avant l'opération
    "lits_fermes": 10,             # lits indisponibles (fermeture estivale d'une partie du service)...
    "fermeture_debut": 14,         # ... à partir de ce jour de l'horizon...
    "fermeture_duree": 14,         # ... pendant ce nombre de nuits
    "preavis_fermeture": 3,        # jours entre l'annonce et la fermeture
    "retard_bloc": True,           # durées réelles tirées selon N(µ_i, σ_i)
    "tolerance_depassement": 30,   # min : on ne commence pas une intervention qui finirait au-delà de H_v + 30 min
    "part_prolongations": 0.15,    # part des hospitalisés qui restent une nuit de plus
    "delai_prevenance": 3,         # jours : un patient opéré avant t + 3 est convoqué, on ne le déplace plus
    # refus d'une nouvelle date par le patient (src/sma.py, patient_accepte) ; valeurs supposées, sans donnée
    # dans la base (remarque du médecin à la soutenance intermédiaire, 2026-10-17)
    "refus_avance": 0.30,          # probabilité de refuser une date plus tôt (s'organiser en quelques jours)
    "refus_report": 0.15,          # probabilité de refuser une date plus tard (il tient à sa date)
    "marge_lits": 1,               # lits gardés libres par l'agent Lits quand il accepte un patient
    # agents : un créneau libéré par une annulation n'est comblé que si avancer un patient augmente F d'au plus
    # cette valeur (unités de F ; 5 = une nuit qui passe de 2 à 3 lits d'écart à la cible) ; choix de l'utilisateur, 2026-10-19
    "tolerance_comblement": 5.0,
    "cout_deplacement": 20.0,      # réoptimisation centralisée : coût (unités de F) d'un patient déplacé
    "budget_reparation": 3000,     # réoptimisation centralisée : mouvements essayés à chaque réparation
    # vacations libérées (sans patient dans le planning de départ) : « utilisables » comme les autres,
    # « dernier_recours » (seulement si aucune autre vacation ne garde la marge de la salle ou n'est
    # acceptée) ou « fermees » (jamais rouvertes, le praticien ne vient pas) ; choix de l'utilisateur, 2026-10-13
    "vacations_liberees": "dernier_recours",
    "graine": 0,
}
# Aléas « ordinaires » (sans fermeture de lits) : scénarios et indicateur de robustesse du planning
ALEAS_ORDINAIRES = {"intensite_urgences": 1.0, "taux_annulation": 0.05, "lits_fermes": 0, "part_prolongations": 0.15}
# Aléas forts (scénario « Perturbations fortes », préréglage « Crise »)
ALEAS_CRISE = {"intensite_urgences": 2.0, "taux_annulation": 0.10, "lits_fermes": 12, "fermeture_duree": 21,
               "part_prolongations": 0.25}


def parametres(params=None):
    """Paramètres complets (valeurs par défaut + réglages reçus, convertis).
    reglages_score (facultatif) : seuils et poids du score de robustesse modifiés dans l'onglet
    Simulation (src/robustesse.reglages_valides) ; absent = valeurs par défaut."""
    p = {**PARAMS_ALEAS, **{k: v for k, v in (params or {}).items() if k in PARAMS_ALEAS and v is not None}}
    for k, v in PARAMS_ALEAS.items():
        if isinstance(v, bool):
            p[k] = p[k].strip().lower() in ("1", "true", "oui", "on") if isinstance(p[k], str) else bool(p[k])
        else:
            p[k] = type(v)(float(p[k])) if isinstance(v, int) else type(v)(p[k])
    reglages = reglages_valides((params or {}).get("reglages_score"))
    if reglages:
        p["reglages_score"] = reglages
    return p


def historique_urgences(df):
    """Séjours en urgence de la base (hors COVID), en jours depuis le premier lundi de la base :
    entrée et nuits des hospitalisés, jour des urgences ambulatoires, jours COVID à éviter."""
    debut = df["date_entree"].min().normalize()
    debut -= pd.Timedelta(days=int(debut.dayofweek))
    fin = df["date_inter"].max().normalize()
    u = df[(df["urgence"] == 1) & (df["covid"] == 0)]
    hosp, amb = u[u["nuits"] > 0], u[u["nuits"] == 0]
    covid = df[df["covid"] == 1]["date_inter"]
    return {
        "debut": debut.strftime("%Y-%m-%d"), "nb_jours": int((fin - debut).days) + 1,
        "hosp_entree": (hosp["date_entree"].dt.normalize() - debut).dt.days.astype(int).tolist(),
        "hosp_nuits": hosp["nuits"].astype(int).tolist(),
        "ambu_jour": (amb["date_inter"].dt.normalize() - debut).dt.days.astype(int).tolist(),
        "covid": [int((covid.min().normalize() - debut).days), int((covid.max().normalize() - debut).days)]
        if len(covid) else None,
    }


def tirer_aleas(d, hist, params=None, graine=0):
    """Tire les cinq aléas sur l'horizon de d (indépendamment du planning)."""
    p = parametres(params)
    rng = np.random.default_rng(graine)
    T, N = d["nb_jours"], len(d["duree"])
    k = max(0.0, p["intensite_urgences"])

    def copies(n):   # nombre de fois que compte chaque séjour (k en moyenne)
        return int(k) + (rng.random(n) < k - int(k)).astype(int)

    # 1. urgences : une période réelle de T jours, commençant un lundi, hors COVID
    debuts = [s for s in range(0, max(1, hist["nb_jours"] - T + 1), 7)
              if not hist.get("covid") or s + T <= hist["covid"][0] or s > hist["covid"][1]]
    s = int(rng.choice(debuts))
    e = np.asarray(hist["hosp_entree"], int) - s
    L = np.asarray(hist["hosp_nuits"], int)
    m = (e < T) & (e + L > 0)
    urgences = []   # (jour où on l'apprend, première nuit, fin exclue)
    for ei, Li, c in zip(e[m], L[m], copies(int(m.sum()))):
        urgences += [(int(max(0, ei)), int(max(0, ei)), int(min(T, ei + Li)))] * int(c)
    a = np.asarray(hist["ambu_jour"], int) - s
    ma = (a >= 0) & (a < T)
    urg_ambu = np.zeros(T)
    np.add.at(urg_ambu, a[ma], copies(int(ma.sum())))
    # 2. annulations
    annule = rng.random(N) < p["taux_annulation"]
    preavis = rng.integers(1, max(1, p["preavis_annulation_max"]) + 1, size=N)
    # 3. lits fermés
    deb = p["fermeture_debut"] if p["fermeture_debut"] < T else T // 2
    fin = min(T, deb + p["fermeture_duree"])
    # 4. retard bloc
    mu = np.asarray(d["duree"], float)
    sig = np.asarray(d["sigma"], float) if d.get("sigma") is not None else np.zeros(N)
    duree_reelle = np.maximum(10.0, rng.normal(mu, sig)) if p["retard_bloc"] else mu.copy()
    # 5. séjours prolongés
    prolonge = (rng.random(N) < p["part_prolongations"]) & (np.asarray(d["ambu"]) == 0)
    debut_hist = pd.Timestamp(hist["debut"]) + pd.Timedelta(days=s)
    reserves = d.get("urgences_prevues_lits", d.get("lits_reserves"))
    return {
        "graine": int(graine),   # refus des patients : tirés dans src/sma.py à partir de cette graine
        "urgences": urgences, "urg_ambu": urg_ambu.tolist(),
        "annule": annule.tolist(), "preavis": preavis.tolist(),
        "fermeture": {"debut": int(deb), "fin": int(fin), "lits": int(p["lits_fermes"]),
                      "annonce": int(max(0, deb - p["preavis_fermeture"]))},
        "duree_reelle": duree_reelle.tolist(), "prolonge": prolonge.tolist(),
        "resume": {
            "periode_historique": f"du {debut_hist.strftime('%d/%m/%Y')} au "
                                  f"{(debut_hist + pd.Timedelta(days=T - 1)).strftime('%d/%m/%Y')}",
            "urgences_hospitalisees": len(urgences),
            "lits_nuits_urgences": int(sum(f - dbt for _, dbt, f in urgences)),
            "lits_nuits_reserves": round(float(np.sum(reserves)), 1) if reserves is not None else 0.0,
            "urgences_ambulatoires": int(urg_ambu.sum()),
            "annulations": int(annule.sum()), "prolongations": int(prolonge.sum()),
            "ecart_moyen_duree_min": round(float(np.mean(np.abs(duree_reelle - mu))), 1) if N else 0.0,
        },
    }


def reparer(d, solution, jour_min, budget=3000, cout_deplacement=20.0, graine=0):
    """Réoptimisation centralisée : recherche locale (descente) qui minimise
        F(d) + cout_deplacement × nombre de patients dont la vacation a changé.
    jour_min : {patient mobile i: premier jour où il peut être opéré} ; les autres patients
    ne bougent pas (déjà convoqués). solution peut contenir -1 (patient annulé).
    Renvoie (nouvelle solution, valeur de l'objectif)."""
    rng = random.Random(graine)
    cands = {i: [v for v in d["admissibles"][i] if d["jour"][v] >= j] for i, j in jour_min.items()}
    cands = {i: c for i, c in cands.items() if c and solution[i] >= 0}
    ev = Evaluateur(d, solution)
    depart = list(solution)
    if not cands:
        return depart, ev.valeur()
    mobiles = list(cands)
    nb_dep = 0
    total = ev.valeur()
    for _ in range(int(budget)):
        i = rng.choice(mobiles)
        v = rng.choice(cands[i])
        if v == ev.sol[i]:
            continue
        dep = nb_dep - (ev.sol[i] != depart[i]) + (v != depart[i])
        val = ev.essayer(i, v) + cout_deplacement * dep
        if val < total - 1e-9:
            ev.deplacer(i, v)
            nb_dep, total = dep, val
    return list(ev.sol), total
