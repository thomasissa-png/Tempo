"""Algorithme de prediction Tempo v3.0 — Meteo France.

Audit ML fev 2026 : recalibrage complet des poids et seuils.
- Poids temperature 27% → 40% (seul signal discriminant, ecart 53pts BLEU→ROUGE)
- Poids budget 20% → 12% (causait 210 faux BLANC sur 711 evaluations)
- Seuil ROUGE dynamique : abaisse a 55 si temp < 7C et budget >= 50 (capture
  les 22 rouges reels qui avaient un score 45-65)
- Budget scoring abaisse pour reduire la sur-prediction en debut de saison
- Boost urgence budgetaire releve a 80 (etait 70, trop agressif)

Resultats backtest (597 evaluations avec sub-scores) :
  Accuracy : 54.6% → 64.5% (+9.9pts)
  ROUGE recall : 59.6% → 82.7% (+23pts)
  ROUGE F1 : 37.3% → 53.4% (+16pts)
  BLANC precision : 29.0% → 34.7% (+5.7pts)

Facteurs de scoring (sur 100, poids ajustables) :
  1. Temperature nationale ponderee (9 villes, poids 40%)
  2. Pression budgetaire avec profil mensuel (12%)
  3. Jour de la semaine + jours feries (10%)
  4. Gradient thermique (chute de temperature J/J-1) (8%)
  5. Clustering : continuite des jours rouges consecutifs (12%)
  6. Consommation RTE eco2mix (prevision pointe + nucleaire) (10%)
  7. Pression atmospherique (anticyclone hivernal = risque accru) (8%)
"""

import logging
import math
from functools import lru_cache
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_PARIS_TZ = ZoneInfo("Europe/Paris")

def _now_paris() -> datetime:
    """Retourne l'heure actuelle en timezone Paris (CET/CEST)."""
    return datetime.now(tz=_PARIS_TZ)

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


def _count_eligible_days(start: date, end: date, mode: str = "rouge") -> int:
    """Compte le nombre exact de jours eligibles entre start et end (inclus).

    Remplace l'approximation 5/7 ou 6/7 par un comptage exact qui tient
    compte des weekends ET des jours feries.

    mode='rouge' : eligible = weekday (lun-ven) ET pas ferie (R2)
    mode='blanc' : eligible = lun-sam, pas dimanche (R3).
                   Les feries restent eligibles pour BLANC.
    """
    if end < start:
        return 0
    count = 0
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        if mode == "rouge":
            if d.weekday() < 5 and not is_french_holiday(d):
                count += 1
        else:  # blanc
            if d.weekday() != 6:  # pas dimanche
                count += 1
        d += one_day
    return count


# ================================================================
# HELPERS ML : interpolation continue + wind chill
# ================================================================

def _piecewise_linear(x: float, points: list[tuple[float, float]]) -> float:
    """Interpolation lineaire par morceaux entre des points de controle.
    ML-2 : remplace les step functions par des transitions continues.
    points = [(x0, y0), (x1, y1), ...] tries par x croissant."""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]


def _wind_chill(temp: float, wind_speed_kmh: float) -> float:
    """Indice de refroidissement eolien (formule nord-americaine).
    ML-4 : applicable quand temp <= 10C et vent >= 4.8 km/h."""
    if temp > 10 or wind_speed_kmh < 4.8:
        return temp
    wc = (13.12 + 0.6215 * temp
          - 11.37 * (wind_speed_kmh ** 0.16)
          + 0.3965 * temp * (wind_speed_kmh ** 0.16))
    return min(temp, wc)


# Points de controle pour scoring continu (ML-2)
_TEMP_SCORE_POINTS = [
    (-5, 98), (-2, 90), (0, 80), (2, 68), (4, 55),
    (6, 40), (8, 25), (10, 15), (14, 8), (20, 3),
]

_GRADIENT_SCORE_POINTS = [
    (-5, 5), (-3, 15), (0, 25), (1, 35), (3, 50),
    (5, 70), (8, 90), (12, 98),
]

# Scoring pression atmospherique (Phase 2 Meteo France)
# Anticyclone hivernal (haute pression + froid) = forte consommation chauffage
# Les jours rouges Tempo correlent avec les situations anticycloniques froides :
#   - Haute pression (> 1025 hPa) + froid = ciel degage, rayonnement nocturne,
#     temperatures qui plongent, consommation electrique maximale
#   - Basse pression (< 1005 hPa) = temps perturbe, souvent plus doux
# Le score est maximal pour les hautes pressions en hiver
_PRESSURE_SCORE_POINTS = [
    (995, 10),    # Depression : temps doux, faible risque
    (1005, 20),   # Pression normale basse
    (1013, 35),   # Pression standard
    (1020, 50),   # Anticyclone modere
    (1025, 70),   # Anticyclone marque — risque significatif
    (1030, 85),   # Anticyclone puissant — risque eleve
    (1035, 95),   # Anticyclone exceptionnel — quasi certain rouge si froid
]

# Facteur de confiance par source meteo (attenuation pour modeles basse resolution)
_SOURCE_CONFIDENCE = {
    "arome": 1.0,    # Haute resolution 1.3 km — confiance maximale
    "arpege": 0.85,  # Resolution 10 km — legere attenuation
    "open-meteo": 0.80,  # Fallback ~25 km — attenuation supplementaire
}


# ================================================================
# PREDICTION PRINCIPALE
# ================================================================

