"""Algorithme de prediction Tempo v2 — 7 corrections audit expert.

Facteurs de scoring (sur 100, poids ajustables) :
  1. Temperature nationale ponderee (8 villes) — recalibree zone 0-5C
  2. Pression budgetaire avec profil mensuel historique
  3. Jour de la semaine + jours feries
  4. Gradient thermique (chute de temperature J/J-1)
  5. Clustering : continuite des jours rouges consecutifs
  6. Consommation RTE eco2mix (prevision pointe + nucleaire)

Bonus hors-poids :
  - Vague de froid (3+ jours consecutifs < 0C national) : +15-25 pts
"""

import logging
from datetime import date, datetime, timedelta
from database import get_db, get_current_weights
from config import Config
from tempo_client import get_remaining_days, is_in_season, days_left_in_season

logger = logging.getLogger(__name__)


# ================================================================
# JOURS FERIES FRANCAIS
# ================================================================

def _get_french_holidays(year: int) -> set[date]:
    """Retourne l'ensemble des jours feries francais pour une annee."""
    holidays = {
        date(year, 1, 1),    # Jour de l'an
        date(year, 5, 1),    # Fete du travail
        date(year, 5, 8),    # Victoire 1945
        date(year, 7, 14),   # Fete nationale
        date(year, 8, 15),   # Assomption
        date(year, 11, 1),   # Toussaint
        date(year, 11, 11),  # Armistice
        date(year, 12, 25),  # Noel
    }
    # Paques (algorithme de Gauss/Meeus)
    easter = _easter(year)
    holidays.add(easter)                         # Dimanche de Paques
    holidays.add(easter + timedelta(days=1))     # Lundi de Paques
    holidays.add(easter + timedelta(days=39))    # Ascension
    holidays.add(easter + timedelta(days=50))    # Lundi de Pentecote
    return holidays


