"""Chargement et nettoyage de la base des séjours chirurgicaux (2019-2022).

Les fichiers de data/ ne sont jamais modifiés : la base nettoyée est écrite
dans data/processed/sejours.pkl (cache) et data/processed/sejours.csv (lisible).
"""
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_DATA = RACINE / "data"
DOSSIER_PROCESSED = DOSSIER_DATA / "processed"

# Mois COVID : mars, avril et mai 2020. Activité du bloc rapportée à la moyenne
# 2019/2022 du même mois : mars 57 %, avril 25 %, mai 95 % mais confinement jusqu'au
# 11/05 puis reprise progressive ; retour à la normale dès juin 2020 (109 %). Les
# vagues suivantes (novembre 2020, printemps 2021) n'ont pas fait baisser l'activité.
COVID_DEBUT = pd.Timestamp("2020-03-01")
COVID_FIN = pd.Timestamp("2020-05-31")
MOIS_COVID = "mars, avril et mai 2020"

# Noms lisibles des colonnes, dans l'ordre du fichier (les noms d'origine ont
# des problèmes d'encodage : on renomme par position)
NOMS_COLONNES = [
    "no_cas", "date_entree", "date_sortie", "date_naissance", "sexe", "cim",
    "cim_assoc1", "cim_assoc2", "cim_assoc3", "cim_assoc4", "cim_assoc5",
    "ccam1", "ccam2", "ccam3", "ccam4", "ghm", "ghs", "annee", "id_patient",
    "date_inter", "praticien", "nom_chir",
    "h_sspi", "h_entree_salle", "h_incision", "h_sortie_salle",  # colonnes 22 à 25
    "anesth_type", "anesth_locoreg", "duree_sejour_col", "type_interv",
]

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def chemin_sejours():
    """Fichier Excel de la base des séjours (le nom exact varie selon la copie)."""
    fichiers = sorted(DOSSIER_DATA.glob("don*bloc*anonyme*.xlsx"))
    if not fichiers:
        raise FileNotFoundError(f"Base des séjours introuvable dans {DOSSIER_DATA}")
    return fichiers[0]


def chemin_grille():
    """Fichier Excel de la grille des vacations."""
    fichiers = sorted(DOSSIER_DATA.glob("Vacations*.xlsx"))
    if not fichiers:
        raise FileNotFoundError(f"Grille des vacations introuvable dans {DOSSIER_DATA}")
    return fichiers[0]


def _heure_en_minutes(x):
    """Heure -> minutes depuis minuit. 00:00:00 est une valeur manquante, pas minuit."""
    if not isinstance(x, time):
        return np.nan
    m = x.hour * 60 + x.minute
    return np.nan if m == 0 else float(m)


def nettoyer(brut):
    """Renomme les colonnes et calcule les variables dérivées."""
    df = brut.copy()
    df.columns = NOMS_COLONNES
    for h in ["h_sspi", "h_entree_salle", "h_incision", "h_sortie_salle"]:
        df[h] = df[h].map(_heure_en_minutes)

    # Durée opératoire = sortie de salle - entrée en salle (minutes)
    df["duree_op"] = df["h_sortie_salle"] - df["h_entree_salle"]
    df.loc[df["duree_op"] <= 0, "duree_op"] = np.nan

    # La colonne de durée de séjour contient des aberrations (jusqu'à 2,4 millions) :
    # on recalcule le nombre de nuits à partir des dates
    df["nuits"] = (df["date_sortie"] - df["date_entree"]).dt.days
    df["nuits_post_op"] = (df["date_sortie"] - df["date_inter"]).dt.days
    df["jours_avant_op"] = (df["date_inter"] - df["date_entree"]).dt.days
    # Ambulatoire = entrée et sortie le même jour (et non « durée == 1 »)
    df["ambu"] = (df["nuits"] == 0).astype(int)

    df["age"] = ((df["date_inter"] - df["date_naissance"]).dt.days / 365.25).round(1)
    df["jour_semaine"] = df["date_inter"].dt.dayofweek
    df["week_end"] = (df["jour_semaine"] >= 5).astype(int)
    df["covid"] = df["date_inter"].between(COVID_DEBUT, COVID_FIN).astype(int)

    df["cim"] = df["cim"].fillna("").str.strip().str.upper()
    df["cim_chapitre"] = df["cim"].str[:1].replace("", "?")
    df["cim3"] = df["cim"].str[:3].replace("", "?")
    df["type_interv"] = df["type_interv"].fillna("Inconnu").str.strip()
    df["anesth_type"] = df["anesth_type"].fillna("Non renseigné")
    df["anesth_locoreg"] = df["anesth_locoreg"].fillna("Aucune")
    df["praticien"] = df["praticien"].str.strip()
    return df


