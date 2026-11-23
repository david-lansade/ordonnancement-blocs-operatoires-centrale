"""Grille hebdomadaire des vacations : lecture de la grille actuelle, construction
d'une nouvelle grille à partir de la demande, et calendrier des vacations v de l'horizon.

Une vacation de la grille est un dict :
    jour (0 = lundi ... 4 = vendredi), salle (1..5), chir (code praticien, ou
    "URGENCES" / "LIBRE"), debut / fin (minutes depuis minuit),
    semaines (positions dans le cycle de 4 semaines où elle a lieu).

Cycle : la grille actuelle alterne semaine paire / impaire, et certaines cases
« 1 sem. impaire sur 2 » : le cycle réel est donc de 4 semaines. La position
d'une date dans le cycle est son numéro de semaine ISO modulo 4 :
    0 et 2 = semaines paires, 1 = impaire « A », 3 = impaire « B ».
"""
import re
from datetime import date, timedelta

import numpy as np
import openpyxl
import pandas as pd

from .chargement import JOURS, chemin_grille

TOUTES = (0, 1, 2, 3)
PAIRES = (0, 2)
IMPAIRES = (1, 3)
SALLES = (1, 2, 3, 4, 5)
MATIN = (8 * 60, 13 * 60)            # 8h - 13h
APRES_MIDI = (13 * 60 + 30, 17 * 60 + 30)  # 13h30 - 17h30


def _fr(x, d=1):
    """Nombre au format français (virgule décimale), pour les textes affichés."""
    return f"{x:.{d}f}".replace(".", ",")


def h(texte):
    """'7h45' -> 465 minutes."""
    m = re.fullmatch(r"(\d{1,2})h(\d{2})?", texte.strip())
    return int(m.group(1)) * 60 + int(m.group(2) or 0)


def V(jour, salle, chir, debut, fin, semaines=TOUTES, source=""):
    return {"jour": jour, "salle": salle, "chir": chir, "debut": h(debut), "fin": h(fin),
            "semaines": tuple(semaines), "source": source}