def _easter(year: int) -> date:
    """Calcule la date de Paques (algorithme de Meeus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def is_french_holiday(d: date) -> bool:
    """Verifie si une date est un jour ferie francais."""
    return d in _get_french_holidays(d.year)


# ================================================================
# PREDICTION PRINCIPALE
# ================================================================

def predict_day(target_date: date, weather: dict | None = None,
                forecasts: list[dict] | None = None, target_idx: int = 0,
                remaining: dict | None = None,
                weights: dict | None = None,
                rte_score: dict | None = None) -> dict:
    """Predit la couleur Tempo pour une date donnee (algorithme v2)."""
    # Hors saison = toujours BLEU
    if not is_in_season(target_date):
        return _result(target_date, "BLEU", 0, 1.0, 0.0, 0.0,
                       weather, "Hors saison Tempo (juin-aout)")

    if remaining is None:
        remaining = get_remaining_days()
    d_left = days_left_in_season()

    # Quota epuise ?
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return _result(target_date, "BLEU", 0, 1.0, 0.0, 0.0,
                       weather, "Quotas rouge et blanc epuises")

    # Poids courants
    if weights is None:
        weights = get_current_weights()
    w_temp = weights.get("temperature", 0.30)
    w_budget = weights.get("jours_restants", 0.20)
    w_dow = weights.get("jour_semaine", 0.10)
    w_gradient = weights.get("gradient_thermique", 0.15)
    w_cluster = weights.get("clustering", 0.10)
    w_rte = weights.get("consommation_rte", 0.15)

    # --- Donnees meteo ---
    temp_min = weather.get("temp_min", 5) if weather else 5
    temp_max = weather.get("temp_max", 10) if weather else 10
    temp_moy = weather.get("temp_moy", 7.5) if weather else 7.5

    # === 1. Score temperature (recalibre zone 0-5C) ===
    temp_score = _score_temperature_v2(temp_moy)

    # === 2. Score budget (profil mensuel) ===
    budget_score = _score_budget_v2(remaining, d_left, target_date)

    # === 3. Score jour semaine + feries ===
    dow_score = _score_weekday_v2(target_date)

    # === 4. Score gradient thermique (chute J/J-1) ===
    gradient_score = _score_gradient(forecasts or [], target_idx)

    # === 5. Score clustering (jours rouges consecutifs) ===
    cluster_score = _score_clustering(target_date, forecasts or [], target_idx)

    # === 6. Score consommation RTE ===
    rte_s = 50  # neutre par defaut si pas de donnees RTE
    if rte_score and rte_score.get("available"):
        rte_s = rte_score["score"]

    # === Bonus vague de froid (hors poids) ===
    cold_wave = _detect_cold_wave(forecasts or [], target_idx)

    # === Score composite pondere ===
    base_score = (
        temp_score * w_temp
        + budget_score * w_budget
        + dow_score * w_dow
        + gradient_score * w_gradient
        + cluster_score * w_cluster
        + rte_s * w_rte
    )
    score_risque = min(100, base_score + cold_wave)

    # === Determiner la couleur predite ===
    if score_risque >= Config.SEUIL_ROUGE and remaining["ROUGE"] > 0:
        couleur = "ROUGE"
    elif score_risque >= Config.SEUIL_BLANC and remaining["BLANC"] > 0:
        couleur = "BLANC"
    else:
        couleur = "BLEU"

    # === Probabilites ===
    prob_rouge, prob_blanc, prob_bleu = _compute_probabilities(
        score_risque, remaining)

    # === Raison humaine ===
    raison = _build_raison_v2(
        temp_moy, temp_min, gradient_score, cold_wave, remaining,
        target_date, d_left, rte_score, cluster_score)

    return _result(target_date, couleur, score_risque,
                   prob_bleu, prob_blanc, prob_rouge, weather, raison,
                   remaining)


def predict_range(forecasts: list[dict],
                  rte_score: dict | None = None) -> list[dict]:
    """Predit la couleur pour chaque jour du forecast."""
    remaining = get_remaining_days()
    weights = get_current_weights()
    predictions = []
    for i, weather in enumerate(forecasts):
        target = date.fromisoformat(weather["date"])
        pred = predict_day(target, weather=weather,
                           forecasts=forecasts, target_idx=i,
                           remaining=remaining, weights=weights,
                           rte_score=rte_score)
        delta = (target - date.today()).days
        pred["horizon"] = f"J-{delta}" if delta > 0 else "J0"
        predictions.append(pred)
    return predictions


# ================================================================
# FONCTIONS DE SCORING v2
# ================================================================

def _score_temperature_v2(temp_moy_nationale: float) -> float:
    """Score 0-100 base sur la temperature moyenne nationale ponderee.

    Recalibre sur 20 ans d'historique :
    - La zone critique est 0-5C (la ou se prennent les decisions)
    - Les grands froids < -5C sont rares mais quasi certains rouge
    """
    if temp_moy_nationale < -5:
        return 98  # Grand froid national = quasi certain rouge
    if temp_moy_nationale < -2:
        return 90
    if temp_moy_nationale < 0:
        return 80
    if temp_moy_nationale < 2:
        return 68  # Zone haute de decision
    if temp_moy_nationale < 4:
        return 55  # Zone moyenne de decision (la plus frequente pour rouge)
    if temp_moy_nationale < 6:
        return 40  # Zone basse, possible blanc
    if temp_moy_nationale < 8:
        return 25
    if temp_moy_nationale < 10:
        return 15
    if temp_moy_nationale < 14:
        return 8
    return 3  # Doux, quasi impossible rouge


def _score_budget_v2(remaining: dict, d_left: int, target_date: date) -> float:
    """Score 0-100 base sur la pression budgetaire avec profil mensuel.

    Compare le rythme reel d'utilisation des jours rouges au profil
    historique de distribution sur 20 saisons.
    """
    if d_left <= 0:
        return 0

    month = target_date.month
    expected_pct = Config.MONTHLY_RED_PROFILE.get(month, 0.0)

    # Combien de jours rouges "devrait-il" rester a ce stade de la saison ?
    # On cumule les % des mois restants
    months_ahead = []
    m = month
    while m != 6:  # jusqu'a fin mai (mois 5)
        months_ahead.append(m)
        m = m + 1 if m < 12 else 1
        if m == 6:
            break
    expected_remaining_pct = sum(
        Config.MONTHLY_RED_PROFILE.get(mo, 0.0) for mo in months_ahead
    )
    expected_remaining = expected_remaining_pct * 22

    actual_remaining = remaining["ROUGE"]

    score = 0

    # Si plus de rouges restent que prevu, pression accrue
    if expected_remaining > 0:
        ratio = actual_remaining / expected_remaining
        if ratio > 2.0:
            score += 50  # Forte pression : beaucoup de retard
        elif ratio > 1.5:
            score += 35
        elif ratio > 1.2:
            score += 20
        elif ratio > 1.0:
            score += 10

    # Boost le mois ou les rouges sont historiquement concentres
    if expected_pct >= 0.25:  # Janvier
        score += 25
    elif expected_pct >= 0.15:  # Decembre, Fevrier
        score += 15
    elif expected_pct >= 0.05:  # Novembre, Mars
        score += 5

    # Urgence fin de saison
    if d_left < 30 and actual_remaining > 3:
        score += 25
    elif d_left < 60 and actual_remaining > 8:
        score += 15

    return min(100, score)


def _score_weekday_v2(target_date: date) -> float:
    """Score 0-100 base sur le jour de la semaine + jours feries.

    Correction #3 : les jours feries sont traites comme des dimanches.
    Sur 20 ans, 0 jour rouge un jour ferie.
    """
    if is_french_holiday(target_date):
        return 5  # Quasi impossible rouge un jour ferie

    dow = target_date.weekday()
    if dow >= 5:  # Samedi, dimanche
        return 8
    # Mardi-jeudi legerement plus probables historiquement
    return {0: 50, 1: 65, 2: 70, 3: 65, 4: 45}[dow]


def _score_gradient(forecasts: list[dict], target_idx: int) -> float:
    """Score 0-100 base sur le gradient thermique (chute de temperature).

    Remplace la pression atmospherique (correction #4).
    Une chute brutale de temperature entre J-1 et J est un signal fort.
    """
    if not forecasts or target_idx <= 0 or target_idx >= len(forecasts):
        return 30  # Neutre

    prev = forecasts[target_idx - 1]
    curr = forecasts[target_idx]

    prev_moy = prev.get("temp_moy", 10)
    curr_moy = curr.get("temp_moy", 10)
    drop = prev_moy - curr_moy  # Positif = il fait plus froid

    if drop >= 8:
        return 90  # Chute brutale (>8C en 1 jour)
    if drop >= 5:
        return 70  # Forte chute
    if drop >= 3:
        return 50
    if drop >= 1:
        return 35
    if drop >= 0:
        return 25  # Stable
    # Il se rechauffe
    if drop >= -3:
        return 15
    return 5  # Fort rechauffement = risque rouge tres faible


def _score_clustering(target_date: date, forecasts: list[dict],
                      target_idx: int) -> float:
    """Score 0-100 base sur la continuite des jours rouges.

    Correction #5 : si la veille est rouge/prevue rouge et que le froid
    continue, forte probabilite de jour rouge consecutif.
    """
    # Verifier si hier etait rouge dans les actuals
    yesterday = target_date - timedelta(days=1)
    yesterday_was_red = _check_yesterday_color(yesterday)

    if yesterday_was_red:
        # La veille etait rouge. Le froid continue-t-il ?
        if target_idx > 0 and forecasts:
            curr_temp = forecasts[target_idx].get("temp_moy", 10)
            if curr_temp < 2:
                return 90  # Rouge hier + froid qui continue = tres probable
            if curr_temp < 5:
                return 70
            return 40  # Rouge hier mais radoucissement

    # Verifier si J-1 est predit rouge dans les previsions courantes
    if target_idx > 0 and target_idx < len(forecasts):
        prev_temp = forecasts[target_idx - 1].get("temp_moy", 10)
        curr_temp = forecasts[target_idx].get("temp_moy", 10)
        if prev_temp < 2 and curr_temp < 2:
            return 60  # Froid persistant J-1 et J
        if prev_temp < 4 and curr_temp < 4:
            return 40

    return 20  # Pas de continuite detectee


def _check_yesterday_color(yesterday: date) -> bool:
    """Verifie dans les actuals si hier etait rouge."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT couleur_reelle FROM actuals WHERE date = ?",
            (yesterday.isoformat(),)
        ).fetchone()
        return row is not None and row["couleur_reelle"] == "ROUGE"
    finally:
        conn.close()


