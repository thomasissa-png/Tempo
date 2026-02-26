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
from datetime import date, datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Paris")

# Signal que le backfill post-startup est terminé.
# Les endpoints qui génèrent des prédictions à la volée (fallback)
# doivent attendre ce signal pour que get_remaining_days() soit fiable.
_backfill_done = asyncio.Event()


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

    # 7h30 tous les jours — alertes matinales (users préférant le matin)
    scheduler.add_job(
        task_morning_alerts,
        CronTrigger(hour=7, minute=30, timezone="Europe/Paris"),
        id="morning_alerts",
        name="Alertes matinales 7h30",
        replace_existing=True,
    )

    # 18h00 tous les jours — nouvelles prédictions + alertes soir
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

    # Mardi 9h00 — Agent SEO autonome (publication blog saisonnière)
    # Tourne chaque mardi, la logique saisonnière est dans task_seo_agent()
    scheduler.add_job(
        task_seo_agent,
        CronTrigger(day_of_week="tue", hour=9, minute=0, timezone="Europe/Paris"),
        id="seo_agent_seasonal",
        name="Agent SEO blog saisonnier (mardi 9h)",
        replace_existing=True,
    )

    # Mercredi 10h00 — Agent Backlinks autonome (prospection netlinking)
    # Tourne chaque mercredi en saison active, même gate saisonnière que l'agent SEO
    scheduler.add_job(
        task_backlinks_agent,
        CronTrigger(day_of_week="wed", hour=10, minute=0, timezone="Europe/Paris"),
        id="backlinks_agent_weekly",
        name="Agent Backlinks netlinking (mercredi 10h)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Démarré avec 10 tâches planifiées")


