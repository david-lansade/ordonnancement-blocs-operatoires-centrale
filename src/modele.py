"""Modèle d'optimisation : dictionnaire des données fixes d, fonction objectif evaluer.

Solution : vecteur d'entiers, solution[i] = numéro de la vacation du patient i.

Clés de d (section 6 du CLAUDE.md, inchangées) :
    nb_jours, pers[t], cap_lits[t], cap_ambu[t], jour[v], capacite[v],
    duree[i], sigma[i], ambu[i], nuits[i], limite[i], admissibles[i]

Clés AJOUTÉES (toutes facultatives : si elles sont absentes, evaluer donne
exactement le résultat de la version de référence, donc le code des autres
membres du groupe continue de fonctionner) :
    poids            dict {"W1", "W1b", "W2", "W3", "W4", "W5", "W6"} qui remplace les
                     poids par défaut (réglés depuis l'onglet Technique)
    souhait[i]       s_i, jour souhaité par le patient / le chirurgien. S'il est présent,
                     F contient le terme de confort d'écart à la date souhaitée :
                     W6 * Σ_i [max(0, s_i - j_i) + max(0, j_i - s_i - τ_i)]
                     nul dans la plage [s_i, s_i + τ_i] ; indépendant de la limite d_i
    tolerance[i]     τ_i, jours acceptés après s_i sans pénalité W6 (défaut 0)
    poids_retard[i]  w_i, urgence médicale du diagnostic dans le terme de retard :
                     W3 * Σ_i w_i max(0, j_i - d_i)   (défaut 1)
                     où limite[i] = d_i est la limite MÉDICALE (date de la demande
                     + délai médical maximal), sans lien avec la date souhaitée
    lits_reserves[t] lits occupés la nuit t par les urgences (défaut 0) : ils
                     s'ajoutent à n_t, l'occupation est lissée « urgences comprises »
    places_reservees[t]  idem pour les places ambulatoires (défaut 0)
    z_beta           coefficient de la marge d'aléa des salles (défaut Z_BETA)
    penaliser_capacites  True pour ajouter les termes W4 / W5 (défaut False :
                     un d construit par les autres membres n'est pas pénalisé)
    terme_salles     "regroupement" : le terme W2 devient le temps de bloc perdu dans les
                     vacations utilisées (choix de l'utilisateur, 2026-10-13), voir plus bas.
                     Absent (défaut) : terme de référence W2 * Σ_v (H_v - c_v)²
    epsilon_salles   ε, poids du guidage dans ce terme (défaut EPSILON_SALLES = 0,5)
Clés d'information (non lues par evaluer) :
    chir[i], vac_chir[v], vac_salle[v], vac_debut[v]
    rotation         minutes de rotation entre deux patients d'une même vacation (défaut 0) ;
                     lue seulement par details() pour le taux de remplissage des salles :
                     occupation d'une vacation à n patients = Σ µ_i + rotation × (n − 1).
                     Mise à 0 quand µ contient déjà la rotation : rotation_dans_mu (minutes
                     ajoutées à chaque µ_i, défaut 0) est alors > 0 ; information seulement.

Termes de capacité (remarque de la section 6 : « à ajouter sous forme de pénalité ») :
    W4 * [Σ_t max(0, n_t - CL(t))² + Σ_t max(0, a_t - CA(t))²]
    W5 * Σ_v max(0, Σ µ_i + z_β √(Σ σ_i²) - H_v)²
Terme d'écart à la date souhaitée (ajout, clés souhait et tolerance) :
    W6 * Σ_i [max(0, s_i - j_i) + max(0, j_i - s_i - τ_i)]
Terme des salles « regroupement » (ajout, clé terme_salles), c_v = Σ_{i∈v} µ_i :
    W2 * Σ_{v utilisée} [(H_v - c_v)⁺ + ε c_v (H_v - c_v)⁺ / H_v]
    (vacation utilisée = au moins un patient ; une vacation vide vaut 0.)
    Pourquoi : tous les patients sont placés, chacun dans les vacations de son chirurgien, donc
    Σ c_v est fixé et le remplissage global ne dépend pas de la solution. Le terme de référence
    Σ (H_v - c_v)² pousse alors à étaler (même temps vide partout). Ici :
      - (H_v - c_v)⁺ : temps vide d'une vacation ouverte ; une vacation presque vide coûte
        presque toute sa durée, une vacation vide est libérée (rendue ou fermée) et ne coûte rien ;
      - ε c_v (H_v - c_v)⁺ / H_v : guidage ; sans lui le terme ne change pas tant qu'une vacation
        n'est pas complètement vide, avec lui chaque déplacement vers la vacation la plus
        remplie est récompensé (terme concave en c_v) ;
      - au-delà de H_v le terme vaut 0 : le débordement reste traité par W5.
    Poids par défaut W2_REGROUPEMENT = 0,05 par minute (libérer une vacation de 5 h rapporte 15).
"""
import math

