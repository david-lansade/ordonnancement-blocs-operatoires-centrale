"""Génération des patients à programmer à l'ouverture de l'hôpital.

Les patients sont INVENTÉS, mais tirés dans les distributions observées dans la base
(interventions programmées, jours ouvrés, hors printemps 2020), en chaîne :

    chirurgien      ~ activité de chaque chirurgien sur l'année récente (2022)
    intervention    ~ P(type | chirurgien)            (2021-2022 : pratique actuelle)
    diagnostic CIM  ~ P(cim | type, chirurgien), sinon P(cim | type)
    sexe            ~ P(sexe | type)
    âge             ~ âge d'un patient passé du même type + bruit N(0, 3 ans)
    anesthésie      ~ P(anesthésie | type, chirurgien), sinon P(anesthésie | type)
    jour souhaité   ~ semaine uniforme dans l'horizon, jour ~ P(jour | chirurgien)

Un diagnostic fréquent dans la base sort donc plus souvent, et les combinaisons
restent plausibles (un chirurgien de la main ne reçoit pas de prothèse de hanche).
Le tirage étant fait composante par composante, les patients ne sont pas des copies
de séjours passés.
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd

from .chargement import DOSSIER_PROCESSED

FICHIER = DOSSIER_PROCESSED / "patients_initiaux.json"


class Generateur:
    def __init__(self, df, annee_recente=2022):
        base = df[(df["urgence"] == 0) & (df["covid"] == 0) & (df["jour_semaine"] < 5)].copy()
        med = base.groupby("type_interv")["duree_op"].transform("median")
        base["duree_op"] = base["duree_op"].fillna(med).fillna(base["duree_op"].median())
        self.base = base
        recente = base[base["date_inter"].dt.year == annee_recente]
        self.recente = base[base["date_inter"].dt.year >= annee_recente - 1]
        self.p_chir = recente["praticien"].value_counts(normalize=True)
        self.p_jour = {c: g["jour_semaine"].value_counts(normalize=True).reindex(range(5), fill_value=0)
                       for c, g in recente.groupby("praticien")}
        self.par_chir = {c: g for c, g in self.recente.groupby("praticien")}
        self.par_type_chir = {k: g for k, g in base.groupby(["type_interv", "praticien"])}
        self.par_type = {k: g for k, g in base.groupby("type_interv")}

    def chirurgiens(self, autorises=None):
        p = self.p_chir
        if autorises is not None:
            p = p[p.index.isin(autorises)]
        return p / p.sum()

    def _tirer(self, serie, rng):
        """Tire une valeur selon sa fréquence dans la série."""
        vc = serie.value_counts(normalize=True, dropna=False)
        return vc.index[rng.choice(len(vc), p=vc.values)]

    def patient(self, chir, rng, type_interv=None):
        """Un patient inventé pour ce chirurgien (profil seulement)."""
        g_chir = self.par_chir.get(chir)
        if g_chir is None or not len(g_chir):
            g_chir = self.base[self.base["praticien"] == chir]
        t = type_interv or self._tirer(g_chir["type_interv"], rng)
        g_tc = self.par_type_chir.get((t, chir))
        g_t = self.par_type.get(t, g_chir)
        ref = g_tc if g_tc is not None and len(g_tc) >= 3 else g_t
        cim = self._tirer(ref["cim"], rng)
        sexe = int(self._tirer(g_t["sexe"], rng))
        age = float(np.clip(g_t["age"].iloc[rng.integers(len(g_t))] + rng.normal(0, 3), 1, 100))
        anesth = ref.iloc[rng.integers(len(ref))]
        rng.integers(len(ref))   # tirage conservé : les listes générées restent identiques à graine égale
        return {
            "chir": chir, "type_interv": t, "cim": cim, "sexe": sexe, "age": round(age),
            "anesth_type": anesth["anesth_type"], "anesth_locoreg": anesth["anesth_locoreg"],
        }

    def generer(self, x, semaines, date_ouverture, graine=0, autorises=None):
        """x patients dont la date souhaitée tombe dans les `semaines` premières semaines."""
        rng = np.random.default_rng(graine)
        p = self.chirurgiens(autorises)
        chirs = rng.choice(p.index.to_numpy(), size=int(x), p=p.values)
        debut = pd.Timestamp(date_ouverture).normalize()
        patients = []
        for k, chir in enumerate(chirs):
            pat = self.patient(chir, rng)
            pj = self.p_jour.get(chir)
            j = int(rng.choice(5, p=(pj / pj.sum()).values)) if pj is not None and pj.sum() > 0 else int(rng.integers(5))
            semaine = int(rng.integers(int(semaines)))
            pat["date_souhait"] = (debut + pd.Timedelta(days=7 * semaine + j)).strftime("%Y-%m-%d")
            pat["id"] = f"G{k + 1:04d}"
            pat["source"] = "généré"
            patients.append(pat)
        patients.sort(key=lambda q: (q["date_souhait"], q["chir"]))
        return patients


def completer_predictions(patients, predicteur, methode, params):
    """Ajoute µ, σ, P(ambulatoire), A, L et w_i prédits à chaque patient."""
    from .scenario import poids_retard
    if not patients:
        return patients
    X = pd.DataFrame([{
        "type_interv": p["type_interv"], "praticien": p["chir"],
        "anesth_type": p.get("anesth_type") or "Non renseigné", "anesth_locoreg": p.get("anesth_locoreg") or "Aucune",
        "cim_chapitre": (p.get("cim") or "?")[:1], "cim3": (p.get("cim") or "?")[:3],
        "age": float(p.get("age") or 50), "sexe": int(p.get("sexe") or 1),
    } for p in patients])
    pred = predicteur.predire(X, methode)
    for p, (_, r) in zip(patients, pred.iterrows()):
        p.update({"duree": round(float(r["duree"]), 1), "sigma": round(float(r["sigma"]), 1),
                  "p_ambu": round(float(r["p_ambu"]), 3), "ambu": int(r["ambu"]), "nuits": int(r["nuits"]),
                  "poids_retard": poids_retard(p.get("cim"), params)})
    return patients


class ListeInitiale:
    """Liste des patients à programmer à l'ouverture (modifiable par le médecin),
    sauvegardée dans data/processed/patients_initiaux.json."""

    def __init__(self):
        self.parametres, self.patients = {}, []
        if FICHIER.exists():
            contenu = json.loads(FICHIER.read_text(encoding="utf-8"))
            self.parametres, self.patients = contenu.get("parametres", {}), contenu.get("patients", [])

    def sauver(self):
        DOSSIER_PROCESSED.mkdir(parents=True, exist_ok=True)
        FICHIER.write_text(json.dumps({"parametres": self.parametres, "patients": self.patients},
                                      ensure_ascii=False, indent=0), encoding="utf-8")

    def remplacer(self, patients, parametres):
        self.patients, self.parametres = patients, parametres
        self.sauver()

    def ajouter(self, patient):
        n = 1 + max([int(p["id"][1:]) for p in self.patients if p["id"].startswith("M")] or [0])
        patient["id"] = f"M{n:04d}"
        patient["source"] = "ajouté"
        patient["ajoute_le"] = datetime.now().isoformat(timespec="seconds")
        self.patients.append(patient)
        self.patients.sort(key=lambda q: (q["date_souhait"], q["chir"]))
        self.sauver()
        return patient

    def supprimer(self, pid):
        self.patients = [p for p in self.patients if p["id"] != pid]
        self.sauver()
