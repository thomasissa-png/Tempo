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

# python:3.12-slim n'a pas /etc/mime.types : sans ça, les polices partent en text/plain.
mimetypes.add_type("font/woff2", ".woff2")
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


_static_files = {
    "/static/css/style.css": _preload("static/css/style.css"),
    "/static/css/style.min.css": _preload("static/css/style.min.css"),
    "/static/js/app.js": _preload("static/js/app.js"),
    "/static/js/app.min.js": _preload("static/js/app.min.js"),
    # Favicon : jamais un 503 HTML pendant le démarrage (Google et les onglets le gardent)
    "/favicon.ico": _preload("static/favicon.ico"),
    "/static/favicon.ico": _preload("static/favicon.ico"),
}

# Dashboard rendu (Jinja2) pendant le démarrage : jamais le template brut
# (des balises {% ... %} servies en 200 seraient indexées telles quelles).
_cold_dashboard: bytes | None = None
_cold_dashboard_lock = threading.Lock()

# Page servie (503 + Retry-After) pour les autres URL pendant le démarrage :
# signal « indisponible temporairement » pour les robots, rechargement auto pour les visiteurs.
_LOADING_PAGE = (
    "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<meta http-equiv='refresh' content='3'><meta name='robots' content='noindex'>"
    "<title>Calendrier Tempo EDF</title>"
    "<style>body{font-family:sans-serif;text-align:center;padding:50px;color:#333}</style>"
    "</head><body><h1>Calendrier Tempo EDF</h1>"
    "<p>Démarrage du service, la page se recharge automatiquement…</p></body></html>"
).encode("utf-8")


def _render_cold_dashboard() -> bytes | None:
    """Rend dashboard.html sans données (même rendu que FastAPI quand la DB n'est pas prête).

    Jinja2 et site_facts sont légers (~100 ms) ; le rendu est mis en cache.
    """
    global _cold_dashboard
    if _cold_dashboard is not None:
        return _cold_dashboard
    with _cold_dashboard_lock:
        if _cold_dashboard is None:
            try:
                from jinja2 import Environment, FileSystemLoader
                import site_facts
                env = Environment(loader=FileSystemLoader(os.path.join(_base_dir, "templates")),
                                  autoescape=True)
                env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": False}
                env.globals.update(
                    facts=site_facts.template_globals(),
                    bing_verification=os.getenv("BING_SITE_VERIFICATION", ""),
                    google_verification=os.getenv("GOOGLE_SITE_VERIFICATION", ""),
                )
                html = env.get_template("dashboard.html").render(
                    request=None,
                    ssr={"predictions": [], "week_summary": []},
                    faq=site_facts.FAQ_HOME,
                    faq_ld=site_facts.faq_jsonld(site_facts.FAQ_HOME),
                    itemlist_ld=None,
                )
                _cold_dashboard = html.encode("utf-8")
            except Exception as e:
                logger.warning("[Proxy] Rendu du dashboard de démarrage impossible : %s", e)
                return None
    return _cold_dashboard

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
    """Réponses pendant le chargement de FastAPI.

    - « / » : le vrai dashboard, rendu par Jinja2 sans données (jamais le
      template brut). Les appels API échouent gracieusement (le JS affiche
      Réessayer) puis fonctionnent dès que FastAPI est prêt.
    - CSS/JS préchargés : servis directement.
    - /api/* : 503 JSON. Autres pages : 503 + Retry-After (signal temporaire
      pour les robots, rechargement automatique pour les visiteurs).
    """
    path = scope.get("path", "/")

    cache_control = None

    if path in ("/health", "/keepalive"):
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
        cache_control = b"public, max-age=3600, stale-while-revalidate=86400"
        status = 200
    elif path == "/" and _render_cold_dashboard():
        # Vrai dashboard rendu (sans données : le JS les charge dès que l'API répond)
        body = _render_cold_dashboard()
        content_type = b"text/html; charset=utf-8"
        cache_control = b"no-store"
        status = 200
    elif path == "/":
        # Fallback si le rendu a échoué : 200 pour ne pas faire échouer un health check sur /
        body = _LOADING_PAGE
        content_type = b"text/html; charset=utf-8"
        cache_control = b"no-store"
        status = 200
    else:
        # Autres pages (calendrier, blog, robots.txt, sitemap.xml…) : 503 + Retry-After,
        # jamais un contenu de remplacement en 200 sous une autre URL.
        body = _LOADING_PAGE
        content_type = b"text/html; charset=utf-8"
        cache_control = b"no-store"
        status = 503

    headers = [
        [b"content-type", content_type],
        [b"content-length", str(len(body)).encode()],
    ]
    if cache_control:
        headers.append([b"cache-control", cache_control])
    if status == 503:
        headers.append([b"retry-after", b"5"])

    await send({
        "type": "http.response.start",
        "status": status,
        "headers": headers,
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
        for _src in ("ADMIN_PASSWORD", "SESSION_SECRET"):
            if os.getenv(_src, ""):
                logger.info("[Admin] Mot de passe source: %s env var (longueur=%d)",
                            _src, len(os.getenv(_src, "")))
                break
        if not Config.ADMIN_PASSWORD:
            import secrets
            Config.ADMIN_PASSWORD = secrets.token_urlsafe(24)
            logger.warning(
                "[Proxy] ADMIN_PASSWORD non défini — mot de passe aléatoire généré. "
                "Définissez ADMIN_PASSWORD dans .env pour le conserver."
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
