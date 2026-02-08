"""Algorithme de prediction Tempo v2.1 — audit complet corrige.

Facteurs de scoring (sur 100, poids ajustables) :
  1. Temperature nationale ponderee (8 villes) — recalibree zone 0-5C
  2. Pression budgetaire avec profil mensuel historique
  3. Jour de la semaine + jours feries
  4. Gradient thermique (chute de temperature J/J-1)
  5. Clustering : continuite des jours rouges consecutifs
  6. Consommation RTE eco2mix (prevision pointe + nucleaire)

Bonus hors-poids :
  - Vague de froid (3+ jours consecutifs < 2C national) : +15-25 pts

Corrections audit v2.1 :
  - Fix #2 : predict_range decremente les quotas simules
  - Fix #6 : probabilites recalibrees (sigmoide reelle)
  - Fix #8 : fenetre vague de froid symetrique (5 jours centres)
  - Fix #11 : clustering evite requetes DB inutiles pour jours futurs
"""

import logging
import math
from functools import lru_cache
from datetime import date, datetime, timedelta
from database import get_db, get_current_weights
from config import Config
from tempo_client import get_remaining_days, is_in_season, days_left_in_season

logger = logging.getLogger(__name__)


# ================================================================
# JOURS FERIES FRANCAIS
# ================================================================

@lru_cache(maxsize=8)
def _get_french_holidays(year: int) -> frozenset[date]:
    """Retourne l'ensemble des jours feries francais pour une annee.
    Fix #11 audit v4 : cache LRU pour eviter recalcul a chaque appel."""
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
    return frozenset(holidays)


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
                rte_score: dict | None = None,
                _actuals_cache: dict | None = None) -> dict:
    """Predit la couleur Tempo pour une date donnee (algorithme v2.1)."""
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

    # Fix meteo #1 : qualite de la meteo (api / climatology / simulated)
    forecast_quality = weather.get("forecast_quality", "api") if weather else "simulated"

    # === 1. Score temperature (recalibre zone 0-5C) ===
    temp_score = _score_temperature_v2(temp_moy)

    # === 2. Score budget (profil mensuel) ===
    budget_score = _score_budget_v2(remaining, d_left, target_date)

    # === 3. Score jour semaine + feries ===
    dow_score = _score_weekday_v2(target_date)

    # === 4. Score gradient thermique (chute J/J-1) ===
    gradient_score = _score_gradient(forecasts or [], target_idx)

    # === 5. Score clustering (jours rouges consecutifs) ===
    # Fix #11 : passer le cache actuals pour eviter requetes DB inutiles
    cluster_score = _score_clustering(
        target_date, forecasts or [], target_idx, _actuals_cache)

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

    # Attenuation si meteo simulee (Open-Meteo indisponible, fallback saisonnier)
    if forecast_quality == "simulated":
        score_risque = score_risque * 0.5 + 50 * 0.5

    # === Determiner la couleur predite ===
    if score_risque >= Config.SEUIL_ROUGE and remaining["ROUGE"] > 0:
        couleur = "ROUGE"
    elif score_risque >= Config.SEUIL_BLANC and remaining["BLANC"] > 0:
        couleur = "BLANC"
    else:
        couleur = "BLEU"

    # === Probabilites (Fix #6 : sigmoide calibree) ===
    prob_rouge, prob_blanc, prob_bleu = _compute_probabilities(
        score_risque, remaining)

    # === Raison humaine ===
    raison = _build_raison_v2(
        temp_moy, temp_min, gradient_score, cold_wave, remaining,
        target_date, d_left, rte_score, cluster_score, forecast_quality)

    # Sub-scores pour stockage ML (Fix audit apprentissage #2)
    sub_scores = {
        "score_temperature": round(temp_score, 1),
        "score_budget": round(budget_score, 1),
        "score_weekday": round(dow_score, 1),
        "score_gradient": round(gradient_score, 1),
        "score_clustering": round(cluster_score, 1),
        "score_rte": round(rte_s, 1),
    }

    return _result(target_date, couleur, score_risque,
                   prob_bleu, prob_blanc, prob_rouge, weather, raison,
                   remaining, sub_scores)


