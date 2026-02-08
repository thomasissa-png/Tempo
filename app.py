"""Application FastAPI principale — TempoForecast.

Endpoints :
  - GET  /                       → Dashboard principal (HTML)
  - GET  /admin                  → Dashboard performance (HTML)
  - GET  /mentions-legales       → Page légale (HTML)
  - GET  /api/predictions        → Prédictions J+1→J+15 (JSON)
  - GET  /api/today              → Couleur Tempo du jour (JSON)
  - GET  /api/tomorrow           → Couleur Tempo de demain (JSON)
  - GET  /api/remaining          → Jours restants par couleur (JSON)
  - GET  /api/performance        → Métriques de performance (JSON)
  - GET  /api/performance/csv    → Export CSV mensuel
  - POST /api/subscribe          → Inscription alertes SMS
  - POST /api/unsubscribe        → Désinscription alertes SMS
  - POST /admin/run-task         → Exécuter une tâche manuellement
"""

import asyncio
import hmac
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Config
from database import init_db
from scheduler import start_scheduler, stop_scheduler

# === Logging (Fix #13 : RotatingFileHandler) ===
os.makedirs("logs", exist_ok=True)
_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_file_handler = RotatingFileHandler(
    Config.LOG_FILE, maxBytes=Config.LOG_MAX_BYTES,
    backupCount=Config.LOG_BACKUP_COUNT, encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    handlers=[_file_handler, _stream_handler],
)
logger = logging.getLogger(__name__)

# === Fix #2 : Cache en mémoire pour /api/predictions ===
_predictions_cache = {"data": None, "expires": 0}
_predictions_lock = asyncio.Lock()

# === Fix #16 : Rate limiting simple pour /api/subscribe ===
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = asyncio.Lock()

_RATE_LIMIT_MAX_ENTRIES = 1000
_RATE_LIMIT_PURGE_AGE = 3600  # 1 hour in seconds


def _cleanup_rate_limit_store(now: float) -> None:
    """Fix #9 : purge stale rate-limit entries to prevent memory leak."""
    if len(_rate_limit_store) <= _RATE_LIMIT_MAX_ENTRIES:
        return
    cutoff = now - _RATE_LIMIT_PURGE_AGE
    stale_keys = [
        ip for ip, timestamps in _rate_limit_store.items()
        if not timestamps or timestamps[-1] < cutoff
    ]
    for key in stale_keys:
        del _rate_limit_store[key]


# === Fix #10 : Purge old data from the database ===
def purge_old_data() -> None:
    """Delete weather_cache entries older than 30 days and predictions older than 90 days."""
    from database import get_db

    conn = get_db()
    try:
        cutoff_cache = (date.today() - timedelta(days=30)).isoformat()
        cutoff_preds = (date.today() - timedelta(days=90)).isoformat()

        deleted_cache = conn.execute(
            "DELETE FROM weather_cache WHERE date < ?", (cutoff_cache,)
        ).rowcount
        deleted_preds = conn.execute(
            "DELETE FROM predictions WHERE date < ?", (cutoff_preds,)
        ).rowcount
        # Fix #15 audit v4 : purger aussi la table performance (>180 jours)
        cutoff_perf = (date.today() - timedelta(days=180)).isoformat()
        deleted_perf = conn.execute(
            "DELETE FROM performance WHERE date_cible < ?", (cutoff_perf,)
        ).rowcount
        conn.commit()

        logger.info(
            "purge_old_data: deleted %d weather_cache (>30d), %d predictions (>90d), "
            "%d performance (>180d)",
            deleted_cache, deleted_preds, deleted_perf,
        )
    except Exception:
        logger.exception("purge_old_data: error during purge")
    finally:
        conn.close()


# === Lifespan ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialisation au démarrage, nettoyage à l'arrêt."""
    logger.info("=== TempoForecast démarrage ===")

    # Fix audit v6 : avertir si le mot de passe admin n'est pas configuré
    if not Config.ADMIN_PASSWORD:
        import secrets
        Config.ADMIN_PASSWORD = secrets.token_urlsafe(24)
        logger.warning(
            "[SECURITE] ADMIN_PASSWORD non défini ! "
            "Un mot de passe aléatoire a été généré pour cette session : %s "
            "Définissez ADMIN_PASSWORD dans .env pour le conserver.",
            Config.ADMIN_PASSWORD,
        )

    init_db()
    purge_old_data()  # Fix #10 : clean stale DB rows on startup

    # Backfill : remplir les actuals manquants pour la saison en cours
    from tempo_client import backfill_season_actuals
    try:
        await backfill_season_actuals()
    except Exception as e:
        logger.error(f"[Startup] Erreur backfill actuals: {e}")

    start_scheduler()
    yield
    stop_scheduler()
    logger.info("=== TempoForecast arrêt ===")