import numpy as np
import pandas as pd

# Poids provisoires (section 6)
W1 = 1.0      # lissage des lits
W1b = 1.0     # lissage des places ambulatoires
W2 = 0.001    # remplissage des salles (minutes²)
W3 = 5.0      # retard (jours)
# Poids des pénalités de capacité (ajout)
W4 = 50.0     # dépassement de lits / places (par lit²)
W5 = 0.05     # dépassement de la marge de salle (par minute²)
W6 = 0.5      # écart à la date souhaitée hors tolérance (par jour), volontairement faible
Z_BETA = 1.0  # marge d'aléa : P(durée totale <= H_v) ≈ 84 % si les durées sont normales
# Terme des salles « regroupement » (clé terme_salles, choix de l'utilisateur, 2026-10-13)
W2_REGROUPEMENT = 0.05   # W2 de ce terme (par minute de temps vide)
EPSILON_SALLES = 0.5     # ε : poids du guidage


def _opt(d, cle, defaut):
    """Clé facultative de d (None ou absente -> valeur par défaut)."""
    x = d.get(cle)
    return defaut if x is None else x


def _regroupement(d):
    return d.get("terme_salles") == "regroupement"


def lire_poids(d):
    w2 = W2_REGROUPEMENT if _regroupement(d) else W2
    p = {"W1": W1, "W1b": W1b, "W2": w2, "W3": W3, "W4": W4, "W5": W5, "W6": W6}
    p.update(_opt(d, "poids", {}))
    return p


def cout_salle(H, c, n, eps=EPSILON_SALLES):
    """Terme des salles « regroupement » d'une vacation de durée H, chargée de c minutes par n patients :
    (H - c)⁺ + ε c (H - c)⁺ / H si elle est utilisée (n > 0), 0 si elle est vide."""
    if n <= 0 or H <= 0:
        return 0.0
    return max(0.0, H - c) * (1.0 + eps * c / H)


def evaluer(solution, d):
    """Fonction objectif F (à minimiser). Même signature et même résultat que la
    version de référence quand les clés ajoutées sont absentes."""
    p = lire_poids(d)
    T = d["nb_jours"]
    charge = [0] * len(d["capacite"])   # minutes programmées par vacation
    var = [0.0] * len(d["capacite"])    # Σ σ_i² par vacation (marge z_β)
    lits = list(_opt(d, "lits_reserves", [0] * T))          # lits occupés chaque nuit
    places = list(_opt(d, "places_reservees", [0] * T))     # places ambulatoires chaque jour
    sigma = d.get("sigma")
    for i, v in enumerate(solution):
        charge[v] += d["duree"][i]
        if sigma is not None:
            var[v] += sigma[i] ** 2
        if d["ambu"][i] == 1:           # ambulatoire : une place, un jour
            places[d["jour"][v]] += 1
        else:                           # hospitalisé : un lit par nuit
            debut, nuits = d["jour"][v], d["nuits"][i]
            if (debut + nuits) % 7 == 6:    # sortie un dimanche : reportée au lundi
                nuits += 1
            for t in range(debut, min(debut + nuits, T)):
                lits[t] += 1
    # cibles proportionnelles au personnel (même total, forme du planning infirmier)
    cible_lits = [sum(lits) * e / sum(d["pers"]) for e in d["pers"]]
    poids_j = [e if C > 0 else 0 for e, C in zip(d["pers"], d["cap_ambu"])]  # jours ouverts
    cible_places = [sum(places) * q / sum(poids_j) for q in poids_j]
    p_lits = sum((n - c) ** 2 for n, c in zip(lits, cible_lits))
    p_places = sum((a - c) ** 2 for a, c in zip(places, cible_places))
    if _regroupement(d):   # temps de bloc perdu dans les vacations utilisées + guidage
        eps = _opt(d, "epsilon_salles", EPSILON_SALLES)
        nb = [0] * len(charge)
        for v in solution:
            nb[v] += 1
        p_salles = sum(cout_salle(H, c, n, eps) for H, c, n in zip(d["capacite"], charge, nb))
    else:
        p_salles = sum((H - c) ** 2 for H, c in zip(d["capacite"], charge))
    w = _opt(d, "poids_retard", [1.0] * len(solution))
    p_retard = sum(w[i] * max(0, d["jour"][v] - d["limite"][i]) for i, v in enumerate(solution))
    F = p["W1"] * p_lits + p["W1b"] * p_places + p["W2"] * p_salles + p["W3"] * p_retard
    souhait = d.get("souhait")
    if souhait is not None:   # écart à la date souhaitée, nul dans la plage [s_i, s_i + τ_i]
        tol = _opt(d, "tolerance", [0] * len(solution))
        F += p["W6"] * sum(max(0, souhait[i] - d["jour"][v]) + max(0, d["jour"][v] - souhait[i] - tol[i])
                           for i, v in enumerate(solution))
    if d.get("penaliser_capacites", False):
        F += p["W4"] * _depassement_hebergement(lits, places, d) + p["W5"] * _depassement_salles(charge, var, d)
    return F


