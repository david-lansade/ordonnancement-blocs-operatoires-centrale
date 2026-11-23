"""Optimisation bayésienne sous contraintes des poids de F (onglet Technique, fenêtre « Comment ont été
définis les poids par défaut de F »).

Question : quels poids W₁′, W₂, W₅, W₆ donnent le planning le plus robuste (score R le plus haut) parmi ceux
qui respectent toutes les cibles de CIBLES ? W₁ = 1 fixe l'échelle (multiplier tous les poids par une même
constante ne change pas le planning : la température du recuit est calibrée sur les dégradations, le tabou
compare des valeurs). W₃ = 30, W₄ = 100 et ε restent fixes : leurs termes jouent le rôle de contraintes,
vérifiées par les cibles de sécurité.

Une évaluation (un jeu de poids) :
  1. instance : la liste des patients initiaux de l'application, la grille enregistrée, les poids essayés ;
  2. planning prévu : Recuit → Tabou, 3 graines × 200 000 évaluations, meilleure F (méthode du bouton
     « Calculer » du mode Médecin jusqu'au 2026-10-20) ; on y mesure les cibles « prévu » ;
  3. planning exécuté : simulation avec les agents (src/sma.py), aléas ordinaires et nouveaux patients en
     flux (3,2 par jour ouvré), 20 tirages, les MÊMES pour tous les jeux de poids ; on y mesure les cibles
     « exécuté » et le score de robustesse R.

Optimisation bayésienne (scikit-learn) :
  - variables : u ∈ [0, 1]⁴, log₁₀(W_k / W_k^réf) = 4 (u_k − 0,5), soit ×0,01 à ×100 autour du barème initial ;
  - départ : le barème initial + 20 jeux en hypercube latin (scipy.stats.qmc) ;
  - un processus gaussien par grandeur (R et une fonction g_k par cible, g_k ≤ 0 si la cible est
    respectée), noyau constante × Matérn 5/2 à une longueur par poids + bruit blanc, y normalisé ;
  - critère d'acquisition : EI(u) × Π_k P(g_k(u) ≤ 0), EI = amélioration espérée de R sur le meilleur jeu
    respectant les cibles (moyenne du modèle) ; tant qu'aucun jeu ne respecte tout : Π_k P(g_k ≤ 0) seul ;
  - lots de 4 jeux par tour (« kriging believer » : après chaque choix, le modèle fait comme si la mesure
    valait sa prédiction, ce qui écarte les jeux suivants du même endroit) ;
  - validation : les 3 meilleurs jeux respectant les cibles et le barème initial, avec 3 autres graines
    d'optimisation et 20 autres tirages (autres aléas, autres nouveaux patients) ; le meilleur, arrondi à 2 chiffres,
    est revalidé, ainsi que les poids par défaut en vigueur (src/scenario.py) : ceux-ci ne sont remplacés que si le jeu
    arrondi fait mieux qu'eux en validation en respectant toutes les cibles.
Résultats dans app/static/resultats/optimisation_poids/ : optimisation.json (lu par le site), essais.csv
(séparateur « ; », virgule décimale) et figures PNG. Aucun fichier de data/ n'est modifié.

Lancer depuis la racine :
    .venv\\Scripts\\python scripts\\optimisation_poids.py            (101 essais, environ 8 min sur 12 processus ; 201 essais : environ 17 min)
    .venv\\Scripts\\python scripts\\optimisation_poids.py --rapide   (essai en quelques minutes, sous-dossier essai)
    .venv\\Scripts\\python scripts\\optimisation_poids.py --figures  (redessine les figures)
    .venv\\Scripts\\python scripts\\optimisation_poids.py --revalider  (réévalue seulement les poids par défaut en vigueur)
    .venv\\Scripts\\python scripts\\optimisation_poids.py --prolonger 60  (60 essais de plus à partir du calcul enregistré)
    .venv\\Scripts\\python scripts\\optimisation_poids.py --essais 201  (201 jeux de poids en un seul passage, au lieu de 101)
"""
import argparse
import json
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm, qmc  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from src.aleas import ALEAS_ORDINAIRES, parametres, tirer_aleas  # noqa: E402
from src.comparaison import lancer  # noqa: E402
from src.flux import injecter, tirer_flux_tirages  # noqa: E402
from src.metaheuristiques import solution_aleatoire  # noqa: E402
from src.scenario import PARAMETRES_DEFAUT  # noqa: E402
from src.modele import details  # noqa: E402
from src.sma import simuler  # noqa: E402

