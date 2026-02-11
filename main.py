"""Point d'entrée de l'application TempoForecast.

Fix #42 : pré-serveur health check pour Replit.

Le chargement de FastAPI + dépendances prend 5-15s sur Replit.
Pendant ce temps, aucun port n'est ouvert → le health check échoue.

Solution : démarrer un mini-serveur HTTP (<0.1s) qui répond 200
sur le port attendu PENDANT que l'app lourde se charge.
Séquence :
  1. Mini-serveur HTTP démarre instantanément sur port 5000
  2. Import de l'app FastAPI (lent, 5-15s) — health checks servis pendant ce temps
  3. Mini-serveur s'arrête, uvicorn prend le relais sur le même port

Usage :
    python main.py          → Démarre le serveur sur port 5000
    uvicorn app:app --reload → Mode développement avec hot-reload (sans pré-serveur)
"""

import os
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


class _HealthHandler(BaseHTTPRequestHandler):
    """Répond 200 à toute requête — juste pour le health check Replit."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><p>TempoForecast - chargement en cours...</p></body></html>"
        )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

    def log_message(self, *args):
        pass  # Silence les logs du mini-serveur


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))

    # === Phase 1 : mini-serveur health check instantané (<0.1s) ===
    health_server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    health_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()
    print(f"[TempoForecast] Health check prêt sur port {port}")

    # === Phase 2 : chargement de l'app lourde (5-15s sur Replit) ===
    # Pendant ce temps, le mini-serveur répond 200 aux health checks
    import uvicorn

    from app import app  # noqa: E402 — déclenche tous les imports lourds

    # === Phase 3 : bascule vers uvicorn ===
    health_server.shutdown()
    health_server.server_close()
    time.sleep(0.2)  # Laisser le socket se libérer

    print(f"[TempoForecast] Démarrage uvicorn sur port {port}")
    uvicorn.run(
        app,  # Objet direct (déjà importé), pas de string "app:app"
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
