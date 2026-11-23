"""Système multi-agents (mesa) : le bloc opératoire, le service d'hospitalisation (lits)
et l'ambulatoire exécutent un planning jour après jour et se coordonnent face aux aléas
(src/aleas.py). C'est l'« approche hybride » : une métaheuristique construit le planning
(statique), les agents l'adaptent pendant son exécution (dynamique).

Agents (un pas du modèle = une journée) :
- AgentBloc : opère les patients du jour avec les durées réelles, dans l'ordre de passage du
  programme horaire (src/horaires.ordonner, recalculé chaque jour) ; un patient n'est commencé
  que si sa fin prévue tient dans la vacation + tolérance, sinon il est reporté ; propose
  des créneaux (ses vacations où l'intervention tient), en commençant par celui qui fait le
  moins augmenter F ; comble les créneaux libérés par une annulation en avançant un patient
  plus tardif du même chirurgien, celui qui fait le moins augmenter F, si F n'augmente pas de plus
  de tolerance_comblement (src/aleas.py).
- AgentLits : connaît les lits disponibles chaque nuit (lits fermés compris) et les
  urgences déjà arrivées ; accepte un patient si toutes ses nuits restent sous la capacité
  moins une marge (pour les nuits futures, il compte au moins le profil moyen des
  urgences) ; quand une nuit est en surcapacité, il demande au bloc de déplacer des
  patients pas encore convoqués.
- AgentAmbulatoire : même rôle pour les places ambulatoires de chaque jour.

Protocole (de type « contract net ») : l'agent qui a un problème le signale au bloc ; le
bloc classe ses créneaux possibles selon la hausse de F qu'ils entraînent (F « à l'instant t »,
voir d_courant) et les propose dans cet ordre, d'abord dans la fenêtre de dates du patient ;
l'agent d'hébergement concerné (lits ou ambulatoire) accepte ou refuse ; le premier créneau
accepté, c'est-à-dire celui qui dégrade le moins F parmi les créneaux acceptables, est retenu.
Si aucun n'est accepté pour un patient reporté (ou si aucune vacation ne garde la marge d'aléa de
la salle, auquel cas aucun créneau n'est proposé), le bloc le place quand même là où F augmente
le moins, sans cette marge si besoin (conflit). Un patient convoqué (opération avant t + délai de
prévenance) ne bouge plus.

Trois stratégies, sur les MÊMES aléas :
- « statique » : pas de coordination ; le bloc reprogramme seul ses reportés au premier
  créneau libre, sans demander les lits ; les surcapacités sont subies ; un placement qui ne garde
  plus la marge de la salle compte comme un conflit, comme avec les agents (aussi en centralisé) ;
- « sma » : les agents se coordonnent comme ci-dessus ;
- « centralise » : quand un problème apparaît, tout le planning (patients non convoqués)
  est réoptimisé par recherche locale (aleas.reparer).

Nouveaux patients en flux (facultatif, src/flux.py) : d et la solution contiennent alors des
patients de vacation -1 et aleas["arrivee"][i] donne leur jour d'arrivée (None pour les patients
du planning). Le jour de son arrivée, le patient reçoit une date, au plus tôt t + délai de
prévenance : avec les agents, comme un patient reporté (dates classées par hausse de F, première
acceptée par l'agent d'hébergement, sinon conflit) ; sans coordination, le bloc prend le créneau
libre le plus proche de la date souhaitée, sans demander les lits ; en centralisé, insertion là où
F augmente le moins puis réoptimisation. Sa première date sert de date initiale (dates changées).

Vacations libérées (réglage vacations_liberees, src/aleas.py) : vacations sans aucun patient dans le
planning de départ (la fonction objectif favorise leur libération). « utilisables » : comme les autres ;
« dernier_recours » : proposées seulement après toutes les autres vacations qui gardent la marge de la
salle (avec les agents : après tous les refus de l'agent d'hébergement), avant tout placement forcé sans
cette marge, et jamais rouvertes par la réoptimisation centralisée ; « fermees » : jamais proposées.
Une vacation rouverte (un patient y est placé) compte ensuite comme une vacation utilisée : le praticien
vient de toute façon, elle est remplie avant d'en rouvrir une autre (choix de l'utilisateur, 2026-10-13).
Indicateurs : liberees_rouvertes (vacations libérées au départ qui ont des patients à la fin) et patients_liberees.

Refus des patients (réglages refus_avance et refus_report, src/aleas.py ; remarque du médecin à la
soutenance intermédiaire, ajout du 2026-10-17) : un patient qui a déjà une date peut refuser la nouvelle
date qu'on lui propose, avec la probabilité refus_avance si elle est plus tôt, refus_report si elle est
plus tard (même jour, autre salle : toujours accepté ; première date d'un nouveau patient : pas un
changement). Le tirage est propre au patient et au jour proposé (_uniforme) : les trois stratégies
rencontrent les mêmes refus, et une date refusée n'est plus reproposée à ce patient (refus_connus).
Le patient est appelé après l'accord de l'agent d'hébergement. S'il refuse :
- déplacement demandé pour libérer des lits ou des places : il garde sa date, l'agent demande à un autre patient ;
- créneau libéré par une annulation : on propose l'avance au patient suivant ;
- patient reporté (il doit être réopéré) : date suivante, jusqu'à une date qu'il accepte, même en conflit ;
  s'il les refuse toutes, il est sans solution ;
- réoptimisation centralisée : les dates déjà refusées sont exclues du recalcul ; un patient déplacé qui
  refuse garde sa date (un patient reporté prend la date qu'il accepte où F augmente le moins).
"""
import time

import mesa
import numpy as np
import pandas as pd

from .aleas import parametres, reparer
from .horaires import HORAIRES_DEFAUT, ordonner
from .modele import Evaluateur
from .robustesse import OBJECTIF_REMPLISSAGE, score_robustesse

NOMS_STRATEGIES = {"statique": "Sans coordination (statique)", "sma": "Agents coordonnés (SMA)",
                   "centralise": "Réoptimisation centralisée"}
MAX_PROPOSITIONS = 25    # créneaux proposés au plus par le bloc pour un patient
MAX_JOURNAL = 1500       # messages gardés dans le journal (le flux de nouveaux patients en ajoute beaucoup)
MODES_LIBEREES = ("utilisables", "dernier_recours", "fermees")


def _effet_f(hausse):
    """« F +12,3 » / « F −4,0 » pour le journal des échanges."""
    return "F " + f"{hausse:+.1f}".replace(".", ",").replace("-", "−")


_MASQUE = (1 << 64) - 1


