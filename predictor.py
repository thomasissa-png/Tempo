"""Algorithme de prédiction Tempo v1 — scoring par points avec poids ajustables.

Scoring (sur 100) :
  - Température < 0°C  : +30 pts  │  < -5°C : +50 pts
  - Jour de semaine     : +10 pts (lun-ven favorisés pour rouge)
  - Jours rouges restants < 5 après février : +20 pts
  - Anticyclone (pression > 1025 hPa) : +15 pts
  - Vague de froid (3+ jours consécutifs < 0°C) : +20 pts

Seuils : score > 70 → ROUGE, 40–70 → BLANC, < 40 → BLEU

Les poids sont récupérés de weights_history et ajustés mensuellement
par le système d'auto-amélioration (performance_tracker.py).
"""

import logging
from datetime import date, datetime, timedelta
from database import get_db, get_current_weights
from config import Config
from tempo_client import get_remaining_days, is_in_season, days_left_in_season

logger = logging.getLogger(__name__)


def predict_day(target_date: date, weather: dict | None = None,
                forecasts: list[dict] | None = None, target_idx: int = 0) -> dict:
    """Prédit la couleur Tempo pour une date donnée.

    Args:
        target_date: la date à prédire
        weather: données météo du jour (temp_min, temp_max, pressure, etc.)
        forecasts: liste complète des prévisions (pour détection vague de froid)
        target_idx: index du jour cible dans forecasts

    Returns:
        dict avec couleur_predite, probabilités, score_risque, raison
    """
    # Hors saison = toujours BLEU
    if not is_in_season(target_date):
        return _result(target_date, "BLEU", 0, 1.0, 0.0, 0.0,
                       weather, "Hors saison Tempo (juin-août)")

    remaining = get_remaining_days()
    d_left = days_left_in_season()

    # Quota épuisé ?
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return _result(target_date, "BLEU", 0, 1.0, 0.0, 0.0,
                       weather, "Quotas rouge et blanc épuisés")

    # Récupérer les poids courants
    weights = get_current_weights()
    w_temp = weights.get("temperature", 0.40)
    w_jours = weights.get("jours_restants", 0.25)
    w_dow = weights.get("jour_semaine", 0.15)
    w_pres = weights.get("pression_meteo", 0.20)

    # --- Score température ---
    temp_min = weather.get("temp_min", 5) if weather else 5
    temp_max = weather.get("temp_max", 10) if weather else 10
    temp_score = _score_temperature(temp_min)

    # --- Score jours restants (pression budgétaire) ---
    budget_score = _score_budget(remaining, d_left, target_date)

    # --- Score jour de la semaine ---
    dow_score = _score_weekday(target_date)

    # --- Score pression atmosphérique (anticyclone) ---
    pressure = weather.get("pressure", 1013) if weather else 1013
    pression_score = _score_pressure(pressure)

    # --- Bonus vague de froid ---
    cold_wave = _detect_cold_wave(forecasts or [], target_idx)

    # --- Score composite pondéré ---
    base_score = (
        temp_score * w_temp
        + budget_score * w_jours
        + dow_score * w_dow
        + pression_score * w_pres
    )
    # Le bonus vague de froid s'ajoute en extra (pas dans les poids ajustables)
    score_risque = min(100, base_score + cold_wave)

    # --- Déterminer la couleur prédite ---
    if score_risque >= Config.SEUIL_ROUGE and remaining["ROUGE"] > 0:
        couleur = "ROUGE"
    elif score_risque >= Config.SEUIL_BLANC and remaining["BLANC"] > 0:
        couleur = "BLANC"
    else:
        couleur = "BLEU"

    # --- Calculer les probabilités ---
    prob_rouge, prob_blanc, prob_bleu = _compute_probabilities(
        score_risque, remaining)

    # --- Raison humaine ---
    raison = _build_raison(temp_min, pressure, cold_wave, remaining,
                           target_date, d_left)

    return _result(target_date, couleur, score_risque,
                   prob_bleu, prob_blanc, prob_rouge, weather, raison,
                   remaining)


