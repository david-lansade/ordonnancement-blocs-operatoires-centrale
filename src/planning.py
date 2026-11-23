"""Planning en ligne (flux tiré) : les patients arrivent un par un.

Pour un nouveau patient, on prédit µ, σ, A, L, puis on essaie chacune de ses
vacations admissibles (bon chirurgien, fenêtre du délai clinique, durée qui tient)
en gardant fixés les patients déjà programmés : la valeur de F après insertion
classe les créneaux. Pas de métaheuristique ici, c'est une insertion exacte
(évaluation incrémentale, quelques millisecondes).

On propose au médecin la meilleure date (A) et une alternative un autre jour (B),
avec les raisons (remplissage de la vacation, lits ou places ces jours-là, retard).
L'option « créneau d'urgence encore vide la veille » a été supprimée le 2026-10-17 (demande de
l'utilisateur) : elle proposait le lendemain de la demande, avant la fin du délai de préparation.
"""
import json
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from . import grille as G
from .chargement import DOSSIER_PROCESSED, JOURS
from .horaires import en_texte, programme
from .modele import Evaluateur, construire_d, details
from .scenario import feries_horizon, options_d, poids_retard, ajouter_rotation, horaires_effectifs

FICHIER = DOSSIER_PROCESSED / "planning.json"