def stop_scheduler():
    """Arrête proprement le scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("[Scheduler] Arrêté")


def schedule_post_startup():
    """Planifie les tâches réseau 90s après démarrage.

    Fix #42 : le startup ne fait plus d'appels API (provoquaient des 429
    et bloquaient le health check Replit). Les opérations réseau
    (backfill EDF, prédictions météo) sont différées ici.
    """
    from datetime import timedelta
    from apscheduler.triggers.date import DateTrigger

    run_at = datetime.now() + timedelta(seconds=90)
    scheduler.add_job(
        _task_post_startup,
        DateTrigger(run_date=run_at),
        id="post_startup",
        name="Post-startup : backfill + prédictions (différé 90s)",
        replace_existing=True,
    )
    logger.info(f"[Scheduler] Backfill + prédictions planifiés à {run_at.strftime('%H:%M:%S')}")


async def _task_post_startup():
    """Tâche one-shot exécutée ~90s après démarrage.

    Fix #42 : TOUTES les opérations lourdes sont ici, pas au startup.
    Le startup ne fait que init_db + purge + scheduler pour que le
    health check Replit passe immédiatement.

    Opérations (dans l'ordre) :
    1. Backfill des actuals EDF (potentiellement 100+ appels API)
    2. ML : évaluation rattrapage + analyse patterns + recalcul poids
    3. Recalcul des prédictions (9 appels météo + RTE)
    """
    loop = asyncio.get_running_loop()

    # 0. Auto-import si la DB production est vide (sync depuis db_dump.json)
    try:
        from db_sync import auto_import_if_empty
        sync_result = await loop.run_in_executor(None, auto_import_if_empty)
        if sync_result:
            logger.info(f"[Post-startup] DB sync: {sync_result}")
    except Exception as e:
        logger.warning(f"[Post-startup] DB sync ignoré: {e}")

    # 1. Backfill actuals EDF
    _backfill_ok = False
    try:
        from tempo_client import backfill_season_actuals
        await backfill_season_actuals()
        logger.info("[Post-startup] Backfill actuals terminé")
        _backfill_ok = True
    except Exception as e:
        logger.error(f"[Post-startup] Erreur backfill: {e}")
    finally:
        # Signaler que le backfill est terminé (même en erreur)
        # pour débloquer le fallback de /api/predictions
        _backfill_done.set()

    # S-2 : si le backfill a échoué, planifier un retry en background
    if not _backfill_ok:
        _schedule_backfill_retry()

    # 2. Recalcul ML complet (CPU/DB local)
    try:
        from performance_tracker import (
            recalculate_weights, analyze_error_patterns,
            evaluate_missed_days, get_history_depth_days
        )

        await loop.run_in_executor(None, lambda: evaluate_missed_days(lookback=30))

        history_days = await loop.run_in_executor(None, get_history_depth_days)

        patterns = await loop.run_in_executor(
            None, lambda: analyze_error_patterns(days=history_days, force=True)
        )
        if patterns:
            logger.info(f"[Post-startup] {len(patterns)} patterns détectés sur {history_days}j")

        new_weights = await loop.run_in_executor(None, recalculate_weights)
        if new_weights:
            logger.info("[Post-startup] Poids ML recalculés")
        else:
            logger.info("[Post-startup] Recalcul poids: pas assez de données")
    except Exception as e:
        logger.error(f"[Post-startup] Erreur recalcul ML: {e}")

    # 3. Prédictions fraîches (météo + RTE)
    try:
        count = await _refresh_predictions("startup", send_sms=False)
        if count:
            logger.info(f"[Post-startup] {count} prédictions recalculées")
    except Exception as e:
        logger.error(f"[Post-startup] Erreur prédictions: {e}")

    logger.info("[Post-startup] Toutes les tâches différées terminées")


def _schedule_backfill_retry():
    """S-2 : planifie un retry du backfill EDF 5 min plus tard.

    Le flag _backfill_done est déjà set (API non bloquée).
    Ce retry tente silencieusement de compléter les actuals manquants.
    """
    from apscheduler.triggers.date import DateTrigger

    run_at = datetime.now() + timedelta(minutes=5)
    scheduler.add_job(
        _task_backfill_retry,
        DateTrigger(run_date=run_at),
        id=f"backfill_retry_{date.today().isoformat()}",
        name="Retry backfill EDF (+5min)",
        replace_existing=True,
    )
    logger.info(f"[Scheduler] Retry backfill planifié à {run_at.strftime('%H:%M:%S')}")


async def _task_backfill_retry():
    """Retry silencieux du backfill EDF après échec au startup."""
    try:
        from tempo_client import backfill_season_actuals
        await backfill_season_actuals()
        logger.info("[Backfill-retry] Backfill actuals récupéré avec succès")
    except Exception as e:
        logger.error(f"[Backfill-retry] Échec du retry backfill: {e}")


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
    from weather_client import fetch_forecast_extended, fetch_vigilance
    from predictor import predict_range, store_prediction
    from rte_client import get_consumption_score
    from app import invalidate_predictions_cache

    cycle_id = f"{date.today().isoformat()}_{trigger}_{uuid.uuid4().hex[:8]}"

    # 1. Météo fraîche — avec retry en cas d'échec transitoire
    #    Si les deux sources (MF + Open-Meteo) échouent, on retente 2 fois
    #    avec un délai exponentiel pour gérer les pannes temporaires.
    forecasts = await fetch_forecast_extended()
    if not forecasts:
        for retry in range(1, 3):
            delay = 30 * retry  # 30s, 60s
            logger.warning(
                f"[{trigger}] Pas de données météo (tentative {retry}/2), "
                f"retry dans {delay}s"
            )
            await asyncio.sleep(delay)
            forecasts = await fetch_forecast_extended()
            if forecasts:
                logger.info(f"[{trigger}] Météo récupérée au retry {retry}")
                break
    if not forecasts:
        logger.error(f"[{trigger}] Pas de données météo après 3 tentatives, recalcul reporté")
        return 0

    # 2. Score RTE + Vigilance Météo France (en parallèle)
    #    + stockage des données RTE réalisées dans rte_daily pour le ML
    rte_score, vigilance = await asyncio.gather(
        get_consumption_score(),
        fetch_vigilance(),
    )
    await _store_rte_daily(rte_score)

    # 3. Prédictions (avec vigilance grand froid/neige-verglas)
    predictions = predict_range(forecasts, rte_score=rte_score, vigilance=vigilance)

    # 4. Stocker les previsions meteo dans weather_cache (audit ML fev 2026)
    # Le cache alimente l'analyse de performance (temperature reelle vs couleur)
    # et le learning journal. Sans ca, pressure=0 et temp_min=NULL dans les stats.
    _store_weather_cache(forecasts)

    # 5. Stocker predictions, détecter les changements
    changes = []
    for pred in predictions:
        horizon = pred.get("horizon", "J-?")
        change = store_prediction(pred, horizon, cycle_id=cycle_id)
        if change:
            changes.append(change)

        # Alertes SMS uniquement si demandé (cycle 18h → users "soir" uniquement)
        if send_sms and not pred.get("confirmed"):
            from alerts import send_alerts_for_prediction
            target = date.fromisoformat(pred["date"])
            delta = (target - date.today()).days
            if 1 <= delta <= 3 and pred["couleur_predite"] in ("ROUGE", "BLANC"):
                # Fix audit DB : run blocking SMS/sleep in thread pool
                await asyncio.to_thread(
                    send_alerts_for_prediction, target, pred, "soir"
                )

    # #6 : alerter les utilisateurs sur les changements de prédiction
    if changes:
        logger.info(f"[{trigger}] {len(changes)} changements: "
                    + ", ".join(f"{c['date']} {c['couleur_avant']}→{c['couleur_apres']}"
                                for c in changes))
        from alerts import send_change_alerts
        for c in changes:
            try:
                target = date.fromisoformat(c["date"])
                await asyncio.to_thread(
                    send_change_alerts, target,
                    c["couleur_avant"], c["couleur_apres"]
                )
            except Exception as e:
                logger.debug(f"[{trigger}] Erreur alerte changement: {e}")

    logger.info(f"[{trigger}] {len(predictions)} prédictions recalculées (cycle={cycle_id})")

    invalidate_predictions_cache()
    return len(predictions)


def _store_weather_cache(forecasts: list[dict]) -> None:
    """Stocke les previsions meteo dans weather_cache + weather_forecast_log.

    Audit ML fev 2026 : le weather_cache n'etait plus alimente apres la
    migration vers Meteo France, privant le learning journal de temperature
    reelle et de pression atmospherique (tout etait 0 ou NULL).

    Migration v18 : alimente aussi weather_forecast_log pour conserver
    l'historique des previsions par horizon (J+2..J+5). Permet de mesurer
    la degradation des previsions meteo et de faire des backtests realistes.
    """
    from database import get_db
    conn = get_db()
    try:
        now_iso = datetime.now().isoformat()
        today_str = date.today().isoformat()
        for f in forecasts:
            d = f.get("date")
            if not d:
                continue
            conn.execute(
                """INSERT INTO weather_cache
                   (date, temp_min, temp_max, temp_moy, pressure, humidity,
                    wind_speed, description, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       temp_min = excluded.temp_min,
                       temp_max = excluded.temp_max,
                       temp_moy = excluded.temp_moy,
                       pressure = excluded.pressure,
                       humidity = excluded.humidity,
                       wind_speed = excluded.wind_speed,
                       description = excluded.description,
                       fetched_at = excluded.fetched_at""",
                (d, f.get("temp_min"), f.get("temp_max"), f.get("temp_moy"),
                 f.get("pressure"), f.get("humidity"), f.get("wind_speed"),
                 f.get("source", "api"), now_iso),
            )

            # Historique par horizon (J+0..J+15) pour backtests
            try:
                target = date.fromisoformat(d)
                today = date.fromisoformat(today_str)
                horizon = (target - today).days
                if 0 <= horizon <= 15:
                    conn.execute(
                        """INSERT INTO weather_forecast_log
                           (target_date, forecast_date, horizon_days,
                            temp_min, temp_max, temp_moy,
                            pressure, humidity, wind_speed,
                            source, fetched_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(target_date, forecast_date) DO UPDATE SET
                               horizon_days = excluded.horizon_days,
                               temp_min = excluded.temp_min,
                               temp_max = excluded.temp_max,
                               temp_moy = excluded.temp_moy,
                               pressure = excluded.pressure,
                               humidity = excluded.humidity,
                               wind_speed = excluded.wind_speed,
                               source = excluded.source,
                               fetched_at = excluded.fetched_at""",
                        (d, today_str, horizon,
                         f.get("temp_min"), f.get("temp_max"), f.get("temp_moy"),
                         f.get("pressure"), f.get("humidity"), f.get("wind_speed"),
                         f.get("source", "api"), now_iso),
                    )
            except (ValueError, TypeError):
                pass  # date invalide, on skip le log

        conn.commit()
    except Exception as e:
        logger.debug(f"[Weather Cache] Erreur stockage: {e}")
    finally:
        conn.close()


async def _store_rte_daily(rte_score: dict | None) -> None:
    """Stocke les donnees RTE dans rte_daily pour alimenter les features ML lag.

    Deux sources :
    1. rte_score (prevision J+1) : stocke peak/mean pour demain
    2. fetch_realised_consumption (realise J-1) : stocke les donnees d'hier
    """
    from database import get_db

    # Source 1 : prevision J+1 (peak + mean de la prevision de consommation)
    if rte_score and rte_score.get("available"):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        conn = get_db()
        try:
            # Fix P0-2 audit : ne pas melanger prevision et realise via COALESCE
            # La prevision J+1 ne doit ecrire QUE prevision_j1_peak_mw,
            # en preservant les donnees realisees si elles existent deja
            conn.execute(
                """INSERT INTO rte_daily (date, prevision_j1_peak_mw)
                   VALUES (?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       prevision_j1_peak_mw = excluded.prevision_j1_peak_mw""",
                (tomorrow, rte_score.get("peak_mw")),
            )
            conn.commit()
        except Exception as e:
            # Fix P3-14 audit : erreur de stockage = WARNING, pas DEBUG
            logger.warning(f"[RTE Daily] Erreur stockage prevision: {e}")
        finally:
            conn.close()

    # Source 2 : consommation realisee de la veille
    try:
        from rte_client import fetch_realised_consumption
        realised = await fetch_realised_consumption()
        if realised:
            yesterday = realised["date"]
            conn = get_db()
            try:
                conn.execute(
                    """INSERT INTO rte_daily (date, conso_peak_mw, conso_mean_mw)
                       VALUES (?, ?, ?)
                       ON CONFLICT(date) DO UPDATE SET
                           conso_peak_mw = excluded.conso_peak_mw,
                           conso_mean_mw = excluded.conso_mean_mw""",
                    (yesterday, realised["conso_peak_mw"], realised["conso_mean_mw"]),
                )
                conn.commit()
                logger.info(f"[RTE Daily] Stocke {yesterday}: "
                            f"peak={realised['conso_peak_mw']}MW, mean={realised['conso_mean_mw']}MW")
            except Exception as e:
                logger.warning(f"[RTE Daily] Erreur stockage realise: {e}")
            finally:
                conn.close()
    except Exception as e:
        logger.warning(f"[RTE Daily] fetch_realised indisponible: {e}")


# ================================================================
# TÂCHE 0 : Polling réactif EDF (6h–11h15, toutes les 15 min)
# ================================================================

# Flags en mémoire pour éviter de re-confirmer en boucle le même jour
_edf_confirmed_today: str = ""
_edf_confirmed_tomorrow: str = ""
# Flag pour le refresh météo matinal (une fois par jour, même sans changement EDF)
_morning_refresh_done: str = ""


async def task_edf_polling():
    """Polling réactif — détecte la couleur EDF dès publication + refresh météo matinal.

    Fix #27 : EDF publie parfois la couleur du jour bien avant 11h.
    Ce job tourne toutes les 15 min entre 6h et 11h15 pour :
      1. Confirmer les couleurs EDF dès publication (J et J+1)
      2. Rafraîchir les prédictions J+2→J+15 avec météo fraîche du matin

    Le refresh météo se fait UNE FOIS par jour, dès que J+1 est confirmé
    (ou au premier passage après 9h si J+1 n'est pas encore disponible).
    Ceci garantit que les prédictions intègrent les dernières données météo
    même si la couleur EDF était déjà connue.
    """
    global _edf_confirmed_today, _edf_confirmed_tomorrow, _morning_refresh_done

    today_str = date.today().isoformat()

    # Reset des flags au changement de jour
    if _edf_confirmed_today and not _edf_confirmed_today.startswith(today_str):
        _edf_confirmed_today = ""
        _edf_confirmed_tomorrow = ""
        _morning_refresh_done = ""

    try:
        from tempo_client import fetch_tempo_today, fetch_tempo_tomorrow, store_actual
        from predictor import confirm_prediction
        from performance_tracker import evaluate_predictions_for_date
        from app import invalidate_predictions_cache

        predictions_updated = False

        # 1. Couleur du jour — pas encore confirmée aujourd'hui ?
        if not _edf_confirmed_today:
            today_data = await fetch_tempo_today()
            if today_data and today_data.get("couleur"):
                store_actual(today_data["date"], today_data["couleur"])
                # Évaluer avant confirmation (robustesse si scheduler 11h30 échoue)
                try:
                    evaluate_predictions_for_date(
                        date.fromisoformat(today_data["date"]),
                        today_data["couleur"]
                    )
                except Exception as e:
                    logger.debug(f"[Polling EDF] Évaluation aujourd'hui échouée: {e}")
                updated = confirm_prediction(today_data["date"], today_data["couleur"])
                if updated:
                    predictions_updated = True
                    invalidate_predictions_cache()
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
                # Évaluer avant confirmation (robustesse si scheduler 11h30 échoue)
                try:
                    evaluate_predictions_for_date(
                        date.fromisoformat(tomorrow_data["date"]),
                        tomorrow_data["couleur"]
                    )
                except Exception as e:
                    logger.debug(f"[Polling EDF] Évaluation demain échouée: {e}")
                updated = confirm_prediction(tomorrow_data["date"], tomorrow_data["couleur"])
                if updated:
                    predictions_updated = True
                    invalidate_predictions_cache()
                _edf_confirmed_tomorrow = f"{today_str}:{tomorrow_data['couleur']}"
                logger.info(
                    f"[Polling EDF] Demain confirmé : {tomorrow_data['couleur']} "
                    f"(détecté à {datetime.now().strftime('%H:%M')})"
                )

        # 3. Refresh météo matinal — une fois par jour
        # Déclenché quand : (a) une couleur EDF vient de changer, OU
        # (b) J+1 est confirmé et on n'a pas encore fait le refresh matinal,
        # OU (c) après 9h si toujours pas de refresh (MF a publié ses runs).
        now_hour = datetime.now().hour
        need_morning_refresh = (
            predictions_updated
            or (_edf_confirmed_tomorrow and not _morning_refresh_done)
            or (now_hour >= 9 and not _morning_refresh_done)
        )

        if need_morning_refresh:
            count = await _refresh_predictions("polling_edf", send_sms=False)
            if count:
                _morning_refresh_done = today_str
                logger.info(
                    f"[Polling EDF] Refresh matinal OK — {count} prédictions "
                    f"avec météo fraîche (à {datetime.now().strftime('%H:%M')})"
                )

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
            from app import invalidate_predictions_cache

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
                    # Fix #31 : invalider le cache immédiatement
                    invalidate_predictions_cache()

            # 2. Couleur de demain (annoncée à 11h par EDF)
            tomorrow_data = await fetch_tempo_tomorrow()
            if tomorrow_data:
                store_actual(tomorrow_data["date"], tomorrow_data["couleur"])
                logger.info(f"[Task 11h30] Demain: {tomorrow_data['couleur']}")

                # Évaluer les prédictions faites pour demain (J-2, J-3, etc.)
                # Dès que EDF confirme, on peut mesurer la qualité de nos anticipations
                tomorrow_date = date.fromisoformat(tomorrow_data["date"])
                evaluate_predictions_for_date(
                    tomorrow_date, tomorrow_data["couleur"]
                )

                # Mettre à jour les prédictions en DB avec la couleur officielle
                updated = confirm_prediction(tomorrow_data["date"], tomorrow_data["couleur"])
                if updated:
                    predictions_updated = True
                    invalidate_predictions_cache()

                # Envoyer alerte officielle si rouge ou blanc
                # Fix audit DB : run blocking SMS in thread pool
                await asyncio.to_thread(
                    send_official_alerts, tomorrow_date, tomorrow_data["couleur"]
                )

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
# TÂCHE 1b : Alertes matinales (7h30)
# ================================================================

async def task_morning_alerts():
    """7h30 — Rafraîchit les prédictions avec la météo du matin, puis envoie
    les alertes aux utilisateurs qui préfèrent recevoir le matin.

    La météo du run AROME 00h (disponible vers 5h-6h) est souvent plus
    fiable pour J+1 que le run 12h de la veille.
    """
    try:
        logger.info("[Task 7h30] Début alertes matinales")

        # 1. Rafraîchir les prédictions avec la météo du matin
        count = await _refresh_predictions("matin_7h30", send_sms=False)
        if not count:
            logger.warning("[Task 7h30] Pas de données météo, alertes matinales annulées")
            return

        # 2. Envoyer les alertes uniquement aux users "matin"
        from alerts import send_alerts_for_prediction
        from database import get_db

        conn = get_db()
        try:
            today_str = date.today().isoformat()
            predictions = conn.execute(
                """SELECT date, couleur_predite, probabilite_rouge,
                          probabilite_blanc, temp_min_prevue, confirmed
                   FROM predictions
                   WHERE date >= ? AND confirmed = 0
                   ORDER BY date""",
                (today_str,)
            ).fetchall()
        finally:
            conn.close()

        for pred in predictions:
            pred_dict = dict(pred)
            target = date.fromisoformat(pred_dict["date"])
            delta = (target - date.today()).days
            if 1 <= delta <= 3 and pred_dict["couleur_predite"] in ("ROUGE", "BLANC"):
                await asyncio.to_thread(
                    send_alerts_for_prediction, target, pred_dict, "matin"
                )

        logger.info(f"[Task 7h30] Alertes matinales terminées ({count} prédictions)")
    except Exception as e:
        logger.error(f"[Scheduler] task_morning_alerts failed: {e}")


# ================================================================
# TÂCHE 2 : Prédictions quotidiennes (18h00)
# ================================================================

async def task_daily_predictions():
    """18h00 — Génère les prédictions J+1→J+15, envoie les alertes SMS.

    Fix #28 : utilise _refresh_predictions() partagée avec send_sms=True.
    C'est le seul cycle qui envoie les alertes SMS (prévenir la veille au soir).

    Si la météo est indisponible (MF + Open-Meteo down), planifie des
    retries différés à 10 min, 30 min et 60 min pour laisser les circuit
    breakers se réinitialiser et les APIs revenir.
    """
    for attempt in range(2):
        try:
            logger.info("[Task 18h00] Début génération des prédictions")
            count = await _refresh_predictions("18h", send_sms=True)
            if count:
                logger.info(f"[Task 18h00] Terminé — {count} prédictions")
                return
            # count == 0 : météo indisponible, planifier des retries différés
            logger.warning("[Task 18h00] Météo indisponible — retries différés planifiés")
            _schedule_deferred_retries()
            return
        except Exception as e:
            logger.error(f"[Scheduler] task_daily_predictions attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(30)
    logger.error("[Scheduler] task_daily_predictions failed after 2 attempts")
    _schedule_deferred_retries()


def _schedule_deferred_retries():
    """Planifie des retries one-shot pour récupérer les prédictions manquantes.

    Délais : 10 min, 30 min, 60 min, 120 min après maintenant.
    Les circuit breakers MF (2 min) et Open-Meteo (5 min) auront eu le temps
    de se réinitialiser. Chaque retry vérifie si des prédictions existent déjà
    pour aujourd'hui avant de relancer.

    S-1 : borne horaire — aucun retry après 21h (évite les SMS à des heures indues).
    Le 4e retry (+120 min) désactive les SMS (send_sms=False).
    """
    from apscheduler.triggers.date import DateTrigger

    now = datetime.now()
    scheduled = 0
    for delay_min in [10, 30, 60, 120]:
        run_at = now + timedelta(minutes=delay_min)
        # S-1 : ne pas planifier de retry après 21h
        if run_at.hour >= 21:
            logger.info(f"[Scheduler] Retry +{delay_min}min ignoré (après 21h)")
            continue
        job_id = f"deferred_retry_{date.today().isoformat()}_{delay_min}m"
        scheduler.add_job(
            _task_deferred_retry_no_sms if delay_min >= 120 else _task_deferred_retry,
            DateTrigger(run_date=run_at),
            id=job_id,
            name=f"Retry prédictions (+{delay_min}min)",
            replace_existing=True,
        )
        scheduled += 1
    logger.info(f"[Scheduler] {scheduled} retries différés planifiés")


async def _task_deferred_retry():
    """Retry one-shot : re-tente les prédictions si aucune n'a été générée.

    Vérifie d'abord si le cycle 18h (ou un retry précédent) a déjà réussi
    pour éviter les appels API inutiles.
    """
    from database import get_db

    # Vérifier si des prédictions fraîches existent déjà pour aujourd'hui
    conn = get_db()
    try:
        today_str = date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM predictions "
            "WHERE timestamp_prediction >= ? AND simulated = 0",
            (today_str,)
        ).fetchone()
        if row and row["c"] > 0:
            logger.info(f"[Retry] {row['c']} prédictions existent déjà, skip")
            return
    finally:
        conn.close()

    logger.info("[Retry] Aucune prédiction aujourd'hui — re-tentative météo")
    count = await _refresh_predictions("retry_deferred", send_sms=True)
    if count:
        logger.info(f"[Retry] Récupération réussie — {count} prédictions générées")
    else:
        logger.warning("[Retry] Météo toujours indisponible")


async def _task_deferred_retry_no_sms():
    """Retry tardif (S-1) : re-tente sans SMS pour ne pas déranger les utilisateurs."""
    from database import get_db

    conn = get_db()
    try:
        today_str = date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM predictions "
            "WHERE timestamp_prediction >= ? AND simulated = 0",
            (today_str,)
        ).fetchone()
        if row and row["c"] > 0:
            logger.info(f"[Retry-late] {row['c']} prédictions existent déjà, skip")
            return
    finally:
        conn.close()

    logger.info("[Retry-late] Aucune prédiction — re-tentative sans SMS")
    count = await _refresh_predictions("retry_late", send_sms=False)
    if count:
        logger.info(f"[Retry-late] Récupération réussie — {count} prédictions (sans SMS)")
    else:
        logger.warning("[Retry-late] Météo toujours indisponible après 4 tentatives")


# ================================================================
# TÂCHE 3 : Recalcul mensuel des poids (1er du mois)
# ================================================================

async def task_monthly_weights():
    """1er du mois — Recalcule les poids de l'algorithme via régression."""
    for attempt in range(2):
        try:
            from performance_tracker import recalculate_weights, get_accuracy_global
            from performance_tracker import analyze_error_patterns, evaluate_missed_days
            from performance_tracker import get_history_depth_days
            from alerts import cleanup_inactive_users

            logger.info("[Task mensuel] Début recalcul des poids")

            # Rattrapage + analyse avant recalcul
            evaluate_missed_days(lookback=30)

            # Fix audit ML #37 : analyser sur TOUT l'historique disponible
            history_days = get_history_depth_days()
            patterns = analyze_error_patterns(days=history_days)
            if patterns:
                logger.info(
                    f"[Task mensuel] {len(patterns)} patterns d'erreurs "
                    f"détectés sur {history_days}j d'historique"
                )

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
            from performance_tracker import analyze_error_patterns, get_history_depth_days

            logger.info("[Task hebdo] Début récap hebdomadaire")

            # Fix audit ML #38 : analyse sur tout l'historique (pas juste 90 jours)
            history_days = get_history_depth_days()
            patterns = analyze_error_patterns(days=history_days)
            if patterns:
                logger.info(f"[Task hebdo] {len(patterns)} patterns mis à jour ({history_days}j)")

            forecasts = await fetch_forecast_extended()
            if not forecasts:
                logger.warning("[Task hebdo] Pas de données météo")
                return

            rte_score = await get_consumption_score()
            predictions = predict_range(forecasts, rte_score=rte_score)
            # Fix audit DB : run blocking SMS in thread pool
            await asyncio.to_thread(send_weekly_recap, predictions)

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
# TÂCHE 6 : Agent SEO blog (mardi 9h00, fréquence saisonnière)
# ================================================================


def _should_publish_today(today_override: date | None = None) -> bool:
    """Détermine si l'agent SEO doit publier aujourd'hui selon le calendrier saisonnier.

    Fréquences (Config.SEO_SEASON_SCHEDULE) :
    - "weekly"    : chaque mardi
    - "bimonthly" : 1er et 3e mardi du mois
    - "monthly"   : 1er mardi du mois uniquement
    - "off"       : aucune publication

    Args:
        today_override: date à utiliser (pour les tests). Si None, utilise date.today().
    """
    from config import Config

    today = today_override or date.today()
    month = today.month
    schedule = Config.SEO_SEASON_SCHEDULE.get(month, "off")

    if schedule == "off":
        return False
    if schedule == "weekly":
        return True

    # Numéro du mardi dans le mois (1er mardi = 1, 2e = 2, etc.)
    week_of_month = (today.day - 1) // 7 + 1

    if schedule == "bimonthly":
        return week_of_month in (1, 3)
    if schedule == "monthly":
        return week_of_month == 1

    return False


async def task_seo_agent():
    """Mardi 9h — Exécute l'agent SEO selon le calendrier saisonnier.

    Fréquence adaptée au trafic Tempo :
    - Nov-Mar (saison active)  : hebdomadaire
    - Sep-Oct (pré-saison)     : bimensuel (1er et 3e mardi)
    - Avr-Mai (post-saison)    : mensuel (1er mardi)
    - Juin-Août (morte-saison) : pause complète

    Nécessite ANTHROPIC_API_KEY dans les variables d'environnement.
    Si la clé n'est pas configurée, la tâche est silencieusement ignorée.
    """
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.debug("[Agent SEO] ANTHROPIC_API_KEY non configurée, tâche ignorée")
        return

    if not _should_publish_today():
        from datetime import date
        from config import Config
        month = date.today().month
        schedule = Config.SEO_SEASON_SCHEDULE.get(month, "off")
        logger.info(
            f"[Agent SEO] Pas de publication aujourd'hui "
            f"(mois={month}, fréquence={schedule})"
        )
        return

    try:
        from seo_agent import run_seo_agent

        logger.info("[Agent SEO] Démarrage de la publication saisonnière")

        # L'agent est CPU/IO bound (appels API), on l'exécute dans un thread
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, run_seo_agent)

        if result["success"]:
            logger.info(
                f"[Agent SEO] Terminé en {result['turns']} tours. "
                f"Rapport : {result['report'][:500]}"
            )
        else:
            logger.error(f"[Agent SEO] Échec : {result['error']}")

    except Exception as e:
        logger.error(f"[Agent SEO] Erreur inattendue : {e}")


async def task_backlinks_agent():
    """Mercredi 10h — Exécute l'agent Backlinks pour la prospection netlinking.

    Fréquence adaptée au trafic Tempo (même gate saisonnière que l'agent SEO) :
    - Nov-Mar (saison active)  : hebdomadaire
    - Sep-Oct (pré-saison)     : bimensuel
    - Avr-Mai (post-saison)    : mensuel
    - Juin-Août (morte-saison) : pause complète

    Nécessite ANTHROPIC_API_KEY dans les variables d'environnement.
    Si la clé n'est pas configurée, la tâche est silencieusement ignorée.
    """
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.debug("[Agent Backlinks] ANTHROPIC_API_KEY non configurée, tâche ignorée")
        return

    if not _should_publish_today():
        from datetime import date
        from config import Config
        month = date.today().month
        schedule = Config.SEO_SEASON_SCHEDULE.get(month, "off")
        logger.info(
            f"[Agent Backlinks] Pas de prospection aujourd'hui "
            f"(mois={month}, fréquence={schedule})"
        )
        return

    try:
        from backlinks_agent import run_backlinks_agent

        logger.info("[Agent Backlinks] Démarrage de la prospection hebdomadaire")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, run_backlinks_agent)

        if result["success"]:
            logger.info(
                f"[Agent Backlinks] Terminé en {result['turns']} tours. "
                f"Rapport : {result['report'][:500]}"
            )
        else:
            logger.error(f"[Agent Backlinks] Échec : {result['error']}")

    except Exception as e:
        logger.error(f"[Agent Backlinks] Erreur inattendue : {e}")


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
        from performance_tracker import (
            analyze_error_patterns, evaluate_missed_days, get_history_depth_days
        )
        missed = evaluate_missed_days(lookback=30)
        # Fix audit ML #38 : analyse sur tout l'historique disponible
        history_days = get_history_depth_days()
        patterns = analyze_error_patterns(days=history_days)
        return (f"Analyse terminée : {missed} jours rattrapés, "
                f"{len(patterns)} patterns détectés sur {history_days}j")

    if task_name == "evaluate_missed":
        from performance_tracker import evaluate_missed_days
        loop = asyncio.get_running_loop()
        missed = await loop.run_in_executor(
            None, lambda: evaluate_missed_days(lookback=30))
        return f"Réévaluation terminée : {missed} jour(s) manquant(s) rattrapé(s) (lookback=30j)"

    if task_name == "db_export":
        from db_sync import export_to_file
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, export_to_file)

    if task_name == "db_import":
        from db_sync import import_from_file
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: import_from_file(force=False))

    if task_name == "db_import_force":
        from db_sync import import_from_file
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: import_from_file(force=True))

    if task_name == "reset_bad_backfill":
        from db_sync import reset_backfilled_temps
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, reset_backfilled_temps)

    # Agents SEO/Backlinks : exécution directe (bypass gate saisonnière)
    # avec remontée du vrai résultat/erreur à l'admin
    if task_name in ("seo_agent", "backlinks_agent"):
        import os
        if not os.getenv("ANTHROPIC_API_KEY"):
            try:
                from config import Config as _Cfg
                api_key = _Cfg.ANTHROPIC_API_KEY
            except Exception:
                api_key = ""
            if not api_key:
                return (
                    f"ERREUR : ANTHROPIC_API_KEY non configurée. "
                    f"Ajoutez-la dans Replit Secrets pour activer l'agent."
                )

        loop = asyncio.get_running_loop()
        if task_name == "seo_agent":
            from seo_agent import run_seo_agent
            result = await loop.run_in_executor(None, run_seo_agent)
        else:
            from backlinks_agent import run_backlinks_agent
            result = await loop.run_in_executor(None, run_backlinks_agent)

        if result["success"]:
            summary = result["report"][:300] if result["report"] else "OK"
            return f"Agent terminé en {result['turns']} tours. {summary}"
        else:
            return f"ERREUR agent : {result['error']}"

    tasks = {
        "verification": task_daily_verification,
        "predictions": task_daily_predictions,
        "morning_alerts": task_morning_alerts,
        "weights": task_monthly_weights,
        "recap": task_weekly_recap,
        "validation": task_daily_validation,
    }
    if task_name not in tasks:
        available = list(tasks.keys()) + [
            "backfill", "analyze", "evaluate_missed",
            "db_export", "db_import",
            "seo_agent", "backlinks_agent", "morning_alerts",
        ]
        return f"Tâche inconnue: {task_name}. Disponibles: {available}"

    await tasks[task_name]()
    return f"Tâche '{task_name}' exécutée avec succès"