# === App FastAPI ===
app = FastAPI(
    title="TempoForecast",
    description="Prévision des jours Tempo EDF avec alertes SMS",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# === Vérification admin (Fix #6 : via header Authorization) ===
def verify_admin(authorization: str | None):
    """Vérifie le mot de passe admin depuis le header Authorization: Bearer <password>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Header Authorization manquant")
    password = authorization[len("Bearer "):]
    # Fix #15 : constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(password, Config.ADMIN_PASSWORD):
        raise HTTPException(status_code=403, detail="Mot de passe admin incorrect")


# === Fix #16 (CSRF) : origin check for POST endpoints ===
def _check_origin(request: Request) -> bool:
    """Return True if the request origin is acceptable (same host or non-browser client).

    Checks the Origin header first, then the Referer header.  If neither is
    present the request is assumed to come from a non-browser client (e.g. curl,
    mobile app) and is allowed through.
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host", "")

    # Non-browser clients typically send neither header — allow them.
    if not origin and not referer:
        return True

    if origin:
        # Origin is like "https://example.com" — extract host part.
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        return parsed.netloc == host

    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        return parsed.netloc == host

    return False


# ================================================================
# PAGES HTML
# ================================================================

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    """Page principale — dashboard des prévisions."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    """Dashboard admin — performance et gestion.

    Fix #18 : simple deterrent — redirect to homepage if ``?auth=1`` query
    param is absent.  Real data protection is enforced via the Authorization
    header on every admin API endpoint.
    """
    if request.query_params.get("auth") != "1":
        return RedirectResponse(url="/")
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/mentions-legales", response_class=HTMLResponse)
async def page_legal(request: Request):
    """Page mentions légales et RGPD."""
    return templates.TemplateResponse("legal.html", {"request": request})


# Fix #S7 : robots.txt et sitemap.xml pour le SEO
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt pour les moteurs de recherche."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "\n"
        "Sitemap: https://tempoforecast.fr/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    """Sitemap XML dynamique."""
    today = date.today().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        "    <loc>https://tempoforecast.fr/</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://tempoforecast.fr/mentions-legales</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.3</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return PlainTextResponse(content=xml, media_type="application/xml")


# ================================================================
# Fix #24 : HEALTHCHECK
# ================================================================

@app.get("/health")
async def health():
    """Health check endpoint — returns OK status and current server timestamp."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ================================================================
# API : DONNÉES TEMPO
# ================================================================

@app.get("/api/today")
async def api_today():
    """Couleur Tempo du jour via l'API officielle."""
    from tempo_client import fetch_tempo_today
    data = await fetch_tempo_today()
    if not data:
        return {"status": "unavailable", "message": "Données non disponibles"}
    return {"status": "ok", **data}


@app.get("/api/tomorrow")
async def api_tomorrow():
    """Couleur Tempo de demain (disponible après 11h)."""
    from tempo_client import fetch_tempo_tomorrow
    data = await fetch_tempo_tomorrow()
    if not data:
        return {"status": "unavailable", "message": "Pas encore annoncé (disponible après 11h)"}
    return {"status": "ok", **data}


@app.get("/api/remaining")
async def api_remaining():
    """Jours restants par couleur pour la saison en cours."""
    from tempo_client import (get_remaining_days, days_left_in_season,
                              get_season_dates, get_blue_days_total,
                              fetch_edf_remaining, count_actuals_in_season)
    remaining = get_remaining_days()
    start, end = get_season_dates()
    actuals_count = count_actuals_in_season()

    result = {
        "status": "ok",
        "remaining": remaining,
        "totals": {
            "ROUGE": Config.JOURS_ROUGES_TOTAL,
            "BLANC": Config.JOURS_BLANCS_TOTAL,
            "BLEU": get_blue_days_total(),
        },
        "actuals_in_db": actuals_count,
        "days_left_in_season": days_left_in_season(),
        "season_start": start.isoformat(),
        "season_end": end.isoformat(),
    }

    # Ajouter les compteurs officiels EDF si disponibles
    edf = await fetch_edf_remaining()
    if edf:
        result["edf_official"] = edf

    return result


# ================================================================
# API : PRÉDICTIONS
# ================================================================

@app.get("/api/predictions")
async def api_predictions():
    """Retourne les prédictions J+1 → J+15 depuis la DB (source unique).

    Fix v5 #1/#2 : lit les prédictions stockées par le scheduler (18h)
    au lieu de recalculer à chaque visite. Tous les visiteurs voient
    la même chose. Cache mémoire court (5min) pour réduire les accès DB.

    Fallback : si aucune prédiction n'existe en DB (premier lancement),
    génère à la volée et stocke pour les visiteurs suivants.
    """
    now = time.time()

    # Cache mémoire court — évite les accès DB répétés
    if _predictions_cache["data"] and now < _predictions_cache["expires"]:
        return _predictions_cache["data"]

    # Fix audit v6 : asyncio.Lock pour éviter les race conditions
    # (deux requêtes simultanées pourraient remplir le cache en double)
    async with _predictions_lock:
        # Re-vérifier après acquisition du lock
        now = time.time()
        if _predictions_cache["data"] and now < _predictions_cache["expires"]:
            return _predictions_cache["data"]

        from database import get_db
        from performance_tracker import get_accuracy_global

        conn = get_db()
        try:
            today_str = date.today().isoformat()
            rows = conn.execute(
                """SELECT date, couleur_predite, probabilite_bleu, probabilite_blanc,
                          probabilite_rouge, score_risque, temp_min_prevue, temp_max_prevue,
                          pression_prevue, jours_rouges_restants, jours_blancs_restants,
                          raison, horizon, timestamp_prediction, cycle_id,
                          couleur_precedente, simulated, confirmed
                   FROM predictions
                   WHERE date >= ?
                   ORDER BY date ASC""",
                (today_str,)
            ).fetchall()
        finally:
            conn.close()

        if rows:
            predictions = []
            for r in rows:
                pred = {
                    "date": r["date"],
                    "couleur_predite": r["couleur_predite"],
                    "probabilite_bleu": r["probabilite_bleu"],
                    "probabilite_blanc": r["probabilite_blanc"],
                    "probabilite_rouge": r["probabilite_rouge"],
                    "score_risque": r["score_risque"],
                    "temp_min_prevue": r["temp_min_prevue"],
                    "temp_max_prevue": r["temp_max_prevue"],
                    "raison": r["raison"],
                    "horizon": r["horizon"],
                    "confirmed": bool(r["confirmed"]),
                    "simulated": bool(r["simulated"]),
                }
                if r["couleur_precedente"]:
                    pred["couleur_precedente"] = r["couleur_precedente"]
                predictions.append(pred)

            accuracy = get_accuracy_global(30)
            cycle_id = rows[0]["cycle_id"] if rows else ""
            generated_at = rows[0]["timestamp_prediction"] if rows else ""

            result = {
                "status": "ok",
                "predictions": predictions,
                "accuracy": accuracy,
                "generated_at": generated_at,
                "cycle_id": cycle_id,
            }

            _predictions_cache["data"] = result
            _predictions_cache["expires"] = now + 300

            return result

        # Fallback premier lancement : aucune prédiction en DB
        from weather_client import fetch_forecast_extended
        from predictor import predict_range, store_prediction
        from rte_client import get_consumption_score

        forecasts = await fetch_forecast_extended()
        if not forecasts:
            return {
                "status": "ok",
                "predictions": [],
                "accuracy": get_accuracy_global(30),
                "generated_at": datetime.now().isoformat(),
                "message": "Données météo temporairement indisponibles. "
                           "Les prédictions seront disponibles après le prochain cycle (18h).",
            }

        simulated = any(
            f.get("forecast_quality") == "simulated"
            or f.get("description", "") == "donnees simulees"
            for f in forecasts
        )
        rte_score = await get_consumption_score()
        predictions = predict_range(forecasts, rte_score=rte_score, simulated=simulated)

        cycle_id = f"{date.today().isoformat()}_init"
        for pred in predictions:
            store_prediction(pred, pred.get("horizon", "J-?"), cycle_id=cycle_id)

        accuracy = get_accuracy_global(30)
        result = {
            "status": "ok",
            "predictions": predictions,
            "accuracy": accuracy,
            "generated_at": datetime.now().isoformat(),
            "cycle_id": cycle_id,
        }

        _predictions_cache["data"] = result
        _predictions_cache["expires"] = now + 300

        return result


@app.get("/api/history")
async def api_history(days: int = 30):
    """Historique des couleurs reelles des N derniers jours.
    Fix #10 audit v4 : cap a 365 jours pour eviter scan complet."""
    days = max(1, min(days, 365))
    from database import get_db
    from datetime import timedelta

    conn = get_db()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ? ORDER BY date",
            (since,)
        ).fetchall()
        return {"status": "ok", "history": [dict(r) for r in rows]}
    finally:
        conn.close()


