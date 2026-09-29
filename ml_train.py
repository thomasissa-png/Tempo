#!/usr/bin/env python3
"""Ré-entraînement reproductible du modèle ML Tempo (ml_model.pkl).

Données : uniquement les données RÉELLES de db_dump.json (couleurs EDF non
synthétiques, météo observée Open-Meteo Archive, rte_daily réalisé).
Aucune donnée inventée ni simulée.

Source de vérité unique des variables : ml_scorer._build_features (appelée
telle quelle, avec les mêmes arguments qu'en production) :
  - prédiction émise à 18 h le jour D pour la cible T = D + h (h = 2..5) ;
  - forecasts = liste météo commençant à D (comme fetch_forecast), target_idx = h ;
  - actuals_cache = couleurs EDF de D-7 à D+1 (J+1 est publié à 11 h) ;
  - rte_daily : réalisé connu jusqu'à D-1 (scheduler._store_rte_daily).
Seule différence assumée : la météo de T est la météo OBSERVÉE (pas de
prévisions archivées dans le dépôt), donc toutes les métriques sont des
bornes optimistes vis-à-vis de l'erreur de prévision météo.

Validation : saison laissée de côté (LOSO). Les seuils sont choisis en
validation imbriquée (pour la saison testée, seuils choisis sur les
prédictions hors échantillon des autres saisons). Aucune métrique mesurée
sur des jours d'entraînement n'est enregistrée comme performance.

Usage :
  python ml_train.py                       # écrit ml_model_candidate.pkl
  python ml_train.py --out chemin.pkl
  python ml_train.py --no-nested           # plus rapide, seuils non imbriqués
  python ml_train.py --install             # remplace ml_model.pkl (ancien -> ml_model_2024-08.pkl)
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import ml_scorer  # noqa: E402

DUMP_PATH = ROOT / "db_dump.json"
BACKUP_PATH = ROOT / "ml_model_2024-08.pkl"
HORIZONS = (2, 3, 4, 5)
CLASS_WEIGHTS = {"ROUGE": 25, "BLANC": 3, "BLEU": 1}
GBM_PARAMS = dict(learning_rate=0.08, max_depth=4, min_samples_leaf=5,
                  n_estimators=300, random_state=42, subsample=0.8)
FEATURE_NAMES = [
    "temp_moy", "temp_min", "temp_max", "pressure", "humidity", "wind_speed",
    "gradient", "temp_3d", "temp_7d", "cold_streak", "month_sin", "month_cos",
    "dow_sin", "dow_cos", "is_weekend", "in_red_season", "week_in_season",
    "prev_rouge", "prev_blanc", "reds_7", "whites_7", "days_to_red_end",
    "temp_x_pressure", "rte_conso_peak_d1", "rte_conso_mean_d1",
    "rte_conso_peak_3d", "rte_conso_mean_3d", "rte_conso_peak_7d",
    "rte_nuclear_d1", "rte_gaz_d1", "rte_renewable_d1",
    "rte_nuclear_ratio_d1", "rte_has_data",
]
# Descriptions weather_cache correspondant à la météo OBSERVÉE (archive)
OBSERVED_WX = ("archive", "Open-Meteo Archive", "historique Open-Meteo")
WX_KEYS = ("temp_moy", "temp_min", "temp_max", "pressure", "humidity", "wind_speed")

# Seuils d'inférence retenus (politique "pipeline", défaut).
# Rejeu du pipeline complet (predict_day, 7 saisons, modèles hors saison) :
# le rappel ROUGE J+2..J+5 du pipeline est identique (91 %) pour tout seuil
# ML de 0,05 à 0,30 ; seules les fausses alertes varient (378 à 334). Le choix
# imbriqué sur le pipeline donne 0,30, en bord du plateau (0,40 perd déjà des
# ROUGE). On garde 0,19 (inchangé, marge vers le rappel), coût : 8 FP sur 7 saisons.
# Le BLANC n'a aucun effet mesurable dans le pipeline (0,10 / 0,20 / 0,30).
PIPELINE_THRESHOLDS = {"rouge_threshold": 0.19, "blanc_threshold": 0.20}

# Objectif ML seul (politique "ml-objective") : rappel ROUGE >= 90 %
TARGET_ROUGE_RECALL = 0.90
ROUGE_GRID = [round(x, 2) for x in np.arange(0.02, 0.61, 0.01)]
BLANC_GRID = [round(x, 2) for x in np.arange(0.05, 0.61, 0.01)]


# ---------------------------------------------------------------------------
# Données réelles
# ---------------------------------------------------------------------------

def season_of(d: date) -> str:
    y = d.year if d.month >= 9 else d.year - 1
    return f"{y}-{y + 1}"


def load_data(dump_path: Path = DUMP_PATH) -> dict:
    """Charge couleurs EDF (non synthétiques), météo observée et rte_daily."""
    tables = json.load(open(dump_path, encoding="utf-8"))["tables"]
    actuals = {r["date"]: r["couleur_reelle"] for r in tables["actuals"]
               if not r.get("synthetic")}
    last_actual = max(actuals)
    weather = {}
    for r in tables["weather_cache"]:
        if r["date"] <= last_actual and r.get("description") in OBSERVED_WX:
            # Clés None retirées : _build_features applique alors ses valeurs par défaut
            weather[r["date"]] = {"date": r["date"],
                                  **{k: r[k] for k in WX_KEYS if r.get(k) is not None}}
    rte = {r["date"]: r for r in tables["rte_daily"] if r.get("conso_peak_mw") is not None}
    return {"actuals": actuals, "weather": weather, "rte": rte,
            "first": min(actuals), "last": last_actual}


def make_rte_lag_fn(rte: dict, cutoff: date):
    """Même contrat que ml_scorer._get_rte_lag, limité au réalisé connu à `cutoff`."""
    def lag_fn(target_date: date, lag_days: int = 1, window: int = 1) -> list[dict]:
        out = []
        for k in range(lag_days, lag_days + window):
            d = target_date - timedelta(days=k)
            r = rte.get(d.isoformat())
            if r is not None and d <= cutoff:
                out.append({
                    "conso_peak": r["conso_peak_mw"],
                    "conso_mean": r["conso_mean_mw"] or 50000,
                    "nucleaire": r["nucleaire_mean_mw"] or 0,
                    "eolien": r["eolien_mean_mw"] or 0,
                    "solaire": r["solaire_mean_mw"] or 0,
                    "gaz": r["gaz_mean_mw"] or 0,
                    "hydraulique": r["hydraulique_mean_mw"] or 0,
                })
        return out
    return lag_fn


def features_at_horizon(data: dict, target: date, h: int) -> list[float] | None:
    """Variables telles que la production les calcule à 18 h le jour D = T - h."""
    d0 = target - timedelta(days=h)
    forecasts = []
    for i in range(h + 1):
        wx = data["weather"].get((d0 + timedelta(days=i)).isoformat())
        if wx is None:
            return None
        forecasts.append(wx)
    known_until = d0 + timedelta(days=1)
    cache = {}
    k = d0 - timedelta(days=7)
    while k <= known_until:
        c = data["actuals"].get(k.isoformat())
        if c:
            cache[k.isoformat()] = c
        k += timedelta(days=1)
    return ml_scorer._build_features(
        forecasts[h], target, forecasts, h, cache,
        rte_lag_fn=make_rte_lag_fn(data["rte"], d0 - timedelta(days=1)))


def build_dataset(data: dict, horizons=HORIZONS) -> list[dict]:
    rows = []
    d = date.fromisoformat(data["first"])
    last = date.fromisoformat(data["last"])
    while d <= last:
        y = data["actuals"].get(d.isoformat())
        if y:
            for h in horizons:
                x = features_at_horizon(data, d, h)
                if x is not None:
                    rows.append({"date": d, "season": season_of(d), "h": h, "x": x, "y": y})
        d += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Modèle, décision, métriques
# ---------------------------------------------------------------------------

def fit_model(rows: list[dict]):
    from sklearn.ensemble import GradientBoostingClassifier
    m = GradientBoostingClassifier(**GBM_PARAMS)
    m.fit(np.array([r["x"] for r in rows]), [r["y"] for r in rows],
          sample_weight=[CLASS_WEIGHTS[r["y"]] for r in rows])
    return m


def probas(model, rows: list[dict]) -> list[tuple[float, float, float]]:
    """(P ROUGE, P BLANC, P BLEU) pour chaque ligne."""
    if not rows:
        return []
    p = model.predict_proba(np.array([r["x"] for r in rows]))
    cl = list(model.classes_)
    iR, iB, iL = cl.index("ROUGE"), cl.index("BLANC"), cl.index("BLEU")
    return [(float(a[iR]), float(a[iB]), float(a[iL])) for a in p]


def decide(p: tuple[float, float, float], rouge_thr: float, blanc_thr: float) -> str:
    """Même règle que ml_scorer.compute_ml_score."""
    pr, pb, pl = p
    if pr >= rouge_thr:
        return "ROUGE"
    if pb >= blanc_thr and pb > pl:
        return "BLANC"
    return "BLEU"


@lru_cache(maxsize=None)
def _rouge_forbidden(d: date) -> bool:
    from predictor import is_french_holiday
    return not (d.month >= 11 or d.month <= 3) or d.weekday() >= 5 or is_french_holiday(d)


def edf_mask(d: date, pred: str) -> str:
    """Règles R1-R3 (appliquées ensuite par le predictor quel que soit le ML)."""
    dow = d.weekday()
    if pred == "ROUGE" and _rouge_forbidden(d):
        pred = "BLANC" if dow != 6 else "BLEU"
    if pred == "BLANC" and dow == 6:
        pred = "BLEU"
    return pred


def metrics(pairs: list[tuple[str, str]]) -> dict:
    """pairs = [(réel, prédit)] ; retourne VP/FP/FN, rappel, précision par couleur."""
    out = {"n": len(pairs),
           "accuracy": round(sum(a == p for a, p in pairs) / max(1, len(pairs)), 4)}
    for c in ("ROUGE", "BLANC"):
        tp = sum(1 for a, p in pairs if a == c and p == c)
        fp = sum(1 for a, p in pairs if a != c and p == c)
        fn = sum(1 for a, p in pairs if a == c and p != c)
        rec = tp / (tp + fn) if tp + fn else None
        prec = tp / (tp + fp) if tp + fp else None
        f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
        out[c] = {"tp": tp, "fp": fp, "fn": fn,
                  "recall": None if rec is None else round(rec, 4),
                  "precision": None if prec is None else round(prec, 4),
                  "f1": None if f1 is None else round(f1, 4)}
    return out


def evaluate(rows: list[dict], probs: list, rouge_thr: float, blanc_thr: float) -> dict:
    return metrics([(r["y"], edf_mask(r["date"], decide(p, rouge_thr, blanc_thr)))
                    for r, p in zip(rows, probs)])


# ---------------------------------------------------------------------------
# Choix des seuils (objectif explicite) et validation LOSO
# ---------------------------------------------------------------------------

def _fast_eval(rows, probs, rouge_thr, blanc_thr):
    return evaluate(rows, probs, rouge_thr, blanc_thr)


def choose_thresholds(rows: list[dict], probs: list) -> dict:
    """Objectif : rappel ROUGE >= TARGET_ROUGE_RECALL (J+2..J+5 poolés).

    ROUGE : parmi les seuils qui tiennent ce rappel, le plus élevé (donc la
    meilleure précision). Si aucun ne le tient : le seuil qui maximise F2
    (rappel pondéré 2x plus que la précision).
    BLANC : à seuil ROUGE fixé, le seuil qui maximise le F1 BLANC.
    """
    curve = []
    for rt in ROUGE_GRID:
        m = _fast_eval(rows, probs, rt, 0.20)["ROUGE"]
        curve.append((rt, m))
    ok = [(rt, m) for rt, m in curve if (m["recall"] or 0) >= TARGET_ROUGE_RECALL]
    if ok:
        rouge_thr, rule = max(ok, key=lambda x: x[0])[0], f"rappel>={TARGET_ROUGE_RECALL}"
    else:
        def f2(m):
            return 5 * m["tp"] / max(1, 5 * m["tp"] + 4 * m["fn"] + m["fp"])
        rouge_thr, rule = max(curve, key=lambda x: (f2(x[1]), -x[0]))[0], "F2 max"
    best_b, blanc_thr = -1.0, 0.20
    for bt in BLANC_GRID:
        f1 = _fast_eval(rows, probs, rouge_thr, bt)["BLANC"]["f1"] or 0
        if f1 > best_b:
            best_b, blanc_thr = f1, bt
    return {"rouge_threshold": float(rouge_thr), "blanc_threshold": float(blanc_thr), "rule": rule}


def _fit_and_predict(args):
    train_rows, test_rows = args
    m = fit_model(train_rows)
    return probas(m, test_rows), m


def loso(rows: list[dict], nested: bool = True, workers: int = 4) -> dict:
    """Validation saison laissée de côté.

    Retourne pour chaque saison : modèle hors saison, probabilités hors
    échantillon, seuils choisis SANS la saison testée (si nested).
    """
    from concurrent.futures import ProcessPoolExecutor
    seasons = sorted({r["season"] for r in rows})
    by_s = {s: [r for r in rows if r["season"] == s] for s in seasons}
    jobs = {("outer", s): ([r for r in rows if r["season"] != s], by_s[s]) for s in seasons}
    if nested:
        for s in seasons:
            for s2 in seasons:
                if s2 != s:
                    jobs[("inner", s, s2)] = (
                        [r for r in rows if r["season"] not in (s, s2)], by_s[s2])
    keys = list(jobs)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = dict(zip(keys, ex.map(_fit_and_predict, [jobs[k] for k in keys])))
    out = {}
    for s in seasons:
        probs_s, model_s = results[("outer", s)]
        if nested:
            sel_rows, sel_probs = [], []
            for s2 in seasons:
                if s2 != s:
                    sel_rows += by_s[s2]
                    sel_probs += results[("inner", s, s2)][0]
            thr = choose_thresholds(sel_rows, sel_probs)
        else:
            thr = None
        out[s] = {"rows": by_s[s], "probs": probs_s, "model": model_s, "thresholds": thr}
    return out


def summarize(folds: dict, thr_fn) -> dict:
    """Métriques hors échantillon : global, par horizon, par saison.
    thr_fn(season) -> (rouge_thr, blanc_thr)."""
    allp, by_h, by_s = [], defaultdict(list), {}
    for s, f in folds.items():
        rt, bt = thr_fn(s)
        pairs = [(r["y"], edf_mask(r["date"], decide(p, rt, bt)))
                 for r, p in zip(f["rows"], f["probs"])]
        allp += pairs
        by_s[s] = metrics(pairs)
        for r, pr in zip(f["rows"], pairs):
            by_h[r["h"]].append(pr)
    return {"all": metrics(allp), "by_horizon": {h: metrics(v) for h, v in sorted(by_h.items())},
            "by_season": by_s}


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def no_rte_lag_fn(target_date, lag_days=1, window=1):
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "ml_model_candidate.pkl"))
    ap.add_argument("--report", default=None, help="rapport JSON (hors dépôt)")
    ap.add_argument("--save-folds", default=None, help="modèles LOSO + seuils (rejeu pipeline)")
    ap.add_argument("--no-nested", action="store_true")
    ap.add_argument("--rte", choices=("dump", "none"), default="dump",
                    help="none : variables RTE neutralisées (valeurs de repli) à l'entraînement")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threshold-policy", choices=("pipeline", "ml-objective"), default="pipeline",
                    help="pipeline : seuils PIPELINE_THRESHOLDS ; ml-objective : rappel ML seul >= 90 %%")
    ap.add_argument("--install", action="store_true",
                    help="remplace ml_model.pkl (ancien sauvegardé en ml_model_2024-08.pkl)")
    a = ap.parse_args(argv)
    import sklearn

    data = load_data()
    if a.rte == "none":
        data["rte"] = {}
    rows = build_dataset(data)
    seasons = sorted({r["season"] for r in rows})
    print(f"{len(rows)} lignes ({len({r['date'] for r in rows})} jours réels), saisons {seasons}")

    folds = loso(rows, nested=not a.no_nested, workers=a.workers)
    oof_rows = [r for f in folds.values() for r in f["rows"]]
    oof_probs = [p for f in folds.values() for p in f["probs"]]
    mlobj_thr = choose_thresholds(oof_rows, oof_probs)
    if a.no_nested:
        mlobj_oos = summarize(folds, lambda s: (mlobj_thr["rouge_threshold"], mlobj_thr["blanc_threshold"]))
        mlobj_scope = "LOSO, seuils choisis sur l'ensemble des prédictions hors échantillon (non imbriqué)"
    else:
        mlobj_oos = summarize(folds, lambda s: (folds[s]["thresholds"]["rouge_threshold"],
                                                folds[s]["thresholds"]["blanc_threshold"]))
        mlobj_scope = "LOSO imbriqué : modèle ET seuils choisis sans la saison testée"
    if a.threshold_policy == "pipeline":
        final_thr = dict(PIPELINE_THRESHOLDS, rule="seuils fixés (rejeu pipeline), non choisis sur ces prédictions")
        oos = summarize(folds, lambda s: (final_thr["rouge_threshold"], final_thr["blanc_threshold"]))
        scope = "LOSO : modèle entraîné sans la saison testée, seuils fixés a priori"
    else:
        final_thr, oos, scope = mlobj_thr, mlobj_oos, mlobj_scope
    at_old_thr = summarize(folds, lambda s: (0.19, 0.20))

    # Modèle actuel, mêmes lignes, mêmes variables (en échantillon avant 2024-09)
    prev_path = BACKUP_PATH if BACKUP_PATH.exists() else ROOT / "ml_model.pkl"
    shipped = pickle.load(open(prev_path, "rb"))
    sm = shipped["metadata"]
    s_folds = {s: {"rows": f["rows"], "probs": probas(shipped["model"], f["rows"])}
               for s, f in folds.items()}
    shipped_eval = summarize(s_folds, lambda s: (sm.get("rouge_threshold", 0.19),
                                                 sm.get("blanc_threshold", 0.20)))

    model = fit_model(rows)
    m_all = oos["all"]
    days = sorted({r["date"] for r in rows})
    meta = {
        "model_name": "GBM_v4_horizon_J2J5",
        "n_features": len(FEATURE_NAMES), "feature_names": list(FEATURE_NAMES),
        "train_size": len(rows), "train_days": len(days),
        "test_size": m_all["n"],
        "date_range": f"{days[0].isoformat()} to {days[-1].isoformat()}",
        "train_date_range": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "seasons": f"{seasons[0]} à {seasons[-1]} ({len(seasons)} saisons, la dernière arrêtée au {days[-1].isoformat()})",
        "horizons": list(HORIZONS),
        "rouge_threshold": final_thr["rouge_threshold"],
        "blanc_threshold": final_thr["blanc_threshold"],
        "threshold_policy": a.threshold_policy,
        "threshold_rule": final_thr["rule"],
        "ml_objective": {
            "objective": f"rappel ROUGE ML seul >= {TARGET_ROUGE_RECALL} (J+2..J+5), plus haut seuil tenant l'objectif ; BLANC : F1 max",
            "thresholds": {k: mlobj_thr[k] for k in ("rouge_threshold", "blanc_threshold")},
            "scope": mlobj_scope, "oos_all": mlobj_oos["all"],
        },
        "rouge_weight": CLASS_WEIGHTS["ROUGE"], "class_weights": dict(CLASS_WEIGHTS),
        "gbm_params": dict(GBM_PARAMS),
        "metrics_scope": scope + " ; J+2 à J+5 poolés ; météo OBSERVÉE (borne optimiste) ; règles R1-R3 appliquées",
        "accuracy": m_all["accuracy"],
        "rouge_recall": m_all["ROUGE"]["recall"], "rouge_precision": m_all["ROUGE"]["precision"],
        "rouge_f1": m_all["ROUGE"]["f1"],
        "blanc_recall": m_all["BLANC"]["recall"], "blanc_precision": m_all["BLANC"]["precision"],
        "oos_by_horizon": oos["by_horizon"], "oos_by_season": oos["by_season"],
        "rte_features": a.rte == "dump", "rte_lag_only": True,
        "rte_mode": a.rte,
        "data_source": "db_dump.json (actuals non synthétiques, weather_cache archive, rte_daily)",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "sklearn_version": sklearn.__version__,
        "training_script": "ml_train.py",
    }
    bundle = {"model": model, "scaler": None, "metadata": meta}
    out_path = Path(a.out)
    if a.install:
        import shutil
        if not BACKUP_PATH.exists():
            shutil.copy2(ROOT / "ml_model.pkl", BACKUP_PATH)
            print(f"Ancien modèle sauvegardé : {BACKUP_PATH}")
        out_path = ROOT / "ml_model.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(bundle, f)
    print(f"Modèle écrit : {out_path}")

    report = {"oos": oos, "ml_objective": mlobj_oos, "at_thresholds_0.19_0.20": at_old_thr, "shipped": shipped_eval,
              "final_thresholds": final_thr,
              "fold_thresholds": {s: f["thresholds"] for s, f in folds.items()}}
    if a.report:
        json.dump(report, open(a.report, "w"), indent=1, default=str)
    if a.save_folds:
        pickle.dump({s: {"model": f["model"], "thresholds": f["thresholds"] or final_thr}
                     for s, f in folds.items()}, open(a.save_folds, "wb"))

    def line(name, m):
        r, b = m["ROUGE"], m["BLANC"]
        return (f"{name:28s} n={m['n']:5d} ROUGE {r['tp']}/{r['fp']}/{r['fn']} rappel={r['recall']} "
                f"préc={r['precision']} | BLANC rappel={b['recall']} préc={b['precision']}")
    print(line("NOUVEAU (hors échantillon)", oos["all"]))
    for h, m in oos["by_horizon"].items():
        print(line(f"  J+{h}", m))
    print(line("objectif ML seul (imbriqué)", mlobj_oos["all"]))
    print(line("nouveau @0.19/0.20", at_old_thr["all"]))
    print(line("ACTUEL (mélangé in/out)", shipped_eval["all"]))
    for s in seasons:
        print(f"  {s}: nouveau {oos['by_season'][s]['ROUGE']} | actuel {shipped_eval['by_season'][s]['ROUGE']}")
    print("seuils finaux", final_thr, "par saison", {s: f["thresholds"] for s, f in folds.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