# ---------------------------------------------------------------------------
# Grille actuelle, transcrite à la main depuis la feuille « Propo CDB à venir ».
# La colonne Salle 1 (E) est vide tous les jours : la salle 1 n'est pas ouverte.
# Chaque ligne cite la cellule d'origine ; verifier_transcription() recoupe
# automatiquement les codes avec le fichier Excel.
# ---------------------------------------------------------------------------
GRILLE_ACTUELLE = [
    # Lundi
    V(0, 2, "JT", "7h45", "17h30", source="F5 JT (Début 7h45, TVO 9,75 h)"),
    V(0, 3, "SR", "8h", "17h30", PAIRES, "I5 Semaine paire SR 8h/17h30"),
    V(0, 3, "GA", "8h", "13h", (1,), "J5 1 sem. impaire sur 2 : GA 8h/13h"),
    V(0, 3, "MT", "13h30", "17h30", (1,), "J8 Sem impaire : MT"),
    V(0, 3, "GA", "8h", "10h", (3,), "K5 1 sem. impaire sur 2 : GHREA (= GA) 8h/10h"),
    V(0, 3, "DS", "10h", "15h30", (3,), "K6 DEVOS 10h/15h30 (même chirurgien que DS)"),
    V(0, 4, "GA", "8h", "13h", PAIRES, "M5 Semaine paire : GA (2 prothèses - fin 13h)"),
    V(0, 4, "URGENCES", "13h30", "15h30", PAIRES, "M8 Sem. Paire : URGENCES"),
    V(0, 4, "URGENCES", "8h", "10h", (1,), "N5 1 sem. impaire sur 2 : TDO/BS (urg) 8h/10h"),
    V(0, 4, "DS", "10h", "15h30", (1,), "N6 DS 10h/15h30"),
    V(0, 4, "RL", "8h", "13h", (3,), "O5 1 sem. impaire sur 2 : RL 8h/13h"),
    V(0, 4, "MT", "13h30", "17h30", (3,), "O8 MT"),
    V(0, 5, "FN", "8h", "15h30", source="Q6 FN, Stop 15h30 (TVO 7,5 h)"),
    # Mardi
    V(1, 2, "JT", "7h45", "17h30", IMPAIRES, "F12 JT sem. impaire : fin 17h30"),
    V(1, 2, "JT", "7h45", "15h30", PAIRES, "F12 JT sem. paire : fin 15h30"),
    V(1, 3, "TR", "8h", "13h", source="I12 TR (TVO 5 h)"),
    V(1, 3, "MT", "13h30", "17h30", PAIRES, "I14 Semaine paire MT 13h30/17h30"),
    V(1, 3, "URGENCES", "13h30", "15h30", IMPAIRES, "J14 URGENCES, Stop 15h30 (TVO (2+4)/2)"),
    V(1, 4, "CT", "8h", "15h30", source="M12 CT, Stop 15h30 (TVO 7,5 h)"),
    V(1, 5, "MO", "8h", "17h30", source="Q12 MO (TVO 9,5 h)"),
    # Mercredi
    V(2, 2, "CL", "7h45", "17h30", source="F18 CL (TVO 9,75 h)"),
    V(2, 3, "DE", "8h", "17h30", source="I18 DE (TVO 9,5 h)"),
    V(2, 4, "DN", "8h", "13h", source="M18 DN 8h/13h"),
    V(2, 4, "URGENCES", "13h30", "15h30", source="M20 URGENCES, Stop 15h30"),
    V(2, 5, "MT", "8h", "13h", PAIRES, "Q18 Semaine paire MT 8h/13h"),
    V(2, 5, "URGENCES", "13h30", "15h30", PAIRES, "Q20 URGENCES, Stop 15h30"),
    V(2, 5, "JE", "8h", "15h30", IMPAIRES, "R18 Semaine impaire JE 8h/15h30"),
    # Jeudi
    V(3, 2, "DE", "7h45", "17h30", source="F24 DE (TVO 9,75 h)"),
    V(3, 3, "SR", "8h", "15h30", source="I24 SR, Stop 15h30 (le TVO du fichier dit 9,5 h)"),
    V(3, 4, "CT", "8h", "15h30", source="M24 CT, Stop 15h30 (TVO 7,5 h)"),
    V(3, 5, "LZ", "8h", "17h30", source="Q24 LZ (TVO 9,5 h)"),
    # Vendredi
    V(4, 2, "CL", "7h45", "13h", source="F30 CL 7h45/13h"),
    V(4, 2, "URGENCES", "13h30", "15h30", source="F32 URGENCES, Stop 15h30"),
    V(4, 3, "CU", "8h", "13h", source="I30 CU"),
    V(4, 3, "MT", "13h30", "15h30", source="I32 MT 13h30/15h30"),
    V(4, 4, "HA", "8h", "13h", PAIRES, "M30 Semaine paire HA 8h/13h"),
    V(4, 4, "FP", "13h30", "17h30", PAIRES, "M32 FP 13h30/17h30"),
    V(4, 4, "SM", "8h", "17h30", IMPAIRES, "N30 Semaine impaire SM 8h/17h30"),
    V(4, 5, "LR", "8h", "13h", source="Q30 LR"),
    V(4, 5, "LIBRE", "13h30", "17h30", source="Q32 LIBRE 13h30/17h30"),
]

# Correspondances de noms relevées dans la grille
ALIAS = {"GHREA": "GA", "DEVOS": "DS", "TDO/BS": "URGENCES"}
# Blocs de colonnes de chaque salle dans la feuille (Salle 1 = E, TVO = dernière colonne)
COLONNES_SALLES = {1: "E", 2: "FGH", 3: "IJKL", 4: "MNOP", 5: "QRS"}
LIGNES_JOURS = {0: (4, 9), 1: (11, 15), 2: (17, 21), 3: (23, 27), 4: (29, 33)}


def minutes_par_semaine(grille, chir=None):
    """Minutes de vacation par semaine (moyenne sur le cycle), par code."""
    res = {}
    for v in grille:
        if chir is None or v["chir"] == chir:
            res[v["chir"]] = res.get(v["chir"], 0) + (v["fin"] - v["debut"]) * len(v["semaines"]) / 4
    return res


