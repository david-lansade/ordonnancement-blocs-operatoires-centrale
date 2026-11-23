"""Balayage des poids W_k de F (onglet Technique, carte « Recherche des poids de F »).

Chaque poids W_k est multiplié par 0,2, 0,5, 2 et 5, les autres gardant leur valeur actuelle :
  1. instance : la liste des patients initiaux de l'application, la grille du planning, les poids modifiés ;
  2. planning : Recuit → Tabou, 5 graines, 200 000 évaluations par graine, meilleure F (le bouton
     « Calculer » du mode Médecin utilisait aussi Recuit → Tabou jusqu'au 2026-10-20) ;
  3. planning prévu : valeur brute f_k de chaque terme (F = Σ W_k f_k) et F de référence, calculée avec
     les poids actuels (ce que le changement coûte au sens de F actuelle) ;
  4. simulation avec les agents (src/sma.py) face aux MÊMES tirages d'aléas pour tous les jeux de poids :
     20 tirages ordinaires et 20 tirages de crise ; score de robustesse R et ses indicateurs, dont l'écart à
     la cible des lits et des places en valeur absolue (prévu et exécuté). L'écart à la référence (poids
     actuels) est calculé tirage par tirage. Son incertitude u combine deux hasards : celui des aléas
     (erreur-type de l'écart apparié) et celui de l'optimisation (une autre graine donne un autre planning),
     mesuré en simulant les plannings des 5 graines avec les poids actuels : u² = e² + 2 s_plan².
     Un écart est significatif s'il dépasse 2u.
Résultats dans app/static/resultats/balayage_poids/ : balayage.json (lu par le site), balayage.csv
(séparateur « ; », virgule décimale) et figures PNG. Aucun fichier de data/ n'est modifié
(grille_nouvelle.json est seulement réécrit à l'identique au chargement du contexte).

Lancer depuis la racine :
    .venv\\Scripts\\python scripts\\balayage_poids.py            (environ 5 min sur 12 processus)
    .venv\\Scripts\\python scripts\\balayage_poids.py --rapide   (essai en quelques minutes)
"""
import argparse
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from src import scenario as S  # noqa: E402
from src.aleas import ALEAS_CRISE, ALEAS_ORDINAIRES, parametres, tirer_aleas  # noqa: E402
from src.comparaison import lancer  # noqa: E402
from src.metaheuristiques import solution_aleatoire  # noqa: E402
from src.modele import details  # noqa: E402
from src.parallele import executer  # noqa: E402
from src.sma import simuler  # noqa: E402

SORTIE = RACINE / "app" / "static" / "resultats" / "balayage_poids"
ALGO = "relais_recuit_tabou"
FACTEURS = [0.2, 0.5, 2.0, 5.0]
# clé du poids : terme de details()["termes"], libellé ; ordre fixe = ordre des couleurs
POIDS = {"W1": ("lits", "lits"), "W1b": ("places", "places ambulatoires"), "W2": ("salles", "regroupement"),
         "W3": ("retard", "retard"), "W4": ("capacite_hebergement", "capacités"),
         "W5": ("marge_salles", "marge de salle"), "W6": ("ecart_souhait", "date souhaitée")}
NOTATION = {"W1": "W₁", "W1b": "W₁′", "W2": "W₂", "W3": "W₃", "W4": "W₄", "W5": "W₅", "W6": "W₆"}
SITUATIONS = {"ordinaire": ("Aléas ordinaires", ALEAS_ORDINAIRES), "crise": ("Crise", ALEAS_CRISE)}
INDICATEURS_SIM = ["score_robustesse", "lits_nuits_manquants", "places_manquantes", "patients_reportes",
                   "patients_sans_creneau", "conflits_non_resolus", "patients_dates_changees", "vacations_debordantes",
                   "minutes_vides_annulation", "liberees_rouvertes", "hausse_ecart_lits", "hausse_ecart_places",
                   "baisse_remplissage", "F_apres", "ecart_cible_lits_prevu", "ecart_cible_lits_execute",
                   "ecart_cible_places_prevu", "ecart_cible_places_execute", "remplissage_prevu",
                   "taux_occupation_bloc_utilisees"]