def _depassement_hebergement(lits, places, d):
    return (sum(max(0, n - C) ** 2 for n, C in zip(lits, d["cap_lits"]))
            + sum(max(0, a - C) ** 2 for a, C in zip(places, d["cap_ambu"])))


def _depassement_salles(charge, var, d):
    z = d.get("z_beta", Z_BETA)
    return sum(max(0.0, c + z * math.sqrt(s) - H) ** 2 for c, s, H in zip(charge, var, d["capacite"]))


def details(solution, d):
    """Décomposition de F et indicateurs, pour l'affichage et la comparaison."""
    p = lire_poids(d)
    ev = EvaluateurVect(d)
    solution = np.asarray(solution, dtype=np.int64)
    r = ev.calculer(solution)
    termes = {
        "lits": p["W1"] * r["p_lits"], "places": p["W1b"] * r["p_places"],
        "salles": p["W2"] * r["p_salles"], "retard": p["W3"] * r["p_retard"],
        "capacite_hebergement": p["W4"] * r["p_heberg"] if ev.penaliser else 0.0,
        "marge_salles": p["W5"] * r["p_marge"] if ev.penaliser else 0.0,
        "ecart_souhait": p["W6"] * r["p_ecart"],
    }
    lits, places, cl, cp = r["lits"], r["places"], r["cible_lits"], r["cible_places"]
    ouverts = np.asarray(d["cap_ambu"]) > 0
    charge, H = r["charge"], np.asarray(d["capacite"], float)
    # occupation des salles rotations comprises : (n − 1) rotations dans une vacation à n patients
    nb_v = np.bincount(solution, minlength=len(H)) if len(solution) else np.zeros(len(H))
    occupe = charge + float(_opt(d, "rotation", 0.0)) * np.maximum(nb_v - 1, 0)
    jour_i = np.asarray(d["jour"])[solution]
    retard = np.maximum(0, jour_i - np.asarray(d["limite"], dtype=np.int64))
    z = d.get("z_beta", Z_BETA)
    if d.get("souhait") is not None and len(solution):
        s_i = np.asarray(d["souhait"], dtype=np.int64)
        fin_i = s_i + np.asarray(_opt(d, "tolerance", np.zeros(len(s_i))), dtype=np.int64)
        avance, report = np.maximum(0, s_i - jour_i), np.maximum(0, jour_i - fin_i)
        ecart = {"patients_avances": int(np.sum(avance > 0)), "patients_dans_tolerance": int(np.sum((avance == 0) & (report == 0))),
                 # part des patients opérés à la date souhaitée ou dans la tolérance [s_i, s_i + τ_i]
                 "part_dans_tolerance": round(float(np.mean((avance == 0) & (report == 0))), 4),
                 "patients_reportes": int(np.sum(report > 0)), "ecart_moyen_souhait": round(float((avance + report).mean()), 2)}
    else:
        ecart = {}
    return {
        "F": float(sum(termes.values())),
        "termes": {k: round(float(v), 3) for k, v in termes.items()},
        "lits": lits.round(2).tolist(), "cible_lits": cl.round(2).tolist(),
        "places": places.round(2).tolist(), "cible_places": cp.round(2).tolist(),
        "cap_lits": list(d["cap_lits"]), "cap_ambu": list(d["cap_ambu"]),
        "charge": charge.round(1).tolist(),
        "indicateurs": {
            "ecart_type_lits": round(float(np.std(lits - cl)), 2),
            "ecart_max_lits": round(float(np.max(np.abs(lits - cl))), 2),
            "lits_max": round(float(lits.max()), 1),
            "lits_moyens": round(float(lits.mean()), 2),
            "lits_moyens_week_end": round(float(lits[np.arange(len(lits)) % 7 >= 5].mean()), 2),
            "lits_moyens_semaine": round(float(lits[np.arange(len(lits)) % 7 < 5].mean()), 2),
            "lits_max_semaine": round(float(lits[np.arange(len(lits)) % 7 < 5].max()), 1),
            "lits_max_week_end": round(float(lits[np.arange(len(lits)) % 7 >= 5].max()), 1),
            "ecart_type_lits_semaine": round(float(np.std(lits[np.arange(len(lits)) % 7 < 5])), 2),
            "ecart_type_lits_week_end": round(float(np.std(lits[np.arange(len(lits)) % 7 >= 5])), 2),
            "places_moyennes": round(float(places[ouverts].mean()), 2) if ouverts.any() else 0.0,
            "ecart_type_places_semaine": round(float(np.std(places[ouverts])), 2) if ouverts.any() else 0.0,
            "ecart_type_places": round(float(np.std((places - cp)[ouverts])), 2) if ouverts.any() else 0.0,
            "places_max": round(float(places.max()), 1),
            "taux_remplissage_moyen": round(float(occupe.sum() / H.sum()), 3),
            "taux_remplissage_sans_rotation": round(float(charge.sum() / H.sum()), 3),
            "rotation_min": float(_opt(d, "rotation", 0.0)),
            "rotation_dans_mu": float(_opt(d, "rotation_dans_mu", 0.0)),
            "vacations_depassees": int(np.sum(charge + z * np.sqrt(r["var"]) > H + 1e-9)),
            "vacations_vides": int(np.sum(charge == 0)),
            # remplissage des seules vacations utilisées (au moins un patient) : c'est lui que le terme
            # « regroupement » fait monter, le remplissage global est fixé par la liste de patients
            "remplissage_vacations_utilisees": round(float(occupe[nb_v > 0].sum() / H[nb_v > 0].sum()), 3)
            if np.any(nb_v > 0) else 0.0,
            "nuits_au_dela_capacite": int(np.sum(lits > np.asarray(d["cap_lits"]) + 1e-9)),
            "jours_places_au_dela_capacite": int(np.sum(places > np.asarray(d["cap_ambu"]) + 1e-9)),
            "patients_en_retard": int(np.sum(retard > 0)),
            "retard_moyen_jours": round(float(retard.mean()), 2) if len(retard) else 0.0,
            "retard_max_jours": int(retard.max()) if len(retard) else 0,
            **ecart,
        },
    }


