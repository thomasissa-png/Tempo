"""Application FastAPI principale — Calendrier Tempo EDF (calendrier-tempo.fr).

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
import hashlib
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

from fastapi import BackgroundTasks, FastAPI, Request, Form, HTTPException, Header
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Config
from database import init_db
import site_facts

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
    # Invalider le cache performance pour que le dashboard reflète
    # immédiatement les nouvelles évaluations
    try:
        from performance_tracker import invalidate_perf_summary_cache
        invalidate_perf_summary_cache()
    except Exception:
        pass
    try:
        from prediction_history import invalidate_history_cache
        invalidate_history_cache()
    except Exception:
        pass

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
        # Historique public (2026-09-29) : les prédictions réelles ne sont plus
        # jamais purgées (grille J-15 → J-1 et températures de /historique-previsions,
        # ~15 lignes par jour cible). Seules les prédictions simulées > 90 jours le sont.
        deleted_preds = conn.execute(
            "DELETE FROM predictions WHERE date < ? AND simulated = 1 AND cycle_id NOT LIKE 'backtest%'",
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
            "purge_old_data: deleted %d weather_cache (>30d), %d simulated predictions (>90d), "
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
    # Diagnostic : log la source du mot de passe pour faciliter le debug
    _pw_from_admin = os.getenv("ADMIN_PASSWORD", "")
    _pw_from_session = os.getenv("SESSION_SECRET", "")
    if _pw_from_admin:
        logger.info(
            "[Admin] Mot de passe source: ADMIN_PASSWORD env var (longueur=%d)",
            len(_pw_from_admin),
        )
    elif _pw_from_session:
        logger.info(
            "[Admin] Mot de passe source: SESSION_SECRET env var (longueur=%d)",
            len(_pw_from_session),
        )

    if not Config.ADMIN_PASSWORD:
        import secrets
        Config.ADMIN_PASSWORD = secrets.token_urlsafe(24)
        logger.warning(
            "[SECURITE] ADMIN_PASSWORD non défini ! "
            "Un mot de passe aléatoire a été généré pour cette session. "
            "Définissez ADMIN_PASSWORD dans .env pour le conserver."
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
    title="Calendrier Tempo EDF",
    description="Prévision des jours Tempo EDF avec alertes WhatsApp",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)  # Compresse CSS/JS/JSON > 500 octets
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === SEO : variables globales Jinja2 pour les meta tags de vérification ===
_BING_VERIFY = os.getenv("BING_SITE_VERIFICATION", "")
_GOOGLE_VERIFY = os.getenv("GOOGLE_SITE_VERIFICATION", "")
if hasattr(templates, "env"):
    templates.env.globals["bing_verification"] = _BING_VERIFY
    templates.env.globals["google_verification"] = _GOOGLE_VERIFY
    # Faits chiffrés du site (source unique : site_facts.py)
    templates.env.globals["facts"] = site_facts.template_globals()
    # JSON-LD via |tojson : UTF-8 lisible, ordre des clés conservé
    # (tojson échappe toujours < > & ' : sûr dans un <script>)
    templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": False}


# === SEO : page 404 personnalisée (HTML au lieu de JSON brut) ===
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc):
    """Page 404 SEO-friendly avec navigation vers les pages principales.

    HTML pour toute URL hors /api/ (y compris curl et crawlers qui envoient
    Accept: */*) ; JSON pour l'API ou si le client demande explicitement du JSON.
    """
    accept = request.headers.get("accept", "")
    wants_json = "application/json" in accept and "text/html" not in accept
    if request.url.path.startswith("/api/") or wants_json:
        return JSONResponse(status_code=404, content={"detail": "Page non trouvée"})
    return templates.TemplateResponse(
        "404.html", {"request": request}, status_code=404,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


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
    # API données EDF (cache serveur 2 min → idem côté client, SWR pour UX)
    ("/api/today", "public, max-age=120, stale-while-revalidate=60"),
    ("/api/tomorrow", "public, max-age=120, stale-while-revalidate=60"),
    ("/api/remaining", "public, max-age=120, stale-while-revalidate=60"),
    # Prédictions : 2 min cache + SWR pour fraîcheur (couleurs changent après confirmation EDF)
    ("/api/predictions", "public, max-age=120, stale-while-revalidate=120"),
    # Badge performance (change rarement)
    ("/api/performance/badge", "public, max-age=300, stale-while-revalidate=300"),
    # Pages mois / saison du calendrier (SSR, prévisions recalculées chaque jour)
    ("/calendrier/", "public, max-age=600, stale-while-revalidate=1800"),
    # Historique des prévisions, saisons passées
    ("/historique-previsions/", "public, max-age=600, stale-while-revalidate=1800"),
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
    "/tarif-tempo-edf": "public, max-age=3600, stale-while-revalidate=7200",
    "/api-tempo": "public, max-age=3600, stale-while-revalidate=7200",
    "/methodologie": "public, max-age=3600, stale-while-revalidate=7200",
    # Historique des prévisions : change à chaque confirmation EDF (cache 10 min)
    "/historique-previsions": "public, max-age=600, stale-while-revalidate=1800",
    "/historique-previsions.csv": "public, max-age=600, stale-while-revalidate=1800",
    # Couleur de demain : change quand EDF publie (fin de matinée)
    "/couleur-tempo-demain": "public, max-age=120, stale-while-revalidate=60",
    # SEO files — Bing re-fetche robots.txt et sitemap.xml à chaque crawl sans cache
    "/robots.txt": "public, max-age=86400",
    "/sitemap.xml": "public, max-age=3600, stale-while-revalidate=3600",
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
    # --- Content-Language pour les pages HTML (aide Bing à classifier la langue) ---
    if path in _CACHE_EXACT or path.startswith(("/blog/", "/calendrier/", "/historique-previsions/")):
        response.headers["Content-Language"] = "fr"
    # --- Cache-Control --- (jamais sur les erreurs : une 404 ne doit pas être mise en cache 1 h)
    if response.status_code >= 400:
        return response
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
        logger.warning("[Admin] Échec login depuis %s", client_ip)
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
    today_d = date.today()
    tomorrow_d = today_d + timedelta(days=1)
    ssr = {
        "today_color": None, "tomorrow_color": None,
        "remaining": None, "predictions": [], "week_summary": [],
        "last_update": None, "last_update_iso": None,
        # Réponse directe en texte (SSR) : dates lisibles + prévision de demain
        "today_iso": today_d.isoformat(), "today_label": site_facts.fr_date(today_d),
        "tomorrow_iso": tomorrow_d.isoformat(),
        "tomorrow_label": site_facts.fr_date(tomorrow_d, with_year=False),
        "tomorrow_label_year": site_facts.fr_date(tomorrow_d),
        "tomorrow_forecast_color": None, "tomorrow_forecast_confidence": None,
        "week_outlook": "",
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
            else:
                row = conn.execute(
                    "SELECT couleur_predite FROM predictions WHERE date = ? AND confirmed = 1 LIMIT 1",
                    (today_str,)
                ).fetchone()
                if row:
                    ssr["today_color"] = row["couleur_predite"]

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
                        # Arrondi « demi vers le haut » comme Math.round côté JS (même HTML)
                        confidence = int((r[prob_key] or 0) * 100 + 0.5)
                        ssr["week_summary"].append({
                            "date": r["date"],
                            "day_label": JOURS_SSR[d.weekday()],
                            "day_num": d.day,
                            "date_label": site_facts.fr_date(d, with_year=False),
                            "couleur": couleur,
                            "couleur_label": site_facts.couleur_label(couleur),
                            "confirmed": is_confirmed,
                            "confidence": confidence,
                            "is_today": d == today_d,
                            "is_tomorrow": d == tomorrow_d,
                            "is_hero": d in (today_d, tomorrow_d),
                        })
                    except Exception:
                        pass
                # Prévision de demain (si EDF n'a pas encore publié la couleur)
                if r["date"] == tomorrow_d.isoformat() and not is_confirmed:
                    prob = r[f"probabilite_{(couleur or '').lower()}"] if couleur in ("BLEU", "BLANC", "ROUGE") else None
                    ssr["tomorrow_forecast_color"] = couleur
                    ssr["tomorrow_forecast_confidence"] = round(prob * 100) if prob is not None else None

            # Phrase « la suite » sous les pastilles (même texte que renderWeekSummary en JS)
            ssr["week_outlook"] = site_facts.week_outlook_html(ssr["week_summary"])

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
                        ssr["last_update_iso"] = parsed.isoformat(timespec="minutes")
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[SSR] Erreur pré-chargement: {e}")
    return ssr


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_dashboard(request: Request):
    """Page principale — dashboard des prévisions."""
    ssr = _get_ssr_data()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "ssr": ssr,
        "faq": site_facts.FAQ_HOME,
        "faq_ld": site_facts.faq_jsonld(site_facts.FAQ_HOME),
        "itemlist_ld": _predictions_itemlist_ld(ssr),
    })


def _predictions_itemlist_ld(ssr: dict) -> dict | None:
    """ItemList JSON-LD des prochains jours (données SSR réelles uniquement)."""
    preds = ssr.get("predictions") or []
    if not preds:
        return None
    items = []
    for i, p in enumerate(preds[:5], start=1):
        try:
            label = site_facts.fr_date(date.fromisoformat(p["date"]))
        except (TypeError, ValueError):
            label = p["date"]
        statut = "couleur officielle EDF" if p["confirmed"] else "prévision"
        items.append({
            "@type": "ListItem", "position": i,
            "name": f"{label} : {site_facts.couleur_label(p['couleur'])} ({statut})",
        })
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Couleurs Tempo EDF des prochains jours",
        "numberOfItems": len(items),
        "itemListElement": items,
    }


_MONTH_NAMES_FR = [
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]


def _season_of(d: date) -> int:
    """Année de début de la saison Tempo contenant d (saison = 1er sept. → 31 août)."""
    return d.year if d.month >= 9 else d.year - 1


def _first_actual_date() -> date | None:
    """Première date avec une couleur officielle réelle (actuals, synthetic = 0)."""
    try:
        from database import get_db
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT MIN(date) AS first_date FROM actuals WHERE synthetic = 0"
            ).fetchone()
        finally:
            conn.close()
        if row and row["first_date"]:
            return date.fromisoformat(row["first_date"])
    except Exception as e:
        logger.debug(f"[Calendrier] Erreur première date: {e}")
    return None


def _calendar_bounds() -> tuple[date, date]:
    """(premier mois, dernier mois) navigables : saisons avec données réelles → fin de saison en cours."""
    from tempo_client import get_season_dates
    season_start, season_end = get_season_dates()
    first = _first_actual_date()
    first_month = date(_season_of(first), 9, 1) if first and first < season_start else season_start
    return first_month, date(season_end.year, season_end.month, 1)


def _month_path(year: int, month: int) -> str:
    """URL canonique d'un mois : /calendrier pour le mois en cours, sinon /calendrier/AAAA-MM."""
    today = date.today()
    if (year, month) == (today.year, today.month):
        return "/calendrier"
    return f"/calendrier/{year}-{month:02d}"


def _past_seasons_with_data() -> list[str]:
    """Saisons terminées ayant au moins une couleur officielle réelle (plus récente d'abord)."""
    from tempo_client import get_season_dates
    season_start, _ = get_season_dates()
    first = _first_actual_date()
    if not first or first >= season_start:
        return []
    return [f"{y}-{y + 1}" for y in range(season_start.year - 1, _season_of(first) - 1, -1)]


def _get_calendrier_data(month: int | None = None, year: int | None = None) -> dict:
    """Compute calendar data for a given month/year.

    Returns a dict with all data needed to render the calendar grid and navigation.
    Shared by the SSR pages (/calendrier, /calendrier/AAAA-MM) and the JSON endpoint.
    Couleurs : officielles (actuals, synthetic = 0) pour le passé, prévisions pour
    le futur ; un jour sans donnée reste « non publié » / « pas encore prévu »
    (jamais de couleur par défaut).
    """
    import calendar as cal_module

    today = date.today()
    if not month or not year:
        month = today.month
        year = today.year
    month = max(1, min(12, month))
    year = max(2019, min(2030, year))

    from tempo_client import get_season_dates
    season_start, season_end = get_season_dates()
    season_label = f"{season_start.year}-{season_end.year}"
    month_season = _season_of(date(year, month, 1))
    month_season_label = f"{month_season}-{month_season + 1}"

    # Charger les couleurs depuis la DB
    official: dict[str, str] = {}
    predicted: dict[str, str] = {}
    try:
        from database import get_db
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT date, couleur_reelle FROM actuals WHERE date LIKE ? AND synthetic = 0",
                (f"{year}-{month:02d}-%",)
            ).fetchall()
            for r in rows:
                official[r["date"]] = r["couleur_reelle"]

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
                if r["date"] not in official:
                    predicted[r["date"]] = r["couleur_predite"]
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[Calendrier] Erreur DB: {e}")

    # Stats de la saison en cours
    stats = {"rouge_used": 0, "blanc_used": 0, "bleu_used": 0}
    try:
        from tempo_client import count_used_days
        used = count_used_days()
        stats["rouge_used"] = used.get("ROUGE", 0)
        stats["blanc_used"] = used.get("BLANC", 0)
        stats["bleu_used"] = used.get("BLEU", 0)
    except Exception:
        pass

    first_weekday, num_days = cal_module.monthrange(year, month)
    calendar_days = []
    for _ in range(first_weekday):
        calendar_days.append({"empty": True})
    red_days, white_days, predicted_red, predicted_white = [], [], [], []
    for day in range(1, num_days + 1):
        d = date(year, month, day)
        d_str = d.isoformat()
        if d_str in official:
            color, status = official[d_str], "officiel"
        elif d_str in predicted:
            color, status = predicted[d_str], "prevision"
        else:
            color, status = None, ("non_publie" if d <= today else "non_prevu")
        is_future = status in ("prevision", "non_prevu")
        label = site_facts.fr_date(d)
        if status == "officiel":
            state = f"{site_facts.couleur_label(color)} (couleur officielle)"
        elif status == "prevision":
            state = f"{site_facts.couleur_label(color)} (prévision)"
        elif status == "non_publie":
            state = "couleur non disponible"
        else:
            state = "pas encore prévu"
        calendar_days.append({
            "empty": False, "num": day, "color": color, "status": status,
            "is_future": is_future, "is_today": d == today,
            "date": d_str, "label": f"{label} : {state}",
        })
        if color == "ROUGE":
            (red_days if status == "officiel" else predicted_red).append(label)
        elif color == "BLANC":
            (white_days if status == "officiel" else predicted_white).append(label)

    first_month, last_month = _calendar_bounds()
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    show_prev = date(prev_y, prev_m, 1) >= first_month
    show_next = date(next_y, next_m, 1) <= last_month

    return {
        "season_label": season_label,
        "season_start": season_start.isoformat(),
        "season_end": season_end.isoformat(),
        "month_season_label": month_season_label,
        "stats": stats,
        "calendar_days": calendar_days,
        "month": month,
        "year": year,
        "month_name": _MONTH_NAMES_FR[month],
        "current_month_label": f"{_MONTH_NAMES_FR[month]} {year}",
        "month_path": _month_path(year, month),
        "is_current_month": (year, month) == (today.year, today.month),
        "red_days": red_days,
        "white_days": white_days,
        "predicted_red": predicted_red,
        "predicted_white": predicted_white,
        "official_count": len(official),
        "num_days": num_days,
        "prev_month": prev_m if show_prev else None,
        "prev_year": prev_y,
        "prev_path": _month_path(prev_y, prev_m) if show_prev else None,
        "prev_month_label": f"{_MONTH_NAMES_FR[prev_m]} {prev_y}" if show_prev else "",
        "next_month": next_m if show_next else None,
        "next_year": next_y,
        "next_path": _month_path(next_y, next_m) if show_next else None,
        "next_month_label": f"{_MONTH_NAMES_FR[next_m]} {next_y}" if show_next else "",
    }


def _get_season_data(start_year: int) -> dict:
    """Couleurs officielles d'une saison (actuals, synthetic = 0 : données réelles uniquement)."""
    start, end = date(start_year, 9, 1), date(start_year + 1, 8, 31)
    rows = []
    try:
        from database import get_db
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT date, couleur_reelle FROM actuals "
                "WHERE date >= ? AND date <= ? AND synthetic = 0 ORDER BY date",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[Saison] Erreur DB: {e}")
    counts = {"ROUGE": 0, "BLANC": 0, "BLEU": 0}
    red_days, white_days = [], []
    per_month: dict[str, dict] = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        c = r["couleur_reelle"]
        if c not in counts:
            continue
        counts[c] += 1
        key = f"{d.year}-{d.month:02d}"
        m = per_month.setdefault(key, {"ROUGE": 0, "BLANC": 0, "BLEU": 0})
        m[c] += 1
        if c == "ROUGE":
            red_days.append({"iso": r["date"], "label": site_facts.fr_date(d)})
        elif c == "BLANC":
            white_days.append({"iso": r["date"], "label": site_facts.fr_date(d)})
    months = []
    y, mth = start_year, 9
    for _ in range(12):
        key = f"{y}-{mth:02d}"
        months.append({
            "key": key, "label": f"{_MONTH_NAMES_FR[mth]} {y}",
            "path": _month_path(y, mth), "counts": per_month.get(key),
        })
        y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    total_days = (end - start).days + 1
    return {
        "season_label": f"{start_year}-{start_year + 1}",
        "season_start": start.isoformat(),
        "season_end": end.isoformat(),
        "counts": counts,
        "days_with_data": len(rows),
        "total_days": total_days,
        "first_date": site_facts.fr_date(date.fromisoformat(rows[0]["date"])) if rows else None,
        "last_date": site_facts.fr_date(date.fromisoformat(rows[-1]["date"])) if rows else None,
        "red_days": red_days,
        "white_days": white_days,
        "months": months,
        "complete": len(rows) >= total_days,
    }


def _calendar_context(request: Request, data: dict, canonical_path: str) -> dict:
    """Contexte commun des pages calendrier (SSR)."""
    today = date.today()
    faq = site_facts.faq_calendrier(data["season_label"])
    crumbs = [("Accueil", "/"), ("Calendrier Tempo EDF", "/calendrier")]
    if canonical_path != "/calendrier":
        crumbs.append((data["current_month_label"], canonical_path))
    ctx = dict(data)
    ctx.update({
        "request": request,
        "canonical_path": canonical_path,
        "faq": faq,
        "faq_ld": site_facts.faq_jsonld(faq),
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
        "past_seasons": _past_seasons_with_data(),
        "today_iso": today.isoformat(),
    })
    return ctx


@app.api_route("/calendrier", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_calendrier(request: Request, month: int | None = None, year: int | None = None):
    """Page calendrier Tempo EDF — mois en cours (couleurs officielles + prévisions).

    Cible SEO : 'calendrier tempo', 'calendrier tempo edf'.
    Rendu serveur complet ; les autres mois ont leur propre URL /calendrier/AAAA-MM.
    Les anciennes URL ?month=&year= redirigent (301) vers l'URL canonique du mois.
    """
    if month and year and 1 <= month <= 12 and 2019 <= year <= 2030:
        path = _month_path(year, month)
        if path != "/calendrier":
            return RedirectResponse(path, status_code=301)
    data = _get_calendrier_data(month, year)
    return templates.TemplateResponse("calendrier.html", _calendar_context(request, data, "/calendrier"))


@app.api_route("/calendrier/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_calendrier_slug(request: Request, slug: str):
    """/calendrier/AAAA-MM (mois) ou /calendrier/AAAA-AAAA (saison, dates réelles)."""
    import re as _re
    m = _re.fullmatch(r"(\d{4})-(\d{2})", slug)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12 or year < 1:
            raise HTTPException(status_code=404, detail="Mois inconnu")
        first_month, last_month = _calendar_bounds()
        if not first_month <= date(year, month, 1) <= last_month:
            raise HTTPException(status_code=404, detail="Mois hors calendrier")
        data = _get_calendrier_data(month, year)
        return templates.TemplateResponse(
            "calendrier.html", _calendar_context(request, data, _month_path(year, month)))
    m = _re.fullmatch(r"(\d{4})-(\d{4})", slug)
    if m and int(m.group(2)) == int(m.group(1)) + 1 and int(m.group(1)) >= 1:
        start_year = int(m.group(1))
        from tempo_client import get_season_dates
        current_start, _ = get_season_dates()
        season = _get_season_data(start_year)
        if start_year > current_start.year or (season["days_with_data"] == 0 and start_year != current_start.year):
            raise HTTPException(status_code=404, detail="Saison sans données")
        season["is_current"] = start_year == current_start.year
        path = f"/calendrier/{season['season_label']}"
        crumbs = [("Accueil", "/"), ("Calendrier Tempo EDF", "/calendrier"),
                  (f"Saison {season['season_label']}", path)]
        return templates.TemplateResponse("calendrier_saison.html", {
            "request": request, "s": season, "canonical_path": path,
            "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
            "past_seasons": _past_seasons_with_data(),
        })
    raise HTTPException(status_code=404, detail="Page non trouvée")


@app.api_route("/calendrier/", methods=["GET", "HEAD"], include_in_schema=False)
async def page_calendrier_slash():
    """Redirection permanente (301 et non 307) vers l'URL canonique sans slash."""
    return RedirectResponse("/calendrier", status_code=301)


@app.get("/api/calendrier-data")
async def api_calendrier_data(month: int | None = None, year: int | None = None):
    """API JSON du calendrier mensuel (mêmes données que les pages SSR)."""
    return _get_calendrier_data(month, year)


@app.api_route("/alertes", methods=["GET", "HEAD"], response_class=HTMLResponse)
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


@app.api_route("/mentions-legales", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_legal(request: Request):
    """Page mentions légales et RGPD."""
    return templates.TemplateResponse("legal.html", {"request": request})


@app.api_route("/a-propos", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_a_propos(request: Request):
    """Page À propos — méthodologie, transparence, E-E-A-T.

    Cible SEO : renforce la confiance et l'autorité du site.
    Essentiel pour les critères E-E-A-T de Google (Experience, Expertise, Authority, Trust).
    """
    return templates.TemplateResponse("a_propos.html", {"request": request})


# ================================================================
# Pages SEO de référence (SSR, contenu issu de site_facts.py et de la DB)
# ================================================================

@app.api_route("/tarif-tempo-edf", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_tarif_tempo(request: Request):
    """Grille tarifaire Tempo EDF en vigueur (source : site_facts.TARIFS).

    Cible SEO : 'tarif tempo edf'. Rendu SSG-like (contenu statique, cache 1 h).
    """
    crumbs = [("Accueil", "/"), ("Tarif Tempo EDF", "/tarif-tempo-edf")]
    return templates.TemplateResponse("tarif_tempo.html", {
        "request": request,
        "canonical_path": "/tarif-tempo-edf",
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
    })


@app.api_route("/couleur-tempo-demain", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_couleur_demain(request: Request):
    """Couleur Tempo EDF de demain : officielle si publiée, sinon prévision signalée.

    Cible SEO : 'couleur tempo demain', 'edf tempo couleur du lendemain'.
    SSR à chaque requête (cache 2 min) : la couleur change en fin de matinée.
    """
    ssr = _get_ssr_data()
    crumbs = [("Accueil", "/"), ("Couleur Tempo demain", "/couleur-tempo-demain")]
    return templates.TemplateResponse("couleur_demain.html", {
        "request": request,
        "canonical_path": "/couleur-tempo-demain",
        "ssr": ssr,
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
    })


@app.api_route("/api-tempo", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_api_tempo(request: Request):
    """Documentation de l'API publique JSON (formats lus dans les routes /api/*)."""
    crumbs = [("Accueil", "/"), ("API Tempo EDF", "/api-tempo")]
    return templates.TemplateResponse("api_tempo.html", {
        "request": request,
        "canonical_path": "/api-tempo",
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
    })


@app.api_route("/methodologie", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_methodologie(request: Request):
    """Méthodologie de prévision et chiffres de performance, avec leur nature exacte."""
    live = None
    if _db_ready.is_set():
        try:
            from performance_tracker import get_accuracy_global
            acc = get_accuracy_global(30, min_horizon=2, max_horizon=5)
            if acc.get("total", 0) >= 10:
                live = acc
        except Exception as e:
            logger.debug(f"[Méthodologie] Mesure en direct indisponible : {e}")
    labels = {
        "temperature": "Température nationale pondérée (9 villes)",
        "jours_restants": "Jours rouges et blancs restant à placer",
        "consommation_rte": "Consommation nette prévue (RTE)",
        "pression": "Pression atmosphérique",
        "jour_semaine": "Jour de la semaine et jours fériés",
        "gradient_thermique": "Évolution de la température",
        "clustering": "Continuité (jours rouges groupés)",
    }
    weights = [
        (labels.get(k, k), round(v * 100))
        for k, v in sorted(Config.DEFAULT_WEIGHTS.items(), key=lambda kv: kv[1], reverse=True)
    ]
    cities = [c["name"] for c in Config.WEATHER_CITIES]
    # MET-01 : mêmes effectifs que le bloc « En bref » de /historique-previsions (saison en cours)
    bref, bref_season = None, None
    try:
        from prediction_history import page_context, season_start_year, today_paris, season_label
        current = season_label(season_start_year(today_paris()))
        hctx = page_context(_history_data(), current, current)
        if hctx.get("h"):
            bref, bref_season = hctx["h"]["bref"], current
    except Exception as e:
        logger.debug(f"[Méthodologie] Historique indisponible : {e}")
    crumbs = [("Accueil", "/"), ("Méthodologie", "/methodologie")]
    return templates.TemplateResponse("methodologie.html", {
        "request": request,
        "canonical_path": "/methodologie",
        "live": live,
        "bref": bref,
        "bref_season": bref_season,
        "weights": weights,
        "cities": cities,
        "measured_on": site_facts.fr_date(date.today(), with_weekday=False),
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
    })


# ================================================================
# Historique public des prévisions (prediction_history.py)
# ================================================================

def _history_data() -> dict:
    """Historique réel ; vide (jamais d'exception) si la base n'est pas prête."""
    if not _db_ready.is_set():
        return {"seasons": {}, "last_evaluation": None}
    from prediction_history import get_history
    return get_history()


def _render_history(request: Request, label: str):
    from prediction_history import page_context, season_path, season_start_year, today_paris, season_label
    current = season_label(season_start_year(today_paris()))
    ctx = page_context(_history_data(), label, current)
    path = season_path(label, current)
    crumbs = [("Accueil", "/"), ("Historique des prévisions", "/historique-previsions")]
    if path != "/historique-previsions":
        crumbs.append((f"Saison {label}", path))
    return templates.TemplateResponse("historique_previsions.html", {
        "request": request,
        "canonical_path": path,
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(crumbs),
        **ctx,
    })


@app.api_route("/historique-previsions", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_historique_previsions(request: Request):
    """Historique de nos prévisions J-2 à J-5 face aux couleurs EDF, saison en cours.

    SSR à chaque requête (cache mémoire 5 min + HTTP 10 min) : change à chaque confirmation EDF.
    """
    from prediction_history import season_start_year, today_paris, season_label
    return _render_history(request, season_label(season_start_year(today_paris())))


@app.api_route("/historique-previsions.csv", methods=["GET", "HEAD"])
async def historique_previsions_csv():
    """Export CSV complet (toutes saisons), séparateur « ; », UTF-8 avec BOM."""
    from starlette.responses import Response
    from prediction_history import to_csv_bytes
    return Response(
        content=to_csv_bytes(_history_data()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="historique-previsions-tempo.csv"'},
    )


@app.api_route("/historique-previsions/{saison}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_historique_previsions_saison(request: Request, saison: str):
    """Historique d'une saison passée ; la saison en cours redirige vers /historique-previsions."""
    from prediction_history import parse_season_label, season_start_year, today_paris
    y = parse_season_label(saison)
    current_y = season_start_year(today_paris())
    if y is None or y > current_y or y < 2000:
        raise HTTPException(status_code=404, detail="Saison inconnue")
    if y == current_y:
        return RedirectResponse("/historique-previsions", status_code=301)
    return _render_history(request, saison)


async def _render_blog_index(request: Request):
    """Render blog index page (shared by /blog and /blog/)."""
    from blog import get_published_articles, CLUSTER_LABELS
    articles = get_published_articles()
    # BLG-02 : piliers en tête, puis le reste groupé par cluster (ordre de CLUSTER_LABELS)
    pillars = [a for a in articles if a.pillar]
    rest = [a for a in articles if not a.pillar]
    groups = [(label, [a for a in rest if a.cluster == key]) for key, label in CLUSTER_LABELS.items()]
    others = [a for a in rest if a.cluster not in CLUSTER_LABELS]
    if others:
        groups.append(("Autres guides", others))
    return templates.TemplateResponse("blog_index.html", {
        "request": request,
        "articles": articles,
        "pillars": pillars,
        "groups": [(label, arts) for label, arts in groups if arts],
    })


@app.api_route("/blog", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
async def page_blog(request: Request):
    """Sert /blog directement (évite le 301 redirect)."""
    return await _render_blog_index(request)


@app.api_route("/blog/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_blog_index(request: Request):
    """Page index du blog — liste les articles publiés."""
    return await _render_blog_index(request)


@app.api_route("/blog/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def page_blog_article(request: Request, slug: str):
    """Page d'un article de blog individuel."""
    from blog import get_article_by_slug, get_published_articles
    article = get_article_by_slug(slug)
    if not article:
        raise HTTPException(status_code=404, detail="Article non trouvé")
    url = f"{site_facts.SITE_URL}/blog/{article.slug}"
    org = {
        "@type": "Organization",
        "name": site_facts.SITE_NAME,
        "url": f"{site_facts.SITE_URL}/",
        "logo": {"@type": "ImageObject", "url": f"{site_facts.SITE_URL}/static/favicon-192.png",
                 "width": 192, "height": 192},
    }
    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article.title,
        "description": article.description,
        "datePublished": article.publish_date.isoformat(),
        "dateModified": article.last_modified.isoformat(),
        "inLanguage": "fr",
        "author": {"@type": "Organization", "name": site_facts.SITE_NAME, "url": f"{site_facts.SITE_URL}/"},
        "publisher": org,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "image": {"@type": "ImageObject", "url": f"{site_facts.SITE_URL}/static/og-image.png",
                  "width": 1200, "height": 630},
    }
    if article.keywords:
        article_ld["keywords"] = article.keywords
    faq_ld = None
    if article.faq_items:
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": " ".join(a.split())}}
                for q, a in article.faq_items
            ],
        }
    # Maillage : articles du même cluster (puis les plus récents) — liens internes contextuels
    others = [a for a in get_published_articles() if a.slug != article.slug]
    related = [a for a in others if article.cluster and a.cluster == article.cluster][:4]
    if len(related) < 3:
        related += [a for a in others if a not in related][: 3 - len(related)]
    return templates.TemplateResponse("blog_article.html", {
        "request": request,
        "article": article,
        "article_ld": article_ld,
        "faq_ld": faq_ld,
        "related": related,
        "breadcrumb_ld": site_facts.breadcrumb_jsonld(
            [("Accueil", "/"), ("Blog", "/blog/"), (article.title, f"/blog/{article.slug}")]),
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
            "description": "Prévision des jours Tempo EDF : couleur du jour et 15 jours à l'avance",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#1565C0",
            "icons": [
                {"src": "/static/favicon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
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


# Browsers auto-request these at root level — serve the real file to avoid 404s
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse(
        Path(__file__).parent / "static" / "favicon-180.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Fix #S7 : robots.txt et sitemap.xml pour le SEO
# robots.txt généré par boucle : un seul bloc de règles par famille de bots,
# pas de copier-coller qui dérive. Règle de lecture (Google, Bing, RFC 9309) :
# la règle la plus longue gagne, donc « Allow: /api/today » l'emporte sur
# « Disallow: /api/ » : les bots accèdent aux endpoints publics documentés
# sur /api-tempo et à rien d'autre sous /api/ (ex. /api/indexnow/ping).
_ROBOTS_PAGES = (
    "Allow: /\n",
    "Allow: /calendrier\n",
    "Allow: /alertes\n",
    "Allow: /blog/\n",
    "Allow: /llms.txt\n",
    "Allow: /llms-full.txt\n",
    "Allow: /feed.xml\n",
)
_ROBOTS_DISALLOW = (
    "Disallow: /admin\n",
    "Disallow: /api/\n",
    "Disallow: /manage/\n",
)
# Endpoints JSON publics (documentés sur /api-tempo)
PUBLIC_API_ENDPOINTS = (
    "/api/today",
    "/api/tomorrow",
    "/api/remaining",
    "/api/predictions",
    "/api/history",
    "/api/performance/badge",
)
# Moteurs de recherche classiques avec accès aux endpoints publics
_ROBOTS_SEARCH_BOTS = (
    ("bingbot", "Bing (index web et Copilot)"),
    ("msnbot", "Bing (ancien agent)"),
)
# Agents IA : recherche / citation en direct (requêtes des utilisateurs)
_ROBOTS_AI_SEARCH_BOTS = (
    ("OAI-SearchBot", "OpenAI : index de recherche ChatGPT"),
    ("ChatGPT-User", "OpenAI : visites déclenchées par un utilisateur"),
    ("Claude-SearchBot", "Anthropic : index de recherche Claude"),
    ("Claude-User", "Anthropic : visites déclenchées par un utilisateur"),
    ("PerplexityBot", "Perplexity : index de recherche"),
    ("Perplexity-User", "Perplexity : visites déclenchées par un utilisateur"),
    ("Applebot", "Apple : Siri, Spotlight, Safari"),
    ("DuckAssistBot", "DuckDuckGo : réponses IA"),
    ("MistralAI-User", "Mistral : Le Chat, visites déclenchées par un utilisateur"),
    ("Meta-ExternalFetcher", "Meta : visites déclenchées par un utilisateur"),
    ("YouBot", "You.com"),
)
# Agents IA : collecte pour l'entraînement ou jetons de contrôle d'usage.
# Décision business : autorisés (objectif de visibilité dans les réponses IA).
_ROBOTS_AI_TRAINING_BOTS = (
    ("GPTBot", "OpenAI : entraînement"),
    ("ClaudeBot", "Anthropic : entraînement"),
    ("anthropic-ai", "Anthropic : ancien agent"),
    ("Google-Extended", "Google : jeton de contrôle d'usage pour Gemini (pas un crawler)"),
    ("GoogleOther", "Google : crawls hors recherche"),
    ("Applebot-Extended", "Apple : jeton de contrôle d'usage pour l'IA (pas un crawler)"),
    ("Meta-ExternalAgent", "Meta : entraînement"),
    ("CCBot", "Common Crawl (jeu de données utilisé par de nombreux modèles)"),
    ("cohere-ai", "Cohere"),
    ("Amazonbot", "Amazon (Alexa)"),
    ("Bytespider", "ByteDance"),
    ("Diffbot", "Diffbot"),
)


def _robots_group(user_agent: str, comment: str, crawl_delay: int | None = None) -> str:
    lines = [f"# {comment}\n", f"User-agent: {user_agent}\n"]
    lines += list(_ROBOTS_PAGES)
    lines += [f"Allow: {ep}\n" for ep in PUBLIC_API_ENDPOINTS]
    lines += list(_ROBOTS_DISALLOW)
    if crawl_delay:
        lines.append(f"Crawl-delay: {crawl_delay}\n")
    return "".join(lines)


def build_robots_txt() -> str:
    """Contenu de robots.txt (fonction pure, testée)."""
    parts = [
        "# Tous les robots : pages publiques, pas d'API\n"
        "User-agent: *\n" + "".join(_ROBOTS_PAGES) + "".join(_ROBOTS_DISALLOW)
    ]
    for ua, comment in _ROBOTS_SEARCH_BOTS:
        parts.append(_robots_group(ua, comment, crawl_delay=1))
    for ua, comment in _ROBOTS_AI_SEARCH_BOTS + _ROBOTS_AI_TRAINING_BOTS:
        parts.append(_robots_group(ua, comment))
    parts.append(f"Sitemap: {site_facts.SITE_URL}/sitemap.xml\n")
    return "\n".join(parts)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Robots.txt pour les moteurs de recherche et crawlers IA."""
    return build_robots_txt()


def _sitemap_url(path: str, lastmod: date | str, changefreq: str, priority: str) -> str:
    lm = lastmod.isoformat() if isinstance(lastmod, date) else lastmod
    return (
        "  <url>\n"
        f"    <loc>{site_facts.SITE_URL}{path}</loc>\n"
        f"    <lastmod>{lm}</lastmod>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>"
    )


def _data_lastmods() -> tuple[date | None, dict[str, date]]:
    """(date du dernier cycle de prédictions, {AAAA-MM: dernière confirmation EDF du mois}).

    Sert de lastmod réel aux pages dont le HTML change avec les données.
    """
    last_pred = None
    per_month: dict[str, date] = {}
    try:
        from database import get_db
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT MAX(timestamp_prediction) AS ts FROM predictions WHERE simulated = 0"
            ).fetchone()
            if row and row["ts"]:
                last_pred = date.fromisoformat(str(row["ts"])[:10])
            rows = conn.execute(
                "SELECT SUBSTR(date, 1, 7) AS ym, MAX(timestamp_confirmation) AS ts "
                "FROM actuals WHERE synthetic = 0 GROUP BY SUBSTR(date, 1, 7)"
            ).fetchall()
            for r in rows:
                if r["ts"]:
                    per_month[r["ym"]] = date.fromisoformat(str(r["ts"])[:10])
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[Sitemap] Dates de données indisponibles : {e}")
    return last_pred, per_month


def _history_sitemap_urls() -> list[str]:
    """Pages /historique-previsions : lastmod = dernière évaluation réelle (jamais « aujourd'hui »)."""
    from prediction_history import season_path, season_start_year, today_paris, season_label
    content_date = site_facts.PAGE_LASTMOD["/historique-previsions"]
    try:
        data = _history_data()
    except Exception as e:
        logger.debug(f"[Sitemap] Historique indisponible : {e}")
        data = {"seasons": {}}
    current = season_label(season_start_year(today_paris()))

    def lm(iso: str | None) -> date:
        try:
            return max(content_date, date.fromisoformat(iso)) if iso else content_date
        except ValueError:
            return content_date

    seasons = data.get("seasons", {})
    cur = seasons.get(current, {})
    urls = [_sitemap_url("/historique-previsions", lm(cur.get("last_evaluation") or data.get("last_evaluation")), "daily", "0.6")]
    for label in sorted(seasons, reverse=True):
        if label != current:
            urls.append(_sitemap_url(season_path(label, current), lm(seasons[label]["last_evaluation"]), "monthly", "0.4"))
    return urls


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    """Sitemap XML dynamique avec des lastmod réels.

    - Pages statiques : date de dernière modification du contenu (site_facts.PAGE_LASTMOD).
    - Pages alimentées par les données (/, /calendrier, /couleur-tempo-demain, mois,
      saisons) : max(date du contenu, dernier cycle de prédictions ou dernière
      confirmation EDF concernée). Jamais « aujourd'hui » par défaut.
    - Articles : updated_date si présent, sinon publish_date.
    - feed.xml n'est pas une page : absent du sitemap.
    """
    from blog import get_published_articles
    content_date = site_facts.LLMS_CONTENT_DATE
    last_pred, month_confirm = _data_lastmods()
    all_confirm = max(month_confirm.values()) if month_confirm else None
    live = max(d for d in (content_date, last_pred, all_confirm) if d)

    urls = [
        _sitemap_url("/", live, "daily", "1.0"),
        _sitemap_url("/calendrier", live, "daily", "0.9"),
        _sitemap_url("/couleur-tempo-demain", live, "daily", "0.8"),
        _sitemap_url("/tarif-tempo-edf", site_facts.PAGE_LASTMOD["/tarif-tempo-edf"], "monthly", "0.8"),
        _sitemap_url("/alertes", site_facts.PAGE_LASTMOD["/alertes"], "monthly", "0.8"),
        _sitemap_url("/api-tempo", site_facts.PAGE_LASTMOD["/api-tempo"], "monthly", "0.6"),
        _sitemap_url("/methodologie", site_facts.PAGE_LASTMOD["/methodologie"], "monthly", "0.6"),
        _sitemap_url("/a-propos", site_facts.PAGE_LASTMOD["/a-propos"], "monthly", "0.5"),
        *_history_sitemap_urls(),
        _sitemap_url("/mentions-legales", site_facts.PAGE_LASTMOD["/mentions-legales"], "yearly", "0.3"),
    ]
    # Pages saison (dates réelles) et pages mois (jusqu'au mois en cours)
    from tempo_client import get_season_dates
    current_start, _ = get_season_dates()
    for label in [f"{current_start.year}-{current_start.year + 1}"] + _past_seasons_with_data():
        y0 = int(label[:4])
        dates = [d for ym, d in month_confirm.items()
                 if f"{y0}-09" <= ym <= f"{y0 + 1}-08"]
        lm = max(dates + [content_date])
        urls.append(_sitemap_url(f"/calendrier/{label}", lm, "monthly", "0.7"))
    first_month, _ = _calendar_bounds()
    today = date.today()
    y, m = first_month.year, first_month.month
    while (y, m) < (today.year, today.month):
        ym = f"{y}-{m:02d}"
        lm = max(d for d in (month_confirm.get(ym), content_date) if d)
        urls.append(_sitemap_url(f"/calendrier/{ym}", lm, "monthly", "0.5"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    # Blog
    articles = get_published_articles()
    if articles:
        latest = max(a.last_modified for a in articles)
        urls.append(_sitemap_url("/blog/", latest, "weekly", "0.7"))
    for a in articles:
        urls.append(_sitemap_url(f"/blog/{a.slug}", a.last_modified, "monthly", "0.6"))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n"
        "</urlset>\n"
    )
    from starlette.responses import Response
    return Response(content=xml, media_type="application/xml; charset=utf-8")


def _llms_revision_date(articles) -> date:
    """Date réelle de dernière révision du contenu décrit (pas « aujourd'hui »)."""
    dates = [site_facts.LLMS_CONTENT_DATE, site_facts.TARIFS_DATE_EFFET]
    dates += [a.last_modified for a in articles]
    return max(dates)


def _llms_common_sections() -> str:
    """Sections factuelles communes à llms.txt et llms-full.txt (source : site_facts)."""
    from tempo_client import get_season_dates
    start, end = get_season_dates()
    t = site_facts.TARIFS
    p = site_facts.fr_price
    api_lines = "".join(
        f"- [{ep}]({site_facts.SITE_URL}{ep})\n" for ep in PUBLIC_API_ENDPOINTS
    )
    return (
        f"Saison en cours : {start.year}-{end.year} (du {site_facts.fr_date(start, with_weekday=False)} "
        f"au {site_facts.fr_date(end, with_weekday=False)}).\n"
        "\n"
        "## Règles EDF Tempo\n"
        f"- {site_facts.JOURS_ROUGES} jours rouges par saison, uniquement du 1er novembre au 31 mars\n"
        "- Jours rouges : du lundi au vendredi, jamais le week-end ni un jour férié\n"
        f"- Maximum {site_facts.MAX_ROUGES_CONSECUTIFS} jours rouges consécutifs\n"
        f"- {site_facts.JOURS_BLANCS} jours blancs par saison, jamais le dimanche\n"
        f"- {site_facts.JOURS_BLEUS} jours bleus par saison (301 quand la saison contient un 29 février)\n"
        "- Saison Tempo : du 1er septembre au 31 août\n"
        "- Un jour Tempo va de 6 h à 6 h le lendemain\n"
        f"- {site_facts.ANNONCE_J1}\n"
        "\n"
        f"## Tarifs Tempo EDF (au {site_facts.fr_date(site_facts.TARIFS_DATE_EFFET, with_weekday=False)}, TTC, €/kWh)\n"
        "\n"
        f"| Couleur | Heures pleines ({site_facts.HEURES_PLEINES}) | Heures creuses ({site_facts.HEURES_CREUSES}) |\n"
        "|---|---|---|\n"
        f"| Bleu | {p(t['BLEU']['hp'])} | {p(t['BLEU']['hc'])} |\n"
        f"| Blanc | {p(t['BLANC']['hp'])} | {p(t['BLANC']['hc'])} |\n"
        f"| Rouge | {p(t['ROUGE']['hp'])} | {p(t['ROUGE']['hc'])} |\n"
        "\n"
        f"Source : {site_facts.TARIFS_SOURCE}. Un kWh rouge en heures pleines coûte "
        f"{site_facts.fr_num(site_facts.RATIO_ROUGE_BLEU_HP)} fois un kWh bleu en heures pleines.\n"
        "\n"
        "## Sources des données\n"
        f"- Couleurs officielles : {site_facts.SOURCE_COULEURS_PHRASE}. Seule la couleur publiée par EDF fait foi.\n"
        "- Météo : Météo France (modèles AROME et ARPEGE), moyenne pondérée de 9 villes ; Open-Meteo en secours\n"
        "- Consommation d'électricité : prévisions RTE\n"
        "\n"
        "## Performance (nature exacte des chiffres)\n"
        f"- {site_facts.PERFORMANCE_POLICY}\n"
        f"- En conditions réelles : taux de prévisions correctes de {site_facts.HORIZON_FIABLE} sur 30 jours, "
        f"publié sur {site_facts.SITE_URL}/methodologie et {site_facts.SITE_URL}/api/performance/badge\n"
        "\n"
        "## API publiques (JSON, gratuites, sans clé)\n"
        f"Documentation complète : {site_facts.SITE_URL}/api-tempo\n"
        + api_lines
    )


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """LLMs.txt — standard émergent pour la découverte par les LLMs.

    Format spec : https://llmstxt.org/
    H1 (requis) → blockquote résumé → sections H2 avec listes de liens.
    """
    from blog import get_published_articles
    articles = get_published_articles()
    revision = _llms_revision_date(articles).isoformat()
    u = site_facts.SITE_URL

    blog_links = "".join(
        f"- [{a.title}]({u}/blog/{a.slug}){': ' + a.description if a.description else ''}\n"
        for a in articles
    )
    season_links = "".join(
        f"- [Calendrier Tempo {s}]({u}/calendrier/{s}): dates réelles des jours rouges et blancs de la saison {s}\n"
        for s in _past_seasons_with_data()
    )
    faq = "".join(
        f"- Q: {it['question']} R: {site_facts.html_to_text(it['answer_html'])}\n"
        for it in site_facts.FAQ_HOME
    )

    return PlainTextResponse(
        content=(
            "# Calendrier Tempo EDF\n"
            "\n"
            "> Service gratuit et indépendant (non affilié à EDF ni à RTE) : couleur Tempo EDF du jour et de demain, "
            "calendrier des saisons Tempo et prévisions des jours rouges, blancs et bleus jusqu'à J+15.\n"
            "> À notre connaissance, le seul service gratuit qui prévoit les couleurs Tempo jusqu'à J+15 "
            "(les autres services de prévision s'arrêtent entre J+7 et J+9, relevé du 29 septembre 2026).\n"
            f"> Dernière révision du contenu : {revision}\n"
            "\n"
            "L'offre Tempo EDF est un contrat d'électricité où le prix du kWh varie "
            "selon la couleur du jour : Bleu (300 jours/an, tarif bas), Blanc "
            "(43 jours/an, tarif moyen) et Rouge (22 jours/an, tarif très élevé). "
            "Notre algorithme combine les prévisions météo de 9 villes françaises "
            "(Météo France AROME + ARPEGE), la consommation nationale (RTE) et un "
            "modèle de machine learning (GradientBoosting, 33 variables) pour anticiper "
            "les couleurs. Près de 900 000 foyers étaient abonnés à l'option Tempo en juillet 2025 "
            "(données de la Commission de régulation de l'énergie, CRE).\n"
            "\n"
            + _llms_common_sections() +
            f"- [/feed.xml]({u}/feed.xml): flux RSS des articles du blog\n"
            "\n"
            "## Pages principales\n"
            f"- [Accueil]({u}/): couleur Tempo EDF aujourd'hui, demain et prévisions à 15 jours\n"
            f"- [Couleur Tempo demain]({u}/couleur-tempo-demain): couleur de demain, officielle ou prévue\n"
            f"- [Calendrier Tempo EDF]({u}/calendrier): calendrier mensuel de la saison en cours\n"
            f"- [Tarif Tempo EDF]({u}/tarif-tempo-edf): grille tarifaire en vigueur et coût d'un jour rouge\n"
            f"- [Méthodologie]({u}/methodologie): méthode de prévision et chiffres de performance\n"
            f"- [Historique des prévisions]({u}/historique-previsions): nos prévisions faites 2 à 5 jours avant, "
            "comparées jour par jour aux couleurs officielles, sans sélection (export CSV : "
            f"{u}/historique-previsions.csv)\n"
            f"- [API Tempo]({u}/api-tempo): documentation de l'API JSON gratuite\n"
            f"- [Blog]({u}/blog/): guides pour économiser avec Tempo EDF\n"
            f"- [Alertes]({u}/alertes): alertes WhatsApp gratuites : {site_facts.ALERTES_DESCRIPTION}\n"
            f"- [À propos]({u}/a-propos): présentation du service\n"
            "\n"
            "## Saisons passées (données officielles)\n"
            + season_links +
            "\n"
            "## Questions fréquentes\n"
            + faq +
            "\n"
            "## Articles du blog\n"
            + blog_links +
            "\n"
            "## Optional\n"
            f"- [Mentions légales]({u}/mentions-legales): RGPD, politique de confidentialité\n"
            f"- [llms-full.txt]({u}/llms-full.txt): version complète avec le texte Markdown de tous les articles\n"
        ),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _demote_markdown_headings(md: str) -> str:
    """Décale les titres Markdown d'un niveau (hors blocs de code) pour l'imbrication."""
    out, in_code = [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("#"):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


@app.get("/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt():
    """LLMs-full.txt — contenu complet en Markdown brut (tableaux intacts).

    Faits essentiels, FAQ, puis le Markdown source de chaque article publié.
    """
    from blog import get_published_articles
    articles = get_published_articles()
    revision = _llms_revision_date(articles).isoformat()
    u = site_facts.SITE_URL

    faq = "".join(
        f"### {it['question']}\n\n{site_facts.html_to_text(it['answer_html'])}\n\n"
        for it in site_facts.FAQ_HOME
    )
    articles_content = ""
    for a in articles:
        updated = f" (mis à jour le {a.updated_date.isoformat()})" if a.updated_date else ""
        articles_content += (
            "\n---\n\n"
            f"## {a.title}\n\n"
            f"URL : {u}/blog/{a.slug}\n"
            f"Publié le {a.publish_date.isoformat()}{updated}\n\n"
            f"{_demote_markdown_headings(a.body_md)}\n"
        )

    return PlainTextResponse(
        content=(
            "# Calendrier Tempo EDF : contenu complet\n"
            "\n"
            "> Faits essentiels, FAQ et texte intégral (Markdown) des articles publiés sur calendrier-tempo.fr.\n"
            "> Résumé structuré : /llms.txt. Données du jour : /api/today, /api/tomorrow.\n"
            f"> Dernière révision du contenu : {revision}\n"
            "\n"
            + _llms_common_sections() +
            "\n"
            "## Questions fréquentes\n\n"
            + faq +
            "## Articles du blog\n"
            + articles_content
        ),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/feed.xml")
async def rss_feed():
    """Flux RSS des articles du blog — enrichi avec content:encoded et categories."""
    from blog import get_published_articles
    import html as html_mod
    from email.utils import format_datetime
    articles = get_published_articles()

    def _rfc822(d: date) -> str:
        # Minuit heure de Paris, fuseau réel (+0100 l'hiver, +0200 l'été)
        return format_datetime(datetime(d.year, d.month, d.day, tzinfo=_PARIS_TZ))

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
            f"      <pubDate>{_rfc822(a.publish_date)}</pubDate>\n"
            f"      <guid isPermaLink=\"true\">https://www.calendrier-tempo.fr/blog/{a.slug}</guid>\n"
            f"      <author>contact@calendrier-tempo.fr (Calendrier Tempo EDF)</author>\n"
            f"{category}"
            "    </item>"
        )
    last_build = _rfc822(max(a.last_modified for a in articles)) if articles else _rfc822(site_facts.LLMS_CONTENT_DATE)
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
    from starlette.responses import Response
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


# === SEO : IndexNow protocol — notification instantanée Bing/Yandex ===
_INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "calendrier-tempo-indexnow-key")


@app.get(f"/{_INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key_file():
    """Fichier de vérification IndexNow (requis par le protocole)."""
    return PlainTextResponse(content=_INDEXNOW_KEY, media_type="text/plain")


@app.get("/api/indexnow/ping")
async def indexnow_ping(request: Request, url: str | None = None,
                        authorization: str | None = Header(None)):
    """Ping IndexNow pour notifier Bing/Yandex d'une mise à jour (admin uniquement).

    Usage admin : GET /api/indexnow/ping?url=https://www.calendrier-tempo.fr/blog/slug
    avec le header Authorization: Bearer <mot de passe admin>.
    Si url absent, notifie les pages principales. Seules les URL du site sont acceptées.
    """
    verify_admin(authorization, request.client.host if request.client else "unknown")
    import httpx

    host = "www.calendrier-tempo.fr"
    if url:
        if not url.startswith(f"https://{host}/"):
            raise HTTPException(status_code=400, detail="URL hors du site")
        urls_to_submit = [url]
    else:
        urls_to_submit = [
            f"https://{host}/",
            f"https://{host}/calendrier",
            f"https://{host}/couleur-tempo-demain",
            f"https://{host}/tarif-tempo-edf",
            f"https://{host}/blog/",
            f"https://{host}/alertes",
            f"https://{host}/a-propos",
            f"https://{host}/methodologie",
            f"https://{host}/api-tempo",
        ]

    results = []
    key_location = f"https://{host}/{_INDEXNOW_KEY}.txt"
    async with httpx.AsyncClient(timeout=10) as client:
        # Bulk POST — plus fiable que les GET individuels
        try:
            resp = await client.post(
                "https://api.indexnow.org/indexnow",
                json={
                    "host": host,
                    "key": _INDEXNOW_KEY,
                    "keyLocation": key_location,
                    "urlList": urls_to_submit,
                },
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            results.append({"urls": urls_to_submit, "status": resp.status_code})
        except Exception as e:
            results.append({"urls": urls_to_submit, "error": str(e)})

    return {"submitted": len(urls_to_submit), "results": results}


# ================================================================
# Fix #24 : HEALTHCHECK
# ================================================================

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Health check endpoint — répond 200 immédiatement (Cloud Run startup probe).

    Retourne le statut DB pour le monitoring, mais ne bloque pas le démarrage.
    """
    db_ok = False
    if _db_ready.is_set():
        try:
            from database import get_db
            conn = get_db()
            try:
                conn.execute("SELECT 1").fetchone()
                db_ok = True
            finally:
                conn.close()
        except Exception:
            pass
    status = "ok" if db_ok else "starting"
    return {"status": status, "db": db_ok, "timestamp": datetime.now().isoformat()}


@app.get("/keepalive")
async def keepalive():
    """Endpoint pour services de keepalive externes (UptimeRobot, cron-job.org).

    Résilience Autoscale : maintient l'instance éveillée et déclenche
    le rattrapage des tâches manquées si nécessaire (cooldown 5 min).

    Configurer un service externe pour pinger cette URL toutes les 5 min
    entre 6h et 23h (heure Paris) afin de garantir l'exécution des
    tâches planifiées par APScheduler.
    """
    recovered = []
    scheduler_running = False

    if _db_ready.is_set():
        try:
            from scheduler import check_and_recover, scheduler as _scheduler
            scheduler_running = _scheduler.running
            recovered = await check_and_recover()
        except Exception as e:
            logger.debug(f"[Keepalive] Erreur recovery: {e}")

    return {
        "status": "alive",
        "scheduler": scheduler_running,
        "recovered": recovered,
        "timestamp": _now_paris().isoformat(),
    }


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
        from performance_tracker import evaluate_predictions_for_date

        store_actual(date_str, couleur)

        # Évaluer AVANT la confirmation (même ordre que le scheduler 11h30)
        # Garantit que les prédictions sont évaluées même si le scheduler échoue
        try:
            evaluate_predictions_for_date(
                date.fromisoformat(date_str), couleur
            )
        except Exception as e:
            logger.warning(f"[API→DB] Évaluation échouée pour {date_str}: {e}")

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
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ? AND synthetic = 0 ORDER BY date",
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
                          season: str | None = None):
    """Métriques de performance complètes (admin).

    Args:
        season: saison au format "YYYY-YYYY" (ex: "2025-2026"). Absente = saison en cours.
    """
    verify_admin(authorization, request.client.host if request.client else "unknown")
    import re as _re
    if season is not None and not _re.fullmatch(r"\d{4}-\d{4}", season):
        raise HTTPException(status_code=400, detail="Saison invalide (format AAAA-AAAA)")
    from performance_tracker import get_performance_summary
    return {"status": "ok", **get_performance_summary(season=season)}


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
        "label": f"Nos prévisions J+2 à J+5 : {acc['precision']}% de prévisions correctes sur {acc['total']} évaluations (30 derniers jours)"
                 if acc["total"] >= min_evaluations
                 else "Taux de réussite en cours de calcul : pas encore assez de données",
    }


@app.get("/api/performance/csv")
async def api_performance_csv(request: Request, month: int | None = None, year: int | None = None,
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
    delai: int = Form(3),
    alerte_blanc: bool = Form(False),
    recap_hebdo: bool = Form(True),
    heure_envoi: str = Form("matin"),
    website: str = Form(""),
    t: str = Form(""),
):
    """Inscription aux alertes WhatsApp (Fix #16 : rate limiting + CSRF check)."""
    # Fix #16 (CSRF) : verify origin
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    # Anti-bot: honeypot field must be empty (bots auto-fill hidden fields)
    if website:
        logger.warning(f"[Subscribe] Honeypot triggered from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(status_code=400, detail="Inscription impossible.")

    # Anti-bot: JS timestamp check (form submitted too fast = bot)
    if t:
        try:
            open_ts = int(t)
            elapsed = int(time.time()) - open_ts
            if elapsed < 3:
                logger.warning(f"[Subscribe] Speed check failed ({elapsed}s) from {request.client.host if request.client else 'unknown'}")
                raise HTTPException(status_code=400, detail="Trop rapide. Veuillez réessayer.")
        except ValueError:
            pass  # Invalid timestamp, skip check

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
    heure_envoi = heure_envoi if heure_envoi in ("matin", "soir") else "matin"

    from alerts import register_user, send_welcome
    result = register_user(phone, seuil_rouge, delai, alerte_blanc, recap_hebdo,
                           heure_envoi)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    # Ajouter le lien de gestion dans la réponse
    if result.get("manage_token"):
        result["manage_url"] = f"/manage/{result['manage_token']}"

    # #4 : Envoyer un message de bienvenue WhatsApp immédiatement
    if result.get("success") and result.get("phone"):
        try:
            await asyncio.to_thread(
                send_welcome, result["phone"], result.get("manage_token", "")
            )
        except Exception as e:
            logger.warning(f"[Subscribe] Erreur envoi bienvenue: {e}")

    # Ne pas exposer le numéro dans la réponse JSON
    result.pop("phone", None)
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

    # H-06 QA : validation format téléphone (international)
    phone_clean = phone.strip().replace(" ", "").replace("-", "").replace(".", "")
    if not phone_clean.startswith("+") or len(phone_clean) < 10 or len(phone_clean) > 15 or not phone_clean[1:].isdigit():
        raise HTTPException(status_code=400, detail="Format invalide. Utilisez un format international (+33, +32, +41...).")

    from alerts import unsubscribe_user
    # Numéro nettoyé (points, tirets, espaces) : même hash qu'à l'inscription
    unsubscribe_user(phone_clean)
    # Réponse identique que le numéro soit inscrit ou non : ne pas révéler
    # l'existence d'un abonnement (QA 2026-09-29, énumération des abonnés).
    return {"success": True,
            "message": "Si ce numéro était inscrit, la désinscription est effectuée."}


@app.post("/api/resend-manage-link")
async def api_resend_manage_link(
    request: Request, bg: BackgroundTasks, phone: str = Form(...)
):
    """Renvoie le lien de gestion par WhatsApp (pour les utilisateurs déjà inscrits).

    Répond immédiatement, l'envoi WhatsApp se fait en arrière-plan pour éviter
    les timeouts (send_whatsapp peut prendre 30-45s avec les retries).
    """
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    # Rate limiting (same pool as subscribe)
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

    phone_clean = phone.strip().replace(" ", "").replace("-", "").replace(".", "")
    if not phone_clean.startswith("+") or len(phone_clean) < 10 or len(phone_clean) > 15:
        raise HTTPException(status_code=400, detail="Format invalide.")

    # Generic message to avoid revealing if number exists (privacy)
    generic_msg = "Si ce numéro est inscrit, vous recevrez un message WhatsApp avec votre lien de gestion."

    from alerts import hash_phone
    from database import get_db
    phone_h = hash_phone(phone_clean)
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT manage_token, actif FROM users WHERE phone_hash = ?", (phone_h,)
        ).fetchone()
    finally:
        conn.close()

    if user and user["actif"]:
        # Envoi via template (fonctionne hors fenêtre 24h, contrairement aux messages texte)
        manage_url = f"https://www.calendrier-tempo.fr/manage/{user['manage_token']}"

        def _send_manage_link():
            try:
                from alerts import (KIND_TRANSACTIONAL, _build_manage_link_template,
                                    send_whatsapp_template)
                tpl_name, tpl_components = _build_manage_link_template(manage_url)
                # Transactionnel : demandé par la personne, jamais retenu en mode test
                send_whatsapp_template(phone_clean, tpl_name, tpl_components,
                                       kind=KIND_TRANSACTIONAL)
            except Exception as e:
                logger.warning(f"[ResendManage] Erreur envoi: {e}")

        bg.add_task(_send_manage_link)

    return {"success": True, "message": generic_msg}


@app.get("/api/webhook/whatsapp")
async def whatsapp_webhook_verify(request: Request):
    """Vérification du webhook Meta WhatsApp (challenge handshake).

    Meta envoie un GET avec hub.mode, hub.verify_token, hub.challenge.
    On répond avec le challenge si le verify_token correspond.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == Config.WHATSAPP_VERIFY_TOKEN:
        logger.info("[WhatsApp Webhook] Vérification réussie")
        return PlainTextResponse(content=challenge or "")
    logger.warning("[WhatsApp Webhook] Vérification échouée (token invalide)")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/webhook/whatsapp")
async def whatsapp_webhook_incoming(request: Request):
    """Webhook Meta WhatsApp pour messages entrants + statuts de livraison.

    Meta envoie le payload JSON avec les messages reçus ET les statuts
    de livraison (sent/delivered/read/failed).
    BUG-01 QA : endpoint pour l'opt-out par message.
    """
    from alerts import handle_incoming_sms
    from database import get_db

    # C-1 Sécurité : vérification de la signature HMAC-SHA256 Meta
    raw_body = await request.body()
    app_secret = Config.WHATSAPP_APP_SECRET
    if app_secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            app_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            logger.warning("[WhatsApp Webhook] Signature invalide — requête rejetée")
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        logger.warning(
            "[WhatsApp Webhook] WHATSAPP_APP_SECRET non configuré — "
            "vérification de signature désactivée (S1: risque de sécurité en production)"
        )

    import json as _json
    try:
        payload = _json.loads(raw_body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Structure: { "entry": [{ "changes": [{ "value": { "messages": [...], "statuses": [...] } }] }] }
    messages_processed = 0
    statuses_processed = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # --- Messages entrants (STOP/START/RECAP) ---
            for msg in value.get("messages", []):
                from_number = msg.get("from", "")
                body = ""
                if msg.get("type") == "text":
                    body = msg.get("text", {}).get("body", "")
                elif msg.get("type") == "button":
                    body = msg.get("button", {}).get("text", "")

                if from_number and body:
                    reply = handle_incoming_sms(from_number, body)
                    messages_processed += 1
                    # B1 fix: envoyer la réponse au user via WhatsApp
                    if reply:
                        from alerts import KIND_TRANSACTIONAL, send_whatsapp
                        phone_for_reply = from_number
                        if not phone_for_reply.startswith("+"):
                            phone_for_reply = f"+{phone_for_reply}"
                        # Réponse du bot (STOP/START/RECAP) : transactionnelle
                        send_whatsapp(phone_for_reply, reply, kind=KIND_TRANSACTIONAL)

            # --- Statuts de livraison (sent/delivered/read/failed) ---
            for status in value.get("statuses", []):
                msg_id = status.get("id", "")
                delivery_status = status.get("status", "")
                recipient = status.get("recipient_id", "")
                errors = status.get("errors", [])
                statuses_processed += 1

                if delivery_status == "failed":
                    error_detail = errors[0] if errors else {}
                    error_code = error_detail.get("code", "?")
                    error_title = error_detail.get("title", "unknown")
                    logger.error(
                        f"[WhatsApp Status] ÉCHEC livraison → ****{recipient[-4:]}: "
                        f"code={error_code}, {error_title}"
                    )
                    # Mettre à jour sms_logs pour marquer l'échec
                    # S3 fix: conn.close() dans finally pour éviter les fuites
                    conn = None
                    try:
                        conn = get_db()
                        if msg_id:
                            conn.execute(
                                "UPDATE sms_logs SET statut = ?, erreur = ? WHERE whatsapp_msg_id = ?",
                                ("failed", f"{error_code}: {error_title}", msg_id),
                            )
                        # Désactiver l'utilisateur si le numéro est invalide
                        # Codes 131026 (recipient not on WhatsApp), 131047 (re-engagement limit)
                        if str(error_code) in ("131026", "131047") and recipient:
                            from alerts import hash_phone
                            phone_normalized = "+" + recipient if not recipient.startswith("+") else recipient
                            ph = hash_phone(phone_normalized)
                            conn.execute(
                                "UPDATE users SET actif = 0, updated_at = ? WHERE phone_hash = ? AND actif = 1",
                                (_now_paris().isoformat(), ph),
                            )
                            # U5: log explicite (impossible d'envoyer WhatsApp à un numéro en erreur)
                            logger.warning(
                                f"[WhatsApp Status] User ****{recipient[-4:]} désactivé automatiquement "
                                f"(code {error_code}: {error_title}). L'user ne sera pas notifié."
                            )
                        conn.commit()
                    except Exception as e:
                        logger.debug(f"[WhatsApp Status] Erreur MAJ sms_logs: {e}")
                    finally:
                        if conn:
                            conn.close()
                elif delivery_status == "delivered":
                    logger.info(f"[WhatsApp Status] Délivré → ****{recipient[-4:]}")
                elif delivery_status == "read":
                    logger.info(f"[WhatsApp Status] Lu → ****{recipient[-4:]}")
                elif delivery_status == "sent":
                    logger.debug(f"[WhatsApp Status] Envoyé → ****{recipient[-4:]}")

    if messages_processed or statuses_processed:
        logger.info(
            f"[WhatsApp Webhook] {messages_processed} message(s), "
            f"{statuses_processed} statut(s) traité(s)"
        )
    return JSONResponse(content={"status": "ok"}, status_code=200)


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
        "heure_envoi": user.get("heure_envoi", "matin"),
    }


@app.post("/api/manage/{token}")
async def api_manage_update(
    request: Request,
    token: str,
    seuil_rouge: int = Form(70),
    delai: int = Form(3),
    alerte_blanc: bool = Form(False),
    recap_hebdo: bool = Form(False),
    heure_envoi: str = Form("matin"),
):
    """Met à jour les préférences via le token de gestion."""
    # CSRF check
    if not _check_origin(request):
        raise HTTPException(status_code=403, detail="Origine de la requête non autorisée")

    # Valider les paramètres
    seuil_rouge = max(0, min(100, seuil_rouge))
    delai = max(1, min(3, delai))
    heure_envoi = heure_envoi if heure_envoi in ("matin", "soir") else "matin"

    from alerts import update_user_preferences
    result = update_user_preferences(token, seuil_rouge, delai, alerte_blanc,
                                     recap_hebdo, heure_envoi)
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
        return JSONResponse(
            status_code=500,
            content={"status": "error", "result": f"Erreur d'exécution: {e}"},
        )


@app.get("/admin/scheduler-status")
async def admin_scheduler_status(request: Request, authorization: str | None = Header(None)):
    """Etat des taches planifiees du scheduler."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from scheduler import scheduler as _scheduler

    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run": next_run.isoformat() if next_run else None,
            "pending": next_run is not None,
        })
    jobs.sort(key=lambda j: j["next_run"] or "9999")
    # Diagnostic lecture seule : volume et fraîcheur des archives de prévisions
    try:
        from database import get_forecast_log_stats
        forecast_logs = await asyncio.to_thread(get_forecast_log_stats)
    except Exception as e:
        logger.warning(f"[Admin] Stats archives de prévisions indisponibles: {e}")
        forecast_logs = None
    return {"status": "ok", "jobs": jobs, "running": _scheduler.running,
            "forecast_logs": forecast_logs}


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
            """SELECT id, type_alerte, couleur, message_body, date_envoi,
                      statut, whatsapp_msg_id, erreur, date_cible
               FROM sms_logs ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
        return {"status": "ok", "logs": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/admin/whatsapp-diagnostic")
async def admin_whatsapp_diagnostic(request: Request, authorization: str | None = Header(None)):
    """Diagnostic des templates WhatsApp — vérifie la cohérence code↔Meta.

    Liste chaque template avec le nom, le nombre de paramètres envoyés par le code,
    et le statut de la configuration Meta (token présent, numéro configuré).
    """
    verify_admin(authorization, request.client.host if request.client else "unknown")

    from alerts import _is_whatsapp_configured, whatsapp_test_numbers

    templates = [
        {"name": Config.WHATSAPP_TEMPLATE_WELCOME, "usage": "Bienvenue", "body_params": 2,
         "params": ["{{1}} prévisions 5j", "{{2}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_ALERT_ROUGE, "usage": "Alerte rouge", "body_params": 4,
         "params": ["{{1}} date", "{{2}} proba%", "{{3}} temp°C", "{{4}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_ALERT_BLANC, "usage": "Alerte blanc", "body_params": 3,
         "params": ["{{1}} date", "{{2}} proba%", "{{3}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_CONFIRMATION, "usage": "Confirmation EDF", "body_params": 5,
         "params": ["{{1}} emoji", "{{2}} couleur", "{{3}} date", "{{4}} conseil", "{{5}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_CHANGE, "usage": "Changement prédiction", "body_params": 5,
         "params": ["{{1}} date", "{{2}} ancienne couleur", "{{3}} nouvelle couleur", "{{4}} conseil", "{{5}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_RECAP, "usage": "Récap hebdomadaire", "body_params": 3,
         "params": ["{{1}} prévisions 7j", "{{2}} résumé", "{{3}} URL gestion"]},
        {"name": Config.WHATSAPP_TEMPLATE_MANAGE_LINK, "usage": "Lien de gestion (renvoi)", "body_params": 1,
         "params": ["{{1}} URL gestion"]},
    ]

    # Dernier statut d'envoi par template depuis sms_logs
    recent_errors = {}
    try:
        from database import get_db
        conn = get_db()
        try:
            for tpl in templates:
                row = conn.execute(
                    """SELECT statut, erreur, date_envoi FROM sms_logs
                       WHERE statut LIKE 'error%'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                if row:
                    recent_errors["last_error"] = dict(row)
        finally:
            conn.close()
    except Exception:
        pass

    return {
        "status": "ok",
        "configured": _is_whatsapp_configured(),
        "api_version": Config.WHATSAPP_API_VERSION,
        "language": Config.WHATSAPP_TEMPLATE_LANG,
        "phone_number_id": bool(Config.WHATSAPP_PHONE_NUMBER_ID),
        "token_set": bool(Config.WHATSAPP_TOKEN),
        # Mode test (WHATSAPP_TEST_NUMBERS) : jamais les numéros eux-mêmes
        "test_mode": bool(whatsapp_test_numbers()),
        "test_numbers_count": len(whatsapp_test_numbers()),
        "templates": templates,
        "recent_errors": recent_errors,
        "note": "Chaque template Meta doit avoir EXACTEMENT le nombre de {{body}} params "
                "indiqué ci-dessus. Si un template a 3 params dans Meta mais que le code en envoie 4, "
                "Meta retourne erreur 132018 (parameter count mismatch).",
    }


@app.get("/admin/db-diagnostic")
async def admin_db_diagnostic(request: Request, authorization: str | None = Header(None)):
    """Diagnostic rapide de l'état des données en base.

    Permet de vérifier si les predictions, actuals et performance
    existent pour les dates récentes. Utile pour déboguer les problèmes
    de données manquantes en production.
    """
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from database import get_db

    conn = get_db()
    try:
        # 1. Predictions: count by date (last 15 days)
        pred_rows = conn.execute(
            """SELECT date, COUNT(*) as cnt, MIN(horizon) as min_h, MAX(horizon) as max_h,
                      MAX(confirmed) as any_confirmed, MAX(simulated) as any_simulated
               FROM predictions
               WHERE date >= ? AND date <= ?
               GROUP BY date ORDER BY date DESC""",
            ((date.today() - timedelta(days=15)).isoformat(),
             (date.today() + timedelta(days=15)).isoformat()),
        ).fetchall()

        # 2. Actuals: recent entries
        actual_rows = conn.execute(
            """SELECT date, couleur_reelle, synthetic
               FROM actuals
               WHERE date >= ?
               ORDER BY date DESC""",
            ((date.today() - timedelta(days=15)).isoformat(),),
        ).fetchall()

        # 3. Performance: count evaluations
        perf_rows = conn.execute(
            """SELECT date_cible, COUNT(*) as cnt,
                      SUM(correct) as corrects
               FROM performance
               WHERE date_cible >= ?
               GROUP BY date_cible ORDER BY date_cible DESC""",
            ((date.today() - timedelta(days=15)).isoformat(),),
        ).fetchall()

        # 4. Global stats
        stats = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM predictions WHERE simulated = 0) as pred_total,
                 (SELECT MIN(date) FROM predictions WHERE simulated = 0) as pred_min_date,
                 (SELECT MAX(date) FROM predictions WHERE simulated = 0) as pred_max_date,
                 (SELECT COUNT(*) FROM actuals WHERE synthetic = 0) as actual_total,
                 (SELECT MIN(date) FROM actuals WHERE synthetic = 0) as actual_min_date,
                 (SELECT MAX(date) FROM actuals WHERE synthetic = 0) as actual_max_date,
                 (SELECT COUNT(*) FROM performance) as perf_total,
                 (SELECT MIN(date_cible) FROM performance) as perf_min_date,
                 (SELECT MAX(date_cible) FROM performance) as perf_max_date"""
        ).fetchone()

        return {
            "status": "ok",
            "prediction_start_date": Config.PREDICTION_START_DATE,
            "today": date.today().isoformat(),
            "global_stats": dict(stats),
            "predictions_by_date": [dict(r) for r in pred_rows],
            "actuals_recent": [dict(r) for r in actual_rows],
            "performance_by_date": [dict(r) for r in perf_rows],
        }
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


@app.get("/admin/validate-articles")
async def admin_validate_articles(request: Request, authorization: str | None = Header(None)):
    """Valide tous les articles de blog via validate_article.py."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    import pathlib
    from validate_article import validate

    articles_dir = pathlib.Path(__file__).parent / "articles"
    results = []
    for md_file in sorted(articles_dir.glob("*.md")):
        if md_file.name.startswith("_"):
            continue
        try:
            ret = validate(str(md_file))
            if isinstance(ret, tuple):
                errors, warnings = ret
            else:
                errors, warnings = ret, []
            results.append({
                "filename": md_file.name,
                "errors": errors,
                "warnings": warnings,
                "valid": len(errors) == 0,
            })
        except Exception as e:
            results.append({
                "filename": md_file.name,
                "errors": [f"Exception: {e}"],
                "warnings": [],
                "valid": False,
            })
    total = len(results)
    valid = sum(1 for r in results if r["valid"])
    return {"status": "ok", "total": total, "valid": valid, "articles": results}


@app.get("/admin/subscribers")
async def admin_subscribers(request: Request, authorization: str | None = Header(None)):
    """Liste des abonnés WhatsApp (4 derniers chiffres uniquement)."""
    verify_admin(authorization, request.client.host if request.client else "unknown")
    from database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, phone_last4, seuil_alerte_rouge, delai_alerte,
                      alerte_blanc, recap_hebdo, heure_envoi, actif, created_at
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
