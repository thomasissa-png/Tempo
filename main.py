"""Point d'entrée — Proxy ASGI ultra-léger pour health check Replit.

Fix #42 : Le problème fondamental est que l'import de FastAPI +
Pydantic + Starlette + Jinja2 prend plusieurs secondes. Pendant
ce temps, uvicorn ne peut pas encore accepter de requêtes HTTP,
et le health check Replit Cloud Run timeout.

Solution : ce proxy ASGI démarre en < 100ms (aucun import lourd)
et sert des réponses 200 instantanées. L'app FastAPI se charge
en arrière-plan dans un thread, puis toutes les requêtes lui sont
déléguées transparentement.

Flux :
1. uvicorn charge main:app (proxy) → serveur prêt en < 100ms
2. Health check Replit → 200 OK instantané
3. En arrière-plan : import app.py (FastAPI + deps) ~3-8s
4. Une fois chargé : toutes les requêtes → FastAPI
"""

import asyncio
import logging
import mimetypes
import os
import threading

logger = logging.getLogger("main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# --- Pré-chargement des fichiers statiques (< 1ms, aucun import lourd) ---
_base_dir = os.path.dirname(os.path.abspath(__file__))


def _preload(relpath):
    """Lit un fichier au démarrage pour le servir instantanément."""
    try:
        with open(os.path.join(_base_dir, relpath), "rb") as f:
            return f.read()
    except Exception:
        return None


_dashboard_html = _preload("templates/dashboard.html")
_static_files = {
    "/static/css/style.css": _preload("static/css/style.css"),
    "/static/js/app.js": _preload("static/js/app.js"),
}

# --- État global du proxy ---
_real_app = None
_event_loop = None


async def app(scope, receive, send):
    """ASGI callable — proxy vers FastAPI avec fallback health check."""
    global _event_loop

    if scope["type"] == "lifespan":
        await _handle_lifespan(scope, receive, send)
        return

    # Déléguer à FastAPI si chargé
    if _real_app is not None:
        await _real_app(scope, receive, send)
        return

    # Fallback : réponse minimale pendant le chargement
    if scope["type"] == "http":
        await _serve_loading_response(scope, send)


async def _handle_lifespan(scope, receive, send):
    """Gère le protocole lifespan ASGI."""
    global _event_loop

    message = await receive()
    if message["type"] == "lifespan.startup":
        # Capturer la boucle pour l'utiliser depuis le thread de chargement
        _event_loop = asyncio.get_running_loop()

        # Charger l'app réelle en arrière-plan
        threading.Thread(target=_load_real_app, daemon=True).start()

        # Répondre immédiatement — le serveur HTTP démarre MAINTENANT
        await send({"type": "lifespan.startup.complete"})
        logger.info("[Proxy] Serveur prêt — health check actif, chargement FastAPI en cours")

    # Attendre le signal de shutdown
    message = await receive()
    if message["type"] == "lifespan.shutdown":
        try:
            from scheduler import stop_scheduler
            stop_scheduler()
        except Exception:
            pass
        await send({"type": "lifespan.shutdown.complete"})
        logger.info("[Proxy] Shutdown terminé")


async def _serve_loading_response(scope, send):
    """Sert le vrai dashboard pendant le chargement de FastAPI.

    Au lieu d'une page minimale 'Chargement en cours...', on sert
    directement le dashboard HTML + CSS + JS. L'interface apparaît
    immédiatement ; les appels API échouent gracieusement (le JS
    affiche des boutons Réessayer) puis fonctionnent dès que FastAPI
    est prêt.
    """
    path = scope.get("path", "/")

    if path == "/health":
        body = b'{"status":"starting","detail":"FastAPI loading"}'
        content_type = b"application/json"
        status = 200
    elif path.startswith("/api/"):
        # Les appels JS reçoivent un 503 propre → le JS affiche
        # "Données non disponibles" ou "Réessayer"
        body = b'{"status":"starting","detail":"Serveur en cours de demarrage"}'
        content_type = b"application/json"
        status = 503
    elif path in _static_files and _static_files[path]:
        body = _static_files[path]
        ct = mimetypes.guess_type(path)[0] or "application/octet-stream"
        content_type = ct.encode()
        status = 200
    elif _dashboard_html:
        body = _dashboard_html
        content_type = b"text/html; charset=utf-8"
        status = 200
    else:
        # Fallback si le fichier n'a pas pu être lu
        body = (
            b"<!DOCTYPE html><html><head>"
            b"<meta charset='utf-8'>"
            b"<meta http-equiv='refresh' content='3'>"
            b"<title>Calendrier Tempo EDF</title>"
            b"<style>body{font-family:sans-serif;text-align:center;"
            b"padding:50px;color:#333}</style>"
            b"</head><body>"
            b"<h1>Calendrier Tempo EDF</h1>"
            b"<p>Chargement en cours...</p>"
            b"</body></html>"
        )
        content_type = b"text/html; charset=utf-8"
        status = 200

    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", content_type],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


def _load_real_app():
    """Charge l'app FastAPI dans un thread séparé puis déclenche le startup."""
    global _real_app

    try:
        logger.info("[Proxy] Import de l'app FastAPI...")

        # Cet import charge FastAPI + Pydantic + Starlette + Jinja2 + config + database
        from app import app as fastapi_app, _deferred_startup
        from config import Config

        # Reproduire la logique du lifespan FastAPI (qui ne sera pas appelé
        # par uvicorn car c'est le proxy qui gère le lifespan)
        if not Config.ADMIN_PASSWORD:
            import secrets
            Config.ADMIN_PASSWORD = secrets.token_urlsafe(24)
            logger.warning(
                "[Proxy] ADMIN_PASSWORD non défini — mot de passe généré : %s",
                Config.ADMIN_PASSWORD,
            )

        # Déclencher le startup (init_db, scheduler, etc.) dans l'event loop uvicorn
        if _event_loop is not None:
            asyncio.run_coroutine_threadsafe(_deferred_startup(), _event_loop)

        # Activer la délégation — à partir de maintenant, toutes les requêtes
        # vont à FastAPI (y compris les routes, middleware, static files, templates)
        _real_app = fastapi_app
        logger.info("[Proxy] App FastAPI chargée — toutes les requêtes sont déléguées")

    except Exception as e:
        logger.error("[Proxy] Erreur chargement FastAPI: %s", e, exc_info=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        reload=False,
        log_level="info",
    )