def _uniforme(graine, i, j):
    """Tirage uniforme dans [0, 1) propre au patient i et au jour j, pour une graine (mélange splitmix64) :
    le même patient donne la même réponse pour le même jour dans les trois stratégies, sans toucher
    aux tirages des autres aléas."""
    x = (int(graine) * 0x9E3779B97F4A7C15 + (i + 1) * 0xD1B54A32D192ED03 + (j + 1) * 0xABC98388FB8FAC03) & _MASQUE
    for _ in range(2):
        x = (x + 0x9E3779B97F4A7C15) & _MASQUE
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASQUE
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASQUE
        x ^= x >> 31
    return (x >> 11) / float(1 << 53)


def _liste(x, defaut):
    """Clé facultative de d (liste ou tableau numpy) ; defaut si elle est absente."""
    return defaut if x is None else list(x)


class AgentBloc(mesa.Agent):
    nom = "Bloc"

    def __init__(self, model):
        super().__init__(model)
        m = model
        self.par_jour = {}
        self.du_chir = {}
        for v, j in enumerate(m.jour):
            self.par_jour.setdefault(j, []).append(v)
            self.du_chir.setdefault(m.vac_chir[v], []).append(v)

    def tient(self, i, v, marge=True):
        """L'intervention de i tient dans la vacation v (avec la marge d'aléa µ + z_β √Σσ² de la section 6)."""
        m = self.model
        if not marge:
            return m.ev.charge[v] + m.mu[i] <= m.H[v] + 1e-9
        return m.ev.charge[v] + m.mu[i] + m.z * np.sqrt(m.ev.var[v] + m.sig2[i]) <= m.H[v] + 1e-9

    def proposer(self, i, apres, jour_ref, etendre=False, marge=True, liberees=None):
        """Créneaux pour le patient i à partir du jour `apres` où son intervention tient,
        du plus proche de jour_ref au plus éloigné (etendre : hors de sa fenêtre de dates).
        liberees : None toutes les vacations, False sans les vacations libérées, True seulement elles ;
        les vacations fermées (réglage « fermees ») et les jours que le patient a déjà refusés ne sont jamais proposés."""
        m = self.model
        liste = self.du_chir.get(m.chir[i], []) if etendre else m.adm[i]
        res = [v for v in liste if m.jour[v] >= apres and v != m.ev.sol[i] and v not in m.fermees
               and (i, m.jour[v]) not in m.refus_connus
               and (liberees is None or (v in m.liberees) == liberees) and self.tient(i, v, marge)]
        res.sort(key=lambda v: (abs(m.jour[v] - jour_ref), m.jour[v]))
        return res

    def classer(self, i, vacations, t):
        """[(v, hausse de F)] triés par hausse de F croissante si le patient i passait dans v
        (à égalité, l'ordre reçu : la plus proche d'abord)."""
        ev = self.model.evaluateur_f(t)
        F0 = ev.valeur()
        res = [(v, ev.essayer(i, v) - F0) for v in vacations]
        return sorted(res, key=lambda x: round(x[1], 6))   # tri stable

    def operer(self, t):
        """Opère les patients du jour ; renvoie les patients reportés."""
        m = self.model
        tol = m.p["tolerance_depassement"]
        par_vac = {}
        for i, v in enumerate(m.ev.sol):
            if v >= 0 and m.jour[v] == t:
                par_vac.setdefault(v, []).append(i)
        reportes = []
        for v in self.par_jour.get(t, []):
            # ordre du programme horaire : ambulatoires tôt, alternance opérations lourdes / légères
            brut = par_vac.get(v, [])
            pts = [brut[k] for k in ordonner([{"duree": m.mu[i], "ambu": m.ambu[i]} for i in brut], 0, m.horaires)]
            m.minutes_offertes += m.H[v]
            if m.libere.get(v):
                # baisse de la charge prévue de la vacation, au plus les minutes des patients annulés
                vide = min(m.libere[v], max(0.0, m.charge_initiale[v] - m.ev.charge[v]))
                if vide > 1e-9:
                    m.minutes_vides_annulation += vide
                    m.vacations_trou_annulation += 1
            fin, nb = 0.0, 0
            for i in pts:
                debut = fin + (m.rotation if nb else 0.0)
                if debut + m.mu[i] > m.H[v] + tol:
                    reportes.append(i)
                    continue
                fin = debut + m.duree_reelle[i]
                nb += 1
                m.operes.add(i)
                m.minutes_operees += m.duree_reelle[i]
            if nb:   # vacation où au moins un patient est opéré (les vacations restées vides n'y comptent pas)
                m.minutes_offertes_utilisees += m.H[v]
                m.vacations_operees += 1
            if fin > m.H[v] + 1e-9:
                m.vacations_debordantes += 1
                m.minutes_debordement += fin - m.H[v]
        return reportes

    def combler(self, v, t):
        """Créneau libéré par une annulation : avance un patient plus tardif qui y est admissible.
        Les candidats sont classés par hausse de F (F à l'instant t, comme pour les autres déplacements) et
        on n'avance que si F augmente au plus de tolerance_comblement (5 par défaut, soit une nuit qui passe de
        2 à 3 lits d'écart à la cible) ; sinon le créneau reste libre. Compromis entre le remplissage du bloc
        et le lissage des lits et des places (choix de l'utilisateur, 2026-10-19 : avancer le patient le plus
        en retard sans regarder F dégradait le lissage, surtout des places). Le patient peut refuser l'avance
        (alors le suivant)."""
        m = self.model
        tau = float(m.p["tolerance_comblement"])
        avances = 0
        for _ in range(3):
            cands = [k for k in m.admissibles_de[v]
                     if m.ev.sol[k] >= 0 and k not in m.operes and m.jour[m.ev.sol[k]] > m.jour[v]
                     and m.jour[m.ev.sol[k]] >= t + m.D and (k, m.jour[v]) not in m.refus_connus and self.tient(k, v)]
            if not cands:
                return
            ev = m.evaluateur_f(t)
            F0 = ev.valeur()
            notes = sorted(((ev.essayer(k, v) - F0, k) for k in cands), key=lambda x: (round(x[0], 6), x[1]))
            for hausse, k in notes[:MAX_PROPOSITIONS]:
                if hausse > tau + 1e-9:
                    if not avances:
                        m.log(t, "Bloc", "—", f"un créneau s'est libéré le {m.date(m.jour[v])}, mais y avancer un patient "
                                              f"dégraderait trop le planning ({_effet_f(hausse)} au mieux) : il reste libre.")
                    return
                heberg = m.hebergement(k)
                ok, motif = heberg.accepte(k, v, t)
                if ok and m.patient_accepte(k, v, t):
                    m.log(t, "Bloc", heberg.nom, f"un créneau s'est libéré le {m.date(m.jour[v])}. Je peux y avancer "
                                                 f"{m.ident(k)}, prévu le {m.date(m.jour[m.ev.sol[k]])} ({_effet_f(hausse)}). D'accord ?")
                    m.log(t, heberg.nom, "Bloc", "d'accord, il reste de la place.")
                    m.deplacer(k, v)
                    m.creneaux_recombles += 1
                    avances += 1
                    break
            else:
                return