INDICATEURS_PLANNING = ["ecart_type_lits", "lits_max", "ecart_type_places", "vacations_vides",
                        "remplissage_vacations_utilisees", "vacations_depassees", "patients_en_retard",
                        "nuits_au_dela_capacite", "ecart_moyen_souhait"]
# palette du site (app/static/style.css, --s1 ... --s7), dans l'ordre des poids
COULEURS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
COULEUR_SITUATION = {"ordinaire": "#2a78d6", "crise": "#eb6834"}
TEXTE, TEXTE_2, GRILLE = "#0b0b0b", "#52514e", "#e1e0d9"


def _optimiser(tache):
    """Un jeu de poids × une graine : Recuit → Tabou depuis une solution aléatoire (processus fils)."""
    cle, graine, d, budget = tache
    r = lancer(ALGO, d, budget, graine, solution_aleatoire(d, graine), {})
    return cle, graine, float(r["F"]), [int(v) for v in r["solution"]]


def _simuler(tache):
    """Un jeu de poids × une situation : indicateurs de chaque tirage (graines 0, 1, ...), avec les agents."""
    cle, situation, d, solution, hist, p, nb, rotation = tache
    res = []
    for r in range(nb):
        aleas = tirer_aleas(d, hist, p, p["graine"] + r)
        I = simuler(d, solution, aleas, "sma", p, rotation)["indicateurs"]
        res.append({**{k: float(I[k]) for k in INDICATEURS_SIM if k in I},
                    **{k: float(v) for k, v in I.items() if k.startswith("perte_")}})
    return cle, situation, res