def predict_day(target_date: date, weather: dict | None = None,
                forecasts: list[dict] | None = None, target_idx: int = 0,
                remaining: dict | None = None,
                weights: dict | None = None,
                rte_score: dict | None = None,
                _actuals_cache: dict | None = None,
                _learnings: dict | None = None,
                vigilance: dict | None = None) -> dict:
    """Predit la couleur Tempo pour une date donnee (algorithme v2.2)."""
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
    w_temp = weights.get("temperature", 0.27)
    w_budget = weights.get("jours_restants", 0.20)
    w_dow = weights.get("jour_semaine", 0.10)
    w_gradient = weights.get("gradient_thermique", 0.13)
    w_cluster = weights.get("clustering", 0.10)
    w_rte = weights.get("consommation_rte", 0.13)
    w_pressure = weights.get("pression", 0.07)

    # --- Donnees meteo ---
    temp_min = weather.get("temp_min", 5) if weather else 5
    temp_max = weather.get("temp_max", 10) if weather else 10
    temp_moy = weather.get("temp_moy", 7.5) if weather else 7.5
    wind_speed = weather.get("wind_speed", 10) if weather else 10
    humidity = weather.get("humidity", 50) if weather else 50
    pressure = weather.get("pressure") if weather else None
    source = weather.get("source", "arpege") if weather else "arpege"

    forecast_quality = weather.get("forecast_quality", "api") if weather else "api"

    # === 1. Score temperature (recalibre + wind chill) ===
    temp_score = _score_temperature_v2(temp_moy, wind_speed)

    # === 2. Score budget (profil mensuel) ===
    budget_score = _score_budget_v2(remaining, d_left, target_date)

    # === 3. Score jour semaine + feries ===
    dow_score = _score_weekday_v2(target_date)

    # === 4. Score gradient thermique (chute J/J-1) ===
    gradient_score = _score_gradient(forecasts or [], target_idx)

    # === 5. Score clustering (jours rouges consecutifs) ===
    cluster_score = _score_clustering(
        target_date, forecasts or [], target_idx, _actuals_cache)

    # === 6. Score consommation RTE ===
    rte_s = 50  # neutre par defaut si pas de donnees RTE
    if rte_score and rte_score.get("available"):
        rte_s = rte_score["score"]

    # === 7. Score pression atmospherique (Phase 2 Meteo France) ===
    # Anticyclone hivernal (haute pression + froid) = risque accru
    # Le score est combine avec la temperature : haute pression seule
    # n'est pas un signal (ex: anticyclone d'ete = beau temps chaud)
    pressure_score = _score_pressure(pressure, temp_moy)

    # === Bonus vague de froid (ameliore avec humidite) ===
    # Phase 2 : l'humidite amplifie le bonus — froid humide = plus de chauffage
    cold_wave = _detect_cold_wave(forecasts or [], target_idx, humidity)
    if cold_wave > 0:
        temp_score = min(100, temp_score + cold_wave)

    # === Bonus vigilance Meteo France grand froid ===
    # Signal expert humain — ajoute au score temperature si alerte active
    vigilance_bonus = 0.0
    if vigilance and vigilance.get("grand_froid"):
        vigilance_bonus = 20.0
        temp_score = min(100, temp_score + vigilance_bonus)
    elif vigilance and vigilance.get("neige_verglas"):
        vigilance_bonus = 10.0
        temp_score = min(100, temp_score + vigilance_bonus)

    # === C-1: Raw sub-scores BEFORE corrections (uncontaminated for ML training) ===
    raw_sub_scores = {
        "score_temperature_raw": round(temp_score, 1),
        "score_budget_raw": round(budget_score, 1),
        "score_weekday_raw": round(dow_score, 1),
        "score_gradient_raw": round(gradient_score, 1),
        "score_clustering_raw": round(cluster_score, 1),
        "score_rte_raw": round(rte_s, 1),
    }

    # === Corrections par facteur du journal d'apprentissage ===
    learning_adjustment = 0.0
    if _learnings and _learnings.get("factor"):
        factor_corrections = _learnings["factor"]
        temp_score = _apply_factor_correction(temp_score, factor_corrections, "temperature")
        budget_score = _apply_factor_correction(budget_score, factor_corrections, "budget")
        dow_score = _apply_factor_correction(dow_score, factor_corrections, "weekday")
        gradient_score = _apply_factor_correction(gradient_score, factor_corrections, "gradient")
        cluster_score = _apply_factor_correction(cluster_score, factor_corrections, "clustering")
        rte_s = _apply_factor_correction(rte_s, factor_corrections, "rte")

    # === Score composite pondere ===
    base_score = (
        temp_score * w_temp
        + budget_score * w_budget
        + dow_score * w_dow
        + gradient_score * w_gradient
        + cluster_score * w_cluster
        + rte_s * w_rte
        + pressure_score * w_pressure
    )

    # === Confiance source meteo (AROME vs ARPEGE) ===
    # AROME (J a J+2) : resolution 1.3 km, confiance maximale
    # ARPEGE (J+2 a J+5) : resolution 10 km, legere attenuation
    # Attenuation = score ramene vers le neutre (50) proportionnellement
    source_confidence = _SOURCE_CONFIDENCE.get(source, 0.85)
    if source_confidence < 1.0:
        base_score = base_score * source_confidence + 50 * (1 - source_confidence)

    # === Boost d'urgence budgétaire (recalibre audit fev 2026) ===
    # Actif uniquement en urgence budgetaire reelle (budget >= 80), et
    # avec un coefficient reduit (0.008 au lieu de 0.015) pour eviter que
    # le budget ne noie le signal temperature en temps normal.
    # Le seuil dynamique ROUGE (amelioration 2) gere les cas intermediaires.
    if budget_score >= 80:
        urgency_boost = 1.0 + (budget_score - 80) * 0.008
        base_score *= urgency_boost

    score_risque = min(100, base_score)

    # === Corrections contextuelles du journal d'apprentissage ===
    # (horizon, température, mois, jour de semaine — ajustements au score composite)
    if _learnings:
        horizon_days = max(0, (target_date - date.today()).days)
        score_before = score_risque
        score_risque = _apply_learning_corrections(
            score_risque, target_date, horizon_days, temp_moy, _learnings)
        learning_adjustment = score_risque - score_before

    # === Score ML (GradientBoosting entraine sur 7 saisons + RTE) ===
    # Le ML utilise 33 features (meteo + temporel + RTE lag) pour capturer
    # les patterns de facon plus fine que le scoring manuel. Utilise comme :
    #   1. Filet de securite ROUGE : si ML predit ROUGE et scoring hesite, → ROUGE
    #   2. Filtre faux positifs : si ML predit BLEU et scoring dit BLANC, → BLEU
    ml_result = None
    try:
        from ml_scorer import compute_ml_score, ml_score_available
        if ml_score_available() and weather:
            ml_result = compute_ml_score(
                weather, target_date, forecasts or [],
                target_idx, _actuals_cache)
    except Exception:
        pass  # ML indisponible = on utilise le scoring seul

    # === Determiner la couleur predite (ensemble scoring + ML) ===
    # Seuil ROUGE dynamique (audit ML fev 2026)
    seuil_rouge_effectif = Config.SEUIL_ROUGE
    if (temp_moy < Config.SEUIL_ROUGE_TEMP_TRES_FROID
            and budget_score >= Config.SEUIL_ROUGE_BUDGET_MIN):
        seuil_rouge_effectif = Config.SEUIL_ROUGE_TRES_FROID
    elif (temp_moy < Config.SEUIL_ROUGE_TEMP_TRIGGER
            and budget_score >= Config.SEUIL_ROUGE_BUDGET_MIN):
        seuil_rouge_effectif = Config.SEUIL_ROUGE_FROID

    # Decision scoring classique
    if score_risque >= seuil_rouge_effectif and remaining["ROUGE"] > 0:
        couleur = "ROUGE"
    elif score_risque >= Config.SEUIL_BLANC and remaining["BLANC"] > 0:
        couleur = "BLANC"
    else:
        couleur = "BLEU"

    # === Ensemble ML : ajustement de la decision ===
    # Le ML utilise des seuils de probabilite optimises pour maximiser le
    # ROUGE recall. On l'utilise pour :
    if ml_result and ml_result.get("available"):
        ml_rouge = ml_result["score_rouge"]  # P(ROUGE) * 100
        ml_pred = ml_result["prediction"]

        # 1a. ML ROUGE fort : P(ROUGE) >= 15% → override meme si scoring faible
        #     Garde thermique stricte (< 6°C) pour eviter faux positifs hors froid
        #     En saison rouge uniquement (nov-mars, R1)
        #     Audit DS : le ML etait musele — ne pouvait agir que si scoring >= BLANC
        if (ml_rouge >= 15 and couleur != "ROUGE"
                and remaining["ROUGE"] > 0
                and temp_moy < 6
                and (target_date.month >= 11 or target_date.month <= 3)):
            couleur = "ROUGE"
            raison_ml = " · ML:ROUGE(fort)"

        # 1b. ML ROUGE modere : P(ROUGE) >= 10% + scoring >= BLANC → ROUGE
        #     Garde thermique relachee (< 8°C) car le scoring confirme la tendance
        elif (ml_pred == "ROUGE" and couleur != "ROUGE"
                and remaining["ROUGE"] > 0
                and score_risque >= Config.SEUIL_BLANC
                and temp_moy < 8
                and (target_date.month >= 11 or target_date.month <= 3)):
            couleur = "ROUGE"
            raison_ml = " · ML:ROUGE"

        # 2. Confirmation ROUGE : si scoring dit ROUGE et ML aussi, confiance haute
        elif couleur == "ROUGE" and ml_pred == "ROUGE":
            raison_ml = " · ML:confirme"

        # 3. Filtre faux BLANC : ML dit BLEU + scoring dit BLANC + ML P(rouge)<5%
        #    Reduit les faux BLANC du scoring quand le ML est tres confiant BLEU
        #    Fix budget : ne PAS filtrer si la pression budgetaire est forte
        #    (budget_score >= 50 = quotas tendus, les BLANC sont necessaires).
        #    Sans cette garde, le ML convertit TOUS les BLANC en BLEU en fin
        #    de saison, ignorant la pression budgetaire reelle.
        elif (couleur == "BLANC" and ml_pred == "BLEU" and ml_rouge < 5
              and score_risque < Config.SEUIL_ROUGE
              and budget_score < 50):
            couleur = "BLEU"
            raison_ml = " · ML:BLEU"

        else:
            raison_ml = ""
    else:
        raison_ml = ""

    # === Override densité progressive ROUGE ===
    # Quand la densité RED (remaining/eligible) est élevée, abaisser le seuil
    # RED effectif pour répartir les jours rouges plus uniformément.
    #
    # Problème résolu : avec 14 ROUGE restants sur 32 jours éligibles (densité
    # 44%), le budget (poids 12%) et l'urgency boost (1.16x) ne suffisent pas
    # à atteindre le seuil ROUGE standard (65) ou froid (55) quand la
    # température est de 5-7°C (fréquent en fév-mars). Sans cette réduction,
    # le système attend le density override critique (slack ≤ 1) vers le
    # ~11 mars, puis concentre tous les ROUGE en fin de saison.
    #
    # La réduction progressive permet de capturer les jours "moyennement
    # froids" (5-7°C) quand la densité l'exige, sans forcer ROUGE sur les
    # jours doux (≥ 8°C) grâce au score_risque naturellement bas.
    #
    # Calibration : densité 45% → réduction ~8 pts.
    #   - 5°C : score ~63, seuil 55-8=47 → ROUGE ✓ (déjà capté sans override)
    #   - 6°C : score ~60, seuil 55-8=47 → ROUGE ✓ (borderline capté)
    #   - 8°C : score ~55, seuil 65-8=57 → pas ROUGE ✓ (trop doux)
    # Densité 64% → réduction ~18 pts → capture 7°C (seuil 65-18=47)
    if couleur != "ROUGE" and remaining["ROUGE"] > 0:
        if target_date.month <= 3:
            _red_deadline = date(target_date.year, 3, 31)
        elif target_date.month >= 11:
            _red_deadline = date(target_date.year + 1, 3, 31)
        else:
            _red_deadline = target_date
        _red_eligible = _count_eligible_days(target_date, _red_deadline, "rouge")
        _red_slack = max(_red_eligible, 1) - remaining["ROUGE"]

        if _red_slack <= 1 and (target_date.month >= 11 or target_date.month <= 3):
            # Densité critique : force ROUGE sur chaque jour éligible
            couleur = "ROUGE"
            raison_ml += " · Densité critique ROUGE"
        elif _red_eligible > 0 and (target_date.month >= 11 or target_date.month <= 3):
            # Densité progressive : abaissement du seuil RED proportionnel
            _red_density = remaining["ROUGE"] / _red_eligible
            _density_reduction = _piecewise_linear(_red_density, [
                (0.20, 0), (0.35, 3), (0.50, 10), (0.70, 22),
            ])
            if _density_reduction > 0:
                _effective_threshold = seuil_rouge_effectif - _density_reduction
                if score_risque >= _effective_threshold:
                    couleur = "ROUGE"
                    raison_ml += f" · Pression densité ROUGE ({_red_density:.0%})"

    if couleur != "BLANC" and couleur != "ROUGE" and remaining["BLANC"] > 0:
        if target_date.month <= 5:
            _wh_deadline = date(target_date.year, 5, 31)
        elif target_date.month >= 9:
            _wh_deadline = date(target_date.year + 1, 5, 31)
        else:
            _wh_deadline = target_date
        _wh_eligible = _count_eligible_days(target_date, _wh_deadline, "blanc")
        _wh_slack = max(_wh_eligible, 1) - remaining["BLANC"]
        if _wh_slack <= 1:
            couleur = "BLANC"
            raison_ml += " · Densité critique BLANC"

    # === Fix #29 : contraintes dures EDF (non outrepassables par le scoring) ===
    # Regles officielles EDF appliquees APRES le scoring pour garantir la conformite.
    dow = target_date.weekday()  # 0=lundi, 5=samedi, 6=dimanche
    is_holiday = is_french_holiday(target_date)

    # R1 : Rouge uniquement du 1er novembre au 31 mars
    if couleur == "ROUGE" and not (target_date.month >= 11 or target_date.month <= 3):
        couleur = "BLANC" if remaining["BLANC"] > 0 else "BLEU"

    # R2 : Weekends et jours feries jamais rouges
    #   - Samedi rouge → blanc (samedi peut etre blanc)
    #   - Dimanche rouge → bleu (dimanche ne peut pas etre blanc non plus)
    #   - Ferie en semaine rouge → blanc (ferie peut etre blanc sauf dimanche)
    if couleur == "ROUGE" and (dow >= 5 or is_holiday):
        if remaining["BLANC"] > 0 and dow != 6:
            couleur = "BLANC"
        else:
            couleur = "BLEU"

    # R3 : Dimanche jamais blanc
    if couleur == "BLANC" and dow == 6:
        couleur = "BLEU"

    # R4 : Max 5 jours rouges consecutifs
    if couleur == "ROUGE" and _actuals_cache:
        consecutive = 0
        check = target_date - timedelta(days=1)
        while consecutive < 5:
            key = check.isoformat()
            if _actuals_cache.get(key) == "ROUGE":
                consecutive += 1
                check -= timedelta(days=1)
            else:
                break
        if consecutive >= 5:
            couleur = "BLANC" if remaining["BLANC"] > 0 else "BLEU"

    # === Probabilites (Fix #26 : softmax calibree) ===
    # Passe la couleur finale ET les contraintes EDF pour que les couleurs
    # impossibles (rouge un dimanche, blanc un dimanche, rouge hors saison,
    # rouge un ferie...) aient une probabilite de 0%.
    edf_impossible = set()
    # R1 : Rouge uniquement nov-mars
    if not (target_date.month >= 11 or target_date.month <= 3):
        edf_impossible.add("ROUGE")
    # R2 : Weekends et feries jamais rouges
    if dow >= 5 or is_holiday:
        edf_impossible.add("ROUGE")
    # R3 : Dimanche jamais blanc
    if dow == 6:
        edf_impossible.add("BLANC")

    prob_rouge, prob_blanc, prob_bleu = _compute_probabilities(
        score_risque, remaining, couleur, edf_impossible)

    # === Raison humaine ===
    raison = _build_raison_v2(
        temp_moy, temp_min, gradient_score, cold_wave, remaining,
        target_date, d_left, rte_score, cluster_score, forecast_quality,
        pressure=pressure, vigilance=vigilance,
        vigilance_bonus=vigilance_bonus)

    # Note apprentissage si correction significative
    if abs(learning_adjustment) >= 2:
        sign = "+" if learning_adjustment > 0 else ""
        raison += f" · Corr. apprentissage ({sign}{learning_adjustment:.0f}pts)"

    # Note ML si le ML a influence la decision
    if raison_ml:
        raison += raison_ml

    # Sub-scores pour stockage ML — valeurs après correction par facteur
    sub_scores = {
        "score_temperature": round(temp_score, 1),
        "score_budget": round(budget_score, 1),
        "score_weekday": round(dow_score, 1),
        "score_gradient": round(gradient_score, 1),
        "score_clustering": round(cluster_score, 1),
        "score_rte": round(rte_s, 1),
        "score_pressure": round(pressure_score, 1),
    }
    # ML model scores
    if ml_result and ml_result.get("available"):
        sub_scores["score_ml_rouge"] = ml_result["score_rouge"]
        sub_scores["score_ml_blanc"] = ml_result["score_blanc"]
        sub_scores["score_ml_bleu"] = ml_result["score_bleu"]
        sub_scores["ml_prediction"] = ml_result["prediction"]
    # C-1: Include raw sub-scores for uncontaminated ML training
    sub_scores.update(raw_sub_scores)

    return _result(target_date, couleur, score_risque,
                   prob_bleu, prob_blanc, prob_rouge, weather, raison,
                   remaining, sub_scores)


