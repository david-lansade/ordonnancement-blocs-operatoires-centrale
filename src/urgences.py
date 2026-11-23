"""Tri urgences / programmé et dimensionnement de la réserve d'urgences.

La base ne marque pas les urgences : on les reconstruit avec un score d'indices.
Règle revue avec un chirurgien (13/10/2026) : les critères ESSENTIELS sont le
DIAGNOSTIC et l'ACTE. Le contexte (week-end, fin d'après-midi) ne fait que
confirmer : il ne suffit jamais, seul, à classer un séjour en urgence.

Score = diagnostic + acte + contexte ; urgence à partir de SEUIL_URGENCE (3) points.

| Famille     | Indice                                                       | Points |
|-------------|--------------------------------------------------------------|--------|
| Diagnostic  | CIM-10 en S (lésion traumatique) ou T00-T14 (traumatisme     | 2      |
|             | de siège non précisé)                                        |        |
| Acte        | type ostéosynthèse / réduction / fracture / luxation /       | 2      |
|             | embrochage / plaie (sauf « ablation de matériel »)           |        |
|             | ou type main / tendon / doigt (souvent programmé : canal     | 1      |
|             | carpien, Dupuytren…)                                         |        |
| Contexte    | intervention le week-end OU entrée en salle à 16 h ou plus   | 1 au   |
|             | (un seul point même si les deux sont vrais)                  | plus   |

Conséquences :
- diagnostic traumatique ET acte de traumatologie (2 + 2) : toujours une urgence ;
- diagnostic traumatique ET acte de la main (2 + 1) : urgence ;
- un seul critère essentiel (2) : urgence seulement si le contexte le confirme (+ 1) ;
- acte de la main sans diagnostic traumatique (1 + 1 au plus) : jamais une urgence ;
- contexte seul (1) : jamais une urgence (ancienne règle : le week-end valait 3 points
  et suffisait à lui seul).

L'indice « admission le jour même » n'est pas utilisé : dans cette base, 82 %
des hospitalisés non traumatiques entrent aussi le jour même (84 % pour les S),
il ne distingue donc pas les urgences.
"""
import numpy as np
import pandas as pd

from .chargement import COVID_DEBUT, COVID_FIN

SEUIL_URGENCE = 3
POINTS_DIAGNOSTIC = 2      # diagnostic traumatique (S, T00-T14)
POINTS_ACTE_TRAUMA = 2     # acte de traumatologie
POINTS_ACTE_MAIN = 1       # acte de la main / tendon (ambigu : souvent programmé)
POINTS_CONTEXTE = 1        # week-end ou fin d'après-midi (plafonné à 1 point)
MOTS_TRAUMA = r"osteosynth|r[ée]duction|fractur|luxation|embroch|plaie"
MOTS_MAIN = r"\bmain\b|tendon|doigt"


def marquer_urgences(df):
    """Ajoute les colonnes score_urgence, urgence (0/1) et motif_urgence.
    Le diagnostic et l'acte sont les critères essentiels ; le contexte ne fait que confirmer."""
    df = df.copy()
    t = df["type_interv"].str.lower()
    diag = df["cim"].str.startswith("S") | df["cim"].str.match(r"T(0\d|1[0-4])")
    acte_trauma = t.str.contains(MOTS_TRAUMA) & ~t.str.contains("ablation")
    acte_main = t.str.contains(MOTS_MAIN) & ~acte_trauma
    week_end = df["jour_semaine"] >= 5
    tard = df["h_entree_salle"] >= 16 * 60
    contexte = week_end | tard
    score = (POINTS_DIAGNOSTIC * diag.astype(int)
             + POINTS_ACTE_TRAUMA * acte_trauma.astype(int)
             + POINTS_ACTE_MAIN * acte_main.astype(int)
             + POINTS_CONTEXTE * contexte.astype(int))
    df["score_urgence"] = score
    df["urgence"] = (score >= SEUIL_URGENCE).astype(int)
    motifs = pd.Series("", index=df.index)
    for nom, masque in (("diagnostic traumatique", diag), ("acte de traumatologie", acte_trauma),
                        ("acte main / tendon", acte_main), ("week-end", week_end), ("fin d'après-midi", tard)):
        motifs = motifs.where(~masque, motifs + nom + ", ")
    df["motif_urgence"] = motifs.str.rstrip(", ")
    return df