def calculer(budget, graines, nb, facteurs, poids, processus):
    from src.contexte import Contexte
    t0 = time.time()
    ctx = Contexte()
    params = ctx.params
    grille = ctx.grille(params["grille"])
    w0 = {k: float(v) for k, v in params["poids"].items()}
    jeux = [{"cle": "actuels", "poids": None, "facteur": 1.0}] + [
        {"cle": f"{k}_x{f:g}", "poids": k, "facteur": f} for k in poids for f in facteurs]
    instances = {}
    for j in jeux:
        p = copy.deepcopy(params)
        if j["poids"]:
            p["poids"][j["poids"]] = w0[j["poids"]] * j["facteur"]
        j["w"] = {k: float(v) for k, v in p["poids"].items()}
        d, P, vac = S.creer_instance_liste(ctx.liste.patients, ctx.pred_complet, grille, p, ctx.reserve, ctx.rotation)
        instances[j["cle"]] = d
    d0 = instances["actuels"]
    print(f"{len(jeux)} jeux de poids × {len(graines)} graines (budget {budget}), puis {len(SITUATIONS)} situations × "
          f"{nb} tirages ; {len(d0['duree'])} patients", flush=True)

    def rappel(f, m):
        print(f"  {100 * f:5.1f} %  {m}  ({(time.time() - t0) / 60:.1f} min)", flush=True)

    # 1. un planning par jeu de poids : meilleure des graines
    res = executer(_optimiser, [(j["cle"], g, instances[j["cle"]], budget) for j in jeux for g in graines],
                   processus, rappel, libelle=lambda t: f"{t[0]}, graine {t[1]}")
    meilleurs, F_graines, graines_ref = {}, {}, {}
    for cle, g, F, sol in res:
        F_graines.setdefault(cle, []).append(round(F, 1))
        if cle == "actuels":
            graines_ref[g] = sol
        if cle not in meilleurs or F < meilleurs[cle][0]:
            meilleurs[cle] = (F, sol)

    # 2. planning prévu : termes bruts f_k (contribution / poids), F avec les poids actuels
    for j in jeux:
        d = instances[j["cle"]]
        F, sol = meilleurs[j["cle"]]
        det = details(sol, d)
        brut = {k: det["termes"][t] / d["poids"][k] if d["poids"][k] else 0.0 for k, (t, _) in POIDS.items()}
        j.update(F=round(F, 1), F_graines=sorted(F_graines[j["cle"]]),
                 termes_bruts={k: round(v, 3) for k, v in brut.items()},
                 F_reference=round(sum(w0[k] * brut[k] for k in POIDS), 1),
                 planning={k: det["indicateurs"].get(k) for k in INDICATEURS_PLANNING})

    # 3. simulation avec les agents, mêmes tirages pour tous les jeux de poids
    hist = ctx.historique_urgences()
    taches = [(j["cle"], s, instances[j["cle"]], meilleurs[j["cle"]][1], hist, parametres(al), nb, ctx.rotation)
              for j in jeux for s, (_, al) in SITUATIONS.items()]
    # bruit de l'optimisation : le planning de chaque graine, avec les poids actuels, face aux mêmes tirages
    taches += [(f"graine_{g}", s, d0, sol, hist, parametres(al), nb, ctx.rotation)
               for g, sol in sorted(graines_ref.items()) for s, (_, al) in SITUATIONS.items()]
    res = executer(_simuler, taches, processus, rappel, libelle=lambda t: f"{t[0]}, {t[1]}")
    tirages = {(cle, s): r for cle, s, r in res}
    bruit = {}
    for s in SITUATIONS:
        R_g = [float(np.mean([x["score_robustesse"] for x in tirages[(f"graine_{g}", s)]])) for g in sorted(graines_ref)]
        bruit[s] = {"R_graines": [round(x, 2) for x in R_g],
                    "ecart_type": round(float(np.std(R_g, ddof=1)), 2) if len(R_g) > 1 else 0.0}
    for j in jeux:
        j["simulation"] = {}
        for s in SITUATIONS:
            liste, ref = tirages[(j["cle"], s)], tirages[("actuels", s)]
            diff = np.array([x["score_robustesse"] - y["score_robustesse"] for x, y in zip(liste, ref)])
            et = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 else 0.0
            # la référence elle-même n'a pas d'écart ; les autres jeux : hasard des aléas + de l'optimisation
            u = float(np.sqrt(et ** 2 + 2 * bruit[s]["ecart_type"] ** 2)) if j["poids"] else 0.0
            j["simulation"][s] = {
                "moyennes": {k: round(float(np.mean([x[k] for x in liste])), 2) for k in liste[0]},
                "scores": [x["score_robustesse"] for x in liste],
                "ecart_R": round(float(diff.mean()), 2), "erreur_type": round(et, 2), "incertitude": round(u, 2),
                "significatif": bool(j["poids"] and abs(diff.mean()) > 2 * u)}

    # 4. influence de chaque poids : amplitude du score R sur ×0,2 ... ×5, meilleur facteur
    tous = sorted([1.0] + list(facteurs))
    influence = []
    for k in poids:
        serie = sorted([j for j in jeux if j["poids"] == k] + [jeux[0]], key=lambda j: j["facteur"])
        ligne = {"poids": k, "libelle": POIDS[k][1], "w_actuel": w0[k], "facteurs": tous,
                 "terme_propre": [j["termes_bruts"][k] for j in serie], "F_reference": [j["F_reference"] for j in serie]}
        for s in SITUATIONS:
            R = [j["simulation"][s]["moyennes"]["score_robustesse"] for j in serie]
            m = max(serie, key=lambda j: j["simulation"][s]["moyennes"]["score_robustesse"])
            ligne[s] = {"R": R, "amplitude": round(max(R) - min(R), 2), "meilleur_facteur": m["facteur"],
                        "gain": m["simulation"][s]["ecart_R"], "incertitude": m["simulation"][s]["incertitude"],
                        "significatif": m["simulation"][s]["significatif"] and m["simulation"][s]["ecart_R"] > 0}
        influence.append(ligne)
    influence.sort(key=lambda x: -(x["ordinaire"]["amplitude"] + x["crise"]["amplitude"]))
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "duree_min": round((time.time() - t0) / 60, 1),
        "protocole": {"methode": "Recuit → Tabou", "budget": budget, "graines": list(graines), "tirages": nb,
                      "facteurs": tous, "strategie": "agents (SMA)", "grille": params["grille"],
                      "patients": len(d0["duree"]), "semaines": int(params["instance_semaines"]),
                      "situations": {s: {"nom": nom, "aleas": al} for s, (nom, al) in SITUATIONS.items()}},
        "poids_actuels": w0, "bruit_planning": bruit,
        "libelles": {k: v[1] for k, v in POIDS.items()}, "notation": NOTATION,
        "jeux": jeux, "influence": influence,
    }


