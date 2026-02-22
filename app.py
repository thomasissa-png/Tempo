"""Application FastAPI principale — TempoForecast.

Endpoints :
  - GET  /                       → Dashboard principal (HTML)
  - GET  /admin                  → Dashboard performance (HTML)
  - GET  /mentions-legales       → Page légale (HTML)
  - GET  /manage/{token}         → Page de gestion des préférences (HTML)
  - GET  /api/predictions        → Prédictions J+1→J+15 (JSON)
  - GET  /api/today              → Couleur Tempo du jour (JSON)
  - GET  /api/tomorrow           → Couleur Tempo de demain (JSON)
  - GET  /api/remaining          → Jours restants par couleur (JSON)
  - GET  /api/performance        → Métriques de performance (JSON)
  - GET  /api/performance/csv    → Export CSV mensuel
  - POST /api/subscribe          → Inscription alertes WhatsApp
  - POST /api/unsubscribe        → Désinscription alertes WhatsApp
  - GET  /api/manage/{token}     → Récupérer les préférences (JSON)
  - POST /api/manage/{token}     → Mettre à jour les préférences (JSON)
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
from zoneinfo import ZoneInfo

_PARIS_TZ = ZoneInfo("Europe/Paris")

def _now_paris() -> datetime:
    """Retourne l'heure actuelle en timezone Paris (CET/CEST)."""
    return datetime.now(tz=_PARIS_TZ)

from fastapi import FastAPI, Request, Form, HTTPException, Header
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Config
from database import init_db

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

# === Cache en mémoire pour les endpoints EDF (today/tomorrow/remaining) ===
# EDF ne met à jour les couleurs que 1-2 fois/jour, pas besoin d'appeler
# l'API externe à chaque visiteur. Cache court (2 min) pour réactivité.
_edf_cache = {
    "today": {"data": None, "expires": 0},
    "tomorrow": {"data": None, "expires": 0},
    "remaining": {"data": None, "expires": 0},
}
_EDF_CACHE_TTL = 120  # 2 minutes

# === Fix #25 : Signal de disponibilité DB pour Cloud Run health checks ===
_db_ready = asyncio.Event()



def invalidate_predictions_cache():
    """Invalide le cache mémoire des prédictions ET des endpoints EDF.

    Appelée par le scheduler après confirmation EDF (polling/11h30) ou
    après génération de nouvelles prédictions (18h00) pour que
    les visiteurs voient immédiatement les données à jour.
    """
    _predictions_cache["data"] = None
    _predictions_cache["expires"] = 0
    # Invalider aussi les caches EDF pour que today/tomorrow/remaining
    # reflètent immédiatement les nouvelles couleurs confirmées
    for key in _edf_cache:
        _edf_cache[key]["data"] = None
        _edf_cache[key]["expires"] = 0

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
    """Purge les données obsolètes en préservant les données backtest historiques.

    Fix audit ML #38 : ne PAS supprimer les données backtest (cycle_id='backtest')
    ni les performances associées. Ces données alimentent recalculate_weights() et
    analyze_error_patterns() pour l'apprentissage sur 2+ saisons.
    """
    from database import get_db

    conn = get_db()
    try:
        # Fix : ne PAS purger weather_cache — les données météo historiques sont
        # essentielles pour le ML (features température), le backtest, et
        # la normalisation RTE (C_nette). Le volume est faible (~1 ligne/jour,
        # ~2000 lignes pour 6 saisons) donc aucun risque de croissance.
        deleted_cache = 0
        cutoff_preds = (date.today() - timedelta(days=90)).isoformat()
        # Fix audit ML #38 : préserver les predictions backtest (essentielles pour ML)
        # Seules les prédictions live > 90 jours sont purgées.
        deleted_preds = conn.execute(
            "DELETE FROM predictions WHERE date < ? AND cycle_id NOT LIKE 'backtest%'",
            (cutoff_preds,)
        ).rowcount
        # Fix audit ML #38 : ne plus purger la table performance.
        # Elle contient les évaluations backtest (2+ saisons) nécessaires à
        # analyze_error_patterns() et recalculate_weights().
        # Le volume est faible (~1 ligne/jour) donc pas de risque de croissance.
        deleted_perf = 0

        # Fix #32 : supprimer uniquement la prediction non-confirmee au MEME
        # horizon qu'une prediction confirmee (pas les autres horizons).
        # Les predictions J+2 a J+5 sont precieuses pour l'evaluation multi-horizon.
        deleted_orphans = conn.execute(
            """DELETE FROM predictions
               WHERE confirmed = 0
                 AND (date, horizon) IN (
                     SELECT date, horizon FROM predictions WHERE confirmed = 1
                 )"""
        ).rowcount

        conn.commit()

        logger.info(
            "purge_old_data: deleted %d weather_cache (>30d), %d predictions live (>90d), "
            "%d orphan predictions (backtest preserved)",
            deleted_cache, deleted_preds, deleted_orphans,
        )
    except Exception:
        logger.exception("purge_old_data: error during purge")
    finally:
        conn.close()


# === Lifespan ===

