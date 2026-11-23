"""Export du programme opératoire (onglet Planning) en classeur Excel (.xlsx).

Le programme vient de Planning.programme_operatoire : une ligne par patient, dans l'ordre des jours,
des salles et de passage, avec la convocation, le passage en SAS et l'entrée en salle.
"""
from datetime import datetime
from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .chargement import JOURS

COLONNES = [("Date", 20), ("Salle", 7), ("Chirurgien", 22), ("Vacation", 13), ("Rang", 6), ("Patient", 14),
            ("Intervention", 30), ("Diagnostic", 11), ("Âge", 6), ("Sexe", 6), ("Anesthésie", 22),
            ("Locorégionale", 18), ("Convocation", 12), ("SAS", 8), ("Entrée en salle", 14), ("Sortie prévue", 13),
            ("Durée prévue (min)", 12), ("Hébergement", 14), ("Remarque", 28)]


def _date_longue(iso):
    ts = pd.Timestamp(iso)
    return f"{JOURS[ts.dayofweek]} {ts.strftime('%d/%m/%Y')}"


def programme_en_excel(programme, noms_chirurgiens=None):
    """Classeur .xlsx (octets) du programme opératoire ; noms_chirurgiens : {code: nom}."""
    noms = noms_chirurgiens or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Programme opératoire"
    ws["A1"] = (f"Programme opératoire du {_date_longue(programme['debut'])} au {_date_longue(programme['fin'])}")
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Édité le {datetime.now().strftime('%d/%m/%Y à %H:%M')} (horaires prévus, susceptibles de changer)"
    ws["A2"].font = Font(italic=True, color="666666")
    ligne_entete = 4
    trait = Side(style="thin", color="BBBBBB")
    for k, (titre, largeur) in enumerate(COLONNES, start=1):
        c = ws.cell(row=ligne_entete, column=k, value=titre)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2A78D6")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(k)].width = largeur
    n = ligne_entete
    alterne = PatternFill("solid", fgColor="EEF3FA")
    for k, v in enumerate(programme["vacations"]):
        chir = v["chir"] if v["type"] != "urgences" else "URGENCES"
        if chir in noms:
            chir = f"{chir} - {noms[chir]}"
        for p in v["patients"]:
            h = p["horaire"]
            n += 1
            valeurs = [_date_longue(v["date"]), v["salle"], chir, f"{v['debut']} - {v['fin']}", h["rang"],
                       p.get("nom") or p["id"], p.get("type_interv"), p.get("cim"),
                       None if p.get("age") is None else round(float(p["age"])),
                       {1: "H", 2: "F"}.get(p.get("sexe"), ""), p.get("anesth_type") or "Non renseignée",
                       p.get("anesth_locoreg") or "Aucune", h["convocation"], h["sas"], h["entree"], h["sortie"],
                       round(float(p["duree"])), "ambulatoire" if p.get("ambu") else f"{p.get('nuits')} nuit(s)",
                       "sortie de salle après la fin de la vacation" if h.get("depasse") else ""]
            for col, x in enumerate(valeurs, start=1):
                c = ws.cell(row=n, column=col, value=x)
                c.border = Border(bottom=trait)
                if k % 2:
                    c.fill = alterne
    if n == ligne_entete:
        ws.cell(row=n + 1, column=1, value="Aucun patient programmé sur cette période.")
    ws.freeze_panes = ws.cell(row=ligne_entete + 1, column=1)
    ws.auto_filter.ref = f"A{ligne_entete}:{get_column_letter(len(COLONNES))}{max(n, ligne_entete + 1)}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    tampon = BytesIO()
    wb.save(tampon)
    return tampon.getvalue()