def ecrire_csv(r, chemin):
    lignes = []
    for j in r["jeux"]:
        x = {"jeu": j["cle"], "poids_modifie": j["poids"] or "", "facteur": j["facteur"],
             **{f"w_{k}": v for k, v in j["w"].items()}, "F": j["F"], "F_reference": j["F_reference"],
             "F_graines": " / ".join(f"{v:g}" for v in j["F_graines"]),
             **{f"terme_brut_{k}": v for k, v in j["termes_bruts"].items()},
             **{f"planning_{k}": v for k, v in j["planning"].items()}}
        for s, sim in j["simulation"].items():
            x.update({f"{s}_ecart_R": sim["ecart_R"], f"{s}_erreur_type": sim["erreur_type"],
                      f"{s}_incertitude": sim["incertitude"], f"{s}_significatif": sim["significatif"]})
            x.update({f"{s}_{k}": v for k, v in sim["moyennes"].items()})
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
    inf = {x["poids"]: x for x in r["influence"]}
    ordre = [k for k in POIDS if k in inf]
    tous = r["protocole"]["facteurs"]
    fichiers = []
    # 1. score de robustesse selon le facteur appliqué à chaque poids (petits multiples, axe commun)
    n = len(ordre)
    cols = min(4, n)
    lignes_fig = int(np.ceil(n / cols))
    fig, axes = plt.subplots(lignes_fig, cols, figsize=(3.2 * cols, 2.8 * lignes_fig), sharey=True, squeeze=False)
    valeurs = [v for k in ordre for s in SITUATIONS for v in inf[k][s]["R"]]
    bas, haut = min(valeurs) - 1, max(valeurs) + 1
    for i, k in enumerate(ordre):
        ax = axes[i // cols][i % cols]
        for s, (nom, _) in SITUATIONS.items():
            ax.plot(tous, inf[k][s]["R"], color=COULEUR_SITUATION[s], linewidth=2, marker="o", markersize=5, label=nom)
        ax.axvline(1, color="#898781", linewidth=1, linestyle=(0, (3, 3)))
        ax.set_xscale("log")
        ax.set_xticks(tous)
        ax.set_xticklabels([f"×{f:g}".replace(".", ",") for f in tous])
        ax.minorticks_off()
        ax.set_ylim(bas, haut)
        ax.set_title(f"{NOTATION[k]} · {POIDS[k][1]}", fontsize=10, color=TEXTE, loc="left")
        _axes(ax)
    for i in range(n, lignes_fig * cols):
        axes[i // cols][i % cols].axis("off")
    for ligne in axes:
        ligne[0].set_ylabel("Score R", color=TEXTE_2)
    poignees, noms = axes[0][0].get_legend_handles_labels()
    if n < lignes_fig * cols:   # légende dans la case libre
        axes[-1][-1].legend(poignees, noms, loc="center", frameon=False, fontsize=10,
                            title="ligne pointillée : poids actuels", title_fontsize=9)
    else:
        fig.legend(poignees, noms, loc="lower center", ncol=2, frameon=False, fontsize=10)
    fig.suptitle("Score de robustesse R selon le facteur appliqué à un poids", fontsize=11, x=0.01, ha="left",
                 color=TEXTE)
    fig.tight_layout(rect=(0, 0.0 if n < lignes_fig * cols else 0.06, 1, 0.95))
    fichiers.append("robustesse_par_poids.png")
    fig.savefig(dossier / fichiers[-1], dpi=150)
    plt.close(fig)

    # 2. influence : amplitude du score R entre ×0,2 et ×5, par poids et par situation
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(ordre) + 1.2))
    ordre_inf = [x["poids"] for x in r["influence"]][::-1]
    y = np.arange(len(ordre_inf))
    h = 0.36
    for dy, s in ((h / 2 + 0.02, "ordinaire"), (-h / 2 - 0.02, "crise")):
        ax.barh(y + dy, [inf[k][s]["amplitude"] for k in ordre_inf], height=h, color=COULEUR_SITUATION[s],
                label=SITUATIONS[s][0])
    ax.set_yticks(y)
    ax.set_yticklabels([f"{NOTATION[k]} · {POIDS[k][1]}" for k in ordre_inf])
    ax.set_xlabel("Écart entre le meilleur et le pire score R (points)", color=TEXTE_2)
    ax.grid(axis="x", color=GRILLE, linewidth=0.8)
    ax.set_axisbelow(True)
    for c in ("top", "right"):
        ax.spines[c].set_visible(False)
    ax.tick_params(colors=TEXTE_2, labelsize=9)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.set_title("Influence de chaque poids sur la robustesse", fontsize=11, loc="left", color=TEXTE)
    fig.tight_layout()
    fichiers.append("influence.png")
    fig.savefig(dossier / fichiers[-1], dpi=150)
    plt.close(fig)

    # 3. compromis : qualité du planning prévu (F avec les poids actuels) et robustesse en crise
    fig, ax = plt.subplots(figsize=(7, 4.6))
    for i, k in enumerate(POIDS):
        js = [j for j in r["jeux"] if j["poids"] == k]
        if js:
            ax.scatter([j["F_reference"] for j in js], [j["simulation"]["crise"]["moyennes"]["score_robustesse"] for j in js],
                       s=42, color=COULEURS[i], edgecolor="white", linewidth=1.5, label=f"{NOTATION[k]} · {POIDS[k][1]}",
                       zorder=3)
    j0 = r["jeux"][0]
    ax.scatter([j0["F_reference"]], [j0["simulation"]["crise"]["moyennes"]["score_robustesse"]], s=90, marker="D",
               color=TEXTE, edgecolor="white", linewidth=1.5, label="poids actuels", zorder=4)
    ax.set_xlabel("F du planning prévu, calculée avec les poids actuels (plus bas = meilleur)", color=TEXTE_2)
    ax.set_ylabel("Score de robustesse R en crise", color=TEXTE_2)
    _axes(ax)
    ax.grid(axis="x", color=GRILLE, linewidth=0.8)
    ax.legend(frameon=False, fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.set_title("Compromis entre qualité prévue et robustesse (un point par jeu de poids)", fontsize=11, loc="left",
                 color=TEXTE)
    fig.tight_layout()
    fichiers.append("compromis.png")
    fig.savefig(dossier / fichiers[-1], dpi=150)
    plt.close(fig)
    return fichiers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapide", action="store_true", help="essai : 2 poids, 1 facteur, petit budget")
    ap.add_argument("--processus", type=int, default=12)
    ap.add_argument("--figures", action="store_true", help="redessine seulement les figures à partir de balayage.json")
    a = ap.parse_args()
    if a.figures:
        r = json.loads((SORTIE / "balayage.json").read_text(encoding="utf-8"))
        r["fichiers"]["figures"] = figures(r, SORTIE)
        (SORTIE / "balayage.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Figures redessinées :", ", ".join(r["fichiers"]["figures"]))
        return
    if a.rapide:
        r = calculer(3000, [0], 2, [0.5], ["W1", "W5"], a.processus)
    else:
        r = calculer(200000, [0, 1, 2, 3, 4], 20, FACTEURS, list(POIDS), a.processus)
    dossier = SORTIE / "essai" if a.rapide else SORTIE   # l'essai ne remplace pas les vrais résultats
    dossier.mkdir(parents=True, exist_ok=True)
    r["fichiers"] = {"csv": "balayage.csv"}
    ecrire_csv(r, dossier / "balayage.csv")
    r["fichiers"]["figures"] = figures(r, dossier)
    (dossier / "balayage.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Terminé en {r['duree_min']} min : {dossier}", flush=True)


if __name__ == "__main__":
    main()