class AgentHebergement(mesa.Agent):
    """Partie commune aux agents Lits et Ambulatoire."""
    nom = ""

    def resoudre(self, t):
        """Demande au bloc de déplacer des patients non convoqués tant qu'une journée est en surcapacité."""
        m = self.model
        for w in self.surcharges(t):
            essais = 0
            for i in self.presents(w, t):
                if self.occupation(w) <= self.capacite(w) + 1e-9 or essais >= 15:
                    break
                essais += 1
                m.negocier(i, t, self, self.manque(w))


class AgentLits(AgentHebergement):
    nom = "Lits"

    def capacite(self, w):
        return self.model.cap_lits[w]

    def occupation(self, w):
        m = self.model
        return m.ev.lits[w] + m.urg_lits[w]

    def prevision(self, w, t):
        """Nuits futures : au moins le profil moyen des urgences réservé par le planning."""
        m = self.model
        u = m.urg_lits[w] if w <= t else max(m.urg_lits[w], m.urg_attendu_lits[w])
        return m.ev.lits[w] + u

    def manque(self, w):
        return (f"il manque des lits la nuit du {self.model.date(w)} "
                f"({self.occupation(w):.0f} patients pour {self.capacite(w):.0f} lits)")

    def accepte(self, i, v, t):
        m = self.model
        actuel = range(*m.ev._nuits_de(i, m.ev.sol[i])) if m.ev.sol[i] >= 0 else range(0)
        for w in range(*m.ev._nuits_de(i, v)):
            occ = self.prevision(w, t) - (1 if w in actuel else 0)
            if occ + 1 > m.cap_lits[w] - m.p["marge_lits"] + 1e-9:
                marge = f", en gardant {m.p['marge_lits']} lit(s) libre(s) pour les imprévus" if m.p["marge_lits"] else ""
                return False, (f"la nuit du {m.date(w)} serait trop chargée ({occ:.0f} patients pour "
                               f"{m.cap_lits[w]:.0f} lits{marge})")
        return True, ""

    def surcharges(self, t):
        m = self.model
        return [w for w in range(t, m.T) if self.occupation(w) > m.cap_lits[w] + 1e-9]

    def presents(self, w, t):
        """Hospitalisés présents la nuit w et pas encore convoqués (les plus tardifs d'abord)."""
        m = self.model
        res = [i for i in m.hospitalises if m.ev.sol[i] >= 0 and i not in m.operes
               and m.jour[m.ev.sol[i]] >= t + m.D and w in range(*m.ev._nuits_de(i, m.ev.sol[i]))]
        return sorted(res, key=lambda i: -m.jour[m.ev.sol[i]])


class AgentAmbulatoire(AgentHebergement):
    nom = "Ambulatoire"

    def capacite(self, w):
        return self.model.cap_places[w]

    def occupation(self, w):
        m = self.model
        return m.ev.places[w] + m.urg_places[w]

    def manque(self, w):
        return (f"il manque des places ambulatoires le {self.model.date(w)} "
                f"({self.occupation(w):.0f} patients pour {self.capacite(w):.0f} places)")

    def accepte(self, i, v, t):
        m = self.model
        j = m.jour[v]
        u = m.urg_places[j] if j <= t else max(m.urg_places[j], m.urg_attendu_places[j])
        deja = 1 if m.ev.sol[i] >= 0 and m.jour[m.ev.sol[i]] == j else 0
        occ = m.ev.places[j] + u - deja
        if m.cap_places[j] <= 0 or occ + 1 > m.cap_places[j] + 1e-9:
            return False, f"les places du {m.date(j)} sont toutes prises ({occ:.0f} patients pour {m.cap_places[j]:.0f} places)"
        return True, ""

    def surcharges(self, t):
        m = self.model
        return [w for w in range(t, m.T) if self.occupation(w) > m.cap_places[w] + 1e-9]

    def presents(self, w, t):
        m = self.model
        res = [i for i in m.ambulatoires if m.ev.sol[i] >= 0 and i not in m.operes
               and m.jour[m.ev.sol[i]] == w and w >= t + m.D]
        return sorted(res, key=lambda i: -m.mu[i])


