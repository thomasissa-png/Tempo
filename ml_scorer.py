"""Score ML (GradientBoosting) pour prediction Tempo.

Module autonome qui charge le modele entraine (ml_model.pkl) et fournit
un score 0-100 base sur les probabilites du classifieur.

Le modele a ete entraine sur 1827 jours (saisons 2019-2026) et teste
sur 530 jours (saison 2024-2026) :
  - Accuracy : 83.4%
  - ROUGE recall : 23.3%
  - ROUGE precision : 43.8%

Features utilisees (33) :
  - Temperatures : moy, min, max, moyennes 3j/7j, gradient, cold streak
  - Meteo : pression, humidite, vent
  - Temporel : mois (cyclique), jour semaine (cyclique), weekend, saison rouge
  - Historique : couleur veille, rouges/blancs 7 derniers jours
  - Budget : jours restants avant fin fenetre rouge
  - RTE lag (J-1) : conso peak/mean, nucleaire, gaz, renouvelables, ratios
  - RTE rolling : conso peak/mean 3j et 7j

Integration : le score ML est utilise en ensemble avec le scoring
classique dans le predictor. Il sert de filet de securite ROUGE et
de filtre pour les faux positifs BLANC.
"""

import logging
import math
import pickle
from datetime import date, timedelta
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_MODEL = None
_SCALER = None
_METADATA = None
_LOAD_ERROR = False


def _load_model():
    """Charge le modele une seule fois (lazy loading)."""
    global _MODEL, _SCALER, _METADATA, _LOAD_ERROR
    if _MODEL is not None or _LOAD_ERROR:
        return
    model_path = Path(__file__).parent / "ml_model.pkl"
    if not model_path.exists():
        logger.warning("[ML] ml_model.pkl introuvable — ML score desactive")
        _LOAD_ERROR = True
        return
    try:
        with open(model_path, "rb") as f:
            bundle = pickle.load(f)
        _MODEL = bundle["model"]
        _SCALER = bundle["scaler"]
        _METADATA = bundle["metadata"]
        logger.info(f"[ML] Modele {_METADATA['model_name']} charge "
                    f"(train={_METADATA['train_size']}, "
                    f"rouge_f1={_METADATA['rouge_f1']*100:.1f}%)")
    except Exception as e:
        logger.error(f"[ML] Erreur chargement modele: {e}")
        _LOAD_ERROR = True


def ml_score_available() -> bool:
    """Verifie si le modele ML est disponible."""
    _load_model()
    return _MODEL is not None


def compute_ml_score(
    weather: dict,
    target_date: date,
    forecasts: list[dict] | None = None,
    target_idx: int = 0,
    actuals_cache: dict[str, str] | None = None,
) -> dict:
    """Calcule le score ML pour une date donnee.

    Retourne un dict avec :
      - available: bool
      - score_rouge: float 0-100 (P(ROUGE) * 100)
      - score_blanc: float 0-100 (P(BLANC) * 100)
      - score_bleu: float 0-100 (P(BLEU) * 100)
      - prediction: str (BLEU/BLANC/ROUGE — argmax)
    """
    _load_model()
    if _MODEL is None:
        return {"available": False, "score_rouge": 50, "score_blanc": 0,
                "score_bleu": 50, "prediction": "BLEU"}

    try:
        features = _build_features(weather, target_date, forecasts,
                                    target_idx, actuals_cache)
        X = np.array([features])
        proba = _MODEL.predict_proba(X)[0]
        classes = list(_MODEL.classes_)

        p_rouge = float(proba[classes.index("ROUGE")])
        p_blanc = float(proba[classes.index("BLANC")])
        p_bleu = float(proba[classes.index("BLEU")])

        # Threshold-based prediction (from model metadata)
        rouge_thresh = (_METADATA or {}).get("rouge_threshold", 0.10)
        blanc_thresh = (_METADATA or {}).get("blanc_threshold", 0.20)

        if p_rouge >= rouge_thresh:
            pred = "ROUGE"
        elif p_blanc >= blanc_thresh and p_blanc > p_bleu:
            pred = "BLANC"
        else:
            pred = "BLEU"

        return {
            "available": True,
            "score_rouge": round(p_rouge * 100, 1),
            "score_blanc": round(p_blanc * 100, 1),
            "score_bleu": round(p_bleu * 100, 1),
            "prediction": pred,
        }
    except Exception as e:
        logger.warning(f"[ML] Erreur prediction: {e}")
        return {"available": False, "score_rouge": 50, "score_blanc": 0,
                "score_bleu": 50, "prediction": "BLEU"}


def _get_rte_lag(target_date: date, lag_days: int = 1, window: int = 1) -> list[dict]:
    """Recupere les donnees RTE des jours precedents depuis rte_daily."""
    try:
        from database import get_db
        conn = get_db()
        try:
            results = []
            for k in range(lag_days, lag_days + window):
                d = (target_date - timedelta(days=k)).isoformat()
                row = conn.execute(
                    """SELECT conso_peak_mw, conso_mean_mw, nucleaire_mean_mw,
                              eolien_mean_mw, solaire_mean_mw, gaz_mean_mw,
                              hydraulique_mean_mw
                       FROM rte_daily WHERE date = ?""", (d,)
                ).fetchone()
                if row and row["conso_peak_mw"] is not None:
                    results.append({
                        "conso_peak": row["conso_peak_mw"],
                        "conso_mean": row["conso_mean_mw"] or 50000,
                        "nucleaire": row["nucleaire_mean_mw"] or 0,
                        "eolien": row["eolien_mean_mw"] or 0,
                        "solaire": row["solaire_mean_mw"] or 0,
                        "gaz": row["gaz_mean_mw"] or 0,
                        "hydraulique": row["hydraulique_mean_mw"] or 0,
                    })
            return results
        finally:
            conn.close()
    except Exception:
        return []