def predict_range(forecasts: list[dict],
                  rte_score: dict | None = None,
                  vigilance: dict | None = None) -> list[dict]:
    """Predit la couleur pour chaque jour du forecast.

    Fix #2 : decremente les quotas au fur et a mesure pour que les predictions
    ulterieures ne predisent pas plus de rouges/blancs que le quota restant.
    Fix #11 : charge les actuals une seule fois (batch).
    Fix v5 #5 : si J+1 a une couleur officielle dans actuals, l'utiliser.
    Phase 2 : passe les donnees de vigilance Meteo France au scoring.
    """
    remaining = get_remaining_days()
    weights = get_current_weights()

    # Fix #11 : pre-charger les actuals recents en une seule requete
    actuals_cache = _load_recent_actuals()

    # Fix v5 #5 : pre-charger les actuals futurs (J+1) s'ils existent
    actuals_future = _load_future_actuals()

    # Charger les corrections d'apprentissage une seule fois
    try:
        from performance_tracker import get_active_learnings
        learnings = get_active_learnings()
    except Exception:
        learnings = {}

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

        # Fix #5 audit v4 : RTE fiable J+1, degrade progressivement J+2→J+6
        # Fix audit v7 : courbe plus douce — signal RTE pertinent jusqu'à J+5-6
        # (les tendances de consommation restent valides sur une semaine)
        day_rte = rte_score
        if rte_score and rte_score.get("available") and delta > 1:
            if delta > 6:
                day_rte = None  # Au-delà de J+6, RTE non pertinent
            else:
                # Atténuation exponentielle douce : 1.0 à J+1, ~0.37 à J+4, ~0.14 à J+6
                blend = max(0.0, math.exp(-(delta - 1) / 3.0))
                day_rte = {
                    **rte_score,
                    "score": round(rte_score["score"] * blend + 50 * (1 - blend)),
                }

        pred = predict_day(target, weather=weather,
                           forecasts=forecasts, target_idx=i,
                           remaining=sim_remaining, weights=weights,
                           rte_score=day_rte,
                           _actuals_cache=actuals_cache,
                           _learnings=learnings,
                           vigilance=vigilance)
        pred["horizon"] = f"J-{delta}" if delta > 0 else ("J0" if delta == 0 else f"J+{-delta}")
        pred["confirmed"] = False
        pred["simulated"] = False
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
    Retourne {date_iso: couleur}. Fix #11.
    Fix data-integrity : exclut les actuals synthetiques (backfill)."""
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ? AND synthetic = 0",
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
        # Fix data-integrity : exclure les actuals synthétiques (seed)
        rows = conn.execute(
            """SELECT date, couleur_reelle FROM actuals
               WHERE date >= ? AND date <= ? AND synthetic = 0""",
            (today.isoformat(), tomorrow)
        ).fetchall()
        return {row["date"]: row["couleur_reelle"] for row in rows}
    finally:
        conn.close()


# ================================================================
# FONCTIONS DE SCORING v2
# ================================================================

def _score_temperature_v2(temp_moy_nationale: float, wind_speed_kmh: float = 0.0) -> float:
    """Score 0-100 base sur la temperature effective (avec wind chill).

    ML-2 : interpolation piecewise-linear (plus de step functions).
    ML-4 : integre le wind chill quand T<=10C et vent>=4.8 km/h.

    Points de controle calibres sur 20 ans d'historique :
    - Zone critique 0-5C (la ou se prennent les decisions)
    - Grands froids < -5C quasi certains rouge
    """
    temp_effective = _wind_chill(temp_moy_nationale, wind_speed_kmh)
    return round(_piecewise_linear(temp_effective, _TEMP_SCORE_POINTS), 1)


def _score_budget_v2(remaining: dict, d_left: int, target_date: date) -> float:
    """Score 0-100 base sur la pression budgetaire ROUGE et BLANC combinee.

    ML-1 : ajout signal BLANC (43 jours/saison ignores auparavant).
    ML-2 : fonctions continues (piecewise-linear) au lieu de step functions.
    Deadlines EDF :
      - ROUGE : 31 mars (R1), jours eligibles = weekdays uniquement (R2, 5/7)
      - BLANC : 31 mai, jours eligibles = lun-sam (R3 interdit dimanche, 6/7)
    """
    if d_left <= 0:
        return 0

    month = target_date.month

    # --- Pression ROUGE ---
    # Fix audit ML #39 : deadline ROUGE = 31 mars (regle R1), pas 31 mai
    if target_date.month <= 3:
        red_deadline = date(target_date.year, 3, 31)
    elif target_date.month >= 11:
        red_deadline = date(target_date.year + 1, 3, 31)
    else:
        red_deadline = target_date  # hors saison rouge, 0 jours
    # Comptage exact des jours éligibles ROUGE : weekdays hors fériés (R2).
    # Remplace l'approximation 5/7 qui ignorait les fériés (~4 en saison).
    red_eligible_days = _count_eligible_days(target_date, red_deadline, "rouge")
    if red_eligible_days == 0 and (target_date.month >= 11 or target_date.month <= 3):
        red_eligible_days = 1  # eviter division par 0
    rouge_pressure = _compute_budget_pressure(
        remaining["ROUGE"], red_eligible_days, month,
        Config.MONTHLY_RED_PROFILE, Config.JOURS_ROUGES_TOTAL)

    # --- Pression BLANC (ML-1 : signal manquant) ---
    # Deadline BLANC = 31 mai (tous les blancs doivent être placés avant juin)
    if target_date.month <= 5:
        white_deadline = date(target_date.year, 5, 31)
    elif target_date.month >= 9:
        white_deadline = date(target_date.year + 1, 5, 31)
    else:
        white_deadline = target_date  # juin-août, 0 jours
    # Comptage exact des jours éligibles BLANC : lun-sam, pas dimanche (R3).
    # Les fériés restent éligibles pour BLANC.
    white_eligible_days = _count_eligible_days(target_date, white_deadline, "blanc")
    if white_eligible_days == 0 and target_date.month not in (6, 7, 8):
        white_eligible_days = 1
    blanc_pressure = _compute_budget_pressure(
        remaining["BLANC"], white_eligible_days, month,
        Config.MONTHLY_WHITE_PROFILE, Config.JOURS_BLANCS_TOTAL)
    # Fix audit ML #3 : plafond dynamique lie a SEUIL_ROUGE - 1.
    # La pression BLANC ne doit jamais depasser le seuil ROUGE, sinon une forte
    # urgence BLANC declencherait une prediction ROUGE (semantiquement faux).
    # La pression ROUGE n'a pas de cap car haute pression ROUGE → ROUGE est correct.
    blanc_pressure = min(Config.SEUIL_ROUGE - 1, blanc_pressure)

    # Le score global est le max des deux pressions
    return min(100, max(rouge_pressure, blanc_pressure))


def _compute_budget_pressure(actual_remaining: int, d_left: int, month: int,
                              monthly_profile: dict, total_days: int) -> float:
    """Calcule le score de pression budgetaire pour un type de jour.
    ML-2 : scoring continu (piecewise-linear).
    Fix audit ML #39 : la densité d'urgence utilise les jours éligibles
    (weekdays uniquement pour ROUGE, R2 interdit weekends et fériés)."""
    expected_pct = monthly_profile.get(month, 0.0)

    # Jours restants attendus (mois futurs seulement)
    months_ahead = []
    m = month
    while True:
        m = m + 1 if m < 12 else 1
        if m == 6:
            break
        months_ahead.append(m)
    expected_remaining = sum(
        monthly_profile.get(mo, 0.0) for mo in months_ahead
    ) * total_days

    score = 0.0

    # Ratio actual/expected → score continu (ML-2)
    # Audit fev 2026 : courbe abaissee — l'ancien scoring montait a 60 trop
    # facilement (ratio 1.0 → 10pts suffisait a pousser vers BLANC en cumul).
    if expected_remaining > 0:
        ratio = actual_remaining / expected_remaining
        score += _piecewise_linear(ratio, [
            (0.5, 0), (1.0, 5), (1.5, 15), (2.0, 30), (3.0, 50), (5.0, 60),
        ])
    elif actual_remaining > 0:
        # Fix audit ML #39 : aucun mois futur n'attend de jours, mais il en
        # reste à placer → urgence maximale (tout doit être placé CE mois)
        score += 60

    # Boost mensuel continu (ML-2)
    # Audit fev 2026 : abaisse le plafond de 25 a 15 pour reduire le bruit
    score += _piecewise_linear(expected_pct, [
        (0.0, 0), (0.05, 3), (0.15, 8), (0.25, 15), (0.35, 15),
    ])

    # Urgence fin de saison continue (ML-2)
    # Audit fev 2026 : abaisse les coefficients bas pour eviter que la densite
    # faible (0.05-0.1) n'ajoute des points inutiles en debut de saison
    if actual_remaining > 0 and d_left > 0:
        density = actual_remaining / d_left
        score += _piecewise_linear(density, [
            (0.0, 0), (0.1, 3), (0.2, 15), (0.5, 35), (1.0, 50),
        ])

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


def _score_pressure(pressure: float | None, temp_moy: float) -> float:
    """Score 0-100 base sur la pression atmospherique (Phase 2 Meteo France).

    Anticyclone hivernal (haute pression + froid) correle fortement avec
    les jours rouges Tempo. La haute pression favorise :
      - Ciel degage → fort rayonnement nocturne → temperatures basses
      - Temps stable → froid persistant sur plusieurs jours
      - Consommation electrique de chauffage maximale

    Le score est attenue si la temperature est douce (> 10C) car une haute
    pression en douceur n'est pas un signal de risque Tempo.
    """
    if pressure is None:
        return 35  # Neutre si donnee indisponible

    raw_score = _piecewise_linear(pressure, _PRESSURE_SCORE_POINTS)

    # Attenuation par temperature : haute pression sans froid = peu de risque
    # Ex: anticyclone d'automne avec 15C → divise le signal par ~2
    if temp_moy > 10:
        attenuation = max(0.3, 1.0 - (temp_moy - 10) * 0.07)
        raw_score *= attenuation
    elif temp_moy < 0:
        # Froid + haute pression = amplification du signal
        raw_score = min(100, raw_score * 1.15)

    return round(raw_score, 1)


def _score_gradient(forecasts: list[dict], target_idx: int) -> float:
    """Score 0-100 base sur le gradient thermique (chute de temperature).

    ML-2 : interpolation piecewise-linear (plus de step functions).
    Une chute brutale de temperature entre J-1 et J est un signal fort.
    """
    if not forecasts or target_idx <= 0 or target_idx >= len(forecasts):
        return 30  # Neutre

    prev = forecasts[target_idx - 1]
    curr = forecasts[target_idx]

    prev_moy = prev.get("temp_moy", 10)
    curr_moy = curr.get("temp_moy", 10)
    drop = prev_moy - curr_moy  # Positif = il fait plus froid

    return round(_piecewise_linear(drop, _GRADIENT_SCORE_POINTS), 1)


def _score_clustering(target_date: date, forecasts: list[dict],
                      target_idx: int,
                      actuals_cache: dict | None = None) -> float:
    """Score 0-100 base sur la continuite des jours rouges.

    Correction #5 : si la veille est rouge/prevue rouge et que le froid
    continue, forte probabilite de jour rouge consecutif.
    Fix #11 : utilise actuals_cache au lieu d'ouvrir une connexion DB.
    ML-7 : saturation hebdomadaire (EDF place rarement 4+ rouges/semaine).
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

    score = 20  # Pas de continuite detectee (defaut)

    if yesterday_was_red:
        # La veille etait rouge. Le froid continue-t-il ?
        if target_idx >= 0 and target_idx < len(forecasts):
            curr_temp = forecasts[target_idx].get("temp_moy", 10)
            if curr_temp < 2:
                score = 90  # Rouge hier + froid qui continue = tres probable
            elif curr_temp < 5:
                score = 70
            else:
                score = 40  # Rouge hier mais radoucissement
    elif target_idx > 0 and target_idx < len(forecasts):
        # Verifier si J-1 est predit rouge dans les previsions courantes
        prev_temp = forecasts[target_idx - 1].get("temp_moy", 10)
        curr_temp = forecasts[target_idx].get("temp_moy", 10)
        if prev_temp < 2 and curr_temp < 2:
            score = 60  # Froid persistant J-1 et J
        elif prev_temp < 4 and curr_temp < 4:
            score = 40

    # ML-7 : Saturation hebdomadaire — EDF place rarement 4+ rouges/semaine
    # Fix audit ML #5 : seuils moins agressifs pour ne pas bloquer les vagues de froid
    # EDF peut placer jusqu'a 5 jours rouges consecutifs (regle R4), donc 3 rouges
    # dans une semaine est normal en hiver. Seul 4+ est vraiment inhabituel.
    if actuals_cache and score > 20:
        week_start = target_date - timedelta(days=target_date.weekday())
        reds_this_week = sum(
            1 for i in range(7)
            if actuals_cache.get((week_start + timedelta(days=i)).isoformat()) == "ROUGE"
        )
        if reds_this_week >= 4:
            score = max(10, int(score * 0.3))  # Très forte attenuation (4+ = rare)
        elif reds_this_week >= 3:
            score = int(score * 0.5)  # Attenuation moderee (3 = possible mais inhabituel)
        elif reds_this_week >= 2:
            score = int(score * 0.8)  # Légère attenuation

    return score


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


