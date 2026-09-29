#!/usr/bin/env python3
"""Étude b) : jumeau RTE en prévision J+2..J+5, C_nette estimée depuis la météo.

Protocole identique à replay_pipeline.py (18 h le jour D, couleurs EDF connues
jusqu'à D+1, météo OBSERVÉE de D à D+7 à la place des prévisions).
Tout ce qui est appris l'est en saison exclue :
  - modèle linéaire de C_nette (rte_twin.cnette_features), entraîné sur la
    C_nette réelle eCO2mix des AUTRES saisons ;
  - quantiles de normalisation : année Tempo PRÉCÉDENTE, C_nette estimée par
    ce même modèle depuis la météo observée (source disponible pour toutes les
    saisons ; la C_nette réelle s'arrête au 2024-12-30).
Compare au pipeline rejoué (JSON de replay_pipeline.py) sur les mêmes couples
(horizon, date cible) : actuel, jumeau seul, H1 (filet), H2 (filet + veto).

Usage : python tools/replay/rte_twin_forecast.py --actuel /tmp/apres_E.json [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rte_twin_data import ROOT, load_all, season_of  # noqa: E402

sys.path.insert(0, str(ROOT))
import rte_twin as RT  # noqa: E402
from ml_train import metrics  # noqa: E402
from predictor import is_french_holiday  # noqa: E402

RIDGE = 1.0
SEASONS = [f"{y}-{y + 1}" for y in range(2020, 2026)]  # 2019-2020 : pas d'année précédente


def feats(d, wx):
    t1 = wx.get(d - timedelta(1), {}).get("temp_moy")
    t2 = wx.get(d - timedelta(2), {}).get("temp_moy")
    return RT.cnette_features(d, wx[d], t1, t2, is_french_holiday)


def fit(rows):
    X = np.array([r[0] for r in rows]); y = np.array([r[1] for r in rows])
    mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1; sd[0] = 1; mu[0] = 0
    Z = (X - mu) / sd
    beta = np.linalg.solve(Z.T @ Z + RIDGE * np.eye(Z.shape[1]), Z.T @ y)
    coefs = beta / sd; coefs[0] = beta[0] - float((beta[1:] * mu[1:] / sd[1:]).sum())
    return [float(c) for c in coefs]


def twin_forecasts(act, wx, cn, season, coefs, last):
    """{(h, date cible): (couleur jumeau, C_std, seuil Rouge, seuil BR)} pour la saison."""
    y0 = int(season[:4])
    prev = [date(y0 - 1, 9, 1) + timedelta(k) for k in range((date(y0, 9, 1) - date(y0 - 1, 9, 1)).days)]
    nm = RT.Normalisation.from_history([RT.estimate_cnette(coefs, feats(d, wx)) for d in prev],
                                       [wx[d]["temp_moy"] for d in prev])
    out = {}
    D = date(y0, 9, 1)
    while D <= last - timedelta(2) and D < date(y0 + 1, 9, 1):
        J1 = D + timedelta(1)
        if not all(D + timedelta(i) in wx for i in range(8)) or D not in act or J1 not in act:
            D += timedelta(1); continue
        used = Counter(c for k, c in act.items() if date(y0, 9, 1) <= k <= J1)
        sr, sb = max(0, 22 - used["ROUGE"]), max(0, 43 - used["BLANC"])
        known = {k: c for k, c in act.items() if k <= J1}
        targets = [D + timedelta(h) for h in range(2, 6)]
        cstd = {t: nm.normalise(RT.estimate_cnette(coefs, feats(t, wx))) for t in targets}
        cols = RT.simulate(targets, cstd, sr, sb, known, is_french_holiday)
        for h, t in zip(range(2, 6), targets):
            out[(h, t.isoformat())] = cols[t]
        D += timedelta(1)
    return out, nm


def load_actuel(path):
    raw = json.load(open(path, encoding="utf-8"))["pairs"]
    return {(int(k.split("|")[0]), p[0]): (p[1], p[2]) for k, v in raw.items() for p in v}


def combine(pipe, twin, mode, d):
    if mode == "actuel":
        return pipe
    if mode == "jumeau":
        return twin
    if twin == "ROUGE" and pipe != "ROUGE":
        return "ROUGE"
    if mode == "H2" and pipe == "ROUGE" and twin == "BLEU":
        return "BLANC" if date.fromisoformat(d).weekday() != 6 else "BLEU"
    return pipe


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--actuel", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--export-model", default=None,
                    help="écrit les coefficients appris sur TOUTES les saisons (rte_twin_model.json)")
    a = ap.parse_args(argv)
    act, wx, cn = load_all()
    last = max(act)
    rows_all = {d: (feats(d, wx), cn[d]["cnette"]) for d in cn
                if d in wx and d - timedelta(2) in wx}
    if a.export_model:
        ds = sorted(rows_all)
        json.dump({"feature_names": list(RT.FEATURE_NAMES), "coefs": fit(list(rows_all.values())),
                   "ridge": RIDGE, "trained_on": f"{ds[0]}..{ds[-1]} ({len(ds)} jours Tempo)",
                   "source": "C_nette réelle eCO2mix (conso - éolien - solaire, 6h-6h) ; "
                             "météo observée weather_cache (db_dump.json)",
                   "generated_by": "tools/replay/rte_twin_forecast.py --export-model"},
                  open(a.export_model, "w", encoding="utf-8"), indent=1)
        print("modèle écrit", a.export_model)
        return
    if not a.actuel:
        ap.error("--actuel requis")
    pipe = load_actuel(a.actuel)
    res = defaultdict(list)  # (variante, saison, h) -> [(réel, prédit)]
    for s in SEASONS:
        coefs = fit([r for d, r in rows_all.items() if season_of(d) != s])
        test = [(r, RT.estimate_cnette(coefs, r[0])) for d, r in rows_all.items() if season_of(d) == s]
        rmse = (sum((r[1] - e) ** 2 for r, e in test) / len(test)) ** 0.5 if test else float("nan")
        tw, nm = twin_forecasts(act, wx, cn, s, coefs, last)
        print(f"{s} RMSE C_nette={rmse:.0f} MW (n={len(test)}) échelle={nm.echelle:.0f}", flush=True)
        for (h, t), c in tw.items():
            if (h, t) not in pipe:
                continue
            real, p = pipe[(h, t)]
            for mode in ("actuel", "jumeau", "H1", "H2"):
                res[(mode, s, h)].append((real, combine(p, c, mode, t)))
    report(res)
    if a.json:
        json.dump({f"{m}|{s}|{h}": v for (m, s, h), v in res.items()}, open(a.json, "w"))


def pooled(res, mode, seasons=SEASONS, hs=(2, 3, 4, 5)):
    return [x for s in seasons for h in hs for x in res.get((mode, s, h), [])]


def report(res):
    def f(m, c):
        r = m[c]; return f"{r['tp']}/{r['fp']}/{r['fn']} R={100 * (r['recall'] or 0):.1f} P={100 * (r['precision'] or 0):.1f}"
    for mode in ("actuel", "jumeau", "H1", "H2"):
        m = metrics(pooled(res, mode))
        print(f"== {mode:7s} J+2..J+5 ROUGE {f(m, 'ROUGE')} | BLANC {f(m, 'BLANC')}")
        for h in (2, 3, 4, 5):
            m = metrics(pooled(res, mode, hs=(h,)))
            print(f"   J+{h}      ROUGE {f(m, 'ROUGE')} | BLANC {f(m, 'BLANC')}")
        for s in SEASONS:
            m = metrics(pooled(res, mode, seasons=(s,)))
            print(f"   {s} ROUGE {f(m, 'ROUGE')} | BLANC {f(m, 'BLANC')}")
    rng = random.Random(42)
    for mode in ("jumeau", "H1", "H2"):
        dr, dfp = [], []
        for _ in range(2000):
            ss = [rng.choice(SEASONS) for _ in SEASONS]
            pa = [x for s in ss for h in (2, 3, 4, 5) for x in res.get(("actuel", s, h), [])]
            pb = [x for s in ss for h in (2, 3, 4, 5) for x in res.get((mode, s, h), [])]
            ma, mb = metrics(pa)["ROUGE"], metrics(pb)["ROUGE"]
            if ma["recall"] is None:
                continue
            dr.append(100 * (mb["recall"] - ma["recall"])); dfp.append(mb["fp"] - ma["fp"])
        dr.sort(); dfp.sort()
        q = lambda v, p: v[int(p * (len(v) - 1))]
        print(f"bootstrap {mode}: Δrappel ROUGE IC95 [{q(dr, .025):+.1f} ; {q(dr, .975):+.1f}] pts, "
              f"ΔFP IC95 [{q(dfp, .025):+d} ; {q(dfp, .975):+d}]")


if __name__ == "__main__":
    main()
