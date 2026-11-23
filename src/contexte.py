"""État partagé de l'application : base nettoyée, modèles, grilles, paramètres, planning."""
import json
import threading

from . import grille as G
from .aleas import historique_urgences
from .chargement import DOSSIER_PROCESSED, charger_sejours, temps_rotation
from .generation import Generateur, ListeInitiale, completer_predictions
from .planning import Planning
from .prediction import Predicteur
from .scenario import charger_parametres, sauver_parametres
from .urgences import reserve_urgences

FICHIER_GRILLE = DOSSIER_PROCESSED / "grille_nouvelle.json"


class Contexte:
    def __init__(self):
        self.verrou = threading.Lock()
        self.df = charger_sejours()
        self.rotation = temps_rotation(self.df)
        self.params = charger_parametres()
        self.reserve = reserve_urgences(self.df, self.rotation, self.params["quantile_urgences"])
        # modèles appris sur 2019-2022, avec et sans les mois COVID (les erreurs affichées
        # restent celles du protocole apprentissage 2019-2021 / test 2022)
        avec = Predicteur.charger(self.df)
        sans = Predicteur.charger(self.df, sans_covid=True, metriques=avec.metriques)
        self.predicteurs = {False: avec, True: sans}
        self.construire_grille()
        self.generateur = Generateur(self.df)
        self.liste = ListeInitiale()
        if not self.liste.patients:
            self.generer_liste()
        self.planning = Planning(self)
        self.derniere_instance = None    # (d, P, vac, nom de la grille)
        self.derniere_comparaison = None
        self._hist_urgences = None

    def construire_grille(self):
        p = self.params
        self.reserve = reserve_urgences(self.df, self.rotation, p["quantile_urgences"])
        g, tab, expl = G.construire_grille(self.df, self.rotation, self.reserve, marge=p["grille_marge"],
                                           periode=tuple(p["grille_periode"]), places_ambu=p["cap_ambu"])
        self.grille_nouvelle, self.tableau_grille, self.explications_grille = g, tab, expl
        FICHIER_GRILLE.write_text(json.dumps(G.grille_en_json(g), ensure_ascii=False, indent=0), encoding="utf-8")

    @property
    def pred_complet(self):
        """Prédicteur choisi par le médecin (onglet Technique) : mois COVID exclus ou non."""
        return self.predicteurs[bool(self.params.get("exclure_covid", False))]

    def generer_liste(self, x=None, semaines=None, graine=None):
        """Invente x patients pour l'ouverture et calcule leurs prédictions."""
        p = self.params
        x = int(x or p["initial_nb_patients"])
        semaines = int(semaines or p["instance_semaines"])
        graine = int(p["initial_graine"] if graine is None else graine)
        p.update(initial_nb_patients=x, instance_semaines=semaines, initial_graine=graine)
        self.sauver_parametres()
        codes = {v["chir"] for v in self.grille(p["grille"])}
        patients = self.generateur.generer(x, semaines, p["date_ouverture"], graine, autorises=codes)
        completer_predictions(patients, self.pred_complet, p["methode_prediction"], p)
        self.liste.remplacer(patients, {"x": x, "semaines": semaines, "graine": graine,
                                        "date_ouverture": p["date_ouverture"]})

    def historique_urgences(self):
        """Séjours en urgence de la base, pour tirer les aléas (calculé une fois)."""
        if self._hist_urgences is None:
            self._hist_urgences = historique_urgences(self.df)
        return self._hist_urgences

    def grille(self, nom):
        return G.GRILLE_ACTUELLE if nom == "actuelle" else self.grille_nouvelle

    def sauver_parametres(self):
        sauver_parametres(self.params)