def _detect_cold_wave(forecasts: list[dict], target_idx: int,
                      humidity: float = 50.0) -> float:
    """Detecte une vague de froid (3+ jours consecutifs < 2C national).
    Retourne un bonus de 0 a 30 points (integre au facteur temperature).

    Fix #8 : fenetre symetrique de 5 jours centree sur target_idx.
    Phase 2 : l'humidite elevee (>= 80%) amplifie le bonus (+5 pts max).
    Le froid humide augmente la consommation de chauffage (sensation de froid
    plus intense, condensation, deperdition thermique accrue).
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
    if cold_days >= window_size and cold_days >= 3:
        bonus = 25  # 100% de la fenetre froide (3+ jours)
    elif cold_days >= 4:
        bonus = 20
    elif cold_days >= 3:
        bonus = 15
    elif cold_days >= 2:
        bonus = 8
    else:
        return 0

    # Phase 2 : amplification humidite (froid humide = plus de chauffage)
    if humidity >= 85:
        bonus += 5
    elif humidity >= 80:
        bonus += 3

    return min(30, bonus)


# ================================================================
# PROBABILITES (Fix #6 : sigmoide calibree)
# ================================================================

def _sigmoid(x: float, center: float, steepness: float) -> float:
    """Sigmoide logistique entre 0 et 1."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - center)))


