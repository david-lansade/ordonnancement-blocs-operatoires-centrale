"""Lance l'application web : double-cliquer ou `.venv\\Scripts\\python lancer.py`,
puis ouvrir http://127.0.0.1:5000"""
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent
os.chdir(RACINE)
sys.path.insert(0, str(RACINE))

from app.serveur import main  # noqa: E402

if __name__ == "__main__":
    main()
