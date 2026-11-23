"""Paramètres réglables (onglet Technique) et construction de l'instance initiale.

Scénario « nouvel hôpital » : le bloc ouvre le lundi DATE_OUVERTURE avec un planning
vierge. Toutes les demandes des premières semaines arrivent en même temps : ce sont
les interventions programmées réellement faites sur une fenêtre de 2022, décalées
sur le nouveau calendrier (même jour de la semaine). Leur durée, leur caractère
ambulatoire et leurs nuits sont PRÉDITS (modèle appris jusqu'au 31/12/2021, donc
sans fuite d'information) ; les vraies valeurs servent à l'évaluation a posteriori.

Délai clinique d'un patient (dates en jours de l'horizon) :
    souhait s_i    date d'opération souhaitée par le patient / le chirurgien
    tolérance τ_i  jours acceptés après s_i : hors de [s_i, s_i + τ_i], W6 par jour d'écart
    limite d_i     limite MÉDICALE = date de la demande + délai médical maximal (60 j) :
                   au-delà, chaque jour de retard coûte W3 * w_i (w_i = urgence du diagnostic)
    fenêtre        [s_i - avance_max, s_i + τ_i + retard_max] : bornes dures de
                   admissibles[i] (on ne propose jamais en dehors)
"""
import copy
import json

import numpy as np
import pandas as pd

from . import grille as G
from .chargement import DOSSIER_PROCESSED
from .horaires import HORAIRES_DEFAUT
from .modele import EPSILON_SALLES, Z_BETA, construire_d

FICHIER_PARAMETRES = DOSSIER_PROCESSED / "parametres.json"

CHAPITRES_CIM = {
    "A": "Maladies infectieuses", "B": "Maladies infectieuses", "C": "Tumeurs malignes",
    "D": "Tumeurs bénignes, sang", "E": "Maladies endocriniennes", "F": "Troubles mentaux",
    "G": "Système nerveux (canal carpien...)", "H": "Œil, oreille", "I": "Appareil circulatoire (varices...)",
    "J": "Appareil respiratoire", "K": "Appareil digestif", "L": "Peau (ongle incarné...)",
    "M": "Système ostéo-articulaire (arthrose, ménisque...)", "N": "Appareil génito-urinaire",
    "Q": "Malformations congénitales", "R": "Symptômes et signes", "S": "Lésions traumatiques (fractures...)",
    "T": "Traumatismes multiples, complications de prothèses", "W": "Causes externes",
    "Z": "Soins de contrôle (ablation de matériel...)",
}
LIBELLES_CIM3 = {
    "I83": "Varices des membres inférieurs", "M23": "Lésion interne du genou (ménisque)",
    "M20": "Déformation des orteils (hallux valgus)", "M17": "Gonarthrose", "M75": "Lésions de l'épaule",
    "M65": "Synovite, ténosynovite", "Z47": "Soins de contrôle orthopédiques", "M16": "Coxarthrose",
    "G56": "Mononévrite du membre supérieur (canal carpien)", "M51": "Atteinte des disques intervertébraux",
    "M25": "Autres affections articulaires", "S52": "Fracture de l'avant-bras", "M19": "Autres arthroses",
    "S82": "Fracture de la jambe et de la cheville", "M99": "Lésions biomécaniques",
    "M24": "Autres lésions articulaires", "S62": "Fracture du poignet et de la main",
    "T84": "Complication de prothèse ou d'implant", "S42": "Fracture de l'épaule et du bras",
    "M48": "Spondylopathie (canal lombaire étroit)", "G57": "Mononévrite du membre inférieur",
    "S83": "Luxation, entorse du genou", "M72": "Fibromatose (Dupuytren)", "M21": "Déformation des membres",
    "M77": "Enthésopathie", "M18": "Rhizarthrose", "S86": "Lésion de tendon de la jambe (Achille)",
    "L60": "Maladie des ongles", "M84": "Pseudarthrose", "S63": "Luxation, entorse du poignet et de la main",
    "S66": "Lésion de tendon du poignet et de la main", "M22": "Affection de la rotule",
    "M71": "Bursopathie", "S46": "Lésion de tendon de l'épaule (coiffe)", "M50": "Atteinte des disques cervicaux",
    "Q66": "Malformation congénitale du pied", "S72": "Fracture du fémur",
    "M67": "Affection des synoviales et tendons", "M76": "Enthésopathie du membre inférieur",
    "S64": "Lésion de nerf du poignet et de la main", "M79": "Affection des tissus mous",
    "S92": "Fracture du pied", "M54": "Dorsalgie", "S61": "Plaie du poignet et de la main",
    "S76": "Lésion de tendon de la hanche et de la cuisse",
}