def _compute_probabilities(score: float, remaining: dict,
                           couleur_finale: str | None = None,
                           edf_impossible: set | None = None) -> tuple[float, float, float]:
    """Convertit le score de risque en probabilites par couleur.

    Fix #26 : softmax a 3 classes — chaque couleur a sa propre distribution
    independante, sans suppression artificielle de BLANC par ROUGE.

    Fix audit ML #4 : centres et steepness configurables dans Config.

    Fix contraintes EDF : les couleurs impossibles par regles EDF (rouge
    un dimanche/ferie, blanc un dimanche, rouge hors saison nov-mars)
    ont une probabilite forcee a 0% AVANT la normalisation. La masse
    est redistribuee sur les couleurs possibles.

    Fix coherence : quand la couleur finale a ete forcee par un override
    (density, ML), les probabilites sont ajustees pour que la couleur
    predite soit toujours la plus probable.
    """
    if remaining["ROUGE"] == 0 and remaining["BLANC"] == 0:
        return (0.0, 0.0, 1.0)  # Seul BLEU possible

    impossible = edf_impossible or set()

    # Distances signees aux centres de chaque zone (configurables)
    k = Config.PROBA_STEEPNESS
    c_bleu = Config.PROBA_CENTER_BLEU
    c_blanc = Config.PROBA_CENTER_BLANC
    c_rouge = Config.PROBA_CENTER_ROUGE

    d_bleu = -(score - c_bleu)        # decroit quand score monte
    d_blanc = -abs(score - c_blanc)   # pic au centre, decroit symetriquement
    d_rouge = score - c_rouge          # croit quand score monte

    p_bleu = math.exp(k * d_bleu)
    p_blanc = math.exp(k * d_blanc)
    p_rouge = math.exp(k * d_rouge)

    # Contraintes de quota (quotas epuises)
    if remaining["ROUGE"] == 0:
        p_rouge = 0.0
    if remaining["BLANC"] == 0:
        p_blanc = 0.0

    # Contraintes EDF : couleurs impossibles ce jour-la
    if "ROUGE" in impossible:
        p_rouge = 0.0
    if "BLANC" in impossible:
        p_blanc = 0.0
    if "BLEU" in impossible:
        p_bleu = 0.0

    # Normaliser pour que la somme = 1.0
    total = p_bleu + p_blanc + p_rouge
    if total <= 0:
        return (0.0, 0.0, 1.0)
    p_rouge = p_rouge / total
    p_blanc = p_blanc / total
    p_bleu = max(0.0, 1.0 - p_rouge - p_blanc)

    # Coherence : la couleur finale doit avoir la proba la plus haute.
    # Si un override (density, ML) a change la couleur, on ajuste en
    # transferant juste assez de masse vers la couleur choisie.
    if couleur_finale:
        probs = {"ROUGE": p_rouge, "BLANC": p_blanc, "BLEU": p_bleu}
        p_chosen = probs[couleur_finale]
        p_max = max(probs.values())
        if p_chosen < p_max:
            target = min(p_max + 0.05, 0.95)
            boost_needed = target - p_chosen
            others = {c: v for c, v in probs.items() if c != couleur_finale}
            others_total = sum(others.values())
            if others_total > 0:
                for c in others:
                    others[c] -= (others[c] / others_total) * boost_needed
                    others[c] = max(0.0, others[c])
            probs[couleur_finale] = target
            for c in others:
                probs[c] = others[c]
            # Renormaliser a 1.0
            t = sum(probs.values())
            if t > 0:
                p_rouge = probs["ROUGE"] / t
                p_blanc = probs["BLANC"] / t
                p_bleu = probs["BLEU"] / t

    p_rouge = round(p_rouge, 3)
    p_blanc = round(p_blanc, 3)
    p_bleu = round(max(0.0, 1.0 - p_rouge - p_blanc), 3)
    return (p_rouge, p_blanc, p_bleu)


