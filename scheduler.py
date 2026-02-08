"""Scheduler APScheduler — tâches automatisées TempoForecast.

Tâches planifiées :
  - 11h30 quotidien : vérification couleur EDF + évaluation performance
  - 18h00 quotidien : génération prédictions J+1→J+15 + alertes SMS
  - 1er du mois      : recalcul poids algorithme
  - Dimanche 20h     : récapitulatif hebdomadaire SMS
"""

import asyncio
import logging
from datetime import date, datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Paris")


def start_scheduler():
    """Démarre le scheduler avec toutes les tâches planifiées."""
    # 11h30 tous les jours — vérification + performance
    scheduler.add_job(
        task_daily_verification,
        CronTrigger(hour=11, minute=30, timezone="Europe/Paris"),
        id="daily_verification",
        name="Vérification quotidienne 11h30",
        replace_existing=True,
    )

    # 18h00 tous les jours — nouvelles prédictions + alertes
    scheduler.add_job(
        task_daily_predictions,
        CronTrigger(hour=18, minute=0, timezone="Europe/Paris"),
        id="daily_predictions",
        name="Prédictions quotidiennes 18h00",
        replace_existing=True,
    )

    # 1er du mois à 2h00 — recalcul des poids
    scheduler.add_job(
        task_monthly_weights,
        CronTrigger(day=1, hour=2, minute=0, timezone="Europe/Paris"),
        id="monthly_weights",
        name="Recalcul mensuel des poids",
        replace_existing=True,
    )

    # Dimanche 20h00 — récap hebdomadaire
    scheduler.add_job(
        task_weekly_recap,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone="Europe/Paris"),
        id="weekly_recap",
        name="Récap hebdomadaire dimanche 20h",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Démarré avec 4 tâches planifiées")


def stop_scheduler():
    """Arrête proprement le scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Arrêté")


# ================================================================
# TÂCHE 1 : Vérification quotidienne (11h30)
# ================================================================

async def task_daily_verification():
    """11h30 — Récupère la couleur EDF du jour, évalue les prédictions,
    envoie alertes officielles si rouge/blanc confirmé pour demain."""
    for attempt in range(2):
        try:
            from tempo_client import fetch_tempo_today, fetch_tempo_tomorrow, store_actual
            from performance_tracker import evaluate_predictions_for_date
            from alerts import send_official_alerts

            logger.info("[Task 11h30] Début vérification quotidienne")

            # 1. Couleur du jour (déjà en cours)
            today_data = await fetch_tempo_today()
            if today_data:
                store_actual(today_data["date"], today_data["couleur"])
                logger.info(f"[Task 11h30] Aujourd'hui: {today_data['couleur']}")

                # Évaluer les prédictions faites pour aujourd'hui
                evaluate_predictions_for_date(
                    date.fromisoformat(today_data["date"]),
                    today_data["couleur"]
                )

            # 2. Couleur de demain (annoncée à 11h par EDF)
            tomorrow_data = await fetch_tempo_tomorrow()
            if tomorrow_data:
                store_actual(tomorrow_data["date"], tomorrow_data["couleur"])
                logger.info(f"[Task 11h30] Demain: {tomorrow_data['couleur']}")

                # Envoyer alerte officielle si rouge ou blanc
                tomorrow_date = date.fromisoformat(tomorrow_data["date"])
                send_official_alerts(tomorrow_date, tomorrow_data["couleur"])

            logger.info("[Task 11h30] Vérification terminée")
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_daily_verification attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_daily_verification failed after 2 attempts")


# ================================================================
# TÂCHE 2 : Prédictions quotidiennes (18h00)
# ================================================================

async def task_daily_predictions():
    """18h00 — Génère les prédictions J+1→J+15, envoie les alertes SMS."""
    for attempt in range(2):
        try:
            from weather_client import fetch_forecast_extended, cache_weather
            from predictor import predict_range, store_prediction
            from rte_client import get_consumption_score
            from alerts import send_alerts_for_prediction

            logger.info("[Task 18h00] Début génération des prédictions")

            # 1. Récupérer la météo
            forecasts = await fetch_forecast_extended()
            if not forecasts:
                logger.warning("[Task 18h00] Pas de données météo, prédictions reportées")
                return

            cache_weather(forecasts)

            # 2. Générer les prédictions
            rte_score = await get_consumption_score()
            predictions = predict_range(forecasts, rte_score=rte_score)

            # 3. Stocker et envoyer les alertes
            for pred in predictions:
                horizon = pred.get("horizon", "J-?")
                store_prediction(pred, horizon)

                # Alertes SMS uniquement pour J-1 à J-3
                target = date.fromisoformat(pred["date"])
                delta = (target - date.today()).days
                if 1 <= delta <= 3 and pred["couleur_predite"] in ("ROUGE", "BLANC"):
                    send_alerts_for_prediction(target, pred)

            logger.info(f"[Task 18h00] {len(predictions)} prédictions générées et stockées")
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_daily_predictions attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_daily_predictions failed after 2 attempts")


# ================================================================
# TÂCHE 3 : Recalcul mensuel des poids (1er du mois)
# ================================================================

async def task_monthly_weights():
    """1er du mois — Recalcule les poids de l'algorithme via régression."""
    for attempt in range(2):
        try:
            from performance_tracker import recalculate_weights, get_accuracy_global
            from alerts import cleanup_inactive_users

            logger.info("[Task mensuel] Début recalcul des poids")

            new_weights = recalculate_weights()
            if new_weights:
                logger.info(f"[Task mensuel] Nouveaux poids : {new_weights}")
            else:
                logger.info("[Task mensuel] Recalcul reporté (pas assez de données)")

            # Nettoyage RGPD des users inactifs
            cleanup_inactive_users(months=6)

            logger.info("[Task mensuel] Tâches mensuelles terminées")
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_monthly_weights attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_monthly_weights failed after 2 attempts")


# ================================================================
# TÂCHE 4 : Récap hebdomadaire (dimanche 20h)
# ================================================================

async def task_weekly_recap():
    """Dimanche 20h — Envoie le récap de la semaine à venir."""
    for attempt in range(2):
        try:
            from weather_client import fetch_forecast_extended
            from predictor import predict_range
            from rte_client import get_consumption_score
            from alerts import send_weekly_recap

            logger.info("[Task hebdo] Début récap hebdomadaire")

            forecasts = await fetch_forecast_extended()
            if not forecasts:
                logger.warning("[Task hebdo] Pas de données météo")
                return

            rte_score = await get_consumption_score()
            predictions = predict_range(forecasts, rte_score=rte_score)
            send_weekly_recap(predictions)

            logger.info("[Task hebdo] Récap envoyé")
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_weekly_recap attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_weekly_recap failed after 2 attempts")


# ================================================================
# EXÉCUTION MANUELLE (pour tests / API admin)
# ================================================================

async def run_task_now(task_name: str) -> str:
    """Exécute une tâche manuellement."""
    tasks = {
        "verification": task_daily_verification,
        "predictions": task_daily_predictions,
        "weights": task_monthly_weights,
        "recap": task_weekly_recap,
    }
    if task_name not in tasks:
        return f"Tâche inconnue: {task_name}. Disponibles: {list(tasks.keys())}"

    await tasks[task_name]()
    return f"Tâche '{task_name}' exécutée avec succès"