def verifier_transcription():
    """Recoupe la transcription avec le fichier Excel : pour chaque jour et chaque
    salle, les codes lus dans les cellules doivent être ceux de la transcription.
    Renvoie la liste des écarts (vide si tout concorde) et le TVO par salle."""
    wb = openpyxl.load_workbook(chemin_grille())
    ws = wb["Propo CDB à venir"]
    connus = {v["chir"] for v in GRILLE_ACTUELLE} | set(ALIAS)
    ecarts = []
    for jour, (l0, l1) in LIGNES_JOURS.items():
        for salle, cols in COLONNES_SALLES.items():
            textes = []
            for col in cols[:-1] if len(cols) > 1 else cols:
                for lig in range(l0, l1 + 1):
                    val = ws[f"{col}{lig}"].value
                    if isinstance(val, str):
                        textes.append(val)
            codes_excel = set()
            for t in textes:
                for mot in re.findall(r"[A-Z][A-Z/]+", t.upper()):
                    if mot in connus:
                        codes_excel.add(ALIAS.get(mot, mot))
            codes_transcrits = {v["chir"] for v in GRILLE_ACTUELLE if v["jour"] == jour and v["salle"] == salle}
            if codes_excel != codes_transcrits:
                ecarts.append({"jour": JOURS[jour], "salle": salle,
                               "excel": sorted(codes_excel), "transcription": sorted(codes_transcrits)})
    tvo = {s: round(sum((v["fin"] - v["debut"]) * len(v["semaines"]) / 4
                        for v in GRILLE_ACTUELLE if v["salle"] == s) / 60, 2) for s in SALLES}
    return ecarts, tvo


# ---------------------------------------------------------------------------
# Calendrier : jours fériés et vacations de l'horizon
# ---------------------------------------------------------------------------
def _paques(annee):
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    hh = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - hh - k) % 7
    m = (a + 11 * hh + 22 * l) // 451
    mois = (hh + l - 7 * m + 114) // 31
    jour = ((hh + l - 7 * m + 114) % 31) + 1
    return date(annee, mois, jour)


def jours_feries(annee):
    p = _paques(annee)
    return {date(annee, 1, 1), p + timedelta(days=1), date(annee, 5, 1), date(annee, 5, 8),
            p + timedelta(days=39), p + timedelta(days=50), date(annee, 7, 14), date(annee, 8, 15),
            date(annee, 11, 1), date(annee, 11, 11), date(annee, 12, 25)}


def position_cycle(jour_date):
    return jour_date.isocalendar()[1] % 4