def _build_features(
    weather: dict,
    target_date: date,
    forecasts: list[dict] | None,
    target_idx: int,
    actuals_cache: dict[str, str] | None,
) -> list[float]:
    """Construit le vecteur de 33 features pour le modele."""
    month = target_date.month
    dow = target_date.weekday()

    temp_moy = weather.get("temp_moy", 10)
    temp_min = weather.get("temp_min", 5)
    temp_max = weather.get("temp_max", 15)
    pressure = weather.get("pressure") or 1013
    humidity = weather.get("humidity", 70)
    wind = weather.get("wind_speed", 10)

    # Gradient (temperature drop from previous day)
    gradient = 0.0
    if forecasts and target_idx > 0 and target_idx < len(forecasts):
        prev_temp = forecasts[target_idx - 1].get("temp_moy", 10)
        gradient = prev_temp - temp_moy

    # 3-day and 7-day temperature averages
    temp_3d = temp_moy
    temp_7d = temp_moy
    if forecasts and target_idx > 0:
        temps_3 = [forecasts[j].get("temp_moy", 10)
                   for j in range(max(0, target_idx - 2), target_idx + 1)
                   if j < len(forecasts)]
        if temps_3:
            temp_3d = sum(temps_3) / len(temps_3)

        temps_7 = [forecasts[j].get("temp_moy", 10)
                   for j in range(max(0, target_idx - 6), target_idx + 1)
                   if j < len(forecasts)]
        if temps_7:
            temp_7d = sum(temps_7) / len(temps_7)

    # Cold streak (days < 5C looking backwards in forecasts)
    cold_streak = 0
    if forecasts:
        j = target_idx
        while j >= 0 and j < len(forecasts):
            if forecasts[j].get("temp_moy", 10) < 5:
                cold_streak += 1
                j -= 1
            else:
                break

    # Previous day color from actuals
    yesterday = (target_date - timedelta(days=1)).isoformat()
    prev_color = (actuals_cache or {}).get(yesterday, "BLEU")
    prev_rouge = 1 if prev_color == "ROUGE" else 0
    prev_blanc = 1 if prev_color == "BLANC" else 0

    # Red/white days in last 7 days
    reds_7 = 0
    whites_7 = 0
    if actuals_cache:
        for k in range(1, 8):
            d_str = (target_date - timedelta(days=k)).isoformat()
            c = actuals_cache.get(d_str, "")
            if c == "ROUGE":
                reds_7 += 1
            elif c == "BLANC":
                whites_7 += 1

    # Season context
    in_red_season = int(month >= 11 or month <= 3)
    is_weekend = int(dow >= 5)

    # Cyclical month/dow
    month_sin = math.sin(2 * math.pi * month / 12)
    month_cos = math.cos(2 * math.pi * month / 12)
    dow_sin = math.sin(2 * math.pi * dow / 7)
    dow_cos = math.cos(2 * math.pi * dow / 7)

    # Week in season
    season_start = (date(target_date.year, 9, 1) if month >= 9
                    else date(target_date.year - 1, 9, 1))
    week_in_season = (target_date - season_start).days // 7

    # Days to end of red window
    if month >= 9:
        red_end = date(target_date.year + 1, 3, 31)
    elif month <= 3:
        red_end = date(target_date.year, 3, 31)
    else:
        red_end = target_date
    days_to_red_end = max(0, (red_end - target_date).days)

    # Temperature * pressure interaction
    temp_x_pressure = temp_moy * (pressure - 1013) / 10

    # RTE lag features (D-1 and rolling averages)
    rte_d1 = _get_rte_lag(target_date, 1, 1)
    rte_3d = _get_rte_lag(target_date, 1, 3)
    rte_7d = _get_rte_lag(target_date, 1, 7)

    has_rte = 1.0 if rte_d1 else 0.0

    if rte_d1:
        r = rte_d1[0]
        cp_d1 = r["conso_peak"] / 10000
        cm_d1 = r["conso_mean"] / 10000
        nuc_d1 = r["nucleaire"] / 10000
        gaz_d1 = r["gaz"] / 10000
        renew_d1 = (r["eolien"] + r["solaire"] + r["hydraulique"]) / 10000
        nuc_ratio = r["nucleaire"] / max(r["conso_mean"], 1)
    else:
        cp_d1 = cm_d1 = nuc_d1 = gaz_d1 = renew_d1 = nuc_ratio = 0.0

    cp_3d = (sum(r["conso_peak"] for r in rte_3d) / len(rte_3d) / 10000) if rte_3d else 0
    cm_3d = (sum(r["conso_mean"] for r in rte_3d) / len(rte_3d) / 10000) if rte_3d else 0
    cp_7d = (sum(r["conso_peak"] for r in rte_7d) / len(rte_7d) / 10000) if rte_7d else 0

    return [
        temp_moy, temp_min, temp_max,
        pressure, humidity, wind,
        gradient, temp_3d, temp_7d,
        cold_streak,
        month_sin, month_cos,
        dow_sin, dow_cos,
        is_weekend, in_red_season,
        week_in_season,
        prev_rouge, prev_blanc,
        reds_7, whites_7,
        days_to_red_end,
        temp_x_pressure,
        # RTE lag features (10 features)
        cp_d1, cm_d1,
        cp_3d, cm_3d, cp_7d,
        nuc_d1, gaz_d1, renew_d1,
        nuc_ratio, has_rte,
    ]