class EvaluateurVect:
    """Évaluation complète vectorisée (numpy), utilisée par l'algorithme génétique.
    Donne la même valeur que evaluer (vérifié dans tests/test_modele.py)."""

    def __init__(self, d):
        self.p = lire_poids(d)
        self.T = d["nb_jours"]
        self.V = len(d["capacite"])
        self.jour_v = np.asarray(d["jour"], dtype=np.int64)
        self.H = np.asarray(d["capacite"], dtype=float)
        self.duree = np.asarray(d["duree"], dtype=float)
        self.sig2 = np.asarray(d["sigma"], dtype=float) ** 2 if d.get("sigma") is not None else np.zeros(len(self.duree))
        self.ambu = np.asarray(d["ambu"], dtype=bool)
        self.nuits = np.asarray(d["nuits"], dtype=np.int64)
        self.limite = np.asarray(d["limite"], dtype=np.int64)
        self.w = np.asarray(_opt(d, "poids_retard", np.ones(len(self.duree))), dtype=float)
        self.souhait = None if d.get("souhait") is None else np.asarray(d["souhait"], dtype=np.int64)
        self.tol = np.asarray(_opt(d, "tolerance", np.zeros(len(self.duree))), dtype=np.int64)
        self.E = np.asarray(d["pers"], dtype=float)
        CA = np.asarray(d["cap_ambu"], dtype=float)
        self.Wp = np.where(CA > 0, self.E, 0.0)
        self.CL = np.asarray(d["cap_lits"], dtype=float)
        self.CA = CA
        self.res_lits = np.asarray(_opt(d, "lits_reserves", np.zeros(self.T)), dtype=float)
        self.res_places = np.asarray(_opt(d, "places_reservees", np.zeros(self.T)), dtype=float)
        self.z = d.get("z_beta", Z_BETA)
        self.penaliser = d.get("penaliser_capacites", False)
        self.regroupement = _regroupement(d)
        self.eps = float(_opt(d, "epsilon_salles", EPSILON_SALLES))
        self.hosp = ~self.ambu
        self.nb_evaluations = 0

    def _p_salles(self, sol, charge):
        if not self.regroupement:
            return float(((self.H - charge) ** 2).sum())
        nb = np.bincount(sol, minlength=self.V)
        ratio = np.divide(charge, self.H, out=np.zeros(self.V), where=self.H > 0)
        cout = np.maximum(0.0, self.H - charge) * (1.0 + self.eps * ratio)
        return float(np.where((nb > 0) & (self.H > 0), cout, 0.0).sum())

    def calculer(self, sol):
        j = self.jour_v[sol]
        charge = np.bincount(sol, weights=self.duree, minlength=self.V)
        var = np.bincount(sol, weights=self.sig2, minlength=self.V)
        places = self.res_places + np.bincount(j[self.ambu], minlength=self.T)[:self.T]
        deb = j[self.hosp]
        L = self.nuits[self.hosp]
        L = L + ((deb + L) % 7 == 6)
        fin = np.minimum(deb + L, self.T)
        diff = np.zeros(self.T + 1)
        np.add.at(diff, deb, 1)
        np.add.at(diff, fin, -1)
        lits = self.res_lits + np.cumsum(diff)[:self.T]
        cible_lits = lits.sum() * self.E / self.E.sum()
        cible_places = places.sum() * self.Wp / self.Wp.sum()
        return {
            "charge": charge, "var": var, "lits": lits, "places": places,
            "cible_lits": cible_lits, "cible_places": cible_places,
            "p_lits": float(((lits - cible_lits) ** 2).sum()),
            "p_places": float(((places - cible_places) ** 2).sum()),
            "p_salles": self._p_salles(sol, charge),
            "p_retard": float((self.w * np.maximum(0, j - self.limite)).sum()),
            "p_ecart": 0.0 if self.souhait is None else
            float((np.maximum(0, self.souhait - j) + np.maximum(0, j - self.souhait - self.tol)).sum()),
            "p_heberg": float((np.maximum(0, lits - self.CL) ** 2).sum() + (np.maximum(0, places - self.CA) ** 2).sum()),
            "p_marge": float((np.maximum(0, charge + self.z * np.sqrt(var) - self.H) ** 2).sum()),
        }

    def __call__(self, sol):
        self.nb_evaluations += 1
        r = self.calculer(sol)
        p = self.p
        F = p["W1"] * r["p_lits"] + p["W1b"] * r["p_places"] + p["W2"] * r["p_salles"] + p["W3"] * r["p_retard"]
        F += p["W6"] * r["p_ecart"]
        if self.penaliser:
            F += p["W4"] * r["p_heberg"] + p["W5"] * r["p_marge"]
        return F


