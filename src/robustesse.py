"""Score de robustesse d'un planning, sur 100, calculé sur les indicateurs d'une simulation
(src/sma.py). Défini dans l'onglet « Simulation et aléas », section « Définir ce qu'est un
planning robuste » (choix du 2026-10-13).

Un planning est robuste s'il reste sûr, stable et tenable quand les imprévus arrivent, et si le planning
exécuté garde les qualités demandées par le médecin (lissage des lits et des places, remplissage des vacations) :

    R = 100 − Σ_k w_k × min(1, x_k / s_k)

- x_k : valeur du critère k pendant la simulation, ramenée à 100 patients du planning (patients
  programmés au départ + nouveaux patients du flux) ; pour les vacations libérées, part (en %)
  des vacations sans patient au départ qui ont été rouvertes ; pour la qualité du planning exécuté,
  sa valeur absolue (écart à la cible des lits et des places, en lits et en places ; points de
  remplissage des vacations utilisées sous OBJECTIF_REMPLISSAGE), patients programmés seulement ;
- s_k : seuil critique, valeur à laquelle le critère a perdu tous ses points ;
- w_k : poids du critère (somme = 100) ; quatre familles : sécurité des patients 43,7, parcours des
  patients 24,3, qualité du planning exécuté 17,3, organisation du bloc 14,7 (2026-10-19, choix de l'utilisateur :
  dates changées 8 -> 4, car le patient a accepté sa nouvelle date ; sans solution 10 -> 12 ; temps de bloc
  vide après une annulation 6 -> 8). Puis « sans solution » séparé en deux critères (même jour) : patients placés
  en conflit (11, fortement pénalisés) et patients à programmer après l'horizon (1, très peu : ils passent au
  planning suivant). Enfin, lits manquants 25 -> 35 (seuil inchangé : 3,5 points par lit manquant une nuit pour
  100 patients, sans plafonner en crise) ; les 75 autres points ramenés à 65 en gardant leurs proportions (×65/75,
  arrondis au dixième).

Chaque incident coûte donc w_k / s_k points, jusqu'au seuil. F n'entre pas dans le score : F juge le
planning prévu, le score le planning exécuté face aux imprévus, y compris le niveau atteint sur les
objectifs du médecin, mesuré en valeur absolue (on peut ainsi les comparer).
Calibrage sur le planning adopté de 600 patients sur 8 semaines (20 simulations par situation, sans
coordination puis avec les agents, 2026-10-19) : environ 87-88 en semaines ordinaires, 82-84 avec 10 lits
fermés, 56-67 en crise. Même un bon planning perd une dizaine de points sur la qualité du planning exécuté
(écart à la cible des lits d'environ 2 lits, remplissage des vacations utilisées d'environ 73 %).

Ce sont les valeurs par défaut. Dans l'onglet Simulation, on peut modifier les seuils s_k et les
points perdus par incident (w_k / s_k) ; le poids w_k = s_k × points par incident est recalculé et
envoyé avec chaque simulation (clé reglages_score des paramètres, src/aleas.parametres). Le total
des poids peut alors s'écarter de 100.
"""

# famille, clé, libellé, ce qui est compté, indicateurs de sma.resultats (additionnés), base de calcul
# (« patients » : pour 100 patients du planning ; « liberees » : en % des vacations libérées au départ ;
# « direct » : valeur de l'indicateur telle quelle, qualité du planning exécuté), mesure (seuil qui n'est pas
# compté pour 100 patients), poids w_k, seuil critique s_k, son ordre de grandeur pour 600 patients sur
# 8 semaines, unité d'un incident, échelle d'affichage des points par incident (100 : pour 100 minutes).
# Les explications de l'onglet Simulation (index.html, « Pourquoi ces poids ») citent ces valeurs.
# objectif de remplissage des vacations utilisées (critère « remplissage » : points sous cet objectif)
OBJECTIF_REMPLISSAGE = 0.85