SORTIE = RACINE / "app" / "static" / "resultats" / "optimisation_poids"
ALGO = "relais_recuit_tabou"
# barème initial (trouvé à la main, en testant plusieurs valeurs avec plusieurs simulations) : référence et centre de la recherche
REFERENCE = {"W1": 1.0, "W1b": 1.0, "W2": 0.05, "W3": 30.0, "W4": 100.0, "W5": 0.1, "W6": 1.0}
VARIABLES = ["W1b", "W2", "W5", "W6"]       # poids cherchés ; W1, W3, W4 et ε restent fixes
DECADES = 2                                  # ×10⁻² à ×10² autour du barème
FLUX_PAR_JOUR = 3.2
LIBELLES = {"W1": "lits", "W1b": "places", "W2": "regroupement", "W3": "retard", "W4": "capacités",
            "W5": "marge de salle", "W6": "date souhaitée"}
NOTATION = {"W1": "W₁", "W1b": "W₁′", "W2": "W₂", "W3": "W₃", "W4": "W₄", "W5": "W₅", "W6": "W₆"}


def _compte(x):
    """g d'une cible « 0 » sur un compte : négatif à 0, positif dès 1 (log pour écraser les grands comptes)."""
    return float(np.log1p(x) - np.log1p(0.5))


# cible : clé, libellé, mesure (prévu / exécuté), texte de la cible, indicateur, respectée ?, g (≤ 0 si respectée)
CIBLES = [
    # seuils choisis avec l'utilisateur (19/10/2026) : 0 vacation hors marge est impossible sur la liste de patients
    # (chirurgien MT : Σ µ + z_β √Σ σ² > Σ H) ; 15 et 2,2 étaient dans le bruit des mesures -> 18 et 2,5
    ("hors_marge", "Vacations hors marge", "prévu", "≤ 18", "vacations_depassees",
     lambda x: x <= 18, lambda x: x - 18.5),
    ("retard", "Patients opérés après leur limite médicale", "prévu", "0", "patients_en_retard",
     lambda x: x == 0, _compte),
    ("capacites", "Nuits ou jours au-delà de la capacité (lits, places)", "prévu", "0", "capacites_depassees",
     lambda x: x == 0, _compte),
    ("ecart_lits", "Écart-type des lits autour de la cible", "exécuté", "≤ 2,5 lits", "ecart_cible_lits_execute",
     lambda x: x <= 2.5, lambda x: x - 2.5),
    ("ecart_places", "Écart-type des places autour de la cible", "exécuté", "≤ 2,5 places", "ecart_cible_places_execute",
     lambda x: x <= 2.5, lambda x: x - 2.5),
    ("remplissage", "Remplissage des vacations utilisées", "exécuté", "≥ 75 %", "taux_occupation_bloc_utilisees",
     lambda x: x >= 0.75, lambda x: 0.75 - x),
    ("lits_manquants", "Lits manquants (lits × nuits, moyenne des tirages)", "exécuté", "0 dans tous les tirages",
     "lits_nuits_manquants", lambda x: x == 0, lambda x: float(np.log1p(x) - np.log1p(0.025))),
    ("places_manquantes", "Tirages sans aucune place manquante", "exécuté", "≥ 90 % des tirages",
     "part_sans_place_manquante", lambda x: x >= 0.9 - 1e-9, lambda x: 0.9 - x),
]
INDICATEURS_PREVU = ["vacations_depassees", "patients_en_retard", "nuits_au_dela_capacite", "jours_places_au_dela_capacite",
                     "ecart_type_lits", "ecart_type_places", "remplissage_vacations_utilisees", "vacations_vides",
                     "ecart_moyen_souhait"]
INDICATEURS_SIM = ["score_robustesse", "ecart_cible_lits_execute", "ecart_cible_places_execute",
                   "taux_occupation_bloc_utilisees", "lits_nuits_manquants", "places_manquantes", "patients_reportes",
                   "patients_sans_creneau", "patients_dates_changees", "vacations_debordantes"]
TEXTE, TEXTE_2, GRILLE = "#0b0b0b", "#52514e", "#e1e0d9"
BLEU, ORANGE, VERT, GRIS = "#2a78d6", "#eb6834", "#1baf7a", "#898781"


