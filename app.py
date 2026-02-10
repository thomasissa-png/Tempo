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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
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

# === Fix #25 : Signal de disponibilité DB pour Cloud Run health checks ===
_db_ready = asyncio.Event()


def invalidate_predictions_cache():
    """Invalide le cache mémoire des prédictions.

    Appelée par le scheduler après confirmation EDF (11h30) ou
    après génération de nouvelles prédictions (18h00) pour que
    les visiteurs voient immédiatement les données à jour.
    """
    _predictions_cache["data"] = None
    _predictions_cache["expires"] = 0

# === Fix #16 : Rate limiting simple pour /api/subscribe ===
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = asyncio.Lock()

_RATE_LIMIT_MAX_ENTRIES = 1000
_RATE_LIMIT_PURGE_AGE = 3600  # 1 hour in seconds

# Rate limiting pour les endpoints admin (protection brute-force)
_admin_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_ADMIN_RATE_LIMIT = 10  # max 10 tentatives
_ADMIN_RATE_WINDOW = 300  # par fenêtre de 5 minutes


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

async def _deferred_startup():
    """Tâches de démarrage en arrière-plan.

    Cloud Run exige que / réponde 200 immédiatement.
    init_db() tourne en thread pool pour ne pas bloquer le serveur ;
    les endpoints API attendent _db_ready avant d'accéder à la base.
    """
    # init_db() est synchrone → exécuter dans un thread pool
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, init_db)
        _db_ready.set()
        logger.info("[Startup] Base de données prête")
    except Exception as e:
        logger.error(f"[Startup] Erreur init_db: {e}")
        # Signaler quand même pour éviter le blocage infini des requêtes
        _db_ready.set()

    try:
        purge_old_data()
    except Exception as e:
        logger.error(f"[Startup] Erreur purge: {e}")

    start_scheduler()

    try:
        from tempo_client import backfill_season_actuals
        await backfill_season_actuals()
    except Exception as e:
        logger.error(f"[Startup] Erreur backfill actuals: {e}")

    # Fix #30 : recalcul des poids ML au démarrage si la migration v11 a
    # réinitialisé les poids (nettoyage apprentissage contaminé).
    # Cela permet au modèle de réapprendre immédiatement avec des données propres.
    try:
        from performance_tracker import recalculate_weights
        loop = asyncio.get_running_loop()
        new_weights = await loop.run_in_executor(None, recalculate_weights)
        if new_weights:
            logger.info("[Startup] Poids ML recalculés avec données propres")
        else:
            logger.info("[Startup] Recalcul poids: pas assez de données (normal au début)")
    except Exception as e:
        logger.error(f"[Startup] Erreur recalcul poids: {e}")

    # Fix #29 : recalculer les prédictions au démarrage pour appliquer
    # les nouvelles contraintes EDF (dimanche jamais blanc, rouge nov-mars, etc.)
    # et avoir des prédictions fraîches dès le lancement.
    try:
        from scheduler import _refresh_predictions
        count = await _refresh_predictions("startup", send_sms=False)
        if count:
            logger.info(f"[Startup] {count} prédictions recalculées")
    except Exception as e:
        logger.error(f"[Startup] Erreur recalcul prédictions: {e}")

    logger.info("[Startup] Tâches de fond terminées")


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

    # Fix #25 : tout en arrière-plan pour que Cloud Run reçoive 200 immédiatement
    # init_db() tourne en premier dans _deferred_startup (thread pool),
    # suivi du scheduler, purge, backfill.
    startup_task = asyncio.create_task(_deferred_startup())

    yield  # Serveur prêt immédiatement — "/" sert le HTML sans DB

    startup_task.cancel()
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


# === Fix #25 : middleware — attendre que la DB soit prête pour les endpoints API ===
@app.middleware("http")
async def wait_for_db(request: Request, call_next):
    """Les endpoints /api/ attendent que init_db() soit terminé.

    Les pages HTML (/, /admin, /health, /static) répondent immédiatement
    car elles ne dépendent pas de la base de données.
    """
    if request.url.path.startswith("/api/") or request.url.path == "/admin/run-task":
        if not _db_ready.is_set():
            try:
                await asyncio.wait_for(_db_ready.wait(), timeout=30)
            except asyncio.TimeoutError:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Service en cours de démarrage, réessayez."},
                )
    return await call_next(request)