def heures_urgence_par_jour(df, rotation):
    """Heures de salle consommées par les urgences chaque jour où le bloc est ouvert
    (hors printemps 2020). Les jours sans urgence comptent pour 0 h."""
    x = df[df["covid"] == 0]
    # jours ouvrés où le bloc a fonctionné (au moins 3 interventions programmées)
    prog = x[(x["urgence"] == 0)].groupby("date_inter").size()
    jours_ouverts = prog[prog >= 3].index
    urg = x[x["urgence"] == 1].copy()
    duree_type = df.groupby("type_interv")["duree_op"].median()
    urg["duree"] = urg["duree_op"].fillna(urg["type_interv"].map(duree_type)).fillna(df["duree_op"].median())
    heures = (urg.groupby("date_inter")["duree"].sum() + rotation * urg.groupby("date_inter").size()) / 60
    tous = pd.date_range(x["date_inter"].min(), x["date_inter"].max())
    tous = tous[~tous.to_series().between(COVID_DEBUT, COVID_FIN).values]
    serie = heures.reindex(tous, fill_value=0.0)
    ouvre = serie.index.dayofweek < 5
    # en semaine, on ne garde que les jours où le bloc était ouvert (fériés, fermetures exclus)
    garde = ~ouvre | serie.index.isin(jours_ouverts)
    return serie[garde]


def reserve_urgences(df, rotation, quantile=0.8, pas=0.5):
    """Réserve d'urgences par jour de la semaine = quantile (80 % par défaut) des heures passées.

    Renvoie un tableau (un ligne par jour, lundi = 0) avec la moyenne, le quantile,
    la réserve arrondie au pas supérieur (en heures), la part des urgences opérées
    le matin, l'heure médiane d'entrée en salle, et les lits / places consommés
    en moyenne par les urgences."""
    serie = heures_urgence_par_jour(df, rotation)
    urg = df[(df["urgence"] == 1) & (df["covid"] == 0)]
    lignes = []
    lits_nuit, places_jour = profil_hebergement_urgences(df)
    for j in range(7):
        h = serie[serie.index.dayofweek == j]
        u = urg[urg["jour_semaine"] == j]
        q = float(np.quantile(h, quantile)) if len(h) else 0.0
        lignes.append({
            "jour": j,
            "n_jours": int(len(h)),
            "part_jours_avec_urgence": round(float((h > 0).mean()), 3) if len(h) else 0.0,
            "moyenne_h": round(float(h.mean()), 2) if len(h) else 0.0,
            # part des jours où une réserve égale à la moyenne aurait été dépassée
            "part_au_dela_moyenne": round(float((h > h.mean()).mean()), 3) if len(h) else 0.0,
            "quantile": float(quantile),
            "quantile_h": round(q, 2),
            "reserve_h": float(np.ceil(q / pas - 1e-9) * pas),
            "n_urgences": int(len(u)),
            "part_matin": round(float((u["h_entree_salle"] < 13 * 60).mean()), 3) if len(u) else 0.0,
            "heure_mediane": round(float(u["h_entree_salle"].median() / 60), 2) if len(u) else None,
            "lits_moyens": round(float(lits_nuit[j]), 2),
            "places_moyennes": round(float(places_jour[j]), 2),
        })
    return pd.DataFrame(lignes)


def profil_hebergement_urgences(df):
    """Lits occupés en moyenne chaque nuit de la semaine par des patients arrivés en
    urgence, et places ambulatoires occupées par des urgences, hors COVID."""
    x = df[(df["covid"] == 0)]
    debut, fin = x["date_inter"].min(), x["date_inter"].max()
    jours = pd.date_range(debut, fin)
    diff = pd.Series(0.0, index=pd.date_range(debut, fin + pd.Timedelta(days=120)))
    hosp = x[(x["urgence"] == 1) & (x["nuits"] > 0)]
    for e, s in zip(hosp["date_entree"], hosp["date_sortie"]):
        e = max(e, debut)
        if s > e:
            diff[e] += 1
            diff[s] -= 1
    lits = diff.cumsum().reindex(jours)
    covid = jours.to_series().between(COVID_DEBUT, COVID_FIN).values
    lits = lits[~covid]
    places = x[(x["urgence"] == 1) & (x["ambu"] == 1)].groupby("date_inter").size()
    places = places.reindex(jours[~covid], fill_value=0)
    return (lits.groupby(lits.index.dayofweek).mean().reindex(range(7), fill_value=0).values,
            places.groupby(places.index.dayofweek).mean().reindex(range(7), fill_value=0).values)


def resume_tri(df):
    """Effectifs du tri urgences / programmé, pour le dashboard."""
    u = df[df["urgence"] == 1]
    return {
        "n_urgences": int(len(u)),
        "part_urgences": round(float(df["urgence"].mean()), 4),
        "distribution_score": {int(k): int(v) for k, v in df["score_urgence"].value_counts().sort_index().items()},
        "motifs_frequents": u["motif_urgence"].value_counts().head(8).to_dict(),
        "types_frequents": u["type_interv"].value_counts().head(8).to_dict(),
        "heure_mediane_urgences": round(float(u["h_entree_salle"].median() / 60), 2),
        "heure_mediane_programmes": round(float(df.loc[df["urgence"] == 0, "h_entree_salle"].median() / 60), 2),
        "part_hospitalises": round(float((u["ambu"] == 0).mean()), 3),
    }