def poids_de(u):
    """Point u ∈ [0, 1]⁴ -> jeu de poids complet (W₁, W₃, W₄ fixes)."""
    w = dict(REFERENCE)
    for k, x in zip(VARIABLES, u):
        w[k] = float(REFERENCE[k] * 10 ** (2 * DECADES * (x - 0.5)))
    return w


def point_de(w):
    return np.array([np.log10(w[k] / REFERENCE[k]) / (2 * DECADES) + 0.5 for k in VARIABLES])


# --------------------------------------------------------------------------- tâches (processus fils)
def _optimiser(tache):
    cle, graine, d, budget = tache
    r = lancer(ALGO, d, budget, graine, solution_aleatoire(d, graine), {})
    return cle, graine, float(r["F"]), [int(v) for v in r["solution"]]


def _simuler(tache):
    cle, r, d, solution, hist, p, graine, flux_r, rotation = tache
    aleas = tirer_aleas(d, hist, p, graine)
    d_r, sol_r = d, solution
    if flux_r is not None:
        d_r, sol_r, _, aleas = injecter(d, solution, [str(i) for i in range(len(solution))], aleas, flux_r, p, graine)
    I = simuler(d_r, sol_r, aleas, "sma", p, rotation)["indicateurs"]
    return cle, r, {k: float(I[k]) for k in INDICATEURS_SIM if k in I}


# --------------------------------------------------------------------------- évaluation d'un lot de jeux de poids
class Evaluateur:
    def __init__(self, pool, budget, graines, nb_tirages, decalage_tirages=0, rappel=print):
        from src import scenario as S
        from src.contexte import Contexte
        self.pool, self.budget, self.graines, self.nb = pool, budget, list(graines), nb_tirages
        self.rappel = rappel
        ctx = Contexte()
        self.params = ctx.params
        grille = ctx.grille(self.params["grille"])
        self.d0, _, _ = S.creer_instance_liste(ctx.liste.patients, ctx.pred_complet, grille, self.params, ctx.reserve,
                                               ctx.rotation)
        self.rotation = ctx.rotation
        self.hist = ctx.historique_urgences()
        self.p = parametres(ALEAS_ORDINAIRES)
        self.graine0 = self.p["graine"] + decalage_tirages
        # nouveaux patients : un flux par tirage, le même pour tous les jeux de poids (indépendant des poids)
        self.flux = tirer_flux_tirages(ctx.generateur, ctx.pred_complet, self.params, self.d0, FLUX_PAR_JOUR,
                                       self.graine0, nb_tirages)
        self.ajoutes = round(float(np.mean([f["resume"]["ajoutes"] for f in self.flux])), 1)

    def instance(self, w):
        d = dict(self.d0)
        d["poids"] = dict(w)   # seule clé de d qui dépend des poids
        return d

    def evaluer(self, jeux):
        """jeux : {cle: poids} -> {cle: mesures}."""
        inst = {c: self.instance(w) for c, w in jeux.items()}
        taches = [(c, g, inst[c], self.budget) for c in jeux for g in self.graines]
        meilleurs, F_graines = {}, {}
        for c, g, F, sol in self.pool.map(_optimiser, taches):
            F_graines.setdefault(c, []).append(round(F, 1))
            if c not in meilleurs or F < meilleurs[c][0]:
                meilleurs[c] = (F, sol)
        taches = [(c, r, inst[c], meilleurs[c][1], self.hist, self.p, self.graine0 + r, self.flux[r], self.rotation)
                  for c in jeux for r in range(self.nb)]
        tirages = {}
        for c, r, I in self.pool.map(_simuler, taches):
            tirages.setdefault(c, []).append(I)
        res = {}
        for c, w in jeux.items():
            F, sol = meilleurs[c]
            det = details(sol, inst[c])
            prevu = {k: det["indicateurs"].get(k) for k in INDICATEURS_PREVU}
            prevu["capacites_depassees"] = prevu["nuits_au_dela_capacite"] + prevu["jours_places_au_dela_capacite"]
            liste = tirages[c]
            execute = {k: round(float(np.mean([x[k] for x in liste])), 3) for k in liste[0]}
            execute["part_sans_place_manquante"] = round(float(np.mean([x["places_manquantes"] == 0 for x in liste])), 3)
            res[c] = {"poids": w, "F": round(F, 1), "F_graines": sorted(F_graines[c]),
                      "termes": {k: round(v, 1) for k, v in det["termes"].items()},
                      "prevu": prevu, "execute": execute, "R_tirages": [round(x["score_robustesse"], 2) for x in liste]}
            res[c].update(bilan(res[c]))
        return res


