#!/usr/bin/env python3
"""Rejeu du pipeline de production (predictor.predict_range) sur les saisons réelles.

Appelle le VRAI predictor.predict_range (pas une copie de sa boucle) : tout
changement de predictor.py, ml_scorer.py ou config.py est donc rejoué tel quel.
Seules les dépendances base de données / horloge sont remplacées par les
données RÉELLES de db_dump.json, telles qu'elles étaient connues à 18 h le
jour D :
  - couleurs EDF connues jusqu'à D+1 (J+1 publié à 11 h) ;
  - quota restant = 22 ROUGE / 43 BLANC moins les couleurs de la saison
    jusqu'à D+1 inclus (comme tempo_client.get_remaining_days) ;
  - rte_daily réalisé connu jusqu'à D-1 ;
  - prévisions météo = météo OBSERVÉE de D à D+7 (borne optimiste : pas
    d'archive des prévisions dans le dépôt) ;
  - corrections d'apprentissage désactivées (aucun historique fiable).
Évaluation : horizons J+2 à J+5 (index 2..5 de la liste, qui commence à D).

Aucune donnée inventée : un jour D sans météo observée sur D..D+7 ou sans
couleur EDF pour D et D+1 est sauté.

Usage (depuis la racine du dépôt, Python avec les dépendances du projet) :
  python tools/replay/replay_pipeline.py --out /tmp/avant.json
  python tools/replay/replay_pipeline.py --weights prod --ml folds=/tmp/folds.pkl --out /tmp/x.json
  --weights default|prod   Config.DEFAULT_WEIGHTS ou dernière ligne non rejetée de weights_history
  --thresholds 0.19,0.20   seuils ML imposés (sinon ceux du modèle / du pli)
  --rte-twin off|on  jumeau RTE coupé ou branché (défaut : Config.RTE_TWIN_ENABLED)
  --ml shipped|none|folds=PATH
      shipped : ml_model.pkl du dépôt (attention : vu à l'entraînement sur ces saisons)
      none    : ML et micro-ML désactivés
      folds   : un modèle hors saison par saison (python ml_train.py --save-folds PATH)
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ml_train as T  # noqa: E402  (load_data, make_rte_lag_fn, metrics, season_of)
import ml_scorer  # noqa: E402
import predictor  # noqa: E402
from config import Config  # noqa: E402

HORIZONS = (2, 3, 4, 5)
N_FC = 8  # D..D+7 : même fenêtre que la cohérence thermique en prod


def season_end(d: date) -> date:
    return date(d.year + 1, 8, 31) if d.month >= 9 else date(d.year, 8, 31)


def season_start(d: date) -> date:
    return date(d.year, 9, 1) if d.month >= 9 else date(d.year - 1, 9, 1)


def load_weights(mode: str, dump: dict) -> dict:
    if mode == "prod":
        wh = [r for r in dump["weights_history"] if not r["commentaire"].startswith("REJETE")]
        return json.loads(wh[-1]["weights_json"])
    return dict(Config.DEFAULT_WEIGHTS)


class _Today:
    """Date simulée : predictor.date.today() renvoie le jour D rejoué."""
    value: date | None = None


class _FakeDate(date):
    @classmethod
    def today(cls):
        return _Today.value or date.today()


def _setup_rte_twin(enabled: bool, WX: dict):
    """Jumeau RTE : interrupteur, météo du dump pour la normalisation, coefficients
    appris hors saison (renvoie une fonction saison -> None qui les installe)."""
    Config.RTE_TWIN_ENABLED = enabled
    if not enabled:
        return lambda s: None
    import rte_twin
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rte_twin_data import load_all, season_of as s_of
    from rte_twin_forecast import feats, fit
    act, wx, cn = load_all()
    rows = {d: (feats(d, wx), cn[d]["cnette"]) for d in cn if d in wx and d - timedelta(2) in wx}
    rte_twin._load_weather_history = lambda a, b: {d: w for d, w in wx.items() if a <= d <= b}

    def install(season: str):
        coefs = fit([r for d, r in rows.items() if s_of(d) != season])
        rte_twin._MODEL = {"feature_names": list(rte_twin.FEATURE_NAMES), "coefs": coefs}
        rte_twin._NORM_CACHE.clear()
    return install


def run(weights_mode: str = "default", ml_mode: str = "shipped",
        thresholds: tuple[float, float] | None = None, rte_twin_on: bool = False) -> dict:
    dump = json.load(open(ROOT / "db_dump.json", encoding="utf-8"))["tables"]
    weights = load_weights(weights_mode, dump)
    data = T.load_data()
    ACT, WX = data["actuals"], data["weather"]
    last = date.fromisoformat(data["last"])

    cut = {"d": None}
    ml_scorer._get_rte_lag = lambda t, lag_days=1, window=1: \
        T.make_rte_lag_fn(data["rte"], cut["d"])(t, lag_days, window)
    ml_scorer._load_model()
    folds = None
    if ml_mode == "none":
        ml_scorer._MODEL = None
        ml_scorer._LOAD_ERROR = True
        import confusion_zone_ml
        confusion_zone_ml.predict_confusion_zone = lambda **k: None
    elif ml_mode.startswith("folds="):
        folds = pickle.load(open(ml_mode[6:], "rb"))

    try:
        import performance_tracker
        performance_tracker.get_active_learnings = lambda: {}
    except Exception:  # pragma: no cover - dépend de l'environnement
        pass

    state: dict = {}
    predictor.get_remaining_days = lambda: dict(state["remaining"])
    predictor.get_current_weights = lambda: dict(weights)
    predictor._load_recent_actuals = lambda: dict(state["recent"])
    predictor._load_future_actuals = lambda: dict(state["future"])

    out = defaultdict(list)  # (horizon, saison) -> [(date cible, réel, prédit)]
    seasons = sorted({T.season_of(date.fromisoformat(d)) for d in ACT})
    predictor.date = _FakeDate
    install_twin = _setup_rte_twin(rte_twin_on, WX)
    for s in seasons:
        install_twin(s)
        if folds:
            ml_scorer._MODEL = folds[s]["model"]
            ml_scorer._METADATA = dict(folds[s]["thresholds"], model_name="loso")
        if thresholds:
            ml_scorer._METADATA = dict(ml_scorer._METADATA or {}, rouge_threshold=thresholds[0],
                                       blanc_threshold=thresholds[1])
        y0 = int(s[:4])
        D = date(y0, 9, 1)
        while D <= last - timedelta(2) and D < date(y0 + 1, 9, 1):
            J1 = D + timedelta(1)
            fc = []
            for i in range(N_FC):
                r = WX.get((D + timedelta(i)).isoformat())
                if not r:
                    break
                fc.append(dict(r, source="arome" if i <= 2 else "arpege"))
            if len(fc) < N_FC or D.isoformat() not in ACT or J1.isoformat() not in ACT:
                D += timedelta(1)
                continue
            used = defaultdict(int)
            k = season_start(D)
            while k <= J1:
                used[ACT.get(k.isoformat(), "")] += 1
                k += timedelta(1)
            n_days = (season_end(D) - season_start(D)).days + 1
            state["remaining"] = {
                "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used["ROUGE"]),
                "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used["BLANC"]),
                "BLEU": max(0, n_days - Config.JOURS_ROUGES_TOTAL - Config.JOURS_BLANCS_TOTAL - used["BLEU"]),
            }
            lo = (D - timedelta(7)).isoformat()
            state["recent"] = {d: c for d, c in ACT.items() if lo <= d <= J1.isoformat()}
            state["future"] = {D.isoformat(): ACT[D.isoformat()], J1.isoformat(): ACT[J1.isoformat()]}
            _Today.value = D
            predictor.days_left_in_season = lambda D=D: max(0, (season_end(D) - D).days)
            cut["d"] = D - timedelta(1)

            preds = predictor.predict_range(fc)
            for h in HORIZONS:
                p = preds[h]
                t = p["date"] if isinstance(p["date"], str) else p["date"].isoformat()
                if t in ACT:
                    out[(h, s)].append((t, ACT[t], p["couleur_predite"]))
            D += timedelta(1)
        print(s, flush=True)
    return dict(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weights", choices=("default", "prod"), default="default")
    ap.add_argument("--ml", default="shipped", help="shipped | none | folds=PATH")
    ap.add_argument("--thresholds", default=None,
                    help="seuils ML ROUGE,BLANC imposés (ex. 0.19,0.20 = ceux de ml_model.pkl)")
    ap.add_argument("--rte-twin", choices=("off", "on"), default="on" if Config.RTE_TWIN_ENABLED else "off",
                    help="Config.RTE_TWIN_ENABLED pendant le rejeu (défaut : valeur de config.py ; "
                         "coefficients C_nette appris hors saison)")
    ap.add_argument("--out", required=True, help="JSON des paires (réel, prédit) par horizon/saison")
    a = ap.parse_args(argv)
    thr = tuple(float(x) for x in a.thresholds.split(",")) if a.thresholds else None
    res = run(a.weights, a.ml, thr, a.rte_twin == "on")
    json.dump({"args": vars(a), "pairs": {f"{h}|{s}": v for (h, s), v in res.items()}},
              open(a.out, "w", encoding="utf-8"))
    allp = [x[1:] for v in res.values() for x in v]
    m = T.metrics(allp)
    for c in ("ROUGE", "BLANC"):
        r = m[c]
        print(f"J+2..J+5 {c} VP/FP/FN {r['tp']}/{r['fp']}/{r['fn']} rappel={r['recall']} précision={r['precision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