# === Vérification admin (Fix #6 : via header Authorization) ===
def verify_admin(authorization: str | None, client_ip: str = "unknown"):
    """Vérifie le mot de passe admin depuis le header Authorization: Bearer <password>.
    Rate limiting inclus pour protéger contre le brute-force."""
    # Rate limiting admin
    now = time.time()
    _admin_rate_limit_store[client_ip] = [
        t for t in _admin_rate_limit_store[client_ip] if now - t < _ADMIN_RATE_WINDOW
    ]
    if len(_admin_rate_limit_store[client_ip]) >= _ADMIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Trop de tentatives admin. Réessayez plus tard.")

    if not authorization or not authorization.startswith("Bearer "):
        _admin_rate_limit_store[client_ip].append(now)
        raise HTTPException(status_code=403, detail="Header Authorization manquant")
    password = authorization[len("Bearer "):]
    # Fix #15 : constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(password, Config.ADMIN_PASSWORD):
        _admin_rate_limit_store[client_ip].append(now)
        raise HTTPException(status_code=403, detail="Mot de passe admin incorrect")


# === Fix #16 (CSRF) : origin check for POST endpoints ===
def _check_origin(request: Request) -> bool:
    """Return True if the request origin is acceptable (same host or non-browser client).

    Checks the Origin header first, then the Referer header.  If neither is
    present the request is assumed to come from a non-browser client (e.g. curl,
    mobile app) and is allowed through.

    M-11 QA : case-insensitive comparison, strip default ports.
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host", "").lower()

    # Non-browser clients typically send neither header — allow them.
    if not origin and not referer:
        return True

    def _normalize_netloc(netloc: str) -> str:
        """Normalize: lowercase, strip default ports."""
        netloc = netloc.lower()
        # Strip default ports
        if netloc.endswith(":443") or netloc.endswith(":80"):
            netloc = netloc.rsplit(":", 1)[0]
        return netloc

    # Strip default port from host header too
    host_normalized = _normalize_netloc(host)

    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        return _normalize_netloc(parsed.netloc) == host_normalized

    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        return _normalize_netloc(parsed.netloc) == host_normalized

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
    """Health check endpoint — répond 200 immédiatement (Cloud Run startup probe).

    Retourne le statut DB pour le monitoring, mais ne bloque pas le démarrage.
    """
    db_ok = False
    if _db_ready.is_set():
        try:
            from database import get_db
            conn = get_db()
            conn.execute("SELECT 1").fetchone()
            db_ok = True
            conn.close()
        except Exception:
            pass
    status = "ok" if db_ok else "starting"
    return {"status": status, "db": db_ok, "timestamp": datetime.now().isoformat()}


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
        return {"status": "unavailable", "message": "Pas encore annoncé par EDF. Détection automatique dès publication."}
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
                   WHERE date >= ? AND id IN (
                       SELECT MAX(id) FROM predictions WHERE date >= ? GROUP BY date
                   )
                   ORDER BY date ASC""",
                (today_str, today_str)
            ).fetchall()
        except Exception as e:
            logger.error(f"[API predictions] Erreur DB: {e}")
            rows = []
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
            _predictions_cache["expires"] = now + Config.PREDICTIONS_CACHE_TTL

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
                           "Les prédictions se mettront à jour automatiquement.",
            }

        rte_score = await get_consumption_score()
        predictions = predict_range(forecasts, rte_score=rte_score)

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
        _predictions_cache["expires"] = now + Config.PREDICTIONS_CACHE_TTL

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
async def api_performance(request: Request, authorization: str | None = Header(None)):
    """Métriques de performance complètes (admin, Fix #3)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
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
async def api_performance_csv(request: Request, month: int = None, year: int = None,
                               authorization: str | None = Header(None)):
    """Export CSV des performances mensuelles (admin only)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from performance_tracker import export_monthly_csv

    if not month:
        month = date.today().month
    if not year:
        year = date.today().year

    # M-06 QA : valider month et year
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Mois invalide (1-12)")
    if not (2020 <= year <= 2030):
        raise HTTPException(status_code=400, detail="Année invalide (2020-2030)")

    csv_content = export_monthly_csv(month, year)
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=performance_{year}_{month:02d}.csv"},
    )