def predict_range(forecasts: list[dict],
                  rte_score: dict | None = None,
                  simulated: bool = False) -> list[dict]:
    """Predit la couleur pour chaque jour du forecast.

    Fix #2 : decremente les quotas simules pour que les predictions
    ulterieures ne predisent pas plus de rouges/blancs que le quota restant.
    Fix #11 : charge les actuals une seule fois (batch).
    Fix v5 #5 : si J+1 a une couleur officielle dans actuals, l'utiliser.
    Fix v5 #6 : propage le flag simulated (donnees meteo fallback).
    """
    remaining = get_remaining_days()
    weights = get_current_weights()

    # Fix #11 : pre-charger les actuals recents en une seule requete
    actuals_cache = _load_recent_actuals()

    # Fix v5 #5 : pre-charger les actuals futurs (J+1) s'ils existent
    actuals_future = _load_future_actuals()

    # Fix #2 : copier remaining pour decrementation simulee
    sim_remaining = dict(remaining)

    predictions = []
    for i, weather in enumerate(forecasts):
        target = date.fromisoformat(weather["date"])
        delta = (target - date.today()).days
        target_str = target.isoformat()

        # Fix v5 #5 : si la couleur officielle est connue pour cette date, l'utiliser
        if target_str in actuals_future:
            couleur_officielle = actuals_future[target_str]
            pred = _result_confirmed(target, couleur_officielle, weather)
            pred["horizon"] = f"J-{delta}" if delta > 0 else ("J0" if delta == 0 else f"J+{-delta}")
            pred["confirmed"] = True
            pred["simulated"] = False
            predictions.append(pred)
            # Decrementer le quota meme pour les confirmees
            if couleur_officielle == "ROUGE" and sim_remaining["ROUGE"] > 0:
                sim_remaining["ROUGE"] -= 1
            elif couleur_officielle == "BLANC" and sim_remaining["BLANC"] > 0:
                sim_remaining["BLANC"] -= 1
            continue

        # Fix #5 audit v4 : RTE fiable J+1 seulement, degrade J+2/J+3, ignore au-dela
        day_rte = rte_score
        if rte_score and rte_score.get("available") and delta > 1:
            if delta > 3:
                day_rte = None  # Au-dela de J+3, RTE non pertinent
            else:
                # Attenuation lineaire vers neutre (50)
                blend = max(0.0, 1.0 - (delta - 1) / 3.0)
                day_rte = {
                    **rte_score,
                    "score": round(rte_score["score"] * blend + 50 * (1 - blend)),
                }

        pred = predict_day(target, weather=weather,
                           forecasts=forecasts, target_idx=i,
                           remaining=sim_remaining, weights=weights,
                           rte_score=day_rte,
                           _actuals_cache=actuals_cache)
        pred["horizon"] = f"J-{delta}" if delta > 0 else ("J0" if delta == 0 else f"J+{-delta}")
        pred["confirmed"] = False
        pred["simulated"] = simulated
        predictions.append(pred)

        # Fix #2 : decrementer le quota simule si on a predit rouge/blanc
        couleur = pred["couleur_predite"]
        if couleur == "ROUGE" and sim_remaining["ROUGE"] > 0:
            sim_remaining["ROUGE"] -= 1
        elif couleur == "BLANC" and sim_remaining["BLANC"] > 0:
            sim_remaining["BLANC"] -= 1

    return predictions


def _result_confirmed(target_date: date, couleur: str,
                      weather: dict | None = None) -> dict:
    """Resultat pour une date dont la couleur officielle est connue."""
    p_r = 1.0 if couleur == "ROUGE" else 0.0
    p_b = 1.0 if couleur == "BLANC" else 0.0
    p_bl = 1.0 if couleur == "BLEU" else 0.0
    return _result(target_date, couleur, 100 if couleur == "ROUGE" else (50 if couleur == "BLANC" else 0),
                   p_bl, p_b, p_r, weather, "Couleur officielle EDF")


def _load_recent_actuals() -> dict[str, str]:
    """Charge les couleurs reelles des 7 derniers jours en une requete.
    Retourne {date_iso: couleur}. Fix #11."""
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ?",
            (since,)
        ).fetchall()
        return {row["date"]: row["couleur_reelle"] for row in rows}
    finally:
        conn.close()