def charger_sejours(recalculer=False):
    """Base nettoyée (avec cache dans data/processed/)."""
    DOSSIER_PROCESSED.mkdir(parents=True, exist_ok=True)
    cache = DOSSIER_PROCESSED / "sejours.pkl"
    if cache.exists() and not recalculer:
        # le tri urgence / programmé est refait à chaque chargement (calcul rapide) :
        # un changement de la règle (src/urgences.py) s'applique sans recalculer le cache
        from .urgences import marquer_urgences
        return marquer_urgences(pd.read_pickle(cache))
    df = nettoyer(pd.read_excel(chemin_sejours()))
    # Le tri urgence / programmé est fait ici pour être partagé par tous les modules
    from .urgences import marquer_urgences
    df = marquer_urgences(df)
    df.to_pickle(cache)
    df.to_csv(DOSSIER_PROCESSED / "sejours.csv", index=False, encoding="utf-8")
    return df


def temps_rotation(df):
    """Temps de rotation médian (minutes) entre deux patients d'un même chirurgien
    le même jour : entrée en salle du suivant - sortie du précédent (écarts de 1 à 90 min)."""
    x = df.dropna(subset=["h_entree_salle", "h_sortie_salle"]).sort_values(
        ["praticien", "date_inter", "h_entree_salle"])
    ecart = x["h_entree_salle"] - x.groupby(["praticien", "date_inter"])["h_sortie_salle"].shift()
    ecart = ecart[(ecart > 0) & (ecart <= 90)]
    return float(ecart.median()) if len(ecart) else 15.0


def occupation_historique(df, debut=None, fin=None):
    """Lits occupés chaque nuit (nuit de la date d'entrée à la veille de la sortie)
    et places ambulatoires occupées chaque jour, sur l'historique."""
    debut = debut or df["date_inter"].min()
    fin = fin or df["date_inter"].max()
    jours = pd.date_range(debut, fin)
    diff = pd.Series(0, index=pd.date_range(debut, fin + pd.Timedelta(days=120)))
    hosp = df[df["nuits"] > 0]
    for e, s in zip(hosp["date_entree"], hosp["date_sortie"]):
        if e < debut:
            e = debut
        if s > e:
            diff[e] += 1
            diff[s] -= 1
    lits = diff.cumsum().reindex(jours)
    places = df[df["ambu"] == 1].groupby("date_inter").size().reindex(jours, fill_value=0)
    return pd.DataFrame({"lits": lits, "places": places})


def statistiques_reference(df):
    """Chiffres à comparer à la section 4 du CLAUDE.md (77 min, 38 min, 54,5 %, 13,0 lits...)."""
    d = df["duree_op"].dropna()
    occ = occupation_historique(df, pd.Timestamp("2019-01-02"), pd.Timestamp("2022-12-30"))
    semaine = occ[occ.index.dayofweek < 5]
    ecart_type_intra = df.groupby("type_interv")["duree_op"].transform(lambda s: s - s.mean()).std()
    return {
        "n_sejours": int(len(df)),
        "n_patients": int(df["id_patient"].nunique()),
        "n_praticiens": int(df["praticien"].nunique()),
        "duree_op_moyenne": round(float(d.mean()), 1),
        "duree_op_ecart_type": round(float(d.std()), 1),
        "ecart_type_a_type_fixe": round(float(ecart_type_intra), 1),
        "part_ambulatoire": round(float(df["ambu"].mean()), 4),
        "part_duree_col_egale_1": round(float((df["duree_sejour_col"] == 1).mean()), 4),
        "lits_moyenne_toutes_nuits": round(float(occ["lits"].mean()), 2),
        "lits_moyenne_nuits_semaine": round(float(semaine["lits"].mean()), 2),
        "lits_max": int(occ["lits"].max()),
        "lits_ecart_type": round(float(occ["lits"].std()), 2),
        "lits_par_jour_semaine": [round(float(x), 1) for x in occ["lits"].groupby(occ.index.dayofweek).mean()],
        "places_ambu_moyenne_jour_ouvre": round(float(semaine["places"].mean()), 2),
        "places_ambu_max": int(occ["places"].max()),
        "part_urgences": round(float(df["urgence"].mean()), 4) if "urgence" in df else None,
    }
