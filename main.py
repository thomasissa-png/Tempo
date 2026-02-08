"""Point d'entrée de l'application TempoForecast.

Usage :
    python main.py          → Démarre le serveur sur port 5000
    uvicorn app:app --reload → Mode développement avec hot-reload
"""

import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        reload=False,
        log_level="info",
    )
