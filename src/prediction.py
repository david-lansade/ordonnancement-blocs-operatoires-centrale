"""Prédiction, pour chaque patient, de la durée opératoire (µ, σ), du caractère
ambulatoire (A) et du nombre de nuits (L).

Deux méthodes, même interface predire(df) -> DataFrame(duree, sigma, p_ambu, ambu, nuits) :
- EtudeStatistique : moyenne des cas passés du même type d'intervention et du même
  chirurgien, ou du type seul s'il y a trop peu de cas, sinon moyenne globale ;
- ModeleML : gradient boosting (scikit-learn, HistGradientBoosting) avec le type
  d'intervention, le chirurgien, l'anesthésie, l'âge, le sexe et le diagnostic.

Protocole (section 7) : apprentissage sur 2019-2021, test sur 2022.
Le modèle ne fait que remplir duree, sigma, ambu, nuits dans d : evaluer ne change pas.
"""
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .chargement import DOSSIER_PROCESSED

MIN_CAS_TYPE_CHIR = 5     # en dessous : on passe au type seul
MIN_CAS_TYPE = 3          # en dessous : moyenne globale
SIGMA_MIN = 10.0          # plancher de l'écart-type d'une durée (minutes)

VARIABLES_CAT = ["type_interv", "praticien", "anesth_type", "anesth_locoreg", "cim_chapitre", "cim3"]
VARIABLES_NUM = ["age", "sexe"]


def decoupage(df):
    """Apprentissage 2019-2021 / test 2022."""
    annee = df["date_inter"].dt.year
    return df[annee <= 2021], df[annee == 2022]


class EtudeStatistique:
    """Moyennes par (type, chirurgien) -> type -> global."""

    def fit(self, df):
        self.globale = {
            "duree": df["duree_op"].mean(), "sigma": df["duree_op"].std(),
            "p_ambu": df["ambu"].mean(), "nuits": df["nuits"].mean(),
            "nuits_hosp": df.loc[df["ambu"] == 0, "nuits"].mean(),
        }
        self.tables = {}
        for cle in (["type_interv", "praticien"], ["type_interv"]):
            g = df.groupby(cle)
            t = pd.DataFrame({
                "n": g.size(),
                "duree": g["duree_op"].mean(), "sigma": g["duree_op"].std(),
                "p_ambu": g["ambu"].mean(), "nuits": g["nuits"].mean(),
            })
            t["nuits_hosp"] = df[df["ambu"] == 0].groupby(cle)["nuits"].mean()
            self.tables[tuple(cle)] = t
        return self

    def predire(self, df):
        n = len(df)
        res = pd.DataFrame(index=df.index, columns=["duree", "sigma", "p_ambu", "nuits_hosp", "nuits_moy"], dtype=float)
        res["n_cas"] = 0
        res["niveau"] = "global"
        for cle, minimum in ((("type_interv",), MIN_CAS_TYPE), (("type_interv", "praticien"), MIN_CAS_TYPE_CHIR)):
            t = self.tables[cle]
            t = t[t["n"] >= minimum]
            idx = pd.MultiIndex.from_frame(df[list(cle)]) if len(cle) > 1 else pd.Index(df[cle[0]])
            valeurs = t.reindex(idx)
            ok = valeurs["n"].notna().values
            for col_res, col_t in (("duree", "duree"), ("sigma", "sigma"), ("p_ambu", "p_ambu"),
                                   ("nuits_hosp", "nuits_hosp"), ("nuits_moy", "nuits")):
                v = valeurs[col_t].values
                garde = ok & ~np.isnan(v.astype(float))
                res.loc[res.index[garde], col_res] = v[garde]
            res.loc[res.index[ok], "n_cas"] = valeurs["n"].values[ok]
            res.loc[res.index[ok], "niveau"] = " + ".join("type" if c == "type_interv" else "chirurgien" for c in cle)
        for col, g in (("duree", "duree"), ("sigma", "sigma"), ("p_ambu", "p_ambu"),
                       ("nuits_hosp", "nuits_hosp"), ("nuits_moy", "nuits")):
            res[col] = res[col].fillna(self.globale[g])
        res["sigma"] = res["sigma"].clip(lower=SIGMA_MIN)
        return _finaliser(res, n)