def _detect_cold_wave(forecasts: list[dict], target_idx: int) -> float:
    """Detecte une vague de froid (3+ jours consecutifs < 2C national).
    Retourne un bonus de 0 a 25 points (hors poids ajustables)."""
    if not forecasts or target_idx >= len(forecasts):
        return 0

    start = max(0, target_idx - 2)
    end = min(len(forecasts), target_idx + 3)

    cold_days = sum(
        1 for i in range(start, end)
        if forecasts[i].get("temp_moy", 10) < 2  # < 2C national
    )

    if cold_days >= 5:
        return 25
    if cold_days >= 4:
        return 20
    if cold_days >= 3:
        return 15
    if cold_days >= 2:
        return 8
    return 0


# ================================================================
# PROBABILITES
# ================================================================

def _compute_probabilities(score: float, remaining: dict) -> tuple[float, float, float]:
    """Convertit le score de risque en probabilites par couleur."""
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return 0.0, 0.0, 1.0

    # Approche sigmoide simplifiee
    if score >= 80:
        p_rouge = 0.80 + (score - 80) * 0.008
    elif score >= Config.SEUIL_ROUGE:
        p_rouge = 0.45 + (score - Config.SEUIL_ROUGE) * 0.023
    elif score >= 50:
        p_rouge = 0.15 + (score - 50) * 0.020
    else:
        p_rouge = max(0.02, score * 0.003)

    if remaining["ROUGE"] == 0:
        p_rouge = 0.0

    if score >= 50:
        p_blanc = min(0.40, 0.15 + (score - 50) * 0.008)
    elif score >= Config.SEUIL_BLANC:
        p_blanc = 0.25 + (score - Config.SEUIL_BLANC) * 0.01
    else:
        p_blanc = max(0.05, 0.10 + score * 0.005)

    if remaining["BLANC"] == 0:
        p_blanc = 0.0

    p_bleu = max(0.0, 1.0 - p_rouge - p_blanc)

    # Normaliser
    total = p_rouge + p_blanc + p_bleu
    p_rouge = round(p_rouge / total, 3)
    p_blanc = round(p_blanc / total, 3)
    p_bleu = round(1.0 - p_rouge - p_blanc, 3)
    return (p_rouge, p_blanc, p_bleu)