def _load_future_actuals() -> dict[str, str]:
    """Charge les couleurs officielles pour aujourd'hui et demain uniquement.
    Fix v5 #5 : permet d'utiliser la couleur EDF confirmee au lieu de la prediction.
    Fix audit #P1 : limite a J+0/J+1 pour eviter de traiter des actuals
    synthetiques (backfill) comme des couleurs confirmees."""
    conn = get_db()
    try:
        today = date.today()
        tomorrow = (today + timedelta(days=1)).isoformat()
        rows = conn.execute(
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ? AND date <= ?",
            (today.isoformat(), tomorrow)
        ).fetchall()
        return {row["date"]: row["couleur_reelle"] for row in rows}
    finally:
        conn.close()


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
    # Fix #4 audit v4 : exclure le mois courant (deja partiellement ecoule)
    months_ahead = []
    m = month
    while True:
        m = m + 1 if m < 12 else 1
        if m == 6:
            break
        months_ahead.append(m)
    expected_remaining_pct = sum(
        Config.MONTHLY_RED_PROFILE.get(mo, 0.0) for mo in months_ahead
    )
    expected_remaining = expected_remaining_pct * Config.JOURS_ROUGES_TOTAL

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
                      target_idx: int,
                      actuals_cache: dict | None = None) -> float:
    """Score 0-100 base sur la continuite des jours rouges.

    Correction #5 : si la veille est rouge/prevue rouge et que le froid
    continue, forte probabilite de jour rouge consecutif.
    Fix #11 : utilise actuals_cache au lieu d'ouvrir une connexion DB.
    """
    yesterday = target_date - timedelta(days=1)
    yesterday_str = yesterday.isoformat()

    # Fix #11 : utiliser le cache au lieu d'une requete DB par jour
    yesterday_was_red = False
    if actuals_cache is not None:
        yesterday_was_red = actuals_cache.get(yesterday_str) == "ROUGE"
    else:
        # Fallback : seul J+0 ou J+1 ont une chance d'avoir un actual
        delta = (target_date - date.today()).days
        if delta <= 1:
            yesterday_was_red = _check_yesterday_color(yesterday)

    if yesterday_was_red:
        # La veille etait rouge. Le froid continue-t-il ?
        # Fix #3 audit v4 : target_idx >= 0 au lieu de > 0
        if target_idx >= 0 and target_idx < len(forecasts):
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
    Retourne un bonus de 0 a 25 points (hors poids ajustables).

    Fix #8 : fenetre symetrique de 5 jours centree sur target_idx.
    """
    if not forecasts or target_idx >= len(forecasts):
        return 0

    # Fenetre fixe de 5 jours centree sur target_idx
    half_window = 2
    start = max(0, target_idx - half_window)
    end = min(len(forecasts), target_idx + half_window + 1)

    window_size = end - start
    cold_days = sum(
        1 for i in range(start, end)
        if forecasts[i].get("temp_moy", 10) < 2  # < 2C national
    )

    # Fix #16 audit v4 : bonus maximum si toute la fenetre est froide
    # (meme si fenetre < 5 en bord de forecast)
    if cold_days >= window_size and cold_days >= 3:
        return 25  # 100% de la fenetre froide (3+ jours)
    if cold_days >= 4:
        return 20
    if cold_days >= 3:
        return 15
    if cold_days >= 2:
        return 8
    return 0


# ================================================================
# PROBABILITES (Fix #6 : sigmoide calibree)
# ================================================================

def _sigmoid(x: float, center: float, steepness: float) -> float:
    """Sigmoide logistique entre 0 et 1."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - center)))


def _compute_probabilities(score: float, remaining: dict) -> tuple[float, float, float]:
    """Convertit le score de risque en probabilites par couleur.

    Fix #6 : utilise une sigmoide centree sur les seuils pour des
    probabilites plus coherentes (ex: seuil ROUGE 65 -> ~50% a 65).
    """
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return (0.0, 0.0, 1.0)  # Seul BLEU possible

    # Probabilite rouge via sigmoide centree sur SEUIL_ROUGE
    # steepness 0.12 => transition douce sur ~20 points autour du seuil
    p_rouge = _sigmoid(score, Config.SEUIL_ROUGE, 0.12)

    # Probabilite blanc via sigmoide centree sur SEUIL_BLANC
    p_blanc = _sigmoid(score, Config.SEUIL_BLANC, 0.08) * (1.0 - p_rouge)

    # Appliquer les contraintes de quota
    if remaining["ROUGE"] == 0:
        p_rouge = 0.0
    if remaining["BLANC"] == 0:
        p_blanc = 0.0

    p_bleu = max(0.0, 1.0 - p_rouge - p_blanc)

    # Normaliser pour que la somme = 1.0
    total = p_rouge + p_blanc + p_bleu
    if total <= 0:
        return (0.0, 0.0, 1.0)
    p_rouge = round(p_rouge / total, 3)
    p_blanc = round(p_blanc / total, 3)
    p_bleu = round(max(0.0, 1.0 - p_rouge - p_blanc), 3)
    return (p_rouge, p_blanc, p_bleu)


