"""Programme horaire d'une vacation : ordre de passage, heure d'entrée en salle,
heure de passage en SAS de pré-anesthésie et heure de convocation.

Il est calculé APRÈS l'attribution des vacations (il ne change pas F).

1. Ordre de passage : on minimise, pour les patients d'une vacation,

       C = p_ambu      Σ_{ambulatoires} (sortie de salle - début de la vacation) / 60
         + p_alternance × nombre de paires consécutives de même catégorie

   où une opération est « lourde » si sa durée prévue µ_i >= seuil, « légère » sinon.
   Le premier terme met les ambulatoires tôt (ils doivent pouvoir rentrer le soir), le
   second fait alterner le chirurgien entre grosses et petites interventions. Départ : ambulatoires d'abord (du
   plus court au plus long), puis échanges de deux patients tant que C diminue.

2. Entrée en salle : début de la vacation + Σ (µ_k + rotation) des patients précédents. La rotation
   n'est pas un réglage du programme horaire (seul réglage : « Rotation ajoutée à µ », 2026-10-20) :
   h["rotation"] est fourni par scenario.horaires_effectifs, 0 si µ contient déjà la rotation, sinon
   la rotation mesurée dans la base, comme dans la simulation.
3. Passage en SAS : entrée en salle - temps de SAS (plus long avec un bloc locorégional,
   posé dans le SAS : médianes 37 min avec bloc, 20 min sans, dans la base).
4. Convocation : passage en SAS - délai d'accueil (ambulatoire / hospitalisé), jamais
   avant l'heure de convocation la plus tôt.

Option « reordonner » : si elle est vraie (défaut), tout le programme d'une vacation est
recalculé à chaque arrivée d'un patient (meilleur ordre, mais les horaires déjà annoncés
peuvent bouger). Si elle est fausse, les horaires déjà attribués sont figés et un nouveau
patient passe après les patients figés (le programme n'est plus optimal, mais un horaire
communiqué au patient ne change plus).
"""
from itertools import permutations

HORAIRES_DEFAUT = {
    "poids_ambu": 1.0,            # ambulatoires tôt (par heure d'attente avant la sortie de salle)
    "poids_alternance": 2.0,      # par paire consécutive de même catégorie (lourde / légère)
    "seuil_lourde": 90,           # minutes : µ_i >= seuil -> opération lourde
    "sas_avec_bloc": 37,          # minutes en SAS avec bloc locorégional (médiane mesurée)
    "sas_sans_bloc": 20,          # minutes en SAS sans bloc (médiane mesurée)
    "accueil_ambu": 60,           # minutes entre la convocation et le SAS (à valider : non mesurable)
    "accueil_hospit": 90,
    "convocation_min": "06:30",   # heure de convocation la plus tôt
    "reordonner": True,           # False : les horaires déjà attribués ne bougent plus
}


def en_minutes(h):
    """'7:45', '7h45' ou 465 -> 465."""
    if isinstance(h, (int, float)):
        return int(h)
    h = str(h).lower().replace("h", ":")
    hh, _, mm = h.partition(":")
    return int(hh) * 60 + int(mm or 0)


def en_texte(m):
    m = int(round(m))
    return f"{m // 60}h{m % 60:02d}"


def a_un_bloc(p):
    """Anesthésie locorégionale (posée en SAS) : allonge le passage en SAS."""
    loco = (p.get("anesth_locoreg") or "").strip()
    return bool(loco) and loco not in ("Aucune", "Non renseigné") and not loco.startswith("AG")


def _cout(ordre, patients, debut, h, precedente=None):
    """precedente : catégorie (True = lourde) du patient qui passe juste avant, s'il est figé."""
    t, c = debut, 0.0
    for k in ordre:
        p = patients[k]
        fin = t + p["duree"]
        if p.get("ambu"):
            c += h["poids_ambu"] * (fin - debut) / 60
        lourde = p["duree"] >= h["seuil_lourde"]
        if precedente is not None and lourde == precedente:
            c += h["poids_alternance"]
        precedente = lourde
        t = fin + h.get("rotation", 0.0)
    return c


def ordonner(patients, debut, h, precedente=None):
    """Ordre de passage (liste d'indices) qui minimise le coût C."""
    n = len(patients)
    if n <= 1:
        return list(range(n))
    if n <= 6:   # petite vacation : on essaie tous les ordres
        return list(min(permutations(range(n)), key=lambda o: _cout(o, patients, debut, h, precedente)))
    # départ : ambulatoires d'abord, du plus court au plus long, puis hospitalisés
    ordre = sorted(range(n), key=lambda k: (not patients[k].get("ambu"), patients[k]["duree"]))
    meilleur = _cout(ordre, patients, debut, h, precedente)
    ameliore = True
    while ameliore:
        ameliore = False
        for a in range(n - 1):
            for b in range(a + 1, n):
                ordre[a], ordre[b] = ordre[b], ordre[a]
                c = _cout(ordre, patients, debut, h, precedente)
                if c < meilleur - 1e-9:
                    meilleur, ameliore = c, True
                else:
                    ordre[a], ordre[b] = ordre[b], ordre[a]
    return ordre


def programme(patients, debut, fin, h, figes=None):
    """Programme horaire d'une vacation. patients : dicts avec duree, ambu, anesth_locoreg.
    figes[k] : horaire figé du patient k ({entree, sas, convocation} en minutes) ou None ;
    les patients figés gardent leurs horaires, les autres passent après eux.
    Renvoie une liste (dans l'ordre de passage) de dicts : rang, indice, entree, sortie,
    sas, convocation (minutes), categorie, depasse (sortie après la fin de la vacation), fige."""
    h = {**HORAIRES_DEFAUT, "rotation": 0.0, **(h or {})}
    debut = en_minutes(debut)
    figes = figes or [None] * len(patients)
    conv_min = en_minutes(h["convocation_min"])
    res, t, precedente = [], debut, None
    # 1. patients figés : horaires inchangés, dans l'ordre de leur entrée en salle
    for k in sorted((k for k in range(len(patients)) if figes[k]), key=lambda k: figes[k]["entree"]):
        p, f = patients[k], figes[k]
        res.append({"indice": k, "entree": f["entree"], "sortie": f["entree"] + p["duree"], "sas": f["sas"],
                    "convocation": f["convocation"], "fige": True})
        t = max(t, f["entree"] + p["duree"] + h["rotation"])
        precedente = p["duree"] >= h["seuil_lourde"]
    # 2. patients libres : meilleur ordre, à la suite des patients figés
    libres = [k for k in range(len(patients)) if not figes[k]]
    for j in ordonner([patients[k] for k in libres], t, h, precedente):
        k = libres[j]
        p = patients[k]
        entree, sortie = t, t + p["duree"]
        sas = entree - (h["sas_avec_bloc"] if a_un_bloc(p) else h["sas_sans_bloc"])
        accueil = h["accueil_ambu"] if p.get("ambu") else h["accueil_hospit"]
        res.append({"indice": k, "entree": entree, "sortie": sortie, "sas": sas,
                    "convocation": max(conv_min, sas - accueil), "fige": False})
        t = sortie + h["rotation"]
    for rang, x in enumerate(res, 1):
        x["rang"] = rang
        x["categorie"] = "lourde" if patients[x["indice"]]["duree"] >= h["seuil_lourde"] else "légère"
        x["depasse"] = x["sortie"] > en_minutes(fin)
    return res