# ================================================================
# API : PERFORMANCE (badge + admin)
# ================================================================

@app.get("/api/performance")
async def api_performance(authorization: str | None = Header(None)):
    """Métriques de performance complètes (admin, Fix #3)."""
    verify_admin(authorization)
    from performance_tracker import get_performance_summary
    return {"status": "ok", **get_performance_summary()}


@app.get("/api/performance/badge")
async def api_performance_badge():
    """Badge de fiabilite simplifie pour la homepage.
    Fix #6 audit v4 : filtre J-1 seulement pour que le label soit exact."""
    from performance_tracker import get_accuracy_global
    acc = get_accuracy_global(30, max_horizon=1)
    return {
        "status": "ok",
        "precision_30j": acc["precision"],
        "total_predictions": acc["total"],
        "label": f"Nos prévisions J-1 : {acc['precision']}% de précision sur les 30 derniers jours"
                 if acc["total"] > 0
                 else "Pas encore assez de données pour calculer la précision",
    }


@app.get("/api/performance/csv")
async def api_performance_csv(month: int = None, year: int = None,
                               authorization: str | None = Header(None)):
    """Export CSV des performances mensuelles (admin only)."""
    verify_admin(authorization)
    from performance_tracker import export_monthly_csv

    if not month:
        month = date.today().month
    if not year:
        year = date.today().year

    csv_content = export_monthly_csv(month, year)
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=performance_{year}_{month:02d}.csv"},
    )