def valeur(m, indicateur):
    return m["prevu"][indicateur] if indicateur in m["prevu"] else m["execute"][indicateur]


def bilan(m):
    respecte = {c[0]: bool(c[5](valeur(m, c[4]))) for c in CIBLES}
    return {"R": round(m["execute"]["score_robustesse"], 2), "respecte": respecte, "faisable": all(respecte.values()),
            "g": {c[0]: round(c[6](valeur(m, c[4])), 4) for c in CIBLES}}


def mesure_validation(ev_v, cle, nom, w, ref_v):
    """Un jeu de poids mesuré avec le protocole de validation ; écart de R avec le barème initial, tirage par tirage."""
    m = ev_v.evaluer({cle: w})[cle]
    diff = np.array(m["R_tirages"]) - np.array(ref_v["R_tirages"])
    return {"cle": cle, "nom": nom, **m, "ecart_R": round(float(diff.mean()), 2),
            "erreur_type": round(float(diff.std(ddof=1) / np.sqrt(len(diff))), 2)}


def valider_en_vigueur(ev, ev_v, ref_v, arrondi):
    """Poids par défaut en vigueur (src/scenario.py) mesurés avec le protocole de validation, et sur les tirages de
    la recherche (pour les placer sur les figures des essais). Ils ne sont remplacés que si les poids retenus arrondis
    font mieux qu'eux en validation en respectant toutes les cibles."""
    w = {k: float(v) for k, v in PARAMETRES_DEFAUT["poids"].items()}
    if arrondi and all(abs(arrondi["poids"][k] - w[k]) < 1e-12 for k in w):
        en_vigueur = {**arrondi, "cle": "en_vigueur", "nom": "poids par défaut"}
    else:
        en_vigueur = mesure_validation(ev_v, "en_vigueur", "poids par défaut", w, ref_v)
    m = ev.evaluer({"en_vigueur": w})["en_vigueur"]
    en_vigueur["recherche"] = {"R": m["R"], "faisable": m["faisable"], "respecte": m["respecte"]}
    ok = [x for x in (en_vigueur, arrondi) if x and x["faisable"]]
    choix = max(ok, key=lambda x: x["R"]) if ok else None   # à R égal, les poids en vigueur restent
    source = None if choix is None else "en_vigueur" if choix is en_vigueur else "arrondi"
    return en_vigueur, {"source": source, "poids": choix["poids"] if choix else w}


def poids_par_defaut(r):
    """Poids par défaut issus du calcul : en vigueur gardés, ou retenus arrondis (anciens fichiers : retenus)."""
    if r.get("defaut"):
        return r["defaut"]["poids"]
    return (r.get("retenu_arrondi") or r.get("retenu") or {}).get("poids")