PARAMETRES_DEFAUT = {
    # poids de F (2026-10-19) : barème fixé à dire d'expert (W3 = 30, W4 = 100), puis W1′, W2, W5, W6 choisis par
    # optimisation bayésienne sous contraintes (scripts/optimisation_poids.py, onglet Technique « Poids par défaut de F »),
    # arrondis à 2 chiffres. Les constantes de modele.py (evaluer sans clé poids, code des coéquipiers) ne changent pas.
    "poids": {"W1": 1.0, "W1b": 2.5, "W2": 0.012, "W3": 30.0, "W4": 100.0, "W5": 0.19, "W6": 0.014},
    # terme W2 : temps de bloc perdu dans les vacations utilisées + guidage ε (src/modele.py, 2026-10-13)
    "terme_salles": "regroupement",
    "epsilon_salles": EPSILON_SALLES,
    "z_beta": Z_BETA,
    "penaliser_capacites": True,
    "alpha": 0.6,                 # E_t le week-end (à valider par le médecin)
    "cap_lits_semaine": 25,
    "cap_lits_week_end": 18,
    "cap_ambu": 16,
    "rotation_dans_mu": 5,        # minutes ajoutées à µ_i (choix de l'utilisateur, 2026-10-20 ; 0 = définition de la section 6)
    "avance_max": 7,
    "tolerance": 0,                   # τ_i par défaut (terme W6)
    "delai_medical": 60,              # d_i = demande + 60 j : limite médicale (terme W3)
    "retard_max": 21,
    "delai_preparation": 2,       # jours minimum entre la demande et l'opération
    # w_i : poids punitif du retard, par chapitre CIM puis par code à 3 caractères
    "poids_chapitres": {c: 1.0 for c in CHAPITRES_CIM} | {"C": 3.0, "S": 2.0, "T": 1.5, "Z": 0.5},
    "poids_codes": {"S72": 3.0, "T84": 2.0},
    "grille": "actuelle",         # grille utilisée par le planning : actuelle / nouvelle
    "grille_marge": 0.10,
    "grille_periode": ["2022-01-01", "2022-12-31"],
    "quantile_urgences": 0.8,
    "date_ouverture": "2026-10-05",
    "semaines_horizon": 12,
    "instance_debut": "2022-03-07",   # (script d'évaluation historique seulement)
    "instance_semaines": 8,           # horizon de l'attribution initiale (semaines)
    "initial_nb_patients": 600,       # x : patients inventés pour l'ouverture
    "initial_graine": 0,
    "methode_prediction": "ml",
    "exclure_covid": False,
    "horaires": dict(HORAIRES_DEFAUT),   # programme horaire des vacations (src/horaires.py)           # True : apprentissage sans les mois COVID (mars-mai 2020)
}


# réglages retirés de l'application (urgences dans F, ouverture de l'ambulatoire le week-end et les jours
# fériés, quantile de la demande) : ignorés au chargement, ils disparaissent du fichier au prochain enregistrement
CLES_OBSOLETES = ("inclure_urgences", "ambu_week_end", "ambu_feries", "grille_quantile_demande")


def charger_parametres():
    p = copy.deepcopy(PARAMETRES_DEFAUT)
    if FICHIER_PARAMETRES.exists():
        sauve = json.loads(FICHIER_PARAMETRES.read_text(encoding="utf-8"))
        for k, v in sauve.items():
            if k in CLES_OBSOLETES:
                continue
            if isinstance(v, dict) and isinstance(p.get(k), dict):
                p[k].update(v)
            else:
                p[k] = v
        if "terme_salles" not in sauve:
            # paramètres enregistrés avant le terme « regroupement » : leur W2 pondérait Σ (H_v - c_v)²
            # (minutes²) et n'a pas de sens pour le nouveau terme (minutes) -> poids par défaut
            p["poids"]["W2"] = PARAMETRES_DEFAUT["poids"]["W2"]
    # clés d'horaires retirées du modèle (ex. heure limite des ambulatoires) : ignorées
    p["horaires"] = {k: v for k, v in p["horaires"].items() if k in HORAIRES_DEFAUT}
    return p