# ================================================================
# API : ALERTES SMS
# ================================================================

@app.post("/api/subscribe")
async def api_subscribe(
    request: Request,
    phone: str = Form(...),
    seuil_rouge: int = Form(70),
    delai: int = Form(1),
    alerte_blanc: bool = Form(False),
    recap_hebdo: bool = Form(False),
):
    """Inscription aux alertes SMS (Fix #16 : rate limiting + CSRF check)."""
    # Fix #16 (CSRF) : verify origin
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    # Fix audit v6 : asyncio.Lock pour le rate limiter
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = Config.SUBSCRIBE_RATE_WINDOW
    async with _rate_limit_lock:
        _rate_limit_store[client_ip] = [
            t for t in _rate_limit_store[client_ip] if now - t < window
        ]
        if len(_rate_limit_store[client_ip]) >= Config.SUBSCRIBE_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez plus tard.")
        _rate_limit_store[client_ip].append(now)
        _cleanup_rate_limit_store(now)

    from alerts import register_user
    result = register_user(phone, seuil_rouge, delai, alerte_blanc, recap_hebdo)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/unsubscribe")
async def api_unsubscribe(request: Request, phone: str = Form(...)):
    """Désinscription des alertes SMS."""
    # Fix #16 (CSRF) : verify origin
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    from alerts import unsubscribe_user
    result = unsubscribe_user(phone)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/users/stats")
async def api_user_stats(authorization: str | None = Header(None)):
    """Stats utilisateurs (admin)."""
    verify_admin(authorization)
    from alerts import get_user_count
    return {"status": "ok", **get_user_count()}


# ================================================================
# ADMIN : EXÉCUTION MANUELLE DES TÂCHES
# ================================================================

@app.post("/admin/run-task")
async def admin_run_task(request: Request, task: str = Form(...)):
    """Execute une tache du scheduler manuellement.
    Fix #8 audit v4 : ajout check CSRF."""
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")
    verify_admin(request.headers.get("Authorization"))
    from scheduler import run_task_now
    result = await run_task_now(task)
    return {"status": "ok", "result": result}


@app.get("/admin/weights-history")
async def admin_weights_history(authorization: str | None = Header(None)):
    """Historique des versions de poids."""
    verify_admin(authorization)
    from database import get_db
    import json

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM weights_history ORDER BY id DESC LIMIT 20"
        ).fetchall()
        result = []
        for r in rows:
            entry = dict(r)
            entry["weights"] = json.loads(entry["weights_json"])
            del entry["weights_json"]
            result.append(entry)
        return {"status": "ok", "history": result}
    finally:
        conn.close()


@app.get("/admin/sms-logs")
async def admin_sms_logs(authorization: str | None = Header(None), limit: int = 50):
    """Derniers SMS envoyés."""
    verify_admin(authorization)
    from database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM sms_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"status": "ok", "logs": [dict(r) for r in rows]}
    finally:
        conn.close()