def predict_range(forecasts: list[dict]) -> list[dict]:
    """Prédit la couleur pour chaque jour du forecast."""
    predictions = []
    for i, weather in enumerate(forecasts):
        target = date.fromisoformat(weather["date"])
        pred = predict_day(target, weather=weather,
                           forecasts=forecasts, target_idx=i)
        # Calculer l'horizon (J-N)
        delta = (target - date.today()).days
        pred["horizon"] = f"J-{delta}" if delta > 0 else "J0"
        predictions.append(pred)
    return predictions


# === Fonctions de scoring ===

def _score_temperature(temp_min: float) -> float:
    """Score 0–100 basé sur la température minimale."""
    if temp_min < -10:
        return 100
    if temp_min < -5:
        return 85  # +50 pts dans le système original → mappé sur 100
    if temp_min < -2:
        return 70
    if temp_min < 0:
        return 60  # +30 pts
    if temp_min < 2:
        return 45
    if temp_min < 5:
        return 30
    if temp_min < 8:
        return 15
    return 5


def _score_budget(remaining: dict, d_left: int, target_date: date) -> float:
    """Score 0–100 basé sur la pression du quota de jours restants."""
    if d_left <= 0:
        return 0

    # Taux d'utilisation attendu vs réel
    rouge_rate = remaining["ROUGE"] / d_left if d_left > 0 else 0
    normal_rouge_rate = 22 / 273  # ~0.08

    score = 0
    # Si beaucoup de jours rouges restent par rapport au temps restant
    if rouge_rate > normal_rouge_rate * 2:
        score += 40
    elif rouge_rate > normal_rouge_rate * 1.5:
        score += 25

    # Bonus fin de saison : < 5 jours rouges restants après février
    if target_date.month >= 2 and remaining["ROUGE"] <= 5 and remaining["ROUGE"] > 0:
        score += 30  # +20 pts mappé

    # Très peu de jours restants dans la saison avec du rouge à écouler
    if d_left < 30 and remaining["ROUGE"] > 3:
        score += 20

    return min(100, score)


def _score_weekday(target_date: date) -> float:
    """Score 0–100 basé sur le jour de la semaine.
    EDF évite les jours rouges le week-end → lun-ven favorisés."""
    dow = target_date.weekday()  # 0=lundi, 6=dimanche
    if dow <= 4:  # Lundi-vendredi
        # Mardi-jeudi légèrement plus probables historiquement
        return {0: 55, 1: 70, 2: 75, 3: 70, 4: 50}[dow]
    else:
        return 10  # Week-end : très peu de jours rouges


def _score_pressure(pressure: float) -> float:
    """Score 0–100 basé sur la pression atmosphérique.
    Anticyclone hivernal (haute pression + froid) = risque accru."""
    if pressure >= 1035:
        return 80  # Anticyclone puissant
    if pressure >= 1025:
        return 60  # +15 pts dans le système original
    if pressure >= 1015:
        return 30
    return 10  # Dépression = doux, moins de risque


def _detect_cold_wave(forecasts: list[dict], target_idx: int) -> float:
    """Détecte une vague de froid (3+ jours consécutifs < 0°C).
    Retourne un bonus de 0 à 25 points."""
    if not forecasts or target_idx >= len(forecasts):
        return 0

    # Fenêtre de 5 jours autour de la date cible
    start = max(0, target_idx - 2)
    end = min(len(forecasts), target_idx + 3)

    cold_days = sum(
        1 for i in range(start, end)
        if forecasts[i].get("temp_min", 10) < 0
    )

    if cold_days >= 5:
        return 25  # Vague de froid intense
    if cold_days >= 4:
        return 20
    if cold_days >= 3:
        return 15  # +20 pts dans le système original
    if cold_days >= 2:
        return 8
    return 0