async def _deferred_startup():
    """Tâches de démarrage en arrière-plan — 100% non-bloquant.

    Fix #42 : AUCUN appel synchrone ne doit bloquer l'event loop.
    Chaque opération est soit dans run_in_executor, soit précédée
    d'un yield (asyncio.sleep) pour que le health check passe.

    TOUTES les opérations lourdes (ML, backfill, prédictions) sont
    déléguées au scheduler via schedule_post_startup (exécution à +90s).
    """
    loop = asyncio.get_running_loop()

    # 1. Init DB dans thread pool (non-bloquant)
    try:
        await loop.run_in_executor(None, init_db)
        _db_ready.set()
        logger.info("[Startup] Base de données prête")
    except Exception as e:
        logger.error(f"[Startup] Erreur init_db: {e}")
        _db_ready.set()

    # 2. Purge dans thread pool (non-bloquant)
    try:
        await loop.run_in_executor(None, purge_old_data)
    except Exception as e:
        logger.error(f"[Startup] Erreur purge: {e}")

    # 3. Import du module scheduler dans thread pool pour ne PAS bloquer
    #    l'event loop pendant le chargement d'APScheduler + pytz (~2-5s Replit)
    try:
        await loop.run_in_executor(None, lambda: __import__("scheduler"))
    except Exception as e:
        logger.error(f"[Startup] Erreur import scheduler: {e}")

    # 3b. Pré-charger le modèle ML dans thread pool (évite 2-5s de latence
    #     sur la première requête /api/predictions après un cold start)
    try:
        def _preload_ml():
            from ml_scorer import ml_score_available
            ml_score_available()
        await loop.run_in_executor(None, _preload_ml)
        logger.info("[Startup] Modèle ML pré-chargé")
    except Exception as e:
        logger.warning(f"[Startup] ML pré-chargement échoué (non critique): {e}")

    # Yield explicite : laisser l'event loop traiter les health checks en attente
    await asyncio.sleep(0)

    # 4. Démarrer le scheduler (rapide car module déjà importé en cache)
    #    start_scheduler() doit tourner dans le thread principal (AsyncIOScheduler)
    from scheduler import start_scheduler, schedule_post_startup
    start_scheduler()
    schedule_post_startup()

    logger.info("[Startup] Init terminée — tâches lourdes dans ~90s via scheduler")


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
    from scheduler import stop_scheduler
    stop_scheduler()
    logger.info("=== TempoForecast arrêt ===")


