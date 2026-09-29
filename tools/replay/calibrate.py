#!/usr/bin/env python3
"""Calibration des probabilités par horizon, apprise sur les rejeux RÉELS.

Entrée : sortie de replay_pipeline.py --detail --calibration off (probabilités
brutes du vrai predict_range, cycles 7 h 30 et 18 h, J+1..J+5, 7 saisons).
Modèle : régression logistique multinomiale par horizon (delta 1..5) sur
predictor.calibration_features ; inférence = predictor.calibrate_prediction
(même code qu'en production : règles EDF à 0 %, cohérence couleur/probabilité).

  --loso OUT.json   évaluation honnête : chaque saison est calibrée par un modèle
                    appris SANS elle ; écrit un rejeu « détail » calibré pour
                    alert_sim.py et affiche Brier / fiabilité avant-après
  --export PATH     apprend sur toutes les saisons et écrit le modèle (calibration.json)

Usage : python tools/replay/calibrate.py /tmp/avant.json --loso /tmp/cal_loso.json
        python tools/replay/calibrate.py /tmp/avant.json --export calibration.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import predictor  # noqa: E402

FEATURES = ["lp_rouge", "lp_blanc", "lp_bleu", "pred_rouge", "pred_blanc", "twin_rouge",
            "twin_blanc", "twin_bleu", "ml_rouge", "ml_absent", "score"]
DELTAS = (1, 2, 3, 4, 5)
BINS = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001]


def as_pred(r: dict) -> dict:
    return {"date": r["date_cible"], "couleur_predite": r["predit"], "probabilite_rouge": r["p_rouge"],
            "probabilite_blanc": r["p_blanc"], "probabilite_bleu": r["p_bleu"],
            "rte_twin": r["jumeau"] or None, "score_ml_rouge": r["ml_rouge"],
            "score_risque": r["score_risque"], "jours_rouges_restants": r.get("restants_rouge"),
            "jours_blancs_restants": r.get("restants_blanc"), "confirmed": False}


def fit(rows: list[dict], C: float = 1.0, twin_split: bool = True) -> dict:
    from sklearn.linear_model import LogisticRegression
    model = {"method": "logistique multinomiale par horizon (tools/replay/calibrate.py)",
             "features": FEATURES, "horizons": {}}
    for d in DELTAS:
        sub = [r for r in rows if r["delta"] == d]
        X = [[predictor.calibration_features(as_pred(r))[f] for f in FEATURES] for r in sub]
        y = [r["reel"] for r in sub]
        lr = LogisticRegression(C=C, max_iter=5000).fit(X, y)
        model["horizons"][str(d)] = {"classes": list(lr.classes_), "features": FEATURES,
                                     "coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist(),
                                     "n": len(sub)}
        if twin_split:
            # V2 : modèle sans les variables du jumeau, pour les prédictions sans avis du jumeau
            idx = [i for i, f in enumerate(FEATURES) if not f.startswith("twin_")]
            lr2 = LogisticRegression(C=C, max_iter=5000).fit([[x[i] for i in idx] for x in X], y)
            model["horizons"][str(d)]["sans_jumeau"] = {
                "classes": list(lr2.classes_), "features": [FEATURES[i] for i in idx],
                "coef": lr2.coef_.tolist(), "intercept": lr2.intercept_.tolist(), "n": len(sub)}
    return model


def apply(rows: list[dict], model: dict) -> list[dict]:
    out = []
    for r in rows:
        p = as_pred(r)
        predictor.calibrate_prediction(p, r["delta"], model)
        assert p["couleur_predite"] == r["predit"]
        out.append(dict(r, p_rouge=p["probabilite_rouge"], p_blanc=p["probabilite_blanc"],
                        p_bleu=p["probabilite_bleu"], p_rouge_brute=r["p_rouge"]))
    return out


def brier(rows, cls="ROUGE", key="p_rouge"):
    return sum((r[key] - (r["reel"] == cls)) ** 2 for r in rows) / len(rows) if rows else float("nan")


def brier3(rows):
    ks = {"ROUGE": "p_rouge", "BLANC": "p_blanc", "BLEU": "p_bleu"}
    return sum(sum((r[k] - (r["reel"] == c)) ** 2 for c, k in ks.items()) for r in rows) / len(rows)


def reliability(rows, key="p_rouge", cls="ROUGE"):
    out = []
    for lo, hi in zip(BINS, BINS[1:]):
        b = [r for r in rows if lo <= r[key] < hi]
        if b:
            out.append((lo, hi, len(b), sum(r[key] for r in b) / len(b), sum(r["reel"] == cls for r in b) / len(b)))
    return out


def report(raw: list[dict], cal: list[dict]) -> dict:
    res = {"brier_rouge": {}, "brier_multi": {}}
    elig = lambda r: date.fromisoformat(r["date_cible"]).month in (11, 12, 1, 2, 3) and \
        date.fromisoformat(r["date_cible"]).weekday() < 5
    print("horizon   n     Brier ROUGE brut -> calibré   (jours ouvrés nov-mars)      Brier 3 classes")
    for d in (*DELTAS, "tous"):
        a = [r for r in raw if d == "tous" or r["delta"] == d]
        b = [r for r in cal if d == "tous" or r["delta"] == d]
        ea, eb = [r for r in a if elig(r)], [r for r in b if elig(r)]
        res["brier_rouge"][str(d)] = (brier(a), brier(b))
        res["brier_multi"][str(d)] = (brier3(a), brier3(b))
        print(f"J+{d!s:5s} {len(a):6d}   {brier(a):.4f} -> {brier(b):.4f}          {brier(ea):.4f} -> {brier(eb):.4f}"
              f"            {brier3(a):.4f} -> {brier3(b):.4f}")
    for name, rows in (("brut", raw), ("calibré", cal)):
        print(f"\nFiabilité ROUGE {name} (J+1..J+5) : tranche, n, annoncé moyen, observé")
        for lo, hi, n, m, o in reliability(rows):
            print(f"  [{lo:.1f} ; {min(hi, 1):.1f}[  {n:6d}  {100 * m:5.1f} %  {100 * o:5.1f} %")
        pr = [r for r in rows if r["predit"] == "ROUGE"]
        print(f"  prédits ROUGE : {len(pr)}, annoncé moyen {100 * sum(r['p_rouge'] for r in pr) / len(pr):.1f} %, "
              f"réels {100 * sum(r['reel'] == 'ROUGE' for r in pr) / len(pr):.1f} %")
    res["reliability"] = {"brut": reliability(raw), "calibre": reliability(cal)}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("replay")
    ap.add_argument("--loso", default=None)
    ap.add_argument("--export", default=None)
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--sans-v2", action="store_true",
                    help="première passe : pas de modèle « sans jumeau » (variante V1 de l'audit)")
    ap.add_argument("--coherence", choices=("minimale", "existante"), default=predictor.CALIBRATION_COHERENCE,
                    help="cohérence couleur/probabilité après calibration (predictor.CALIBRATION_COHERENCE)")
    x = ap.parse_args(argv)
    predictor.CALIBRATION_COHERENCE = x.coherence
    blob = json.load(open(x.replay, encoding="utf-8"))
    cols = blob["detail_cols"]
    rows = [dict(zip(cols, r)) for r in blob["detail"]]
    if any(r.get("p_rouge_brute") is not None for r in rows):
        raise SystemExit("rejeu déjà calibré : relancer replay_pipeline.py avec --calibration off")
    if x.loso:
        by_s = defaultdict(list)
        for r in rows:
            by_s[r["saison"]].append(r)
        cal = []
        for s in sorted(by_s):
            cal += apply(by_s[s], fit([r for r in rows if r["saison"] != s], x.C, not x.sans_v2))
            print("LOSO", s, flush=True)
        raw = [r for s in sorted(by_s) for r in by_s[s]]
        res = report(raw, cal)
        json.dump(dict(blob, detail=[[r.get(c) for c in cols] for r in cal], calibration_report=res),
                  open(x.loso, "w", encoding="utf-8"))
    if x.export:
        model = fit(rows, x.C, not x.sans_v2)
        model["source"] = {"replay_args": blob.get("args"), "rows": len(rows),
                           "seasons": sorted({r["saison"] for r in rows})}
        json.dump(model, open(x.export, "w", encoding="utf-8"), indent=1)
        print("modèle écrit :", x.export)
    return 0


if __name__ == "__main__":
    sys.exit(main())