def sauver_parametres(p):
    DOSSIER_PROCESSED.mkdir(parents=True, exist_ok=True)
    FICHIER_PARAMETRES.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def poids_retard(cim, params):
    """w_i du diagnostic : code à 3 caractères s'il est réglé, sinon chapitre, sinon 1."""
    cim = (cim or "").upper()
    if cim[:3] in params["poids_codes"]:
        return float(params["poids_codes"][cim[:3]])
    return float(params["poids_chapitres"].get(cim[:1], 1.0))


def ajouter_rotation(d, params, rotation):
    """Clés d'information de d pour le remplissage des salles : d["rotation"] = rotation mesurée
    entre deux patients, sauf si µ la contient déjà (d["rotation_dans_mu"] > 0, réglage rotation_dans_mu) ;
    d["horaires"] = réglages du programme horaire, pour que la simulation (src/sma.py) opère les
    patients dans le même ordre de passage que l'onglet Planning."""
    d["rotation_dans_mu"] = float(params.get("rotation_dans_mu", 0))
    d["rotation"] = 0.0 if d["rotation_dans_mu"] else float(rotation)
    d["horaires"] = horaires_effectifs(params, rotation)


def horaires_effectifs(params, rotation):
    """Réglages du programme horaire, avec la rotation entre deux patients : 0 si µ la contient déjà
    (rotation_dans_mu > 0, sinon elle serait comptée deux fois, corrigé le 2026-10-17), sinon la rotation
    mesurée `rotation`, comme dans la simulation. « Rotation ajoutée à µ » est le seul réglage de la
    rotation (choix de l'utilisateur, 2026-10-20)."""
    h = {**HORAIRES_DEFAUT, **(params.get("horaires") or {})}
    h["rotation"] = 0.0 if float(params.get("rotation_dans_mu") or 0) > 0 else float(rotation)
    return h


def options_d(params, reserve=None):
    """Arguments de construire_d issus des paramètres."""
    opts = dict(alpha=params["alpha"], cap_lits_semaine=params["cap_lits_semaine"],
                cap_lits_week_end=params["cap_lits_week_end"], cap_ambu=params["cap_ambu"],
                poids=params["poids"], z_beta=params["z_beta"],
                penaliser_capacites=params["penaliser_capacites"],
                terme_salles=params.get("terme_salles"), epsilon_salles=params.get("epsilon_salles"))
    # les urgences ne comptent pas dans F (planning des patients programmés seulement) : leur
    # profil moyen n'est gardé que comme prévision des agents de la simulation (src/sma.py)
    if reserve is not None:
        opts["lits_urgences"] = reserve["lits_moyens"].tolist()
        opts["places_urgences"] = reserve["places_moyennes"].tolist()
        opts["urgences_dans_f"] = False
    return opts


def feries_horizon(date_debut, nb_jours):
    debut = pd.Timestamp(date_debut).date()
    fer = set()
    for a in (debut.year, debut.year + 1):
        fer |= G.jours_feries(a)
    return {(f - debut).days for f in fer if 0 <= (f - debut).days < nb_jours}


def _delais(P, params, T):
    """Délais d'un patient à l'ouverture : toutes les demandes arrivent le jour 0.
    - date souhaitée s_i et tolérance τ_i : terme W6 ;
    - limite médicale d_i = jour de la demande (0) + délai médical maximal : terme W3 ;
    - fenêtre des dates proposables : [s_i - avance_max, s_i + τ_i + retard_max]."""
    if "tolerance" not in P:
        P["tolerance"] = params["tolerance"]
    P["tolerance"] = P["tolerance"].fillna(params["tolerance"]).astype(int)
    P["limite"] = int(params["delai_medical"])
    P["fenetre_min"] = np.maximum(0, P["souhait"] - params["avance_max"])
    P["fenetre_max"] = np.minimum(T - 1, P["souhait"] + P["tolerance"] + params["retard_max"])


