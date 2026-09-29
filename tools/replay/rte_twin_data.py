"""Données RÉELLES pour l'étude du jumeau RTE (aucune donnée inventée).

- C_nette journalière Tempo (6 h -> 6 h) depuis les fichiers eCO2mix du dépôt
  (eCO2mix_RTE_Annuel-Definitif_AAAA.xls, eCO2mix_RTE_En-cours-Consolide.xls :
  texte tabulé latin-1, pas demi-horaire). Couverture : 2019-01-01 -> 2024-12-31.
- Couleurs EDF non synthétiques et météo observée : db_dump.json (via ml_train.load_data).
"""
from __future__ import annotations

import glob
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MIN_DEMI_HEURES = 44  # sur 48 : journée Tempo retenue si au plus 4 demi-heures manquent


def _num(x: str):
    x = x.strip()
    if not x or x == "ND":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def load_eco2mix() -> dict[datetime, dict]:
    """{horodatage demi-heure: {conso, prev_j1, eolien, solaire}}."""
    files = sorted(glob.glob(str(ROOT / "eCO2mix_RTE_Annuel-Definitif_*.xls"))) + \
        [str(ROOT / "eCO2mix_RTE_En-cours-Consolide.xls")]
    out = {}
    for f in files:
        with open(f, encoding="latin-1") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            ix = {k: header.index(k) for k in ("Date", "Heures", "Consommation", "Prévision J-1", "Eolien", "Solaire")}
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) < len(ix) or not p[ix["Date"]][:2].isdigit():
                    continue
                conso = _num(p[ix["Consommation"]])
                if conso is None:
                    continue
                ts = datetime.fromisoformat(f"{p[ix['Date']]}T{p[ix['Heures']]}")
                out[ts] = {"conso": conso, "prev_j1": _num(p[ix["Prévision J-1"]]),
                           "eolien": _num(p[ix["Eolien"]]), "solaire": _num(p[ix["Solaire"]])}
    return out


def daily_cnette(hh: dict[datetime, dict]) -> dict[date, dict]:
    """Moyennes journalières Tempo (6 h du jour j -> 5 h 30 du jour j+1).

    cnette      : consommation réalisée - éolien - solaire
    cnette_prev : prévision J-1 de consommation - éolien - solaire réalisés
    conso       : consommation réalisée brute
    """
    acc = defaultdict(lambda: defaultdict(list))
    for ts, r in hh.items():
        if ts.minute not in (0, 30) or r["eolien"] is None or r["solaire"] is None:
            continue
        j = (ts - timedelta(hours=6)).date()
        fatal = r["eolien"] + r["solaire"]
        acc[j]["cnette"].append(r["conso"] - fatal)
        acc[j]["conso"].append(r["conso"])
        if r["prev_j1"] is not None:
            acc[j]["cnette_prev"].append(r["prev_j1"] - fatal)
    out = {}
    for j, v in acc.items():
        if len(v["cnette"]) >= MIN_DEMI_HEURES:
            out[j] = {k: sum(x) / len(x) for k, x in v.items() if len(x) >= MIN_DEMI_HEURES}
    return out


def season_of(d: date) -> str:
    y = d.year if d.month >= 9 else d.year - 1
    return f"{y}-{y + 1}"


def load_all():
    import ml_train
    data = ml_train.load_data()
    act = {date.fromisoformat(k): v for k, v in data["actuals"].items()}
    wx = {date.fromisoformat(k): v for k, v in data["weather"].items()}
    cn = daily_cnette(load_eco2mix())
    return act, wx, cn
