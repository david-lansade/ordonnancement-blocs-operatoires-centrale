"""Comparaison reproductible des trois métaheuristiques (pour le compte rendu).

Même instance (même d), mêmes graines, même solution de départ, même budget
d'évaluations. Écrit dans figures/ : un CSV par grille (toutes les exécutions),
un tableau de synthèse et trois figures (convergence, dispersion de F, lits).

Lancer depuis la racine :
    .venv\\Scripts\\python scripts\\comparer_metaheuristiques.py --budget 50000 --graines 5
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from src import grille as G, scenario as S  # noqa: E402
from src.chargement import charger_sejours, temps_rotation  # noqa: E402
from src.comparaison import comparer  # noqa: E402
from src.prediction import Predicteur  # noqa: E402
from src.urgences import reserve_urgences  # noqa: E402

COULEURS = {"recuit": "#2a78d6", "tabou": "#eb6834", "genetique": "#1baf7a", "glouton": "#eda100"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=50000)
    ap.add_argument("--graines", type=int, default=5)
    ap.add_argument("--depart", default="aleatoire", choices=["aleatoire", "gloutonne"])
    a = ap.parse_args()

    sortie = RACINE / "figures"
    sortie.mkdir(exist_ok=True)
    df = charger_sejours()
    rot = temps_rotation(df)
    params = S.charger_parametres()
    reserve = reserve_urgences(df, rot, params["quantile_urgences"])
    pred = Predicteur.charger(df, "2022-01-01")
    nouvelle, _, _ = G.construire_grille(df, rot, reserve, marge=params["grille_marge"],
                                         periode=tuple(params["grille_periode"]), places_ambu=params["cap_ambu"])
    lignes = []
    for nom, gr in (("actuelle", G.GRILLE_ACTUELLE), ("nouvelle", nouvelle)):
        d, P, vac = S.creer_instance(df, pred, gr, params, reserve, rotation=rot)
        print(f"\n=== Grille {nom} : {len(d['duree'])} patients, {len(d['capacite'])} vacations, "
              f"{d['nb_jours']} jours, budget {a.budget}, {a.graines} graines")
        res = comparer(d, P, vac, budget=a.budget, graines=tuple(range(a.graines)), depart=a.depart, rotation=rot,
                       rappel=lambda f, m: print(f"  {100 * f:5.1f} %  {m}", end="\r"))
        pd.DataFrame(res["runs"]).to_csv(sortie / f"executions_{nom}.csv", index=False)
        for s in res["synthese"]:
            lignes.append({"grille": nom, "methode": s["nom"], "F_moyen": round(s["F_moyen"]),
                           "F_ecart_type": round(s["F_ecart_type"]), "F_min": round(s["F_min"]),
                           "temps_s": None if s["temps_moyen"] is None else round(s["temps_moyen"], 2),
                           "ecart_type_lits": s["ecart_type_lits"], "lits_max": s["lits_max"],
                           "ecart_type_places": s["ecart_type_places"], "vacations_hors_marge": s["vacations_depassees"],
                           "patients_en_retard": s["patients_en_retard"]})
        figures(res, nom, sortie)
    tab = pd.DataFrame(lignes)
    tab.to_csv(sortie / "synthese.csv", index=False)
    print("\n")
    print(tab.to_string(index=False))


def figures(res, nom, sortie):
    algos = list(res["courbes"])
    noms = {s["algo"]: s["nom"] for s in res["synthese"]}
    fig, ax = plt.subplots(figsize=(7, 4))
    for a in algos:
        x, y = zip(*res["courbes"][a])
        ax.plot(x, y, color=COULEURS[a], lw=2, label=noms[a])
    ax.set_yscale("log")
    ax.set_xlabel("évaluations de F")
    ax.set_ylabel("meilleure valeur de F")
    ax.set_title(f"Convergence (grille {nom}, meilleure graine)")
    ax.grid(alpha=.3)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(sortie / f"convergence_{nom}.png", dpi=150)
    plt.close(fig)

    runs = pd.DataFrame(res["runs"])
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.boxplot([runs[runs.algo == a]["F"] for a in algos], tick_labels=[noms[a] for a in algos])
    ax.set_ylabel("F")
    ax.set_title(f"Dispersion de F sur {runs.graine.nunique()} graines (grille {nom})")
    ax.grid(alpha=.3, axis="y")
    fig.tight_layout()
    fig.savefig(sortie / f"dispersion_{nom}.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 3.8))
    for a in algos + ["glouton"]:
        ax.plot(res["occupation"][a]["lits"], color=COULEURS[a], lw=1.8 if a != "glouton" else 1.2,
                ls="-" if a != "glouton" else ":", label=noms[a])
    ax.step(range(len(res["cap_lits"])), res["occupation"][res["meilleur"]["algo"]]["cible_lits"], where="mid",
            color="black", lw=1.2, ls="--", label="cible (personnel)")
    ax.step(range(len(res["cap_lits"])), res["cap_lits"], where="mid", color="#d03b3b", lw=1, ls=":", label="capacité")
    ax.set_xlabel("jour de l'horizon (0 = lundi d'ouverture)")
    ax.set_ylabel("lits occupés")
    ax.set_title(f"Lits occupés chaque nuit (grille {nom})")
    ax.grid(alpha=.3)
    ax.legend(frameon=False, ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(sortie / f"lits_{nom}.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
