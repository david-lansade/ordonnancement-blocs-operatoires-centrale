"""Serveur web (Flask) : API JSON + interface dans app/static.

Lancer depuis la racine du projet :
    .venv\\Scripts\\python -m app.serveur
puis ouvrir http://127.0.0.1:5000
"""
import copy
import math
import threading
import traceback
import uuid
from io import BytesIO

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from src import grille as G
from src import scenario as S
from src.aleas import ALEAS_CRISE, ALEAS_ORDINAIRES, PARAMS_ALEAS, parametres, tirer_aleas
from src.campagne import SCENARIOS, lancer_campagne
from src.chargement import JOURS, MOIS_COVID, statistiques_reference
from src.comparaison import BUDGET_DEFAUT, comparer
from src.contexte import Contexte
from src.export import programme_en_excel
from src.flux import debit_historique, injecter, tirer_flux, tirer_flux_tirages
from src.generation import completer_predictions
from src.horaires import HORAIRES_DEFAUT
from src.modele import Evaluateur
from src.robustesse import CRITERES as CRITERES_ROBUSTESSE, NIVEAUX as NIVEAUX_ROBUSTESSE
from src.sma import NOMS_STRATEGIES, robustesse, simuler
from src.urgences import resume_tri

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # le navigateur revalide app.js / style.css à chaque chargement
app.json.sort_keys = False   # garder l'ordre des dictionnaires (recuit, tabou, génétique...)
ctx = None
jobs = {}

# Valeurs de référence du CLAUDE.md (sections 4 et 7) pour signaler les écarts
REFERENCE_BASE = {
    "duree_op_moyenne": (77, 1.5, "Durée opératoire moyenne (min)"),
    "duree_op_ecart_type": (38, 1.5, "Écart-type de la durée (min)"),
    "ecart_type_a_type_fixe": (30, 2, "Écart-type à type d'intervention fixé (min)"),
    "part_ambulatoire": (0.545, 0.005, "Part d'ambulatoire (dates)"),
    "part_duree_col_egale_1": (0.67, 0.01, "Part « durée séjour = 1 » dans la colonne brute"),
    "lits_moyenne_toutes_nuits": (13.0, 0.3, "Lits occupés par nuit, moyenne"),
    "lits_max": (33, 0, "Lits occupés, maximum"),
    "lits_ecart_type": (6.06, 0.15, "Écart-type de l'occupation des lits"),
}


