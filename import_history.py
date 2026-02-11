#!/usr/bin/env python3
"""Import historique Tempo — couleurs EDF + températures Open-Meteo.

Usage (depuis le serveur ou en local, PAS depuis le sandbox Claude) :
    python import_history.py

Ce script :
  1. Parse les fichiers iCal (jourstempo.fr) pour extraire les couleurs Tempo
  2. Appelle l'API historique Open-Meteo pour les 9 villes pondérées
  3. Calcule la température nationale pondérée (identique au scoring live)
  4. Stocke tout en base (actuals historiques + weather_history)
  5. Lance un backtesting : predict_day() sur chaque date historique
     comparé à la couleur réelle → table performance remplie

Prérequis :
  - Les fichiers .ics doivent être à la racine du projet
  - httpx installé (pip install httpx)
  - La base tempo.db doit exister (lancer l'app une fois au préalable)

Durée estimée : ~2-3 minutes (appels Open-Meteo en batch par saison).
"""

import os
import sys
import re
import time
import logging
import asyncio
from datetime import date, datetime, timedelta

# Ajouter le dossier racine au path pour importer les modules du projet
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from database import get_db, init_db, get_current_weights

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ================================================================
# Saisons à importer (configurables)
# ================================================================

# Fichiers iCal attendus à la racine du projet
ICS_FILES = [
    "Jours Tempo période 2023-2024.ics",
    "Jours Tempo période 2024-2025.ics",
    "Jours Tempo période 2025-2026 en cours.ics",
]

# Villes et poids — identiques à config.py pour cohérence
CITIES = Config.WEATHER_CITIES

# Open-Meteo Historical API
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = "temperature_2m_max,temperature_2m_min,wind_speed_10m_max"


# ================================================================
# ÉTAPE 1 : Parser les fichiers iCal
# ================================================================

def parse_ics_file(filepath: str) -> dict[str, str]:
    """Parse un fichier iCal jourstempo.fr et retourne {date_iso: couleur}.

    Format attendu :
      SUMMARY:Tempo : Bleu|Blanc|Rouge
      DTSTART;TZID=Europe/Paris;VALUE=DATE:20230901
    """
    if not os.path.exists(filepath):
        logger.warning(f"Fichier introuvable : {filepath}")
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Découper en événements VEVENT
    events = content.split("BEGIN:VEVENT")
    result = {}

    color_map = {
        "bleu": "BLEU",
        "blanc": "BLANC",
        "rouge": "ROUGE",
    }

    for event in events[1:]:  # Le premier split est l'en-tête
        # Extraire la couleur depuis SUMMARY
        summary_match = re.search(r"SUMMARY:Tempo\s*:\s*(\w+)", event)
        if not summary_match:
            continue
        color_raw = summary_match.group(1).lower().strip()
        couleur = color_map.get(color_raw)
        if not couleur:
            logger.warning(f"Couleur inconnue dans iCal : {color_raw}")
            continue

        # Extraire la date depuis DTSTART
        date_match = re.search(r"DTSTART[^:]*:(\d{8})", event)
        if not date_match:
            continue
        date_str = date_match.group(1)
        date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        result[date_iso] = couleur

    return result


def load_all_ics() -> dict[str, str]:
    """Charge tous les fichiers iCal et fusionne les résultats."""
    all_colors = {}
    base_dir = os.path.dirname(os.path.abspath(__file__))

    for filename in ICS_FILES:
        filepath = os.path.join(base_dir, filename)
        colors = parse_ics_file(filepath)
        logger.info(f"  {filename}: {len(colors)} jours parsés "
                    f"(R={sum(1 for v in colors.values() if v == 'ROUGE')}, "
                    f"B={sum(1 for v in colors.values() if v == 'BLANC')}, "
                    f"BL={sum(1 for v in colors.values() if v == 'BLEU')})")
        all_colors.update(colors)

    return all_colors


# ================================================================
# ÉTAPE 2 : Stocker les couleurs historiques en DB
# ================================================================