# ================================================================
# RAISON HUMAINE
# ================================================================

def _build_raison_v2(temp_moy: float, temp_min: float,
                     gradient_score: float, cold_wave: float,
                     remaining: dict, target_date: date, d_left: int,
                     rte_score: dict | None, cluster_score: float,
                     forecast_quality: str = "api") -> str:
    """Construit une explication humaine de la prediction v2."""
    raisons = []

    # Signaler la qualite degradee si Open-Meteo indisponible
    if forecast_quality == "simulated":
        raisons.append("Meteo simulee (confiance faible)")

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
            remaining: dict | None = None,
            sub_scores: dict | None = None) -> dict:
    """Formate le resultat de prediction."""
    result = {
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
    if sub_scores:
        result.update(sub_scores)
    return result


def store_prediction(pred: dict, horizon: str = "J-1",
                     cycle_id: str = "") -> dict | None:
    """Enregistre une prediction en base.

    Fix #3 : INSERT OR REPLACE avec UNIQUE(date, horizon) evite les doublons.
    Fix #2/#7 : stocke les 6 sub-scores pour l'apprentissage ML.
    Fix v5 #3 : detecte les changements vs prediction precedente, retourne le changement.
    Fix v5 #7 : stocke cycle_id, simulated, confirmed.
    """
    conn = get_db()
    change = None
    try:
        # Fix v5 #3 : recuperer la prediction precedente pour detecter les changements
        prev = conn.execute(
            "SELECT couleur_predite, score_risque FROM predictions WHERE date = ? AND horizon = ?",
            (pred["date"], horizon)
        ).fetchone()

        couleur_precedente = ""
        if prev and prev["couleur_predite"] != pred["couleur_predite"]:
            couleur_precedente = prev["couleur_predite"]
            change = {
                "date": pred["date"],
                "horizon": horizon,
                "couleur_avant": prev["couleur_predite"],
                "couleur_apres": pred["couleur_predite"],
                "score_avant": prev["score_risque"],
                "score_apres": pred["score_risque"],
            }
            # Enregistrer le changement
            conn.execute(
                """INSERT INTO prediction_changes
                   (date, horizon, couleur_avant, couleur_apres,
                    score_avant, score_apres, cycle_id, timestamp_change)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pred["date"], horizon,
                 prev["couleur_predite"], pred["couleur_predite"],
                 prev["score_risque"], pred["score_risque"],
                 cycle_id, datetime.now().isoformat()),
            )

        conn.execute(
            """INSERT OR REPLACE INTO predictions
               (date, couleur_predite, probabilite_bleu, probabilite_blanc,
                probabilite_rouge, score_risque, temp_min_prevue, temp_max_prevue,
                pression_prevue, jours_rouges_restants, jours_blancs_restants,
                raison, horizon, timestamp_prediction,
                score_temperature, score_budget, score_weekday,
                score_gradient, score_clustering, score_rte,
                cycle_id, couleur_precedente, simulated, confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pred["date"], pred["couleur_predite"],
             pred["probabilite_bleu"], pred["probabilite_blanc"],
             pred["probabilite_rouge"], pred["score_risque"],
             pred.get("temp_min_prevue"), pred.get("temp_max_prevue"),
             pred.get("pression_prevue"),
             pred.get("jours_rouges_restants"), pred.get("jours_blancs_restants"),
             pred.get("raison", ""), horizon,
             datetime.now().isoformat(),
             pred.get("score_temperature", 0), pred.get("score_budget", 0),
             pred.get("score_weekday", 0), pred.get("score_gradient", 0),
             pred.get("score_clustering", 0), pred.get("score_rte", 0),
             cycle_id, couleur_precedente,
             1 if pred.get("simulated") else 0,
             1 if pred.get("confirmed") else 0),
        )
        conn.commit()
        log_suffix = ""
        if couleur_precedente:
            log_suffix = f" [CHANGE: {couleur_precedente} -> {pred['couleur_predite']}]"
        logger.info(f"[Prediction] {pred['date']} -> {pred['couleur_predite']} "
                     f"(score={pred['score_risque']}, {horizon}, cycle={cycle_id}){log_suffix}")
        return change
    finally:
        conn.close()