class Evaluateur:
    """Évaluation incrémentale : on garde n_t, a_t, la charge des vacations et les
    sommes utiles, et on met à jour en O(L_i) quand un patient change de vacation.
    Utilisée par le recuit simulé, la recherche tabou et l'insertion en ligne.

    Pour p_lits = Σ_t (n_t - S E_t / ΣE)², on garde A = Σ n_t², B = Σ n_t E_t et
    S = Σ n_t : p_lits = A - 2 S B / ΣE + S² ΣE² / (ΣE)².
    Un patient non encore placé a la vacation -1 (utile pour l'insertion)."""

    def __init__(self, d, solution):
        self.d = d
        self.p = lire_poids(d)
        self.T = d["nb_jours"]
        self.jour = list(d["jour"])
        self.H = list(d["capacite"])
        self.duree = list(d["duree"])
        sig = d.get("sigma")
        self.sig2 = [s * s for s in sig] if sig is not None else [0.0] * len(self.duree)
        self.ambu = list(d["ambu"])
        self.nuits = list(d["nuits"])
        self.limite = list(d["limite"])
        self.w = list(_opt(d, "poids_retard", [1.0] * len(self.duree)))
        self.souhait = None if d.get("souhait") is None else list(d["souhait"])
        self.tol = list(_opt(d, "tolerance", [0] * len(self.duree)))
        self.E = [float(e) for e in d["pers"]]
        self.Wp = [e if C > 0 else 0.0 for e, C in zip(self.E, d["cap_ambu"])]
        self.sE, self.sE2 = sum(self.E), sum(e * e for e in self.E)
        self.sW, self.sW2 = sum(self.Wp), sum(q * q for q in self.Wp)
        self.CL = list(d["cap_lits"])
        self.CA = list(d["cap_ambu"])
        self.z = d.get("z_beta", Z_BETA)
        self.penaliser = d.get("penaliser_capacites", False)
        self.regroupement = _regroupement(d)
        self.eps = float(_opt(d, "epsilon_salles", EPSILON_SALLES))
        self.sol = [-1] * len(self.duree)
        self.nb_evaluations = 0
        self._initialiser()
        for i, v in enumerate(solution):
            if v >= 0:
                self._placer(i, v)

    def _initialiser(self):
        T, V = self.T, len(self.H)
        self.lits = [float(x) for x in _opt(self.d, "lits_reserves", [0.0] * T)]
        self.places = [float(x) for x in _opt(self.d, "places_reservees", [0.0] * T)]
        self.charge = [0.0] * V
        self.var = [0.0] * V
        self.nb = [0] * V   # patients par vacation (vacation utilisée : nb > 0)
        self.A = sum(n * n for n in self.lits)
        self.B = sum(n * e for n, e in zip(self.lits, self.E))
        self.S = sum(self.lits)
        self.Ap = sum(a * a for a in self.places)
        self.Bp = sum(a * q for a, q in zip(self.places, self.Wp))
        self.Sp = sum(self.places)
        # vacations toutes vides : (H - 0)² chacune, ou 0 avec le terme « regroupement »
        self.p_salles = 0.0 if self.regroupement else sum(H * H for H in self.H)
        self.p_retard = 0.0
        self.p_ecart = 0.0
        self.p_heberg = (sum(max(0.0, n - C) ** 2 for n, C in zip(self.lits, self.CL))
                         + sum(max(0.0, a - C) ** 2 for a, C in zip(self.places, self.CA)))
        self.p_marge = sum(max(0.0, -H) ** 2 for H in self.H)

    def _nuits_de(self, i, v):
        debut, n = self.jour[v], self.nuits[i]
        if (debut + n) % 7 == 6:
            n += 1
        return debut, min(debut + n, self.T)

    def _marge(self, v):
        return max(0.0, self.charge[v] + self.z * math.sqrt(self.var[v]) - self.H[v]) ** 2

    def _modifier(self, i, v, s):
        """Ajoute (s = +1) ou retire (s = -1) le patient i de la vacation v."""
        mu = self.duree[i]
        c = self.charge[v]
        if self.regroupement:
            self.p_salles -= cout_salle(self.H[v], c, self.nb[v], self.eps)
        else:
            self.p_salles += (self.H[v] - c - s * mu) ** 2 - (self.H[v] - c) ** 2
        self.p_marge -= self._marge(v)
        self.charge[v] = c + s * mu
        self.nb[v] += s
        self.var[v] += s * self.sig2[i]
        if self.var[v] < 1e-9:
            self.var[v] = 0.0
        self.p_marge += self._marge(v)
        if self.regroupement:
            self.p_salles += cout_salle(self.H[v], self.charge[v], self.nb[v], self.eps)
        t0 = self.jour[v]
        self.p_retard += s * self.w[i] * max(0, t0 - self.limite[i])
        if self.souhait is not None:
            self.p_ecart += s * (max(0, self.souhait[i] - t0) + max(0, t0 - self.souhait[i] - self.tol[i]))
        if self.ambu[i] == 1:
            a = self.places[t0]
            self.Ap += 2 * a * s + 1
            self.Bp += s * self.Wp[t0]
            self.Sp += s
            C = self.CA[t0]
            self.p_heberg += max(0.0, a + s - C) ** 2 - max(0.0, a - C) ** 2
            self.places[t0] = a + s
        else:
            debut, fin = self._nuits_de(i, v)
            lits, E, CL = self.lits, self.E, self.CL
            for t in range(debut, fin):
                n = lits[t]
                self.A += 2 * n * s + 1
                self.B += s * E[t]
                C = CL[t]
                self.p_heberg += max(0.0, n + s - C) ** 2 - max(0.0, n - C) ** 2
                lits[t] = n + s
            self.S += s * (fin - debut)

    def _placer(self, i, v):
        self._modifier(i, v, +1)
        self.sol[i] = v

    def _retirer(self, i):
        v = self.sol[i]
        if v >= 0:
            self._modifier(i, v, -1)
            self.sol[i] = -1

    def valeur(self):
        p = self.p
        p_lits = self.A - 2 * self.S * self.B / self.sE + self.S ** 2 * self.sE2 / self.sE ** 2
        p_places = self.Ap - 2 * self.Sp * self.Bp / self.sW + self.Sp ** 2 * self.sW2 / self.sW ** 2
        F = p["W1"] * p_lits + p["W1b"] * p_places + p["W2"] * self.p_salles + p["W3"] * self.p_retard
        F += p["W6"] * self.p_ecart
        if self.penaliser:
            F += p["W4"] * self.p_heberg + p["W5"] * self.p_marge
        return F

    def termes(self):
        """Décomposition de F (termes pondérés), même clés que details()."""
        p = self.p
        p_lits = self.A - 2 * self.S * self.B / self.sE + self.S ** 2 * self.sE2 / self.sE ** 2
        p_places = self.Ap - 2 * self.Sp * self.Bp / self.sW + self.Sp ** 2 * self.sW2 / self.sW ** 2
        return {"lits": p["W1"] * p_lits, "places": p["W1b"] * p_places, "salles": p["W2"] * self.p_salles,
                "retard": p["W3"] * self.p_retard,
                "capacite_hebergement": p["W4"] * self.p_heberg if self.penaliser else 0.0,
                "marge_salles": p["W5"] * self.p_marge if self.penaliser else 0.0,
                "ecart_souhait": p["W6"] * self.p_ecart}

    def cible_lits(self, t):
        return self.S * self.E[t] / self.sE

    def cible_places(self, t):
        return self.Sp * self.Wp[t] / self.sW

    def deplacer(self, i, v):
        """Applique le mouvement « patient i -> vacation v »."""
        self._retirer(i)
        self._placer(i, v)

    def essayer(self, i, v):
        """Valeur de F si le patient i allait dans la vacation v (sans l'appliquer)."""
        self.nb_evaluations += 1
        ancien = self.sol[i]
        self.deplacer(i, v)
        F = self.valeur()
        self._retirer(i)
        if ancien >= 0:
            self._placer(i, ancien)
        return F

    def essayer_echange(self, i, k):
        """Valeur de F si les patients i et k échangeaient leurs vacations."""
        self.nb_evaluations += 1
        vi, vk = self.sol[i], self.sol[k]
        self.deplacer(i, vk)
        self.deplacer(k, vi)
        F = self.valeur()
        self.deplacer(i, vi)
        self.deplacer(k, vk)
        return F

    def echanger(self, i, k):
        vi, vk = self.sol[i], self.sol[k]
        self.deplacer(i, vk)
        self.deplacer(k, vi)

    def resynchroniser(self):
        """Recalcule toutes les sommes (évite la dérive des flottants)."""
        sol = list(self.sol)
        self.sol = [-1] * len(sol)
        self._initialiser()
        for i, v in enumerate(sol):
            if v >= 0:
                self._placer(i, v)