def store_historical_actuals(colors: dict[str, str]) -> int:
    """Insère les couleurs historiques dans la table actuals.

    Utilise INSERT OR IGNORE pour ne pas écraser les données existantes
    (la saison en cours a déjà des actuals confirmés par l'API live).

    Les données historiques sont marquées synthetic=0 car elles sont
    des couleurs EDF réelles (source jourstempo.fr), contrairement aux
    actuals synthétiques créés par seed_from_remaining().
    """
    conn = get_db()
    try:
        inserted = 0
        now = datetime.now().isoformat()
        for date_str, couleur in sorted(colors.items()):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO actuals
                   (date, couleur_reelle, synthetic, timestamp_confirmation)
                   VALUES (?, ?, 0, ?)""",
                (date_str, couleur, now),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


# ================================================================
# ÉTAPE 3 : Fetch températures historiques Open-Meteo
# ================================================================

async def fetch_historical_weather(start_date: date, end_date: date) -> dict[str, dict]:
    """Fetch les températures historiques pour les 9 villes via Open-Meteo.

    Retourne {date_iso: {temp_min, temp_max, temp_moy, wind_speed}}
    avec les moyennes pondérées nationales.
    """
    import httpx

    city_data = {}

    for city in CITIES:
        params = {
            "latitude": city["lat"],
            "longitude": city["lon"],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": DAILY_VARS,
            "timezone": "Europe/Paris",
        }

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(ARCHIVE_API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    city_data[city["name"]] = {
                        "weight": city["weight"],
                        "daily": data.get("daily", {}),
                    }
                    logger.info(f"    {city['name']} OK "
                                f"({len(data.get('daily', {}).get('time', []))} jours)")
                    break
            except Exception as e:
                if attempt < 2:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"    {city['name']} erreur (retry {wait}s): {e}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"    {city['name']} ÉCHEC après 3 tentatives: {e}")

        # Pause entre les villes pour ne pas surcharger l'API
        await asyncio.sleep(0.3)

    if not city_data:
        logger.error("Aucune ville n'a répondu !")
        return {}

    # Fusionner en moyenne pondérée nationale
    return _merge_historical(city_data)


def _merge_historical(city_data: dict) -> dict[str, dict]:
    """Fusionne les données historiques de toutes les villes en moyennes pondérées."""
    # Collecter toutes les dates
    all_dates = set()
    for info in city_data.values():
        times = info["daily"].get("time", [])
        all_dates.update(times)

    result = {}
    for day_str in sorted(all_dates):
        temp_min_w = 0.0
        temp_max_w = 0.0
        wind_w = 0.0
        total_w = 0.0

        for city_name, info in city_data.items():
            daily = info["daily"]
            times = daily.get("time", [])
            if day_str not in times:
                continue
            idx = times.index(day_str)

            t_min_arr = daily.get("temperature_2m_min", [])
            t_max_arr = daily.get("temperature_2m_max", [])
            wind_arr = daily.get("wind_speed_10m_max", [])

            t_min = t_min_arr[idx] if idx < len(t_min_arr) and t_min_arr[idx] is not None else 5.0
            t_max = t_max_arr[idx] if idx < len(t_max_arr) and t_max_arr[idx] is not None else 10.0
            wind = wind_arr[idx] if idx < len(wind_arr) and wind_arr[idx] is not None else 10.0

            w = info["weight"]
            total_w += w
            temp_min_w += t_min * w
            temp_max_w += t_max * w
            wind_w += wind * w

        if total_w == 0:
            continue

        t_min = round(temp_min_w / total_w, 1)
        t_max = round(temp_max_w / total_w, 1)
        t_moy = round((t_min + t_max) / 2, 1)
        wind = round(wind_w / total_w, 1)

        result[day_str] = {
            "date": day_str,
            "temp_min": t_min,
            "temp_max": t_max,
            "temp_moy": t_moy,
            "wind_speed": wind,
            "humidity": 70.0,  # Estimée (Open-Meteo archive ne fournit pas l'humidité daily)
            "pressure": None,
            "description": "historique Open-Meteo",
        }

    return result


def store_historical_weather(weather_data: dict[str, dict]) -> int:
    """Stocke les températures historiques dans weather_cache.

    Utilise INSERT OR IGNORE pour ne pas écraser les données existantes.
    """
    conn = get_db()
    try:
        inserted = 0
        now = datetime.now().isoformat()
        for day_str, w in sorted(weather_data.items()):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO weather_cache
                   (date, temp_min, temp_max, temp_moy, pressure,
                    humidity, wind_speed, description, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (day_str, w["temp_min"], w["temp_max"], w["temp_moy"],
                 w.get("pressure"), w.get("humidity", 70),
                 w["wind_speed"], "historique Open-Meteo", now),
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


# ================================================================
# ÉTAPE 4 : Backtesting — predict_day() sur données historiques
# ================================================================

def run_backtest(colors: dict[str, str], weather_data: dict[str, dict]):
    """Pour chaque date historique, simule predict_day() et évalue la performance.

    Utilise les températures réelles (Open-Meteo archive) au lieu des prévisions,
    et compare avec la couleur EDF réelle.

    Stocke les résultats dans :
      - `performance` : évaluations pour analyze_error_patterns / get_accuracy
      - `predictions`  : avec raw sub-scores pour recalculate_weights (régression)
    """
    from predictor import predict_day, is_french_holiday
    from tempo_client import get_season_dates, is_in_season

    weights = get_current_weights()

    # Trier les dates chronologiquement
    sorted_dates = sorted(colors.keys())

    total = len(sorted_dates)
    correct_count = 0
    evaluated = 0
    predictions_stored = 0
    confusion = {"BLEU": {"BLEU": 0, "BLANC": 0, "ROUGE": 0},
                 "BLANC": {"BLEU": 0, "BLANC": 0, "ROUGE": 0},
                 "ROUGE": {"BLEU": 0, "BLANC": 0, "ROUGE": 0}}

    conn = get_db()
    now = datetime.now().isoformat()

    try:
        for i, date_str in enumerate(sorted_dates):
            couleur_reelle = colors[date_str]
            target = date.fromisoformat(date_str)

            # Skip si pas en saison
            if not is_in_season(target):
                continue

            # Skip si pas de données météo
            if date_str not in weather_data:
                continue

            w = weather_data[date_str]

            # Construire une mini-fenêtre de forecasts (J-2 à J+2) pour le gradient
            forecasts = []
            target_idx = 0
            for offset in range(-2, 3):
                d = target + timedelta(days=offset)
                d_str = d.isoformat()
                if d_str in weather_data:
                    forecasts.append(weather_data[d_str])
                    if offset == 0:
                        target_idx = len(forecasts) - 1

            # Simuler les quotas restants basés sur la position dans la saison
            remaining = _estimate_remaining(target, colors)

            # Construire le cache d'actuals récents (7 jours avant)
            actuals_cache = {}
            for offset in range(1, 8):
                d = target - timedelta(days=offset)
                d_str = d.isoformat()
                if d_str in colors:
                    actuals_cache[d_str] = colors[d_str]

            # Prédire
            pred = predict_day(
                target,
                weather=w,
                forecasts=forecasts,
                target_idx=target_idx,
                remaining=remaining,
                weights=weights,
                _actuals_cache=actuals_cache,
            )

            couleur_predite = pred["couleur_predite"]
            correct = 1 if couleur_predite == couleur_reelle else 0
            correct_count += correct
            evaluated += 1
            confusion[couleur_reelle][couleur_predite] += 1

            # --- Stocker la prédiction dans `predictions` avec raw sub-scores ---
            # Nécessaire pour que recalculate_weights() puisse entraîner la
            # régression logistique (JOIN predictions.raw_sub_scores + actuals)
            ts_prediction = (target - timedelta(days=1)).isoformat() + "T18:00:00"
            cursor = conn.execute(
                """INSERT OR IGNORE INTO predictions
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
                (date_str, pred["couleur_predite"],
                 pred["probabilite_bleu"], pred["probabilite_blanc"],
                 pred["probabilite_rouge"], pred["score_risque"],
                 w["temp_min"], w["temp_max"], None,
                 remaining.get("ROUGE", 0), remaining.get("BLANC", 0),
                 pred.get("raison", "backtest"), "J-1", ts_prediction,
                 pred.get("score_temperature", 0), pred.get("score_budget", 0),
                 pred.get("score_weekday", 0), pred.get("score_gradient", 0),
                 pred.get("score_clustering", 0), pred.get("score_rte", 0),
                 pred.get("score_temperature_raw", 0), pred.get("score_budget_raw", 0),
                 pred.get("score_weekday_raw", 0), pred.get("score_gradient_raw", 0),
                 pred.get("score_clustering_raw", 0), pred.get("score_rte_raw", 0),
                 "backtest", "", 0, 0, ""),
            )
            if cursor.rowcount > 0:
                predictions_stored += 1

            # --- Stocker dans performance ---
            date_prediction = (target - timedelta(days=1)).isoformat()
            score_predit = pred["score_risque"]
            seuil_reel = {"BLEU": 0, "BLANC": 50, "ROUGE": 100}.get(couleur_reelle, 50)
            ecart = abs(score_predit - seuil_reel)

            conn.execute(
                """INSERT OR IGNORE INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, score_risque_predit,
                    ecart_score, contexte_meteo, timestamp_evaluation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date_prediction, date_str, 1, correct,
                 couleur_predite, couleur_reelle, score_predit,
                 ecart, f"backtest temp_moy={w['temp_moy']}", now),
            )

            # Log progression tous les 100 jours
            if (i + 1) % 100 == 0:
                pct = round(correct_count / evaluated * 100, 1) if evaluated else 0
                logger.info(f"    Backtest {i+1}/{total} — précision courante: {pct}%")

        conn.commit()

    finally:
        conn.close()

    # Résumé
    if evaluated > 0:
        precision = round(correct_count / evaluated * 100, 1)
        logger.info(f"\n{'='*60}")
        logger.info(f"BACKTESTING TERMINÉ : {evaluated} jours évalués")
        logger.info(f"Précision globale : {precision}% ({correct_count}/{evaluated})")
        logger.info(f"Predictions stockées : {predictions_stored} (pour recalculate_weights)")
        logger.info(f"\nMatrice de confusion (réel → prédit) :")
        logger.info(f"            BLEU   BLANC  ROUGE")
        for real in ["BLEU", "BLANC", "ROUGE"]:
            row = confusion[real]
            logger.info(f"  {real:6s}  {row['BLEU']:5d}  {row['BLANC']:5d}  {row['ROUGE']:5d}")
        logger.info(f"{'='*60}\n")


def _estimate_remaining(target: date, colors: dict[str, str]) -> dict:
    """Estime les quotas restants à une date donnée dans l'historique.

    Compte les jours rouge/blanc déjà écoulés avant target_date
    dans la même saison et soustrait du total.
    """
    # Déterminer la saison
    if target.month >= 9:
        season_start = date(target.year, 9, 1)
    else:
        season_start = date(target.year - 1, 9, 1)

    used_rouge = 0
    used_blanc = 0
    current = season_start
    while current < target:
        d_str = current.isoformat()
        if d_str in colors:
            if colors[d_str] == "ROUGE":
                used_rouge += 1
            elif colors[d_str] == "BLANC":
                used_blanc += 1
        current += timedelta(days=1)

    return {
        "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used_rouge),
        "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used_blanc),
        "BLEU": 999,  # Pas de limite pratique sur les bleus
    }


# ================================================================
# ÉTAPE 5 : Calibration initiale
# ================================================================

def run_initial_calibration(total_days: int):
    """Lance analyze_error_patterns + recalculate_weights sur tout l'historique.

    En fonctionnement normal, analyze_error_patterns(days=90) ne regarde que
    les 90 derniers jours. Pour la calibration initiale après import historique,
    on utilise toute la profondeur disponible (~900 jours).

    De même, recalculate_weights() utilise toutes les prédictions disponibles
    (sans filtre temporel), donc il bénéficie automatiquement des predictions
    backtest stockées par run_backtest().
    """
    from performance_tracker import analyze_error_patterns, recalculate_weights

    # 1. Analyse des patterns d'erreurs sur tout l'historique
    #    total_days couvre les ~900 jours importés
    logger.info(f"  Analyse des patterns d'erreurs sur {total_days} jours...")
    patterns = analyze_error_patterns(days=total_days, force=True)
    if patterns:
        logger.info(f"  → {len(patterns)} patterns détectés :")
        for p in patterns:
            if abs(p["correction"]) >= 1.0:
                logger.info(f"    {p['type']}:{p['key']} → correction {p['correction']:+.1f} "
                            f"(n={p['sample_size']}, conf={p['confidence']:.2f})")
    else:
        logger.info("  → Aucun pattern significatif détecté")

    # 2. Recalcul des poids via régression logistique
    logger.info("  Recalcul des poids via régression logistique...")
    new_weights = recalculate_weights()
    if new_weights:
        logger.info(f"  → Nouveaux poids déployés : {new_weights}")
    else:
        logger.info("  → Poids inchangés (pas assez de données ou validation échouée)")


# ================================================================
# ORCHESTRATION PRINCIPALE
# ================================================================

async def main():
    """Exécute l'import complet : iCal → DB, Open-Meteo → DB, backtest."""
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("IMPORT HISTORIQUE TEMPO — Début")
    logger.info("=" * 60)

    # Initialiser la DB si besoin
    init_db()

    # --- Étape 1 : Parser les fichiers iCal ---
    logger.info("\n[1/4] Parsing des fichiers iCal...")
    colors = load_all_ics()
    if not colors:
        logger.error("Aucune couleur trouvée dans les fichiers iCal !")
        sys.exit(1)
    logger.info(f"  Total : {len(colors)} jours Tempo parsés")

    # --- Étape 2 : Stocker les couleurs en DB ---
    logger.info("\n[2/4] Stockage des couleurs historiques en DB...")
    inserted = store_historical_actuals(colors)
    logger.info(f"  {inserted} nouvelles entrées ajoutées à la table actuals")

    # --- Étape 3 : Fetch températures historiques ---
    logger.info("\n[3/4] Fetch des températures historiques Open-Meteo...")

    # Déterminer la plage de dates
    all_dates = sorted(colors.keys())
    start = date.fromisoformat(all_dates[0])
    end = date.fromisoformat(all_dates[-1])

    # Open-Meteo accepte des plages longues, mais on découpe par saison
    # pour la lisibilité des logs et la robustesse
    seasons = [
        (date(2023, 9, 1), date(2024, 5, 31), "2023-2024"),
        (date(2024, 9, 1), date(2025, 5, 31), "2024-2025"),
        (date(2025, 9, 1), date.today() - timedelta(days=1), "2025-2026"),
    ]

    all_weather = {}
    for s_start, s_end, label in seasons:
        # Ne pas demander des dates futures
        if s_end > date.today() - timedelta(days=1):
            s_end = date.today() - timedelta(days=1)
        if s_start > s_end:
            continue

        logger.info(f"  Saison {label} ({s_start} → {s_end})...")
        weather = await fetch_historical_weather(s_start, s_end)
        all_weather.update(weather)
        logger.info(f"  → {len(weather)} jours de météo récupérés")

        # Pause entre les saisons
        await asyncio.sleep(1)

    # Stocker en DB
    weather_inserted = store_historical_weather(all_weather)
    logger.info(f"  {weather_inserted} entrées météo stockées dans weather_cache")

    # --- Étape 4 : Backtesting ---
    logger.info("\n[4/5] Backtesting — predict_day() sur données historiques...")
    run_backtest(colors, all_weather)

    # --- Étape 5 : Calibration initiale (analyse patterns + recalcul poids) ---
    logger.info("\n[5/5] Calibration initiale sur l'historique complet...")
    run_initial_calibration(len(colors))

    elapsed = round(time.time() - start_time, 1)
    logger.info(f"\nImport terminé en {elapsed}s")
    logger.info("Le système d'apprentissage peut maintenant s'appuyer sur "
                f"{len(colors)} jours de données réelles.")


if __name__ == "__main__":
    asyncio.run(main())
