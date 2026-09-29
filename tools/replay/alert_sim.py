#!/usr/bin/env python3
"""Performance VÉCUE par les abonnés : alertes rouges reçues, rejouées.

Entrée : sortie de replay_pipeline.py lancé avec --detail (cycles 7 h 30 et 18 h,
prédictions non confirmées J+1..J+5 du vrai predict_range, météo observée).
Reproduit la logique d'envoi de production :
  - scheduler : alertes « prévision » pour 1 <= delta <= 3, prédiction non
    confirmée, couleur ROUGE ; cycle 7 h 30 -> abonnés « matin », cycle 18 h ->
    abonnés « soir » (à 18 h, J+1 est déjà publié par EDF donc confirmé) ;
  - alerts.send_alerts_for_prediction : saison rouge (jour d'envoi en
    novembre-mars), delta <= delai_alerte, une seule alerte par (abonné, date) ;
    décision « faut-il alerter » = alerts.rouge_alert_due (code livré) ou la
    règle d'origine seuil <= round(p_rouge * 100) (--policy origine) ;
  - alerts.send_change_alerts (indicateur secondaire) : changement de couleur
    impliquant ROUGE entre le cycle de 7 h 30 et celui de 18 h du même jour,
    même horizon, delta <= 5, envoyé si delta <= delai ou abonné déjà alerté.
Rouge « signalé » = au moins une alerte prévision ROUGE reçue avant la
publication EDF. Fausse alerte = jour non ROUGE pour lequel l'abonné a reçu une
alerte prévision ROUGE. Hypothèse : alerte_blanc désactivée (défaut).

Usage :
  python tools/replay/alert_sim.py /tmp/avant.json [--policy code|origine|filet|stricte|t=70:0,80:0.5,90:0.6]
                                   [--json out.json]
  python tools/replay/alert_sim.py --compare /tmp/avant_alertes.json /tmp/apres_alertes.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SEUILS = (70, 80, 90)
DELAIS = (1, 2, 3)
HEURES = ("matin", "soir")
DEFAUT = ("matin", 70, 3)  # register_user / modal : seuil 70, delai 3, matin


def _origine(seuil: int, pred: dict, delta: int) -> bool:
    return pred["couleur_predite"] == "ROUGE" and seuil <= round(pred.get("probabilite_rouge", 0) * 100)


def _filet(seuil: int, pred: dict, delta: int) -> bool:
    return pred["couleur_predite"] == "ROUGE"


def seuil_map_policy(tmap: dict[int, float]):
    """P3 : couleur ROUGE et p_rouge (calibrée) >= tmap[seuil]."""
    def due(seuil: int, pred: dict, delta: int) -> bool:
        t = tmap.get(seuil, seuil / 100)
        return pred["couleur_predite"] == "ROUGE" and pred.get("probabilite_rouge", 0) >= t - 1e-9
    return due


def _policy(name):
    """code (alerts.rouge_alert_due livré) | origine | filet | stricte | t=70:0.3,80:0.5,90:0.6"""
    if callable(name):
        return name
    if name == "origine":
        return _origine
    if name == "filet":
        return _filet
    if name == "stricte":
        return seuil_map_policy({})
    if name.startswith("t="):
        return seuil_map_policy({int(k): float(v) for k, v in (x.split(":") for x in name[2:].split(","))})
    from alerts import rouge_alert_due
    return rouge_alert_due


def load_runs(path: str) -> list[dict]:
    blob = json.load(open(path, encoding="utf-8"))
    if "detail" not in blob:
        raise SystemExit("rejeu sans --detail : relancer replay_pipeline.py --detail")
    cols = blob["detail_cols"]
    return [dict(zip(cols, r)) for r in blob["detail"]]


def simulate(runs: list[dict], policy_name="code") -> dict:
    due = _policy(policy_name)
    by_cycle = defaultdict(dict)  # (cycle, jour) -> {date_cible: run}
    for r in runs:
        by_cycle[(r["cycle"], r["jour_emission"])][r["date_cible"]] = r
    actual = {r["date_cible"]: (r["reel"], r["saison"]) for r in runs}
    rouge_days = {d for d, (c, _) in actual.items() if c == "ROUGE"}
    days = sorted({k[1] for k in by_cycle})
    out = {}
    for heure in HEURES:
        for seuil in SEUILS:
            for delai in DELAIS:
                pred_alert, change_rouge, change_logged = set(), set(), set()
                for day in days:
                    if date.fromisoformat(day).month not in (11, 12, 1, 2, 3):
                        continue  # alerts._is_red_season(jour d'envoi)
                    cycle = by_cycle.get(("matin" if heure == "matin" else "soir", day), {})
                    for t, r in cycle.items():
                        dl = r["delta"]
                        if not (1 <= dl <= 3) or dl > delai or t in pred_alert or t in change_logged:
                            continue
                        pred = {"couleur_predite": r["predit"], "probabilite_rouge": r["p_rouge"],
                                "probabilite_blanc": r["p_blanc"], "probabilite_bleu": r["p_bleu"]}
                        if due(seuil, pred, dl):
                            pred_alert.add(t)
                    # changements 7 h 30 -> 18 h (tous abonnés, sans filtre horaire)
                    am, pm = by_cycle.get(("matin", day), {}), by_cycle.get(("soir", day), {})
                    for t, r in pm.items():
                        a = am.get(t)
                        if not a or a["delta"] != r["delta"] or a["predit"] == r["predit"]:
                            continue
                        if "ROUGE" not in (a["predit"], r["predit"]) or not (1 <= r["delta"] <= 5):
                            continue
                        if (r["delta"] <= delai or t in pred_alert) and t not in change_logged:
                            change_logged.add(t)  # bloque ensuite les alertes prévision (dédup B6)
                            if r["predit"] == "ROUGE":
                                change_rouge.add(t)
                signaled = pred_alert & rouge_days
                seasons = defaultdict(lambda: [0, 0, 0])  # signalés, rouges, fausses alertes
                for d in rouge_days:
                    seasons[actual[d][1]][1] += 1
                for d in signaled:
                    seasons[actual[d][1]][0] += 1
                for d in pred_alert - rouge_days:
                    seasons[actual[d][1]][2] += 1
                out[f"{heure}|{seuil}|{delai}"] = {
                    "rouges": len(rouge_days), "signales": len(signaled),
                    "fausses_alertes": len(pred_alert - rouge_days),
                    "signales_avec_changements": len((pred_alert | change_rouge) & rouge_days),
                    "saisons": dict(seasons),
                }
    return out


def pct(a, b):
    return f"{100 * a / b:.1f} %" if b else "n/a"


def show(res: dict) -> None:
    print("heure  seuil délai  rouges signalés        fausses alertes  (avec changements)")
    for k, v in res.items():
        h, s, d = k.split("|")
        print(f"{h:6s} {s:>5s} J-{d}   {v['signales']:3d}/{v['rouges']} {pct(v['signales'], v['rouges']):>8s}"
              f"   {v['fausses_alertes']:5d}            {pct(v['signales_avec_changements'], v['rouges'])}")


def compare(a: dict, b: dict) -> None:
    print("profil              signalés avant -> après        fausses alertes avant -> après")
    for k in a:
        x, y = a[k], b[k]
        print(f"{k:18s}  {x['signales']:3d} ({pct(x['signales'], x['rouges'])}) -> {y['signales']:3d} "
              f"({pct(y['signales'], y['rouges'])})    {x['fausses_alertes']:4d} -> {y['fausses_alertes']:4d}")
    k = "|".join(map(str, DEFAUT))
    print(f"\nprofil par défaut {k}, par saison (signalés/rouges, fausses alertes) :")
    for s in sorted(a[k]["saisons"]):
        sa, sb = a[k]["saisons"][s], b[k]["saisons"].get(s, [0, 0, 0])
        print(f"  {s}  {sa[0]}/{sa[1]} fa {sa[2]}  ->  {sb[0]}/{sb[1]} fa {sb[2]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("replay", nargs="?")
    ap.add_argument("--policy", default="code",
                    help="code (alerts.rouge_alert_due) | origine | filet | stricte | t=70:0.3,80:0.5,90:0.6")
    ap.add_argument("--json", default=None)
    ap.add_argument("--compare", nargs=2, metavar=("AVANT", "APRES"))
    x = ap.parse_args(argv)
    if x.compare:
        compare(*(json.load(open(p, encoding="utf-8")) for p in x.compare))
        return 0
    res = simulate(load_runs(x.replay), x.policy)
    show(res)
    if x.json:
        json.dump(res, open(x.json, "w", encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