# ================================================================
# RAISON HUMAINE
# ================================================================

def _build_raison_v2(temp_moy: float, temp_min: float,
                     gradient_score: float, cold_wave: float,
                     remaining: dict, target_date: date, d_left: int,
                     rte_score: dict | None, cluster_score: float,
                     forecast_quality: str = "api",
                     pressure: float | None = None,
                     vigilance: dict | None = None,
                     vigilance_bonus: float = 0.0) -> str:
    """Construit une explication humaine de la prediction v2.2."""
    raisons = []

    # Vigilance Meteo France (prioritaire si active)
    if vigilance_bonus >= 20:
        raisons.append("Vigilance grand froid Meteo France")
    elif vigilance_bonus >= 10:
        raisons.append("Vigilance neige-verglas Meteo France")

    # Temperature nationale
    if temp_moy < -2:
        raisons.append(f"Grand froid national ({temp_moy:.0f}C moy.)")
    elif temp_moy < 2:
        raisons.append(f"Froid national ({temp_moy:.0f}C moy.)")
    elif temp_moy < 5:
        raisons.append(f"Frais ({temp_moy:.0f}C moy.)")

    # Pression atmospherique (Phase 2)
    if pressure is not None and pressure >= 1025 and temp_moy < 5:
        raisons.append(f"Anticyclone hivernal ({pressure:.0f} hPa)")

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
        raisons.append("Jour f\u00e9ri\u00e9")
    elif target_date.weekday() == 6:
        raisons.append("Dimanche")
    elif target_date.weekday() == 5:
        raisons.append("Samedi")

    # Budget rouge — deadline 31 mars (R1), pas fin de saison
    if remaining["ROUGE"] > 0 and (target_date.month >= 11 or target_date.month <= 3):
        if target_date.month <= 3:
            red_deadline = date(target_date.year, 3, 31)
        else:
            red_deadline = date(target_date.year + 1, 3, 31)
        red_d_left = max(0, (red_deadline - target_date).days)
        if remaining["ROUGE"] <= 5 and target_date.month >= 2:
            raisons.append(f"{remaining['ROUGE']}j rouges restants ({red_d_left}j avant fin mars)")
        if red_d_left < 20 and remaining["ROUGE"] > 2:
            raisons.append("Forte pression quota rouge")

    return " · ".join(raisons) if raisons else "Conditions normales"