class ModeleHopital(mesa.Model):
    """Un pas = une journée de l'horizon."""

    def __init__(self, d, solution, aleas, strategie="sma", params=None, rotation=12.0, debut=None, ids=None):
        super().__init__(rng=int(parametres(params)["graine"]))
        self.p = parametres(params)
        self.strategie = strategie
        self.d = d
        self.T, self.N = d["nb_jours"], len(d["duree"])
        self.jour, self.H = list(d["jour"]), [float(h) for h in d["capacite"]]
        self.mu, self.ambu = list(d["duree"]), list(d["ambu"])
        self.sig2 = [s * s for s in _liste(d.get("sigma"), [0.0] * self.N)]
        self.z = float(d.get("z_beta", 1.0))
        self.adm = [list(a) for a in d["admissibles"]]
        self.chir = _liste(d.get("chir"), [0] * self.N)
        self.vac_chir = _liste(d.get("vac_chir"), [0] * len(self.H))
        self.souhait = _liste(d.get("souhait"), [0] * self.N)
        self.D = self.p["delai_prevenance"]
        # µ contient déjà la rotation (rotation_dans_mu > 0, src/scenario.ajouter_rotation) : on ne l'ajoute pas une 2e fois
        self.rotation = 0.0 if d.get("rotation_dans_mu") else float(rotation)
        # ordre de passage : mêmes réglages que le programme horaire de l'onglet Planning (d["horaires"])
        self.horaires = {**HORAIRES_DEFAUT, "rotation": self.rotation, **(d.get("horaires") or {})}
        self.debut = pd.Timestamp(debut) if debut else None
        self.ids = list(ids) if ids is not None else [f"patient {i + 1}" for i in range(self.N)]
        self.aleas = aleas
        self.duree_reelle = aleas["duree_reelle"]
        # occupation par les patients programmés seulement (les urgences sont comptées à part)
        self.d_sim = {**d, "lits_reserves": None, "places_reservees": None}
        self.ev = Evaluateur(self.d_sim, [int(v) for v in solution])
        self.jour_initial = [self.jour[v] if v >= 0 else None for v in self.ev.sol]
        # vacations libérées : aucun patient dans le planning de départ (nouveaux patients du flux non comptés)
        self.mode_liberees = self.p["vacations_liberees"]
        if self.mode_liberees not in MODES_LIBEREES:
            raise ValueError(f"vacations_liberees : {self.mode_liberees!r} (attendu : {', '.join(MODES_LIBEREES)})")
        self.liberees_depart = frozenset(v for v in range(len(self.H)) if self.ev.nb[v] == 0)   # indicateurs
        self.liberees = set(self.liberees_depart)   # encore libérées : une vacation rouverte en sort (deplacer)
        self.fermees = self.liberees_depart if self.mode_liberees == "fermees" else frozenset()
        # ordre des recherches de créneaux : vacations libérées après les autres en « dernier recours »
        self.passes_liberees = (False, True) if self.mode_liberees == "dernier_recours" else (None,)
        self.charge_initiale = list(self.ev.charge)
        # objectifs du médecin dans le planning prévu (pour comparaison avec le planning exécuté)
        self.qualite_prevue = self.qualite()
        self.libere = {}   # vacation -> minutes libérées par des annulations
        self._ev_f, self._ev_f_jour = None, None   # évaluateur de F à l'instant t (voir evaluateur_f)
        self.minutes_vides_annulation, self.vacations_trou_annulation = 0.0, 0
        self.urg_lits, self.urg_places = np.zeros(self.T), np.zeros(self.T)
        # profil moyen des urgences : prévision des nuits futures (dans F du planning ou seulement prévu)
        self.urg_attendu_lits = np.asarray(_liste(d.get("urgences_prevues_lits", d.get("lits_reserves")),
                                                  [0.0] * self.T), float)
        self.urg_attendu_places = np.asarray(_liste(d.get("urgences_prevues_places", d.get("places_reservees")),
                                                    [0.0] * self.T), float)
        self.cap_lits = np.asarray(d["cap_lits"], float).copy()
        self.cap_places = np.asarray(d["cap_ambu"], float).copy()
        self.hospitalises = [i for i in range(self.N) if not self.ambu[i]]
        self.ambulatoires = [i for i in range(self.N) if self.ambu[i]]
        self.admissibles_de = [[] for _ in self.H]
        for i, a in enumerate(self.adm):
            for v in a:
                self.admissibles_de[v].append(i)
        self.urgences_du_jour = {}
        for connu, dbt, fin in aleas["urgences"]:
            self.urgences_du_jour.setdefault(connu, []).append((dbt, fin))
        # nouveaux patients en flux (src/flux.py) : jour d'arrivée, None pour les patients du planning
        self.arrivee = list(aleas.get("arrivee") or [None] * self.N)
        self.arrivees_du_jour = {}
        for i, t0 in enumerate(self.arrivee):
            if t0 is not None:
                self.arrivees_du_jour.setdefault(int(t0), []).append(i)
        self.nouveaux = set()
        # état et compteurs
        self.operes, self.annules, self.prolonges, self.forces = set(), set(), set(), set()
        self.forces_marge = set()   # conflits faute de vacation qui garde la marge de la salle (aucun refus des lits)
        self.sans_marge = False     # derniers créneaux de candidats() hors marge de la salle
        self.sans_creneau = set()
        self.reportes = set()
        # refus des patients : tirage propre au tirage d'aléas (même graine = mêmes refus), jours déjà refusés
        self.graine_patients = int(aleas.get("graine", self.p["graine"]))
        self.refus_connus, self.patients_refus = set(), set()
        self.refus_avances = self.refus_reports = 0
        self.nb_deplacements = self.creneaux_recombles = self.vacations_debordantes = 0
        self.minutes_debordement = self.minutes_operees = self.minutes_offertes = 0.0
        self.minutes_offertes_utilisees = 0.0
        self.vacations_operees = 0   # vacations où au moins un patient est opéré
        self.nb_messages = self.nb_reparations = 0
        self.journal, self.lits_nuit, self.places_jour = [], [], []
        self.bloc = AgentBloc(self)
        self.lits = AgentLits(self)
        self.ambulatoire = AgentAmbulatoire(self)
        self.collecteur = mesa.DataCollector(model_reporters={
            "lits": lambda m: m.lits_nuit[-1], "cap_lits": lambda m: float(m.cap_lits[m.t]),
            "places": lambda m: m.places_jour[-1], "cap_places": lambda m: float(m.cap_places[m.t]),
            "urgences_lits": lambda m: float(m.urg_lits[m.t])})
        self.t = 0

    # --- outils ---------------------------------------------------------------
    def date(self, t):
        if self.debut is None:
            return f"jour {t}"
        x = self.debut + pd.Timedelta(days=int(t))
        return f"{['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.'][x.dayofweek]} {x.strftime('%d/%m')}"

    def ident(self, i):
        return str(self.ids[i])

    def log(self, t, de, a, texte):
        self.nb_messages += 1
        if len(self.journal) < MAX_JOURNAL:
            self.journal.append({"jour": self.date(t), "de": de, "a": a, "texte": texte})

    def hebergement(self, i):
        return self.ambulatoire if self.ambu[i] else self.lits

    def patient_accepte(self, i, v, t):
        """Le patient i accepte-t-il de passer de sa date actuelle au jour de la vacation v ? Refus avec la
        probabilité refus_avance (date plus tôt) ou refus_report (date plus tard) ; un jour refusé est retenu."""
        v0 = self.ev.sol[i]
        j = self.jour[v]
        if v0 < 0 or j == self.jour[v0]:
            return True   # première date d'un nouveau patient, ou même jour dans une autre salle
        avance = j < self.jour[v0]
        self.nb_messages += 1   # appel du patient
        if _uniforme(self.graine_patients, i, j) >= self.p["refus_avance" if avance else "refus_report"]:
            self.nb_messages += 1
            return True
        self.refus_connus.add((i, j))
        self.patients_refus.add(i)
        if avance:
            self.refus_avances += 1
        else:
            self.refus_reports += 1
        self.log(t, "Patient", "Bloc", f"{self.ident(i)} refuse le {self.date(j)} ({'plus tôt' if avance else 'plus tard'} "
                                       f"que sa date du {self.date(self.jour[v0])}).")
        return False

    def deplacer(self, i, v):
        """Patient i -> vacation v ; la première date d'un nouveau patient n'est pas un déplacement."""
        if self.ev.sol[i] >= 0:
            self.nb_deplacements += 1
        self.liberees.discard(v)   # vacation libérée rouverte : utilisée désormais
        self.ev.deplacer(i, v)
        if self._ev_f is not None:
            self._ev_f.deplacer(i, v)

    def qualite(self):
        """Objectifs du médecin sur l'état actuel du planning, patients programmés seulement (sans les urgences,
        comme le planning prévu) : écart quadratique moyen à la cible des lits (toutes les nuits) et des places
        ambulatoires (jours d'ouverture), cibles proportionnelles au personnel comme dans F (termes W1 et W1′) ;
        remplissage des vacations utilisées (durées prévues µ / durée des vacations qui ont au moins un patient)."""
        ev = self.ev
        lits, E = np.asarray(ev.lits, float), np.asarray(ev.E, float)
        places, Wp = np.asarray(ev.places, float), np.asarray(ev.Wp, float)
        ouverts = Wp > 0
        ecart_lits = float(np.sqrt(np.mean((lits - lits.sum() * E / E.sum()) ** 2))) if len(lits) else 0.0
        ecart_places = (float(np.sqrt(np.mean(((places - places.sum() * Wp / Wp.sum()) ** 2)[ouverts])))
                        if ouverts.any() else 0.0)
        utilisees = [v for v in range(len(self.H)) if ev.nb[v] > 0]
        duree = sum(self.H[v] for v in utilisees)
        remplissage = sum(ev.charge[v] for v in utilisees) / duree if duree else 0.0
        return {"lits": ecart_lits, "places": ecart_places, "remplissage": remplissage}

    def d_courant(self, t):
        """d « à l'instant t » : lits réellement ouverts, urgences déjà arrivées et, pour les
        nuits futures, au moins le profil moyen des urgences ; séjours prolongés compris."""
        prevu_l = [self.urg_lits[w] if w <= t else max(self.urg_lits[w], self.urg_attendu_lits[w]) for w in range(self.T)]
        prevu_p = [self.urg_places[w] if w <= t else max(self.urg_places[w], self.urg_attendu_places[w])
                   for w in range(self.T)]
        return {**self.d, "cap_lits": self.cap_lits.tolist(), "lits_reserves": prevu_l,
                "places_reservees": prevu_p, "penaliser_capacites": True, "nuits": list(self.ev.nuits)}

    def evaluateur_f(self, t):
        """Évaluateur de F à l'instant t (d_courant), reconstruit une fois par jour puis tenu à
        jour par deplacer() ; oublié dès que le planning change autrement (annulation, prolongation)."""
        if self._ev_f is None or self._ev_f_jour != t:
            self._ev_f, self._ev_f_jour = Evaluateur(self.d_courant(t), self.ev.sol), t
        return self._ev_f

    # --- coordination ----------------------------------------------------------
    def negocier(self, i, t, demandeur, raison, apres=None, forcer=False):
        """Le bloc propose des créneaux au patient i ; l'agent d'hébergement accepte ou refuse."""
        apres = t + self.D if apres is None else apres
        heberg = self.hebergement(i)
        v0 = self.ev.sol[i]
        j0 = self.jour[v0] if v0 >= 0 else self.souhait[i]   # v0 = -1 : nouveau patient (annoncé par admettre)
        if v0 < 0:
            pass
        elif demandeur is self.bloc:
            self.log(t, "Bloc", heberg.nom, f"{self.ident(i)} n'a pas pu être opéré aujourd'hui : je lui cherche une nouvelle date.")
        else:
            self.log(t, demandeur.nom, "Bloc", f"{raison}. Peux-tu décaler {self.ident(i)}, prévu le {self.date(j0)} ?")
        refus = refus_patient = 0   # dates refusées par l'agent d'hébergement, par le patient
        for libre, etendre in [(lb, e) for lb in self.passes_liberees for e in (False, True)]:
            for v, hausse in self.bloc.classer(i, self.bloc.proposer(i, apres, j0, etendre, liberees=libre), t)[:MAX_PROPOSITIONS]:
                ok, motif = heberg.accepte(i, v, t)
                self.nb_messages += 2
                if ok and not self.patient_accepte(i, v, t):   # accord des lits (ou des places), puis du patient
                    refus_patient += 1
                    continue
                if ok:
                    self.log(t, "Bloc", heberg.nom, f"je propose le {self.date(self.jour[v])} pour {self.ident(i)}"
                             + (" (hors de sa fenêtre de dates habituelle)" if etendre else "") + f" : {_effet_f(hausse)}.")
                    self.log(t, heberg.nom, "Bloc", "d'accord." if not refus else f"d'accord (après {refus} autre(s) date(s) refusée(s)).")
                    self.deplacer(i, v)
                    return True
                if refus < 2:
                    self.log(t, "Bloc", heberg.nom, f"je propose le {self.date(self.jour[v])} pour {self.ident(i)} : {_effet_f(hausse)}.")
                    self.log(t, heberg.nom, "Bloc", f"non : {motif}.")
                refus += 1
        if forcer:
            while True:   # chaque tour, les dates refusées par le patient sortent des propositions (refus_connus)
                cands = []
                for libre in self.passes_liberees:
                    cands = (self.bloc.proposer(i, apres, j0, marge=False, liberees=libre)
                             or self.bloc.proposer(i, apres, j0, True, marge=False, liberees=libre))
                    if cands:
                        break
                if not cands:
                    break
                choix = next(((v, h) for v, h in self.bloc.classer(i, cands, t) if self.patient_accepte(i, v, t)), None)
                if choix is None:
                    refus_patient += 1
                    continue
                v, hausse = choix
                if refus:
                    cause = "aucune date n'a été acceptée"
                elif refus_patient:
                    cause = f"{self.ident(i)} a refusé les autres dates"
                else:   # aucune date proposée : plus aucune vacation ne garde la marge d'aléa de la salle
                    cause = "plus aucune vacation ne garde la marge de sécurité de la salle"
                    self.forces_marge.add(i)
                self.log(t, "Bloc", heberg.nom, f"{cause} : je place quand même {self.ident(i)} "
                                                f"le {self.date(self.jour[v])}, là où F augmente le moins ({_effet_f(hausse)}, conflit).")
                self.deplacer(i, v)
                self.forces.add(i)
                return True
        if forcer:
            self.sans_creneau.add(i)
            self.log(t, "Bloc", "—", f"plus aucune date libre pour {self.ident(i)} d'ici la fin du planning"
                     + (" (il a refusé les dates proposées)." if refus_patient else "."))
        elif refus_patient:
            self.log(t, "Bloc", demandeur.nom, f"{self.ident(i)} refuse les dates possibles : il garde sa date du {self.date(j0)}.")
        else:
            self.log(t, "Bloc", demandeur.nom, f"je ne trouve aucune date qui convienne pour {self.ident(i)}.")
        return False

    def placer_seul(self, i, t, apres, jour_ref=None):
        """Sans coordination : le bloc prend le créneau libre le plus proche de jour_ref (défaut :
        aujourd'hui, donc le premier créneau libre), sans demander les lits ; le patient peut refuser la date
        (alors la suivante)."""
        ref = t if jour_ref is None else jour_ref
        nouveau = self.ev.sol[i] < 0
        while True:   # les dates refusées sortent des candidats (refus_connus)
            cands = self.candidats(i, apres, ref)
            if not cands:
                break
            v = next((v for v in cands if self.patient_accepte(i, v, t)), None)
            if v is not None:
                self.deplacer(i, v)
                self.noter_sans_marge(i)
                self.log(t, "Bloc", "—", f"{self.ident(i)} est {'programmé' if nouveau else 'reprogrammé'} le "
                                         f"{self.date(self.jour[v])}, sans demander l'avis du service d'hospitalisation"
                                         + (" (conflit : plus aucune vacation ne garde la marge de la salle)." if self.sans_marge else "."))
                return
        self.sans_creneau.add(i)
        self.log(t, "Bloc", "—", f"plus aucune date libre pour {self.ident(i)} d'ici la fin du planning.")

    def reoptimiser(self, t, reportes):
        """Stratégie centralisée : recherche locale sur tous les patients non convoqués."""
        jour_min = {i: t + self.D for i, v in enumerate(self.ev.sol)
                    if v >= 0 and i not in self.operes and self.jour[v] >= t + self.D}
        jour_min.update({i: t + 1 for i in reportes})
        d_rep = self.d_courant(t)
        if self.mode_liberees != "utilisables" and self.liberees:
            # le recalcul ne rouvre pas de vacation libérée (un patient déjà dedans peut y rester)
            d_rep = {**d_rep, "admissibles": [[v for v in a if v not in self.liberees or v == self.ev.sol[k]]
                                              for k, a in enumerate(d_rep["admissibles"])]}
        if self.refus_connus:
            # ni les jours que le patient a déjà refusés
            d_rep = {**d_rep, "admissibles": [[v for v in a if (k, self.jour[v]) not in self.refus_connus or v == self.ev.sol[k]]
                                              for k, a in enumerate(d_rep["admissibles"])]}
        sol, _ = reparer(d_rep, list(self.ev.sol), jour_min, self.p["budget_reparation"],
                         self.p["cout_deplacement"], graine=self.p["graine"] * 1000 + t)
        nb, refusent = 0, []
        for i, v in enumerate(sol):
            if v != self.ev.sol[i]:
                if not self.patient_accepte(i, v, t):   # le patient garde sa date
                    refusent.append(i)
                    continue
                self.deplacer(i, v)
                nb += 1
        for i in refusent:
            if i not in reportes:
                continue
            # patient reporté : il doit avoir une autre date, celle qu'il accepte où F augmente le moins
            choix = None
            while choix is None:
                cands = self.candidats(i, t + 1, t)
                if not cands:
                    break
                choix = next((v for v, _ in self.bloc.classer(i, cands, t) if self.patient_accepte(i, v, t)), None)
            if choix is None:
                self.sans_creneau.add(i)
            else:
                self.deplacer(i, choix)
                self.noter_sans_marge(i)
                nb += 1
        self.nb_reparations += 1
        self.log(t, "Optimiseur", "—", f"le planning est recalculé : {nb} patient(s) déplacé(s)"
                 + (f", {len(refusent)} patient(s) refusent leur nouvelle date." if refusent else "."))

    def candidats(self, i, apres, ref):
        """Créneaux sans coordination : d'abord ceux qui gardent la marge de la salle (dans la fenêtre de
        dates, puis hors fenêtre), sinon sans cette marge ; en « dernier recours », à chaque niveau, les
        vacations libérées seulement s'il n'y a rien d'autre. self.sans_marge dit si les créneaux renvoyés
        ne gardent plus la marge : le placement compte alors comme un conflit (noter_sans_marge), comme
        le placement forcé des agents (choix de l'utilisateur, 2026-10-19 : même décompte pour toutes les stratégies)."""
        for marge, etendres in ((True, (False, True)), (False, (True,))):
            for libre in self.passes_liberees:
                for etendre in etendres:
                    cands = self.bloc.proposer(i, apres, ref, etendre, marge, libre)
                    if cands:
                        self.sans_marge = not marge
                        return cands
        return []

    def noter_sans_marge(self, i):
        """Patient placé par candidats() dans une vacation qui ne garde plus la marge de la salle : conflit."""
        if self.sans_marge:
            self.forces.add(i)
            self.forces_marge.add(i)

    def admettre(self, i, t):
        """Nouvelle demande arrivée le jour t : le patient i reçoit une date, au plus tôt t + délai de prévenance."""
        self.nouveaux.add(i)
        apres, s = t + self.D, self.souhait[i]
        self.log(t, "Patient", "Bloc", f"nouvelle demande : {self.ident(i)} ({self.chir[i]}), "
                                       f"date souhaitée le {self.date(s)}.")
        if self.strategie == "sma":
            self.negocier(i, t, self.bloc, "nouvelle demande", apres=apres, forcer=True)
        elif self.strategie == "statique":
            self.placer_seul(i, t, apres, s)
        else:
            cands = self.candidats(i, apres, s)
            if cands:
                v, hausse = self.bloc.classer(i, cands, t)[0]
                self.deplacer(i, v)
                self.noter_sans_marge(i)
                self.log(t, "Optimiseur", "—", f"{self.ident(i)} est inséré le {self.date(self.jour[v])}, "
                                               f"là où F augmente le moins ({_effet_f(hausse)}).")
            else:
                self.sans_creneau.add(i)
                self.log(t, "Optimiseur", "—", f"plus aucune date libre pour {self.ident(i)} d'ici la fin du planning.")
        if self.ev.sol[i] >= 0:
            self.jour_initial[i] = self.jour[self.ev.sol[i]]

    # --- une journée -------------------------------------------------------------
    def step(self):
        t = self.t
        a, probleme = self.aleas, False
        # 1. ce que l'on apprend aujourd'hui
        f = a["fermeture"]
        if t == f["annonce"] and f["lits"] > 0 and f["fin"] > f["debut"]:
            self.cap_lits[f["debut"]:f["fin"]] = np.maximum(0, self.cap_lits[f["debut"]:f["fin"]] - f["lits"])
            self.log(t, "Lits", "tous", f"{f['lits']} lits seront fermés du {self.date(f['debut'])} au {self.date(f['fin'] - 1)}.")
            probleme = True
        for dbt, fin in self.urgences_du_jour.get(t, []):
            self.urg_lits[dbt:fin] += 1
        self.urg_places[t] += a["urg_ambu"][t]
        for i, v in enumerate(self.ev.sol):
            if v >= 0 and a["annule"][i] and i not in self.operes and self.jour[v] - a["preavis"][i] <= t:
                self.ev._retirer(i)
                self._ev_f = None
                self.annules.add(i)
                self.libere[v] = self.libere.get(v, 0.0) + self.mu[i]
                self.log(t, "Patient", "Bloc", f"{self.ident(i)} annule son opération du {self.date(self.jour[v])}.")
                probleme = True
                if self.strategie == "sma" and self.jour[v] >= t + self.D:
                    self.bloc.combler(v, t)
        for i in self.hospitalises:
            v = self.ev.sol[i]
            if v >= 0 and a["prolonge"][i] and i in self.operes and i not in self.prolonges \
                    and self.ev._nuits_de(i, v)[1] == t:
                self.ev._retirer(i)
                self.ev.nuits[i] += 1
                self.ev._placer(i, v)
                self._ev_f = None
                self.prolonges.add(i)
        for i in self.arrivees_du_jour.get(t, []):   # nouvelles demandes (flux)
            self.admettre(i, t)
            probleme = True
        # 2. adaptation aux problèmes connus
        if self.strategie == "sma":
            self.lits.resoudre(t)
            self.ambulatoire.resoudre(t)
        # 3. le bloc opère ; les reportés sont reprogrammés
        reportes = self.bloc.operer(t)
        for i in reportes:
            self.reportes.add(i)
            self.log(t, "Bloc", "—", f"{self.ident(i)} ne peut pas être opéré aujourd'hui : la vacation déborderait.")
            if self.strategie == "sma":
                self.negocier(i, t, self.bloc, "patient reporté", apres=t + 1, forcer=True)
            else:
                self.placer_seul(i, t, t + 1)
        if self.strategie == "centralise" and (probleme or reportes or self.lits.surcharges(t + 1)
                                                or self.ambulatoire.surcharges(t + 1)):
            self.reoptimiser(t, reportes)
        # 4. relevé de la nuit t
        self.lits_nuit.append(float(self.ev.lits[t] + self.urg_lits[t]))
        self.places_jour.append(float(self.ev.places[t] + self.urg_places[t]))
        self.collecteur.collect(self)
        self.t += 1

    def lancer(self):
        debut = time.perf_counter()
        while self.t < self.T:
            self.step()
        return self.resultats(time.perf_counter() - debut)

    def f_apres(self):
        """F du planning réellement exécuté : urgences réellement arrivées, lits réellement ouverts,
        séjours prolongés ; les patients annulés n'y sont plus."""
        return Evaluateur(self.d_courant(self.T), self.ev.sol).valeur()

    def resultats(self, duree):
        serie = self.collecteur.get_model_vars_dataframe()
        lits, cap = serie["lits"].to_numpy(), serie["cap_lits"].to_numpy()
        places, capp = serie["places"].to_numpy(), serie["cap_places"].to_numpy()
        # mêmes définitions que modele.details (jour 0 = lundi), sur l'occupation simulée, urgences comprises
        semaine, ouverts = np.arange(len(lits)) % 7 < 5, capp > 0
        ecart_type = lambda x: round(float(np.std(x)), 2) if len(x) else 0.0   # noqa: E731
        changes = [i for i, v in enumerate(self.ev.sol)
                   if v >= 0 and self.jour_initial[i] is not None and self.jour[v] != self.jour_initial[i]]
        ecart = [abs(self.jour[self.ev.sol[i]] - self.jour_initial[i]) for i in changes]
        avances = sum(self.jour[self.ev.sol[i]] < self.jour_initial[i] for i in changes)
        jours_gagnes = sum(max(0, self.jour_initial[i] - self.jour[self.ev.sol[i]]) for i in changes)
        # nouveaux patients (flux) qui ont une date à la fin de la simulation (opérés ou à venir, annulés exclus)
        dates_n = [(self.jour[self.ev.sol[i]], i) for i in self.nouveaux if self.ev.sol[i] >= 0]
        moyenne = lambda x: round(float(np.mean(x)), 1) if x else 0.0   # noqa: E731
        indicateurs = {
            "nuits_surcapacite": int(np.sum(lits > cap + 1e-9)),
            "lits_nuits_manquants": round(float(np.maximum(0, lits - cap).sum()), 1),
            "lits_max": round(float(lits.max()), 1) if len(lits) else 0.0,
            "taux_occupation_lits": round(float(np.mean(lits / np.maximum(cap, 1))), 3) if len(lits) else 0.0,
            "jours_ambu_satures": int(np.sum(places > capp + 1e-9)),
            # places au-delà des places ouvertes, les seuls jours d'ouverture : une urgence ambulatoire
            # un jour où l'ambulatoire est fermé n'est pas imputable au planning (score de robustesse)
            "places_manquantes": round(float(np.maximum(0, places - capp)[ouverts].sum()), 1),
            "taux_occupation_bloc": round(self.minutes_operees / self.minutes_offertes, 3) if self.minutes_offertes else 0.0,
            # même calcul sur les seules vacations où au moins un patient est opéré (sans les vacations libérées)
            "taux_occupation_bloc_utilisees": round(self.minutes_operees / self.minutes_offertes_utilisees, 3)
            if self.minutes_offertes_utilisees else 0.0,
            "vacations_debordantes": int(self.vacations_debordantes), "vacations_operees": int(self.vacations_operees),
            "minutes_debordement": round(float(self.minutes_debordement)),
            "patients_operes": len(self.operes), "annulations": len(self.annules),
            "creneaux_recombles": int(self.creneaux_recombles),
            "patients_reportes": len(self.reportes), "prolongations": len(self.prolonges),
            "patients_dates_changees": len(changes), "patients_avances": int(avances),
            "jours_attente_gagnes": int(jours_gagnes),
            "minutes_vides_annulation": round(float(self.minutes_vides_annulation)),
            "vacations_trou_annulation": int(self.vacations_trou_annulation),
            "patients_retardes": int(len(changes) - avances),
            "patients_sans_creneau": len(self.sans_creneau - self.operes),
            "ecart_moyen_jours": round(float(np.mean(ecart)), 1) if ecart else 0.0,
            "conflits_non_resolus": len(self.forces), "conflits_marge_salle": len(self.forces_marge),
            "deplacements": int(self.nb_deplacements),
            # nouvelles dates refusées par les patients (plus tôt / plus tard), patients qui en ont refusé au moins une
            "refus_patients": int(self.refus_avances + self.refus_reports),
            "refus_avances": int(self.refus_avances), "refus_reports": int(self.refus_reports),
            "patients_refus": len(self.patients_refus),
            # vacations libérées au départ qui ont des patients à la fin (opérés ou à venir), et ces patients
            "liberees_depart": len(self.liberees_depart),
            "liberees_rouvertes": sum(1 for v in self.liberees_depart if self.ev.nb[v] > 0),
            "patients_liberees": sum(int(self.ev.nb[v]) for v in self.liberees_depart),
            "nouveaux_patients": len(self.nouveaux), "nouveaux_operes": len(self.nouveaux & self.operes),
            "nouveaux_conflits": len(self.nouveaux & self.forces),
            "nouveaux_sans_creneau": len((self.nouveaux & self.sans_creneau) - self.operes),
            "delai_moyen_nouveaux": moyenne([j - self.arrivee[i] for j, i in dates_n]),
            "ecart_souhait_nouveaux": moyenne([abs(j - self.souhait[i]) for j, i in dates_n]),
            "messages": int(self.nb_messages), "reoptimisations": int(self.nb_reparations),
            "temps_s": round(duree, 2),
            "F_apres": round(self.f_apres(), 1),
            "ecart_type_lits_semaine": ecart_type(lits[semaine]),
            "ecart_type_lits_week_end": ecart_type(lits[~semaine]),
            "ecart_type_places_semaine": ecart_type(places[ouverts]),
        }
        # objectifs du médecin dans le planning prévu et dans le planning exécuté (patients programmés seulement).
        # Le score de robustesse juge le planning exécuté en valeur absolue (écarts à la cible, manque_remplissage) ;
        # hausse en % de l'écart et baisse du remplissage par rapport au prévu : pour information
        q0, q1 = self.qualite_prevue, self.qualite()
        hausse = lambda a, b: round(100 * max(0.0, b / max(a, 0.1) - 1), 1)   # noqa: E731
        indicateurs.update({
            "ecart_cible_lits_prevu": round(q0["lits"], 2), "ecart_cible_lits_execute": round(q1["lits"], 2),
            "ecart_cible_places_prevu": round(q0["places"], 2), "ecart_cible_places_execute": round(q1["places"], 2),
            "remplissage_prevu": round(q0["remplissage"], 3),
            "hausse_ecart_lits": hausse(q0["lits"], q1["lits"]), "hausse_ecart_places": hausse(q0["places"], q1["places"]),
            "baisse_remplissage": round(100 * max(0.0, q0["remplissage"] - indicateurs["taux_occupation_bloc_utilisees"]), 1),
            "manque_remplissage": round(100 * max(0.0, OBJECTIF_REMPLISSAGE - indicateurs["taux_occupation_bloc_utilisees"]), 1),
        })
        # score de robustesse (src/robustesse.py) : critères ramenés aux patients du planning (départ + flux),
        # avec les seuils et poids modifiés dans l'onglet Simulation s'il y en a (paramètre reglages_score)
        indicateurs["patients_planning"] = sum(j is not None for j in self.jour_initial) + len(self.nouveaux)
        indicateurs.update(score_robustesse(indicateurs, indicateurs["patients_planning"], self.p.get("reglages_score")))
        return {
            "strategie": self.strategie, "nom": NOMS_STRATEGIES[self.strategie],
            "indicateurs": indicateurs,
            "series": {k: [round(float(x), 2) for x in serie[k]] for k in serie.columns},
            "journal": self.journal,
        }