def creer_instance(df, predicteur, grille, params, reserve=None, graine=0, rotation=0.0):
    """Instance initiale (planning vierge) : renvoie (d, P, vac) où P contient les
    patients retenus (dans l'ordre i) avec leurs valeurs prédites."""
    debut_src = pd.Timestamp(params["instance_debut"])
    T = 7 * int(params["instance_semaines"])
    x = df[(df["urgence"] == 0) & (df["jour_semaine"] < 5) & (df["date_inter"] >= debut_src)
           & (df["date_inter"] < debut_src + pd.Timedelta(days=T))].copy()
    pred = predicteur.predire(x, params["methode_prediction"])
    P = pd.DataFrame({
        "id": x["no_cas"].astype(str).values, "chir": x["praticien"].values,
        "type_interv": x["type_interv"].values, "cim": x["cim"].values,
        "age": x["age"].values, "sexe": x["sexe"].values,
        "duree": pred["duree"].values + params["rotation_dans_mu"], "sigma": pred["sigma"].values,
        "p_ambu": pred["p_ambu"].values, "ambu": pred["ambu"].values, "nuits": pred["nuits"].values,
        "souhait": (x["date_inter"] - debut_src).dt.days.values,
    })
    P["poids_retard"] = [poids_retard(c, params) for c in P["cim"]]
    _delais(P, params, T)
    vac = G.vacations_horizon(grille, params["date_ouverture"], T)
    feries = feries_horizon(params["date_ouverture"], T)
    d, P, exclus, vac = construire_d(P, vac, T, feries=feries, **options_d(params, reserve))
    d["exclus"] = len(exclus)
    ajouter_rotation(d, params, rotation)
    return d, P.reset_index(drop=True), vac


def creer_instance_liste(patients, predicteur, grille, params, reserve=None, rotation=0.0):
    """Instance initiale à partir de la liste des patients à programmer à l'ouverture
    (patients inventés et/ou ajoutés par le médecin). Les valeurs µ, σ, A, L sont
    prédites par le modèle appris sur 2019-2022. Renvoie (d, P, vac)."""
    from .generation import completer_predictions
    T = 7 * int(params["instance_semaines"])
    ouverture = pd.Timestamp(params["date_ouverture"]).normalize()
    liste = completer_predictions([dict(p) for p in patients], predicteur, params["methode_prediction"], params)
    P = pd.DataFrame(liste)
    P["duree"] = P["duree"] + params["rotation_dans_mu"]
    P["souhait"] = (pd.to_datetime(P["date_souhait"]) - ouverture).dt.days
    hors = (P["souhait"] < 0) | (P["souhait"] >= T)
    ecartes = [_ecarte(r, "date souhaitée hors des semaines du planning") for _, r in P[hors].iterrows()]
    P = P[~hors].reset_index(drop=True)
    _delais(P, params, T)
    vac = G.vacations_horizon(grille, params["date_ouverture"], T)
    feries = feries_horizon(params["date_ouverture"], T)
    P0 = P
    d, P, exclus, vac = construire_d(P, vac, T, feries=feries, **options_d(params, reserve))
    ecartes += [_ecarte(P0.iloc[idx], MOTIFS_EXCLUS.get(motif, motif)) for idx, motif in exclus]
    d["exclus"] = len(ecartes)
    # information (non lue par evaluer) : patients de la liste absents du planning, signalés dans l'onglet Planning
    d["ecartes"] = ecartes
    ajouter_rotation(d, params, rotation)
    return d, P.reset_index(drop=True), vac


# motifs de construire_d (src/modele.py), reformulés pour l'onglet Planning
MOTIFS_EXCLUS = {
    "chirurgien sans vacation": "son chirurgien n'a aucune vacation dans la grille",
    "aucune vacation dans l'horizon": "aucune vacation de son chirurgien entre sa date au plus tôt et la fin du planning",
}


def _ecarte(r, motif):
    """Patient de la liste qui ne peut pas être programmé, et pourquoi."""
    return {"id": str(r["id"]), "chir": r["chir"], "type_interv": r.get("type_interv"),
            "date_souhait": r.get("date_souhait"), "motif": motif}
