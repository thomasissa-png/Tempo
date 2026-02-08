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

import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, PlainTextResponse
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

# === Fix #16 : Rate limiting simple pour /api/subscribe ===
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


# === Lifespan ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialisation au démarrage, nettoyage à l'arrêt."""
    logger.info("=== TempoForecast démarrage ===")
    init_db()
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
    if password != Config.ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Mot de passe admin incorrect")


# ================================================================
# PAGES HTML
# ================================================================

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    """Page principale — dashboard des prévisions."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    """Dashboard admin — performance et gestion."""
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/mentions-legales", response_class=HTMLResponse)
async def page_legal(request: Request):
    """Page mentions légales et RGPD."""
    return templates.TemplateResponse("legal.html", {"request": request})


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
    from tempo_client import get_remaining_days, days_left_in_season, get_season_dates
    remaining = get_remaining_days()
    start, end = get_season_dates()
    return {
        "status": "ok",
        "remaining": remaining,
        "days_left_in_season": days_left_in_season(),
        "season_start": start.isoformat(),
        "season_end": end.isoformat(),
    }


# ================================================================
# API : PRÉDICTIONS
# ================================================================

@app.get("/api/predictions")
async def api_predictions():
    """Génère et retourne les prédictions J+1 → J+15 (cache TTL 15min)."""
    now = time.time()

    # Fix #2 : utiliser le cache si encore valide
    if _predictions_cache["data"] and now < _predictions_cache["expires"]:
        return _predictions_cache["data"]

    from weather_client import fetch_forecast_extended
    from predictor import predict_range
    from performance_tracker import get_accuracy_global
    from rte_client import get_consumption_score

    forecasts = await fetch_forecast_extended()
    rte_score = await get_consumption_score()
    predictions = predict_range(forecasts, rte_score=rte_score)

    # Badge de fiabilité
    accuracy = get_accuracy_global(30)

    result = {
        "status": "ok",
        "predictions": predictions,
        "accuracy": accuracy,
        "generated_at": datetime.now().isoformat(),
    }

    _predictions_cache["data"] = result
    _predictions_cache["expires"] = now + Config.PREDICTIONS_CACHE_TTL

    return result


@app.get("/api/history")
async def api_history(days: int = 30):
    """Historique des couleurs réelles des N derniers jours."""
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
    """Badge de fiabilité simplifié pour la homepage."""
    from performance_tracker import get_accuracy_global
    acc = get_accuracy_global(30)
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
    """Inscription aux alertes SMS (Fix #16 : rate limiting)."""
    # Rate limiting par IP
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = Config.SUBSCRIBE_RATE_WINDOW
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < window
    ]
    if len(_rate_limit_store[client_ip]) >= Config.SUBSCRIBE_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez plus tard.")
    _rate_limit_store[client_ip].append(now)

    from alerts import register_user
    result = register_user(phone, seuil_rouge, delai, alerte_blanc, recap_hebdo)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/unsubscribe")
async def api_unsubscribe(phone: str = Form(...)):
    """Désinscription des alertes SMS."""
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
    """Exécute une tâche du scheduler manuellement."""
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