def construire_d(patients, vacations, nb_jours, alpha=0.6, cap_lits_semaine=25, cap_lits_week_end=18,
                 cap_ambu=16, feries=(), lits_urgences=None, places_urgences=None, poids=None,
                 z_beta=Z_BETA, penaliser_capacites=True, retirer_vacations_sans_patient=True,
                 terme_salles=None, epsilon_salles=None, urgences_dans_f=True):
    """Construit d à partir :
      - patients : DataFrame avec duree, sigma, ambu, nuits, limite (jour limite médicale),
        souhait et tolerance (facultatifs, terme W6), fenetre_min, fenetre_max (bornes dures
        des dates proposables), chir, poids_retard ;
      - vacations : DataFrame de grille.vacations_horizon (jour, capacite, chir, salle, ...).
    alpha : E_t le week-end et les jours fériés (E_t = 1 en semaine).
    lits_urgences / places_urgences : profil moyen par jour de la semaine (7 valeurs).
    terme_salles / epsilon_salles : clés facultatives du même nom (terme W2 « regroupement »).
    urgences_dans_f : True, le profil moyen des urgences s'ajoute à n_t / a_t (lits_reserves) ;
      False, il est seulement gardé comme prévision pour la simulation (urgences_prevues_lits / _places).
    Si un patient a une colonne v_fixe >= 0, sa vacation est imposée (planning en ligne).
    Renvoie (d, P, exclus, vac) : P = patients retenus (dans l'ordre des indices i),
    exclus = patients sans vacation admissible, vac = vacations retenues (ordre des v)."""
    feries = set(feries)
    vac = vacations.reset_index(drop=True)
    retirees = 0
    if retirer_vacations_sans_patient:
        # vacations d'un chirurgien qui n'a aucun patient dans l'instance : aucune
        # décision ne peut les remplir, elles n'ajouteraient qu'une constante à F
        garde_v = vac["chir"].isin(set(patients["chir"]))
        retirees = int((~garde_v).sum())
        vac = vac[garde_v].reset_index(drop=True)
    pers, cap_l, cap_a = [], [], []
    for t in range(nb_jours):
        we = t % 7 >= 5
        pers.append(alpha if (we or t in feries) else 1.0)
        # nuits du samedi, du dimanche et des jours fériés : capacité du week-end (moins de personnel, 2026-10-17)
        cap_l.append(cap_lits_week_end if (we or t in feries) else cap_lits_semaine)
        ferme = we or t in feries   # o_t = 0 : pas d'opération le week-end ni les jours fériés
        cap_a.append(0 if ferme else cap_ambu)
    par_chir = {c: g for c, g in vac.groupby("chir")}
    garde, admissibles, exclus = [], [], []
    for idx, p in patients.reset_index(drop=True).iterrows():
        if "v_fixe" in p and p["v_fixe"] >= 0:
            garde.append(idx)
            admissibles.append([int(p["v_fixe"])])
            continue
        g = par_chir.get(p["chir"])
        if g is None:
            exclus.append((idx, "chirurgien sans vacation"))
            continue
        dans_fenetre = g[(g["jour"] >= p["fenetre_min"]) & (g["jour"] <= p["fenetre_max"])]
        assez_long = dans_fenetre[dans_fenetre["capacite"] >= p["duree"] + z_beta * p["sigma"]]
        choix = assez_long if len(assez_long) else dans_fenetre
        if not len(choix):   # délai clinique impossible : toutes les vacations après la date au plus tôt
            choix = g[g["jour"] >= p["fenetre_min"]]
        if not len(choix):
            exclus.append((idx, "aucune vacation dans l'horizon"))
            continue
        garde.append(idx)
        admissibles.append([int(v) for v in choix.index])
    P = patients.reset_index(drop=True).loc[garde]
    d = {
        "nb_jours": nb_jours,
        "pers": pers, "cap_lits": cap_l, "cap_ambu": cap_a,
        "jour": vac["jour"].astype(int).tolist(),
        "capacite": vac["capacite"].astype(int).tolist(),
        "duree": P["duree"].astype(float).round(1).tolist(),
        "sigma": P["sigma"].astype(float).round(1).tolist(),
        "ambu": P["ambu"].astype(int).tolist(),
        "nuits": P["nuits"].astype(int).tolist(),
        "limite": P["limite"].astype(int).tolist(),
        "admissibles": admissibles,
        # clés ajoutées
        "poids_retard": P["poids_retard"].astype(float).tolist(),
        "z_beta": z_beta,
        "penaliser_capacites": penaliser_capacites,
        "vacations_retirees": retirees,
        # informations (non lues par evaluer)
        "chir": P["chir"].tolist(),
        "vac_chir": vac["chir"].tolist(), "vac_salle": vac["salle"].astype(int).tolist(),
        "vac_debut": vac["debut"].astype(int).tolist(),
    }
    if "souhait" in P:
        d["souhait"] = P["souhait"].astype(int).tolist()
        d["tolerance"] = (P["tolerance"] if "tolerance" in P else pd.Series(0, index=P.index)).fillna(0).astype(int).tolist()
    if poids:
        d["poids"] = dict(poids)
    if terme_salles is not None:
        d["terme_salles"] = terme_salles
    if epsilon_salles is not None:
        d["epsilon_salles"] = float(epsilon_salles)
    if lits_urgences is not None:
        d["lits_reserves" if urgences_dans_f else "urgences_prevues_lits"] =             [float(lits_urgences[t % 7]) for t in range(nb_jours)]
    if places_urgences is not None:
        d["places_reservees" if urgences_dans_f else "urgences_prevues_places"] =             [float(places_urgences[t % 7]) if cap_a[t] > 0 else 0.0 for t in range(nb_jours)]
    return d, P, exclus, vac