# ================================================================
# CORRECTIONS JOURNAL D'APPRENTISSAGE
# ================================================================

def _apply_factor_correction(sub_score: float, factor_corrections: dict,
                              factor_name: str) -> float:
    """Applique une correction d'apprentissage à un sub-score individuel.

    Les corrections 'over' et 'under' pour ce facteur sont combinées.
    Plafonnées à [-10, +10] points par facteur.
    """
    adj = 0.0
    adj += factor_corrections.get(f"{factor_name}:over", 0)
    adj += factor_corrections.get(f"{factor_name}:under", 0)
    adj = max(-10.0, min(10.0, adj))
    return max(0, min(100, sub_score + adj))


MOIS_KEYS = {
    9: "sept", 10: "oct", 11: "nov", 12: "dec",
    1: "jan", 2: "fev", 3: "mars", 4: "avr", 5: "mai",
}


def _apply_learning_corrections(score: float, target_date: date,
                                horizon_days: int, temp_moy: float,
                                learnings: dict) -> float:
    """Applique les corrections contextuelles du journal d'apprentissage au score.

    Ajustements additifs bases sur les biais detectes par dimension :
    - horizon : degradation naturelle avec la distance
    - temp_range : biais dans certaines tranches de temperature
    - month : biais saisonnier par mois
    - weekday : biais semaine vs weekend
    - color_confusion : correction ciblee selon la couleur predite actuelle

    Note: les corrections par facteur (sub-scores) sont appliquées en amont
    dans predict_day via _apply_factor_correction().

    Total plafonne a [-15, +15] points pour eviter les corrections excessives.
    """
    total_adj = 0.0

    # 1. Correction par horizon
    horizon_key = f"J-{horizon_days}"
    total_adj += learnings.get("horizon", {}).get(horizon_key, 0)

    # 2. Correction par tranche de temperature
    temp_ranges = learnings.get("temp_range", {})
    if temp_ranges:
        if temp_moy < -2:
            tkey = "<-2C"
        elif temp_moy < 0:
            tkey = "-2_0C"
        elif temp_moy < 2:
            tkey = "0_2C"
        elif temp_moy < 5:
            tkey = "2_5C"
        elif temp_moy < 8:
            tkey = "5_8C"
        elif temp_moy < 12:
            tkey = "8_12C"
        else:
            tkey = ">12C"
        total_adj += temp_ranges.get(tkey, 0)

    # 3. Correction par mois
    month_key = MOIS_KEYS.get(target_date.month, "")
    total_adj += learnings.get("month", {}).get(month_key, 0)

    # 4. Correction semaine/weekend
    day_key = "weekend" if target_date.weekday() >= 5 else "semaine"
    total_adj += learnings.get("weekday", {}).get(day_key, 0)

    # 5. Correction ciblee color_confusion — s'applique en fonction du score actuel
    # Ex: si on est dans la zone ROUGE (score >= SEUIL_ROUGE) et qu'on a un biais
    # ROUGE->BLEU, appliquer la correction qui baisse le score
    # Fix audit ML #6 : facteur d'attenuation documente et parametrable
    # A 0.5 pour eviter les oscillations (une correction pleine pourrait inverser
    # la prediction, ce qui creerait le biais inverse au cycle suivant).
    CONFUSION_ATTENUATION = 0.5
    confusion_corrections = learnings.get("color_confusion", {})
    if confusion_corrections:
        if score >= Config.SEUIL_ROUGE:
            # On va prédire ROUGE — appliquer les corrections des confusions ROUGE->X
            for key, corr in confusion_corrections.items():
                if key.startswith("ROUGE->"):
                    total_adj += corr * CONFUSION_ATTENUATION
        elif score >= Config.SEUIL_BLANC:
            # On va prédire BLANC
            for key, corr in confusion_corrections.items():
                if key.startswith("BLANC->"):
                    total_adj += corr * CONFUSION_ATTENUATION

    # Plafonnement
    MAX_TOTAL = 15.0
    total_adj = max(-MAX_TOTAL, min(MAX_TOTAL, total_adj))

    return max(0, min(100, score + total_adj))


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
    BUG-01 QA : ne pas ecraser une prediction confirmee (confirmed=1) par une nouvelle prediction.
    """
    conn = get_db()
    change = None
    try:
        # Fix #31 + Fix #35 : vérifier si cette date est déjà confirmée.
        # On bloque TOUT nouvel horizon non-confirmé pour une date confirmée.
        # Raison : l'API fait GROUP BY date + MAX(id), donc un nouvel horizon
        # non-confirmé (ex: J-1 après un J-2 confirmé) prendrait le dessus
        # visuellement et afficherait l'ancienne prédiction au lieu du confirmé.
        # Les anciens horizons (J-2, J-3) restent en base pour l'évaluation.
        any_confirmed = conn.execute(
            "SELECT 1 FROM predictions WHERE date = ? AND confirmed = 1 LIMIT 1",
            (pred["date"],)
        ).fetchone()

        if any_confirmed and not pred.get("confirmed"):
            logger.debug(f"[Prediction] {pred['date']} {horizon} date déjà confirmée, skip")
            return None

        # Récupérer la prédiction précédente pour le même (date, horizon)
        # pour la détection de changements
        prev = conn.execute(
            "SELECT couleur_predite, score_risque, confirmed, couleur_originale "
            "FROM predictions WHERE date = ? AND horizon = ?",
            (pred["date"], horizon)
        ).fetchone()

        # Fix #34 : préserver couleur_originale lors des INSERT OR REPLACE.
        # Sans ça, _refresh_predictions() écrase couleur_originale → l'évaluation
        # de performance skip la prédiction → fiabilité artificiellement à 100%.
        preserved_originale = ""
        if prev:
            if prev["couleur_originale"]:
                preserved_originale = prev["couleur_originale"]
            elif not prev["confirmed"] and pred.get("confirmed"):
                # La prédiction va être confirmée : sauver la couleur prédite actuelle
                preserved_originale = prev["couleur_predite"]

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
                 cycle_id, _now_paris().isoformat()),
            )

        conn.execute(
            """INSERT OR REPLACE INTO predictions
               (date, couleur_predite, probabilite_bleu, probabilite_blanc,
                probabilite_rouge, score_risque, temp_min_prevue, temp_max_prevue,
                pression_prevue, jours_rouges_restants, jours_blancs_restants,
                raison, horizon, timestamp_prediction,
                score_temperature, score_budget, score_weekday,
                score_gradient, score_clustering, score_rte,
                score_temperature_raw, score_budget_raw, score_weekday_raw,
                score_gradient_raw, score_clustering_raw, score_rte_raw,
                cycle_id, couleur_precedente, simulated, confirmed,
                couleur_originale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (pred["date"], pred["couleur_predite"],
             pred["probabilite_bleu"], pred["probabilite_blanc"],
             pred["probabilite_rouge"], pred["score_risque"],
             pred.get("temp_min_prevue"), pred.get("temp_max_prevue"),
             pred.get("pression_prevue"),
             pred.get("jours_rouges_restants"), pred.get("jours_blancs_restants"),
             pred.get("raison", ""), horizon,
             _now_paris().isoformat(),
             pred.get("score_temperature", 0), pred.get("score_budget", 0),
             pred.get("score_weekday", 0), pred.get("score_gradient", 0),
             pred.get("score_clustering", 0), pred.get("score_rte", 0),
             pred.get("score_temperature_raw", 0), pred.get("score_budget_raw", 0),
             pred.get("score_weekday_raw", 0), pred.get("score_gradient_raw", 0),
             pred.get("score_clustering_raw", 0), pred.get("score_rte_raw", 0),
             cycle_id, couleur_precedente,
             1 if pred.get("simulated") else 0,
             1 if pred.get("confirmed") else 0,
             preserved_originale),
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


def confirm_prediction(date_str: str, couleur_officielle: str) -> int:
    """Met a jour toutes les predictions pour une date avec la couleur EDF officielle.

    Appelee par task_daily_verification (11h30) quand EDF confirme une couleur.
    Met a jour la table predictions pour que /api/predictions reflète immédiatement
    la couleur officielle au lieu de l'ancienne prediction.

    Retourne le nombre de lignes mises a jour.
    """
    p_r = 1.0 if couleur_officielle == "ROUGE" else 0.0
    p_b = 1.0 if couleur_officielle == "BLANC" else 0.0
    p_bl = 1.0 if couleur_officielle == "BLEU" else 0.0
    score = 100 if couleur_officielle == "ROUGE" else (50 if couleur_officielle == "BLANC" else 0)

    conn = get_db()
    try:
        # Sauvegarder la couleur originale avant écrasement (BUG-03 QA)
        conn.execute(
            """UPDATE predictions
               SET couleur_originale = couleur_predite
               WHERE date = ? AND confirmed = 0 AND couleur_originale = ''""",
            (date_str,),
        )
        cursor = conn.execute(
            """UPDATE predictions
               SET couleur_predite = ?,
                   probabilite_bleu = ?, probabilite_blanc = ?, probabilite_rouge = ?,
                   score_risque = ?,
                   confirmed = 1,
                   simulated = 0,
                   raison = 'Couleur officielle EDF'
               WHERE date = ? AND confirmed = 0""",
            (couleur_officielle, p_bl, p_b, p_r, score, date_str),
        )
        conn.commit()
        updated = cursor.rowcount
        if updated > 0:
            logger.info(
                f"[Prediction] {date_str} confirmé {couleur_officielle} "
                f"({updated} horizon(s) mis à jour)"
            )
        return updated
    finally:
        conn.close()