# --------------------------------------------------------------------------- modèles et acquisition
def _gp(X, y):
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
    noyau = (ConstantKernel(1.0, (1e-3, 1e3)) * Matern(length_scale=[0.3] * X.shape[1], length_scale_bounds=(0.02, 20), nu=2.5)
             + WhiteKernel(1e-2, (1e-6, 1.0)))
    gp = GaussianProcessRegressor(kernel=noyau, normalize_y=True, n_restarts_optimizer=3, random_state=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gp.fit(X, y)
    return gp


def _fige(gp, X, y):
    """Même noyau (hyperparamètres gardés), données complétées : pour les choix fictifs du lot."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    g = GaussianProcessRegressor(kernel=gp.kernel_, normalize_y=True, optimizer=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g.fit(X, y)
    return g


def modeles(essais):
    X = np.array([e["u"] for e in essais])
    Y = {"R": np.array([e["R"] for e in essais], dtype=float)}
    for c in CIBLES:
        Y[c[0]] = np.array([e["g"][c[0]] for e in essais], dtype=float)
    return {k: _gp(X, y) for k, y in Y.items()}, X, Y


def predire(M, C):
    mu, sd = M["R"].predict(C, return_std=True)
    pf, pk = np.ones(len(C)), {}
    for c in CIBLES:
        m, s = M[c[0]].predict(C, return_std=True)
        pk[c[0]] = norm.cdf(-m / np.maximum(s, 1e-9))
        pf *= pk[c[0]]
    return mu, sd, pf, pk


def meilleur_faisable(M, essais):
    """Meilleur R du modèle parmi les jeux mesurés qui respectent les cibles (moins sensible au hasard d'un tirage)."""
    ok = [e for e in essais if e["faisable"]]
    if not ok:
        return None
    return float(np.max(M["R"].predict(np.array([e["u"] for e in ok]))))


def acquisition(M, C, meilleur):
    mu, sd, pf, _ = predire(M, C)
    if meilleur is None:
        return pf
    sd = np.maximum(sd, 1e-9)
    z = (mu - meilleur) / sd
    ei = (mu - meilleur) * norm.cdf(z) + sd * norm.pdf(z)
    return ei * pf


def candidats(essais, tour, n_sobol=4096):
    C = [qmc.Sobol(len(VARIABLES), scramble=True, seed=1000 + tour).random(n_sobol)]
    rng = np.random.default_rng(tour)
    # autour des meilleurs jeux (faisables d'abord, sinon les plus proches de l'être)
    tri = sorted(essais, key=lambda e: (not e["faisable"], -e["R"] if e["faisable"] else sum(max(0, v) for v in e["g"].values())))
    for e in tri[:4]:
        C.append(np.clip(np.asarray(e["u"]) + rng.normal(0, 0.04, (512, len(VARIABLES))), 0, 1))
    return np.vstack(C)


def proposer_lot(essais, tour, q):
    M, X, yf = modeles(essais)
    meilleur = meilleur_faisable(M, essais)
    C = candidats(essais, tour)
    Xf = X.copy()
    lot, Mf = [], M
    for _ in range(q):
        a = acquisition(Mf, C, meilleur)
        i = int(np.argmax(a))
        u = C[i]
        lot.append([float(x) for x in u])
        C = np.delete(C, i, axis=0)
        # kriging believer : la mesure fictive vaut la prédiction de chaque modèle
        Xf = np.vstack([Xf, u])
        Mf2 = {}
        for k, gp in Mf.items():
            yf[k] = np.append(yf[k], float(gp.predict(u[None, :])[0]))
            Mf2[k] = _fige(M[k], Xf, yf[k])
        Mf = Mf2
    return lot, meilleur


# --------------------------------------------------------------------------- déroulement
def calculer(budget, graines, nb_tirages, nb_depart, nb_total, q, graines_validation, nb_valides, processus, reprise=None):
    """reprise : essais d'un calcul précédent (mêmes réglages, mêmes tirages), que l'on prolonge jusqu'à nb_total."""
    t0 = time.time()

    def dire(m):
        print(f"[{(time.time() - t0) / 60:5.1f} min] {m}", flush=True)

    essais = []

    def ajouter(res, phase, tour, us):
        for (c, m), u in zip(res.items(), us):
            m.update(n=len(essais) + 1, cle=c, phase=phase, tour=tour, u=[round(float(x), 5) for x in u])
            essais.append(m)
            dire(f"essai {m['n']:3d} ({phase}) : " + ", ".join(f"{NOTATION[k]} {m['poids'][k]:.3g}" for k in VARIABLES) +
                 f" | R {m['R']:.1f} | {'respecte tout' if m['faisable'] else 'manque : ' + ', '.join(k for k, v in m['respecte'].items() if not v)}")

    with ProcessPoolExecutor(max_workers=processus) as pool:
        ev = Evaluateur(pool, budget, graines, nb_tirages, rappel=dire)
        P = ev.params
        dire(f"{len(ev.d0['duree'])} patients, grille {P['grille']}, flux {FLUX_PAR_JOUR}/jour ({ev.ajoutes} nouveaux patients par tirage)")
        if reprise:
            # mesures reprises telles quelles ; respect des cibles recalculé avec les seuils actuels
            essais.extend({**{k: v for k, v in e.items() if k != "R_modele"}, **bilan(e)} for e in reprise)
            dire(f"reprise de {len(essais)} essais")
        else:
            # 1. départ : barème initial + hypercube latin
            u_dep = [point_de(REFERENCE)] + list(qmc.LatinHypercube(len(VARIABLES), seed=0).random(nb_depart))
            res = ev.evaluer({f"e{k}": poids_de(u) for k, u in enumerate(u_dep)})
            ajouter({"reference": res.pop("e0"), **res}, "départ", 0, u_dep)
            essais[0]["phase"] = "référence"
        # 2. tours bayésiens
        tour = max(e["tour"] for e in essais)
        while len(essais) < nb_total:
            tour += 1
            n = min(q, nb_total - len(essais))
            lot, meilleur = proposer_lot(essais, tour, n)
            dire(f"tour {tour} : meilleur R respectant les cibles (modèle) {meilleur if meilleur is None else round(meilleur, 2)}")
            res = ev.evaluer({f"t{tour}_{k}": poids_de(u) for k, u in enumerate(lot)})
            ajouter(res, "bayésien", tour, lot)
        # 3. modèles finaux : R prédit aux jeux mesurés, longueurs de corrélation (influence de chaque poids)
        M, X, _ = modeles(essais)
        R_mod = M["R"].predict(X)
        for e, r in zip(essais, R_mod):
            e["R_modele"] = round(float(r), 2)
        longueurs = {}
        for k, gp in M.items():
            ls = gp.kernel_.k1.k2.length_scale
            longueurs[k] = {v: round(float(x), 3) for v, x in zip(VARIABLES, np.atleast_1d(ls))}
        # 4. validation : autres graines d'optimisation, autres tirages et autres nouveaux patients
        faisables = sorted([e for e in essais if e["faisable"] and e["phase"] != "référence"], key=lambda e: -e["R_modele"])
        choisis = faisables[:nb_valides]
        dire(f"validation de {len(choisis)} jeux et du barème initial")
        ev_v = Evaluateur(pool, budget, graines_validation, nb_tirages, decalage_tirages=1000, rappel=dire)
        jeux_v = {"reference": REFERENCE, **{e["cle"]: e["poids"] for e in choisis}}
        res_v = ev_v.evaluer(jeux_v)
        ref_v = res_v["reference"]
        validation = []
        for c, m in res_v.items():
            diff = np.array(m["R_tirages"]) - np.array(ref_v["R_tirages"])
            et = float(diff.std(ddof=1) / np.sqrt(len(diff))) if c != "reference" and len(diff) > 1 else 0.0
            validation.append({"cle": c, "nom": "barème initial" if c == "reference" else f"essai {next(e['n'] for e in essais if e['cle'] == c)}",
                               **m, "ecart_R": round(float(diff.mean()), 2), "erreur_type": round(et, 2)})
        ok_v = [v for v in validation if v["cle"] != "reference" and v["faisable"]]
        retenu = max(ok_v, key=lambda v: v["R"]) if ok_v else None
        # poids enregistrés par défaut : arrondis à 2 chiffres significatifs, revalidés avec le même protocole
        arrondi = None
        if retenu:
            w = {k: float(f"{v:.2g}") for k, v in retenu["poids"].items()}
            arrondi = mesure_validation(ev_v, "arrondi", "poids retenus arrondis", w, ref_v)
            dire(f"poids arrondis {w} : R {arrondi['R']}, {'respectent' if arrondi['faisable'] else 'ne respectent pas'} toutes les cibles")
        en_vigueur, defaut = valider_en_vigueur(ev, ev_v, ref_v, arrondi)
        dire(f"poids par défaut en vigueur : R {en_vigueur['R']}, {'respectent' if en_vigueur['faisable'] else 'ne respectent pas'}"
             f" toutes les cibles -> poids par défaut : {defaut['source']}")
        # cible la plus souvent manquée (utile si rien ne respecte tout)
        manques = {c[0]: sum(not e["respecte"][c[0]] for e in essais) for c in CIBLES}
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "duree_min": round((time.time() - t0) / 60, 1),
        "protocole": {"methode": "Recuit → Tabou", "budget": budget, "graines": list(graines),
                      "graines_validation": list(graines_validation), "tirages": nb_tirages,
                      "flux_par_jour": FLUX_PAR_JOUR, "nouveaux_patients": ev.ajoutes, "grille": P["grille"],
                      "patients": len(ev.d0["duree"]), "semaines": int(P["instance_semaines"]),
                      "aleas": {k: ALEAS_ORDINAIRES[k] for k in ALEAS_ORDINAIRES}, "epsilon_salles": P.get("epsilon_salles"),
                      "variables": VARIABLES, "decades": DECADES, "nb_depart": nb_depart, "nb_total": nb_total, "lot": q,
                      "noyau": "constante × Matérn ν = 5/2 (une longueur par poids) + bruit blanc",
                      "acquisition": "EI × probabilité de respecter toutes les cibles"},
        "reference": REFERENCE, "libelles": LIBELLES, "notation": NOTATION,
        "cibles": [{"cle": c[0], "libelle": c[1], "mesure": c[2], "cible": c[3], "indicateur": c[4]} for c in CIBLES],
        "essais": essais, "validation": validation, "retenu": retenu, "retenu_arrondi": arrondi, "longueurs": longueurs,
        "en_vigueur": en_vigueur, "defaut": defaut,
        "manques": manques, "nb_faisables": sum(e["faisable"] for e in essais),
    }


# --------------------------------------------------------------------------- sorties
def ecrire_csv(r, chemin):
    lignes = []
    for e in r["essais"]:
        x = {"essai": e["n"], "phase": e["phase"], "tour": e["tour"], **{f"poids_{k}": e["poids"][k] for k in REFERENCE},
             "F": e["F"], "R": e["R"], "R_modele": e.get("R_modele"), "respecte_tout": e["faisable"],
             **{f"respecte_{k}": v for k, v in e["respecte"].items()},
             **{f"prevu_{k}": v for k, v in e["prevu"].items()}, **{f"execute_{k}": v for k, v in e["execute"].items()}}
        lignes.append(x)
    pd.DataFrame(lignes).to_csv(chemin, sep=";", decimal=",", index=False, encoding="utf-8-sig")


def _axes(ax):
    ax.grid(axis="y", color=GRILLE, linewidth=0.8)
    ax.set_axisbelow(True)
    for c in ("top", "right"):
        ax.spines[c].set_visible(False)
    for c in ("left", "bottom"):
        ax.spines[c].set_color("#c3c2b7")
    ax.tick_params(colors=TEXTE_2, labelsize=9)


def figures(r, dossier):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": TEXTE})
    E = r["essais"]
    fichiers = []
    # 1. convergence : R de chaque essai, et meilleur R parmi les essais qui respectent toutes les cibles
    fig, ax = plt.subplots(figsize=(8, 3.6))
    n = [e["n"] for e in E]
    ok = [e["faisable"] for e in E]
    ax.scatter([x for x, o in zip(n, ok) if not o], [e["R"] for e, o in zip(E, ok) if not o], s=22, color=GRIS,
               label="ne respecte pas toutes les cibles", zorder=2)
    ax.scatter([x for x, o in zip(n, ok) if o], [e["R"] for e, o in zip(E, ok) if o], s=30, color=BLEU,
               label="respecte toutes les cibles", zorder=3)
    best, courbe = None, []
    for e in E:
        if e["faisable"]:
            best = e["R"] if best is None else max(best, e["R"])
        courbe.append(best)
    ax.step(n, [np.nan if b is None else b for b in courbe], where="post", color=ORANGE, linewidth=2,
            label="meilleur R respectant les cibles")
    ax.axhline(E[0]["R"], color=TEXTE_2, linewidth=1, linestyle=(0, (3, 3)), label="barème initial")
    ax.axvline(r["protocole"]["nb_depart"] + 1.5, color=GRILLE, linewidth=1)
    ax.text(r["protocole"]["nb_depart"] + 2, ax.get_ylim()[0], " tours bayésiens →", color=TEXTE_2, fontsize=9, va="bottom")
    ax.set_xlabel("essai (jeu de poids évalué)", color=TEXTE_2)
    ax.set_ylabel("score de robustesse R", color=TEXTE_2)
    _axes(ax)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    fig.tight_layout()
    fichiers.append("convergence.png")
    fig.savefig(dossier / fichiers[-1], dpi=150)
    plt.close(fig)
    # 2. essais selon chaque poids (échelle log), couleur = respecte les cibles
    fig, axes = plt.subplots(1, len(VARIABLES), figsize=(3.0 * len(VARIABLES), 3.0), sharey=True)
    for ax, k in zip(axes, VARIABLES):
        for o, coul, lab in ((False, GRIS, "ne respecte pas tout"), (True, BLEU, "respecte tout")):
            xs = [e["poids"][k] for e in E if e["faisable"] == o]
            ax.scatter(xs, [e["R"] for e in E if e["faisable"] == o], s=18, color=coul, label=lab, zorder=2 + o)
        ax.axvline(REFERENCE[k], color=TEXTE_2, linewidth=1, linestyle=(0, (3, 3)))
        if poids_par_defaut(r):
            ax.axvline(poids_par_defaut(r)[k], color=ORANGE, linewidth=1.5)
        ax.set_xscale("log")
        ax.set_title(f"{NOTATION[k]} · {LIBELLES[k]}", fontsize=10, color=TEXTE)
        _axes(ax)
    axes[0].set_ylabel("score de robustesse R", color=TEXTE_2)
    axes[-1].legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fichiers.append("essais_par_poids.png")
    fig.savefig(dossier / fichiers[-1], dpi=150)
    plt.close(fig)
    return fichiers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapide", action="store_true", help="essai : petit budget, peu d'essais (sous-dossier essai)")
    ap.add_argument("--processus", type=int, default=12)
    ap.add_argument("--figures", action="store_true", help="redessine seulement les figures à partir de optimisation.json")
    ap.add_argument("--prolonger", type=int, default=0, help="ajoute N essais au calcul enregistré (mêmes réglages, mêmes tirages)")
    ap.add_argument("--essais", type=int, default=101, help="nombre total de jeux de poids évalués (barème initial et départ compris)")
    ap.add_argument("--revalider", action="store_true", help="réévalue seulement les poids par défaut en vigueur (calcul enregistré)")
    a = ap.parse_args()
    if a.figures:
        r = json.loads((SORTIE / "optimisation.json").read_text(encoding="utf-8"))
        r["fichiers"]["figures"] = figures(r, SORTIE)
        (SORTIE / "optimisation.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Figures redessinées :", ", ".join(r["fichiers"]["figures"]))
        return
    if a.revalider:
        r = json.loads((SORTIE / "optimisation.json").read_text(encoding="utf-8"))
        P = r["protocole"]
        ref_v = next(v for v in r["validation"] if v["cle"] == "reference")
        with ProcessPoolExecutor(max_workers=a.processus) as pool:
            ev = Evaluateur(pool, P["budget"], P["graines"], P["tirages"])
            ev_v = Evaluateur(pool, P["budget"], P["graines_validation"], P["tirages"], decalage_tirages=1000)
            r["en_vigueur"], r["defaut"] = valider_en_vigueur(ev, ev_v, ref_v, r.get("retenu_arrondi"))
        r["fichiers"]["figures"] = figures(r, SORTIE)
        (SORTIE / "optimisation.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        e = r["en_vigueur"]
        print(f"Poids en vigueur : R {e['R']} ({e['ecart_R']:+.2f} ± {2 * e['erreur_type']:.2f}), "
              f"{'respectent' if e['faisable'] else 'ne respectent pas'} toutes les cibles ; poids par défaut : {r['defaut']['source']}")
        return
    if a.rapide:
        r = calculer(5000, [0], 3, 5, 10, 2, [10], 1, a.processus)
    elif a.prolonger:
        avant = json.loads((SORTIE / "optimisation.json").read_text(encoding="utf-8"))
        r = calculer(200000, [0, 1, 2], 20, 20, len(avant["essais"]) + a.prolonger, 4, [10, 11, 12], 3, a.processus,
                     reprise=avant["essais"])
        r["duree_min"] = round(r["duree_min"] + avant["duree_min"], 1)
    else:
        r = calculer(200000, [0, 1, 2], 20, 20, a.essais, 4, [10, 11, 12], 3, a.processus)
    dossier = SORTIE / "essai" if a.rapide else SORTIE   # l'essai ne remplace pas les vrais résultats
    dossier.mkdir(parents=True, exist_ok=True)
    r["fichiers"] = {"csv": "essais.csv"}
    ecrire_csv(r, dossier / "essais.csv")
    r["fichiers"]["figures"] = figures(r, dossier)
    (dossier / "optimisation.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Terminé en {r['duree_min']} min : {dossier}", flush=True)


if __name__ == "__main__":
    main()