# === App FastAPI ===
app = FastAPI(
    title="TempoForecast",
    description="Prévision des jours Tempo EDF avec alertes WhatsApp",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)  # Compresse CSS/JS/JSON > 500 octets
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# === SEO : page 404 personnalisée (HTML au lieu de JSON brut) ===
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc):
    """Page 404 SEO-friendly avec navigation vers les pages principales."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404,
        )
    return JSONResponse(status_code=404, content={"detail": "Page non trouvée"})


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


# === Headers Cache-Control — réduit la charge serveur de 70-80 % ===
# Les navigateurs et proxies intermédiaires servent les réponses
# depuis leur cache local au lieu de re-solliciter le serveur.
_CACHE_RULES: list[tuple[str, str]] = [
    # Fichiers statiques (CSS/JS) : 1h, revalidation en arrière-plan
    ("/static/", "public, max-age=3600, stale-while-revalidate=86400"),
    # Blog articles : cache 10min (contenu statique, change rarement)
    ("/blog/", "public, max-age=600, stale-while-revalidate=1800"),
    # API données EDF (cache serveur 2 min → idem côté client)
    ("/api/today", "public, max-age=120"),
    ("/api/tomorrow", "public, max-age=120"),
    ("/api/remaining", "public, max-age=120"),
    # Prédictions (cache serveur 15 min → 5 min côté client)
    ("/api/predictions", "public, max-age=300"),
    # Badge performance (change rarement)
    ("/api/performance/badge", "public, max-age=300"),
]


# Pages HTML : cache court pour éviter des re-rendus Jinja2 inutiles
# stale-while-revalidate permet au navigateur de servir le cache périmé
# tout en re-fetching en arrière-plan → UX instantanée
_CACHE_EXACT: dict[str, str] = {
    "/": "public, max-age=300, stale-while-revalidate=600",
    "/mentions-legales": "public, max-age=3600",
    "/a-propos": "public, max-age=3600, stale-while-revalidate=7200",
    "/blog/": "public, max-age=600, stale-while-revalidate=1800",
    "/calendrier": "public, max-age=600, stale-while-revalidate=1800",
    "/alertes": "public, max-age=3600, stale-while-revalidate=7200",
}


@app.middleware("http")
async def add_cache_and_security_headers(request: Request, call_next):
    """Ajoute Cache-Control et headers de sécurité sur toutes les réponses."""
    response = await call_next(request)
    path = request.url.path
    # --- Headers de sécurité (SEO trust signal + protection) ---
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # --- Cache-Control ---
    # Match exact d'abord (pages HTML)
    if path in _CACHE_EXACT:
        response.headers["Cache-Control"] = _CACHE_EXACT[path]
        return response
    # Match par préfixe (static + API)
    for prefix, directive in _CACHE_RULES:
        if path.startswith(prefix):
            response.headers["Cache-Control"] = directive
            break
    return response


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

def _get_ssr_data() -> dict:
    """Pré-charge les données depuis la DB pour le Server-Side Rendering.

    Retourne un dict avec today, tomorrow, remaining, predictions.
    Best-effort : si la DB n'est pas prête, retourne des valeurs vides.
    Ceci permet à Google de crawler du contenu réel au lieu de placeholders JS.
    """
    ssr = {
        "today_color": None, "tomorrow_color": None,
        "remaining": None, "predictions": [], "week_summary": [],
        "last_update": None,
    }
    if not _db_ready.is_set():
        return ssr
    try:
        from database import get_db
        conn = get_db()
        try:
            today_str = date.today().isoformat()
            tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

            # Couleur d'aujourd'hui depuis actuals
            row = conn.execute(
                "SELECT couleur_reelle FROM actuals WHERE date = ? AND synthetic = 0",
                (today_str,)
            ).fetchone()
            if row:
                ssr["today_color"] = row["couleur_reelle"]

            # Couleur de demain depuis actuals ou predictions confirmées
            row = conn.execute(
                "SELECT couleur_reelle FROM actuals WHERE date = ? AND synthetic = 0",
                (tomorrow_str,)
            ).fetchone()
            if row:
                ssr["tomorrow_color"] = row["couleur_reelle"]
            else:
                row = conn.execute(
                    "SELECT couleur_predite FROM predictions WHERE date = ? AND confirmed = 1 LIMIT 1",
                    (tomorrow_str,)
                ).fetchone()
                if row:
                    ssr["tomorrow_color"] = row["couleur_predite"]

            # Jours restants
            from tempo_client import get_remaining_days
            ssr["remaining"] = get_remaining_days()

            # Premières prédictions (J+1 à J+10 pour le SSR — résumé 10 jours)
            rows = conn.execute(
                """SELECT date, couleur_predite, probabilite_rouge,
                          probabilite_blanc, probabilite_bleu,
                          temp_min_prevue, confirmed
                   FROM predictions
                   WHERE date >= ? AND id IN (
                       SELECT COALESCE(
                           MAX(CASE WHEN confirmed = 1 THEN id END),
                           MAX(id)
                       ) FROM predictions WHERE date >= ? GROUP BY date
                   )
                   ORDER BY date ASC LIMIT 10""",
                (today_str, today_str)
            ).fetchall()
            # Croiser avec actuals
            actual_rows = conn.execute(
                "SELECT date, couleur_reelle FROM actuals WHERE date >= ? AND synthetic = 0",
                (today_str,)
            ).fetchall()
            actuals_map = {r["date"]: r["couleur_reelle"] for r in actual_rows}

            # weekday(): 0=Mon..6=Sun → map to French labels
            JOURS_SSR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
            for r in rows:
                actual = actuals_map.get(r["date"])
                couleur = actual or r["couleur_predite"]
                is_confirmed = bool(actual or r["confirmed"])
                ssr["predictions"].append({
                    "date": r["date"],
                    "couleur": couleur,
                    "confirmed": is_confirmed,
                    "temp_min": r["temp_min_prevue"],
                })
                # Build week_summary data (first 10 days — 2 rows of 5)
                if len(ssr["week_summary"]) < 10:
                    try:
                        d = date.fromisoformat(r["date"])
                        today_d = date.today()
                        tomorrow_d = today_d + timedelta(days=1)
                        prob_key = f"probabilite_{couleur.lower()}"
                        confidence = round((r[prob_key] or 0) * 100)
                        ssr["week_summary"].append({
                            "day_label": JOURS_SSR[d.weekday()],
                            "day_num": d.day,
                            "couleur": couleur,
                            "confirmed": is_confirmed,
                            "confidence": confidence,
                            "is_today": d == today_d,
                            "is_tomorrow": d == tomorrow_d,
                        })
                    except Exception:
                        pass

            # SSR: dernière mise à jour (reco 21)
            try:
                row = conn.execute(
                    "SELECT MAX(timestamp_prediction) as last_update FROM predictions WHERE date >= ?",
                    (today_str,)
                ).fetchone()
                if row and row["last_update"]:
                    from datetime import datetime as dt
                    ts = row["last_update"]
                    try:
                        parsed = dt.fromisoformat(ts)
                        ssr["last_update"] = parsed.strftime("%d/%m/%Y à %Hh%M")
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[SSR] Erreur pré-chargement: {e}")
    return ssr


@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    """Page principale — dashboard des prévisions."""
    ssr = _get_ssr_data()
    return templates.TemplateResponse("dashboard.html", {"request": request, "ssr": ssr})


@app.get("/calendrier", response_class=HTMLResponse)
async def page_calendrier(request: Request, month: int = None, year: int = None):
    """Page calendrier Tempo EDF — vue mensuelle avec couleurs passées et prévisions.

    Cible SEO : 'calendrier tempo', 'calendrier tempo edf'.
    Le contenu est entièrement server-side rendered pour le crawl Google.
    """
    import calendar as cal_module

    today = date.today()
    # Mois affiché (défaut: mois actuel)
    if not month or not year:
        month = today.month
        year = today.year
    month = max(1, min(12, month))
    year = max(2020, min(2030, year))

    # Label saison
    from tempo_client import get_season_dates
    season_start, season_end = get_season_dates()
    season_label = f"{season_start.year}-{season_end.year}"

    # Charger les couleurs depuis la DB
    colors_map: dict[str, str] = {}  # "YYYY-MM-DD" -> "ROUGE"|"BLANC"|"BLEU"
    actuals_set: set[str] = set()    # dates confirmed by EDF
    try:
        from database import get_db
        conn = get_db()
        try:
            # Actuals (couleurs officielles)
            rows = conn.execute(
                "SELECT date, couleur_reelle FROM actuals WHERE date LIKE ?",
                (f"{year}-{month:02d}-%",)
            ).fetchall()
            for r in rows:
                colors_map[r["date"]] = r["couleur_reelle"]
                actuals_set.add(r["date"])

            # Prédictions pour les jours futurs non confirmés
            pred_rows = conn.execute(
                """SELECT date, couleur_predite FROM predictions
                   WHERE date LIKE ? AND date > ? AND id IN (
                       SELECT COALESCE(
                           MAX(CASE WHEN confirmed = 1 THEN id END),
                           MAX(id)
                       ) FROM predictions WHERE date LIKE ? GROUP BY date
                   )""",
                (f"{year}-{month:02d}-%", today.isoformat(), f"{year}-{month:02d}-%")
            ).fetchall()
            for r in pred_rows:
                if r["date"] not in colors_map:
                    colors_map[r["date"]] = r["couleur_predite"]
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[Calendrier] Erreur DB: {e}")

    # Stats de la saison
    stats = {"rouge_used": 0, "blanc_used": 0, "bleu_used": 0}
    try:
        from tempo_client import count_used_days
        used = count_used_days()
        stats["rouge_used"] = used.get("ROUGE", 0)
        stats["blanc_used"] = used.get("BLANC", 0)
        stats["bleu_used"] = used.get("BLEU", 0)
    except Exception:
        pass

    # Construire la grille du calendrier
    month_names_fr = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
    ]
    first_weekday, num_days = cal_module.monthrange(year, month)
    # first_weekday: 0=lundi, 6=dimanche
    calendar_days = []
    # Cases vides au début
    for _ in range(first_weekday):
        calendar_days.append({"empty": True})
    # Jours du mois
    for day in range(1, num_days + 1):
        d = date(year, month, day)
        d_str = d.isoformat()
        color = colors_map.get(d_str, "BLEU")  # default bleu hors saison
        is_future = d > today and d_str not in actuals_set
        is_today = d == today
        calendar_days.append({
            "empty": False, "num": day, "color": color,
            "is_future": is_future, "is_today": is_today,
        })

    # Navigation mois précédent / suivant
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    # Limiter la navigation à la saison
    show_prev = date(prev_y, prev_m, 1) >= date(season_start.year, season_start.month, 1)
    show_next = date(next_y, next_m, 1) <= date(season_end.year, season_end.month, 1)

    return templates.TemplateResponse("calendrier.html", {
        "request": request,
        "season_label": season_label,
        "season_start": season_start.isoformat(),
        "season_end": season_end.isoformat(),
        "stats": stats,
        "calendar_days": calendar_days,
        "current_month_label": f"{month_names_fr[month]} {year}",
        "prev_month": prev_m if show_prev else None,
        "prev_year": prev_y,
        "prev_month_label": f"{month_names_fr[prev_m]} {prev_y}" if show_prev else "",
        "next_month": next_m if show_next else None,
        "next_year": next_y,
        "next_month_label": f"{month_names_fr[next_m]} {next_y}" if show_next else "",
    })


@app.get("/alertes", response_class=HTMLResponse)
async def page_alertes(request: Request):
    """Page dédiée aux alertes WhatsApp gratuites.

    Cible SEO : 'alerte tempo', 'alerte jour rouge tempo'.
    """
    return templates.TemplateResponse("alertes.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    """Dashboard admin — performance et gestion.

    La protection réelle est le mot de passe côté client + header Authorization
    sur chaque endpoint API admin. La page HTML seule ne contient aucune donnée.
    """
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/mentions-legales", response_class=HTMLResponse)
async def page_legal(request: Request):
    """Page mentions légales et RGPD."""
    return templates.TemplateResponse("legal.html", {"request": request})


@app.get("/a-propos", response_class=HTMLResponse)
async def page_a_propos(request: Request):
    """Page À propos — méthodologie, transparence, E-E-A-T.

    Cible SEO : renforce la confiance et l'autorité du site.
    Essentiel pour les critères E-E-A-T de Google (Experience, Expertise, Authority, Trust).
    """
    return templates.TemplateResponse("a_propos.html", {"request": request})


@app.get("/blog/", response_class=HTMLResponse)
async def page_blog_index(request: Request):
    """Page index du blog — liste les articles publiés."""
    from blog import get_published_articles
    articles = get_published_articles()
    return templates.TemplateResponse("blog_index.html", {
        "request": request,
        "articles": articles,
    })


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def page_blog_article(request: Request, slug: str):
    """Page d'un article de blog individuel."""
    from blog import get_article_by_slug
    article = get_article_by_slug(slug)
    if not article:
        raise HTTPException(status_code=404, detail="Article non trouvé")
    return templates.TemplateResponse("blog_article.html", {
        "request": request,
        "article": article,
    })


