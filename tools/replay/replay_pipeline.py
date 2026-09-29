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
  --calibration off|on  calibration des probabilités (défaut : predictor.PROBA_CALIBRATION_ENABLED)
  --detail  ajoute le cycle de 7 h 30 (J+1 pas encore publié) et, pour chaque cycle,
            les prédictions non confirmées J+1..J+5 avec leurs probabilités
            (entrée de tools/replay/alert_sim.py et tools/replay/calibrate.py)
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
N_DETAIL = 5  # --detail : prédictions non confirmées J+1..J+5 (alertes, calibration)
DETAIL_COLS = ["cycle", "jour_emission", "date_cible", "delta", "reel", "predit",
               "p_rouge", "p_blanc", "p_bleu", "jumeau", "score_risque", "ml_rouge", "saison",
               "p_rouge_brute", "restants_rouge", "restants_blanc"]


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
        thresholds: tuple[float, float] | None = None, rte_twin_on: bool = False,
        morning: bool = False, detail: list | None = None, calibration: str = "config") -> dict:
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
    if calibration != "config":
        predictor.PROBA_CALIBRATION_ENABLED = calibration == "on"

    def _predict_at(kind: str, day: date):
        """Cycle de production du jour `day` : « soir » = 18 h (couleurs EDF
        connues jusqu'à day+1, RTE jusqu'à day-1) ; « matin » = 7 h 30 (J+1 pas
        encore publié : couleurs connues jusqu'à day, RTE jusqu'à day-2)."""
        known_to = day + timedelta(1) if kind == "soir" else day
        fc = []
        for i in range(N_FC):
            r = WX.get((day + timedelta(i)).isoformat())
            if not r:
                break
            fc.append(dict(r, source="arome" if i <= 2 else "arpege"))
        if len(fc) < N_FC or any((day + timedelta(k)).isoformat() not in ACT
                                 for k in range((known_to - day).days + 1)):
            return None
        used = defaultdict(int)
        k = season_start(day)
        while k <= known_to:
            used[ACT.get(k.isoformat(), "")] += 1
            k += timedelta(1)
        n_days = (season_end(day) - season_start(day)).days + 1
        state["remaining"] = {
            "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used["ROUGE"]),
            "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used["BLANC"]),
            "BLEU": max(0, n_days - Config.JOURS_ROUGES_TOTAL - Config.JOURS_BLANCS_TOTAL - used["BLEU"]),
        }
        lo = (day - timedelta(7)).isoformat()
        state["recent"] = {d: c for d, c in ACT.items() if lo <= d <= known_to.isoformat()}
        state["future"] = {(day + timedelta(k)).isoformat(): ACT[(day + timedelta(k)).isoformat()]
                           for k in range((known_to - day).days + 1)}
        _Today.value = day
        predictor.days_left_in_season = lambda D=day: max(0, (season_end(D) - D).days)
        cut["d"] = day - timedelta(1 if kind == "soir" else 2)
        return predictor.predict_range(fc)

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
            runs_today = (("soir", D),) if not morning else (("matin", D), ("soir", D))
            for kind, day in runs_today:
                preds = _predict_at(kind, day)
                if preds is None:
                    continue
                for i, p in enumerate(preds):
                    t = p["date"] if isinstance(p["date"], str) else p["date"].isoformat()
                    if kind == "soir" and i in HORIZONS and t in ACT:
                        out[(i, s)].append((t, ACT[t], p["couleur_predite"]))
                    if detail is not None and 1 <= i <= N_DETAIL and t in ACT and not p.get("confirmed"):
                        detail.append([kind, day.isoformat(), t, i, ACT[t], p["couleur_predite"],
                                       p["probabilite_rouge"], p["probabilite_blanc"], p["probabilite_bleu"],
                                       p.get("rte_twin") or "", p.get("score_risque"),
                                       p.get("score_ml_rouge"), s,
                                       (p.get("probabilites_brutes") or [None])[0],
                                       p.get("jours_rouges_restants"), p.get("jours_blancs_restants")])
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
    ap.add_argument("--calibration", choices=("config", "off", "on"), default="config",
                    help="predictor.PROBA_CALIBRATION_ENABLED pendant le rejeu (défaut : valeur du module)")
    ap.add_argument("--detail", action="store_true",
                    help="ajoute les cycles 7 h 30 et le détail J+1..J+5 (probabilités, jumeau) "
                         "pour alert_sim.py et calibrate.py ; environ deux fois plus long")
    ap.add_argument("--out", required=True, help="JSON des paires (réel, prédit) par horizon/saison")
    a = ap.parse_args(argv)
    thr = tuple(float(x) for x in a.thresholds.split(",")) if a.thresholds else None
    detail = [] if a.detail else None
    res = run(a.weights, a.ml, thr, a.rte_twin == "on", morning=a.detail, detail=detail,
              calibration=a.calibration)
    blob = {"args": vars(a), "pairs": {f"{h}|{s}": v for (h, s), v in res.items()}}
    if detail is not None:
        blob["detail_cols"], blob["detail"] = DETAIL_COLS, detail
    json.dump(blob, open(a.out, "w", encoding="utf-8"))
    allp = [x[1:] for v in res.values() for x in v]
    m = T.metrics(allp)
    for c in ("ROUGE", "BLANC"):
        r = m[c]
        print(f"J+2..J+5 {c} VP/FP/FN {r['tp']}/{r['fp']}/{r['fn']} rappel={r['recall']} précision={r['precision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
