"""Les trois métaheuristiques du groupe, sur le même vecteur solution."""
from .commun import solution_aleatoire, solution_gloutonne
from .genetique import algorithme_genetique
from .recuit import recuit_simule
from .tabou import recherche_tabou

ALGORITHMES = {
    "recuit": recuit_simule,
    "tabou": recherche_tabou,
    "genetique": algorithme_genetique,
}