# ================================================================
# RAISON HUMAINE
# ================================================================

def _build_raison_v2(temp_moy: float, temp_min: float,
                     gradient_score: float, cold_wave: float,
                     remaining: dict, target_date: date, d_left: int,
                     rte_score: dict | None, cluster_score: float) -> str:
    """Construit une explication humaine de la prediction v2."""
    raisons = []

    # Temperature nationale
    if temp_moy < -2:
        raisons.append(f"Grand froid national ({temp_moy:.0f}C moy.)")
    elif temp_moy < 2:
        raisons.append(f"Froid national ({temp_moy:.0f}C moy.)")
    elif temp_moy < 5:
        raisons.append(f"Frais ({temp_moy:.0f}C moy.)")

    # Gradient
    if gradient_score >= 70:
        raisons.append("Chute de temperature brutale")
    elif gradient_score >= 50:
        raisons.append("Baisse de temperature")

    # Vague de froid
    if cold_wave >= 15:
        raisons.append("Vague de froid detectee")

    # Clustering
    if cluster_score >= 70:
        raisons.append("Continuite jour rouge probable")
    elif cluster_score >= 50:
        raisons.append("Froid persistant")

    # RTE
    if rte_score and rte_score.get("available"):
        if rte_score["score"] >= 70:
            peak = rte_score.get("peak_mw")
            if peak:
                raisons.append(f"Forte conso prevue ({peak // 1000} GW)")
            else:
                raisons.append("Forte consommation prevue")

    # Jours feries / weekend
    if is_french_holiday(target_date):
        raisons.append("Jour ferie (rouge improbable)")
    elif target_date.weekday() >= 5:
        raisons.append("Week-end (rouge improbable)")

    # Budget
    if remaining["ROUGE"] <= 5 and remaining["ROUGE"] > 0 and target_date.month >= 2:
        raisons.append(f"Fin saison, {remaining['ROUGE']}j rouges restants")
    if d_left < 30 and remaining["ROUGE"] > 3:
        raisons.append("Forte pression quota rouge")

    return " · ".join(raisons) if raisons else "Conditions normales"


# ================================================================
# RESULTAT + STOCKAGE
# ================================================================

def _result(target_date: date, couleur: str, score: float,
            p_bleu: float, p_blanc: float, p_rouge: float,
            weather: dict | None = None, raison: str = "",
            remaining: dict | None = None) -> dict:
    """Formate le resultat de prediction."""
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


def store_prediction(pred: dict, horizon: str = "J-1"):
    """Enregistre une prediction en base."""
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
        logger.info(f"[Prediction] {pred['date']} -> {pred['couleur_predite']} "
                     f"(score={pred['score_risque']}, {horizon})")
    finally:
        conn.close()
