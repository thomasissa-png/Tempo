#!/usr/bin/env python3
"""Étude a) : le jumeau RTE avec la C_nette RÉELLE (borne « consommation nette connue »).

Pour chaque saison dont l'année Tempo précédente est couverte (C_nette eCO2mix
et météo observée), applique rte_twin (normalisation, seuils officiels, règles
de placement) et compare aux couleurs EDF réelles.
Modes :
  stock simulé : saison entière décidée par le jumeau seul, stock 22/43 décrémenté
                 par ses propres décisions (jumeau indépendant) ;
  stock réel   : chaque jour décidé avec le stock et les ROUGE consécutifs RÉELS
                 de la veille (accord jour par jour).
Normalisation : année Tempo précédente (« prec ») ou 365 jours glissants finissant
la veille (« glissant », plus proche du texte de la note).
Usage : python tools/replay/rte_twin_study.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rte_twin_data import ROOT, load_all, season_of  # noqa: E402

sys.path.insert(0, str(ROOT))
import rte_twin as RT  # noqa: E402
from ml_train import metrics  # noqa: E402
from predictor import is_french_holiday  # noqa: E402


def norm_for(d0: date, d1: date, cn: dict, wx: dict, key: str = "cnette"):
    """Normalisation sur [d0, d1] ; None si moins de 330 jours couverts."""
    c, t = [], []
    k = d0
    while k <= d1:
        if k in cn and key in cn[k]:
            c.append(cn[k][key])
        if k in wx and wx[k].get("temp_moy") is not None:
            t.append(wx[k]["temp_moy"])
        k += timedelta(days=1)
    if len(c) < 330 or len(t) < 330:
        return None
    return RT.Normalisation.from_history(c, t)


def run_season(y0: int, act, wx, cn, norm_mode: str, stock_mode: str, key: str = "cnette"):
    s_start, s_end = date(y0, 9, 1), date(y0 + 1, 8, 31)
    prev = norm_for(date(y0 - 1, 9, 1), date(y0, 8, 31), cn, wx, key)
    if norm_mode == "prec" and prev is None:
        return None, None
    pairs, colors = [], {}
    sr, sb = RT.STOCK_ROUGE, RT.STOCK_BLANC
    d = s_start
    while d <= s_end and d in cn and d in act:
        nm = prev if norm_mode == "prec" else norm_for(d - timedelta(365), d - timedelta(1), cn, wx, key)
        if nm is None:
            return None, None
        cstd = nm.normalise(cn[d][key])
        if stock_mode == "reel":
            used = Counter(act[k] for k in act if s_start <= k < d)
            srr, sbr = RT.STOCK_ROUGE - used["ROUGE"], RT.STOCK_BLANC - used["BLANC"]
            c = RT.simulate([d], {d: cstd}, max(0, srr), max(0, sbr), {k: act[k] for k in act if k < d},
                            is_french_holiday)[d]
        else:
            c = RT.simulate([d], {d: cstd}, sr, sb, colors, is_french_holiday)[d]
            sr -= c == "ROUGE"
            sb -= c == "BLANC"
        colors[d] = c
        pairs.append((act[d], c))
        d += timedelta(days=1)
    return pairs, prev


def fmt(m, c):
    r = m[c]
    rec = "-" if r["recall"] is None else f"{100 * r['recall']:.0f}"
    pre = "-" if r["precision"] is None else f"{100 * r['precision']:.0f}"
    return f"{r['tp']}/{r['fp']}/{r['fn']} R={rec} P={pre}"


def main():
    act, wx, cn = load_all()
    seasons = range(2019, 2025)
    for key in ("cnette", "cnette_prev"):
        for norm_mode in ("prec", "glissant"):
            for stock_mode in ("simule", "reel"):
                print(f"== C={key} normalisation={norm_mode} stock={stock_mode}")
                allp = []
                for y0 in seasons:
                    pairs, nm = run_season(y0, act, wx, cn, norm_mode, stock_mode, key)
                    if not pairs:
                        continue
                    allp += pairs
                    m = metrics(pairs)
                    extra = f" q04={nm.q_conso_04:.0f} q08={nm.q_conso_08:.0f} qT03={nm.q_temp_03:.2f} éch={nm.echelle:.0f}" if nm else ""
                    print(f"  {y0}-{y0 + 1} n={m['n']} ROUGE {fmt(m, 'ROUGE')} | BLANC {fmt(m, 'BLANC')}{extra}")
                m = metrics(allp)
                print(f"  total n={m['n']} ROUGE {fmt(m, 'ROUGE')} | BLANC {fmt(m, 'BLANC')}")


if __name__ == "__main__":
    main()
