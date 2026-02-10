"""Scheduler APScheduler — tâches automatisées TempoForecast.

Tâches planifiées :
  - 11h30 quotidien : vérification couleur EDF + évaluation performance + rattrapage
  - 18h00 quotidien : génération prédictions J+1→J+15 + alertes SMS
  - 1er et 15 du mois : recalcul poids algorithme + analyse patterns d'erreurs (W-1)
  - Dimanche 20h     : analyse patterns + récapitulatif hebdomadaire SMS
  - Quotidien 23h    : validation corrections + kill-switch (A-1/C-3)
"""

import asyncio
import logging
import uuid
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

    # 1er et 15 du mois à 2h00 — recalcul des poids (W-1 : bimensuel)
    scheduler.add_job(
        task_monthly_weights,
        CronTrigger(day="1,15", hour=2, minute=0, timezone="Europe/Paris"),
        id="bimonthly_weights",
        name="Recalcul bimensuel des poids",
        replace_existing=True,
    )

    # 23h00 quotidien — validation des corrections + kill-switch (A-1/C-3)
    scheduler.add_job(
        task_daily_validation,
        CronTrigger(hour=23, minute=0, timezone="Europe/Paris"),
        id="daily_validation",
        name="Validation quotidienne des corrections",
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

    # Fix #27 : polling réactif EDF — toutes les 15 min entre 6h et 11h15
    # Détecte la couleur EDF dès publication (parfois avant 11h) et met à jour
    # immédiatement les prédictions. La tâche 11h30 reste en filet de sécurité.
    scheduler.add_job(
        task_edf_polling,
        CronTrigger(hour="6-11", minute="*/15", timezone="Europe/Paris"),
        id="edf_polling",
        name="Polling réactif couleur EDF (6h-11h15)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Démarré avec 6 tâches planifiées")


def stop_scheduler():
    """Arrête proprement le scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Arrêté")


# ================================================================
# HELPER : recalcul des prédictions (partagé polling / 11h30 / 18h)
# ================================================================

async def _refresh_predictions(trigger: str, send_sms: bool = False) -> int:
    """Recalcule les prédictions J+1→J+15 avec météo et RTE frais.

    Fix #28 : fonction partagée pour que le polling, la tâche 11h30
    et la tâche 18h utilisent la même logique de recalcul.

    Args:
        trigger: identifiant du déclencheur (pour cycle_id et logs)
        send_sms: si True, envoie les alertes SMS (uniquement cycle 18h)

    Returns:
        Nombre de prédictions générées, ou 0 si météo indisponible.
    """
    from weather_client import fetch_forecast_extended
    from predictor import predict_range, store_prediction
    from rte_client import get_consumption_score
    from app import invalidate_predictions_cache

    cycle_id = f"{date.today().isoformat()}_{trigger}_{uuid.uuid4().hex[:8]}"

    # 1. Météo fraîche
    forecasts = await fetch_forecast_extended()
    if not forecasts:
        logger.warning(f"[{trigger}] Pas de données météo, recalcul reporté")
        return 0

    # 2. Score RTE
    rte_score = await get_consumption_score()

    # 3. Prédictions
    predictions = predict_range(forecasts, rte_score=rte_score)

    # 4. Stocker, détecter les changements
    changes = []
    for pred in predictions:
        horizon = pred.get("horizon", "J-?")
        change = store_prediction(pred, horizon, cycle_id=cycle_id)
        if change:
            changes.append(change)

        # Alertes SMS uniquement si demandé (cycle 18h)
        if send_sms and not pred.get("confirmed"):
            from alerts import send_alerts_for_prediction
            target = date.fromisoformat(pred["date"])
            delta = (target - date.today()).days
            if 1 <= delta <= 3 and pred["couleur_predite"] in ("ROUGE", "BLANC"):
                send_alerts_for_prediction(target, pred)

    if changes:
        logger.info(f"[{trigger}] {len(changes)} changements: "
                    + ", ".join(f"{c['date']} {c['couleur_avant']}→{c['couleur_apres']}"
                                for c in changes))

    logger.info(f"[{trigger}] {len(predictions)} prédictions recalculées (cycle={cycle_id})")

    invalidate_predictions_cache()
    return len(predictions)


# ================================================================
# TÂCHE 0 : Polling réactif EDF (6h–11h15, toutes les 15 min)
# ================================================================

# Flag en mémoire pour éviter de re-confirmer en boucle le même jour
_edf_confirmed_today: str = ""
_edf_confirmed_tomorrow: str = ""


async def task_edf_polling():
    """Polling réactif — détecte la couleur EDF dès publication.

    Fix #27 : EDF publie parfois la couleur du jour bien avant 11h.
    Ce job tourne toutes les 15 min entre 6h et 11h15 pour mettre
    à jour les prédictions dès que l'info est disponible.

    Utilise des flags mémoire pour ne confirmer qu'une fois par jour
    et éviter les appels DB / cache inutiles.
    """
    global _edf_confirmed_today, _edf_confirmed_tomorrow

    today_str = date.today().isoformat()

    # Reset des flags au changement de jour
    if _edf_confirmed_today and not _edf_confirmed_today.startswith(today_str):
        _edf_confirmed_today = ""
        _edf_confirmed_tomorrow = ""

    try:
        from tempo_client import fetch_tempo_today, fetch_tempo_tomorrow, store_actual
        from predictor import confirm_prediction

        predictions_updated = False

        # 1. Couleur du jour — pas encore confirmée aujourd'hui ?
        if not _edf_confirmed_today:
            today_data = await fetch_tempo_today()
            if today_data and today_data.get("couleur"):
                store_actual(today_data["date"], today_data["couleur"])
                updated = confirm_prediction(today_data["date"], today_data["couleur"])
                if updated:
                    predictions_updated = True
                _edf_confirmed_today = f"{today_str}:{today_data['couleur']}"
                logger.info(
                    f"[Polling EDF] Aujourd'hui confirmé : {today_data['couleur']} "
                    f"(détecté à {datetime.now().strftime('%H:%M')})"
                )

        # 2. Couleur de demain — pas encore confirmée ?
        if not _edf_confirmed_tomorrow:
            tomorrow_data = await fetch_tempo_tomorrow()
            if tomorrow_data and tomorrow_data.get("couleur"):
                store_actual(tomorrow_data["date"], tomorrow_data["couleur"])
                updated = confirm_prediction(tomorrow_data["date"], tomorrow_data["couleur"])
                if updated:
                    predictions_updated = True
                _edf_confirmed_tomorrow = f"{today_str}:{tomorrow_data['couleur']}"
                logger.info(
                    f"[Polling EDF] Demain confirmé : {tomorrow_data['couleur']} "
                    f"(détecté à {datetime.now().strftime('%H:%M')})"
                )

        # Fix #28 : recalculer les prédictions J+2→J+15 avec données fraîches
        # (quotas mis à jour, météo du matin, RTE actualisé)
        if predictions_updated:
            await _refresh_predictions("polling_edf", send_sms=False)

    except Exception as e:
        # Le polling est best-effort, on ne veut pas spammer les logs
        logger.debug(f"[Polling EDF] Erreur (retry dans 15min): {e}")


# ================================================================
# TÂCHE 1 : Vérification quotidienne (11h30)
# ================================================================

async def task_daily_verification():
    """11h30 — Récupère la couleur EDF du jour, évalue les prédictions,
    envoie alertes officielles si rouge/blanc confirmé pour demain.

    Fix: met à jour la table predictions + invalide le cache mémoire
    pour que /api/predictions reflète immédiatement les couleurs officielles.
    """
    for attempt in range(2):
        try:
            from tempo_client import fetch_tempo_today, fetch_tempo_tomorrow, store_actual
            from performance_tracker import evaluate_predictions_for_date, evaluate_missed_days
            from predictor import confirm_prediction
            from alerts import send_official_alerts

            logger.info("[Task 11h30] Début vérification quotidienne")

            predictions_updated = False

            # M-02 QA : récupérer les couleurs AVANT le rattrapage
            # (sinon evaluate_missed_days n'a pas les données du jour)
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

                # Mettre à jour les prédictions en DB avec la couleur officielle
                updated = confirm_prediction(today_data["date"], today_data["couleur"])
                if updated:
                    predictions_updated = True

            # 2. Couleur de demain (annoncée à 11h par EDF)
            tomorrow_data = await fetch_tempo_tomorrow()
            if tomorrow_data:
                store_actual(tomorrow_data["date"], tomorrow_data["couleur"])
                logger.info(f"[Task 11h30] Demain: {tomorrow_data['couleur']}")

                # Mettre à jour les prédictions en DB avec la couleur officielle
                updated = confirm_prediction(tomorrow_data["date"], tomorrow_data["couleur"])
                if updated:
                    predictions_updated = True

                # Envoyer alerte officielle si rouge ou blanc
                tomorrow_date = date.fromisoformat(tomorrow_data["date"])
                send_official_alerts(tomorrow_date, tomorrow_data["couleur"])

            # M-02 QA : rattrapage APRÈS avoir récupéré les couleurs du jour
            evaluate_missed_days(lookback=7)

            # Fix #28 : recalculer les prédictions avec quotas et météo à jour
            # (pas d'alertes SMS — c'est le rôle du cycle 18h)
            if predictions_updated:
                await _refresh_predictions("verif_11h30", send_sms=False)

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
    """18h00 — Génère les prédictions J+1→J+15, envoie les alertes SMS.

    Fix #28 : utilise _refresh_predictions() partagée avec send_sms=True.
    C'est le seul cycle qui envoie les alertes SMS (prévenir la veille au soir).
    """
    for attempt in range(2):
        try:
            logger.info("[Task 18h00] Début génération des prédictions")
            count = await _refresh_predictions("18h", send_sms=True)
            if count:
                logger.info(f"[Task 18h00] Terminé — {count} prédictions")
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
            from performance_tracker import analyze_error_patterns, evaluate_missed_days
            from alerts import cleanup_inactive_users

            logger.info("[Task mensuel] Début recalcul des poids")

            # Rattrapage + analyse avant recalcul
            evaluate_missed_days(lookback=30)
            patterns = analyze_error_patterns(days=90)
            if patterns:
                logger.info(f"[Task mensuel] {len(patterns)} patterns d'erreurs détectés")

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
            from performance_tracker import analyze_error_patterns

            logger.info("[Task hebdo] Début récap hebdomadaire")

            # Analyse hebdo des patterns d'erreurs avant de générer le récap
            patterns = analyze_error_patterns(days=90)
            if patterns:
                logger.info(f"[Task hebdo] {len(patterns)} patterns d'erreurs mis à jour")

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
# TÂCHE 5 : Validation quotidienne des corrections (23h)
# ================================================================

async def task_daily_validation():
    """23h00 — Valide l'impact des corrections et désactive les nocives.

    A-1 : Compare précision avec vs sans corrections.
    C-3 : Kill-switch des corrections individuelles les plus nocives.
    """
    for attempt in range(2):
        try:
            from performance_tracker import validate_correction_impact
            from performance_tracker import killswitch_harmful_corrections

            logger.info("[Task 23h00] Début validation des corrections")

            # A-1 : Validation globale des corrections
            result = validate_correction_impact()
            if result:
                logger.info(f"[Task 23h00] Validation: {result}")

            # C-3 : Kill-switch des corrections individuelles nocives
            killed = killswitch_harmful_corrections()
            if killed:
                logger.warning(f"[Task 23h00] {len(killed)} correction(s) désactivée(s)")

            logger.info("[Task 23h00] Validation terminée")
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_daily_validation attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_daily_validation failed after 2 attempts")


# ================================================================
# EXÉCUTION MANUELLE (pour tests / API admin)
# ================================================================

async def run_task_now(task_name: str) -> str:
    """Exécute une tâche manuellement."""
    if task_name == "backfill":
        from tempo_client import backfill_season_actuals
        await backfill_season_actuals()
        return "Backfill des actuals de la saison terminé"

    if task_name == "analyze":
        from performance_tracker import analyze_error_patterns, evaluate_missed_days
        missed = evaluate_missed_days(lookback=30)
        patterns = analyze_error_patterns(days=90)
        return (f"Analyse terminée : {missed} jours rattrapés, "
                f"{len(patterns)} patterns détectés")

    tasks = {
        "verification": task_daily_verification,
        "predictions": task_daily_predictions,
        "weights": task_monthly_weights,
        "recap": task_weekly_recap,
        "validation": task_daily_validation,
    }
    if task_name not in tasks:
        available = list(tasks.keys()) + ["backfill", "analyze"]
        return f"Tâche inconnue: {task_name}. Disponibles: {available}"

    await tasks[task_name]()
    return f"Tâche '{task_name}' exécutée avec succès"
