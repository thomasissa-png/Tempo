"""Micro-ML pour la zone de confusion [50-70] du scoring Tempo.

T5 v3.4 : classificateur binaire ROUGE vs non-ROUGE entraîné uniquement
sur les jours dont le score_risque tombe dans la zone d'ambiguïté [50-70].

Dans cette zone, les distributions ROUGE/BLANC/BLEU se chevauchent et le
scoring classique ne peut pas séparer les couleurs. Ce micro-ML utilise
des features supplémentaires (interactions temp×vent, mois, ratios budget)
pour arbitrer quand le score est ambigu.

Entraînement : depuis les données du backtest (backtest_predictor_results.json).
Inférence : appelé par predict_day() quand score_risque ∈ [50, 70].

Usage :
    # Entraînement
    python confusion_zone_ml.py train

    # Le modèle est sauvegardé dans confusion_zone_model.pkl
"""

import json
import pickle
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "confusion_zone_model.pkl"
_cached_model = None


def _build_features(temp_moy: float, wind_speed: float, pressure: float,
                    budget_score: float, c_nette_score: float,
                    month: int, weekday: int,
                    remaining_rouge: int, remaining_blanc: int) -> list[float]:
    """Construit le vecteur de features pour le micro-ML."""
    return [
        temp_moy,
        wind_speed,
        pressure,
        budget_score,
        c_nette_score,
        # Interactions
        temp_moy * wind_speed,          # froid + vent = signal fort
        temp_moy * budget_score / 100,  # froid + budget tendu
        # Temporel
        1 if month in (1, 2) else 0,    # pic saison
        1 if month in (11, 12) else 0,  # début saison
        1 if month == 3 else 0,         # fin saison
        1 if weekday in (1, 3) else 0,  # mardi/jeudi (jours ROUGE fréquents)
        # Ratios budget
        remaining_rouge,
        remaining_blanc,
        remaining_rouge / max(remaining_blanc, 1),  # pression relative R/B
    ]


def train_from_backtest(results_path: str = "backtest_predictor_results.json",
                        zone_low: float = 50, zone_high: float = 70) -> dict:
    """Entraîne le micro-ML depuis les résultats du backtest.

    Ne garde que les jours en saison ROUGE (nov-mar) avec score ∈ [zone_low, zone_high].
    Cible : is_rouge (binaire).
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    import numpy as np
    from datetime import date as dt_date

    with open(results_path) as f:
        data = json.load(f)

    preds = data["predictions"]

    # Filtrer la zone de confusion en saison ROUGE
    X, y = [], []
    for p in preds:
        d = dt_date.fromisoformat(p["date"])
        if d.month not in (11, 12, 1, 2, 3):
            continue
        score = p.get("score", 0)
        if score < zone_low or score > zone_high:
            continue

        features = _build_features(
            temp_moy=p["temp_moy"],
            wind_speed=p["wind_speed"],
            pressure=1015,  # pas stocké dans résultats, utiliser default
            budget_score=p.get("score_budget", 50),
            c_nette_score=p.get("score_rte", 50),
            month=d.month,
            weekday=d.weekday(),
            remaining_rouge=p.get("remaining_rouge", 11),
            remaining_blanc=p.get("remaining_blanc", 22),
        )
        X.append(features)
        y.append(1 if p["actual"] == "ROUGE" else 0)

    X = np.array(X)
    y = np.array(y)

    n_rouge = y.sum()
    n_total = len(y)
    logger.info(f"Zone [{zone_low}-{zone_high}]: {n_total} échantillons, "
                f"{n_rouge} ROUGE ({n_rouge/n_total*100:.1f}%)")

    if n_rouge < 5:
        logger.warning("Pas assez de ROUGE dans la zone — modèle non entraîné")
        return {"status": "insufficient_data"}

    # GradientBoosting avec coût asymétrique (ROUGE manqué >> faux ROUGE)
    # Profondeur limitée pour éviter l'overfitting sur peu de données
    weight_ratio = (n_total - n_rouge) / n_rouge  # ex: 200/30 ≈ 6.7
    sample_weights = np.where(y == 1, weight_ratio, 1.0)

    model = GradientBoostingClassifier(
        n_estimators=80,
        max_depth=3,
        min_samples_leaf=5,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
    )
    model.fit(X, y, sample_weight=sample_weights)

    # Cross-validation (sans sample_weight car cross_val_score ne le supporte pas proprement)
    cv_scores = cross_val_score(model, X, y, cv=min(5, n_total // 10),
                                scoring="f1")

    # Sauvegarder
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({
            "model": model,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "n_samples": n_total,
            "n_rouge": int(n_rouge),
            "cv_f1_mean": round(float(cv_scores.mean()), 3),
            "cv_f1_std": round(float(cv_scores.std()), 3),
            "feature_names": [
                "temp_moy", "wind_speed", "pressure", "budget_score",
                "c_nette_score", "temp×wind", "temp×budget",
                "is_peak_season", "is_early_season", "is_late_season",
                "is_tue_thu", "remaining_rouge", "remaining_blanc",
                "ratio_rouge_blanc",
            ],
        }, f)

    result = {
        "status": "ok",
        "n_samples": n_total,
        "n_rouge": int(n_rouge),
        "cv_f1_mean": round(float(cv_scores.mean()), 3),
        "cv_f1_std": round(float(cv_scores.std()), 3),
    }
    logger.info(f"Modèle confusion zone sauvegardé: {result}")
    return result


def predict_confusion_zone(temp_moy: float, wind_speed: float,
                           pressure: float, budget_score: float,
                           c_nette_score: float, month: int, weekday: int,
                           remaining_rouge: int, remaining_blanc: int,
                           threshold: float = 0.35) -> dict | None:
    """Prédit si un jour dans la zone de confusion est ROUGE.

    Args:
        threshold: seuil de probabilité pour prédire ROUGE.
                   Bas (0.35) pour maximiser le recall.

    Returns:
        {"is_rouge": bool, "proba_rouge": float} ou None si modèle absent.
    """
    global _cached_model

    if _cached_model is None:
        if not MODEL_PATH.exists():
            return None
        try:
            with open(MODEL_PATH, "rb") as f:
                _cached_model = pickle.load(f)
        except Exception:
            return None

    features = _build_features(
        temp_moy, wind_speed, pressure, budget_score, c_nette_score,
        month, weekday, remaining_rouge, remaining_blanc,
    )

    import numpy as np
    X = np.array([features])
    model = _cached_model["model"]
    proba = model.predict_proba(X)[0]

    # Index de la classe ROUGE (1)
    rouge_idx = list(model.classes_).index(1) if 1 in model.classes_ else -1
    if rouge_idx < 0:
        return None

    p_rouge = float(proba[rouge_idx])
    return {
        "is_rouge": p_rouge >= threshold,
        "proba_rouge": round(p_rouge, 3),
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        train_from_backtest()
    else:
        print("Usage: python confusion_zone_ml.py train")