def simuler(d, solution, aleas, strategie="sma", params=None, rotation=12.0, debut=None, ids=None):
    """Exécute le planning `solution` jour après jour face aux aléas, avec une stratégie."""
    return ModeleHopital(d, solution, aleas, strategie, params, rotation, debut, ids).lancer()


def _agreger(liste):
    """Moyenne des indicateurs sur les tirages + part des tirages sans nuit en surcapacité."""
    res = {k: round(float(np.mean([x[k] for x in liste])), 2) for k in liste[0]}
    res["part_sans_surcapacite"] = round(float(np.mean([x["nuits_surcapacite"] == 0 for x in liste])), 3)
    res["nb_tirages"] = len(liste)
    return res


def robustesse_plan(d, solution, hist, params=None, nb_tirages=20, rotation=12.0, strategies=("statique", "sma"),
                    flux=None):
    """Le même planning face à nb_tirages tirages d'aléas (graines graine, graine + 1, ...) :
    indicateurs moyens pour chaque stratégie. flux : None, ou un flux de nouveaux patients par
    tirage (src/flux.tirer_flux_tirages), injecté dans la simulation de ce tirage."""
    from .aleas import tirer_aleas
    from .flux import injecter
    p = parametres(params)
    res = {s: [] for s in strategies}
    for r in range(int(nb_tirages)):
        aleas = tirer_aleas(d, hist, p, p["graine"] + r)
        d_r, sol_r = d, solution
        if flux:
            d_r, sol_r, _, aleas = injecter(d, solution, [str(i) for i in range(len(solution))], aleas, flux[r], p,
                                            p["graine"] + r)
        for s in strategies:
            res[s].append(simuler(d_r, sol_r, aleas, s, p, rotation)["indicateurs"])
    return {s: _agreger(x) for s, x in res.items()}


def _tache_robustesse(tache):
    cle, d, solution, hist, params, nb_tirages, rotation, strategies, flux = tache
    return cle, robustesse_plan(d, solution, hist, params, nb_tirages, rotation, strategies, flux)


def robustesse(d, plans, hist, params=None, nb_tirages=20, rotation=12.0, strategies=("statique", "sma"),
               rappel=None, processus=None, noms=None, flux=None):
    """Robustesse de plusieurs plannings {clé: solution} face aux MÊMES tirages d'aléas
    (en parallèle : un processus par planning), et aux mêmes nouveaux patients si flux est donné."""
    from .parallele import executer
    taches = [(cle, d, list(sol), hist, params, nb_tirages, rotation, tuple(strategies), flux)
              for cle, sol in plans.items()]
    res = executer(_tache_robustesse, taches, processus, rappel,
                   libelle=lambda t: (noms or {}).get(t[0], t[0]))
    return dict(res)