def _finaliser(res, n):
    """Décision ambulatoire (seuil 0,5) et nuits (0 si ambulatoire, au moins 1 sinon)."""
    res["ambu"] = (res["p_ambu"] >= 0.5).astype(int)
    res["nuits"] = np.where(res["ambu"] == 1, 0, np.maximum(1, np.round(res["nuits_hosp"]))).astype(int)
    return res


class ModeleML:
    """Gradient boosting avec variables catégorielles natives."""

    def __init__(self, graine=0):
        self.graine = graine

    def _encoder(self, df):
        X = pd.DataFrame(index=df.index)
        for c in VARIABLES_CAT:
            X[c] = pd.Categorical(df[c].where(df[c].isin(self.modalites[c]), "Autre"), categories=self.modalites[c])
        for c in VARIABLES_NUM:
            X[c] = df[c].astype(float)
        return X

    def fit(self, df):
        self.modalites = {}
        for c in VARIABLES_CAT:
            freq = df[c].value_counts()
            self.modalites[c] = list(freq[freq >= 5].index[:250]) + ["Autre"]
        X = self._encoder(df)
        opts = dict(categorical_features="from_dtype", random_state=self.graine, max_iter=400,
                    learning_rate=0.05, early_stopping=True, validation_fraction=0.15, l2_regularization=1.0)
        a = df["duree_op"].notna().values
        self.m_duree = HistGradientBoostingRegressor(loss="squared_error", **opts).fit(X[a], df.loc[a, "duree_op"])
        self.m_ambu = HistGradientBoostingClassifier(**opts).fit(X, df["ambu"])
        h = (df["ambu"] == 0).values
        self.m_nuits = HistGradientBoostingRegressor(loss="absolute_error", **opts).fit(X[h], df.loc[h, "nuits"])
        # σ : écart-type des résidus par type d'intervention (plancher SIGMA_MIN)
        residus = df.loc[a, "duree_op"] - self.m_duree.predict(X[a])
        self.sigma_type = residus.groupby(df.loc[a, "type_interv"]).std()
        self.n_type = residus.groupby(df.loc[a, "type_interv"]).size()
        self.sigma_global = float(residus.std())
        return self

    def predire(self, df):
        X = self._encoder(df)
        res = pd.DataFrame(index=df.index)
        res["duree"] = np.clip(self.m_duree.predict(X), 5, None)
        s = df["type_interv"].map(self.sigma_type.where(self.n_type >= MIN_CAS_TYPE))
        res["sigma"] = s.fillna(self.sigma_global).clip(lower=SIGMA_MIN).values
        res["p_ambu"] = self.m_ambu.predict_proba(X)[:, 1]
        res["nuits_hosp"] = self.m_nuits.predict(X)
        return _finaliser(res, len(df))


def mesurer(df_test, pred):
    """Erreurs sur le test : MAE durée, MAE nuits, taux de bonnes réponses ambulatoire."""
    a = df_test["duree_op"].notna()
    return {
        "mae_duree": round(float((pred.loc[a, "duree"] - df_test.loc[a, "duree_op"]).abs().mean()), 2),
        "mae_nuits": round(float((pred["nuits"] - df_test["nuits"]).abs().mean()), 3),
        "precision_ambu": round(float((pred["ambu"] == df_test["ambu"]).mean()), 4),
    }


def comparer_methodes(df, graine=0):
    """Erreurs sur 2022 (apprentissage 2019-2021) : moyenne globale, étude statistique
    et ML, chacun appris avec ou sans les mois COVID (colonne covid)."""
    train, test = decoupage(df)
    moy = pd.DataFrame(index=test.index)
    moy["duree"] = train["duree_op"].mean()
    moy["p_ambu"] = train["ambu"].mean()
    moy["ambu"] = int(train["ambu"].mean() >= 0.5)
    moy["nuits"] = round(train["nuits"].mean())
    train_sc = train[train["covid"] == 0]   # sans les mois COVID
    resultats = {"Moyenne globale": mesurer(test, moy)}
    for nom, base in (("", train), (" sans COVID", train_sc)):
        resultats["Étude statistique" + nom] = mesurer(test, EtudeStatistique().fit(base).predire(test))
        resultats["Machine learning" + nom] = mesurer(test, ModeleML(graine).fit(base).predire(test))
    resultats["_apprentissage"] = {"avec COVID": int(len(train)), "sans COVID": int(len(train_sc)),
                                   "test 2022": int(len(test))}
    return resultats