def _compute_probabilities(score: float, remaining: dict) -> tuple[float, float, float]:
    """Convertit le score de risque en probabilités par couleur."""
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return 0.0, 0.0, 1.0

    # Approche sigmoïde simplifiée
    if score >= 80:
        p_rouge = 0.85 + (score - 80) * 0.005
    elif score >= Config.SEUIL_ROUGE:
        p_rouge = 0.50 + (score - Config.SEUIL_ROUGE) * 0.035
    elif score >= 50:
        p_rouge = 0.20 + (score - 50) * 0.015
    else:
        p_rouge = max(0.02, score * 0.004)

    if remaining["ROUGE"] == 0:
        p_rouge = 0.0

    if score >= 50:
        p_blanc = min(0.40, 0.15 + (score - 50) * 0.008)
    elif score >= Config.SEUIL_BLANC:
        p_blanc = 0.30 + (score - Config.SEUIL_BLANC) * 0.01
    else:
        p_blanc = max(0.05, 0.10 + score * 0.005)

    if remaining["BLANC"] == 0:
        p_blanc = 0.0

    p_bleu = max(0.0, 1.0 - p_rouge - p_blanc)

    # Normaliser
    total = p_rouge + p_blanc + p_bleu
    return (
        round(p_rouge / total, 3),
        round(p_blanc / total, 3),
        round(p_bleu / total, 3),
    )


def _build_raison(temp_min: float, pressure: float, cold_wave: float,
                  remaining: dict, target_date: date, d_left: int) -> str:
    """Construit une explication humaine de la prédiction."""
    raisons = []
    if temp_min < -5:
        raisons.append(f"Grand froid ({temp_min:.0f}°C)")
    elif temp_min < 0:
        raisons.append(f"Gel prévu ({temp_min:.0f}°C)")
    elif temp_min < 3:
        raisons.append(f"Froid ({temp_min:.0f}°C)")

    if cold_wave >= 15:
        raisons.append("Vague de froid détectée")
    if pressure >= 1025:
        raisons.append(f"Anticyclone ({pressure:.0f} hPa)")

    if target_date.weekday() >= 5:
        raisons.append("Week-end (rouge improbable)")
    if remaining["ROUGE"] <= 5 and remaining["ROUGE"] > 0 and target_date.month >= 2:
        raisons.append(f"Fin saison, {remaining['ROUGE']}j rouges restants")
    if d_left < 30 and remaining["ROUGE"] > 3:
        raisons.append("Pression forte sur quota rouge")

    return " · ".join(raisons) if raisons else "Conditions normales"


def _result(target_date: date, couleur: str, score: float,
            p_bleu: float, p_blanc: float, p_rouge: float,
            weather: dict | None = None, raison: str = "",
            remaining: dict | None = None) -> dict:
    """Formate le résultat de prédiction."""
    return {
        "date": target_date.isoformat(),
        "couleur_predite": couleur,
        "probabilite_bleu": p_bleu,
        "probabilite_blanc": p_blanc,
        "probabilite_rouge": p_rouge,
        "score_risque": round(score, 1),
        "temp_min_prevue": weather.get("temp_min") if weather else None,
        "temp_max_prevue": weather.get("temp_max") if weather else None,
        "pression_prevue": weather.get("pressure") if weather else None,
        "jours_rouges_restants": remaining["ROUGE"] if remaining else None,
        "jours_blancs_restants": remaining["BLANC"] if remaining else None,
        "raison": raison,
    }


# === Stockage ===

def store_prediction(pred: dict, horizon: str = "J-1"):
    """Enregistre une prédiction en base."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO predictions
               (date, couleur_predite, probabilite_bleu, probabilite_blanc,
                probabilite_rouge, score_risque, temp_min_prevue, temp_max_prevue,
                pression_prevue, jours_rouges_restants, jours_blancs_restants,
                raison, horizon, timestamp_prediction)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pred["date"], pred["couleur_predite"],
             pred["probabilite_bleu"], pred["probabilite_blanc"],
             pred["probabilite_rouge"], pred["score_risque"],
             pred.get("temp_min_prevue"), pred.get("temp_max_prevue"),
             pred.get("pression_prevue"),
             pred.get("jours_rouges_restants"), pred.get("jours_blancs_restants"),
             pred.get("raison", ""), horizon,
             datetime.now().isoformat()),
        )
        conn.commit()
        logger.info(f"[Prédiction] {pred['date']} → {pred['couleur_predite']} "
                     f"(score={pred['score_risque']}, {horizon})")
    finally:
        conn.close()