def propre(o):
    """Rend un objet sérialisable en JSON strict (numpy -> Python, NaN -> null)."""
    if isinstance(o, dict):
        return {str(k): propre(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [propre(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        x = float(o)
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(o, np.ndarray):
        return propre(o.tolist())
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def repondre(o, code=200):
    return jsonify(propre(o)), code


@app.errorhandler(Exception)
def erreur(e):
    if isinstance(e, HTTPException):
        return e
    traceback.print_exc()
    return jsonify({"erreur": f"{type(e).__name__} : {e}"}), 500


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# Référentiels pour les formulaires
# ---------------------------------------------------------------------------
@app.get("/api/init")
def init():
    df = ctx.df
    recent = df[df["date_inter"] >= "2021-01-01"]
    grille = ctx.grille(ctx.params["grille"])
    codes_grille = {v["chir"] for v in grille}
    chirs = []
    for code, g in df.groupby("praticien"):
        r = recent[recent["praticien"] == code]
        jours = r.groupby("jour_semaine").size().reindex(range(5), fill_value=0)
        chirs.append({
            "code": code, "nom": g["nom_chir"].mode().iat[0], "n": int(len(g)), "n_recent": int(len(r)),
            "dans_grille": code in codes_grille,
            "jours": [JOURS[j] for j in range(5) if jours[j] >= 0.05 * max(1, jours.sum())],
            "types": r["type_interv"].value_counts().head(25).index.tolist(),
        })
    chirs.sort(key=lambda c: (not c["dans_grille"], -c["n_recent"]))
    types = df["type_interv"].value_counts()
    cim3 = df["cim3"].value_counts()
    chap = df["cim_chapitre"].value_counts()
    return repondre({
        "chirurgiens": chirs,
        "types": types[types >= 3].index.tolist(),
        "cim": [{"code": c, "libelle": S.LIBELLES_CIM3.get(c, ""), "n": int(n)} for c, n in cim3.head(120).items()],
        "chapitres": [{"code": c, "libelle": S.CHAPITRES_CIM.get(c, "Autre"), "n": int(chap.get(c, 0)),
                       "poids": ctx.params["poids_chapitres"].get(c, 1.0)}
                      for c in sorted(set(S.CHAPITRES_CIM) | set(chap.index) - {"?"},
                                      key=lambda c: -int(chap.get(c, 0)))],
        "anesth_types": [a for a in df["anesth_type"].value_counts().index if a != "Non renseigné"],
        "anesth_locoreg": [a for a in df["anesth_locoreg"].value_counts().index if a != "Aucune"],
        "params": ctx.params,
        # valeurs par défaut (bouton « Valeurs par défaut » du mode Médecin, sans tout réinitialiser)
        "params_defaut": S.PARAMETRES_DEFAUT,
        "libelles_cim3": S.LIBELLES_CIM3,
        "aleas_defaut": PARAMS_ALEAS, "aleas_ordinaires": ALEAS_ORDINAIRES, "aleas_crise": ALEAS_CRISE,
        # nouveaux patients en flux (situation de l'onglet Simulation) : débit moyen de la base
        "flux_historique": debit_historique(df),
        # liste de départ par défaut (texte d'explication du flux, si l'utilisateur a changé ces réglages)
        "defauts_liste": {k: S.PARAMETRES_DEFAUT[k] for k in ("initial_nb_patients", "instance_semaines")},
        "scenarios": [{"cle": k, **{c: v for c, v in s.items() if c != "aleas"}} for k, s in SCENARIOS.items()],
        # score de robustesse (src/robustesse.py) : critères, poids et seuils affichés dans l'onglet Simulation
        "robustesse": {"criteres": CRITERES_ROBUSTESSE, "niveaux": NIVEAUX_ROBUSTESSE},
    })


# ---------------------------------------------------------------------------
# Attribution d'un patient (page 1)
# ---------------------------------------------------------------------------
@app.post("/api/proposer")
def proposer():
    infos = request.get_json()
    with ctx.verrou:
        return repondre(ctx.planning.proposer(infos))


@app.post("/api/valider")
def valider():
    corps = request.get_json()
    with ctx.verrou:
        return repondre(ctx.planning.ajouter(corps["infos"], corps["choix"]))


@app.delete("/api/patient/<pid>")
def supprimer(pid):
    with ctx.verrou:
        ctx.planning.supprimer(pid)
    return repondre({"ok": True})


# déplacer un patient programmé à la main (onglet Planning) : dates possibles, puis déplacement choisi
@app.get("/api/patient/<pid>/deplacements")
def options_deplacement(pid):
    with ctx.verrou:
        return repondre(ctx.planning.options_deplacement(pid))


@app.post("/api/patient/<pid>/deplacer")
def deplacer_patient(pid):
    corps = request.get_json() or {}
    with ctx.verrou:
        return repondre(ctx.planning.deplacer(pid, corps))


# programme opératoire d'une période (onglet Planning) : JSON pour l'impression, ou classeur Excel
def _noms_chirurgiens():
    return {c: g.mode().iat[0] for c, g in ctx.df.groupby("praticien")["nom_chir"]}


def _programme():
    debut, fin = request.args.get("debut"), request.args.get("fin")
    if not debut or not fin:
        raise ValueError("Indiquez les dates de début et de fin.")
    with ctx.verrou:
        return ctx.planning.programme_operatoire(debut, fin)


@app.get("/api/programme")
def programme():
    prog, noms = _programme(), _noms_chirurgiens()
    for v in prog["vacations"]:
        v["chir_nom"] = noms.get(v["chir"], "")
    return repondre(prog)


@app.get("/api/programme.xlsx")
def programme_excel():
    prog = _programme()
    contenu = programme_en_excel(prog, _noms_chirurgiens())
    return send_file(BytesIO(contenu), as_attachment=True, download_name=f"programme_{prog['debut']}_{prog['fin']}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/planning")
def planning():
    with ctx.verrou:
        return repondre(ctx.planning.vue())


@app.post("/api/planning/vider")
def vider():
    with ctx.verrou:
        ctx.planning.vider()
    return repondre({"ok": True})


@app.post("/api/planning/replacer")
def replacer():
    with ctx.verrou:
        n = ctx.planning.replacer()
    return repondre({"replaces": n})


# ---------------------------------------------------------------------------
# Grille des vacations
# ---------------------------------------------------------------------------
@app.get("/api/grille")
def grille():
    ecarts, tvo = G.verifier_transcription()
    offre_act = {k: round(v / 60, 2) for k, v in G.minutes_par_semaine(G.GRILLE_ACTUELLE).items()}
    offre_nouv = {k: round(v / 60, 2) for k, v in G.minutes_par_semaine(ctx.grille_nouvelle).items()}
    return repondre({
        "actuelle": G.grille_en_json(G.GRILLE_ACTUELLE),
        "nouvelle": G.grille_en_json(ctx.grille_nouvelle),
        "tableau": ctx.tableau_grille.to_dict("records"),
        "explications": ctx.explications_grille,
        "reserve": ctx.reserve.to_dict("records"),
        "verification": {"ecarts": ecarts, "tvo_par_salle": tvo},
        "total_actuel": round(sum(v for k, v in offre_act.items() if k not in ("URGENCES", "LIBRE")), 2),
        "total_nouveau": round(sum(v for k, v in offre_nouv.items() if k not in ("URGENCES", "LIBRE")), 2),
        "urgences_actuel": offre_act.get("URGENCES", 0), "urgences_nouveau": offre_nouv.get("URGENCES", 0),
        "grille_utilisee": ctx.params["grille"],
        "params": {k: ctx.params[k] for k in ("grille_marge", "grille_periode", "quantile_urgences")},
        "rotation": ctx.rotation,
    })


@app.post("/api/grille/construire")
def construire_grille():
    corps = request.get_json() or {}
    with ctx.verrou:
        for k in ("grille_marge", "grille_periode", "quantile_urgences"):
            if k in corps:
                ctx.params[k] = corps[k]
        ctx.sauver_parametres()
        ctx.construire_grille()
        n = ctx.planning.replacer() if ctx.params["grille"] == "nouvelle" else 0
    return repondre({"ok": True, "replaces": n})


@app.post("/api/grille/utiliser")
def utiliser_grille():
    nom = request.get_json()["nom"]
    with ctx.verrou:
        ctx.params["grille"] = nom
        ctx.sauver_parametres()
        n = ctx.planning.replacer()
    return repondre({"ok": True, "replaces": n})


# ---------------------------------------------------------------------------
# Onglet Technique : paramètres, comparaison des métaheuristiques, analyses
# ---------------------------------------------------------------------------
@app.get("/api/parametres")
def lire_parametres():
    return repondre(ctx.params)


@app.post("/api/parametres")
def ecrire_parametres():
    corps = request.get_json() or {}
    remplacer = set(corps.pop("remplacer", []))   # clés dict à remplacer entièrement (ex. poids_codes)
    if corps.pop("horaires_defaut", False):
        corps["horaires"] = dict(HORAIRES_DEFAUT)
        remplacer.add("horaires")
    with ctx.verrou:
        reordonnait = ctx.planning.reordonner()
        if corps.pop("reinitialiser", False):
            ctx.params.clear()
            ctx.params.update(copy.deepcopy(S.PARAMETRES_DEFAUT))
        for k, v in corps.items():
            if isinstance(v, dict) and isinstance(ctx.params.get(k), dict) and k not in remplacer:
                ctx.params[k].update(v)
            else:
                ctx.params[k] = v
        # les poids w_i des patients déjà programmés suivent le réglage du médecin
        for p in ctx.planning.patients + ctx.liste.patients:
            p["poids_retard"] = S.poids_retard(p.get("cim"), ctx.params)
        if "exclure_covid" in corps or "methode_prediction" in corps:
            # la liste des patients initiaux affiche les prédictions du modèle choisi
            completer_predictions(ctx.liste.patients, ctx.pred_complet, ctx.params["methode_prediction"], ctx.params)
        if reordonnait and not ctx.planning.reordonner():
            # option décochée : on fige les horaires tels qu'ils sont affichés maintenant
            ctx.planning.figer_horaires(recalculer=True)
        ctx.planning.sauver()
        ctx.liste.sauver()
        ctx.sauver_parametres()
    return repondre(ctx.params)


def _travail(job_id, corps):
    job = jobs[job_id]
    try:
        p = dict(ctx.params)
        inst = corps.get("instance", {})
        nom_grille = inst.get("grille") or p["grille"]
        job.update(message="Construction de l'instance")
        if not ctx.liste.patients:
            raise ValueError("La liste des patients initiaux est vide (onglet Patients initiaux).")
        d, P, vac = S.creer_instance_liste(ctx.liste.patients, ctx.pred_complet, ctx.grille(nom_grille), p,
                                           ctx.reserve, ctx.rotation)
        algos = corps.get("algos") or ["recuit", "tabou", "genetique"]
        graines = list(range(int(corps.get("graine", 0)), int(corps.get("graine", 0)) + int(corps.get("nb_graines", 1))))

        def rappel(frac, msg):
            job.update(progression=frac, message=msg)

        res = comparer(d, P, vac, algos=algos, budget=int(corps.get("budget", BUDGET_DEFAUT)), graines=graines,
                       depart=corps.get("depart", "aleatoire"), params_algos=corps.get("params_algos") or {},
                       rotation=ctx.rotation, rappel=rappel)
        res["instance"]["grille"] = nom_grille
        res["instance"]["date_ouverture"] = p["date_ouverture"]
        res["instance"]["semaines"] = p["instance_semaines"]
        res["instance"]["taille_liste"] = len(ctx.liste.patients)
        res["instance"]["modele"] = (("machine learning" if p["methode_prediction"] == "ml" else "étude statistique")
                                     + (", mois COVID exclus" if p.get("exclure_covid") else ", mois COVID inclus"))
        ctx.derniere_instance = (d, P, vac, nom_grille)
        ctx.derniere_comparaison = res
        allege = {k: v for k, v in res.items() if k not in ("solutions",)}
        allege["meilleur"] = {k: v for k, v in res["meilleur"].items() if k != "solution"}
        job.update(etat="termine", progression=1.0, resultat=allege, message="Terminé")
    except Exception as e:
        traceback.print_exc()
        job.update(etat="erreur", message=f"{type(e).__name__} : {e}")


@app.post("/api/optimiser")
def optimiser():
    corps = request.get_json() or {}
    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"etat": "en_cours", "progression": 0.0, "message": "Démarrage"}
    threading.Thread(target=_travail, args=(job_id, corps), daemon=True).start()
    return repondre({"job": job_id})


# ---------------------------------------------------------------------------
# Patients à programmer à l'ouverture (liste inventée, modifiable par le médecin)
# ---------------------------------------------------------------------------
@app.get("/api/initiaux")
def initiaux():
    L = ctx.liste.patients
    base = ctx.generateur.base
    X = pd.DataFrame(L) if L else pd.DataFrame(columns=["cim", "chir", "duree", "ambu", "nuits", "date_souhait"])
    part_base = base["cim3"].value_counts(normalize=True)
    part_liste = X["cim"].str[:3].value_counts(normalize=True) if len(X) else pd.Series(dtype=float)
    top = part_base.head(15).index
    chir_base = ctx.generateur.p_chir
    chir_liste = X["chir"].value_counts(normalize=True) if len(X) else pd.Series(dtype=float)
    p = ctx.params
    T = 7 * int(p["instance_semaines"])
    vac = G.vacations_horizon(ctx.grille(p["grille"]), p["date_ouverture"], T)
    vac = vac[vac["chir"].isin(set(X["chir"]))] if len(vac) else vac   # comme dans l'instance
    offre = float(vac["capacite"].sum()) if len(vac) else 0.0
    return repondre({
        "parametres": {"x": p["initial_nb_patients"], "semaines": p["instance_semaines"], "graine": p["initial_graine"],
                       "date_ouverture": p["date_ouverture"], "grille": p["grille"]},
        "patients": L,
        "resume": {
            "n": len(X), "generes": int((X.get("source") == "généré").sum()) if len(X) else 0,
            "ajoutes": int((X.get("source") == "ajouté").sum()) if len(X) else 0,
            "part_ambu": float(X["ambu"].mean()) if len(X) else None,
            "minutes_demandees": float(X["duree"].sum()) if len(X) else 0.0,
            "minutes_offertes": offre, "nuits": int(X["nuits"].sum()) if len(X) else 0,
            "age_moyen": float(X["age"].mean()) if len(X) else None,
            "chirurgiens": int(X["chir"].nunique()) if len(X) else 0,
        },
        "diagnostics": [{"code": c, "libelle": S.LIBELLES_CIM3.get(c, ""), "part_base": float(part_base[c]),
                         "part_liste": float(part_liste.get(c, 0.0))} for c in top],
        "chirurgiens": [{"code": c, "part_base": float(chir_base.get(c, 0.0)), "part_liste": float(chir_liste.get(c, 0.0))}
                        for c in chir_base.sort_values(ascending=False).index],
    })


@app.post("/api/initiaux/generer")
def generer_initiaux():
    corps = request.get_json() or {}
    with ctx.verrou:
        ctx.generer_liste(corps.get("x"), corps.get("semaines"), corps.get("graine"))
    return repondre({"ok": True, "n": len(ctx.liste.patients)})


@app.post("/api/initiaux/ajouter")
def ajouter_initial():
    infos = request.get_json() or {}
    if not infos.get("praticien") or not infos.get("date_souhait"):
        return repondre({"erreur": "Chirurgien et date souhaitée obligatoires."}, 400)
    rng = np.random.default_rng()
    with ctx.verrou:
        # type d'intervention tiré d'un cas passé du chirurgien s'il n'est pas précisé
        simule = ctx.generateur.patient(infos["praticien"], rng, type_interv=infos.get("type_interv") or None)
        patient = {
            "chir": infos["praticien"], "type_interv": infos.get("type_interv") or simule["type_interv"],
            "cim": (infos.get("cim") or "").upper(), "sexe": int(infos.get("sexe") or 1),
            "age": float(infos.get("age") or 50), "anesth_type": infos.get("anesth_type") or "Non renseigné",
            "anesth_locoreg": infos.get("anesth_locoreg") or "Aucune", "date_souhait": infos["date_souhait"],
            "nom": infos.get("nom") or "",
        }
        completer_predictions([patient], ctx.pred_complet, ctx.params["methode_prediction"], ctx.params)
        ctx.liste.ajouter(patient)
    return repondre(patient)


@app.delete("/api/initiaux/<pid>")
def supprimer_initial(pid):
    with ctx.verrou:
        ctx.liste.supprimer(pid)
    return repondre({"ok": True, "n": len(ctx.liste.patients)})


@app.get("/api/horaires/exemple")
def horaires_exemple():
    """Programme horaire d'une vacation du planning choisie pour être parlante : de préférence
    des ambulatoires ET des hospitalisés, des opérations lourdes ET légères, sur tout l'horizon."""
    with ctx.verrou:
        vue = ctx.planning.vue()
    vacs = [v for v in vue["vacations"] if len(v["patients"]) >= 2] or [v for v in vue["vacations"] if v["patients"]]
    if not vacs:
        return repondre({"vacation": None})

    def composition(x):
        n_ambu = sum(1 for p in x["patients"] if p["ambu"])
        n_lourde = sum(1 for p in x["patients"] if (p.get("horaire") or {}).get("categorie") == "lourde")
        return {"ambulatoires": n_ambu, "hospitalises": len(x["patients"]) - n_ambu,
                "lourdes": n_lourde, "legeres": len(x["patients"]) - n_lourde}

    def interet(x):
        c = composition(x)
        mixte_hebergement = c["ambulatoires"] > 0 and c["hospitalises"] > 0
        mixte_categorie = c["lourdes"] > 0 and c["legeres"] > 0
        # plus les deux groupes sont équilibrés, plus l'ordre de passage est parlant
        variete = min(c["ambulatoires"], c["hospitalises"], 3) + min(c["lourdes"], c["legeres"], 3)
        return (mixte_hebergement and mixte_categorie, mixte_hebergement, mixte_categorie, variete,
                -abs(len(x["patients"]) - 6), -x["jour"])   # puis environ 6 patients, puis la plus proche

    v = max(vacs, key=interet)
    c = composition(v)
    c["complet"] = bool(c["ambulatoires"] and c["hospitalises"] and c["lourdes"] and c["legeres"])
    return repondre({"vacation": v, "composition": c})


@app.get("/api/job/<job_id>")
def etat_job(job_id):
    return repondre(jobs.get(job_id, {"etat": "inconnu"}))


def demarrer_job(cible):
    """Lance cible(rappel) dans un fil d'exécution ; le navigateur suit /api/job/<id>."""
    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"etat": "en_cours", "progression": 0.0, "message": "Démarrage"}

    def travail():
        job = jobs[job_id]
        try:
            res = cible(lambda frac, msg: job.update(progression=frac, message=msg))
            job.update(etat="termine", progression=1.0, resultat=res, message="Terminé")
        except Exception as e:
            traceback.print_exc()
            job.update(etat="erreur", message=f"{type(e).__name__} : {e}")

    threading.Thread(target=travail, daemon=True).start()
    return repondre({"job": job_id})


# ---------------------------------------------------------------------------
# Aléas, système multi-agents, robustesse, scénarios (onglet Simulation et aléas)
# ---------------------------------------------------------------------------
def _instance_planning():
    """(d, solution, identifiants) du planning adopté ; None s'il est vide."""
    with ctx.verrou:
        d, P, exclus, vac = ctx.planning._construire()
    sol = [int(v) for v in P["v_fixe"]]
    if not any(v >= 0 for v in sol):
        return None
    # Dans d, un patient déjà programmé n'a qu'une vacation possible : la sienne (utile pour proposer des
    # dates à un nouveau patient). Pour la simulation, on lui rend ses vacations possibles : même chirurgien,
    # dans sa fenêtre de dates, assez longues (même règle que construire_d), plus sa vacation actuelle.
    z = d.get("z_beta", 1.0)
    par_chir = {c: g for c, g in vac[vac["type"] == "chirurgie"].groupby("chir")}
    admissibles = []
    for (_, p), v0 in zip(P.reset_index(drop=True).iterrows(), sol):
        g = par_chir.get(p["chir"])
        liste = []
        if g is not None:
            f = g[(g["jour"] >= p["fenetre_min"]) & (g["jour"] <= p["fenetre_max"])]
            longues = f[f["capacite"] >= p["duree"] + z * p["sigma"]]
            liste = [int(v) for v in (longues if len(longues) else f).index]
        if v0 >= 0 and v0 not in liste:
            liste.append(v0)
        admissibles.append(liste or [max(v0, 0)])
    d = {**d, "admissibles": admissibles}
    return d, sol, P["id"].astype(str).tolist()


def _debit_flux(corps):
    """Nouveaux patients par jour ouvré demandés par la situation (corps["flux"] = {actif, par_jour}) ; 0 sans flux."""
    flux = corps.get("flux") or {}
    return max(0.0, float(flux.get("par_jour") or 0)) if flux.get("actif") else 0.0


@app.post("/api/simulation")
def simulation():
    """Le planning adopté face à un tirage d'aléas, avec les trois stratégies ; en option, de
    nouveaux patients arrivent chaque jour ouvré (corps["flux"] = {actif, par_jour}, src/flux.py)."""
    corps = request.get_json() or {}
    inst = _instance_planning()
    if inst is None:
        return repondre({"erreur": "Le planning est vide : adoptez d'abord un planning (onglet Technique)."}, 400)
    d, sol, ids = inst
    p = parametres(corps.get("aleas"))
    aleas = tirer_aleas(d, ctx.historique_urgences(), p, p["graine"])
    debit, resume_flux = _debit_flux(corps), None
    d_sim, sol_sim, ids_sim, aleas_sim = d, sol, ids, aleas
    if debit:
        with ctx.verrou:
            nouveaux = tirer_flux(ctx.generateur, ctx.pred_complet, ctx.params, d, debit, p["graine"])
        d_sim, sol_sim, ids_sim, aleas_sim = injecter(d, sol, ids, aleas, nouveaux, p)
        resume_flux = nouveaux["resume"]
    res = [simuler(d_sim, sol_sim, aleas_sim, s, p, ctx.rotation, ctx.params["date_ouverture"], ids_sim)
           for s in corps.get("strategies") or list(NOMS_STRATEGIES)]
    debut = pd.Timestamp(ctx.params["date_ouverture"])
    return repondre({"strategies": res, "aleas": aleas["resume"], "fermeture": aleas["fermeture"],
                     "parametres": p, "nb_patients": sum(v >= 0 for v in sol), "flux": resume_flux,
                     "dates": [(debut + pd.Timedelta(days=t)).strftime("%Y-%m-%d") for t in range(d["nb_jours"])],
                     "cap_lits_prevue": d["cap_lits"],
                     # F du planning adopté avant tout imprévu (patients programmés seulement), pour comparaison
                     "F_prevu": Evaluateur(d, sol).valeur()})


@app.post("/api/robustesse")
def robustesse_alternatives():
    """Plannings alternatifs (meilleure solution de chaque méthode de la dernière comparaison)
    face aux mêmes tirages d'aléas."""
    corps = request.get_json() or {}
    if ctx.derniere_comparaison is None:
        return repondre({"erreur": "Lancez d'abord la comparaison des métaheuristiques (onglet Technique)."}, 400)
    d, P, vac, nom_grille = ctx.derniere_instance
    comp = ctx.derniere_comparaison
    p = parametres(corps.get("aleas"))
    nb = int(corps.get("nb_tirages", 20))
    noms = {s["algo"]: s["nom"] for s in comp["synthese"]}
    debit = _debit_flux(corps)

    def cible(rappel):
        plans = dict(comp["solutions"])
        # nouveaux patients : un flux par tirage, le même pour tous les plannings
        flux = tirer_flux_tirages(ctx.generateur, ctx.pred_complet, ctx.params, d, debit, p["graine"], nb, rappel) if debit else None
        res = robustesse(d, plans, ctx.historique_urgences(), p, nb, ctx.rotation, rappel=rappel, noms=noms, flux=flux)
        lignes = []
        for s in comp["synthese"]:
            if s["algo"] in res:
                lignes.append({"algo": s["algo"], "nom": s["nom"], "famille": s["famille"], "F_min": s["F_min"],
                               "F_moyen": s["F_moyen"], **res[s["algo"]]})
        return {"plans": lignes, "nb_tirages": nb, "parametres": p, "grille": nom_grille,
                "meilleur_F": comp["meilleur"]["algo"],
                "flux": {"par_jour": debit, "ajoutes": round(float(np.mean([f["resume"]["ajoutes"] for f in flux])), 1)}
                if flux else None}
    return demarrer_job(cible)


@app.post("/api/scenarios")
def scenarios():
    corps = request.get_json() or {}
    cles = [c for c in corps.get("scenarios") or list(SCENARIOS) if c in SCENARIOS]
    algos = corps.get("algos") or ["recuit", "relais_recuit_tabou", "relais_genetique_recuit"]
    if not cles:
        return repondre({"erreur": "Cochez au moins un scénario."}, 400)
    return demarrer_job(lambda rappel: lancer_campagne(
        ctx, cles, algos, int(corps.get("budget", 100000)), int(corps.get("nb_tirages", 10)),
        corps.get("aleas"), corps.get("params_algos"), int(corps.get("graine", 0)), rappel,
        flux_par_jour=_debit_flux(corps)))


@app.post("/api/adopter")
def adopter():
    corps = request.get_json() or {}
    if ctx.derniere_comparaison is None:
        return repondre({"erreur": "Lancez d'abord une comparaison."}, 400)
    algo = corps.get("algo") or ctx.derniere_comparaison["meilleur"]["algo"]
    d, P, vac, nom_grille = ctx.derniere_instance
    with ctx.verrou:
        ctx.params["grille"] = nom_grille
        ctx.sauver_parametres()
        ctx.planning.adopter(d, P, vac, ctx.derniere_comparaison["solutions"][algo], ecartes=d.get("ecartes"))
    return repondre({"ok": True, "algo": algo, "patients": len(ctx.planning.patients),
                     "ecartes": len(ctx.planning.ecartes)})


@app.get("/api/analyse")
def analyse():
    stats = statistiques_reference(ctx.df)
    controles = []
    for cle, (ref, tol, libelle) in REFERENCE_BASE.items():
        val = stats[cle]
        controles.append({"libelle": libelle, "calcule": val, "reference": ref,
                          "conforme": abs(val - ref) <= tol + 1e-9})
    metriques = ctx.predicteurs[False].metriques
    df = ctx.df
    hors_covid = df[df["covid"] == 0]
    return repondre({
        "stats": stats, "controles": controles,
        "ml": metriques, "mois_covid": MOIS_COVID,
        "exclure_covid": bool(ctx.params.get("exclure_covid", False)),
        "methode_prediction": ctx.params["methode_prediction"],
        "urgences": resume_tri(df), "reserve": ctx.reserve.to_dict("records"),
        "rotation": ctx.rotation,
        # heures entières (clés « 7 », « 8 »… et non « 7.0 » : sinon le graphique ne les retrouve pas)
        "heures_urgences": (df[df["urgence"] == 1]["h_entree_salle"].dropna() // 60).astype(int).value_counts().sort_index().to_dict(),
        "heures_programmes": (df[df["urgence"] == 0]["h_entree_salle"].dropna() // 60).astype(int).value_counts().sort_index().to_dict(),
        "activite_semaine": hors_covid.groupby("jour_semaine").size().to_dict(),
    })


def main(port=5000):
    global ctx
    print("Chargement de la base et des modèles (premier lancement : ~30 s)...")
    ctx = Contexte()
    print(f"Prêt : http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