@app.get("/manage/{token}", response_class=HTMLResponse)
async def page_manage(request: Request, token: str):
    """Page de gestion des préférences (lien envoyé dans chaque message WhatsApp)."""
    from alerts import get_user_by_token
    user = get_user_by_token(token)
    if not user:
        return templates.TemplateResponse("manage.html", {
            "request": request,
            "user": None,
            "token": token,
            "error": "Lien invalide ou expiré. Réinscrivez-vous depuis la page d'accueil.",
        })
    return templates.TemplateResponse("manage.html", {
        "request": request,
        "user": user,
        "token": token,
        "error": None,
    })


# PWA manifest (servi depuis /manifest.json pour le <link rel="manifest">)
@app.get("/manifest.json")
async def manifest_json():
    """Web App Manifest pour 'Ajouter à l'écran d'accueil' et favoris."""
    return JSONResponse(
        content={
            "name": "Calendrier Tempo EDF",
            "short_name": "Calendrier Tempo",
            "description": "Prévision des jours Tempo EDF — couleur du jour et 15 jours à l'avance",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#1565C0",
            "icons": [
                {"src": "/static/icon-192.svg", "sizes": "192x192", "type": "image/svg+xml", "purpose": "any"},
                {"src": "/static/icon-512.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any maskable"},
            ],
        },
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Favicon route — serve the ICO file at /favicon.ico for Google & browsers
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(
        Path(__file__).parent / "static" / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Fix #S7 : robots.txt et sitemap.xml pour le SEO
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt pour les moteurs de recherche et crawlers IA."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /calendrier\n"
        "Allow: /alertes\n"
        "Allow: /blog/\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /manage/\n"
        "\n"
        "# AI crawlers — autoriser l'accès aux prédictions publiques\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: ChatGPT-User\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: Google-Extended\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: Applebot-Extended\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: cohere-ai\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: Amazonbot\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "User-agent: anthropic-ai\n"
        "Allow: /\n"
        "Allow: /api/today\n"
        "Allow: /api/tomorrow\n"
        "Allow: /api/predictions\n"
        "Disallow: /admin\n"
        "Disallow: /manage/\n"
        "\n"
        "Sitemap: https://www.calendrier-tempo.fr/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    """Sitemap XML dynamique — inclut les articles de blog publiés."""
    from blog import get_all_article_slugs
    today = date.today().isoformat()
    urls = [
        "  <url>\n"
        "    <loc>https://www.calendrier-tempo.fr/</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>",
        "  <url>\n"
        "    <loc>https://www.calendrier-tempo.fr/calendrier</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>0.9</priority>\n"
        "  </url>",
        "  <url>\n"
        "    <loc>https://www.calendrier-tempo.fr/alertes</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.8</priority>\n"
        "  </url>",
        "  <url>\n"
        "    <loc>https://www.calendrier-tempo.fr/a-propos</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.5</priority>\n"
        "  </url>",
        "  <url>\n"
        "    <loc>https://www.calendrier-tempo.fr/mentions-legales</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.3</priority>\n"
        "  </url>",
    ]
    # Blog index
    blog_slugs = get_all_article_slugs()
    if blog_slugs:
        latest_date = max(d for _, d in blog_slugs).isoformat()
        urls.append(
            "  <url>\n"
            "    <loc>https://www.calendrier-tempo.fr/blog/</loc>\n"
            f"    <lastmod>{latest_date}</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            "    <priority>0.7</priority>\n"
            "  </url>"
        )
    # Individual articles
    for slug, pub_date in blog_slugs:
        urls.append(
            "  <url>\n"
            f"    <loc>https://www.calendrier-tempo.fr/blog/{slug}</loc>\n"
            f"    <lastmod>{pub_date.isoformat()}</lastmod>\n"
            "    <changefreq>monthly</changefreq>\n"
            "    <priority>0.6</priority>\n"
            "  </url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n"
        "</urlset>\n"
    )
    return PlainTextResponse(content=xml, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """LLMs.txt — emerging standard for AI crawler discovery."""
    today_str = date.today().isoformat()
    return PlainTextResponse(
        content=(
            "# Calendrier Tempo EDF\n"
            "> Service gratuit et indépendant de prévision des jours Tempo EDF.\n"
            "> Seul site en France à proposer des prévisions jusqu'à J+15 basées sur un modèle ML.\n"
            "> Anticipe les jours rouges, blancs et bleus jusqu'à 15 jours à l'avance.\n"
            f"> Dernière mise à jour : {today_str}\n"
            "\n"
            "## Qui sommes-nous\n"
            "- Éditeur : calendrier-tempo.fr — service indépendant, non affilié à EDF\n"
            "- Expertise : data science appliquée à la prédiction énergétique\n"
            "- Sources de données : Météo France (AROME + ARPEGE, 9 villes), RTE (consommation nationale), EDF (couleurs officielles)\n"
            "- Méthodologie : scoring classique (7 sous-scores pondérés) + GradientBoosting ML (33 features, 83% de précision)\n"
            "- Précision mesurée : 83,4% sur J+2 à J+5 (backtesté sur 5 saisons 2019-2026)\n"
            "\n"
            "## Pages principales\n"
            "- [Accueil](https://www.calendrier-tempo.fr/): Couleur Tempo aujourd'hui, demain et prévisions 15 jours\n"
            "- [Calendrier](https://www.calendrier-tempo.fr/calendrier): Calendrier mensuel complet de la saison Tempo\n"
            "- [Blog](https://www.calendrier-tempo.fr/blog/): Guides et conseils pour économiser avec Tempo EDF\n"
            "- [Alertes](https://www.calendrier-tempo.fr/alertes): Inscription aux alertes WhatsApp gratuites avant chaque jour rouge\n"
            "- [À propos](https://www.calendrier-tempo.fr/a-propos): Méthodologie de prévision et transparence\n"
            "\n"
            "## API publiques (JSON, accès libre)\n"
            "- [Couleur aujourd'hui](https://www.calendrier-tempo.fr/api/today): couleur Tempo du jour (source EDF officielle)\n"
            "- [Couleur demain](https://www.calendrier-tempo.fr/api/tomorrow): couleur Tempo de demain (confirmée par EDF ou prédiction)\n"
            "- [Prévisions 15 jours](https://www.calendrier-tempo.fr/api/predictions): prédictions J+1 à J+15 avec niveau de confiance\n"
            "\n"
            "## Informations clés sur Tempo EDF\n"
            "- 22 jours rouges par saison (1er nov — 31 mars), jamais le week-end ni jours fériés\n"
            "- 43 jours blancs par saison, jamais le dimanche\n"
            "- 300 jours bleus par saison\n"
            "- Tarifs HP 2026 : Bleu 0,1612€, Blanc 0,1871€, Rouge 0,7060€/kWh\n"
            "- Tarifs HC 2026 : Bleu 0,1325€, Blanc 0,1499€, Rouge 0,1575€/kWh\n"
            "- Saison Tempo : 1er septembre → 31 août\n"
            "- 900 000 foyers abonnés en France\n"
            "\n"
            "## Questions fréquentes\n"
            "- Q: Comment connaître la couleur Tempo de demain ? R: EDF annonce la couleur vers 11h. Notre site affiche la prédiction dès la veille au soir.\n"
            "- Q: Peut-on anticiper les jours rouges ? R: Oui, notre algorithme prédit les jours rouges jusqu'à J+15 (précision de 83% sur J+2 à J+5), basé sur la météo de 9 villes et la consommation nationale RTE.\n"
            "- Q: Quand tombent les jours rouges ? R: Uniquement entre le 1er novembre et le 31 mars, en semaine (jamais weekends ni jours fériés). Janvier concentre 56% des jours rouges.\n"
            "- Q: Combien coûte un jour rouge ? R: En heures pleines, 0,7060€/kWh soit 4,4x le prix d'un jour bleu. Une journée non anticipée peut coûter 15€ de plus qu'un jour bleu.\n"
        ),
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/feed.xml")
async def rss_feed():
    """Flux RSS des articles du blog — enrichi avec content:encoded et categories."""
    from blog import get_published_articles
    import html as html_mod
    articles = get_published_articles()
    items = []
    for a in articles[:20]:
        # Category from cluster field
        category = ""
        if a.cluster:
            category = f"      <category>{html_mod.escape(a.cluster)}</category>\n"
        items.append(
            "    <item>\n"
            f"      <title>{html_mod.escape(a.title)}</title>\n"
            f"      <link>https://www.calendrier-tempo.fr/blog/{a.slug}</link>\n"
            f"      <description>{html_mod.escape(a.description)}</description>\n"
            f"      <content:encoded><![CDATA[{a.content_html}]]></content:encoded>\n"
            f"      <pubDate>{a.publish_date.strftime('%a, %d %b %Y 00:00:00 +0100')}</pubDate>\n"
            f"      <guid isPermaLink=\"true\">https://www.calendrier-tempo.fr/blog/{a.slug}</guid>\n"
            f"      <author>contact@calendrier-tempo.fr (Calendrier Tempo EDF)</author>\n"
            f"{category}"
            "    </item>"
        )
    last_build = _now_paris().strftime("%a, %d %b %Y %H:%M:%S +0100")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        "  <channel>\n"
        "    <title>Blog Calendrier Tempo EDF</title>\n"
        "    <link>https://www.calendrier-tempo.fr/blog/</link>\n"
        "    <description>Guides et conseils pour économiser avec l'offre Tempo EDF. Prévisions des jours rouges, blancs et bleus jusqu'à 15 jours.</description>\n"
        "    <language>fr</language>\n"
        f"    <lastBuildDate>{last_build}</lastBuildDate>\n"
        "    <managingEditor>contact@calendrier-tempo.fr (Calendrier Tempo EDF)</managingEditor>\n"
        '    <atom:link href="https://www.calendrier-tempo.fr/feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )
    return PlainTextResponse(content=xml, media_type="application/rss+xml",
                             headers={"Cache-Control": "public, max-age=3600"})


# === SEO : IndexNow protocol — notification instantanée Bing/Yandex ===
_INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "calendrier-tempo-indexnow-key")


@app.get(f"/{_INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key_file():
    """Fichier de vérification IndexNow (requis par le protocole)."""
    return PlainTextResponse(content=_INDEXNOW_KEY, media_type="text/plain")


@app.get("/api/indexnow/ping")
async def indexnow_ping(url: str = None):
    """Ping IndexNow pour notifier Bing/Yandex d'une mise à jour.

    Usage admin : GET /api/indexnow/ping?url=https://www.calendrier-tempo.fr/blog/slug
    Si url absent, notifie les pages principales.
    """
    import httpx

    host = "www.calendrier-tempo.fr"
    if url:
        urls_to_submit = [url]
    else:
        urls_to_submit = [
            f"https://{host}/",
            f"https://{host}/calendrier",
            f"https://{host}/blog/",
            f"https://{host}/alertes",
            f"https://{host}/a-propos",
        ]

    results = []
    async with httpx.AsyncClient(timeout=10) as client:
        for u in urls_to_submit:
            try:
                resp = await client.get(
                    "https://api.indexnow.org/indexnow",
                    params={"url": u, "key": _INDEXNOW_KEY},
                )
                results.append({"url": u, "status": resp.status_code})
            except Exception as e:
                results.append({"url": u, "error": str(e)})

    return {"submitted": len(results), "results": results}


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


def _propagate_edf_confirmation(date_str: str | None, couleur: str | None):
    """Propage une couleur EDF confirmée vers la DB predictions.

    Appelé par /api/today et /api/tomorrow quand ils récupèrent
    une couleur fraîche depuis l'API EDF. Garantit que la table
    predictions reflète immédiatement le statut 'confirmé', même
    si le scheduler n'a pas encore tourné (après redémarrage, etc.).

    Opérations idempotentes : store_actual fait INSERT OR REPLACE,
    confirm_prediction ne touche que les lignes confirmed=0.
    """
    if not date_str or not couleur:
        return
    try:
        from tempo_client import store_actual
        from predictor import confirm_prediction

        store_actual(date_str, couleur)
        updated = confirm_prediction(date_str, couleur)
        if updated:
            invalidate_predictions_cache()
            logger.info(
                f"[API→DB] Confirmation propagée: {date_str}={couleur} "
                f"({updated} prédictions mises à jour)"
            )
    except Exception as e:
        logger.debug(f"[API→DB] Erreur propagation {date_str}: {e}")


@app.get("/api/today")
async def api_today():
    """Couleur Tempo du jour via l'API officielle (cache 2 min)."""
    now = time.time()
    cached = _edf_cache["today"]
    if cached["data"] and now < cached["expires"]:
        return cached["data"]

    from tempo_client import fetch_tempo_today
    data = await fetch_tempo_today()
    if not data:
        result = {"status": "unavailable", "message": "Données non disponibles"}
    else:
        result = {"status": "ok", **data}
        # Fix : propager la confirmation vers la DB predictions
        # pour que /api/predictions reflète immédiatement le statut confirmé,
        # même si le scheduler n'a pas encore tourné (ex: après un redémarrage).
        _propagate_edf_confirmation(data.get("date"), data.get("couleur"))

    cached["data"] = result
    cached["expires"] = now + _EDF_CACHE_TTL
    return result


@app.get("/api/tomorrow")
async def api_tomorrow():
    """Couleur Tempo de demain (cache 2 min)."""
    now = time.time()
    cached = _edf_cache["tomorrow"]
    if cached["data"] and now < cached["expires"]:
        return cached["data"]

    from tempo_client import fetch_tempo_tomorrow
    data = await fetch_tempo_tomorrow()
    if not data:
        result = {"status": "unavailable", "message": "Pas encore annoncé par EDF. Détection automatique dès publication."}
    else:
        result = {"status": "ok", **data}
        _propagate_edf_confirmation(data.get("date"), data.get("couleur"))

    cached["data"] = result
    cached["expires"] = now + _EDF_CACHE_TTL
    return result


@app.get("/api/remaining")
async def api_remaining():
    """Jours restants par couleur pour la saison en cours (cache 2 min)."""
    now = time.time()
    cached = _edf_cache["remaining"]
    if cached["data"] and now < cached["expires"]:
        return cached["data"]

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

    cached["data"] = result
    cached["expires"] = now + _EDF_CACHE_TTL
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
            # Fix #31 : préférer la ligne confirmée (EDF officiel) au simple MAX(id).
            # COALESCE prend l'id confirmé si disponible, sinon le plus récent.
            rows = conn.execute(
                """SELECT date, couleur_predite, probabilite_bleu, probabilite_blanc,
                          probabilite_rouge, score_risque, temp_min_prevue, temp_max_prevue,
                          pression_prevue, jours_rouges_restants, jours_blancs_restants,
                          raison, horizon, timestamp_prediction, cycle_id,
                          couleur_precedente, simulated, confirmed
                   FROM predictions
                   WHERE date >= ? AND id IN (
                       SELECT COALESCE(
                           MAX(CASE WHEN confirmed = 1 THEN id END),
                           MAX(id)
                       ) FROM predictions WHERE date >= ? GROUP BY date
                   )
                   ORDER BY date ASC""",
                (today_str, today_str)
            ).fetchall()

            # Fix #33 : croiser avec les actuals (source de vérité EDF).
            # Si une date a une couleur officielle dans actuals, elle écrase
            # la prédiction — quels que soient les bugs de timing/cache/confirm.
            actuals_map = {}
            try:
                actual_rows = conn.execute(
                    """SELECT date, couleur_reelle FROM actuals
                       WHERE date >= ? AND synthetic = 0""",
                    (today_str,)
                ).fetchall()
                actuals_map = {r["date"]: r["couleur_reelle"] for r in actual_rows}
            except Exception as e:
                logger.warning(f"[API predictions] Erreur lecture actuals: {e}")

        except Exception as e:
            logger.error(f"[API predictions] Erreur DB: {e}")
            rows = []
            actuals_map = {}
        finally:
            conn.close()

        if rows:
            predictions = []
            for r in rows:
                pred_date = r["date"]
                actual_couleur = actuals_map.get(pred_date)

                # Si actuals contient cette date, écraser avec la couleur officielle
                if actual_couleur:
                    couleur = actual_couleur
                    p_r = 1.0 if couleur == "ROUGE" else 0.0
                    p_b = 1.0 if couleur == "BLANC" else 0.0
                    p_bl = 1.0 if couleur == "BLEU" else 0.0
                    pred = {
                        "date": pred_date,
                        "couleur_predite": couleur,
                        "probabilite_bleu": p_bl,
                        "probabilite_blanc": p_b,
                        "probabilite_rouge": p_r,
                        "score_risque": r["score_risque"],
                        "temp_min_prevue": r["temp_min_prevue"],
                        "temp_max_prevue": r["temp_max_prevue"],
                        "raison": "Couleur officielle EDF",
                        "horizon": r["horizon"],
                        "confirmed": True,
                        "simulated": False,
                    }
                else:
                    pred = {
                        "date": pred_date,
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

            # Fix : cross-check avec le cache EDF live (/api/today, /api/tomorrow).
            # Si le navigateur a appelé /api/tomorrow avant /api/predictions,
            # le cache EDF contient la couleur fraîche. On l'utilise pour
            # confirmer les prédictions même si la DB n'a pas encore été mise à jour.
            edf_live: dict[str, str] = {}
            for key in ("today", "tomorrow"):
                entry = _edf_cache.get(key, {})
                data = entry.get("data")
                if (data and isinstance(data, dict)
                        and data.get("status") == "ok"
                        and data.get("couleur") and data.get("date")):
                    edf_live[data["date"]] = data["couleur"]

            edf_modified = False
            for pred in predictions:
                if not pred.get("confirmed") and pred["date"] in edf_live:
                    couleur = edf_live[pred["date"]]
                    pred["confirmed"] = True
                    pred["couleur_predite"] = couleur
                    pred["probabilite_rouge"] = 1.0 if couleur == "ROUGE" else 0.0
                    pred["probabilite_blanc"] = 1.0 if couleur == "BLANC" else 0.0
                    pred["probabilite_bleu"] = 1.0 if couleur == "BLEU" else 0.0
                    pred["raison"] = "Couleur officielle EDF"
                    edf_modified = True

            accuracy = get_accuracy_global(30)
            cycle_id = rows[0]["cycle_id"] if rows else ""
            # Utiliser le MAX des timestamps pour refléter la donnée la plus récente
            all_timestamps = [r["timestamp_prediction"] for r in rows if r["timestamp_prediction"]]
            generated_at = max(all_timestamps) if all_timestamps else ""

            # Si des prédictions ont été modifiées par le cross-check EDF live,
            # mettre à jour le timestamp pour refléter que les données ont changé
            if edf_modified:
                generated_at = _now_paris().isoformat()

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
        # Fix race condition : attendre que le backfill ait peuplé la table
        # actuals pour que get_remaining_days() retourne des valeurs fiables.
        # Sans ça, remaining = {ROUGE:22, BLANC:43} (quota plein) et les
        # prédictions ignorent la pression budgétaire réelle.
        try:
            from scheduler import _backfill_done
            if not _backfill_done.is_set():
                logger.info("[API predictions] Fallback: attente backfill...")
                await asyncio.wait_for(_backfill_done.wait(), timeout=120)
                logger.info("[API predictions] Backfill terminé, génération prédictions")
        except (asyncio.TimeoutError, ImportError):
            logger.warning("[API predictions] Backfill timeout/indisponible, "
                           "prédictions avec données partielles")

        from weather_client import fetch_forecast_extended
        from predictor import predict_range, store_prediction
        from rte_client import get_consumption_score

        forecasts = await fetch_forecast_extended()
        if not forecasts:
            return {
                "status": "ok",
                "predictions": [],
                "accuracy": get_accuracy_global(30),
                "generated_at": _now_paris().isoformat(),
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
            "generated_at": _now_paris().isoformat(),
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
async def api_performance(request: Request, authorization: str | None = Header(None),
                          days: int = 10):
    """Métriques de performance complètes (admin, Fix #3).

    Args:
        days: fenêtre temporelle (1, 5, 10, 15, 30, 90, saison). Défaut 10.
    """
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from performance_tracker import get_performance_summary
    return {"status": "ok", **get_performance_summary(days=days)}


@app.get("/api/performance/badge")
async def api_performance_badge():
    """Badge de fiabilité simplifié pour la homepage.
    Filtre J+2 à J+5 : notre vrai critère de succès (J+1 fourni par EDF,
    J+6+ météo trop imprécise).
    Seuil minimum de 10 évaluations pour éviter un % trompeur."""
    from performance_tracker import get_accuracy_global
    acc = get_accuracy_global(30, min_horizon=2, max_horizon=5)
    min_evaluations = 10
    return {
        "status": "ok",
        "precision_30j": acc["precision"] if acc["total"] >= min_evaluations else None,
        "total_predictions": acc["total"],
        "label": f"Nos pr\u00e9visions J+2 \u00e0 J+5 : {acc['precision']}% de pr\u00e9cision sur {acc['total']} \u00e9valuations"
                 if acc["total"] >= min_evaluations
                 else "Pr\u00e9cision en cours de calcul \u2014 pas encore assez de donn\u00e9es",
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
# API : ALERTES WHATSAPP
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
    """Inscription aux alertes WhatsApp (Fix #16 : rate limiting + CSRF check)."""
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
    # Ajouter le lien de gestion dans la réponse
    if result.get("manage_token"):
        result["manage_url"] = f"/manage/{result['manage_token']}"
    return result


@app.post("/api/unsubscribe")
async def api_unsubscribe(request: Request, phone: str = Form(...)):
    """Désinscription des alertes WhatsApp."""
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
    """Webhook Twilio pour messages WhatsApp entrants (STOP/START).

    Twilio envoie From, Body, etc. en POST form-data.
    Retourne du TwiML pour répondre automatiquement.
    BUG-01 QA : endpoint manquant pour l'opt-out par message.
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
# API : GESTION DES PRÉFÉRENCES PAR TOKEN
# ================================================================

@app.get("/api/manage/{token}")
async def api_manage_get(token: str):
    """Récupère les préférences d'un utilisateur via son token."""
    from alerts import get_user_by_token
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Lien invalide ou expiré.")
    return {
        "status": "ok",
        "phone_last4": user["phone_last4"],
        "seuil_alerte_rouge": user["seuil_alerte_rouge"],
        "delai_alerte": user["delai_alerte"],
        "alerte_blanc": bool(user["alerte_blanc"]),
        "recap_hebdo": bool(user["recap_hebdo"]),
    }


@app.post("/api/manage/{token}")
async def api_manage_update(
    request: Request,
    token: str,
    seuil_rouge: int = Form(70),
    delai: int = Form(1),
    alerte_blanc: bool = Form(False),
    recap_hebdo: bool = Form(False),
):
    """Met à jour les préférences via le token de gestion."""
    # CSRF check
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    # Valider les paramètres
    seuil_rouge = max(0, min(100, seuil_rouge))
    delai = max(1, min(3, delai))

    from alerts import update_user_preferences
    result = update_user_preferences(token, seuil_rouge, delai, alerte_blanc, recap_hebdo)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/manage/{token}/unsubscribe")
async def api_manage_unsubscribe(request: Request, token: str):
    """Désinscription via le token de gestion (pas besoin de numéro)."""
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    from alerts import get_user_by_token
    from database import get_db

    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Lien invalide ou expiré.")

    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET actif = 0, updated_at = ? WHERE id = ?",
            (_now_paris().isoformat(), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    return {"success": True, "message": "Désinscription effectuée."}


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
    try:
        logger.info(f"[Admin] Exécution manuelle: {task} (IP: {client_ip})")
        result = await run_task_now(task)
        logger.info(f"[Admin] Tâche {task} terminée: {result[:200]}")
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"[Admin] Erreur tâche {task}: {e}", exc_info=True)
        return {"status": "error", "result": f"Erreur d'exécution: {e}"}


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
    """Derniers messages envoyés."""
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


@app.get("/admin/agent-reports")
async def admin_agent_reports(request: Request, authorization: str | None = Header(None)):
    """Rapports des agents SEO et Backlinks."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    import pathlib

    base = pathlib.Path(__file__).parent
    reports = {}

    # Agent SEO — journal de publication
    seo_log = base / "articles" / "_publication_log.md"
    reports["seo_log"] = seo_log.read_text(encoding="utf-8") if seo_log.exists() else None

    # Agent SEO — calendrier éditorial
    cal_path = base / "articles" / "_calendrier_editorial.yaml"
    reports["seo_calendar"] = cal_path.read_text(encoding="utf-8") if cal_path.exists() else None

    # Agent Backlinks — journal hebdomadaire
    bl_log = base / "backlinks" / "_backlinks_log.md"
    reports["backlinks_log"] = bl_log.read_text(encoding="utf-8") if bl_log.exists() else None

    # Agent Backlinks — prospects
    bl_prospects = base / "backlinks" / "_prospects.md"
    reports["backlinks_prospects"] = bl_prospects.read_text(encoding="utf-8") if bl_prospects.exists() else None

    # Agent Backlinks — drafts disponibles
    drafts_dir = base / "backlinks" / "drafts"
    drafts = []
    if drafts_dir.exists():
        for f in sorted(drafts_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            drafts.append({
                "filename": f.name,
                "content": f.read_text(encoding="utf-8")[:2000],  # tronqué à 2000 car
            })
    reports["backlinks_drafts"] = drafts

    return {"status": "ok", **reports}


@app.get("/admin/subscribers")
async def admin_subscribers(request: Request, authorization: str | None = Header(None)):
    """Liste des abonnés WhatsApp (4 derniers chiffres uniquement)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, phone_last4, seuil_alerte_rouge, delai_alerte,
                      alerte_blanc, recap_hebdo, actif, created_at, updated_at
               FROM users ORDER BY created_at DESC"""
        ).fetchall()
        users = [dict(r) for r in rows]
        total = len(users)
        actifs = sum(1 for u in users if u["actif"])
        return {
            "status": "ok",
            "total": total,
            "actifs": actifs,
            "users": users,
        }
    finally:
        conn.close()