class Predicteur:
    """Prédicteur (étude statistique + ML) utilisé par l'application, sauvegardé dans
    data/processed/ :
    - modeles_complet.joblib : appris sur 2019-2022, mois COVID compris ;
    - modeles_complet_sans_covid.joblib : appris sur 2019-2022 sans les mois COVID ;
    - modeles_2022-01-01.joblib : appris sur 2019-2021 (script d'évaluation historique)."""

    def __init__(self, stat, ml, metriques, jusqu_a, sans_covid=False):
        self.stat, self.ml, self.metriques, self.jusqu_a = stat, ml, metriques, jusqu_a
        self.sans_covid = sans_covid

    @staticmethod
    def fichier(jusqu_a, sans_covid=False):
        return DOSSIER_PROCESSED / f"modeles_{jusqu_a or 'complet'}{'_sans_covid' if sans_covid else ''}.joblib"

    @classmethod
    def entrainer(cls, df, jusqu_a=None, graine=0, metriques=None, sans_covid=False):
        """jusqu_a : date limite (exclue) des données d'apprentissage (None = toute la base) ;
        sans_covid : retire les mois COVID de l'apprentissage."""
        if metriques is None:
            metriques = comparer_methodes(df, graine)
        base = df if jusqu_a is None else df[df["date_inter"] < pd.Timestamp(jusqu_a)]
        if sans_covid:
            base = base[base["covid"] == 0]
        p = cls(EtudeStatistique().fit(base), ModeleML(graine).fit(base), metriques, jusqu_a, sans_covid)
        DOSSIER_PROCESSED.mkdir(parents=True, exist_ok=True)
        joblib.dump(p, cls.fichier(jusqu_a, sans_covid))
        return p

    @classmethod
    def charger(cls, df=None, jusqu_a=None, sans_covid=False, metriques=None):
        f = cls.fichier(jusqu_a, sans_covid)
        if f.exists():
            p = joblib.load(f)
            if "Machine learning sans COVID" in (p.metriques or {}):   # cache à jour
                return p
        if df is None:
            raise FileNotFoundError("Modèles non entraînés")
        return cls.entrainer(df, jusqu_a, metriques=metriques, sans_covid=sans_covid)

    def predire(self, df, methode="ml"):
        return (self.ml if methode == "ml" else self.stat).predire(df)

    def predire_patient(self, infos, methode="ml"):
        """infos : dict (type_interv, praticien, age, sexe, cim, anesth_type, anesth_locoreg)."""
        cim = (infos.get("cim") or "").strip().upper()
        ligne = pd.DataFrame([{
            "type_interv": infos.get("type_interv") or "Inconnu",
            "praticien": infos.get("praticien"),
            "anesth_type": infos.get("anesth_type") or "Non renseigné",
            "anesth_locoreg": infos.get("anesth_locoreg") or "Aucune",
            "cim_chapitre": cim[:1] or "?",
            "cim3": cim[:3] or "?",
            "age": float(infos.get("age") or 50),
            "sexe": int(infos.get("sexe") or 1),
        }])
        p = self.predire(ligne, methode).iloc[0]
        s = self.stat.predire(ligne).iloc[0]
        return {
            "duree": round(float(p["duree"]), 1),
            "sigma": round(float(p["sigma"]), 1),
            "p_ambu": round(float(p["p_ambu"]), 3),
            "ambu": int(p["ambu"]),
            "nuits": int(p["nuits"]),
            "methode": methode,
            # éléments d'explication : ce que disent les cas passés semblables
            "stat_duree": round(float(s["duree"]), 1),
            "stat_p_ambu": round(float(s["p_ambu"]), 3),
            "stat_nuits": int(s["nuits"]),
            "n_cas_similaires": int(s["n_cas"]),
            "niveau_similarite": s["niveau"],
        }
