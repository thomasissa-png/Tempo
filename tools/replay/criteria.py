#!/usr/bin/env python3
"""Critères d'acceptation d'une variante contre l'actuel (sorties JSON de replay_pipeline.py).

Critères fixés le 2026-09-29 (docs/audits/2026-09-29-jumeau-rte.md, section 2),
J+2..J+5 poolés, mêmes couples (horizon, date cible) :
  C1 rappel ROUGE >= actuel
  C2 FP ROUGE <= actuel, ou <= actuel x 1,05 si le rappel ROUGE gagne >= 3 points
  C3 rappel ROUGE dégradé sur au plus 1 saison
  C4 rappel BLANC >= actuel - 3 points
  R1 à chaque horizon J+2..J+5 : rappel ROUGE >= actuel - 2 points
  R2 bootstrap saisons (2 000 tirages) : IC95 bas de Δrappel ROUGE >= -1 point ET
     (IC95 bas de Δrappel > 0 ou IC95 haut de ΔFP < 0)

Usage : python tools/replay/criteria.py actuel.json variante.json [--seasons 2020-2021,2021-2022,...]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml_train import metrics  # noqa: E402


def load(path):
    raw = json.load(open(path, encoding="utf-8"))["pairs"]
    return {(int(k.split("|")[0]), k.split("|")[1], p[0]): (p[1], p[2]) for k, v in raw.items() for p in v}


def pool(res, keys, seasons, hs=(2, 3, 4, 5)):
    return [res[k] for k in keys if k[1] in seasons and k[0] in hs]


def rec(pairs):
    r = metrics(pairs)["ROUGE"]["recall"]
    return None if r is None else 100 * r


def evaluate(a, b, seasons, n_boot=2000, seed=42):
    keys = sorted(set(a) & set(b))
    ma, mb = metrics(pool(a, keys, seasons)), metrics(pool(b, keys, seasons))
    ra, rb = 100 * ma["ROUGE"]["recall"], 100 * mb["ROUGE"]["recall"]
    fa, fb = ma["ROUGE"]["fp"], mb["ROUGE"]["fp"]
    ba, bb = 100 * ma["BLANC"]["recall"], 100 * mb["BLANC"]["recall"]
    degr = [s for s in seasons if (rec(pool(b, keys, [s])) or 0) < (rec(pool(a, keys, [s])) or 0) - 1e-9]
    by_h = {h: (rec(pool(a, keys, seasons, (h,))), rec(pool(b, keys, seasons, (h,)))) for h in (2, 3, 4, 5)}
    rng = random.Random(seed)
    dr, dfp = [], []
    for _ in range(n_boot):
        ss = [rng.choice(seasons) for _ in seasons]
        pa = [x for s in ss for x in pool(a, keys, [s])]
        pb = [x for s in ss for x in pool(b, keys, [s])]
        if rec(pa) is None:
            continue
        dr.append(rec(pb) - rec(pa))
        dfp.append(metrics(pb)["ROUGE"]["fp"] - metrics(pa)["ROUGE"]["fp"])
    dr.sort(); dfp.sort()
    q = lambda v, p: v[int(p * (len(v) - 1))]
    ic_r, ic_f = (q(dr, .025), q(dr, .975)), (q(dfp, .025), q(dfp, .975))
    out = {
        "C1 rappel ROUGE": (rb >= ra, f"{ra:.1f} -> {rb:.1f} %"),
        "C2 FP ROUGE": (fb <= fa or (rb - ra >= 3 and fb <= 1.05 * fa), f"{fa} -> {fb}"),
        "C3 saisons dégradées <= 1": (len(degr) <= 1, ", ".join(degr) or "aucune"),
        "C4 rappel BLANC >= -3 pts": (bb >= ba - 3, f"{ba:.1f} -> {bb:.1f} %"),
        "R1 horizons >= -2 pts": (all(y >= x - 2 for x, y in by_h.values()),
                                  " ; ".join(f"J+{h} {x:.1f}->{y:.1f}" for h, (x, y) in by_h.items())),
        "R2 bootstrap": (ic_r[0] >= -1 and (ic_r[0] > 0 or ic_f[1] < 0),
                         f"Δrappel IC95 [{ic_r[0]:+.1f} ; {ic_r[1]:+.1f}] pts, ΔFP IC95 [{ic_f[0]:+d} ; {ic_f[1]:+d}]"),
    }
    return out, ma, mb, keys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("actuel"); ap.add_argument("variante")
    ap.add_argument("--seasons", default=None)
    x = ap.parse_args(argv)
    a, b = load(x.actuel), load(x.variante)
    seasons = x.seasons.split(",") if x.seasons else sorted({k[1] for k in a})
    out, ma, mb, keys = evaluate(a, b, seasons)
    print(f"couples comparés : {len([k for k in keys if k[1] in seasons])} ; saisons : {', '.join(seasons)}")
    for c in ("ROUGE", "BLANC"):
        print(f"{c} actuel {ma[c]['tp']}/{ma[c]['fp']}/{ma[c]['fn']}  variante {mb[c]['tp']}/{mb[c]['fp']}/{mb[c]['fn']}")
    ok = True
    for k, (passed, detail) in out.items():
        ok &= passed
        print(f"{'PASS' if passed else 'ÉCHEC'}  {k:28s} {detail}")
    print("VERDICT :", "TOUS LES CRITÈRES PASSÉS" if ok else "REFUSÉ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