def _fmt_date(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _libelle_date(ts):
    ts = pd.Timestamp(ts)
    return f"{JOURS[ts.dayofweek]} {ts.strftime('%d/%m/%Y')}"


def _fr(x, d=1):
    """Nombre au format français (virgule décimale), pour les explications affichées."""
    return f"{x:.{d}f}".replace(".", ",")


def _heure(m):
    return f"{int(m) // 60}h{int(m) % 60:02d}"


class Planning:
    def __init__(self, ctx):
        self.ctx = ctx
        self.patients = []
        # patients de la liste d'ouverture écartés au calcul du planning (aucune vacation possible)
        self.ecartes = []
        if FICHIER.exists():
            contenu = json.loads(FICHIER.read_text(encoding="utf-8"))
            self.patients = contenu.get("patients", [])
            self.ecartes = contenu.get("ecartes", [])

    # --- calendrier -------------------------------------------------------
    @property
    def params(self):
        return self.ctx.params

    def debut(self):
        return pd.Timestamp(self.params["date_ouverture"]).normalize()

    def nb_jours(self):
        return 7 * int(self.params["semaines_horizon"])

    def t_de(self, date_str):
        return int((pd.Timestamp(date_str).normalize() - self.debut()).days)

    def date_de(self, t):
        return _fmt_date(self.debut() + pd.Timedelta(days=int(t)))

    def vacations(self):
        """Toutes les vacations de l'horizon, y compris URGENCES et LIBRE."""
        return G.vacations_horizon(self.ctx.grille(self.params["grille"]), self.debut(), self.nb_jours(),
                                   garder_urgences=True)

    def sauver(self):
        DOSSIER_PROCESSED.mkdir(parents=True, exist_ok=True)
        FICHIER.write_text(json.dumps({"patients": self.patients, "ecartes": self.ecartes}, ensure_ascii=False, indent=0),
                           encoding="utf-8")

    @staticmethod
    def _cle(vac_row):
        return (_fmt_date(vac_row["date"]), int(vac_row["salle"]), int(vac_row["debut"]))

    # --- construction de d avec les patients déjà programmés -----------------
    def horizon_planifie(self, extra=None):
        """Nombre de jours réellement planifiés : jusqu'à la dernière date d'opération
        (ou la fin de la fenêtre du nouveau patient), arrondi à la semaine ; comme dans
        le modèle, les nuits au-delà sont tronquées. Les cibles n*_t et
        a*_t, proportionnelles au total, n'ont de sens que sur cette période : sur des
        semaines encore vides, la moyenne serait artificiellement basse."""
        fin = 7 * int(self.params.get("instance_semaines", 8))
        for p in self.patients + ([extra] if extra else []):
            date_fin = p["vacation"]["date"] if "vacation" in p else p["date_max"]
            fin = max(fin, self.t_de(date_fin) + 1)
        return int(min(self.nb_jours(), 7 * -(-fin // 7)))

    def _construire(self, extra=None):
        vac_all = self.vacations()
        T = self.horizon_planifie(extra)
        vac_all = vac_all[vac_all["jour"] < T]
        utilisees = {(p["vacation"]["date"], p["vacation"]["salle"], p["vacation"]["debut"]) for p in self.patients}
        cles = [self._cle(r) for _, r in vac_all.iterrows()]
        garde = [(t == "chirurgie") or (c in utilisees) for t, c in zip(vac_all["type"], cles)]
        vac = vac_all[garde].reset_index(drop=True)
        index = {self._cle(r): v for v, r in vac.iterrows()}
        lignes = []
        for p in self.patients:
            cle = (p["vacation"]["date"], p["vacation"]["salle"], p["vacation"]["debut"])
            lignes.append({**self._champs_modele(p), "v_fixe": index.get(cle, -1)})
        if extra is not None:
            lignes.append({**self._champs_modele(extra), "v_fixe": -1})
        P = pd.DataFrame(lignes, columns=["duree", "sigma", "ambu", "nuits", "souhait", "tolerance", "limite", "fenetre_min",
                                          "fenetre_max", "chir", "poids_retard", "v_fixe", "id"])
        d, P2, exclus, vac = construire_d(P, vac, T, feries=feries_horizon(self.debut(), T),
                                          retirer_vacations_sans_patient=False,
                                          **options_d(self.params, self.ctx.reserve))
        ajouter_rotation(d, self.params, self.ctx.rotation)
        return d, P2, exclus, vac

    def _champs_modele(self, p):
        return {"duree": p["duree"], "sigma": p["sigma"], "ambu": p["ambu"], "nuits": p["nuits"],
                "souhait": self.t_de(p["date_souhait"]), "tolerance": int(p.get("tolerance") or 0),
                # limite médicale recalculée avec le délai médical en vigueur
                "limite": self.t_de(p.get("date_demande") or self.date_de(0)) + int(self.params["delai_medical"]), "fenetre_min": self.t_de(p["date_min"]),
                "fenetre_max": self.t_de(p["date_max"]), "chir": p["chir"],
                "poids_retard": p["poids_retard"], "id": p["id"]}

    # --- nouveau patient ------------------------------------------------------
    def preparer_patient(self, infos):
        """Prédiction et délai clinique d'un nouveau patient (dates en texte AAAA-MM-JJ)."""
        par = self.params
        methode = infos.get("methode") or par["methode_prediction"]
        pred = self.ctx.pred_complet.predire_patient(infos, methode)
        if infos.get("forcer_ambu") in (0, 1, "0", "1"):
            pred["ambu"] = int(infos["forcer_ambu"])
            if pred["ambu"] == 1:
                pred["nuits"] = 0
            elif pred["nuits"] == 0:
                pred["nuits"] = max(1, pred["stat_nuits"])
        if infos.get("forcer_nuits") not in (None, ""):
            pred["nuits"] = int(infos["forcer_nuits"])
        souhait = pd.Timestamp(infos["date_souhait"]).normalize()
        demande = pd.Timestamp(infos.get("date_demande") or datetime.now().date()).normalize()
        tol = int(infos.get("tolerance") if infos.get("tolerance") not in (None, "") else par["tolerance"])
        # limite MÉDICALE : date de la demande + délai médical maximal (sans lien avec le souhait)
        limite = demande + pd.Timedelta(days=int(par["delai_medical"]))
        date_min = max(souhait - pd.Timedelta(days=par["avance_max"]),
                       demande + pd.Timedelta(days=par["delai_preparation"]), self.debut())
        fin_horizon = self.debut() + pd.Timedelta(days=self.nb_jours() - 1)
        date_max = min(souhait + pd.Timedelta(days=tol + par["retard_max"]), fin_horizon)
        w = poids_retard(infos.get("cim"), par)
        patient = {
            "id": infos.get("id") or "P" + uuid.uuid4().hex[:8].upper(),
            "source": "médecin",
            "chir": infos["praticien"], "type_interv": infos.get("type_interv") or "Inconnu",
            "cim": (infos.get("cim") or "").upper(), "age": infos.get("age"), "sexe": infos.get("sexe"),
            "anesth_type": infos.get("anesth_type"), "anesth_locoreg": infos.get("anesth_locoreg"),
            "nom": infos.get("nom") or "",
            "duree": pred["duree"] + par["rotation_dans_mu"], "sigma": pred["sigma"],
            "p_ambu": pred["p_ambu"], "ambu": pred["ambu"], "nuits": pred["nuits"],
            "poids_retard": w, "date_souhait": _fmt_date(souhait), "date_demande": _fmt_date(demande),
            "tolerance": tol, "limite": _fmt_date(limite), "date_min": _fmt_date(date_min),
            "date_max": _fmt_date(date_max),
        }
        return pred, patient

    def proposer(self, infos, nb=3):
        pred, patient = self.preparer_patient(infos)
        res = {"prediction": pred, "patient": patient, "propositions": [],
               "horizon": {"debut": _fmt_date(self.debut()), "fin": self.date_de(self.nb_jours() - 1)}}
        if pd.Timestamp(patient["date_min"]) > pd.Timestamp(patient["date_max"]):
            res["erreur"] = ("La fenêtre de dates est hors de l'horizon du planning "
                             f"({res['horizon']['debut']} au {res['horizon']['fin']}). "
                             "Allongez l'horizon dans l'onglet Technique.")
            return res
        d, P, exclus, vac = self._construire(extra=patient)
        if P.iloc[-1]["id"] != patient["id"]:
            res["erreur"] = (f"Aucune vacation de {patient['chir']} dans l'horizon après le {patient['date_min']} "
                             f"(grille « {self.params['grille']} »).")
            return res
        i = len(P) - 1
        sol = [int(v) for v in P["v_fixe"].iloc[:-1]] + [-1]
        ev = Evaluateur(d, sol)
        F0 = ev.valeur()
        termes0 = ev.termes()
        candidats = []
        par_vac = self._par_vacation()
        for v in d["admissibles"][i]:
            c = self._decrire(ev, d, vac, i, v, patient, F0, termes0)
            h = self._horaire_insertion(vac.iloc[v], par_vac, patient)
            if h:
                c["horaire"] = h
                c["explication"].append(f"Horaire prévu : convocation à {h['convocation']}, passage en SAS à {h['sas']}, "
                                        f"entrée en salle à {h['entree']} ({h['rang']}e patient sur {h['nb']})"
                                        + ("." if self.reordonner() else " ; horaire définitif, il ne bougera plus."))
                if h["depasse"]:
                    c["explication"].append(f"Attention : sortie de salle prévue à {h['sortie']}, après la fin de la vacation.")
            candidats.append(c)
        candidats.sort(key=lambda c: (not c["faisable"], c["F"]))
        # A = meilleur ; B, C = meilleurs sur d'autres jours
        choisis, jours = [], set()
        for c in candidats:
            if c["date"] not in jours:
                choisis.append(c)
                jours.add(c["date"])
            if len(choisis) >= nb:
                break
        for rang, c in zip("ABCDE", choisis):
            c["rang"] = rang
        res["propositions"] = choisis
        res["nb_candidats"] = len(candidats)
        res["nb_faisables"] = sum(c["faisable"] for c in candidats)
        return res

    def _decrire(self, ev, d, vac, i, v, patient, F0, termes0):
        """Évalue le patient i dans la vacation v et rédige l'explication."""
        H, z = d["capacite"][v], d.get("z_beta", 1.0)
        avant = ev.charge[v]
        var_apres = ev.var[v] + patient["sigma"] ** 2
        ev.deplacer(i, v)
        F = ev.valeur()
        termes = ev.termes()
        apres = ev.charge[v]
        marge_ok = apres + z * np.sqrt(var_apres) <= H + 1e-9
        t0 = d["jour"][v]
        explication = []
        nuits_detail, depasse = [], False
        if patient["ambu"] == 1:
            a, c, C = ev.places[t0], ev.cible_places(t0), d["cap_ambu"][t0]
            depasse = a > C + 1e-9
            explication.append(f"Place ambulatoire le jour même : {a:.0f} places occupées ce jour-là "
                               f"(cible {_fr(c)}, capacité {C}).")
        else:
            debut, fin = ev._nuits_de(i, v)
            for t in range(debut, fin):
                n, c, C = ev.lits[t], ev.cible_lits(t), d["cap_lits"][t]
                depasse |= n > C + 1e-9
                nuits_detail.append({"date": self.date_de(t), "lits": round(n, 1), "cible": round(c, 1), "cap": C,
                                     "week_end": t % 7 >= 5})
            if nuits_detail:
                moy_n = np.mean([x["lits"] for x in nuits_detail])
                moy_c = np.mean([x["cible"] for x in nuits_detail])
                we = sum(x["week_end"] for x in nuits_detail)
                explication.append(f"{len(nuits_detail)} nuit(s) d'hospitalisation : {_fr(moy_n)} lits occupés en "
                                   f"moyenne ces nuits-là, pour une cible de {_fr(moy_c)}"
                                   + (f" ; dont {we} nuit(s) de week-end (moins d'infirmiers)." if we else "."))
        ev._retirer(i)
        explication.insert(0, f"Vacation de {d['vac_chir'][v]} remplie à {100 * avant / H:.0f} % → "
                              f"{100 * apres / H:.0f} % ({apres:.0f} min prévues sur {H} min) ; marge d'aléa "
                              + ("respectée." if marge_ok else "DÉPASSÉE (risque de débordement)."))
        ecart = t0 - self.t_de(patient["date_souhait"])
        tol = int(patient.get("tolerance") or 0)
        retard = max(0, t0 - self.t_de(patient["limite"]))
        if ecart < 0:
            explication.append(f"{-ecart} jour(s) avant la date souhaitée.")
        elif ecart == 0:
            explication.append("À la date souhaitée.")
        elif ecart <= tol:
            explication.append(f"{ecart} jour(s) après la date souhaitée, dans la tolérance.")
        else:
            explication.append(f"{ecart - tol} jour(s) au-delà de la tolérance.")
        if retard > 0:
            explication.append(f"{retard} jour(s) après la limite médicale ({_libelle_date(patient['limite'])}), "
                               f"urgence w_i = {format(patient['poids_retard'], 'g').replace('.', ',')}.")
        r = vac.iloc[v]
        return {
            "v": int(v), "date": _fmt_date(r["date"]), "libelle": _libelle_date(r["date"]),
            "salle": int(r["salle"]), "chir": r["chir"], "debut": _heure(r["debut"]), "fin": _heure(r["fin"]),
            "debut_min": int(r["debut"]), "type": r["type"],
            "F": round(F, 2), "delta_F": round(F - F0, 2),
            "delta_termes": {k: round(termes[k] - termes0[k], 2) for k in termes},
            "remplissage_avant": round(avant / H, 3), "remplissage_apres": round(apres / H, 3),
            "marge_ok": bool(marge_ok), "hebergement_ok": not depasse,
            "faisable": bool(marge_ok and not depasse),
            "retard_jours": int(retard), "ecart_souhait": int(ecart), "nuits": nuits_detail,
            "explication": explication,
        }

    # --- programme horaire ----------------------------------------------------------
    def _par_vacation(self):
        par_vac = {}
        for p in self.patients:
            par_vac.setdefault((p["vacation"]["date"], p["vacation"]["salle"], p["vacation"]["debut"]), []).append(p)
        return par_vac

    def reordonner(self):
        """True : le programme d'une vacation est recalculé à chaque arrivée (horaires mobiles)."""
        return bool((self.params.get("horaires") or {}).get("reordonner", True))

    @staticmethod
    def _cle_patient(p):
        v = p.get("vacation")
        return [v["date"], v["salle"], v["debut"]] if v else None

    def _figes(self, pts):
        """Horaires figés encore valables (même vacation) ; aucun si l'option « reordonner » est active."""
        if self.reordonner():
            return None
        return [p["horaire_fige"] if p.get("horaire_fige") and p["horaire_fige"].get("cle") == self._cle_patient(p)
                else None for p in pts]

    def _programme(self, pts, debut, fin):
        """Programme horaire (ordre, entrée en salle, SAS, convocation) des patients d'une vacation."""
        return programme(pts, debut, fin, horaires_effectifs(self.params, self.ctx.rotation), self._figes(pts))

    def figer_horaires(self, recalculer=False):
        """Fige les horaires actuels de tous les patients programmés (option « reordonner » désactivée).
        recalculer=True : on repart du meilleur programme de chaque vacation avant de figer."""
        for cle, pts in self._par_vacation().items():
            v = pts[0]["vacation"]
            figes = None if recalculer else self._figes(pts)
            for x in programme(pts, v["debut"], v["fin"], horaires_effectifs(self.params, self.ctx.rotation), figes):
                p = pts[x["indice"]]
                p["horaire_fige"] = {"cle": self._cle_patient(p), "entree": x["entree"], "sas": x["sas"],
                                     "convocation": x["convocation"]}
        self.sauver()

    @staticmethod
    def _horaire_texte(h, nb):
        return {"rang": h["rang"], "nb": nb, "entree": en_texte(h["entree"]), "sortie": en_texte(h["sortie"]),
                "sas": en_texte(h["sas"]), "convocation": en_texte(h["convocation"]), "categorie": h["categorie"],
                "depasse": h["depasse"], "fige": h.get("fige", False)}

    def _horaire_insertion(self, r, par_vac, patient):
        """Horaire du nouveau patient s'il était ajouté à la vacation r."""
        pts = par_vac.get(self._cle(r), []) + [patient]
        for h in self._programme(pts, int(r["debut"]), int(r["fin"])):
            if h["indice"] == len(pts) - 1:
                return self._horaire_texte(h, len(pts))
        return None

    def horaire_de(self, pid):
        """Horaire actuel d'un patient programmé (il change si la vacation se remplit)."""
        p = next((q for q in self.patients if q["id"] == pid), None)
        if p is None:
            return None
        cle = (p["vacation"]["date"], p["vacation"]["salle"], p["vacation"]["debut"])
        pts = self._par_vacation().get(cle, [])
        for h in self._programme(pts, p["vacation"]["debut"], p["vacation"]["fin"]):
            if pts[h["indice"]]["id"] == pid:
                return self._horaire_texte(h, len(pts))
        return None

    def ajouter(self, infos, choix):
        """Enregistre le patient dans la vacation choisie : choix = {date, salle, debut_min}."""
        pred, patient = self.preparer_patient(infos)
        vac = self.vacations()
        r = vac[(vac["date"] == pd.Timestamp(choix["date"])) & (vac["salle"] == int(choix["salle"]))
                & (vac["debut"] == int(choix["debut_min"]))]
        if not len(r):
            raise ValueError("Vacation introuvable")
        r = r.iloc[0]
        patient["vacation"] = {"date": _fmt_date(r["date"]), "salle": int(r["salle"]), "debut": int(r["debut"]),
                               "fin": int(r["fin"]), "chir": r["chir"]}
        patient["cree_le"] = datetime.now().isoformat(timespec="seconds")
        self.patients.append(patient)
        if not self.reordonner():
            self.figer_horaires()   # le nouveau patient passe après les patients figés, puis est figé à son tour
        self.sauver()
        return {**patient, "horaire": self.horaire_de(patient["id"])}

    def supprimer(self, pid):
        self.patients = [p for p in self.patients if p["id"] != pid]
        self.sauver()

    # --- modification à la main d'un patient programmé (onglet Planning, 2026-10-17) ------------------
    def _patient(self, pid):
        p = next((q for q in self.patients if q["id"] == pid), None)
        if p is None:
            raise ValueError(f"Patient {pid} introuvable dans le planning.")
        return p

    def options_deplacement(self, pid):
        """Vacations où le médecin peut déplacer le patient pid : toutes celles de son chirurgien sur l'horizon
        planifié, avec pour chacune l'effet sur F par rapport à sa vacation actuelle, les lits ou la place
        ambulatoire, la marge de la salle et l'horaire prévu. Ne modifie pas le planning."""
        p = self._patient(pid)
        d, P, exclus, vac = self._construire()
        ids = P["id"].astype(str).tolist()
        if pid not in ids:
            raise ValueError(f"Le patient {pid} n'a aucune vacation possible sur l'horizon du planning.")
        i = ids.index(pid)
        v0 = int(P.iloc[i]["v_fixe"])   # -1 : sa vacation n'existe plus (grille changée)
        sol = [int(v) for v in P["v_fixe"]]
        sol[i] = -1
        ev = Evaluateur(d, sol)
        F0, termes0 = ev.valeur(), ev.termes()
        # programme horaire des autres patients de chaque vacation (le patient lui-même retiré)
        par_vac = {k: [q for q in L if q["id"] != pid] for k, L in self._par_vacation().items()}
        candidats = [v for v in range(len(vac)) if vac.iloc[v]["chir"] == p["chir"] and vac.iloc[v]["type"] == "chirurgie"]
        if v0 >= 0 and v0 not in candidats:
            candidats.append(v0)
        res = []
        for v in candidats:
            c = self._decrire(ev, d, vac, i, v, p, F0, termes0)
            c["horaire"] = self._horaire_insertion(vac.iloc[v], par_vac, p)
            c["actuelle"] = v == v0
            c["dans_fenetre"] = p["date_min"] <= c["date"] <= p["date_max"]
            res.append(c)
        actuelle = next((c for c in res if c["actuelle"]), None)
        for c in res:   # effet sur F par rapport à la vacation actuelle (sans vacation : par rapport au patient absent)
            c["delta_F"] = round(c["F"] - (actuelle["F"] if actuelle else F0), 2)
            if actuelle:
                c["delta_termes"] = {k: round(x - actuelle["delta_termes"][k], 2) for k, x in c["delta_termes"].items()}
        res.sort(key=lambda c: (c["date"], c["salle"], c["debut_min"]))
        champs = ("id", "nom", "chir", "type_interv", "cim", "age", "sexe", "duree", "sigma", "ambu", "nuits",
                  "date_souhait", "tolerance", "date_min", "date_max", "limite", "poids_retard", "source")
        return {"patient": {k: p.get(k) for k in champs}, "actuelle": actuelle,
                "candidats": [c for c in res if not c["actuelle"]],
                "fin_horizon": self.date_de(d["nb_jours"] - 1)}

    def deplacer(self, pid, choix):
        """Déplace le patient pid dans la vacation choisie par le médecin : choix = {date, salle, debut_min}."""
        p = self._patient(pid)
        vac = self.vacations()
        r = vac[(vac["date"] == pd.Timestamp(choix["date"])) & (vac["salle"] == int(choix["salle"]))
                & (vac["debut"] == int(choix["debut_min"]))]
        if not len(r):
            raise ValueError("Vacation introuvable.")
        r = r.iloc[0]
        if r["chir"] != p["chir"]:
            raise ValueError(f"Cette vacation n'est pas une vacation de {p['chir']}.")
        ancienne = dict(p["vacation"])
        p["vacation"] = {"date": _fmt_date(r["date"]), "salle": int(r["salle"]), "debut": int(r["debut"]),
                         "fin": int(r["fin"]), "chir": r["chir"]}
        p["deplace_le"] = datetime.now().isoformat(timespec="seconds")
        if not self.reordonner():
            self.figer_horaires()   # le patient passe après les patients figés de sa nouvelle vacation, puis est figé
        self.sauver()
        return {**p, "ancienne_vacation": ancienne, "horaire": self.horaire_de(pid)}

    # --- programme opératoire à imprimer ou à exporter (onglet Planning, 2026-10-17) ------------------
    def programme_operatoire(self, debut, fin):
        """Vacations qui ont des patients du `debut` au `fin` (dates AAAA-MM-JJ incluses), par jour et par salle,
        avec l'ordre de passage et les horaires (convocation, passage en SAS, entrée et sortie de salle)."""
        t0, t1 = self.t_de(debut), self.t_de(fin)
        if t1 < t0:
            raise ValueError("La date de fin est avant la date de début.")
        par_vac = self._par_vacation()
        vac = self.vacations()
        vac = vac[(vac["jour"] >= t0) & (vac["jour"] <= t1)].sort_values(["jour", "salle", "debut"])
        champs = ("id", "nom", "type_interv", "cim", "age", "sexe", "anesth_type", "anesth_locoreg", "duree", "ambu", "nuits")
        res = []
        for _, r in vac.iterrows():
            pts = par_vac.get(self._cle(r), [])
            if not pts:
                continue
            prog = self._programme(pts, int(r["debut"]), int(r["fin"]))
            res.append({"date": _fmt_date(r["date"]), "salle": int(r["salle"]), "chir": r["chir"], "type": r["type"],
                        "debut": _heure(r["debut"]), "fin": _heure(r["fin"]),
                        "patients": [{**{k: pts[h["indice"]].get(k) for k in champs},
                                      "horaire": self._horaire_texte(h, len(pts))} for h in prog]})
        return {"debut": _fmt_date(debut), "fin": _fmt_date(fin), "vacations": res}

    def vider(self):
        self.patients, self.ecartes = [], []
        self.sauver()

    def adopter(self, d, P, vac, solution, source="instance initiale", ecartes=None):
        """Le planning devient la solution d'une métaheuristique (phase 1). ecartes : patients de la
        liste sans vacation possible (src/scenario.creer_instance_liste), signalés dans l'onglet Planning."""
        self.patients = []
        self.ecartes = list(ecartes or [])
        for i, v in enumerate(solution):
            p, r = P.iloc[i], vac.iloc[int(v)]
            self.patients.append({
                "id": str(p["id"]), "source": source, "chir": p["chir"], "type_interv": p["type_interv"],
                "cim": p["cim"], "age": float(p["age"]), "sexe": int(p["sexe"]), "nom": "",
                "anesth_type": p.get("anesth_type"), "anesth_locoreg": p.get("anesth_locoreg"),
                "duree": float(p["duree"]), "sigma": float(p["sigma"]), "p_ambu": float(p["p_ambu"]),
                "ambu": int(p["ambu"]), "nuits": int(p["nuits"]), "poids_retard": float(p["poids_retard"]),
                "date_souhait": self.date_de(p["souhait"]), "tolerance": int(p["tolerance"]),
                "limite": self.date_de(p["limite"]), "date_min": self.date_de(p["fenetre_min"]),
                "date_max": self.date_de(p["fenetre_max"]), "date_demande": self.date_de(0),
                "vacation": {"date": _fmt_date(r["date"]), "salle": int(r["salle"]), "debut": int(r["debut"]),
                             "fin": int(r["fin"]), "chir": r["chir"]},
                "cree_le": datetime.now().isoformat(timespec="seconds"),
            })
        if not self.reordonner():
            self.figer_horaires()   # planning neuf : meilleur programme, puis figé
        self.sauver()

    def replacer(self):
        """Replace par insertion gloutonne les patients dont la vacation n'existe plus
        (changement de grille ou d'horizon)."""
        d, P, exclus, vac = self._construire()
        sol = [int(v) for v in P["v_fixe"]]
        a_placer = [i for i, v in enumerate(sol) if v < 0]
        if not a_placer:
            return 0
        # les patients à replacer retrouvent leur fenêtre de dates
        par_chir = {c: g for c, g in vac[vac["type"] == "chirurgie"].groupby("chir")}
        ev = Evaluateur(d, sol)
        places = 0
        for i in a_placer:
            p = P.iloc[i]
            g = par_chir.get(p["chir"])
            if g is None:
                continue
            cand = g[(g["jour"] >= p["fenetre_min"]) & (g["jour"] <= p["fenetre_max"])]
            if not len(cand):
                cand = g[g["jour"] >= p["fenetre_min"]]
            if not len(cand):
                continue
            v = min(cand.index, key=lambda v: ev.essayer(i, int(v)))
            ev.deplacer(i, int(v))
            r = vac.iloc[int(v)]
            pid = P.iloc[i]["id"]
            for q in self.patients:
                if q["id"] == pid:
                    q["vacation"] = {"date": _fmt_date(r["date"]), "salle": int(r["salle"]), "debut": int(r["debut"]),
                                     "fin": int(r["fin"]), "chir": r["chir"]}
            places += 1
        if not self.reordonner():
            self.figer_horaires()   # les patients déplacés prennent un horaire après les patients figés
        self.sauver()
        return places

    # --- vue d'ensemble ---------------------------------------------------------
    def vue(self):
        d, P, exclus, vac = self._construire()
        vac_all = self.vacations()
        vac_all = vac_all[vac_all["jour"] < d["nb_jours"]]
        sol = [int(v) for v in P["v_fixe"]]
        places_ok = [v for v in sol if v >= 0]
        non_places = [P.iloc[i]["id"] for i, v in enumerate(sol) if v < 0]
        res = {"debut": _fmt_date(self.debut()), "nb_jours": d["nb_jours"], "nb_jours_horizon": self.nb_jours(),
               "dates": [self.date_de(t) for t in range(d["nb_jours"])],
               "nb_patients": len(self.patients), "non_places": non_places, "grille": self.params["grille"],
               "ecartes": [e for e in self.ecartes if e["id"] not in {p["id"] for p in self.patients}],
               # vacations de chirurgie de la grille sur l'horizon planifié (case « Remplissage des salles »)
               "vacations_grille": int((vac["type"] == "chirurgie").sum())}
        if places_ok and len(places_ok) == len(sol):
            det = details(sol, d)
        else:
            # évaluation sur les seuls patients placés
            garde = [i for i, v in enumerate(sol) if v >= 0]
            d2 = dict(d)
            for k in ("duree", "sigma", "ambu", "nuits", "limite", "poids_retard", "admissibles", "chir",
                      "souhait", "tolerance"):
                if d.get(k) is not None:
                    d2[k] = [d[k][i] for i in garde]
            det = details([sol[i] for i in garde], d2) if garde else details([], {**d, "duree": [], "sigma": [],
                                                                                   "ambu": [], "nuits": [], "limite": [],
                                                                                   "poids_retard": []})
        res.update({k: det[k] for k in ("lits", "cible_lits", "places", "cible_places", "cap_lits", "cap_ambu",
                                         "termes", "indicateurs", "F")})
        res["pers"] = d["pers"]
        # vacations avec leurs patients
        par_vac = self._par_vacation()
        z = self.params["z_beta"]
        lst, horaires = [], {}
        for _, r in vac_all.iterrows():
            cle = self._cle(r)
            pts = par_vac.get(cle, [])
            prog = self._programme(pts, int(r["debut"]), int(r["fin"])) if pts else []
            for h in prog:
                horaires[pts[h["indice"]]["id"]] = self._horaire_texte(h, len(pts))
            pts = [pts[h["indice"]] for h in prog]   # ordre de passage
            charge = sum(p["duree"] for p in pts)
            marge = charge + z * np.sqrt(sum(p["sigma"] ** 2 for p in pts))
            lst.append({"date": cle[0], "jour": int(r["jour"]), "salle": int(r["salle"]), "chir": r["chir"],
                        "debut": int(r["debut"]), "fin": int(r["fin"]), "capacite": int(r["capacite"]),
                        "type": r["type"], "charge": round(charge, 1), "charge_marge": round(float(marge), 1),
                        "patients": [{"id": p["id"], "type_interv": p["type_interv"], "duree": round(p["duree"]),
                                      "ambu": p["ambu"], "nuits": p["nuits"], "cim": p["cim"], "nom": p.get("nom", ""),
                                      "source": p["source"], "horaire": horaires.get(p["id"])} for p in pts]})
        res["vacations"] = lst
        res["derniers"] = [{**p, "horaire": horaires.get(p["id"])}
                           for p in self.patients if p["source"] == "médecin"][-15:][::-1]
        return res
