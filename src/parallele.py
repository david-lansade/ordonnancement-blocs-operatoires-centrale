"""Exécution de calculs indépendants dans des processus séparés (comparaison des
métaheuristiques, robustesse des plannings, scénarios).

Chaque tâche garde sa graine : les résultats sont les mêmes qu'en séquentiel, seul
le temps total change. Si les processus sont impossibles sur la machine, on repasse
en séquentiel.
"""
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

PROCESSUS_MAX = 8   # tâches simultanées (limite la mémoire : un processus Python par tâche)


def nb_processus(nb_taches, processus=None):
    """None : selon le nombre de cœurs (au plus PROCESSUS_MAX) ; 1 : séquentiel."""
    if processus is None:
        processus = min(PROCESSUS_MAX, max(1, (os.cpu_count() or 2) - 1))
    return max(1, min(int(processus), nb_taches))


def executer(fonction, taches, processus=None, rappel=None, libelle=None):
    """Applique fonction(tache) à chaque tâche ; renvoie la liste des résultats dans l'ordre
    des tâches. fonction doit être définie au niveau d'un module (processus fils).
    rappel(fraction, message) suit l'avancement ; libelle(tache) nomme la tâche terminée."""
    total = len(taches)
    resultats = [None] * total
    fait = 0

    def noter(k, r):
        nonlocal fait
        resultats[k] = r
        fait += 1
        if rappel:
            nom = f" (dernier : {libelle(taches[k])})" if libelle else ""
            rappel(fait / total, f"{fait} / {total} calculs terminés{nom}")

    n = nb_processus(total, processus)
    if n > 1:
        try:
            with ProcessPoolExecutor(max_workers=n) as pool:
                futures = {pool.submit(fonction, t): k for k, t in enumerate(taches)}
                for fut in as_completed(futures):
                    noter(futures[fut], fut.result())
            return resultats
        except (BrokenProcessPool, OSError):
            resultats, fait = [None] * total, 0   # processus impossibles : on refait tout en séquentiel
    for k, t in enumerate(taches):
        noter(k, fonction(t))
    return resultats
