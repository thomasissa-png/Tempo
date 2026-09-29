#!/usr/bin/env python3
"""Compare des rejeux (sorties JSON de replay_pipeline.py), par saison, J+2 à J+5.

Usage : python tools/replay/compare.py avant=/tmp/avant.json apres=/tmp/apres.json
Affiche pour chaque rejeu ROUGE et BLANC en VP/FP/FN, rappel, précision,
cumulés et par saison, puis l'écart du dernier rejeu par rapport au premier.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml_train import metrics  # noqa: E402


def load(path: str) -> dict:
    raw = json.load(open(path, encoding="utf-8"))["pairs"]
    out = {}
    for key, pairs in raw.items():
        h, s = key.split("|")
        out[(int(h), s)] = [tuple(p[-2:]) for p in pairs]  # (réel, prédit) ; p[0] = date cible
    return out


def pool(res: dict, season: str | None = None, hmin: int = 2, hmax: int = 5) -> list:
    return [x for (h, s), v in res.items() if hmin <= h <= hmax and (season is None or s == season) for x in v]


def fmt(m: dict, c: str) -> str:
    r = m[c]
    rec = "-" if r["recall"] is None else f"{100 * r['recall']:.1f}"
    pre = "-" if r["precision"] is None else f"{100 * r['precision']:.1f}"
    return f"{r['tp']:4d}/{r['fp']:4d}/{r['fn']:4d} R={rec:>5s} P={pre:>5s}"


def main(argv: list[str]) -> int:
    runs = {k: load(v) for k, v in (x.split("=", 1) for x in argv)}
    seasons = sorted({s for res in runs.values() for (_, s) in res})
    for name, res in runs.items():
        print(f"== {name}   (VP/FP/FN, rappel, précision)")
        m = metrics(pool(res))
        print(f"  {'J+2..J+5':10s} ROUGE {fmt(m, 'ROUGE')} | BLANC {fmt(m, 'BLANC')}")
        for h in (2, 3, 4, 5):
            m = metrics(pool(res, hmin=h, hmax=h))
            print(f"  {'J+%d' % h:10s} ROUGE {fmt(m, 'ROUGE')} | BLANC {fmt(m, 'BLANC')}")
        for s in seasons:
            m = metrics(pool(res, s))
            print(f"  {s:10s} ROUGE {fmt(m, 'ROUGE')} | BLANC {fmt(m, 'BLANC')}")
    if len(runs) >= 2:
        names = list(runs)
        a, b = runs[names[0]], runs[names[-1]]
        print(f"== écart {names[-1]} - {names[0]} (VP, FP) ROUGE | BLANC")
        for s in [None] + seasons:
            ma, mb = metrics(pool(a, s)), metrics(pool(b, s))
            d = {c: (mb[c]["tp"] - ma[c]["tp"], mb[c]["fp"] - ma[c]["fp"]) for c in ("ROUGE", "BLANC")}
            print(f"  {s or 'total':10s} ROUGE VP {d['ROUGE'][0]:+d} FP {d['ROUGE'][1]:+d} | "
                  f"BLANC VP {d['BLANC'][0]:+d} FP {d['BLANC'][1]:+d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