@app.get("/api/learning")
async def api_learning(request: Request, authorization: str | None = Header(None)):
    """Journal d'apprentissage — patterns d'erreurs et corrections actives (admin)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from performance_tracker import get_learning_summary, get_active_learnings
    return {
        "status": "ok",
        "journal": get_learning_summary(),
        "active_corrections": get_active_learnings(),
    }


@app.get("/api/learning/health")
async def api_learning_health(request: Request, authorization: str | None = Header(None)):
    """Métriques de santé du système d'apprentissage (admin)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from performance_tracker import get_learning_health
    return {"status": "ok", **get_learning_health()}


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

    # M-05 QA : valider seuil_rouge et delai
    seuil_rouge = max(0, min(100, seuil_rouge))
    delai = max(1, min(3, delai))

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

    # H-06 QA : rate limiting sur la désinscription
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

    # H-06 QA : validation format téléphone
    phone_clean = phone.strip().replace(" ", "")
    if not phone_clean.startswith("+33") or len(phone_clean) != 12 or not phone_clean[3:].isdigit():
        raise HTTPException(status_code=400, detail="Format invalide. Utilisez +33XXXXXXXXX.")

    from alerts import unsubscribe_user
    result = unsubscribe_user(phone)
    if "error" in result:
        # H-07 QA : message générique (ne pas révéler si le numéro existe)
        raise HTTPException(status_code=400, detail="Désinscription impossible. Vérifiez votre numéro.")
    return result


@app.post("/api/sms/incoming")
async def api_sms_incoming(request: Request):
    """Webhook Twilio pour SMS entrants (STOP/START).

    Twilio envoie From, Body, etc. en POST form-data.
    Retourne du TwiML pour répondre automatiquement.
    BUG-01 QA : endpoint manquant pour l'opt-out par SMS.
    BUG-05 QA : vérification de la signature Twilio.
    """
    from alerts import handle_incoming_sms

    # BUG-05 QA : vérifier la signature Twilio si le token est configuré
    if Config.TWILIO_AUTH_TOKEN:
        try:
            from twilio.request_validator import RequestValidator
            validator = RequestValidator(Config.TWILIO_AUTH_TOKEN)
            signature = request.headers.get("X-Twilio-Signature", "")
            # Construire l'URL complète de la requête
            url = str(request.url)
            form_data = dict(await request.form())
            if not validator.validate(url, form_data, signature):
                logger.warning("[SMS IN] Signature Twilio invalide")
                raise HTTPException(status_code=403, detail="Invalid signature")
        except ImportError:
            # Module twilio non installé, skip la vérification
            pass
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[SMS IN] Erreur vérification signature: {e}")

    form = await request.form()
    from_number = form.get("From", "")
    body = form.get("Body", "")

    if not from_number or not body:
        raise HTTPException(status_code=400, detail="Missing From or Body")

    response_text = handle_incoming_sms(from_number, body)

    # Réponse TwiML pour que Twilio envoie un SMS de confirmation
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Message>{response_text}</Message>"
        "</Response>"
    )
    return PlainTextResponse(content=twiml, media_type="application/xml")


@app.get("/api/users/stats")
async def api_user_stats(request: Request, authorization: str | None = Header(None)):
    """Stats utilisateurs (admin)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
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
    client_ip = request.client.host if request.client else "unknown"
    verify_admin(request.headers.get("Authorization"), client_ip)
    from scheduler import run_task_now
    result = await run_task_now(task)
    return {"status": "ok", "result": result}


@app.get("/admin/weights-history")
async def admin_weights_history(request: Request, authorization: str | None = Header(None)):
    """Historique des versions de poids."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
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
async def admin_sms_logs(request: Request, authorization: str | None = Header(None), limit: int = 50):
    """Derniers SMS envoyés."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    # H-05 QA : valider le paramètre limit
    limit = max(1, min(limit, 500))
    from database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM sms_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"status": "ok", "logs": [dict(r) for r in rows]}
    finally:
        conn.close()