CRITERES = [
    {"famille": "Sécurité des patients", "cle": "lits", "libelle": "Lits manquants",
     "compte": "lits × nuits au-delà des lits ouverts", "indicateurs": ["lits_nuits_manquants"], "base": "patients",
     "poids": 35, "seuil": 10, "repere": "≈ 1 lit manquant chaque nuit", "unite": "lit manquant une nuit"},
    {"famille": "Sécurité des patients", "cle": "places", "libelle": "Places ambulatoires manquantes",
     "compte": "places × jours au-delà des places, jours d'ouverture", "indicateurs": ["places_manquantes"],
     "base": "patients", "poids": 8.7, "seuil": 5, "repere": "≈ 4 places manquantes par semaine", "unite": "place manquante un jour"},
    {"famille": "Parcours des patients", "cle": "deprogrammes", "libelle": "Patients déprogrammés le jour même",
     "compte": "patients non opérés le jour prévu (la vacation aurait trop débordé)", "indicateurs": ["patients_reportes"],
     "base": "patients", "poids": 10.4, "seuil": 10, "repere": "10 % des patients", "unite": "patient"},
    {"famille": "Parcours des patients", "cle": "conflits", "libelle": "Patients placés en conflit",
     "compte": "placés faute de mieux dans une vacation qui ne garde plus sa marge de sécurité (avec les agents, aussi : "
               "sans lit ou place libre, ou toutes les autres dates refusées par le patient)",
     "indicateurs": ["conflits_non_resolus"], "base": "patients",
     "poids": 9.5, "seuil": 5, "repere": "5 % des patients", "unite": "patient"},
    {"famille": "Parcours des patients", "cle": "apres_horizon", "libelle": "Patients à programmer après l'horizon",
     "compte": "pas de date avant la fin de l'horizon du planning : ils passent au planning suivant (ils en auraient "
               "une si l'on allongeait l'horizon) ; très peu pénalisés",
     "indicateurs": ["patients_sans_creneau"], "base": "patients",
     "poids": 0.9, "seuil": 10, "repere": "10 % des patients", "unite": "patient"},
    {"famille": "Parcours des patients", "cle": "dates_changees", "libelle": "Patients dont la date change",
     "compte": "prévenus à l'avance, avancés ou retardés, et d'accord avec leur nouvelle date", "indicateurs": ["patients_dates_changees"],
     "base": "patients", "poids": 3.5, "seuil": 20, "repere": "20 % des patients", "unite": "patient"},
    # objectifs du médecin (termes W1, W1′ et W2 de F) atteints dans le planning exécuté, en valeur absolue, patients
    # programmés seulement (src/sma.py, ModeleHopital.qualite)
    {"famille": "Qualité du planning exécuté", "cle": "lissage_lits", "libelle": "Lissage des lits",
     "compte": "écart à la cible des lits occupés dans le planning exécuté (écart quadratique moyen, toutes les nuits)",
     "indicateurs": ["ecart_cible_lits_execute"], "base": "direct", "mesure": "en lits d'écart à la cible",
     "poids": 6.9, "seuil": 4, "repere": "≈ 2 fois l'écart d'une semaine ordinaire", "unite": "lit d'écart"},
    {"famille": "Qualité du planning exécuté", "cle": "lissage_places", "libelle": "Lissage des places ambulatoires",
     "compte": "écart à la cible des places occupées dans le planning exécuté, jours d'ouverture",
     "indicateurs": ["ecart_cible_places_execute"], "base": "direct", "mesure": "en places d'écart à la cible",
     "poids": 2.6, "seuil": 4, "repere": "≈ 2 fois l'écart d'une semaine ordinaire", "unite": "place d'écart"},
    {"famille": "Qualité du planning exécuté", "cle": "remplissage", "libelle": "Remplissage des vacations",
     "compte": "points de remplissage des vacations utilisées sous 85 % dans le planning exécuté (durées réelles)",
     "indicateurs": ["manque_remplissage"], "base": "direct", "mesure": "en points sous 85 %",
     "poids": 7.8, "seuil": 20, "repere": "vacations remplies à 65 %", "unite": "point de remplissage"},
    {"famille": "Organisation du bloc", "cle": "retards", "libelle": "Vacations qui finissent en retard",
     "compte": "vacations dont la dernière opération dépasse l'heure de fin", "indicateurs": ["vacations_debordantes"],
     "base": "patients", "poids": 5.2, "seuil": 10, "repere": "≈ 1 vacation utilisée sur 2", "unite": "vacation"},
    {"famille": "Organisation du bloc", "cle": "temps_vide", "libelle": "Temps de bloc vide après une annulation",
     "compte": "minutes laissées vides par une annulation", "indicateurs": ["minutes_vides_annulation"], "base": "patients",
     "poids": 6.9, "seuil": 1000, "repere": "≈ 10 % du temps de bloc", "unite": "minute", "echelle_prix": 100},
    {"famille": "Organisation du bloc", "cle": "liberees", "libelle": "Vacations libérées rouvertes",
     "compte": "le praticien libéré doit finalement revenir", "indicateurs": ["liberees_rouvertes"], "base": "liberees",
     "mesure": "en % des vacations libérées au départ",
     "poids": 2.6, "seuil": 25, "repere": "1 vacation libérée sur 4", "unite": "% des vacations libérées"},
]
assert abs(sum(c["poids"] for c in CRITERES) - 100) < 1e-9

# niveaux d'interprétation : score minimal, libellé
NIVEAUX = [(80, "robuste"), (65, "acceptable"), (50, "fragile"), (0, "critique")]


def reglages_valides(reglages):
    """Réglages modifiés dans l'onglet Simulation (case « Modifier les seuils… ») :
    {clé: {"seuil": s_k > 0, "poids": w_k >= 0}} ; les clés inconnues et les valeurs invalides sont
    ignorées (le critère garde alors sa valeur par défaut). None s'il ne reste rien."""
    res = {}
    for c in CRITERES:
        r = (reglages or {}).get(c["cle"])
        if not isinstance(r, dict):
            continue
        try:
            seuil, poids = float(r.get("seuil", c["seuil"])), float(r.get("poids", c["poids"]))
        except (TypeError, ValueError):
            continue
        if seuil > 0 and poids >= 0:
            res[c["cle"]] = {"seuil": seuil, "poids": poids}
    return res or None


def score_robustesse(I, n_patients, reglages=None):
    """Score de robustesse (sur 100) et points perdus par critère (perte_<clé>), à partir des
    indicateurs I d'une simulation ; n_patients : patients du planning (départ + flux).
    reglages : seuils et poids modifiés (voir reglages_valides) ; None = valeurs par défaut."""
    reglages = reglages_valides(reglages) or {}
    res = {}
    for c in CRITERES:
        r = reglages.get(c["cle"], c)
        x = float(sum(I[k] for k in c["indicateurs"]))
        if c["base"] == "liberees":
            x = 100 * x / I["liberees_depart"] if I["liberees_depart"] else 0.0
        elif c["base"] == "patients":
            x = 100 * x / n_patients if n_patients else 0.0
        # base « direct » : valeur absolue de la qualité du planning exécuté (lits ou places d'écart, points de remplissage)
        res["perte_" + c["cle"]] = round(r["poids"] * min(1.0, x / r["seuil"]), 2)
    res["score_robustesse"] = round(100 - sum(res.values()), 1)
    return res


def niveau(score):
    """Libellé du niveau de robustesse d'un score."""
    return next(lib for seuil, lib in NIVEAUX if score >= seuil)