def vacations_horizon(grille, date_debut, nb_jours, feries=True, garder_urgences=False):
    """Liste des vacations v de l'horizon (jour 0 = date_debut, un lundi).

    Renvoie un DataFrame, une ligne par vacation : jour (t), date, salle, chir,
    debut, fin, capacite (H_v en minutes), type ('chirurgie' / 'urgences' / 'libre')."""
    date_debut = pd.Timestamp(date_debut).date()
    fer = set()
    if feries:
        for a in range(date_debut.year, date_debut.year + 2 + nb_jours // 365):
            fer |= jours_feries(a)
    lignes = []
    for t in range(nb_jours):
        jour = date_debut + timedelta(days=t)
        if jour.weekday() >= 5 or jour in fer:
            continue
        k = position_cycle(jour)
        for v in grille:
            if v["jour"] == jour.weekday() and k in v["semaines"]:
                type_v = {"URGENCES": "urgences", "LIBRE": "libre"}.get(v["chir"], "chirurgie")
                if type_v != "chirurgie" and not garder_urgences:
                    continue
                lignes.append({"jour": t, "date": pd.Timestamp(jour), "salle": v["salle"], "chir": v["chir"],
                               "debut": v["debut"], "fin": v["fin"], "capacite": v["fin"] - v["debut"],
                               "type": type_v})
    return pd.DataFrame(lignes).sort_values(["jour", "salle", "debut"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Nouvelle grille à partir de la demande (méthode de la section 8)
# ---------------------------------------------------------------------------
def demande_par_chirurgien(df, rotation, debut="2022-01-01", fin="2022-12-31"):
    """Étape 1 : heures de salle par semaine d'activité, interventions programmées
    seulement (les urgences vont dans la réserve), hors printemps 2020.
    Renvoie aussi le profil de chaque chirurgien (jours, nuits, ambulatoire)."""
    x = df[(df["date_inter"] >= debut) & (df["date_inter"] <= fin) & (df["covid"] == 0)
           & (df["urgence"] == 0) & (df["jour_semaine"] < 5)].copy()
    med = df.groupby("type_interv")["duree_op"].median()
    x["duree"] = x["duree_op"].fillna(x["type_interv"].map(med)).fillna(df["duree_op"].median())
    x["semaine"] = x["date_inter"].dt.to_period("W")
    lignes = []
    for chir, g in x.groupby("praticien"):
        semaines = g["semaine"].nunique()
        minutes = (g["duree"] + rotation).sum()
        jours = g.groupby("jour_semaine").size().reindex(range(5), fill_value=0)
        lignes.append({
            "chir": chir, "nom": g["nom_chir"].mode().iat[0], "n_interventions": len(g),
            "semaines_actives": semaines,
            "demande_h_sem": minutes / 60 / semaines,
            "patients_sem": len(g) / semaines,
            "duree_moyenne": g["duree"].mean(),
            "part_ambu": g["ambu"].mean(),
            "nuits_moyennes": g["nuits"].mean(),
            "jours": (jours / jours.sum()).round(3).tolist(),
        })
    return pd.DataFrame(lignes).set_index("chir")


def construire_grille(df, rotation, reserve, marge=0.10, periode=("2022-01-01", "2022-12-31"),
                      salles=SALLES, min_patients_sem=0.5, poids_lits=1.0,
                      bonus_journee=1.0, places_ambu=16, seuil_places=0.8, poids_ambu=1.0, poids_equilibre=0.5,
                      min_interventions=5):
    """Nouvelle grille hebdomadaire (cycle de 2 semaines paire / impaire, noté (0, 2) / (1, 3)).

    Étapes :
      2. offre actuelle par chirurgien (grille actuelle) ;
      3. besoin (h/sem) = demande moyenne x (1 + marge) (choix de l'utilisateur, 2026-10-08 :
         plus de « demande en période chargée ») ; converti en demi-journées sur 2 semaines
         (matin 5 h, après-midi 4 h) ;
      4. placement glouton dans les cases jour x salle x demi-journée x parité :
         - un chirurgien dans une seule salle à la fois ;
         - ses jours de consultation sont respectés : on ne le place que les jours
           où il a opéré au moins 5 % de ses patients (sinon, jours libres en dernier recours) ;
         - spécialité / habitudes : bonus pour la salle et la case de la grille actuelle ;
         - lits : pénalité pour les chirurgiens à longs séjours le jeudi et le vendredi
           (leurs patients occupent des lits le week-end, quand il y a moins d'infirmiers) ;
         - places ambulatoires : chaque patient ambulatoire attendu au-delà de seuil_places
           (80 %) des places du jour (places_ambu moins les urgences ambulatoires moyennes)
           coûte poids_ambu ; sans ce terme, les trois chirurgiens les plus ambulatoires
           (CT, MT, MO) se retrouvaient tous le mardi : 17 à 18 ambulatoires pour 16 places ;
         - équilibre des semaines : poids_equilibre par demi-journée d'écart entre la semaine
           paire et la semaine impaire du chirurgien ; sans lui, le terme ambulatoire repoussait
           un chirurgien vers une seule semaine sur deux (vacations surchargées, F plus élevé) ;
      la réserve d'urgences (quantile 80 % par jour) est placée d'abord, l'après-midi,
      heure médiane des urgences observées.
    Chirurgiens retenus : au moins min_interventions interventions programmées sur la période et
    min_patients_sem patients par semaine d'activité. Les autres (nouveaux codes sans historique,
    activité marginale) sont retirés de la grille proposée (choix de l'utilisateur, 2026-10-09) :
    aucun patient ne leur est attribué, leurs vacations resteraient vides.
    Renvoie (grille, tableau demande / offre / proposition, explications)."""
    demande = demande_par_chirurgien(df, rotation, *periode)
    demande["besoin_h_sem"] = demande["demande_h_sem"] * (1 + marge)
    offre = minutes_par_semaine(GRILLE_ACTUELLE)
    # chirurgiens retenus : actifs sur la période
    actifs = (demande["patients_sem"] >= min_patients_sem) & (demande["n_interventions"] >= min_interventions)
    n_interv = demande["n_interventions"].to_dict()
    demande = demande[actifs]
    # cases libres : (jour, salle, demi-journée, parité) -> occupant
    cases = {(j, s, dj, p): None for j in range(5) for s in salles for dj in (0, 1) for p in (0, 1)}
    grille, explications = [], []

    # --- chirurgiens de la grille actuelle ou de la base écartés : leurs créneaux sont libérés
    retires = sorted(({v["chir"] for v in GRILLE_ACTUELLE} | set(n_interv)) - set(demande.index) - {"URGENCES", "LIBRE"})
    if retires:
        explications.append(f"Retirés de la grille proposée (moins de {min_interventions} interventions programmées sur la période : "
                            "nouveaux chirurgiens ou activité marginale) : "
                            + ", ".join(f"{c} ({n_interv.get(c, 0)} intervention{'s' if n_interv.get(c, 0) > 1 else ''})"
                                        for c in retires))

    # --- réserve d'urgences : placée d'abord, l'après-midi (heure médiane > 13 h)
    for _, r in reserve.iterrows():
        j = int(r["jour"])
        if j >= 5 or r["reserve_h"] <= 0:
            continue
        besoin = int(round(r["reserve_h"] * 60))
        # salle qui a déjà les urgences ce jour-là, sinon la moins chargée l'après-midi
        salle = _salle_urgence(j, salles, cases)
        dj = 0 if r["part_matin"] > 0.6 else 1
        debut, fin_max = (MATIN if dj == 0 else APRES_MIDI)
        fin = min(debut + besoin, fin_max)
        grille.append({"jour": j, "salle": salle, "chir": "URGENCES", "debut": debut, "fin": fin,
                       "semaines": TOUTES, "source": f"réserve quantile {100 * r['quantile']:.0f} %"})
        for p in (0, 1):
            cases[(j, salle, dj, p)] = "URGENCES"
        if fin - debut < besoin:  # reste de la réserve sur l'autre demi-journée
            autre = 1 - dj
            d2 = (APRES_MIDI if autre == 1 else MATIN)[0]
            grille.append({"jour": j, "salle": salle, "chir": "URGENCES", "debut": d2,
                           "fin": d2 + besoin - (fin - debut), "semaines": TOUTES, "source": f"réserve quantile {100 * r['quantile']:.0f} %"})
            for p in (0, 1):
                cases[(j, salle, autre, p)] = "URGENCES"
        explications.append(f"{JOURS[j]} : réserve d'urgences de {_fr(r['reserve_h'])} h en salle {salle} "
                            f"(quantile {100 * r['quantile']:.0f} % des heures d'urgence ; moyenne {_fr(r['moyenne_h'])} h)")

    # --- besoin de chaque chirurgien en demi-journées sur 2 semaines
    duree_dj = {0: (MATIN[1] - MATIN[0]) / 60, 1: (APRES_MIDI[1] - APRES_MIDI[0]) / 60}
    besoin = {}
    for chir, r in demande.iterrows():
        heures_2sem = 2 * r["besoin_h_sem"]
        besoin[chir] = max(1, int(round(heures_2sem / np.mean(list(duree_dj.values())))))
    # pénalité « lits le week-end » : nuits moyennes des patients hospitalisés
    nuits = demande["nuits_moyennes"]
    # pénalité « places ambulatoires » : patients ambulatoires attendus par heure de vacation de
    # chaque chirurgien, et charge ambulatoire déjà prévue chaque jour (semaine paire / impaire)
    ambu_h = (demande["patients_sem"] * demande["part_ambu"] / demande["demande_h_sem"]).to_dict()
    urg_places = {int(r["jour"]): float(r["places_moyennes"]) for _, r in reserve.iterrows()}
    seuil_ambu = {j: seuil_places * (places_ambu - urg_places.get(j, 0.0)) for j in range(5)}
    ambu_jour = {(j, p): 0.0 for j in range(5) for p in (0, 1)}
    par_semaine = {(c, p): 0 for c in demande.index for p in (0, 1)}   # demi-journées par semaine du cycle

    def ambu_dj(chir, dj):
        """Patients ambulatoires attendus sur une demi-journée de chir."""
        return ambu_h[chir] * duree_dj[dj]

    reste = dict(besoin)
    places = {c: 0 for c in besoin}
    def score_case(chir, j, s, dj, p, deja=0.0):
        """Intérêt de donner la demi-journée (j, s, dj, p) à chir ; None si interdit.
        deja : patients ambulatoires que l'on s'apprête à ajouter ce jour-là (l'autre demi-journée)."""
        if cases[(j, s, dj, p)] is not None:
            return None
        # un chirurgien dans une seule salle à la fois
        if any(cases.get((j, s2, dj, p)) == chir for s2 in salles if s2 != s):
            return None
        jours_pref = demande.loc[chir, "jours"]
        # jours de consultation respectés : pénalité forte les jours où il n'opère pas
        score = 3.0 * jours_pref[j] + (0 if jours_pref[j] >= 0.05 else -5.0)
        # (pas de bonus de stabilité par rapport à la grille actuelle : choix de l'utilisateur, 2026-10-08)
        # continuité : compléter une journée entière dans la même salle, pas dans une autre
        if cases.get((j, s, 1 - dj, p)) == chir:
            score += 1.0
        elif any(cases.get((j, s2, 1 - dj, p)) == chir for s2 in salles if s2 != s):
            score -= 3.0
        # alternance paire / impaire : même case l'autre semaine
        if cases.get((j, s, dj, 1 - p)) == chir:
            score += 0.6
        # séjours longs en fin de semaine -> lits occupés le week-end
        if j >= 3:
            score -= poids_lits * 0.15 * (j - 2) * nuits[chir] * (1 - demande.loc[chir, "part_ambu"])
        # équilibre des semaines paire / impaire : le chirurgien opère chaque semaine (sinon ses patients
        # d'une semaine sur deux s'entassent dans moins de vacations)
        score -= poids_equilibre * (par_semaine[(chir, p)] - par_semaine[(chir, 1 - p)])
        # places ambulatoires : patients attendus au-delà du seuil des places du jour
        a, deja_jour, sj = ambu_dj(chir, dj), ambu_jour[(j, p)] + deja, seuil_ambu[j]
        score -= poids_ambu * (max(0.0, deja_jour + a - sj) - max(0.0, deja_jour - sj))
        # préférence pour les salles déjà ouvertes (la salle 1 est fermée aujourd'hui)
        if s == 1:
            score -= 0.5
        return score

    # boucle gloutonne : on sert d'abord le chirurgien dont la couverture est la plus faible.
    # Quand il lui reste au moins 2 demi-journées, on préfère une journée entière dans une
    # même salle : la marge d'aléa z_β √Σσ² croît moins vite que Σµ, une journée de 9 h 30
    # absorbe mieux les longues interventions et l'aléa que deux demi-journées séparées.
    while True:
        candidats = [c for c in reste if reste[c] > 0]
        if not candidats:
            break
        chir = min(candidats, key=lambda c: (places[c] / besoin[c], -besoin[c]))
        meilleure, score_max = None, -1e9
        for (j, s, dj, p) in cases:
            sc = score_case(chir, j, s, dj, p)
            if sc is not None and sc > score_max:
                meilleure, score_max = [(j, s, dj, p)], sc
        if reste[chir] >= 2:
            for j in range(5):
                for s in salles:
                    for p in (0, 1):
                        a = score_case(chir, j, s, 0, p)
                        b = score_case(chir, j, s, 1, p, deja=ambu_dj(chir, 0)) if a is not None else None
                        if a is not None and b is not None and (a + b) / 2 + bonus_journee > score_max:
                            meilleure, score_max = [(j, s, 0, p), (j, s, 1, p)], (a + b) / 2 + bonus_journee
        if meilleure is None:
            explications.append(f"{chir} : plus de case disponible, {reste[chir]} demi-journée(s) non placée(s)")
            reste[chir] = 0
            continue
        for case in meilleure:
            cases[case] = chir
            ambu_jour[(case[0], case[3])] += ambu_dj(chir, case[2])
            par_semaine[(chir, case[3])] += 1
        places[chir] += len(meilleure)
        reste[chir] -= len(meilleure)

    grille += _cases_vers_vacations(cases)
    explications.append("Places ambulatoires : patients ambulatoires attendus par jour (semaine paire / impaire) : "
                        + " ; ".join(f"{JOURS[j].lower()} {_fr(ambu_jour[(j, 0)])} / {_fr(ambu_jour[(j, 1)])}" for j in range(5))
                        + f" (pour {places_ambu} places)")
    # tableau demande / offre / proposition et explications par chirurgien
    propose = minutes_par_semaine(grille)
    lignes = []
    for chir, r in demande.iterrows():
        o, n = offre.get(chir, 0) / 60, propose.get(chir, 0) / 60
        lignes.append({"chir": chir, "nom": r["nom"], "demande_h_sem": round(r["demande_h_sem"], 2),
                       "besoin_h_sem": round(r["besoin_h_sem"], 2),
                       "offre_actuelle_h_sem": round(o, 2), "offre_proposee_h_sem": round(n, 2),
                       "taux_actuel": round(r["demande_h_sem"] / o, 2) if o else None,
                       "taux_propose": round(r["demande_h_sem"] / n, 2) if n else None,
                       "patients_sem": round(r["patients_sem"], 1), "part_ambu": round(r["part_ambu"], 2),
                       "nuits_moyennes": round(r["nuits_moyennes"], 2), "jours": r["jours"]})
        if o == 0:
            explications.append(f"{chir} : absent de l'ancienne grille, demande {_fr(r['demande_h_sem'])} h/sem "
                                f"→ {_fr(n)} h/sem proposées")
        elif abs(n - o) >= 1:
            sens = "déborde" if r["demande_h_sem"] > o else "n'utilise pas toute son offre"
            explications.append(f"{chir} : {sens} (demande {_fr(r['demande_h_sem'])} h/sem en moyenne pour {_fr(o)} h/sem "
                                f"offertes, soit {100 * r['demande_h_sem'] / o:.0f} %) → {_fr(n)} h/sem proposées")
    return grille, pd.DataFrame(lignes), explications


def _salle_urgence(jour, salles, cases):
    """Salle où poser la réserve : celle de la grille actuelle qui a déjà un créneau
    URGENCES ce jour-là, sinon, parmi les salles ouvertes aujourd'hui (2 à 5), celle
    dont l'après-midi est le moins chargé dans la grille actuelle."""
    libre = [s for s in salles if all(cases[(jour, s, 1, p)] is None for p in (0, 1))]
    for v in GRILLE_ACTUELLE:
        if v["jour"] == jour and v["chir"] == "URGENCES" and v["salle"] in libre:
            return v["salle"]
    ouvertes = [s for s in libre if s != 1] or libre or list(salles)
    charge = {s: sum(v["fin"] - max(v["debut"], APRES_MIDI[0]) for v in GRILLE_ACTUELLE
                     if v["jour"] == jour and v["salle"] == s and v["fin"] > APRES_MIDI[0]) for s in ouvertes}
    return min(charge, key=charge.get)


def _cases_vers_vacations(cases):
    """Regroupe les demi-journées en vacations : matin + après-midi d'un même
    chirurgien dans une même salle = une journée 8h-17h30 ; même case les deux
    semaines = toutes les semaines."""
    vac = []
    for j in range(5):
        for s in SALLES:
            for p in (0, 1):
                m, a = cases.get((j, s, 0, p)), cases.get((j, s, 1, p))
                m = None if (m or "#").startswith("#") else m     # cases figées : déjà dans la grille
                a = None if (a or "#").startswith("#") else a
                if m and m != "URGENCES" and m == a:
                    vac.append((j, s, m, MATIN[0], APRES_MIDI[1], p))
                else:
                    if m and m != "URGENCES":
                        vac.append((j, s, m, *MATIN, p))
                    if a and a != "URGENCES":
                        vac.append((j, s, a, *APRES_MIDI, p))
    # fusion des parités identiques
    res = {}
    for j, s, c, d, f, p in vac:
        res.setdefault((j, s, c, d, f), set()).add(p)
    return [{"jour": j, "salle": s, "chir": c, "debut": d, "fin": f,
             "semaines": TOUTES if len(ps) == 2 else (PAIRES if 0 in ps else IMPAIRES), "source": "proposée"}
            for (j, s, c, d, f), ps in sorted(res.items())]


def grille_en_json(grille):
    return [{**v, "semaines": list(v["semaines"]), "jour_nom": JOURS[v["jour"]]} for v in grille]
